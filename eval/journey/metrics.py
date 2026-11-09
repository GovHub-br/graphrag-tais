"""Métricas da jornada (ADR-0020) — funções puras, rodam no host.

Eixos separados, sem score composto (mesma regra do ADR-0012):

- rota: a regra número/assunto acertou o que o caso espera?
- desfecho: a primeira resposta chegou a um número (quando devia)?
- frase: o texto do LLM passou nas guardas ou caiu na frase determinística?
  E os números que ela cita existem nos fatos (headline + digest)?
- trilhas: cada botão abriu sem erro? Quanto pesou e quanto demorou?
- contrato: sem emoji, botão já clicado não volta, marcador presente.
"""
from __future__ import annotations

import re
from statistics import median
from typing import Any, Dict, List, Mapping, Sequence

_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_FALLBACK = {
    "frase sem o número": "sem_numero",
    "frase atribui o total": "total_mal_atribuido",
    "frase do LLM falhou": "llm_falhou",
}
_SEM_PERGUNTA = "Faça uma pergunta primeiro"
_SEM_NUMERO_TRILHA = "não chegou a um número"


def phrase_numbers_outside(phrase: str, facts_text: str) -> List[str]:
    """Números da frase que não aparecem nos fatos que o LLM recebeu.

    Comparação textual, no formato em que o número foi entregue ao modelo
    ("2,1 mil", "R$ 3,4 bi"): o prompt manda usar os números do resumo "no
    formato compacto em que aparecem". Número fora disso é número inventado
    (ou recalculado pelo modelo, que dá no mesmo)."""
    fatos = {m.group(0) for m in _NUM.finditer(facts_text or "")}
    fora = []
    for m in _NUM.finditer(phrase or ""):
        valor = m.group(0).rstrip(".,")
        if valor not in fatos and valor not in (facts_text or ""):
            fora.append(valor)
    return fora


def _phrase_source(warnings: Sequence[str]) -> str:
    for aviso in warnings:
        for trecho, motivo in _FALLBACK.items():
            if trecho in aviso:
                return motivo
    return "llm"


def _observed_route(t0: Mapping[str, Any]) -> str:
    if t0.get("rota"):
        return t0["rota"]
    if _SEM_PERGUNTA in t0.get("markdown", ""):
        return "sem_pergunta"
    if not t0.get("marker"):
        return "recusa"
    return "?"


def _turn_ok(t: Mapping[str, Any]) -> bool:
    return (not t.get("exception") and not t.get("chat_error") and t.get("bytes", 0) > 0)


def score_case(case: Mapping[str, Any], turns: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    t0 = turns[0]
    rota = _observed_route(t0)
    chegou = t0.get("status") == "ok" and bool(t0.get("exec_ok"))
    esperado = case["expected_outcome"]
    if esperado == "numero":
        desfecho_ok = chegou
    elif esperado == "assunto":
        desfecho_ok = rota == "assunto" and bool(t0.get("follow_ups"))
    elif esperado == "recusa":
        desfecho_ok = rota == "recusa" and "```sql" not in t0.get("markdown", "")
    elif esperado == "sem_pergunta":
        desfecho_ok = rota == "sem_pergunta"
    else:  # "honesto": chegar ou não chegar ao número é aceitável; a leitura é manual
        desfecho_ok = _turn_ok(t0)

    cliques = [t for t in turns if t["kind"] == "clique"]
    trilhas = []
    for t in cliques:
        trilhas.append({
            "botao": t["input"], "ok": _turn_ok(t),
            "sem_numero": _SEM_NUMERO_TRILHA in t.get("markdown", ""),
            "latency_s": t["latency_s"], "kb": t["bytes"] // 1024, "images": len(t["images"]),
            "botao_repetido": t["input"] in (t.get("follow_ups") or []),
            "avisos": len(t.get("warnings") or []),
        })
    return {
        "route_observed": rota, "route_ok": rota == case["expected_route"],
        "reached_number": chegou, "outcome_ok": desfecho_ok,
        "needs_review": esperado == "honesto",
        "phrase_source": _phrase_source(t0.get("warnings") or []) if chegou else "",
        "phrase_numbers_outside": t0.get("phrase_numbers_outside", []),
        "first_latency_s": t0["latency_s"], "first_kb": t0["bytes"] // 1024,
        "first_images": len(t0["images"]), "buttons": t0.get("follow_ups") or [],
        "tracks": trilhas,
        "any_exception": any(t.get("exception") or t.get("chat_error") for t in turns),
        "emoji_leak": any(t.get("emoji_leak") for t in turns),
        "max_kb": max(t["bytes"] for t in turns) // 1024,
        # Resposta sem resultado guardado não tem trilha para abrir: botão ali
        # é um laço ("faça uma pergunta primeiro" de novo).
        "buttons_without_state": bool(t0.get("follow_ups")) and not t0.get("ref"),
    }


def _pct(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordem = sorted(values)
    return round(ordem[min(len(ordem) - 1, int(round(q * (len(ordem) - 1))))], 2)


def _rate(n: int, d: int) -> float:
    return round(n / d, 3) if d else 0.0


def aggregate(cases: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    scores = [c["score"] for c in cases]
    numero = [c for c in cases if c["expected_outcome"] == "numero"]
    chegaram = [c["score"] for c in cases if c["score"]["reached_number"]]
    fontes: Dict[str, int] = {}
    for s in chegaram:
        fontes[s["phrase_source"]] = fontes.get(s["phrase_source"], 0) + 1

    por_trilha: Dict[str, Dict[str, Any]] = {}
    for s in scores:
        for t in s["tracks"]:
            d = por_trilha.setdefault(t["botao"], {"n": 0, "ok": 0, "sem_numero": 0,
                                                    "repetido": 0, "lat": [], "kb": []})
            d["n"] += 1
            d["ok"] += t["ok"]
            d["sem_numero"] += t["sem_numero"]
            d["repetido"] += t["botao_repetido"]
            d["lat"].append(t["latency_s"])
            d["kb"].append(t["kb"])
    trilhas = {b: {"n": d["n"], "ok_rate": _rate(d["ok"], d["n"]),
                   "sem_numero": d["sem_numero"], "botao_repetido": d["repetido"],
                   "latency_p50_s": round(median(d["lat"]), 2), "latency_max_s": max(d["lat"]),
                   "kb_max": max(d["kb"])}
               for b, d in por_trilha.items()}

    primeiras = [s["first_latency_s"] for s in scores if s["route_observed"] in ("numero", "assunto")]
    return {
        "cases": len(cases),
        "route_accuracy": _rate(sum(s["route_ok"] for s in scores), len(scores)),
        "outcome_ok_rate": _rate(sum(s["outcome_ok"] for s in scores), len(scores)),
        "number_reach_rate": _rate(sum(c["score"]["reached_number"] for c in numero), len(numero)),
        "phrase_source": fontes,
        "phrase_llm_rate": _rate(fontes.get("llm", 0), len(chegaram)),
        "phrase_with_numbers_outside_facts": sum(1 for s in chegaram if s["phrase_numbers_outside"]),
        "first_turn_latency_p50_s": _pct(primeiras, 0.5),
        "first_turn_latency_p95_s": _pct(primeiras, 0.95),
        "max_response_kb": max((s["max_kb"] for s in scores), default=0),
        "cases_with_exception": [c["id"] for c in cases if c["score"]["any_exception"]],
        "cases_with_emoji": [c["id"] for c in cases if c["score"]["emoji_leak"]],
        "route_errors": [c["id"] for c in cases if not c["score"]["route_ok"]],
        "outcome_errors": [c["id"] for c in cases if not c["score"]["outcome_ok"]],
        "needs_review": [c["id"] for c in cases if c["score"]["needs_review"]],
        "buttons_without_state": [c["id"] for c in cases if c["score"]["buttons_without_state"]],
        "tracks": trilhas,
    }

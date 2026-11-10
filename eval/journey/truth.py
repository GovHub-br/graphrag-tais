"""Gabarito da jornada: o número que o BANCO REAL dá, comparado ao que o sistema mostrou.

Cada caso de número pode trazer um bloco `truth` no dataset:

    "truth": {
      "sql": "SELECT uf AS chave, COUNT(DISTINCT apf) AS valor FROM … GROUP BY uf",
      "headline": "total",        # total | ultimo | maior | valor
      "key": "uf",                # uf | periodo | texto — como casar a chave
      "tolerance": 0.005,         # erro relativo aceito
      "alternatives": [{"name": "saldo do mês", "sql": "…", "headline": "ultimo"}],
      "data_checks": [{"name": "…", "sql": "SELECT … AS v", "expect": "zero", "means": "…"}],
      "catalog_tables": ["ouro_far_ficha_empreendimento"],
      "note": "por que este é o número certo"
    }

O SQL do gabarito é escrito À MÃO, lendo a tabela e o catálogo — não sai do
sistema avaliado. Ele roda AO VIVO (o banco muda; um número congelado no
dataset envelheceria em silêncio), e `expected_snapshot` guarda o valor da
época em que o gabarito foi escrito, só para notar deriva.

O veredito separa de quem é o defeito:
- `ok`: o destaque do sistema bate com o banco;
- `leitura_alternativa`: bate com uma leitura também válida da pergunta;
- `diferente`: não bate — `pista` diz o padrão (dobrado, fator 100, escala…);
- `sem_numero`: o sistema não chegou a número.
E os `data_checks` dizem se o DADO tem a armadilha (linha de total, último
mês vazio, unidade trocada): defeito de dado aparece como dado, não como erro
do modelo. Funções puras — o collector (`live.py`) executa os SQL.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

_PERIODO = re.compile(r"^(\d{4})-(\d{2})")


def as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(str(value).replace(",", "."))
        except ValueError:
            return None


def _fold(texto: str) -> str:
    sem = unicodedata.normalize("NFKD", str(texto or ""))
    sem = "".join(c for c in sem if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem.strip().lower())


def norm_key(label: Any, kind: str = "texto", uf_resolver=None) -> str:
    """Chave comparável: sigla da UF, `AAAA-MM` do período, ou texto dobrado."""
    texto = str(label if label is not None else "")
    if kind == "periodo":
        achado = _PERIODO.match(texto.strip())
        return f"{achado.group(1)}-{achado.group(2)}" if achado else _fold(texto)
    if kind == "uf" and uf_resolver is not None:
        uf = uf_resolver(texto)
        if uf:
            return uf["sigla"]
    return _fold(texto)


def headline_from_rows(rows: Sequence[Mapping[str, Any]], mode: str) -> Optional[float]:
    """Destaque do gabarito a partir das linhas `chave, valor`."""
    pares = [(r.get("chave"), as_float(r.get("valor"))) for r in rows]
    pares = [(k, v) for k, v in pares if v is not None]
    if not pares:
        return None
    if mode == "total":
        return sum(v for _, v in pares)
    if mode == "maior":
        return max(v for _, v in pares)
    if mode == "ultimo":
        return sorted(pares, key=lambda kv: str(kv[0]))[-1][1]
    return pares[0][1]  # "valor": resultado escalar


def by_key(rows: Sequence[Mapping[str, Any]], kind: str, uf_resolver=None) -> Dict[str, float]:
    saida: Dict[str, float] = {}
    for r in rows:
        v = as_float(r.get("valor"))
        if v is None or "chave" not in r:
            continue
        k = norm_key(r.get("chave"), kind, uf_resolver)
        saida[k] = saida.get(k, 0.0) + v
    return saida


def close(a: Optional[float], b: Optional[float], tol: float) -> bool:
    if a is None or b is None:
        return False
    if b == 0:
        return abs(a) <= tol
    return abs(a - b) / abs(b) <= tol


_PISTAS: Tuple[Tuple[float, str], ...] = (
    (2.0, "dobrado — linha de total/agregado somada junto?"),
    (0.5, "metade — filtro a mais ou só parte das categorias?"),
    (0.01, "fator 1/100 — fração exibida onde se esperava percentual"),
    (100.0, "fator 100 — percentual onde se esperava fração"),
    (1e-3, "fator 1/1.000 — escala (mil) não aplicada"),
    (1e-6, "fator 1/1.000.000 — escala (milhões) não aplicada"),
    (1e3, "fator 1.000 — escala aplicada a mais"),
    (1e6, "fator 1.000.000 — escala aplicada a mais"),
)


def hint(system: Optional[float], truth: Optional[float]) -> str:
    """O padrão da diferença: quase sempre aponta a causa."""
    if system is None or truth is None:
        return ""
    if truth == 0:
        return "gabarito é zero"
    razao = system / truth
    for fator, texto in _PISTAS:
        if abs(razao / fator - 1) <= 0.03:
            return texto
    if razao < 0:
        return "sinal trocado"
    return f"razão sistema/banco = {razao:.3g}"


def check_data(check: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Uma checagem do DADO. `expect`: zero | nonzero | ">N" | "<N"."""
    valor = as_float(rows[0].get("v")) if rows else None
    esperado = str(check.get("expect", "zero"))
    if valor is None:
        ok = esperado == "zero"
    elif esperado == "zero":
        ok = valor == 0
    elif esperado == "nonzero":
        ok = valor != 0
    elif esperado.startswith(">"):
        ok = valor > float(esperado[1:])
    elif esperado.startswith("<"):
        ok = valor < float(esperado[1:])
    else:
        ok = False
    return {"name": check.get("name", ""), "value": valor, "expect": esperado, "ok": ok,
            "means": check.get("means", "") if not ok else ""}


def compare(truth_spec: Mapping[str, Any], truth_rows: Sequence[Mapping[str, Any]],
            system: Mapping[str, Any], alternatives: Iterable[Tuple[str, Optional[float]]] = (),
            uf_resolver=None) -> Dict[str, Any]:
    """Veredito de UM caso. `system` traz `reached`, `headline` (float, na unidade
    base que o sistema calculou), `headline_kind` e `values` [(rótulo, valor)]."""
    tol = float(truth_spec.get("tolerance", 0.005))
    kind = truth_spec.get("key", "texto")
    esperado = headline_from_rows(truth_rows, truth_spec.get("headline", "total"))
    out: Dict[str, Any] = {"expected": esperado, "received": system.get("headline"),
                           "received_kind": system.get("headline_kind", ""),
                           "expected_kind": truth_spec.get("headline", "total")}
    if not system.get("reached"):
        out.update(verdict="sem_numero", hint="")
        return out
    recebido = as_float(system.get("headline"))
    if close(recebido, esperado, tol):
        out.update(verdict="ok", hint="")
    else:
        alt = next((nome for nome, v in alternatives if close(recebido, v, tol)), "")
        if alt:
            out.update(verdict="leitura_alternativa", hint=alt)
        else:
            pista = hint(recebido, esperado)
            # A diferença pode ser limpa em relação a uma leitura alternativa
            # (ex.: o dobro do "pago com restos a pagar"): diz qual.
            for nome, v in alternatives:
                p_alt = hint(recebido, v)
                if p_alt and not p_alt.startswith("razão"):
                    pista = f"{p_alt} (em relação a: {nome})"
                    break
            out.update(verdict="diferente", hint=pista)

    # Por chave: o mapa/ranking pode estar certo mesmo com destaque errado (e vice-versa).
    if "chave" in (truth_rows[0] if truth_rows else {}) and system.get("values"):
        verdade = by_key(truth_rows, kind, uf_resolver)
        sistema: Dict[str, float] = {}
        for rotulo, v in system["values"]:
            k = norm_key(rotulo, kind, uf_resolver)
            sistema[k] = sistema.get(k, 0.0) + float(v)
        comuns = [k for k in verdade if k in sistema]
        erradas = [(k, sistema[k], verdade[k]) for k in comuns if not close(sistema[k], verdade[k], tol)]
        out["keys"] = {
            "expected": len(verdade), "received": len(sistema), "matched": len(comuns) - len(erradas),
            "wrong": [{"chave": k, "sistema": s, "banco": b, "pista": hint(s, b)} for k, s, b in erradas[:8]],
            "missing": sorted(k for k in verdade if k not in sistema)[:10],
            "extra": sorted(k for k in sistema if k not in verdade)[:10],
        }
    return out


def owner(case_truth: Mapping[str, Any]) -> str:
    """De quem é o defeito, pela evidência coletada. Só sugere — a leitura confirma."""
    v = case_truth.get("verdict")
    dados_falhos = [c for c in case_truth.get("data_checks", []) if not c.get("ok")]
    if v == "ok":
        return "ok" if not dados_falhos else "ok (dado com armadilha, contornada)"
    if case_truth.get("system_tables_missing_in_db"):
        return "catálogo (o SQL usou tabela que não existe no banco)"
    if case_truth.get("catalog_missing"):
        return "catálogo (tabela fora do escopo ou inexistente no banco)"
    if v == "leitura_alternativa":
        return "interpretação (outra leitura válida)"
    if dados_falhos:
        return "dado + sistema (o dado tem a armadilha; o sistema não a tratou)"
    return "sistema"


# --------------------------------------------------------------------------- #
# Tabela esperado × recebido de um run — o histórico que se compara entre runs.
def _num(v: Any) -> str:
    f = as_float(v)
    if f is None:
        return "—"
    return f"{f:,.4g}" if abs(f) < 1000 else f"{f:,.0f}"


def table_rows(cases: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    linhas = []
    for c in cases:
        tr = c.get("truth_result")
        if not tr:
            continue
        chaves = tr.get("keys") or {}
        dados = [d["name"] for d in tr.get("data_checks", []) if not d.get("ok")]
        linhas.append({
            "caso": c["id"], "veredito": tr["verdict"], "dono": tr["owner"],
            "esperado_banco": tr["expected"], "recebido_sistema": tr["received"],
            "na_tela": tr.get("system_headline_text", ""),
            "pista": tr.get("hint", ""),
            "chaves_ok": f'{chaves.get("matched", "")}/{chaves.get("expected", "")}' if chaves else "",
            "armadilhas_no_dado": "; ".join(dados),
            "fora_do_catalogo": ", ".join(tr.get("catalog_missing", [])),
            "tabela_fantasma": ", ".join(tr.get("system_tables_missing_in_db", [])),
        })
    return linhas


def write_table(cases: Sequence[Mapping[str, Any]], out_dir) -> None:
    import csv
    from pathlib import Path

    linhas = table_rows(cases)
    if not linhas:
        return
    out = Path(out_dir)
    with open(out / "gabarito.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0]), delimiter=";")
        w.writeheader()
        w.writerows(linhas)
    md = ["| caso | veredito | dono provável | banco | sistema | na tela | pista | chaves | armadilha no dado |",
          "|---|---|---|---|---|---|---|---|---|"]
    for l in linhas:
        md.append(f'| {l["caso"]} | {l["veredito"]} | {l["dono"]} | {_num(l["esperado_banco"])} | '
                  f'{_num(l["recebido_sistema"])} | {l["na_tela"]} | {l["pista"]} | {l["chaves_ok"]} | '
                  f'{l["armadilhas_no_dado"] or l["fora_do_catalogo"] or l["tabela_fantasma"]} |')
    (out / "gabarito.md").write_text("\n".join(md) + "\n", encoding="utf-8")


if __name__ == "__main__":  # python -m eval.journey.truth <dir do run>
    import json
    import sys
    from pathlib import Path

    pasta = Path(sys.argv[1])
    write_table(json.load(open(pasta / "run.json", encoding="utf-8"))["cases"], pasta)
    print((pasta / "gabarito.md").read_text(encoding="utf-8"))

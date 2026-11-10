"""Collector da jornada (ADR-0020): conversa simulada do OpenWebUI, ponta a ponta.

Não mede retrieval nem SQL contra gold — isso é do `eval.retrieval` e do
`eval.answer`, e a jornada usa o motor da Persona 2 como caixa-preta. Aqui se
mede o que a jornada ACRESCENTA: a rota (número ou assunto), se a primeira
resposta chega a um número, se a frase do LLM passa nas guardas ou cai na
determinística, se os números da frase existem nos fatos, e se cada botão abre
a trilha sem erro, com o peso e o tempo de cada resposta.

Roda DENTRO do container (precisa das pipelines reais), como o `eval.answer`:

    docker exec hayhooks rm -rf /app/eval && docker cp eval hayhooks:/app/eval
    docker exec -w /app -e PYTHONPATH=/app:/app/src hayhooks \\
        python -m eval.journey.live \\
        --dataset eval/datasets/journey/cidades-journey-v0.1.0.json \\
        --output-dir eval/reports/journey-AAAAMMDD

O fluxo de cada caso imita o OpenWebUI: pergunta → pedido de follow-ups
(`### Task:` com o histórico) → clique em CADA botão, na ordem sugerida, com o
histórico acumulando (o marcador escondido carrega `vistas`). Chama o LLM.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from . import truth as truth_mod
from .metrics import aggregate, phrase_numbers_outside, score_case

_IMG = re.compile(r"!\[([^\]]*)\]\(data:image/png;base64,([A-Za-z0-9+/=]+)\)")
_FOLLOW_UP_TASK = (
    "### Task:\nSuggest 3-5 relevant follow-up questions or prompts that the user might "
    "naturally ask next in this conversation as a **user**, based on the chat history.\n"
    "### Output:\nJSON format: { \"follow_ups\": [\"Question 1?\", \"Question 2?\"] }\n"
    "### Chat History:\n<chat_history>\n{history}\n</chat_history>"
)


class _Warnings:
    """Avisos do loguru durante um turno — é por eles que se sabe o fallback da frase."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def __call__(self, message) -> None:
        record = message.record
        if record["level"].no >= 30 or "[jornada/serie]" in record["message"]:
            self.items.append(f'{record["level"].name}: {record["message"][:300]}')

    def take(self) -> list[str]:
        out, self.items = self.items, []
        return out


def _history(messages: list) -> str:
    linhas = []
    for m in messages[-6:]:
        linhas.append(f'{m["role"].upper()}: {m["content"]}')
    return "\n".join(linhas)


def _strip_images(text: str, img_dir: Path, prefix: str) -> tuple[str, list[dict]]:
    imagens: list[dict] = []

    def troca(m: re.Match) -> str:
        dado = base64.b64decode(m.group(2))
        nome = f"{prefix}-{len(imagens) + 1}.png"
        (img_dir / nome).write_bytes(dado)
        imagens.append({"alt": m.group(1), "file": nome, "bytes": len(dado)})
        return f"![{m.group(1)}](img/{nome})"

    return _IMG.sub(troca, text), imagens


def collect(dataset_path: str, output_dir: str, only: list[str] | None = None) -> dict:
    from loguru import logger

    from rag.journey import marker as marker_mod
    from rag.journey import render
    from rag.journey.text import strip_emoji
    from rag.pipelines.jornada.pipeline_wrapper import PipelineWrapper

    dataset = json.load(open(dataset_path, encoding="utf-8"))
    out_dir = Path(output_dir)
    img_dir = out_dir / "img"
    img_dir.mkdir(parents=True, exist_ok=True)

    avisos = _Warnings()
    logger.add(avisos, level="INFO")

    wrapper = PipelineWrapper()
    wrapper.setup()
    service = wrapper.service

    def turno(case_id: str, n: int, kind: str, messages: list) -> dict:
        avisos.take()
        inicio = time.time()
        erro = ""
        try:
            texto = "".join(wrapper.run_chat_completion("jornada", messages, {}))
        except Exception as exc:  # noqa: BLE001
            texto, erro = "", f"{type(exc).__name__}: {exc}"
        latencia = round(time.time() - inicio, 2)
        marcador = marker_mod.parse(texto)
        limpo, imagens = _strip_images(texto, img_dir, f"{case_id}-{n:02d}")
        registro = {
            "n": n, "kind": kind, "input": messages[-1]["content"], "latency_s": latencia,
            "bytes": len(texto.encode("utf-8")), "images": imagens, "exception": erro,
            "chat_error": "**Erro ao processar" in texto,
            "emoji_leak": strip_emoji(texto) != texto,
            "marker": marker_mod.Marker.render(marcador) if marcador else "",
            "warnings": avisos.take(), "markdown": limpo,
        }
        if marcador and marcador.ref:
            registro.update(_state_view(service, render, marcador.ref, kind))
        # Botões, pelo mesmo caminho do OpenWebUI (sem LLM).
        historico = messages + [{"role": "assistant", "content": texto}]
        tarefa = _FOLLOW_UP_TASK.replace("{history}", _history(historico))
        bruto = "".join(wrapper.run_chat_completion("jornada", [{"role": "user", "content": tarefa}], {}))
        try:
            registro["follow_ups"] = json.loads(bruto).get("follow_ups", [])
        except json.JSONDecodeError:
            registro["follow_ups"] = []
            registro["follow_ups_raw"] = bruto[:300]
        registro["_raw"] = texto
        return registro

    casos = []
    for case in dataset["cases"]:
        if only and case["id"] not in only:
            continue
        print(f'>>> {case["id"]}: {case["question"]}', flush=True)
        messages = [{"role": "user", "content": case["question"]}]
        turnos = [turno(case["id"], 0, "pergunta", messages)]
        messages.append({"role": "assistant", "content": turnos[0].pop("_raw")})

        # Assunto: o botão é uma pergunta de número; clica na primeira e
        # depois na trilha técnica dela (o caminho "errou para assunto").
        # Número: clica em TODOS os botões oferecidos, na ordem sugerida.
        # Sem `ref` (recusa, clique órfão) não há o que abrir: os botões que
        # vierem ficam registrados no score, mas não se clica neles.
        if not turnos[0].get("ref"):
            fila = []
        elif turnos[0].get("rota") == "assunto":
            fila = turnos[0].get("follow_ups", [])[:1]
        else:
            fila = list(turnos[0].get("follow_ups", []))
        for botao in fila:
            messages.append({"role": "user", "content": botao})
            t = turno(case["id"], len(turnos), "clique", messages)
            messages.append({"role": "assistant", "content": t.pop("_raw")})
            turnos.append(t)
            print(f'    {botao!r}: {t["latency_s"]}s, {len(t["images"])} img, '
                  f'{len(t["warnings"])} aviso(s)', flush=True)
            if turnos[0].get("rota") == "assunto" and t.get("follow_ups"):
                tecnica = next((b for b in t["follow_ups"] if "calculado" in b), "")
                if tecnica:
                    messages.append({"role": "user", "content": tecnica})
                    t2 = turno(case["id"], len(turnos), "clique", messages)
                    messages.append({"role": "assistant", "content": t2.pop("_raw")})
                    turnos.append(t2)

        resultado = {**case, "turns": turnos}
        resultado["score"] = score_case(case, turnos)
        if case.get("truth"):
            resultado["truth_result"] = _truth(case["truth"], turnos[0], service)
            print(f'    gabarito: {resultado["truth_result"]["verdict"]} '
                  f'({resultado["truth_result"]["owner"]}) {resultado["truth_result"]["hint"]}', flush=True)
        casos.append(resultado)
        _transcript(out_dir, resultado)

    run = {
        "dataset": dataset["name"], "dataset_version": dataset["version"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "settings": _settings_snapshot(),
        "summary": aggregate(casos), "cases": casos,
    }
    (out_dir / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    truth_mod.write_table(casos, out_dir)
    return run


def _state_view(service, render, ref: str, kind: str) -> dict:
    """O que ficou guardado para este `ref`: é o que a pessoa leu, em dado."""
    state = service.store.load(ref) or {}
    view = {"ref": ref, "rota": state.get("route", ""), "status": state.get("status", "")}
    execu = state.get("exec") or {}
    if execu:
        view.update({"exec_ok": bool(execu.get("exec_ok")), "rows": len(execu.get("rows") or []),
                     "columns": execu.get("columns") or [], "sql": execu.get("sql_final", ""),
                     "exec_error": (execu.get("error") or "")[:300],
                     "truncated": bool(execu.get("truncated"))})
    if kind == "pergunta" and state.get("status") == "ok":
        f = service._facts(state)
        headline, digest = render.headline_text(f), render.digest(f)
        phrase = state.get("phrase", "")
        view.update({"headline": headline, "label": state.get("label", ""), "phrase": phrase,
                     "digest": digest, "dimension_kind": f.dimension_kind, "facts_n": f.n,
                     # O número CRU (já na unidade base): é o que se compara ao banco.
                     "headline_raw": f.headline, "headline_kind": f.headline_kind,
                     "values_raw": [[str(k), float(v)] for k, v in f.values][:300],
                     "phrase_numbers_outside": phrase_numbers_outside(
                         phrase, headline + " " + digest + " " + state.get("question", ""))})
    return view


_EXECUTOR = None


def _run_sql(sql: str) -> dict:
    """SQL do gabarito, direto no banco (sem LLM, sem autocorreção)."""
    global _EXECUTOR
    from rag.components.executors.mcp_sql_executor import McpSqlExecutor

    if _EXECUTOR is None:
        _EXECUTOR = McpSqlExecutor(direct_only=True)
        _EXECUTOR.max_rows = 100000
    r = _EXECUTOR.run(sql=sql)
    return {"rows": r["rows"], "error": r["error"]}


def _catalog_has(tables: list) -> dict:
    """Quais tabelas do gabarito o catálogo do escopo conhece (Neo4j)."""
    if not tables:
        return {}
    from neo4j import GraphDatabase

    from rag.settings.configs import settings as s

    d = GraphDatabase.driver(s.NEO4J_URL, auth=(s.NEO4J_USERNAME, s.NEO4J_PASSWORD))
    try:
        with d.session(database=s.NEO4J_DATABASE) as ss:
            achadas = {r["n"] for r in ss.run(
                "MATCH (t)-[:HAS_COLUMN]->() WHERE t.name IN $ts RETURN DISTINCT t.name AS n", ts=tables)}
    finally:
        d.close()
    return {t: t in achadas for t in tables}


def _truth(spec: dict, t0: dict, service) -> dict:
    """Roda o gabarito no banco real e compara com o que o sistema mostrou."""
    from rag.journey import ibge

    base = _run_sql(spec["sql"])
    alternativas = []
    for alt in spec.get("alternatives", []):
        r = _run_sql(alt["sql"])
        alternativas.append((alt["name"], truth_mod.headline_from_rows(r["rows"], alt.get("headline", "total"))))
    checks = []
    for chk in spec.get("data_checks", []):
        r = _run_sql(chk["sql"])
        item = truth_mod.check_data(chk, r["rows"])
        if r["error"]:
            item.update(ok=False, means=f'checagem não rodou: {r["error"][:150]}')
        checks.append(item)
    sistema = {"reached": t0.get("status") == "ok" and bool(t0.get("exec_ok")),
               "headline": t0.get("headline_raw"), "headline_kind": t0.get("headline_kind", ""),
               "values": t0.get("values_raw") or []}
    out = truth_mod.compare(spec, base["rows"], sistema, alternativas, uf_resolver=ibge.resolve)
    catalogo = _catalog_has(spec.get("catalog_tables", []))
    # Tabela que o SQL do SISTEMA usou e que o banco não tem: o catálogo
    # prometeu algo que não existe (defeito de catálogo, não do modelo).
    usadas = sorted({f"{a}.{b}" for a, b in re.findall(r"\b(ouro|prata|bronze)\.(\w+)", t0.get("sql") or "")})
    fantasmas = []
    if usadas:
        lista = ", ".join(f"'{u}'" for u in usadas)
        r = _run_sql("SELECT table_schema||'.'||table_name AS t FROM information_schema.tables "
                     f"WHERE table_schema||'.'||table_name IN ({lista})")
        existem = {x["t"] for x in r["rows"]}
        fantasmas = [u for u in usadas if u not in existem]
    out.update({
        "system_tables": usadas, "system_tables_missing_in_db": fantasmas,
        "truth_sql": spec["sql"], "truth_error": base["error"][:300], "truth_rows": len(base["rows"]),
        "expected_snapshot": spec.get("expected_snapshot"),
        "alternatives": [{"name": n, "value": v} for n, v in alternativas],
        "data_checks": checks, "catalog": catalogo,
        "catalog_missing": [t for t, ok in catalogo.items() if not ok],
        "system_sql": t0.get("sql", ""), "system_headline_text": t0.get("headline", ""),
        "note": spec.get("note", ""),
    })
    out["owner"] = truth_mod.owner(out)
    return out


def _settings_snapshot() -> dict:
    from rag.settings.configs import settings

    campos = ("OM_SCOPE_ID", "SQL_DIALECT", "SQL_MCP_URL", "JOURNEY_POLICY_SERIES",
              "JOURNEY_PLAIN_MAX_TOKENS", "JOURNEY_STATE_TTL", "JOURNEY_OPEN_DATA_URL",
              "EXPANSION_MODE", "OPENAI_MODEL")
    return {c: str(getattr(settings, c, "")) for c in campos}


def _transcript(out_dir: Path, case: dict) -> None:
    linhas = [f'# {case["id"]}', "", f'**Pergunta:** {case["question"]}', "",
              f'Esperado: rota `{case["expected_route"]}`, desfecho `{case["expected_outcome"]}`',
              "", "```json", json.dumps(case["score"], ensure_ascii=False, indent=2), "```", ""]
    tr = case.get("truth_result")
    if tr:
        linhas += ["## Gabarito (banco real) × sistema", "",
                   f'- veredito: **{tr["verdict"]}** — dono provável: **{tr["owner"]}** {tr["hint"]}',
                   f'- esperado (banco): `{tr["expected"]}` ({tr["expected_kind"]}) · recebido: '
                   f'`{tr["received"]}` ({tr["received_kind"]}) → "{tr["system_headline_text"]}"',
                   f'- catálogo: {tr["catalog"]}', ""]
        if tr.get("keys"):
            linhas += [f'- por chave: {json.dumps(tr["keys"], ensure_ascii=False)}', ""]
        for c in tr["data_checks"]:
            linhas.append(f'- dado · {c["name"]}: {"ok" if c["ok"] else "FALHA"} (v={c["value"]}) {c["means"]}')
        linhas += ["", "SQL do gabarito:", "```sql", tr["truth_sql"], "```", ""]
    for t in case["turns"]:
        linhas += [f'---\n\n## Turno {t["n"]} — {t["kind"]}: “{t["input"]}”', "",
                   f'{t["latency_s"]}s · {t["bytes"] // 1024} KB · {len(t["images"])} imagem(ns) · '
                   f'status `{t.get("status", "-")}` · exec_ok `{t.get("exec_ok", "-")}`', ""]
        if t.get("warnings"):
            linhas += ["Avisos:", *[f"- `{w}`" for w in t["warnings"]], ""]
        if t.get("sql"):
            linhas += ["SQL:", "```sql", t["sql"], "```", ""]
        linhas += [t["markdown"], "", f'Botões: {t.get("follow_ups")}', ""]
    (out_dir / f'{case["id"]}.md').write_text("\n".join(linhas), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--only", nargs="*", help="ids de caso (default: todos)")
    args = parser.parse_args()
    run = collect(args.dataset, args.output_dir, args.only)
    print(json.dumps(run["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

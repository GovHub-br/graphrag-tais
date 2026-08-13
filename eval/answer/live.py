"""Collector de resposta: roda a pipeline REAL e pontua o SQL gerado.

Diferente do collector de retrieval, este **chama o LLM** — é o ponto do
benchmark. Por isso é separado: `eval.retrieval` continua barato e determinístico.

    python -m eval.answer.live --dataset <gold.json> --output <run.json>

O contexto disponível (tabelas, colunas, JOINs autorizados) vem do subgrafo que
a própria execução recuperou, não do gold: a pergunta é "o SQL respeitou o que
foi recuperado?", que é verificável em qualquer pergunta, com ou sem gold.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

from .metrics import aggregate, score_answer


def _duplicated_tables() -> dict[str, str]:
    """Tabelas cujo grão declara linhas duplicadas -> chave do grão.

    Lê de `OM_GRAIN_KEYS`, a mesma fonte que alimenta o contexto do LLM, para
    a métrica cobrar exatamente o que foi prometido ao modelo.
    """
    from rag.settings.configs import settings

    raw = (settings.OM_GRAIN_KEYS or "").strip()
    if not raw:
        return {}
    try:
        grain = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    out: dict[str, str] = {}
    for fqn, text in grain.items():
        if "DUPLICADAS" not in str(text).upper():
            continue
        parts = fqn.split(".")
        if len(parts) >= 2:
            physical = f"{parts[-2]}.{parts[-1]}".lower()
            out[physical] = text
    return out


def collect(dataset_path: str, output_path: str) -> dict:
    from rag.pipelines.query.pipeline_wrapper import PipelineWrapper

    dataset = json.load(open(dataset_path, encoding="utf-8"))
    duplicated = _duplicated_tables()

    wrapper = PipelineWrapper()
    wrapper.setup()

    cases = []
    for case in dataset["cases"]:
        started = time.time()
        error = ""
        answer: dict = {}
        docs = []
        joins = []
        try:
            result = wrapper._run(case["question"])
            answer = result["answer_builder"]["answer"]
            sub = result.get("subgraph_retriever", {})
            docs = sub.get("documents", [])
            joins = sub.get("joins", [])
        except Exception as exc:  # noqa: BLE001 — falha vira caso, não aborta o run
            error = f"{type(exc).__name__}: {exc}"

        tables = [d.meta.get("sql_name", "") for d in docs]
        columns = [
            c["name"] for d in docs for c in (d.meta.get("columns") or [])
        ]
        by_fqn = {d.meta.get("node_id"): d.meta.get("sql_name", "") for d in docs}
        pairs = [
            (by_fqn.get(j["left_table_fqn"], ""), by_fqn.get(j["right_table_fqn"], ""))
            for j in joins
        ]
        pairs = [(a, b) for a, b in pairs if a and b]

        scores = score_answer(
            answer.get("sql", ""),
            available_tables=tables,
            available_columns=columns,
            authorized_pairs=pairs,
            duplicated_tables=duplicated,
        )
        cases.append({
            "case_id": case["id"],
            "question": case["question"],
            "slices": case.get("slices", []),
            "sql": answer.get("sql", ""),
            "error": error,
            "latency_ms": round((time.time() - started) * 1000, 1),
            "metrics": scores,
        })
        flag = "" if scores.get("schema_faithfulness", 0) >= 1.0 else "  <<"
        print(f"  {case['id']:<38} fidelidade={scores.get('schema_faithfulness',0):.2f} "
              f"grão={scores.get('grain_compliance',0):.2f}{flag}")

    run = {
        "run_id": f"answer-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_name": dataset.get("name"),
        "dataset_version": dataset.get("version"),
        "dataset_status": dataset.get("status"),
        "duplicated_tables_known": sorted(duplicated),
        "cases": cases,
        "summary": aggregate([c["metrics"] for c in cases]),
    }
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(run, handle, ensure_ascii=False, indent=2)
    return run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run = collect(args.dataset, args.output)
    print("\nresumo:")
    for name, value in sorted(run["summary"].items()):
        print(f"  {name:<28} {value:.4f}")
    print(f"\n-> {args.output}")


if __name__ == "__main__":
    main()

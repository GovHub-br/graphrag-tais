"""Collector do modo Explorar: roda a pipeline REAL e guarda a resposta crua.

Como o `eval.answer.live`, este **chama o LLM** e precisa dos serviços (Qdrant,
Neo4j, Ollama, OpenAI) — roda dentro do container:

    docker exec -w /app -e PYTHONPATH=/app:/app/src hayhooks \\
        python -m eval.explore.live --dataset <gold.json> --output <run.json>

O run guarda a **resposta estruturada inteira**, não só as métricas: pontuar é
offline (`eval.explore.cli score`) e reprocessável. Isso é o que permite mudar
uma métrica depois e reavaliar runs antigos sem gastar LLM — e o que impede
ajustar a métrica olhando para o resultado de uma rodada nova a cada tentativa.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

from .metrics import aggregate, aggregate_by_slice, percentile, score_case
from .models import load_dataset


def _config_snapshot() -> dict:
    """Parâmetros que mudam o resultado, gravados junto do run.

    Um número sem a configuração que o produziu não é comparável com nada.
    """
    from rag.settings.configs import settings

    return {
        "explore_top_k": settings.EXPLORE_TOP_K,
        "explore_max_tables": settings.EXPLORE_MAX_TABLES,
        "explore_max_columns": settings.EXPLORE_MAX_COLUMNS,
        "explore_expand_neighbors": settings.EXPLORE_EXPAND_NEIGHBORS,
        "explore_llm_reorder": settings.EXPLORE_LLM_REORDER,
        "scope_filter_enabled": settings.SCOPE_FILTER_ENABLED,
        "retrieval_pool_size": settings.RETRIEVAL_POOL_SIZE,
        "openai_chat_model": settings.OPENAI_CHAT_MODEL,
        "ollama_embed_model": settings.OLLAMA_EMBED_MODEL,
        "card_schema_version": settings.OM_CARD_SCHEMA_VERSION,
        "scope_gaps_configured": bool((settings.SCOPE_GAPS or "").strip()),
    }


def collect(dataset_path: str, output_path: str) -> dict:
    from rag.pipelines.explore.pipeline_wrapper import PipelineWrapper

    dataset = load_dataset(json.loads(open(dataset_path, encoding="utf-8").read()))
    wrapper = PipelineWrapper()
    wrapper.setup()

    cases = []
    for case in dataset.cases:
        started = time.time()
        answer: dict = {}
        error = ""
        try:
            answer = wrapper._run(case.question)["answer_builder"]["answer"]
        except Exception as exc:  # noqa: BLE001 — falha vira caso, não aborta o run
            error = f"{type(exc).__name__}: {exc}"

        metrics = score_case(answer, case.as_gold())
        cases.append({
            "case_id": case.id,
            "question": case.question,
            "slices": case.slices,
            "critical": case.critical,
            "latency_ms": round((time.time() - started) * 1000, 1),
            "error": error,
            "answer": answer,
            "metrics": metrics,
        })
        flag = "" if metrics.get("core_recall_at_10", 0.0) >= 1.0 else "  <<"
        print(
            f"  {case.id:<34} core@10={metrics.get('core_recall_at_10', 0):.2f} "
            f"recall@10={metrics.get('table_recall_at_10', 0):.2f} "
            f"mrr@10={metrics.get('mrr_at_10', 0):.2f} "
            f"fid={metrics.get('catalog_fidelity', 0):.2f}{flag}"
        )

    latencies = [c["latency_ms"] for c in cases if c["latency_ms"]]
    summary = aggregate([c["metrics"] for c in cases])
    summary.update(
        case_count=float(len(cases)),
        error_rate=(sum(1.0 for c in cases if c["error"]) / len(cases)) if cases else 0.0,
        latency_p50_ms=percentile(latencies, 0.50),
        latency_p95_ms=percentile(latencies, 0.95),
    )
    run = {
        "run_id": f"explore-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset_name": dataset.name,
        "dataset_version": dataset.version,
        "dataset_status": dataset.status,
        "config": _config_snapshot(),
        "cases": cases,
        "summary": summary,
        "by_slice": aggregate_by_slice(cases),
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
    for name in ("table_recall_at_10", "core_recall_at_10", "mrr_at_10",
                 "ndcg_at_10", "map_at_10", "catalog_fidelity", "domain_recall",
                 "gap_disclosure", "listed_ratio", "latency_p95_ms"):
        print(f"  {name:<24} {run['summary'][name]:.4f}")
    if run["dataset_status"] != "accepted":
        print("\naviso: gold em `draft` — diagnóstico, não gate.")
    print(f"\n-> {args.output}")


if __name__ == "__main__":
    main()

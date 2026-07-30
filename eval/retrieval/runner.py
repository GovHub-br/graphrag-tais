"""Pure scoring runner for pre-LLM retrieval traces."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Iterable

from .metrics import (
    CASE_METRIC_NAMES,
    aggregate_case_results,
    seed_metrics,
    select_best_alternative,
)
from .models import (
    EvaluationReport,
    InvariantResult,
    RetrievalCase,
    RetrievalDataset,
    RetrievalRun,
    RetrievalTrace,
    RelationshipType,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _invariant(name: str, passed: bool, detail: str) -> InvariantResult:
    return InvariantResult(name=name, passed=passed, detail=detail)


def _case_result(
    case: RetrievalCase,
    trace: RetrievalTrace | None,
) -> dict[str, object]:
    base: dict[str, object] = {
        "case_id": case.id,
        "question": case.question,
        "critical": case.critical,
        "slices": list(case.slices),
    }
    if trace is None:
        return {
            **base,
            "chosen_gold_subgraph_id": None,
            "metrics": {metric: 0.0 for metric in CASE_METRIC_NAMES},
            "latency_ms": None,
            "error": "missing retrieval trace",
        }
    if trace.error is not None:
        return {
            **base,
            "chosen_gold_subgraph_id": None,
            "metrics": {metric: 0.0 for metric in CASE_METRIC_NAMES},
            "latency_ms": trace.total_latency_ms,
            "error": trace.error,
        }

    selected, graph_scores = select_best_alternative(
        trace,
        case.gold_subgraphs,
    )
    node_types = {
        node.id: node.type
        for alternative in case.gold_subgraphs
        for node in alternative.nodes
        if node.type is not None
    }
    scores = seed_metrics(
        trace.seeds,
        case.seed_relevance,
        node_types=node_types,
        selected_seed_node_ids=trace.selected_seed_node_ids,
    )
    scores.update(graph_scores)
    context_raw = trace.metadata.get("context", {})
    context = (
        {
            str(name): numeric
            for name, value in context_raw.items()
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(numeric := float(value))
            and numeric >= 0
        }
        if isinstance(context_raw, dict)
        else {}
    )
    invalid_context_fields = (
        sorted(
            str(name)
            for name, value in context_raw.items()
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (
                not math.isfinite(float(value))
                or float(value) < 0
            )
        )
        if isinstance(context_raw, dict)
        else []
    )
    return {
        **base,
        "chosen_gold_subgraph_id": selected.id,
        "metrics": scores,
        "latency_ms": trace.total_latency_ms,
        "timings_ms": dict(trace.timings_ms),
        "context": context,
        "invalid_context_fields": invalid_context_fields,
        "error": None,
        "counts": {
            "seeds": len(trace.seeds),
            "final_nodes": len(trace.final_nodes),
            "relationships": len(trace.relationships),
            "paths": len(trace.paths),
            "gold_nodes": len(selected.nodes),
            "gold_relationships": len(selected.relationships),
            "gold_paths": len(selected.paths),
        },
    }


def _slice_results(
    cases: Iterable[RetrievalCase],
    case_results: dict[str, dict[str, object]],
) -> dict[str, object]:
    names = sorted({name for case in cases for name in case.slices})
    return {
        name: aggregate_case_results(
            case_results[case.id]
            for case in cases
            if name in case.slices
        )
        for name in names
    }


def evaluate_run(
    dataset: RetrievalDataset,
    run: RetrievalRun,
    *,
    generated_at: str | None = None,
) -> EvaluationReport:
    """Score a run and preserve every validation result in the report."""

    expected_ids = {case.id for case in dataset.cases}
    actual_ids = {trace.case_id for trace in run.traces}
    missing_ids = sorted(expected_ids - actual_ids)
    extra_ids = sorted(actual_ids - expected_ids)
    trace_fingerprints = {trace.fingerprint for trace in run.traces}
    trace_scope_ids = {trace.scope_id for trace in run.traces}
    expected_scope_id = dataset.metadata.get("scope_id")
    run_scope_id = run.metadata.get("scope_id")
    unauthorized_join_uses: list[str] = []
    for trace in run.traces:
        relationships = {
            relationship.id: relationship
            for relationship in trace.relationships
        }
        authorized_ids = set(trace.authorized_join_relationship_ids)
        for graph_path in trace.paths:
            if graph_path.join_path:
                authorized_ids.update(graph_path.relationship_ids)
        for relationship_id in sorted(authorized_ids):
            relationship = relationships.get(relationship_id)
            if (
                relationship is None
                or relationship.type
                not in (
                    RelationshipType.FOREIGN_KEY,
                    RelationshipType.SEMANTIC_JOIN,
                )
                or not relationship.sql_join_eligible
            ):
                unauthorized_join_uses.append(
                    f"{trace.case_id}:{relationship_id}"
                )

    invariants = (
        _invariant(
            "dataset_name_matches",
            run.dataset_name == dataset.name,
            f"expected={dataset.name!r}, actual={run.dataset_name!r}",
        ),
        _invariant(
            "dataset_version_matches",
            run.dataset_version == dataset.version,
            f"expected={dataset.version!r}, actual={run.dataset_version!r}",
        ),
        _invariant(
            "dataset_hash_matches",
            run.dataset_hash == dataset.canonical_hash,
            f"expected={dataset.canonical_hash}, actual={run.dataset_hash}",
        ),
        _invariant(
            "case_ids_complete",
            not missing_ids and not extra_ids,
            f"missing={missing_ids}, extra={extra_ids}",
        ),
        _invariant(
            "trace_fingerprint_consistent",
            trace_fingerprints in ({run.fingerprint}, set()),
            (
                f"run={run.fingerprint!r}, "
                f"traces={sorted(trace_fingerprints)!r}"
            ),
        ),
        _invariant(
            "scope_id_matches",
            (
                isinstance(expected_scope_id, str)
                and bool(expected_scope_id)
                and run_scope_id == expected_scope_id
                and trace_scope_ids in ({expected_scope_id}, set())
            ),
            (
                f"dataset={expected_scope_id!r}, run={run_scope_id!r}, "
                f"traces={sorted(trace_scope_ids)!r}"
            ),
        ),
        _invariant(
            "authorized_joins_are_eligible",
            not unauthorized_join_uses,
            f"ineligible join usages={unauthorized_join_uses}",
        ),
    )

    traces_by_case = {trace.case_id: trace for trace in run.traces}
    results = {
        case.id: _case_result(case, traces_by_case.get(case.id))
        for case in dataset.cases
    }
    errors = [
        f"missing trace for case {case_id}" for case_id in missing_ids
    ]
    errors.extend(
        f"trace {trace.case_id}: {trace.error}"
        for trace in run.traces
        if trace.error is not None
    )
    return EvaluationReport(
        generated_at=generated_at or _utc_now(),
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        dataset_status=dataset.status,
        dataset_hash=dataset.canonical_hash,
        run_id=run.run_id,
        fingerprint=run.fingerprint,
        summary=aggregate_case_results(results.values()),
        slices=_slice_results(dataset.cases, results),
        cases=results,
        invariants=invariants,
        run_metadata=run.metadata,
        errors=tuple(errors),
    )


# A concise alias reads naturally in scripts and tests.
score_run = evaluate_run

"""Deterministic metrics for seed retrieval and graph expansion."""

from __future__ import annotations

import math
from typing import Iterable, Mapping, Sequence

from .models import (
    GoldSubgraph,
    GraphPath,
    GraphRelationship,
    NodeType,
    RankedSeed,
    RelationshipDirection,
    RelationshipType,
    RetrievalTrace,
)


CASE_METRIC_NAMES = (
    "seed_recall_at_5",
    "seed_precision_at_5",
    "seed_hit_at_5",
    "seed_table_recall_at_5",
    "seed_table_reachable_at_5",
    "seed_recall_ceiling_at_5",
    "seed_recall_headroom_at_5",
    "seed_column_recall_at_5",
    "seed_recall_at_10",
    "seed_precision_at_10",
    "seed_hit_at_10",
    "seed_table_recall_at_10",
    "seed_table_reachable_at_10",
    "seed_recall_ceiling_at_10",
    "seed_recall_headroom_at_10",
    "seed_column_recall_at_10",
    "mrr_at_10",
    "ndcg_at_10",
    "expansion_recall_gain",
    "marginal_expansion_precision",
    "node_precision",
    "node_recall",
    "node_f1",
    "table_recall",
    "column_recall",
    "relationship_precision",
    "relationship_recall",
    "relationship_f1",
    "eligible_join_relationship_recall",
    "join_eligibility_accuracy",
    "unauthorized_join_rate",
    "path_recall",
    "join_path_recall",
    "structural_sufficiency",
    "weighted_dependency_recall",
    "overexpansion",
    "subgraph_size_ratio",
    "disconnected_rate",
)


def safe_divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def harmonic_mean(precision: float, recall: float) -> float:
    return (
        2.0 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )


def _ordered_unique_seed_ids(seeds: Sequence[RankedSeed]) -> list[str]:
    ordered = sorted(seeds, key=lambda seed: seed.rank)
    seen: set[str] = set()
    result: list[str] = []
    for seed in ordered:
        if seed.node_id not in seen:
            result.append(seed.node_id)
            seen.add(seed.node_id)
    return result


def _resolve_to_tables(
    node_ids: Iterable[str],
    node_types: Mapping[str, NodeType] | None,
) -> set[str]:
    """Sobe de Column para a Table pai, como o runtime faz antes de expandir.

    Espelha ``_SEED_TABLES_CYPHER`` e o fallback por string do
    ``Neo4jSubgraphRetriever``: um card de coluna no top-k já traz a tabela
    dela para o subgrafo. Tipo desconhecido é tratado como coluna, igual à
    produção.
    """
    tables: set[str] = set()
    for node_id in node_ids:
        if (node_types or {}).get(node_id) is NodeType.TABLE:
            tables.add(node_id)
        else:
            parent, _, _ = node_id.rpartition(".")
            tables.add(parent or node_id)
    return tables


def seed_metrics(
    seeds: Sequence[RankedSeed],
    relevance: Mapping[str, float],
    node_types: Mapping[str, NodeType] | None = None,
    selected_seed_node_ids: Sequence[str] | None = None,
) -> dict[str, float]:
    ordered_ids = _ordered_unique_seed_ids(seeds)
    selected_ids = list(
        ordered_ids
        if selected_seed_node_ids is None
        else selected_seed_node_ids
    )[:5]
    relevant_ids = {
        node_id for node_id, grade in relevance.items() if grade > 0
    }
    metrics: dict[str, float] = {}
    for cutoff in (5, 10):
        # @5 measures the production call that actually entered graph
        # expansion. @10 and the rank-sensitive metrics measure the separate,
        # real diagnostic top-10 call preserved in ``seeds``.
        retrieved = (
            selected_ids
            if cutoff == 5
            else ordered_ids[:cutoff]
        )
        hits = len(set(retrieved) & relevant_ids)
        metrics[f"seed_recall_at_{cutoff}"] = safe_divide(
            hits,
            len(relevant_ids),
        )
        metrics[f"seed_precision_at_{cutoff}"] = safe_divide(
            hits,
            len(retrieved),
        )
        metrics[f"seed_hit_at_{cutoff}"] = float(hits > 0)
        for node_type, metric_prefix in (
            (NodeType.TABLE, "seed_table_recall"),
            (NodeType.COLUMN, "seed_column_recall"),
        ):
            expected = {
                node_id
                for node_id in relevant_ids
                if (node_types or {}).get(node_id) is node_type
            }
            metrics[f"{metric_prefix}_at_{cutoff}"] = (
                safe_divide(len(set(retrieved) & expected), len(expected))
                if expected
                else 1.0
            )

        # Recall de TABELA ALCANÇÁVEL — o que o runtime de fato alcança.
        # `seed_table_recall_*` exige o CARD DE TABELA no top-k, mas o
        # Neo4jSubgraphRetriever sobe de Column para a Table pai antes de
        # expandir: uma coluna no top-k já resolve a tabela dela. As duas
        # métricas convivem porque respondem perguntas diferentes — "o card
        # de tabela ranqueou?" e "a tabela-gold entrou no subgrafo?".
        gold_tables = {
            node_id
            for node_id in relevant_ids
            if (node_types or {}).get(node_id) is NodeType.TABLE
        }
        metrics[f"seed_table_reachable_at_{cutoff}"] = (
            safe_divide(
                len(gold_tables & _resolve_to_tables(retrieved, node_types)),
                len(gold_tables),
            )
            if gold_tables
            else 1.0
        )

        # Teto e aproveitamento do teto. `seed_recall` divide por TODAS as
        # sementes-gold, mas só existem `cutoff` posições: um caso com 14
        # sementes-gold tem teto 5/14 = 0,357. Publicar o teto evita ler 30%
        # como "70% faltando" quando parte da diferença é inalcançável por
        # construção.
        ceiling = safe_divide(min(cutoff, len(relevant_ids)), len(relevant_ids))
        metrics[f"seed_recall_ceiling_at_{cutoff}"] = ceiling
        metrics[f"seed_recall_headroom_at_{cutoff}"] = safe_divide(
            metrics[f"seed_recall_at_{cutoff}"],
            ceiling,
        )

    reciprocal_rank = 0.0
    for rank, node_id in enumerate(ordered_ids[:10], start=1):
        if node_id in relevant_ids:
            reciprocal_rank = 1.0 / rank
            break
    metrics["mrr_at_10"] = reciprocal_rank

    dcg = sum(
        (2.0 ** relevance.get(node_id, 0.0) - 1.0)
        / math.log2(rank + 1)
        for rank, node_id in enumerate(ordered_ids[:10], start=1)
    )
    ideal_grades = sorted(
        (grade for grade in relevance.values() if grade > 0),
        reverse=True,
    )[:10]
    ideal_dcg = sum(
        (2.0**grade - 1.0) / math.log2(rank + 1)
        for rank, grade in enumerate(ideal_grades, start=1)
    )
    metrics["ndcg_at_10"] = safe_divide(dcg, ideal_dcg)
    return metrics


def _relationship_identity_matches(
    retrieved: GraphRelationship,
    gold: GraphRelationship,
) -> bool:
    if retrieved.id != gold.id or retrieved.type is not gold.type:
        return False
    if retrieved.direction is not gold.direction:
        return False
    if gold.direction is RelationshipDirection.UNDIRECTED:
        return {
            retrieved.source,
            retrieved.target,
        } == {gold.source, gold.target}
    return (
        retrieved.source == gold.source
        and retrieved.target == gold.target
    )


def _relationship_matches(
    retrieved: GraphRelationship,
    gold: GraphRelationship,
) -> bool:
    return (
        _relationship_identity_matches(retrieved, gold)
        and retrieved.sql_join_eligible is gold.sql_join_eligible
    )


def _matched_relationship_ids(
    retrieved: Sequence[GraphRelationship],
    gold: Sequence[GraphRelationship],
) -> set[str]:
    return {
        expected.id
        for expected in gold
        if any(
            _relationship_matches(candidate, expected)
            for candidate in retrieved
        )
    }


def _path_matches(
    retrieved: GraphPath,
    gold: GraphPath,
    matched_relationship_ids: set[str],
) -> bool:
    if not set(gold.relationship_ids).issubset(matched_relationship_ids):
        return False
    return retrieved.relationship_ids == gold.relationship_ids


def _is_join_path(
    graph_path: GraphPath,
    relationships_by_id: Mapping[str, GraphRelationship],
) -> bool:
    if graph_path.join_path is not None:
        return graph_path.join_path
    relationships = [
        relationships_by_id[relationship_id]
        for relationship_id in graph_path.relationship_ids
        if relationship_id in relationships_by_id
    ]
    join_relationships = [
        relationship
        for relationship in relationships
        if relationship.type
        in (RelationshipType.FOREIGN_KEY, RelationshipType.SEMANTIC_JOIN)
    ]
    return bool(join_relationships) and all(
        relationship.sql_join_eligible
        for relationship in join_relationships
    )


def _recall_for_node_type(
    retrieved_ids: set[str],
    gold: GoldSubgraph,
    node_type: NodeType,
) -> float:
    expected = {node.id for node in gold.nodes if node.type is node_type}
    # A query with no dependency of this type has no opportunity to fail it.
    return safe_divide(len(retrieved_ids & expected), len(expected)) if expected else 1.0


def _disconnected_rate(trace: RetrievalTrace) -> float:
    node_ids = {node.id for node in trace.final_nodes}
    if len(node_ids) <= 1:
        return 0.0
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for relationship in trace.relationships:
        if (
            relationship.source in node_ids
            and relationship.target in node_ids
        ):
            adjacency[relationship.source].add(relationship.target)
            adjacency[relationship.target].add(relationship.source)
    unseen = set(node_ids)
    largest_component = 0
    while unseen:
        start = unseen.pop()
        stack = [start]
        size = 0
        while stack:
            current = stack.pop()
            size += 1
            neighbours = adjacency[current] & unseen
            unseen.difference_update(neighbours)
            stack.extend(neighbours)
        largest_component = max(largest_component, size)
    return safe_divide(len(node_ids) - largest_component, len(node_ids))


def graph_metrics(
    trace: RetrievalTrace,
    gold: GoldSubgraph,
) -> dict[str, float]:
    # A trace may preserve a deeper diagnostic ranking than the subset that
    # actually entered graph expansion. Expansion gain must use the selected
    # production seeds, otherwise relevant items observed only below runtime
    # top_k are incorrectly credited as graph inputs.
    # The parser supplies all ranked seeds only when the field is absent.
    # An explicitly empty selection means that graph expansion had no inputs
    # and must not silently fall back to the diagnostic ranking.
    seed_ids = set(trace.selected_seed_node_ids)
    retrieved_node_ids = {node.id for node in trace.final_nodes}
    gold_node_ids = {node.id for node in gold.nodes}

    seed_gold_recall = safe_divide(
        len(seed_ids & gold_node_ids),
        len(gold_node_ids),
    )
    node_recall = safe_divide(
        len(retrieved_node_ids & gold_node_ids),
        len(gold_node_ids),
    )
    node_precision = safe_divide(
        len(retrieved_node_ids & gold_node_ids),
        len(retrieved_node_ids),
    )
    added_ids = retrieved_node_ids - seed_ids

    matched_relationship_ids = _matched_relationship_ids(
        trace.relationships,
        gold.relationships,
    )
    retrieved_relationship_ids = {
        relationship.id for relationship in trace.relationships
    }
    gold_relationship_ids = {
        relationship.id for relationship in gold.relationships
    }
    relationship_precision = safe_divide(
        len(matched_relationship_ids),
        len(retrieved_relationship_ids),
    )
    relationship_recall = safe_divide(
        len(matched_relationship_ids),
        len(gold_relationship_ids),
    )
    if not gold_relationship_ids:
        relationship_recall = 1.0

    eligible_gold = {
        relationship.id
        for relationship in gold.relationships
        if relationship.sql_join_eligible
        and relationship.type
        in (RelationshipType.FOREIGN_KEY, RelationshipType.SEMANTIC_JOIN)
    }
    retrieved_eligible = {
        relationship.id
        for relationship in trace.relationships
        if relationship.sql_join_eligible
    }
    eligible_matches = (
        matched_relationship_ids & eligible_gold & retrieved_eligible
    )
    eligible_recall = (
        safe_divide(len(eligible_matches), len(eligible_gold))
        if eligible_gold
        else 1.0
    )
    gold_join_relationships = [
        relationship
        for relationship in gold.relationships
        if relationship.type
        in (RelationshipType.FOREIGN_KEY, RelationshipType.SEMANTIC_JOIN)
    ]
    correct_join_eligibility = sum(
        1
        for expected in gold_join_relationships
        if any(
            _relationship_identity_matches(candidate, expected)
            and candidate.sql_join_eligible
            is expected.sql_join_eligible
            for candidate in trace.relationships
        )
    )
    join_eligibility_accuracy = (
        safe_divide(
            correct_join_eligibility,
            len(gold_join_relationships),
        )
        if gold_join_relationships
        else 1.0
    )
    authorized_join_ids = set(trace.authorized_join_relationship_ids)
    authorized_join_ids.update(
        relationship_id
        for graph_path in trace.paths
        if graph_path.join_path
        for relationship_id in graph_path.relationship_ids
    )
    retrieved_by_id = {
        relationship.id: relationship for relationship in trace.relationships
    }
    unauthorized_join_count = sum(
        1
        for relationship_id in authorized_join_ids
        if (
            relationship_id not in retrieved_by_id
            or retrieved_by_id[relationship_id].type
            not in (
                RelationshipType.FOREIGN_KEY,
                RelationshipType.SEMANTIC_JOIN,
            )
            or not retrieved_by_id[relationship_id].sql_join_eligible
        )
    )

    matched_paths = {
        expected.stable_id
        for expected in gold.paths
        if any(
            _path_matches(
                candidate,
                expected,
                matched_relationship_ids,
            )
            for candidate in trace.paths
        )
    }
    path_recall = (
        safe_divide(len(matched_paths), len(gold.paths))
        if gold.paths
        else 1.0
    )
    relationships_by_id = {
        relationship.id: relationship for relationship in gold.relationships
    }
    gold_join_paths = {
        graph_path.stable_id
        for graph_path in gold.paths
        if _is_join_path(graph_path, relationships_by_id)
    }
    join_path_recall = (
        safe_divide(
            len(matched_paths & gold_join_paths),
            len(gold_join_paths),
        )
        if gold_join_paths
        else 1.0
    )

    required_nodes = {node.id for node in gold.nodes if node.required}
    required_relationships = {
        relationship.id
        for relationship in gold.relationships
        if relationship.required
    }
    required_paths = {
        graph_path.stable_id
        for graph_path in gold.paths
        if graph_path.required
    }
    structurally_sufficient = (
        required_nodes.issubset(retrieved_node_ids)
        and required_relationships.issubset(matched_relationship_ids)
        and required_paths.issubset(matched_paths)
    )

    recovered_weight = 0.0
    total_weight = 0.0
    for node in gold.nodes:
        total_weight += node.weight
        if node.id in retrieved_node_ids:
            recovered_weight += node.weight
    for relationship in gold.relationships:
        total_weight += relationship.weight
        if relationship.id in matched_relationship_ids:
            recovered_weight += relationship.weight
    for graph_path in gold.paths:
        total_weight += graph_path.weight
        if graph_path.stable_id in matched_paths:
            recovered_weight += graph_path.weight

    return {
        "expansion_recall_gain": node_recall - seed_gold_recall,
        "marginal_expansion_precision": safe_divide(
            len(added_ids & gold_node_ids),
            len(added_ids),
        ),
        "node_precision": node_precision,
        "node_recall": node_recall,
        "node_f1": harmonic_mean(node_precision, node_recall),
        "table_recall": _recall_for_node_type(
            retrieved_node_ids,
            gold,
            NodeType.TABLE,
        ),
        "column_recall": _recall_for_node_type(
            retrieved_node_ids,
            gold,
            NodeType.COLUMN,
        ),
        "relationship_precision": relationship_precision,
        "relationship_recall": relationship_recall,
        "relationship_f1": harmonic_mean(
            relationship_precision,
            relationship_recall,
        ),
        "eligible_join_relationship_recall": eligible_recall,
        "join_eligibility_accuracy": join_eligibility_accuracy,
        "unauthorized_join_rate": safe_divide(
            unauthorized_join_count,
            len(authorized_join_ids),
        ),
        "path_recall": path_recall,
        "join_path_recall": join_path_recall,
        "structural_sufficiency": float(structurally_sufficient),
        "weighted_dependency_recall": safe_divide(
            recovered_weight,
            total_weight,
        ),
        "overexpansion": safe_divide(
            len(retrieved_node_ids - gold_node_ids),
            len(gold_node_ids),
        ),
        "subgraph_size_ratio": safe_divide(
            len(retrieved_node_ids),
            len(gold_node_ids),
        ),
        "disconnected_rate": _disconnected_rate(trace),
    }


def score_against_alternative(
    trace: RetrievalTrace,
    gold: GoldSubgraph,
) -> dict[str, float]:
    return graph_metrics(trace, gold)


def select_best_alternative(
    trace: RetrievalTrace,
    alternatives: Sequence[GoldSubgraph],
) -> tuple[GoldSubgraph, dict[str, float]]:
    """Choose the fairest accepted gold interpretation deterministically.

    Weighted dependency recall is the primary objective.  Ties are broken by
    structural sufficiency, node recall, relationship recall, and finally the
    alternative ID so results remain reproducible regardless of input order.
    """

    scored = [
        (alternative, score_against_alternative(trace, alternative))
        for alternative in alternatives
    ]
    return max(
        scored,
        key=lambda item: (
            item[1]["weighted_dependency_recall"],
            item[1]["structural_sufficiency"],
            item[1]["node_recall"],
            item[1]["relationship_recall"],
            item[0].id,
        ),
    )


def percentile(values: Iterable[float], percentile_value: float) -> float:
    """Linear-interpolated percentile, equivalent to the common R-7 method."""

    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile_value
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def aggregate_case_results(
    case_results: Iterable[Mapping[str, object]],
) -> dict[str, float | int]:
    results = list(case_results)
    total = len(results)
    successful = [
        result
        for result in results
        if not bool(result.get("error"))
    ]
    summary: dict[str, float | int] = {
        "case_count": total,
        "successful_case_count": len(successful),
        "error_rate": safe_divide(total - len(successful), total),
    }
    for metric in CASE_METRIC_NAMES:
        values = [
            float(metrics[metric])
            for result in successful
            if isinstance((metrics := result.get("metrics")), Mapping)
            and metric in metrics
        ]
        summary[metric] = safe_divide(sum(values), len(values))
    latencies = [
        float(result["latency_ms"])
        for result in successful
        if isinstance(result.get("latency_ms"), (int, float))
    ]
    summary["latency_p50_ms"] = percentile(latencies, 0.50)
    summary["latency_p95_ms"] = percentile(latencies, 0.95)
    timing_names = sorted(
        {
            str(name)
            for result in successful
            if isinstance(result.get("timings_ms"), Mapping)
            for name in result["timings_ms"]
        }
    )
    for timing_name in timing_names:
        timing_values = [
            float(timings[timing_name])
            for result in successful
            if isinstance((timings := result.get("timings_ms")), Mapping)
            and isinstance(timings.get(timing_name), (int, float))
        ]
        summary[f"latency_{timing_name}_p50_ms"] = percentile(
            timing_values,
            0.50,
        )
        summary[f"latency_{timing_name}_p95_ms"] = percentile(
            timing_values,
            0.95,
        )
    context_names = sorted(
        {
            str(name)
            for result in successful
            if isinstance(result.get("context"), Mapping)
            for name in result["context"]
        }
    )
    for context_name in context_names:
        context_values = [
            float(context[context_name])
            for result in successful
            if isinstance((context := result.get("context")), Mapping)
            and isinstance(context.get(context_name), (int, float))
            and not isinstance(context.get(context_name), bool)
        ]
        if not context_values:
            continue
        prefix = f"context_{context_name}"
        summary[f"{prefix}_coverage_count"] = len(context_values)
        summary[f"{prefix}_coverage_rate"] = safe_divide(
            len(context_values),
            len(successful),
        )
        summary[f"{prefix}_mean"] = safe_divide(
            sum(context_values),
            len(context_values),
        )
        summary[f"{prefix}_p50"] = percentile(context_values, 0.50)
        summary[f"{prefix}_p95"] = percentile(context_values, 0.95)
        summary[f"{prefix}_max"] = max(context_values)
    return summary

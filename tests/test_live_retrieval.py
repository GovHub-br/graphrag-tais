from __future__ import annotations

from dataclasses import dataclass
import unittest

from eval.retrieval.live import (
    DIAGNOSTIC_TOP_K,
    SELECTED_TOP_K,
    LiveRetrievalError,
    LiveRetrievalRuntime,
    _qdrant_record,
    collect_retrieval_run,
)
from eval.retrieval.models import RetrievalDataset, RetrievalRun


SCOPE_ID = "synthetic"
FACT = "db.schema.fact"
FACT_DIMENSION_ID = f"{FACT}.dimension_id"
FACT_OTHER_ID = f"{FACT}.other_id"
DIMENSION = "db.schema.dimension"
DIMENSION_ID = f"{DIMENSION}.id"
DIMENSION_OTHER_ID = f"{DIMENSION}.other_id"


@dataclass
class FakeDocument:
    content: str
    meta: dict
    score: float | None = None
    id: str = ""


@dataclass
class FakePoint:
    id: str
    payload: dict


class FakeEmbedder:
    def __init__(self, output_name: str, value: object):
        self.output_name = output_name
        self.value = value
        self.calls: list[str] = []

    def run(self, *, text: str) -> dict:
        self.calls.append(text)
        return {self.output_name: self.value}


class FakeHybridRetriever:
    def __init__(
        self,
        selected: list[FakeDocument],
        diagnostic: list[FakeDocument],
    ):
        self.selected = selected
        self.diagnostic = diagnostic
        self.top_k_calls: list[int] = []

    def run(
        self,
        *,
        query_embedding: object,
        query_sparse_embedding: object,
        top_k: int,
    ) -> dict:
        self.top_k_calls.append(top_k)
        if top_k == SELECTED_TOP_K:
            return {"documents": self.selected}
        if top_k == DIAGNOSTIC_TOP_K:
            return {"documents": self.diagnostic}
        raise AssertionError(f"unexpected top_k: {top_k}")


class FakeDenseRetriever:
    def __init__(self, documents: list[FakeDocument]):
        self.documents = documents
        self.top_k_calls: list[int] = []

    def run(self, *, query_embedding: object, top_k: int) -> dict:
        self.top_k_calls.append(top_k)
        return {"documents": self.documents}


class FakeSparseRetriever:
    def __init__(self, documents: list[FakeDocument]):
        self.documents = documents
        self.top_k_calls: list[int] = []

    def run(self, *, query_sparse_embedding: object, top_k: int) -> dict:
        self.top_k_calls.append(top_k)
        return {"documents": self.documents}


class FakeSubgraphRetriever:
    max_seeds = 10

    def __init__(
        self,
        documents: list[FakeDocument],
        joins: list[dict],
    ):
        self.documents = documents
        self.joins = joins
        self.received_node_ids: list[str] = []

    def run(self, *, documents: list[FakeDocument]) -> dict:
        self.received_node_ids = [
            document.meta["node_id"] for document in documents
        ]
        return {"documents": self.documents, "joins": self.joins}


class FakeGraphStore:
    def __init__(
        self,
        has_column_rows: list[dict],
        semantic_rows: list[dict],
    ):
        self.has_column_rows = has_column_rows
        self.semantic_rows = semantic_rows
        self.calls: list[tuple[str, dict | None]] = []

    def execute_read(
        self,
        cypher: str,
        parameters: dict | None = None,
    ) -> list[dict]:
        self.calls.append((cypher, parameters))
        if "live_trace_has_column" in cypher:
            return self.has_column_rows
        if "live_trace_semantic_join" in cypher:
            return self.semantic_rows
        raise AssertionError("unexpected Cypher query")


def _card(node_id: str, card_type: str, score: float) -> FakeDocument:
    return FakeDocument(
        id=f"point:{node_id}",
        content=f"card {node_id}",
        score=score,
        meta={
            "node_id": node_id,
            "card_type": card_type,
            "scope_id": SCOPE_ID,
        },
    )


def _table_document(
    table_id: str,
    column_names: list[str],
) -> FakeDocument:
    content = "\n".join(
        [f"Tabela: {table_id}", "  Colunas:"]
        + [f"    - {name} TEXT" for name in column_names]
    )
    return FakeDocument(
        content=content,
        meta={
            "node_id": table_id,
            "label": "Table",
            "columns": [{"name": name} for name in column_names],
        },
    )


def _scope_fields() -> dict[str, str]:
    return {
        "table_scope_id": SCOPE_ID,
        "column_scope_id": SCOPE_ID,
        "relationship_scope_id": SCOPE_ID,
    }


def _ordered_diagnostic_top5() -> list[str]:
    return [
        FACT,
        FACT_DIMENSION_ID,
        DIMENSION,
        DIMENSION_ID,
        FACT_OTHER_ID,
    ]


def _dataset() -> RetrievalDataset:
    return RetrievalDataset.from_dict(
        {
            "schema_version": "1",
            "name": "live-retrieval-test",
            "version": "1.0.0",
            "status": "draft",
            "metadata": {"scope_id": SCOPE_ID},
            "cases": [
                {
                    "id": "business-001",
                    "question": "Quais fatos têm a maior dimensão?",
                    "seed_relevance": {
                        FACT: 3,
                        FACT_DIMENSION_ID: 3,
                    },
                    "gold_subgraphs": [
                        {
                            "id": "fact-dimension",
                            "nodes": [
                                {"id": FACT, "type": "Table"},
                                {
                                    "id": FACT_DIMENSION_ID,
                                    "type": "Column",
                                },
                                {"id": DIMENSION, "type": "Table"},
                                {"id": DIMENSION_ID, "type": "Column"},
                            ],
                            "relationships": [
                                {
                                    "id": "has-fact-dimension",
                                    "type": "HAS_COLUMN",
                                    "source": FACT,
                                    "target": FACT_DIMENSION_ID,
                                    "sql_join_eligible": False,
                                },
                                {
                                    "id": "has-dimension-id",
                                    "type": "HAS_COLUMN",
                                    "source": DIMENSION,
                                    "target": DIMENSION_ID,
                                    "sql_join_eligible": False,
                                },
                                {
                                    "id": "join-fact-dimension",
                                    "type": "SEMANTIC_JOIN",
                                    "source": FACT_DIMENSION_ID,
                                    "target": DIMENSION_ID,
                                    "sql_join_eligible": True,
                                },
                            ],
                            "paths": [
                                {
                                    "id": "fact-dimension-path",
                                    "relationship_ids": [
                                        "join-fact-dimension"
                                    ],
                                    "join_path": True,
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )


def _snapshot_provider(scope_id: str) -> dict:
    return {
        "snapshot_schema_version": "test",
        "scope_id": scope_id,
        "source_snapshot_hash": "source-v1",
        "graph_snapshot_hash": "graph-v1",
        "qdrant_snapshot_hash": "qdrant-v1",
        "ingestion_run_id": "ingestion-v1",
        "embedding_profile": "test-profile-v1",
        "card_schema_version": "v1",
        "source_version": "test-source-v1",
        "collection": "test-cards",
        "physical_collection": "test-cards-v1",
        "qdrant_point_count": 7,
        "graph_node_count": 6,
        "graph_relationship_count": 6,
    }


def _prompt_renderer(
    documents: list[FakeDocument],
    joins: list[dict],
    query: str,
) -> dict[str, str]:
    schema = "\n".join(document.content for document in documents)
    return {
        "0:system": "synthetic system prompt",
        "1:user": (
            f"# Schema\n{schema}\n"
            f"# Joins\n{len(joins)}\n"
            f"# Query\n{query}"
        ),
    }


def _runtime(
    *,
    configuration_suffix: str = "v1",
    omit_runtime_join: bool = False,
    swap_selected_tie: bool = False,
) -> tuple[LiveRetrievalRuntime, FakeHybridRetriever, FakeSubgraphRetriever]:
    diagnostic = [
        _card(FACT, "table", 0.90),
        _card(FACT_DIMENSION_ID, "column", 0.80),
        _card(DIMENSION, "table", 0.70),
        _card(DIMENSION_ID, "column", 0.60),
        _card(FACT_OTHER_ID, "column", 0.50),
        _card(DIMENSION_OTHER_ID, "column", 0.40),
        _card("db.schema.unrelated", "table", 0.30),
    ]
    selected = diagnostic[:SELECTED_TOP_K]
    if swap_selected_tie:
        selected = [
            selected[0],
            selected[1],
            selected[3],
            selected[2],
            selected[4],
        ]
    hybrid = FakeHybridRetriever(selected, diagnostic)

    table_documents = [
        _table_document(FACT, ["dimension_id", "other_id"]),
        _table_document(DIMENSION, ["id", "other_id"]),
    ]
    joins = [
        {
            # Runtime presentation order is deliberately opposite to the
            # canonical relationship direction returned by Neo4j.
            "left_table_fqn": DIMENSION,
            "left_col": "id",
            "right_table_fqn": FACT,
            "right_col": "dimension_id",
            "join_rule": "exact",
            "semantic_key": "dimension",
            "sql_join_eligible": True,
            "cardinality": "many_to_one",
            "confidence": "high",
        },
        {
            "left_table_fqn": DIMENSION,
            "left_col": "other_id",
            "right_table_fqn": FACT,
            "right_col": "other_id",
            "join_rule": "normalized_exact",
            "semantic_key": "other",
            "sql_join_eligible": False,
            "cardinality": "many_to_one",
            "confidence": "medium",
        },
    ]
    if omit_runtime_join:
        joins = joins[:1]
    subgraph = FakeSubgraphRetriever(table_documents, joins)

    has_column_rows = [
        {
            "table_id": FACT,
            "column_id": FACT_DIMENSION_ID,
            "column_name": "dimension_id",
            "ordinal_position": 1,
            "relationship_id": "has-fact-dimension",
            "sql_join_eligible": False,
            **_scope_fields(),
        },
        {
            "table_id": FACT,
            "column_id": FACT_OTHER_ID,
            "column_name": "other_id",
            "ordinal_position": 2,
            "relationship_id": "has-fact-other",
            "sql_join_eligible": False,
            **_scope_fields(),
        },
        {
            "table_id": DIMENSION,
            "column_id": DIMENSION_ID,
            "column_name": "id",
            "ordinal_position": 1,
            "relationship_id": "has-dimension-id",
            "sql_join_eligible": False,
            **_scope_fields(),
        },
        {
            "table_id": DIMENSION,
            "column_id": DIMENSION_OTHER_ID,
            "column_name": "other_id",
            "ordinal_position": 2,
            "relationship_id": "has-dimension-other",
            "sql_join_eligible": False,
            **_scope_fields(),
        },
    ]
    semantic_scope = {
        "source_scope_id": SCOPE_ID,
        "target_scope_id": SCOPE_ID,
        "relationship_scope_id": SCOPE_ID,
    }
    semantic_rows = [
        {
            "relationship_id": "join-fact-dimension",
            "source_node_id": FACT_DIMENSION_ID,
            "source_column_name": "dimension_id",
            "source_table_id": FACT,
            "target_node_id": DIMENSION_ID,
            "target_column_name": "id",
            "target_table_id": DIMENSION,
            "sql_join_eligible": True,
            "join_rule": "exact",
            "semantic_key": "dimension",
            "cardinality": "many_to_one",
            "confidence": "high",
            **semantic_scope,
        },
        {
            "relationship_id": "join-fact-other",
            "source_node_id": FACT_OTHER_ID,
            "source_column_name": "other_id",
            "source_table_id": FACT,
            "target_node_id": DIMENSION_OTHER_ID,
            "target_column_name": "other_id",
            "target_table_id": DIMENSION,
            "sql_join_eligible": False,
            "join_rule": "normalized_exact",
            "semantic_key": "other",
            "cardinality": "many_to_one",
            "confidence": "medium",
            **semantic_scope,
        },
    ]
    graph_store = FakeGraphStore(has_column_rows, semantic_rows)
    runtime = LiveRetrievalRuntime(
        dense_embedder=FakeEmbedder("embedding", [0.1, 0.2]),
        sparse_embedder=FakeEmbedder(
            "sparse_embedding",
            {"indices": [1], "values": [0.5]},
        ),
        hybrid_retriever=hybrid,
        dense_retriever=FakeDenseRetriever(
            [
                diagnostic[1],
                diagnostic[0],
                diagnostic[2],
                diagnostic[3],
            ]
        ),
        sparse_retriever=FakeSparseRetriever(
            [
                diagnostic[0],
                diagnostic[3],
                diagnostic[1],
                diagnostic[2],
            ]
        ),
        subgraph_retriever=subgraph,
        graph_store=graph_store,
        qdrant_store=object(),
        collection="test-cards",
        configuration={
            "embedding_profile": f"test-profile-{configuration_suffix}",
            "scope_filter": None,
        },
        snapshot_provider=_snapshot_provider,
        prompt_renderer=_prompt_renderer,
    )
    return runtime, hybrid, subgraph


class LiveRetrievalCollectorTest(unittest.TestCase):
    def _collect(
        self,
        runtime: LiveRetrievalRuntime,
        *,
        run_id: str = "live-test-run",
        retrieval_channel: str = "hybrid",
    ) -> RetrievalRun:
        return collect_retrieval_run(
            _dataset(),
            runtime=runtime,
            run_id=run_id,
            created_at="2026-07-30T12:00:00Z",
            code_revision="abc123",
            worktree_state="clean",
            environment_class="unit-test",
            dependency_versions={"test-runtime": "1"},
            retrieval_channel=retrieval_channel,
        )

    def test_collects_real_top5_and_keeps_top10_as_diagnostic(self) -> None:
        runtime, hybrid, subgraph = _runtime()
        run = self._collect(runtime)
        trace = run.traces[0]

        self.assertIsNone(trace.error)
        self.assertEqual(hybrid.top_k_calls, [SELECTED_TOP_K, DIAGNOSTIC_TOP_K])
        self.assertEqual(
            subgraph.received_node_ids,
            list(trace.selected_seed_node_ids),
        )
        self.assertEqual(len(trace.seeds), 7)
        self.assertEqual(trace.selected_seed_node_ids, tuple(subgraph.received_node_ids))

        seed_by_id = {seed.node_id: seed for seed in trace.seeds}
        self.assertEqual(seed_by_id[FACT].dense_rank, 2)
        self.assertEqual(seed_by_id[FACT].sparse_rank, 1)
        self.assertEqual(seed_by_id[FACT].score, 0.90)
        signals = trace.metadata["signal_diagnostics"]
        self.assertIn("lateral diagnostic", signals["warning"])
        self.assertEqual(signals["dense"]["source"], "lateral_diagnostic_query")
        self.assertEqual(signals["sparse"]["source"], "lateral_diagnostic_query")

        # The serialized context contains two tables and every one of their
        # columns, so every HAS_COLUMN must be represented.
        self.assertEqual(len(trace.final_nodes), 6)
        has_columns = [
            relationship
            for relationship in trace.relationships
            if relationship.type.value == "HAS_COLUMN"
        ]
        self.assertEqual(len(has_columns), 4)
        semantic = [
            relationship
            for relationship in trace.relationships
            if relationship.type.value == "SEMANTIC_JOIN"
        ]
        self.assertEqual(len(semantic), 2)
        canonical_join = next(
            item for item in semantic if item.id == "join-fact-dimension"
        )
        self.assertEqual(canonical_join.source, FACT_DIMENSION_ID)
        self.assertEqual(canonical_join.target, DIMENSION_ID)
        self.assertEqual(
            trace.authorized_join_relationship_ids,
            ("join-fact-dimension",),
        )
        self.assertEqual(
            {path.join_path for path in trace.paths},
            {True, False},
        )
        self.assertEqual(
            trace.serialized_context_node_ids,
            tuple(node.id for node in trace.final_nodes),
        )
        context = trace.metadata["context"]
        self.assertEqual(context["document_count"], 2)
        self.assertEqual(context["join_count"], 2)
        self.assertEqual(context["column_node_count"], 4)
        self.assertEqual(
            context["schema_context_chars"],
            context["document_content_chars"],
        )
        self.assertGreater(
            context["rendered_llm_prompt_chars"],
            context["schema_context_chars"],
        )
        self.assertGreater(context["rendered_user_prompt_chars"], 0)
        self.assertGreater(context["rendered_system_prompt_chars"], 0)
        self.assertGreater(context["document_content_chars"], 0)

        self.assertIn("total", trace.timings_ms)
        self.assertIn("collector_total", trace.timings_ms)
        self.assertIn("graph_introspection", trace.timings_ms)
        self.assertEqual(run.metadata["scope_id"], SCOPE_ID)
        self.assertEqual(run.metadata["source_snapshot_hash"], "source-v1")
        self.assertEqual(
            RetrievalRun.from_dict(run.to_dict()).to_dict(),
            run.to_dict(),
        )

    def test_fingerprint_is_deterministic_and_tracks_configuration(self) -> None:
        first_runtime, _, _ = _runtime(configuration_suffix="v1")
        second_runtime, _, _ = _runtime(configuration_suffix="v1")
        changed_runtime, _, _ = _runtime(configuration_suffix="v2")

        first = self._collect(first_runtime, run_id="run-a")
        second = self._collect(second_runtime, run_id="run-b")
        changed = self._collect(changed_runtime, run_id="run-c")

        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertNotEqual(first.fingerprint, changed.fingerprint)
        self.assertNotIn("run_id", first.metadata["fingerprint_payload"])

    def test_production_top5_is_separate_from_real_diagnostic_ranking(self) -> None:
        runtime, hybrid, subgraph = _runtime(swap_selected_tie=True)
        run = self._collect(runtime)
        trace = run.traces[0]

        self.assertIsNone(trace.error)
        expected_top5 = subgraph.received_node_ids
        self.assertEqual(
            [seed.node_id for seed in trace.seeds[:SELECTED_TOP_K]],
            _ordered_diagnostic_top5(),
        )
        self.assertEqual(trace.selected_seed_node_ids, tuple(expected_top5))
        selected_metadata = trace.metadata["selected_ranking"]
        self.assertFalse(selected_metadata["matches_diagnostic_prefix"])
        self.assertNotEqual(
            selected_metadata["node_ids"],
            selected_metadata["diagnostic_prefix_node_ids"],
        )
        self.assertEqual(
            trace.metadata["ranked_seeds"]["composition"],
            "diagnostic_top10_call",
        )
        self.assertEqual(
            hybrid.top_k_calls,
            [SELECTED_TOP_K, DIAGNOSTIC_TOP_K],
        )

    def test_snapshot_rejects_partially_missing_identity(self) -> None:
        runtime, _, _ = _runtime()

        def incomplete_snapshot(scope_id: str) -> dict:
            snapshot = _snapshot_provider(scope_id)
            snapshot["embedding_profile"] = ""
            return snapshot

        runtime.snapshot_provider = incomplete_snapshot
        with self.assertRaisesRegex(
            LiveRetrievalError,
            "embedding_profile",
        ):
            self._collect(runtime)

    def test_blank_seed_scope_becomes_an_error_trace(self) -> None:
        runtime, _, _ = _runtime()
        runtime.hybrid_retriever.selected[0].meta["scope_id"] = ""

        run = self._collect(runtime)
        trace = run.traces[0]

        self.assertIsNotNone(trace.error)
        self.assertEqual(
            trace.metadata["failed_stage"],
            "selected_document_validation",
        )

    def test_selected_seed_without_node_id_fails_before_graph_expansion(
        self,
    ) -> None:
        runtime, _, subgraph = _runtime()
        runtime.hybrid_retriever.selected[0].meta.pop("node_id")

        run = self._collect(runtime)
        trace = run.traces[0]

        self.assertIsNotNone(trace.error)
        self.assertEqual(
            trace.metadata["failed_stage"],
            "selected_document_validation",
        )
        self.assertIn("missing node_id", trace.error)
        self.assertEqual(subgraph.received_node_ids, [])

    def test_qdrant_snapshot_record_rejects_missing_content_hash(
        self,
    ) -> None:
        point = FakePoint(
            id="point-1",
            payload={
                "meta": {
                    "node_id": FACT,
                    "scope_id": SCOPE_ID,
                    "content_hash": "",
                    "card_type": "table",
                    "card_schema_version": "v1",
                    "embedding_profile": "test-profile-v1",
                    "ingestion_run_id": "ingestion-v1",
                    "source_version": "source-v1",
                }
            },
        )

        with self.assertRaisesRegex(
            LiveRetrievalError,
            "content_hash",
        ):
            _qdrant_record(point, index=0)

    def test_selected_seed_missing_from_diagnostic_ranking_is_not_synthesized(
        self,
    ) -> None:
        runtime, hybrid, _ = _runtime()
        hybrid.selected[0] = _card(
            "db.schema.only-selected",
            "table",
            0.90,
        )

        run = self._collect(runtime)
        trace = run.traces[0]

        self.assertIsNotNone(trace.error)
        self.assertEqual(trace.metadata["failed_stage"], "ranking_consistency")
        self.assertIn("absent from the diagnostic", trace.error)

    def test_dense_channel_uses_dense_top5_for_graph_expansion(self) -> None:
        runtime, hybrid, subgraph = _runtime()
        run = self._collect(runtime, retrieval_channel="dense")
        trace = run.traces[0]

        self.assertIsNone(trace.error)
        self.assertEqual(hybrid.top_k_calls, [DIAGNOSTIC_TOP_K])
        self.assertEqual(
            runtime.dense_retriever.top_k_calls,
            [SELECTED_TOP_K, DIAGNOSTIC_TOP_K],
        )
        self.assertEqual(
            subgraph.received_node_ids,
            [FACT_DIMENSION_ID, FACT, DIMENSION, DIMENSION_ID],
        )
        self.assertEqual(
            trace.selected_seed_node_ids,
            tuple(subgraph.received_node_ids),
        )
        self.assertEqual(trace.metadata["retrieval_channel"], "dense")
        self.assertEqual(run.metadata["retrieval_channel"], "dense")
        self.assertEqual(
            run.metadata["retrieval_configuration"]["retrieval_channel"],
            "dense",
        )

    def test_graph_context_mismatch_becomes_a_valid_error_trace(self) -> None:
        runtime, _, _ = _runtime(omit_runtime_join=True)
        run = self._collect(runtime)
        trace = run.traces[0]

        self.assertIsNotNone(trace.error)
        self.assertEqual(trace.metadata["failed_stage"], "graph_introspection")
        self.assertEqual(trace.seeds, ())
        self.assertEqual(trace.final_nodes, ())
        self.assertEqual(
            RetrievalRun.from_dict(run.to_dict()).to_dict(),
            run.to_dict(),
        )


if __name__ == "__main__":
    unittest.main()

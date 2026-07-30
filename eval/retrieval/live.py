"""Read-only collector for real, pre-LLM retrieval runs.

This module deliberately does not calculate retrieval metrics.  It executes
the same embedding, Qdrant and Neo4j components used by the query runtime,
captures their observable outputs, and returns a validated ``RetrievalRun``.
The deterministic evaluator in :mod:`eval.retrieval.runner` remains the only
place that turns those observations into metrics.

Haystack and project runtime dependencies are imported only by
``build_default_runtime``.  Importing this module therefore remains safe in the
offline evaluation/test environment.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence

from .models import (
    GraphNode,
    GraphPath,
    GraphRelationship,
    NodeType,
    RankedSeed,
    RelationshipDirection,
    RelationshipType,
    RetrievalDataset,
    RetrievalRun,
    RetrievalTrace,
    canonical_hash,
    canonical_json,
)


SELECTED_TOP_K = 5
DIAGNOSTIC_TOP_K = 10
TRACE_SCHEMA_VERSION = "1"
FINGERPRINT_SCHEMA_VERSION = "live-retrieval-v1"
RETRIEVAL_CHANNELS = ("hybrid", "dense", "sparse")


_HAS_COLUMN_CYPHER = """
/* live_trace_has_column */
UNWIND $tids AS tid
MATCH (t:Table {node_id: tid})-[r:HAS_COLUMN]->(c:Column)
RETURN t.node_id AS table_id,
       t.scope_id AS table_scope_id,
       c.node_id AS column_id,
       c.name AS column_name,
       c.ordinal_position AS ordinal_position,
       c.scope_id AS column_scope_id,
       r.relationship_id AS relationship_id,
       r.scope_id AS relationship_scope_id,
       coalesce(r.sql_join_eligible, false) AS sql_join_eligible
ORDER BY table_id, ordinal_position, column_id, relationship_id
"""


_SEMANTIC_JOIN_CYPHER = """
/* live_trace_semantic_join */
UNWIND $tids AS tid
MATCH (t1:Table {node_id: tid})-[:HAS_COLUMN]->(c1:Column)
      -[r:SEMANTIC_JOIN]-(c2:Column)<-[:HAS_COLUMN]-(t2:Table)
WHERE t2.node_id IN $tids AND t1.node_id < t2.node_id
WITH DISTINCT r
MATCH (source:Column)-[r]->(target:Column)
MATCH (source_table:Table)-[:HAS_COLUMN]->(source)
MATCH (target_table:Table)-[:HAS_COLUMN]->(target)
RETURN r.relationship_id AS relationship_id,
       r.scope_id AS relationship_scope_id,
       source.node_id AS source_node_id,
       source.name AS source_column_name,
       source.scope_id AS source_scope_id,
       source_table.node_id AS source_table_id,
       target.node_id AS target_node_id,
       target.name AS target_column_name,
       target.scope_id AS target_scope_id,
       target_table.node_id AS target_table_id,
       coalesce(r.sql_join_eligible, false) AS sql_join_eligible,
       r.join_rule AS join_rule,
       r.semantic_key AS semantic_key,
       r.cardinality AS cardinality,
       r.confidence AS confidence
ORDER BY relationship_id
"""


_FINGERPRINT_NODES_CYPHER = """
/* live_fingerprint_nodes */
MATCH (n)
WHERE n:Table OR n:Column
RETURN CASE WHEN n:Table THEN 'Table' ELSE 'Column' END AS label,
       n.node_id AS node_id,
       properties(n) AS properties
ORDER BY label, node_id
"""


_FINGERPRINT_RELATIONSHIPS_CYPHER = """
/* live_fingerprint_relationships */
MATCH (source)-[r]->(target)
WHERE type(r) IN ['HAS_COLUMN', 'FOREIGN_KEY', 'SEMANTIC_JOIN']
RETURN r.relationship_id AS relationship_id,
       type(r) AS relationship_type,
       source.node_id AS source_node_id,
       target.node_id AS target_node_id,
       properties(r) AS properties
ORDER BY relationship_type, relationship_id
"""


class LiveRetrievalError(RuntimeError):
    """Raised when a live observation cannot be represented honestly."""


SnapshotProvider = Callable[[str], Mapping[str, Any]]
PromptRenderer = Callable[
    [Sequence[Any], Sequence[Mapping[str, Any]], str],
    Mapping[str, str],
]


@dataclass
class LiveRetrievalRuntime:
    """Runtime components needed by the read-only collector.

    Tests can inject small fakes.  Production callers normally use
    :func:`build_default_runtime`, whose imports are intentionally lazy.
    """

    dense_embedder: Any
    sparse_embedder: Any
    hybrid_retriever: Any
    dense_retriever: Any
    sparse_retriever: Any
    subgraph_retriever: Any
    graph_store: Any
    qdrant_store: Any
    collection: str
    configuration: dict[str, Any] = field(default_factory=dict)
    snapshot_provider: SnapshotProvider | None = None
    prompt_renderer: PromptRenderer | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _generated_run_id(created_at: str) -> str:
    compact = (
        created_at.replace("-", "")
        .replace(":", "")
        .replace(".", "")
        .replace("+00:00", "Z")
    )
    return f"live-retrieval-{compact}"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [_json_safe(item) for item in value]
    return str(value)


def _elapsed_ms(started_at: float) -> float:
    return (perf_counter() - started_at) * 1000.0


def _node_id(document: Any) -> str:
    meta = getattr(document, "meta", None) or {}
    return str(meta.get("node_id") or "").strip()


def _document_scope(document: Any) -> str:
    meta = getattr(document, "meta", None) or {}
    return str(meta.get("scope_id") or "").strip()


def _ordered_document_ids(documents: Sequence[Any]) -> list[str]:
    result: list[str] = []
    for document in documents:
        node_id = _node_id(document)
        if node_id and node_id not in result:
            result.append(node_id)
    return result


def _ordered_unique_documents(documents: Sequence[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for document in documents:
        node_id = _node_id(document)
        if node_id and node_id not in seen:
            result.append(document)
            seen.add(node_id)
    return result


def _card_type(document: Any) -> NodeType:
    meta = getattr(document, "meta", None) or {}
    raw = str(meta.get("card_type") or meta.get("label") or "").lower()
    if raw == "table":
        return NodeType.TABLE
    if raw == "column":
        return NodeType.COLUMN
    raise LiveRetrievalError(
        f"Qdrant document {_node_id(document)!r} has invalid card_type {raw!r}"
    )


def _score(document: Any) -> float | None:
    value = getattr(document, "score", None)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LiveRetrievalError(
            f"document {_node_id(document)!r} has a non-numeric score"
        )
    return float(value)


def _validate_retrieved_documents(
    documents: Sequence[Any],
    *,
    source: str,
    scope_id: str,
) -> None:
    for index, document in enumerate(documents):
        node_id = _node_id(document)
        if not node_id:
            raise LiveRetrievalError(
                f"{source} document at index {index} is missing node_id"
            )
        point_id = str(getattr(document, "id", "") or "").strip()
        if not point_id:
            raise LiveRetrievalError(
                f"{source} document {node_id!r} is missing point_id"
            )
        document_scope = _document_scope(document)
        if document_scope != scope_id:
            raise LiveRetrievalError(
                f"{source} document {node_id!r} has scope_id "
                f"{document_scope!r}; expected {scope_id!r}"
            )
        _card_type(document)
        _score(document)


def _diagnostic_results(documents: Sequence[Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for rank, document in enumerate(documents, start=1):
        node_id = _node_id(document)
        if not node_id:
            continue
        item: dict[str, Any] = {"node_id": node_id, "rank": rank}
        score = _score(document)
        if score is not None:
            item["score"] = score
        results.append(item)
    return results


def _rank_lookup(documents: Sequence[Any]) -> dict[str, int]:
    return {
        item["node_id"]: int(item["rank"])
        for item in _diagnostic_results(documents)
    }


def _timed(call: Callable[[], Any]) -> tuple[Any, float]:
    started_at = perf_counter()
    result = call()
    return result, _elapsed_ms(started_at)


def _run_retriever(
    runtime: LiveRetrievalRuntime,
    *,
    channel: str,
    dense_embedding: Any,
    sparse_embedding: Any,
    top_k: int,
) -> dict[str, Any]:
    if channel == "hybrid":
        return runtime.hybrid_retriever.run(
            query_embedding=dense_embedding,
            query_sparse_embedding=sparse_embedding,
            top_k=top_k,
        )
    if channel == "dense":
        return runtime.dense_retriever.run(
            query_embedding=dense_embedding,
            top_k=top_k,
        )
    if channel == "sparse":
        return runtime.sparse_retriever.run(
            query_sparse_embedding=sparse_embedding,
            top_k=top_k,
        )
    raise ValueError(
        f"retrieval_channel must be one of {RETRIEVAL_CHANNELS!r}"
    )


def _dependency_versions() -> dict[str, str]:
    packages = (
        "haystack-ai",
        "qdrant-haystack",
        "qdrant-client",
        "neo4j",
        "ollama-haystack",
        "fastembed-haystack",
    )
    versions: dict[str, str] = {}
    for package in packages:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "unavailable"
    return versions


def build_default_runtime(
    collection: str | None = None,
) -> LiveRetrievalRuntime:
    """Build the current production retrieval components lazily.

    No generator, SQL executor or chart component is instantiated here. The
    production prompt builder is instantiated only to render and measure the
    exact pre-LLM messages; it never calls the model.
    """

    from haystack_integrations.components.retrievers.qdrant import (
        QdrantEmbeddingRetriever,
        QdrantSparseEmbeddingRetriever,
    )

    from rag.components.embedders.fastembed_sparse_text_embedder import (
        FastembedSparseTextEmbedderFactory,
    )
    from rag.components.embedders.ollama_text_embedder import (
        OllamaTextEmbedderFactory,
    )
    from rag.components.graphstore.neo4j import Neo4jGraphStoreFactory
    from rag.components.retrievers.neo4j_subgraph_retriever import (
        Neo4jSubgraphRetriever,
    )
    from rag.components.retrievers.qdrant_hybrid_retriever import (
        QdrantHybridRetrieverFactory,
    )
    from rag.components.vectorstore.qdrant import QdrantStoreFactory
    from rag.pipelines.query.query_pipeline import SYSTEM_PROMPT, USER_PROMPT
    from rag.settings.configs import settings
    from haystack.components.builders import ChatPromptBuilder
    from haystack.dataclasses import ChatMessage

    resolved_collection = collection or settings.QDRANT_COLLECTION
    qdrant_store = QdrantStoreFactory(index=resolved_collection).build()
    graph_store = Neo4jGraphStoreFactory().build()
    sparse_embedder = FastembedSparseTextEmbedderFactory().build()
    if hasattr(sparse_embedder, "warm_up"):
        sparse_embedder.warm_up()

    subgraph_retriever = Neo4jSubgraphRetriever(graph_store=graph_store)
    prompt_builder = ChatPromptBuilder(
        template=[
            ChatMessage.from_system(SYSTEM_PROMPT),
            ChatMessage.from_user(USER_PROMPT),
        ],
        required_variables=["documents", "query"],
        variables=["documents", "joins", "query"],
    )

    def render_prompt(
        documents: Sequence[Any],
        joins: Sequence[Mapping[str, Any]],
        query: str,
    ) -> Mapping[str, str]:
        messages = prompt_builder.run(
            documents=documents,
            joins=joins,
            query=query,
        )["prompt"]
        return {
            f"{index}:{message.role.value}": str(message.text or "")
            for index, message in enumerate(messages)
        }

    return LiveRetrievalRuntime(
        dense_embedder=OllamaTextEmbedderFactory().build(),
        sparse_embedder=sparse_embedder,
        hybrid_retriever=QdrantHybridRetrieverFactory(
            qdrant_store,
            top_k=DIAGNOSTIC_TOP_K,
        ).build(),
        dense_retriever=QdrantEmbeddingRetriever(
            document_store=qdrant_store,
            top_k=DIAGNOSTIC_TOP_K,
        ),
        sparse_retriever=QdrantSparseEmbeddingRetriever(
            document_store=qdrant_store,
            top_k=DIAGNOSTIC_TOP_K,
        ),
        subgraph_retriever=subgraph_retriever,
        graph_store=graph_store,
        qdrant_store=qdrant_store,
        collection=resolved_collection,
        configuration={
            "dense_embedding_model": settings.OLLAMA_EMBED_MODEL,
            "sparse_embedding_model": settings.QDRANT_SPARSE_MODEL,
            "embedding_profile": settings.OM_EMBEDDING_PROFILE,
            "embedding_dimension": settings.QDRANT_EMBEDDING_DIM,
            "similarity": settings.QDRANT_SIMILARITY,
            "card_schema_version": settings.OM_CARD_SCHEMA_VERSION,
            "qdrant_grpc": settings.QDRANT_GRPC,
            "scope_filter": None,
        },
        prompt_renderer=render_prompt,
    )


def _qdrant_points(runtime: LiveRetrievalRuntime) -> list[Any]:
    store = runtime.qdrant_store
    initializer = getattr(store, "_initialize_client", None)
    if callable(initializer):
        initializer()
    client = getattr(store, "_client", None)
    if client is None:
        raise LiveRetrievalError("Qdrant document store did not expose a client")

    points: list[Any] = []
    offset: Any = None
    seen_offsets: set[str] = set()
    while True:
        page, next_offset = client.scroll(
            collection_name=runtime.collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points.extend(page)
        if next_offset is None:
            break
        marker = repr(next_offset)
        if marker in seen_offsets:
            raise LiveRetrievalError("Qdrant scroll returned a repeated offset")
        seen_offsets.add(marker)
        offset = next_offset
    return points


def _physical_collection(runtime: LiveRetrievalRuntime) -> str:
    store = runtime.qdrant_store
    initializer = getattr(store, "_initialize_client", None)
    if callable(initializer):
        initializer()
    client = getattr(store, "_client", None)
    if client is None:
        return runtime.collection
    try:
        response = client.get_aliases()
    except Exception:
        return runtime.collection
    for alias in getattr(response, "aliases", ()) or ():
        if getattr(alias, "alias_name", None) == runtime.collection:
            return str(getattr(alias, "collection_name"))
    return runtime.collection


def _point_payload(point: Any) -> Mapping[str, Any]:
    payload = getattr(point, "payload", None) or {}
    return payload if isinstance(payload, Mapping) else {}


def _point_meta(point: Any) -> Mapping[str, Any]:
    payload = _point_payload(point)
    meta = payload.get("meta", payload)
    return meta if isinstance(meta, Mapping) else {}


def _qdrant_record(point: Any, *, index: int) -> dict[str, str]:
    meta = _point_meta(point)
    record = {
        "point_id": str(getattr(point, "id", "") or "").strip(),
        "node_id": str(meta.get("node_id") or "").strip(),
        "scope_id": str(meta.get("scope_id") or "").strip(),
        "content_hash": str(meta.get("content_hash") or "").strip(),
        "card_type": str(meta.get("card_type") or "").strip().lower(),
        "card_schema_version": str(
            meta.get("card_schema_version") or ""
        ).strip(),
        "embedding_profile": str(
            meta.get("embedding_profile") or ""
        ).strip(),
        "ingestion_run_id": str(
            meta.get("ingestion_run_id") or ""
        ).strip(),
        "source_version": str(
            meta.get("source_version") or ""
        ).strip(),
    }
    missing = [name for name, value in record.items() if not value]
    if missing:
        raise LiveRetrievalError(
            f"Qdrant point at index {index} is missing required metadata: "
            + ", ".join(missing)
        )
    if record["card_type"] not in {"table", "column"}:
        raise LiveRetrievalError(
            f"Qdrant point {record['point_id']!r} has invalid card_type "
            f"{record['card_type']!r}"
        )
    return record


def _single_value(
    values: set[str],
    *,
    name: str,
    expected: str | None = None,
) -> str:
    normalized = {str(value).strip() for value in values}
    if not normalized or "" in normalized:
        raise LiveRetrievalError(
            f"{name} is missing from one or more records"
        )
    cleaned = normalized
    if len(cleaned) != 1:
        raise LiveRetrievalError(
            f"expected one {name}, found {sorted(cleaned)!r}"
        )
    value = next(iter(cleaned))
    if expected is not None and value != expected:
        raise LiveRetrievalError(
            f"{name} mismatch: expected {expected!r}, found {value!r}"
        )
    return value


def _default_snapshot(
    runtime: LiveRetrievalRuntime,
    scope_id: str,
) -> dict[str, Any]:
    points = _qdrant_points(runtime)
    if not points:
        raise LiveRetrievalError("Qdrant collection is empty")

    qdrant_records: list[dict[str, Any]] = []
    qdrant_scopes: set[str] = set()
    qdrant_runs: set[str] = set()
    profiles: set[str] = set()
    card_versions: set[str] = set()
    source_versions: set[str] = set()
    for index, point in enumerate(points):
        record = _qdrant_record(point, index=index)
        qdrant_scopes.add(record["scope_id"])
        qdrant_runs.add(record["ingestion_run_id"])
        profiles.add(record["embedding_profile"])
        card_versions.add(record["card_schema_version"])
        source_versions.add(record["source_version"])
        qdrant_records.append(record)

    _single_value(qdrant_scopes, name="Qdrant scope_id", expected=scope_id)
    qdrant_run = _single_value(
        qdrant_runs,
        name="Qdrant ingestion_run_id",
    )
    embedding_profile = _single_value(
        profiles,
        name="Qdrant embedding_profile",
    )
    card_schema_version = _single_value(
        card_versions,
        name="Qdrant card_schema_version",
    )
    source_version = _single_value(
        source_versions,
        name="Qdrant source_version",
    )

    raw_nodes = runtime.graph_store.execute_read(_FINGERPRINT_NODES_CYPHER)
    raw_relationships = runtime.graph_store.execute_read(
        _FINGERPRINT_RELATIONSHIPS_CYPHER
    )
    graph_nodes = [
        {
            "label": str(row.get("label") or ""),
            "node_id": str(row.get("node_id") or ""),
            "properties": _json_safe(row.get("properties") or {}),
        }
        for row in raw_nodes
        if str((row.get("properties") or {}).get("scope_id") or "")
        == scope_id
    ]
    graph_relationships = [
        {
            "relationship_id": str(row.get("relationship_id") or ""),
            "relationship_type": str(row.get("relationship_type") or ""),
            "source_node_id": str(row.get("source_node_id") or ""),
            "target_node_id": str(row.get("target_node_id") or ""),
            "properties": _json_safe(row.get("properties") or {}),
        }
        for row in raw_relationships
        if str((row.get("properties") or {}).get("scope_id") or "")
        == scope_id
    ]
    if not graph_nodes:
        raise LiveRetrievalError(f"Neo4j has no nodes for scope {scope_id!r}")

    graph_runs = {
        str(item["properties"].get("ingestion_run_id") or "")
        for item in graph_nodes + graph_relationships
    }
    graph_run = _single_value(
        graph_runs,
        name="Neo4j ingestion_run_id",
    )
    if graph_run != qdrant_run:
        raise LiveRetrievalError(
            "Qdrant and Neo4j ingestion_run_id differ: "
            f"{qdrant_run!r} != {graph_run!r}"
        )

    source_identity = [
        {
            "node_id": item["node_id"],
            "om_id": item["properties"].get("om_id"),
            "source_updated_at": item["properties"].get(
                "source_updated_at"
            ),
            "service": item["properties"].get("service"),
            "database": item["properties"].get("database"),
            "schema": item["properties"].get("schema"),
        }
        for item in graph_nodes
        if item["label"] == "Table"
    ]
    source_identity.sort(key=lambda item: item["node_id"])
    graph_nodes.sort(key=lambda item: (item["label"], item["node_id"]))
    graph_relationships.sort(
        key=lambda item: (
            item["relationship_type"],
            item["relationship_id"],
        )
    )
    qdrant_records.sort(
        key=lambda item: (item["point_id"], item["node_id"])
    )
    return {
        "snapshot_schema_version": "1",
        "scope_id": scope_id,
        "source_snapshot_hash": canonical_hash(source_identity),
        "graph_snapshot_hash": canonical_hash(
            {
                "nodes": graph_nodes,
                "relationships": graph_relationships,
            }
        ),
        "qdrant_snapshot_hash": canonical_hash(qdrant_records),
        "ingestion_run_id": qdrant_run,
        "embedding_profile": embedding_profile,
        "card_schema_version": card_schema_version,
        "source_version": source_version,
        "collection": runtime.collection,
        "physical_collection": _physical_collection(runtime),
        "qdrant_point_count": len(qdrant_records),
        "graph_node_count": len(graph_nodes),
        "graph_relationship_count": len(graph_relationships),
    }


def _snapshot(
    runtime: LiveRetrievalRuntime,
    scope_id: str,
) -> dict[str, Any]:
    raw = (
        runtime.snapshot_provider(scope_id)
        if runtime.snapshot_provider is not None
        else _default_snapshot(runtime, scope_id)
    )
    snapshot = dict(_json_safe(raw))
    if snapshot.get("scope_id") != scope_id:
        raise LiveRetrievalError(
            "snapshot scope_id mismatch: "
            f"{snapshot.get('scope_id')!r} != {scope_id!r}"
        )
    for key in (
        "source_snapshot_hash",
        "graph_snapshot_hash",
        "qdrant_snapshot_hash",
        "ingestion_run_id",
        "embedding_profile",
        "card_schema_version",
        "source_version",
        "collection",
        "physical_collection",
    ):
        if not str(snapshot.get(key) or "").strip():
            raise LiveRetrievalError(f"snapshot is missing {key!r}")
    for key in (
        "qdrant_point_count",
        "graph_node_count",
        "graph_relationship_count",
    ):
        value = snapshot.get(key)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            raise LiveRetrievalError(
                f"snapshot has invalid {key!r}: {value!r}"
            )
    return snapshot


def _row_scope_values(row: Mapping[str, Any]) -> set[str]:
    return {
        str(value)
        for key, value in row.items()
        if key.endswith("_scope_id") and value is not None
    }


def _runtime_join_identity(join: Mapping[str, Any]) -> tuple[Any, ...]:
    left = (
        str(join.get("left_table_fqn") or ""),
        str(join.get("left_col") or ""),
    )
    right = (
        str(join.get("right_table_fqn") or ""),
        str(join.get("right_col") or ""),
    )
    endpoints = tuple(sorted((left, right)))
    return (
        endpoints,
        str(join.get("join_rule") or ""),
        str(join.get("semantic_key") or ""),
        bool(join.get("sql_join_eligible")),
        str(join.get("cardinality") or ""),
        str(join.get("confidence") or ""),
    )


def _graph_join_identity(row: Mapping[str, Any]) -> tuple[Any, ...]:
    source = (
        str(row.get("source_table_id") or ""),
        str(row.get("source_column_name") or ""),
    )
    target = (
        str(row.get("target_table_id") or ""),
        str(row.get("target_column_name") or ""),
    )
    endpoints = tuple(sorted((source, target)))
    return (
        endpoints,
        str(row.get("join_rule") or ""),
        str(row.get("semantic_key") or ""),
        bool(row.get("sql_join_eligible")),
        str(row.get("cardinality") or ""),
        str(row.get("confidence") or ""),
    )


def _introspect_graph(
    runtime: LiveRetrievalRuntime,
    *,
    documents: Sequence[Any],
    joins: Sequence[Mapping[str, Any]],
    scope_id: str,
) -> tuple[
    tuple[GraphNode, ...],
    tuple[GraphRelationship, ...],
    tuple[GraphPath, ...],
    tuple[str, ...],
]:
    table_ids = _ordered_document_ids(documents)
    if not table_ids:
        return (), (), (), ()

    has_column_rows = runtime.graph_store.execute_read(
        _HAS_COLUMN_CYPHER,
        {"tids": table_ids},
    )
    semantic_rows = runtime.graph_store.execute_read(
        _SEMANTIC_JOIN_CYPHER,
        {"tids": table_ids},
    )
    for row in [*has_column_rows, *semantic_rows]:
        scopes = _row_scope_values(row)
        if scopes != {scope_id}:
            raise LiveRetrievalError(
                f"graph row scope mismatch: expected {scope_id!r}, "
                f"found {sorted(scopes)!r}"
            )

    expected_columns = {
        _node_id(document): [
            str(column.get("name") or "")
            for column in (
                (getattr(document, "meta", None) or {}).get("columns", [])
            )
            if isinstance(column, Mapping) and column.get("name")
        ]
        for document in documents
    }
    actual_columns: dict[str, list[str]] = {table_id: [] for table_id in table_ids}
    for row in has_column_rows:
        actual_columns.setdefault(str(row["table_id"]), []).append(
            str(row.get("column_name") or "")
        )
    if expected_columns != actual_columns:
        raise LiveRetrievalError(
            "Neo4j HAS_COLUMN rows differ from the columns serialized in "
            f"context: expected={expected_columns!r}, actual={actual_columns!r}"
        )

    runtime_join_counts = Counter(
        _runtime_join_identity(join) for join in joins
    )
    graph_join_counts = Counter(
        _graph_join_identity(row) for row in semantic_rows
    )
    if runtime_join_counts != graph_join_counts:
        raise LiveRetrievalError(
            "Neo4j SEMANTIC_JOIN rows differ from runtime joins: "
            f"runtime={runtime_join_counts!r}, graph={graph_join_counts!r}"
        )

    nodes: list[GraphNode] = [
        GraphNode(id=table_id, type=NodeType.TABLE)
        for table_id in table_ids
    ]
    nodes.extend(
        GraphNode(
            id=str(row["column_id"]),
            type=NodeType.COLUMN,
        )
        for row in has_column_rows
    )

    relationships: list[GraphRelationship] = [
        GraphRelationship(
            id=str(row["relationship_id"]),
            type=RelationshipType.HAS_COLUMN,
            source=str(row["table_id"]),
            target=str(row["column_id"]),
            direction=RelationshipDirection.FORWARD,
            sql_join_eligible=bool(row.get("sql_join_eligible")),
        )
        for row in has_column_rows
    ]
    semantic_relationships = [
        GraphRelationship(
            id=str(row["relationship_id"]),
            type=RelationshipType.SEMANTIC_JOIN,
            source=str(row["source_node_id"]),
            target=str(row["target_node_id"]),
            direction=RelationshipDirection.FORWARD,
            sql_join_eligible=bool(row.get("sql_join_eligible")),
        )
        for row in semantic_rows
    ]
    relationships.extend(semantic_relationships)
    paths = tuple(
        GraphPath(
            id=f"semantic-join:{relationship.id}",
            relationship_ids=(relationship.id,),
            join_path=relationship.sql_join_eligible,
        )
        for relationship in semantic_relationships
    )
    authorized = tuple(
        relationship.id
        for relationship in semantic_relationships
        if relationship.sql_join_eligible
    )
    return tuple(nodes), tuple(relationships), paths, authorized


def _context_observation(
    documents: Sequence[Any],
    joins: Sequence[Mapping[str, Any]],
    final_nodes: Sequence[GraphNode],
    *,
    query: str,
    prompt_renderer: PromptRenderer | None,
) -> dict[str, Any]:
    contents = [str(getattr(document, "content", "") or "") for document in documents]
    if prompt_renderer is None:
        raise LiveRetrievalError(
            "runtime is missing the production prompt renderer"
        )
    rendered_messages = {
        str(name): str(content)
        for name, content in prompt_renderer(
            documents,
            joins,
            query,
        ).items()
    }
    if not rendered_messages:
        raise LiveRetrievalError("production prompt renderer returned no messages")
    table_count = sum(node.type is NodeType.TABLE for node in final_nodes)
    column_count = sum(node.type is NodeType.COLUMN for node in final_nodes)
    schema_context_chars = sum(len(content) for content in contents)
    rendered_prompt_chars = sum(
        len(content) for content in rendered_messages.values()
    )
    rendered_prompt_utf8_bytes = sum(
        len(content.encode("utf-8"))
        for content in rendered_messages.values()
    )
    system_prompt_chars = sum(
        len(content)
        for name, content in rendered_messages.items()
        if name.endswith(":system")
    )
    user_prompt_chars = sum(
        len(content)
        for name, content in rendered_messages.items()
        if name.endswith(":user")
    )
    return {
        "document_count": len(documents),
        "join_count": len(joins),
        "table_node_count": table_count,
        "column_node_count": column_count,
        "serialized_node_count": len(final_nodes),
        "schema_context_chars": schema_context_chars,
        "document_content_chars": schema_context_chars,
        "document_content_utf8_bytes": sum(
            len(content.encode("utf-8")) for content in contents
        ),
        "rendered_system_prompt_chars": system_prompt_chars,
        "rendered_user_prompt_chars": user_prompt_chars,
        "rendered_llm_prompt_chars": rendered_prompt_chars,
        "rendered_llm_prompt_utf8_bytes": rendered_prompt_utf8_bytes,
        "prompt_overhead_chars": (
            rendered_prompt_chars - schema_context_chars
        ),
        "context_hash": canonical_hash(rendered_messages),
    }


def _error_trace(
    *,
    case_id: str,
    scope_id: str,
    fingerprint: str,
    error: Exception,
    stage: str,
    collector_started_at: float,
    timings_ms: Mapping[str, float],
) -> RetrievalTrace:
    timings = dict(timings_ms)
    timings.setdefault("total", _elapsed_ms(collector_started_at))
    timings["collector_total"] = _elapsed_ms(collector_started_at)
    return RetrievalTrace(
        case_id=case_id,
        scope_id=scope_id,
        seeds=(),
        final_nodes=(),
        relationships=(),
        paths=(),
        timings_ms=timings,
        fingerprint=fingerprint,
        authorized_join_relationship_ids=(),
        selected_seed_node_ids=(),
        serialized_context_node_ids=(),
        trace_schema_version=TRACE_SCHEMA_VERSION,
        error=f"{type(error).__name__}: {error}",
        metadata={"failed_stage": stage},
    )


def _collect_trace(
    runtime: LiveRetrievalRuntime,
    *,
    case_id: str,
    question: str,
    scope_id: str,
    fingerprint: str,
    run_id: str,
    retrieval_channel: str,
) -> RetrievalTrace:
    collector_started_at = perf_counter()
    production_started_at = collector_started_at
    timings: dict[str, float] = {}
    stage = "dense_embedding"
    try:
        dense_result, timings["dense_embedding"] = _timed(
            lambda: runtime.dense_embedder.run(text=question)
        )
        dense_embedding = dense_result["embedding"]

        stage = "sparse_embedding"
        sparse_result, timings["sparse_embedding"] = _timed(
            lambda: runtime.sparse_embedder.run(text=question)
        )
        sparse_embedding = sparse_result["sparse_embedding"]

        selected_timing_name = f"{retrieval_channel}_selected_top5"
        stage = selected_timing_name
        selected_result, timings[selected_timing_name] = _timed(
            lambda: _run_retriever(
                runtime,
                channel=retrieval_channel,
                dense_embedding=dense_embedding,
                sparse_embedding=sparse_embedding,
                top_k=SELECTED_TOP_K,
            )
        )
        selected_documents = list(selected_result.get("documents") or [])

        stage = "selected_document_validation"
        _validate_retrieved_documents(
            selected_documents,
            source=f"{retrieval_channel} selected retrieval",
            scope_id=scope_id,
        )

        stage = "graph_expansion"
        subgraph_result, timings["graph_expansion"] = _timed(
            lambda: runtime.subgraph_retriever.run(
                documents=selected_documents
            )
        )
        context_documents = list(subgraph_result.get("documents") or [])
        runtime_joins = list(subgraph_result.get("joins") or [])
        timings["total"] = _elapsed_ms(production_started_at)

        stage = "hybrid_diagnostic_top10"
        hybrid_diagnostic, timings["hybrid_diagnostic_top10"] = _timed(
            lambda: runtime.hybrid_retriever.run(
                query_embedding=dense_embedding,
                query_sparse_embedding=sparse_embedding,
                top_k=DIAGNOSTIC_TOP_K,
            )
        )
        hybrid_documents = list(
            hybrid_diagnostic.get("documents") or []
        )

        stage = "dense_diagnostic_top10"
        dense_diagnostic, timings["dense_diagnostic_top10"] = _timed(
            lambda: runtime.dense_retriever.run(
                query_embedding=dense_embedding,
                top_k=DIAGNOSTIC_TOP_K,
            )
        )
        dense_documents = list(dense_diagnostic.get("documents") or [])

        stage = "sparse_diagnostic_top10"
        sparse_diagnostic, timings["sparse_diagnostic_top10"] = _timed(
            lambda: runtime.sparse_retriever.run(
                query_sparse_embedding=sparse_embedding,
                top_k=DIAGNOSTIC_TOP_K,
            )
        )
        sparse_documents = list(sparse_diagnostic.get("documents") or [])

        stage = "diagnostic_document_validation"
        _validate_retrieved_documents(
            hybrid_documents,
            source="hybrid diagnostic retrieval",
            scope_id=scope_id,
        )
        _validate_retrieved_documents(
            dense_documents,
            source="dense diagnostic retrieval",
            scope_id=scope_id,
        )
        _validate_retrieved_documents(
            sparse_documents,
            source="sparse diagnostic retrieval",
            scope_id=scope_id,
        )

        diagnostic_documents = {
            "hybrid": hybrid_documents,
            "dense": dense_documents,
            "sparse": sparse_documents,
        }[retrieval_channel]

        stage = "ranking_consistency"
        selected_unique_documents = _ordered_unique_documents(
            selected_documents
        )
        diagnostic_unique_documents = _ordered_unique_documents(
            diagnostic_documents
        )
        selected_seed_ids = _ordered_document_ids(
            selected_unique_documents
        )
        diagnostic_ids = _ordered_document_ids(diagnostic_documents)
        diagnostic_prefix = diagnostic_ids[: len(selected_seed_ids)]
        selected_matches_diagnostic_prefix = (
            selected_seed_ids == diagnostic_prefix
        )
        ranked_documents = diagnostic_unique_documents[:DIAGNOSTIC_TOP_K]
        ranked_ids = set(_ordered_document_ids(ranked_documents))
        selected_missing_from_ranking = set(selected_seed_ids) - ranked_ids
        if selected_missing_from_ranking:
            raise LiveRetrievalError(
                "selected production seeds are absent from the diagnostic "
                "top-10 ranking: "
                + ", ".join(sorted(selected_missing_from_ranking))
            )
        seed_scope_values = [
            _document_scope(document)
            for document in [
                *selected_unique_documents,
                *diagnostic_unique_documents,
            ]
        ]
        seed_scopes = set(seed_scope_values)
        if not seed_scope_values or "" in seed_scopes or seed_scopes != {scope_id}:
            raise LiveRetrievalError(
                f"Qdrant seed scope mismatch: expected {scope_id!r}, "
                f"found {sorted(seed_scopes)!r}"
            )

        dense_ranks = _rank_lookup(dense_documents)
        sparse_ranks = _rank_lookup(sparse_documents)
        seeds = tuple(
            RankedSeed(
                node_id=_node_id(document),
                rank=rank,
                score=_score(document),
                card_type=_card_type(document),
                dense_rank=dense_ranks.get(_node_id(document)),
                sparse_rank=sparse_ranks.get(_node_id(document)),
            )
            for rank, document in enumerate(
                ranked_documents,
                start=1,
            )
        )

        stage = "graph_introspection"
        graph_result, timings["graph_introspection"] = _timed(
            lambda: _introspect_graph(
                runtime,
                documents=context_documents,
                joins=runtime_joins,
                scope_id=scope_id,
            )
        )
        final_nodes, relationships, paths, authorized = graph_result

        stage = "context_observation"
        context, timings["context_observation"] = _timed(
            lambda: _context_observation(
                context_documents,
                runtime_joins,
                final_nodes,
                query=question,
                prompt_renderer=runtime.prompt_renderer,
            )
        )
        timings["collector_total"] = _elapsed_ms(collector_started_at)
        return RetrievalTrace(
            case_id=case_id,
            scope_id=scope_id,
            seeds=seeds,
            final_nodes=final_nodes,
            relationships=relationships,
            paths=paths,
            timings_ms=timings,
            fingerprint=fingerprint,
            authorized_join_relationship_ids=authorized,
            selected_seed_node_ids=tuple(selected_seed_ids),
            serialized_context_node_ids=tuple(
                node.id for node in final_nodes
            ),
            trace_schema_version=TRACE_SCHEMA_VERSION,
            metadata={
                "run_id": run_id,
                "retrieval_channel": retrieval_channel,
                "score_kind": {
                    "hybrid": "qdrant_rrf",
                    "dense": "qdrant_dense_similarity",
                    "sparse": "qdrant_sparse_score",
                }[retrieval_channel],
                "selected_ranking": {
                    "source": f"{retrieval_channel}_top5_call",
                    "top_k": SELECTED_TOP_K,
                    "node_ids": selected_seed_ids,
                    "diagnostic_prefix_node_ids": diagnostic_prefix,
                    "matches_diagnostic_prefix": (
                        selected_matches_diagnostic_prefix
                    ),
                },
                "ranked_seeds": {
                    "composition": "diagnostic_top10_call",
                    "source": f"{retrieval_channel}_top10_call",
                    "node_ids": [
                        _node_id(document)
                        for document in ranked_documents
                    ],
                },
                "signal_diagnostics": {
                    "warning": (
                        "Dense and sparse ranks come from lateral diagnostic "
                        "queries and were not used to reconstruct the RRF score."
                    ),
                    "hybrid": {
                        "source": "separate_diagnostic_call",
                        "top_k": DIAGNOSTIC_TOP_K,
                        "results": _diagnostic_results(
                            hybrid_documents
                        ),
                    },
                    "dense": {
                        "source": "lateral_diagnostic_query",
                        "top_k": DIAGNOSTIC_TOP_K,
                        "results": _diagnostic_results(dense_documents),
                    },
                    "sparse": {
                        "source": "lateral_diagnostic_query",
                        "top_k": DIAGNOSTIC_TOP_K,
                        "results": _diagnostic_results(sparse_documents),
                    },
                },
                "expansion": {
                    "selected_top_k": SELECTED_TOP_K,
                    "max_seeds": getattr(
                        runtime.subgraph_retriever,
                        "max_seeds",
                        None,
                    ),
                    "hops": 1,
                    "relationship_types": [
                        "HAS_COLUMN",
                        "SEMANTIC_JOIN",
                    ],
                    "pruning": "none",
                    "pruned_items": [],
                },
                "context": context,
            },
        )
    except Exception as error:
        return _error_trace(
            case_id=case_id,
            scope_id=scope_id,
            fingerprint=fingerprint,
            error=error,
            stage=stage,
            collector_started_at=collector_started_at,
            timings_ms=timings,
        )


def collect_retrieval_run(
    dataset: RetrievalDataset,
    *,
    runtime: LiveRetrievalRuntime | None = None,
    collection: str | None = None,
    run_id: str | None = None,
    created_at: str | None = None,
    code_revision: str = "unknown",
    worktree_state: str = "unknown",
    environment_class: str = "docker-compose-local",
    dependency_versions: Mapping[str, str] | None = None,
    retrieval_channel: str = "hybrid",
) -> RetrievalRun:
    """Collect and validate one real pre-LLM trace for every dataset case.

    The selected channel is called with ``top_k=5`` for the documents actually
    sent to graph expansion. Separate hybrid/dense/sparse top-10 calls are
    recorded so each channel can be audited without presenting lateral scores
    as part of another channel's ranking.
    """

    if worktree_state not in {"clean", "dirty", "unknown"}:
        raise ValueError("worktree_state must be clean, dirty, or unknown")
    if retrieval_channel not in RETRIEVAL_CHANNELS:
        raise ValueError(
            f"retrieval_channel must be one of {RETRIEVAL_CHANNELS!r}"
        )
    scope_id = str(dataset.metadata.get("scope_id") or "").strip()
    if not scope_id:
        raise LiveRetrievalError("dataset.metadata.scope_id is required")

    live_runtime = runtime or build_default_runtime(collection)
    snapshot = _snapshot(live_runtime, scope_id)
    resolved_created_at = created_at or _utc_now()
    resolved_run_id = run_id or _generated_run_id(resolved_created_at)
    versions = dict(dependency_versions or _dependency_versions())
    retrieval_configuration = {
        **_json_safe(live_runtime.configuration),
        "selected_top_k": SELECTED_TOP_K,
        "diagnostic_top_k": DIAGNOSTIC_TOP_K,
        "hybrid_fusion": "qdrant_rrf",
        "retrieval_channel": retrieval_channel,
        "dense_sparse_ranks": "lateral_diagnostic_queries",
        "graph_hops": 1,
        "graph_relationship_types": ["HAS_COLUMN", "SEMANTIC_JOIN"],
        "pruning": "none",
        "collection": live_runtime.collection,
    }
    fingerprint_payload = {
        "schema_version": FINGERPRINT_SCHEMA_VERSION,
        "dataset": {
            "name": dataset.name,
            "version": dataset.version,
            "status": dataset.status.value,
            "hash": dataset.canonical_hash,
        },
        "trace_schema_version": TRACE_SCHEMA_VERSION,
        "code": {
            "revision": code_revision,
            "worktree_state": worktree_state,
        },
        "environment_class": environment_class,
        "dependencies": dict(sorted(versions.items())),
        "retrieval": retrieval_configuration,
        "snapshot": snapshot,
    }
    fingerprint = canonical_hash(fingerprint_payload)

    traces = tuple(
        _collect_trace(
            live_runtime,
            case_id=case.id,
            question=case.question,
            scope_id=scope_id,
            fingerprint=fingerprint,
            run_id=resolved_run_id,
            retrieval_channel=retrieval_channel,
        )
        for case in dataset.cases
    )
    run = RetrievalRun(
        run_id=resolved_run_id,
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        dataset_hash=dataset.canonical_hash,
        fingerprint=fingerprint,
        traces=traces,
        created_at=resolved_created_at,
        metadata={
            "scope_id": scope_id,
            "source_snapshot_hash": snapshot["source_snapshot_hash"],
            "graph_snapshot_hash": snapshot["graph_snapshot_hash"],
            "qdrant_snapshot_hash": snapshot["qdrant_snapshot_hash"],
            "ingestion_run_id": snapshot["ingestion_run_id"],
            "environment_class": environment_class,
            "retrieval_channel": retrieval_channel,
            "retrieval_configuration": retrieval_configuration,
            "fingerprint_payload": fingerprint_payload,
        },
    )
    # Validate the exact serialized artifact, not only the dataclass instances.
    return RetrievalRun.from_dict(run.to_dict())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval.retrieval.live",
        description="Collect a read-only, pre-LLM RetrievalRun",
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--collection")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--channel",
        choices=RETRIEVAL_CHANNELS,
        default="hybrid",
        help="ranking used for top-5 selection and graph expansion",
    )
    parser.add_argument(
        "--code-revision",
        default=os.environ.get("RETRIEVAL_CODE_REVISION", "unknown"),
    )
    parser.add_argument(
        "--worktree-state",
        choices=("clean", "dirty", "unknown"),
        default=os.environ.get("RETRIEVAL_WORKTREE_STATE", "unknown"),
    )
    parser.add_argument(
        "--environment-class",
        default=os.environ.get(
            "RETRIEVAL_ENVIRONMENT_CLASS",
            "docker-compose-local",
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    dataset = RetrievalDataset.from_json_file(args.dataset)
    run = collect_retrieval_run(
        dataset,
        collection=args.collection,
        run_id=args.run_id,
        code_revision=args.code_revision,
        worktree_state=args.worktree_state,
        environment_class=args.environment_class,
        retrieval_channel=args.channel,
    )
    rendered = json.dumps(
        run.to_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "run_id": run.run_id,
                "fingerprint": run.fingerprint,
                "trace_count": len(run.traces),
                "error_count": sum(
                    trace.error is not None for trace in run.traces
                ),
                "output": str(destination),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

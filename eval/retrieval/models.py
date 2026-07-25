"""Versioned contracts used by the retrieval evaluation runner.

The module intentionally depends only on the Python standard library.  These
contracts are strict at the boundary so malformed gold data or traces fail
before they can produce deceptively plausible metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


class ContractError(ValueError):
    """Raised when a dataset, trace, report, or policy violates its contract."""


class DatasetStatus(str, Enum):
    DRAFT = "draft"
    ACCEPTED = "accepted"


class NodeType(str, Enum):
    TABLE = "Table"
    COLUMN = "Column"


class RelationshipType(str, Enum):
    HAS_COLUMN = "HAS_COLUMN"
    FOREIGN_KEY = "FOREIGN_KEY"
    SEMANTIC_JOIN = "SEMANTIC_JOIN"


class RelationshipDirection(str, Enum):
    FORWARD = "forward"
    REVERSE = "reverse"
    UNDIRECTED = "undirected"


class MetricDirection(str, Enum):
    HIGHER = "higher"
    LOWER = "lower"


def _fail(path: str, message: str) -> ContractError:
    return ContractError(f"{path}: {message}")


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(path, "expected an object")
    return value


def _sequence(value: Any, path: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(path, "expected an array")
    return value


def _string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise _fail(path, "expected a string")
    result = value.strip()
    if not allow_empty and not result:
        raise _fail(path, "must not be empty")
    return result


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(path, "expected a boolean")
    return value


def _number(
    value: Any,
    path: str,
    *,
    minimum: float | None = None,
    strictly_positive: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(path, "expected a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise _fail(path, "expected a finite number")
    if minimum is not None and result < minimum:
        raise _fail(path, f"must be >= {minimum}")
    if strictly_positive and result <= 0:
        raise _fail(path, "must be > 0")
    return result


def _integer(value: Any, path: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(path, "expected an integer")
    if minimum is not None and value < minimum:
        raise _fail(path, f"must be >= {minimum}")
    return value


def _optional_number(value: Any, path: str) -> float | None:
    return None if value is None else _number(value, path)


def _enum(enum_type: type[Enum], value: Any, path: str) -> Any:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        choices = ", ".join(str(item.value) for item in enum_type)
        raise _fail(path, f"expected one of: {choices}") from None


def _unique(items: Sequence[str], path: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        if item in seen:
            duplicates.add(item)
        seen.add(item)
    if duplicates:
        raise _fail(path, f"duplicate values: {', '.join(sorted(duplicates))}")


def _string_tuple(value: Any, path: str) -> tuple[str, ...]:
    result = tuple(
        _string(item, f"{path}[{index}]")
        for index, item in enumerate(_sequence(value, path))
    )
    _unique(result, path)
    return result


def _normalise_direction(value: Any, path: str) -> RelationshipDirection:
    if isinstance(value, RelationshipDirection):
        return value
    raw = _string(value, path).lower().replace("-", "_")
    aliases = {
        "forward": RelationshipDirection.FORWARD,
        "outgoing": RelationshipDirection.FORWARD,
        "source_to_target": RelationshipDirection.FORWARD,
        "reverse": RelationshipDirection.REVERSE,
        "incoming": RelationshipDirection.REVERSE,
        "target_to_source": RelationshipDirection.REVERSE,
        "undirected": RelationshipDirection.UNDIRECTED,
        "both": RelationshipDirection.UNDIRECTED,
        "bidirectional": RelationshipDirection.UNDIRECTED,
    }
    if raw not in aliases:
        raise _fail(
            path,
            "expected forward/outgoing, reverse/incoming, or undirected",
        )
    return aliases[raw]


def _normalise_node_type(value: Any, path: str) -> NodeType:
    if isinstance(value, NodeType):
        return value
    raw = _string(value, path).lower()
    aliases = {
        "table": NodeType.TABLE,
        "column": NodeType.COLUMN,
    }
    if raw not in aliases:
        raise _fail(path, "expected Table/table or Column/column")
    return aliases[raw]


def canonical_json(value: Any) -> str:
    """Return the canonical UTF-8 JSON representation used for hashing."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_json(path: str | Path) -> Any:
    source = Path(path)
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContractError(f"{source}: cannot read file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(
            f"{source}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}"
        ) from exc


@dataclass(frozen=True)
class GraphNode:
    id: str
    type: NodeType | None = None
    weight: float = 1.0
    required: bool = True

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        path: str = "node",
        gold: bool = False,
    ) -> "GraphNode":
        if isinstance(value, str):
            if gold:
                raise _fail(path, "gold nodes must include id and type")
            return cls(id=_string(value, path))
        data = _mapping(value, path)
        node_id = _string(data.get("id"), f"{path}.id")
        raw_type = data.get("type")
        node_type = (
            None
            if raw_type is None
            else _normalise_node_type(raw_type, f"{path}.type")
        )
        if gold and node_type is None:
            raise _fail(f"{path}.type", "is required for a gold node")
        default_weight = 3.0 if node_type is NodeType.TABLE else 2.0
        weight = _number(
            data.get("weight", default_weight if gold else 1.0),
            f"{path}.weight",
            strictly_positive=True,
        )
        required = _boolean(data.get("required", True), f"{path}.required")
        return cls(node_id, node_type, weight, required)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "weight": self.weight,
            "required": self.required,
        }
        if self.type is not None:
            result["type"] = self.type.value
        return result


@dataclass(frozen=True)
class GraphRelationship:
    id: str
    type: RelationshipType
    source: str
    target: str
    direction: RelationshipDirection = RelationshipDirection.FORWARD
    sql_join_eligible: bool = False
    weight: float = 1.0
    required: bool = True

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        path: str = "relationship",
        gold: bool = False,
    ) -> "GraphRelationship":
        data = _mapping(value, path)
        rel_type = _enum(
            RelationshipType,
            data.get("type"),
            f"{path}.type",
        )
        eligible = _boolean(
            data.get("sql_join_eligible", False),
            f"{path}.sql_join_eligible",
        )
        default_weight = (
            3.0
            if eligible
            and rel_type
            in (RelationshipType.FOREIGN_KEY, RelationshipType.SEMANTIC_JOIN)
            else 1.0
        )
        return cls(
            id=_string(data.get("id"), f"{path}.id"),
            type=rel_type,
            source=_string(data.get("source"), f"{path}.source"),
            target=_string(data.get("target"), f"{path}.target"),
            direction=_normalise_direction(
                data.get("direction", "forward"),
                f"{path}.direction",
            ),
            sql_join_eligible=eligible,
            weight=_number(
                data.get("weight", default_weight if gold else 1.0),
                f"{path}.weight",
                strictly_positive=True,
            ),
            required=_boolean(
                data.get("required", True),
                f"{path}.required",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "source": self.source,
            "target": self.target,
            "direction": self.direction.value,
            "sql_join_eligible": self.sql_join_eligible,
            "weight": self.weight,
            "required": self.required,
        }


@dataclass(frozen=True)
class GraphPath:
    relationship_ids: tuple[str, ...]
    id: str = ""
    join_path: bool | None = None
    weight: float = 2.0
    required: bool = True

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        path: str = "path",
        gold: bool = False,
    ) -> "GraphPath":
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            data: Mapping[str, Any] = {"relationship_ids": value}
        else:
            data = _mapping(value, path)
        relationship_ids = _string_tuple(
            data.get("relationship_ids"),
            f"{path}.relationship_ids",
        )
        if not relationship_ids:
            raise _fail(f"{path}.relationship_ids", "must not be empty")
        join_path_raw = data.get("join_path")
        join_path = (
            None
            if join_path_raw is None
            else _boolean(join_path_raw, f"{path}.join_path")
        )
        default_weight = 3.0 if join_path else 2.0
        path_id = data.get("id", "")
        return cls(
            relationship_ids=relationship_ids,
            id=_string(path_id, f"{path}.id", allow_empty=True),
            join_path=join_path,
            weight=_number(
                data.get("weight", default_weight if gold else 1.0),
                f"{path}.weight",
                strictly_positive=True,
            ),
            required=_boolean(
                data.get("required", True),
                f"{path}.required",
            ),
        )

    @property
    def stable_id(self) -> str:
        return self.id or "->".join(self.relationship_ids)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "relationship_ids": list(self.relationship_ids),
            "weight": self.weight,
            "required": self.required,
        }
        if self.join_path is not None:
            result["join_path"] = self.join_path
        return result


@dataclass(frozen=True)
class RankedSeed:
    node_id: str
    rank: int
    score: float | None = None
    card_type: NodeType | None = None
    dense_rank: int | None = None
    sparse_rank: int | None = None

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        path: str = "seed",
        default_rank: int | None = None,
    ) -> "RankedSeed":
        if isinstance(value, str):
            raise _fail(
                path,
                "expected an object with node_id, rank, and card_type",
            )
        data = _mapping(value, path)
        rank_value = data.get("rank", default_rank)
        if rank_value is None:
            raise _fail(f"{path}.rank", "is required")
        card_type_raw = data.get("card_type", data.get("node_type"))
        if card_type_raw is None:
            raise _fail(f"{path}.card_type", "is required")
        dense_rank_raw = data.get("dense_rank")
        sparse_rank_raw = data.get("sparse_rank")
        return cls(
            node_id=_string(
                data.get("node_id", data.get("id")),
                f"{path}.node_id",
            ),
            rank=_integer(rank_value, f"{path}.rank", minimum=1),
            score=_optional_number(data.get("score"), f"{path}.score"),
            card_type=_normalise_node_type(
                card_type_raw,
                f"{path}.card_type",
            ),
            dense_rank=(
                None
                if dense_rank_raw is None
                else _integer(
                    dense_rank_raw,
                    f"{path}.dense_rank",
                    minimum=1,
                )
            ),
            sparse_rank=(
                None
                if sparse_rank_raw is None
                else _integer(
                    sparse_rank_raw,
                    f"{path}.sparse_rank",
                    minimum=1,
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"node_id": self.node_id, "rank": self.rank}
        if self.score is not None:
            result["score"] = self.score
        if self.card_type is not None:
            result["card_type"] = self.card_type.value
        if self.dense_rank is not None:
            result["dense_rank"] = self.dense_rank
        if self.sparse_rank is not None:
            result["sparse_rank"] = self.sparse_rank
        return result


@dataclass(frozen=True)
class GoldSubgraph:
    id: str
    nodes: tuple[GraphNode, ...]
    relationships: tuple[GraphRelationship, ...]
    paths: tuple[GraphPath, ...] = ()

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        path: str = "gold_subgraph",
        default_id: str = "gold",
    ) -> "GoldSubgraph":
        data = _mapping(value, path)
        nodes = tuple(
            GraphNode.from_dict(item, path=f"{path}.nodes[{index}]", gold=True)
            for index, item in enumerate(
                _sequence(data.get("nodes"), f"{path}.nodes")
            )
        )
        relationships = tuple(
            GraphRelationship.from_dict(
                item,
                path=f"{path}.relationships[{index}]",
                gold=True,
            )
            for index, item in enumerate(
                _sequence(
                    data.get("relationships", []),
                    f"{path}.relationships",
                )
            )
        )
        paths = tuple(
            GraphPath.from_dict(
                item,
                path=f"{path}.paths[{index}]",
                gold=True,
            )
            for index, item in enumerate(
                _sequence(data.get("paths", []), f"{path}.paths")
            )
        )
        if not nodes:
            raise _fail(f"{path}.nodes", "must contain at least one node")
        _unique([node.id for node in nodes], f"{path}.nodes")
        _unique(
            [relationship.id for relationship in relationships],
            f"{path}.relationships",
        )
        _unique([item.stable_id for item in paths], f"{path}.paths")
        node_ids = {node.id for node in nodes}
        for relationship in relationships:
            missing = {
                relationship.source,
                relationship.target,
            } - node_ids
            if missing:
                raise _fail(
                    f"{path}.relationships[{relationship.id}]",
                    "endpoints absent from gold nodes: "
                    + ", ".join(sorted(missing)),
                )
        relationship_ids = {relationship.id for relationship in relationships}
        relationships_by_id = {
            relationship.id: relationship for relationship in relationships
        }
        for graph_path in paths:
            missing = set(graph_path.relationship_ids) - relationship_ids
            if missing:
                raise _fail(
                    f"{path}.paths[{graph_path.stable_id}]",
                    "references unknown relationships: "
                    + ", ".join(sorted(missing)),
                )
            if graph_path.join_path:
                invalid_join_edges = [
                    relationship_id
                    for relationship_id in graph_path.relationship_ids
                    if (
                        relationships_by_id[relationship_id].type
                        not in (
                            RelationshipType.FOREIGN_KEY,
                            RelationshipType.SEMANTIC_JOIN,
                        )
                        or not relationships_by_id[
                            relationship_id
                        ].sql_join_eligible
                    )
                ]
                if invalid_join_edges:
                    raise _fail(
                        f"{path}.paths[{graph_path.stable_id}]",
                        "join path contains relationships that are not "
                        "SQL-eligible joins: "
                        + ", ".join(invalid_join_edges),
                    )
        return cls(
            id=_string(data.get("id", default_id), f"{path}.id"),
            nodes=nodes,
            relationships=relationships,
            paths=paths,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nodes": [node.to_dict() for node in self.nodes],
            "relationships": [
                relationship.to_dict() for relationship in self.relationships
            ],
            "paths": [path.to_dict() for path in self.paths],
        }


def _parse_seed_relevance(value: Any, path: str) -> dict[str, float]:
    if isinstance(value, Mapping):
        result = {
            _string(node_id, f"{path}.<key>"): _number(
                relevance,
                f"{path}.{node_id}",
                minimum=0,
            )
            for node_id, relevance in value.items()
        }
    else:
        result = {}
        for index, raw_item in enumerate(_sequence(value, path)):
            item_path = f"{path}[{index}]"
            item = _mapping(raw_item, item_path)
            node_id = _string(
                item.get("node_id", item.get("id")),
                f"{item_path}.node_id",
            )
            if node_id in result:
                raise _fail(path, f"duplicate node id: {node_id}")
            result[node_id] = _number(
                item.get("relevance"),
                f"{item_path}.relevance",
                minimum=0,
            )
    if not result or not any(relevance > 0 for relevance in result.values()):
        raise _fail(path, "must contain at least one positively relevant node")
    return result


@dataclass(frozen=True)
class RetrievalCase:
    id: str
    question: str
    seed_relevance: dict[str, float]
    gold_subgraphs: tuple[GoldSubgraph, ...]
    critical: bool = False
    slices: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        path: str = "case",
    ) -> "RetrievalCase":
        data = _mapping(value, path)
        raw_gold = data.get("gold_subgraphs", data.get("gold_alternatives"))
        gold_subgraphs = tuple(
            GoldSubgraph.from_dict(
                item,
                path=f"{path}.gold_subgraphs[{index}]",
                default_id=f"gold-{index + 1}",
            )
            for index, item in enumerate(
                _sequence(raw_gold, f"{path}.gold_subgraphs")
            )
        )
        if not gold_subgraphs:
            raise _fail(
                f"{path}.gold_subgraphs",
                "must contain at least one accepted alternative",
            )
        _unique(
            [subgraph.id for subgraph in gold_subgraphs],
            f"{path}.gold_subgraphs",
        )
        slices_raw = data.get("slices", data.get("tags", []))
        metadata_raw = data.get("metadata", {})
        metadata = dict(_mapping(metadata_raw, f"{path}.metadata"))
        seed_relevance = _parse_seed_relevance(
            data.get("seed_relevance"),
            f"{path}.seed_relevance",
        )
        known_node_ids = {
            node.id
            for subgraph in gold_subgraphs
            for node in subgraph.nodes
        }
        unknown_seed_ids = set(seed_relevance) - known_node_ids
        if unknown_seed_ids:
            raise _fail(
                f"{path}.seed_relevance",
                "references nodes absent from every gold alternative: "
                + ", ".join(sorted(unknown_seed_ids)),
            )
        return cls(
            id=_string(data.get("id"), f"{path}.id"),
            question=_string(data.get("question"), f"{path}.question"),
            seed_relevance=seed_relevance,
            gold_subgraphs=gold_subgraphs,
            critical=_boolean(data.get("critical", False), f"{path}.critical"),
            slices=_string_tuple(slices_raw, f"{path}.slices"),
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "critical": self.critical,
            "slices": list(self.slices),
            "seed_relevance": dict(sorted(self.seed_relevance.items())),
            "gold_subgraphs": [
                subgraph.to_dict() for subgraph in self.gold_subgraphs
            ],
            "metadata": copy.deepcopy(self.metadata),
        }


@dataclass(frozen=True)
class RetrievalDataset:
    name: str
    version: str
    status: DatasetStatus
    cases: tuple[RetrievalCase, ...]
    schema_version: str = "1"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        path: str = "dataset",
    ) -> "RetrievalDataset":
        data = _mapping(value, path)
        cases = tuple(
            RetrievalCase.from_dict(item, path=f"{path}.cases[{index}]")
            for index, item in enumerate(
                _sequence(data.get("cases"), f"{path}.cases")
            )
        )
        if not cases:
            raise _fail(f"{path}.cases", "must contain at least one case")
        _unique([case.id for case in cases], f"{path}.cases")
        return cls(
            name=_string(
                data.get("name", "retrieval"),
                f"{path}.name",
            ),
            version=_string(data.get("version"), f"{path}.version"),
            status=_enum(
                DatasetStatus,
                data.get("status"),
                f"{path}.status",
            ),
            cases=cases,
            schema_version=_string(
                data.get("schema_version", "1"),
                f"{path}.schema_version",
            ),
            metadata=dict(
                _mapping(data.get("metadata", {}), f"{path}.metadata")
            ),
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "RetrievalDataset":
        return cls.from_dict(load_json(path))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "version": self.version,
            "status": self.status.value,
            "cases": [case.to_dict() for case in self.cases],
            "metadata": copy.deepcopy(self.metadata),
        }

    @property
    def canonical_hash(self) -> str:
        return canonical_hash(self.to_dict())


@dataclass(frozen=True)
class RetrievalTrace:
    case_id: str
    scope_id: str
    seeds: tuple[RankedSeed, ...]
    final_nodes: tuple[GraphNode, ...]
    relationships: tuple[GraphRelationship, ...]
    paths: tuple[GraphPath, ...]
    timings_ms: dict[str, float]
    fingerprint: str
    authorized_join_relationship_ids: tuple[str, ...] = ()
    selected_seed_node_ids: tuple[str, ...] = ()
    serialized_context_node_ids: tuple[str, ...] = ()
    trace_schema_version: str = "1"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        path: str = "trace",
        inherited_fingerprint: str = "",
        inherited_scope_id: str = "",
    ) -> "RetrievalTrace":
        data = _mapping(value, path)
        seeds = tuple(
            RankedSeed.from_dict(
                item,
                path=f"{path}.seeds[{index}]",
                default_rank=index + 1,
            )
            for index, item in enumerate(
                _sequence(data.get("seeds", []), f"{path}.seeds")
            )
        )
        ranks = [seed.rank for seed in seeds]
        _unique([str(rank) for rank in ranks], f"{path}.seeds.rank")
        ordered_ranks = sorted(ranks)
        if ordered_ranks and ordered_ranks != list(
            range(1, len(ordered_ranks) + 1)
        ):
            raise _fail(
                f"{path}.seeds.rank",
                "ranks must be contiguous and start at 1",
            )
        node_ids = [seed.node_id for seed in seeds]
        _unique(node_ids, f"{path}.seeds.node_id")
        final_nodes = tuple(
            GraphNode.from_dict(
                item,
                path=f"{path}.final_nodes[{index}]",
                gold=True,
            )
            for index, item in enumerate(
                _sequence(
                    data.get("final_nodes", []),
                    f"{path}.final_nodes",
                )
            )
        )
        _unique([node.id for node in final_nodes], f"{path}.final_nodes")
        relationships = tuple(
            GraphRelationship.from_dict(
                item,
                path=f"{path}.relationships[{index}]",
            )
            for index, item in enumerate(
                _sequence(
                    data.get("relationships", []),
                    f"{path}.relationships",
                )
            )
        )
        _unique(
            [relationship.id for relationship in relationships],
            f"{path}.relationships",
        )
        paths = tuple(
            GraphPath.from_dict(item, path=f"{path}.paths[{index}]")
            for index, item in enumerate(
                _sequence(data.get("paths", []), f"{path}.paths")
            )
        )
        _unique([item.stable_id for item in paths], f"{path}.paths")
        final_node_ids = {node.id for node in final_nodes}
        for relationship in relationships:
            missing_endpoints = {
                relationship.source,
                relationship.target,
            } - final_node_ids
            if missing_endpoints:
                raise _fail(
                    f"{path}.relationships[{relationship.id}]",
                    "endpoints absent from final_nodes: "
                    + ", ".join(sorted(missing_endpoints)),
                )
        relationship_ids = {
            relationship.id for relationship in relationships
        }
        for graph_path in paths:
            missing_relationships = (
                set(graph_path.relationship_ids) - relationship_ids
            )
            if missing_relationships:
                raise _fail(
                    f"{path}.paths[{graph_path.stable_id}]",
                    "references relationships absent from the trace: "
                    + ", ".join(sorted(missing_relationships)),
                )
        authorized_join_relationship_ids = _string_tuple(
            data.get("authorized_join_relationship_ids", []),
            f"{path}.authorized_join_relationship_ids",
        )
        unknown_authorized = set(authorized_join_relationship_ids) - {
            relationship.id for relationship in relationships
        }
        if unknown_authorized:
            raise _fail(
                f"{path}.authorized_join_relationship_ids",
                "references relationships absent from the trace: "
                + ", ".join(sorted(unknown_authorized)),
            )
        selected_seed_node_ids = _string_tuple(
            data.get(
                "selected_seed_node_ids",
                [seed.node_id for seed in seeds],
            ),
            f"{path}.selected_seed_node_ids",
        )
        unknown_selected_seeds = set(selected_seed_node_ids) - {
            seed.node_id for seed in seeds
        }
        if unknown_selected_seeds:
            raise _fail(
                f"{path}.selected_seed_node_ids",
                "references nodes absent from seeds: "
                + ", ".join(sorted(unknown_selected_seeds)),
            )
        serialized_context_node_ids = _string_tuple(
            data.get(
                "serialized_context_node_ids",
                [node.id for node in final_nodes],
            ),
            f"{path}.serialized_context_node_ids",
        )
        unknown_context_nodes = set(serialized_context_node_ids) - {
            node.id for node in final_nodes
        }
        if unknown_context_nodes:
            raise _fail(
                f"{path}.serialized_context_node_ids",
                "references nodes absent from final_nodes: "
                + ", ".join(sorted(unknown_context_nodes)),
            )
        timings_raw = _mapping(
            data.get("timings_ms", data.get("timings", {})),
            f"{path}.timings_ms",
        )
        timings = {
            _string(name, f"{path}.timings_ms.<key>"): _number(
                duration,
                f"{path}.timings_ms.{name}",
                minimum=0,
            )
            for name, duration in timings_raw.items()
        }
        fingerprint = _string(
            data.get("fingerprint", inherited_fingerprint),
            f"{path}.fingerprint",
        )
        error_raw = data.get("error")
        error = (
            None
            if error_raw is None
            else _string(error_raw, f"{path}.error")
        )
        return cls(
            case_id=_string(data.get("case_id"), f"{path}.case_id"),
            scope_id=_string(
                data.get("scope_id", inherited_scope_id),
                f"{path}.scope_id",
            ),
            seeds=seeds,
            final_nodes=final_nodes,
            relationships=relationships,
            paths=paths,
            timings_ms=timings,
            fingerprint=fingerprint,
            authorized_join_relationship_ids=authorized_join_relationship_ids,
            selected_seed_node_ids=selected_seed_node_ids,
            serialized_context_node_ids=serialized_context_node_ids,
            trace_schema_version=_string(
                data.get("trace_schema_version", "1"),
                f"{path}.trace_schema_version",
            ),
            error=error,
            metadata=dict(
                _mapping(data.get("metadata", {}), f"{path}.metadata")
            ),
        )

    @property
    def total_latency_ms(self) -> float:
        if "total" in self.timings_ms:
            return self.timings_ms["total"]
        if "total_ms" in self.timings_ms:
            return self.timings_ms["total_ms"]
        return sum(self.timings_ms.values())

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "case_id": self.case_id,
            "scope_id": self.scope_id,
            "seeds": [seed.to_dict() for seed in self.seeds],
            "final_nodes": [node.to_dict() for node in self.final_nodes],
            "relationships": [
                relationship.to_dict() for relationship in self.relationships
            ],
            "paths": [path.to_dict() for path in self.paths],
            "timings_ms": dict(sorted(self.timings_ms.items())),
            "fingerprint": self.fingerprint,
            "trace_schema_version": self.trace_schema_version,
            "authorized_join_relationship_ids": list(
                self.authorized_join_relationship_ids
            ),
            "selected_seed_node_ids": list(self.selected_seed_node_ids),
            "serialized_context_node_ids": list(
                self.serialized_context_node_ids
            ),
            "metadata": copy.deepcopy(self.metadata),
        }
        if self.error is not None:
            result["error"] = self.error
        return result


@dataclass(frozen=True)
class RetrievalRun:
    run_id: str
    dataset_name: str
    dataset_version: str
    dataset_hash: str
    fingerprint: str
    traces: tuple[RetrievalTrace, ...]
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        path: str = "run",
    ) -> "RetrievalRun":
        data = _mapping(value, path)
        metadata = dict(
            _mapping(data.get("metadata", {}), f"{path}.metadata")
        )
        fingerprint = _string(
            data.get("fingerprint"),
            f"{path}.fingerprint",
        )
        traces = tuple(
            RetrievalTrace.from_dict(
                item,
                path=f"{path}.traces[{index}]",
                inherited_fingerprint=fingerprint,
                inherited_scope_id=_string(
                    metadata.get("scope_id"),
                    f"{path}.metadata.scope_id",
                ),
            )
            for index, item in enumerate(
                _sequence(data.get("traces"), f"{path}.traces")
            )
        )
        _unique([trace.case_id for trace in traces], f"{path}.traces.case_id")
        return cls(
            run_id=_string(data.get("run_id"), f"{path}.run_id"),
            dataset_name=_string(
                data.get("dataset_name"),
                f"{path}.dataset_name",
            ),
            dataset_version=_string(
                data.get("dataset_version"),
                f"{path}.dataset_version",
            ),
            dataset_hash=_string(
                data.get("dataset_hash"),
                f"{path}.dataset_hash",
            ),
            fingerprint=fingerprint,
            traces=traces,
            created_at=_string(
                data.get("created_at", ""),
                f"{path}.created_at",
                allow_empty=True,
            ),
            metadata=metadata,
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "RetrievalRun":
        return cls.from_dict(load_json(path))

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "dataset_name": self.dataset_name,
            "dataset_version": self.dataset_version,
            "dataset_hash": self.dataset_hash,
            "fingerprint": self.fingerprint,
            "created_at": self.created_at,
            "traces": [trace.to_dict() for trace in self.traces],
            "metadata": copy.deepcopy(self.metadata),
        }


@dataclass(frozen=True)
class InvariantResult:
    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class EvaluationReport:
    dataset_name: str
    dataset_version: str
    dataset_status: DatasetStatus
    dataset_hash: str
    run_id: str
    fingerprint: str
    summary: dict[str, Any]
    slices: dict[str, Any]
    cases: dict[str, Any]
    invariants: tuple[InvariantResult, ...]
    run_metadata: dict[str, Any] = field(default_factory=dict)
    errors: tuple[str, ...] = ()
    schema_version: str = "1"
    generated_at: str = ""

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        path: str = "report",
    ) -> "EvaluationReport":
        data = _mapping(value, path)
        invariants = tuple(
            InvariantResult(
                name=_string(item.get("name"), f"{item_path}.name"),
                passed=_boolean(
                    item.get("passed"),
                    f"{item_path}.passed",
                ),
                detail=_string(
                    item.get("detail", ""),
                    f"{item_path}.detail",
                    allow_empty=True,
                ),
            )
            for index, raw_item in enumerate(
                _sequence(
                    data.get("invariants", []),
                    f"{path}.invariants",
                )
            )
            for item_path, item in [
                (
                    f"{path}.invariants[{index}]",
                    _mapping(
                        raw_item,
                        f"{path}.invariants[{index}]",
                    ),
                )
            ]
        )
        errors = tuple(
            _string(item, f"{path}.errors[{index}]")
            for index, item in enumerate(
                _sequence(data.get("errors", []), f"{path}.errors")
            )
        )
        return cls(
            schema_version=_string(
                data.get("schema_version", "1"),
                f"{path}.schema_version",
            ),
            dataset_name=_string(
                data.get("dataset_name"),
                f"{path}.dataset_name",
            ),
            dataset_version=_string(
                data.get("dataset_version"),
                f"{path}.dataset_version",
            ),
            dataset_status=_enum(
                DatasetStatus,
                data.get("dataset_status"),
                f"{path}.dataset_status",
            ),
            dataset_hash=_string(
                data.get("dataset_hash"),
                f"{path}.dataset_hash",
            ),
            run_id=_string(data.get("run_id"), f"{path}.run_id"),
            fingerprint=_string(
                data.get("fingerprint"),
                f"{path}.fingerprint",
            ),
            generated_at=_string(
                data.get("generated_at", ""),
                f"{path}.generated_at",
                allow_empty=True,
            ),
            summary=dict(_mapping(data.get("summary"), f"{path}.summary")),
            slices=dict(
                _mapping(data.get("slices", {}), f"{path}.slices")
            ),
            cases=dict(_mapping(data.get("cases"), f"{path}.cases")),
            invariants=invariants,
            run_metadata=dict(
                _mapping(
                    data.get("run_metadata", {}),
                    f"{path}.run_metadata",
                )
            ),
            errors=errors,
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "EvaluationReport":
        return cls.from_dict(load_json(path))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "dataset_name": self.dataset_name,
            "dataset_version": self.dataset_version,
            "dataset_status": self.dataset_status.value,
            "dataset_hash": self.dataset_hash,
            "run_id": self.run_id,
            "fingerprint": self.fingerprint,
            "summary": copy.deepcopy(self.summary),
            "slices": copy.deepcopy(self.slices),
            "cases": copy.deepcopy(self.cases),
            "run_metadata": copy.deepcopy(self.run_metadata),
            "invariants": [
                invariant.to_dict() for invariant in self.invariants
            ],
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class MetricRule:
    metric: str
    direction: MetricDirection
    absolute_tolerance: float = 0.0
    relative_tolerance: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    slices: tuple[str, ...] = ()

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        path: str = "metric_rule",
    ) -> "MetricRule":
        data = _mapping(value, path)
        minimum = _optional_number(data.get("minimum"), f"{path}.minimum")
        maximum = _optional_number(data.get("maximum"), f"{path}.maximum")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise _fail(path, "minimum must be <= maximum")
        return cls(
            metric=_string(data.get("metric"), f"{path}.metric"),
            direction=_enum(
                MetricDirection,
                data.get("direction"),
                f"{path}.direction",
            ),
            absolute_tolerance=_number(
                data.get(
                    "absolute_tolerance",
                    data.get("tolerance_absolute", 0.0),
                ),
                f"{path}.absolute_tolerance",
                minimum=0,
            ),
            relative_tolerance=_number(
                data.get(
                    "relative_tolerance",
                    data.get("tolerance_relative", 0.0),
                ),
                f"{path}.relative_tolerance",
                minimum=0,
            ),
            minimum=minimum,
            maximum=maximum,
            slices=_string_tuple(
                data.get("slices", []),
                f"{path}.slices",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "metric": self.metric,
            "direction": self.direction.value,
            "absolute_tolerance": self.absolute_tolerance,
            "relative_tolerance": self.relative_tolerance,
        }
        if self.minimum is not None:
            result["minimum"] = self.minimum
        if self.maximum is not None:
            result["maximum"] = self.maximum
        if self.slices:
            result["slices"] = list(self.slices)
        return result


DEFAULT_CRITICAL_METRICS = (
    "structural_sufficiency",
    "weighted_dependency_recall",
    "node_recall",
    "relationship_recall",
    "path_recall",
    "join_path_recall",
)


@dataclass(frozen=True)
class GatePolicy:
    name: str
    rules: tuple[MetricRule, ...]
    require_accepted_dataset: bool = True
    reject_errors: bool = True
    reject_failed_invariants: bool = True
    reject_critical_regressions: bool = True
    critical_metrics: tuple[str, ...] = DEFAULT_CRITICAL_METRICS
    required_matching_run_metadata: tuple[str, ...] = ()
    schema_version: str = "1"

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        path: str = "policy",
    ) -> "GatePolicy":
        data = _mapping(value, path)
        raw_rules = data.get("rules", data.get("metrics", []))
        if isinstance(raw_rules, Mapping):
            expanded_rules: list[dict[str, Any]] = []
            for metric, raw_config in raw_rules.items():
                config = dict(
                    _mapping(raw_config, f"{path}.metrics.{metric}")
                )
                config["metric"] = metric
                expanded_rules.append(config)
            raw_rules = expanded_rules
        rules = tuple(
            MetricRule.from_dict(item, path=f"{path}.rules[{index}]")
            for index, item in enumerate(
                _sequence(raw_rules, f"{path}.rules")
            )
        )
        _unique([rule.metric for rule in rules], f"{path}.rules.metric")
        return cls(
            schema_version=_string(
                data.get("schema_version", "1"),
                f"{path}.schema_version",
            ),
            name=_string(data.get("name", "retrieval-gate"), f"{path}.name"),
            rules=rules,
            require_accepted_dataset=_boolean(
                data.get("require_accepted_dataset", True),
                f"{path}.require_accepted_dataset",
            ),
            reject_errors=_boolean(
                data.get("reject_errors", True),
                f"{path}.reject_errors",
            ),
            reject_failed_invariants=_boolean(
                data.get("reject_failed_invariants", True),
                f"{path}.reject_failed_invariants",
            ),
            reject_critical_regressions=_boolean(
                data.get("reject_critical_regressions", True),
                f"{path}.reject_critical_regressions",
            ),
            critical_metrics=_string_tuple(
                data.get("critical_metrics", DEFAULT_CRITICAL_METRICS),
                f"{path}.critical_metrics",
            ),
            required_matching_run_metadata=_string_tuple(
                data.get("required_matching_run_metadata", []),
                f"{path}.required_matching_run_metadata",
            ),
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "GatePolicy":
        return cls.from_dict(load_json(path))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "require_accepted_dataset": self.require_accepted_dataset,
            "reject_errors": self.reject_errors,
            "reject_failed_invariants": self.reject_failed_invariants,
            "reject_critical_regressions": self.reject_critical_regressions,
            "critical_metrics": list(self.critical_metrics),
            "required_matching_run_metadata": list(
                self.required_matching_run_metadata
            ),
            "rules": [rule.to_dict() for rule in self.rules],
        }


# Domain-oriented aliases keep call sites readable without creating divergent
# representations for gold and retrieved entities.
NodeRef = GraphNode
GoldNode = GraphNode
RelationshipRef = GraphRelationship
GoldRelationship = GraphRelationship
RetrievedPath = GraphPath
GoldPath = GraphPath

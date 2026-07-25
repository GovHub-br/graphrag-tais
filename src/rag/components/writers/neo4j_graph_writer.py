"""Writer idempotente do contrato Table/Column/Relationship no Neo4j."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, List

from haystack import component
from loguru import logger

from rag.components.graphstore.neo4j import Neo4jGraphStore

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RELATION_ENDPOINTS = {
    "HAS_COLUMN": ("Table", "Column"),
    "FOREIGN_KEY": ("Column", "Column"),
    "SEMANTIC_JOIN": ("Column", "Column"),
}


def _safe_ident(name: str) -> str:
    if not _IDENT.match(name or ""):
        raise ValueError(f"Identificador Cypher inválido: {name!r}")
    return name


@component
class Neo4jGraphWriter:
    """Cria constraints e faz upsert dos nós antes das arestas."""

    def __init__(self, graph_store: Neo4jGraphStore, batch_size: int = 500):
        self.store = graph_store
        self.batch_size = batch_size

    @component.output_types(nodes_written=int, relationships_written=int)
    def run(
        self,
        nodes: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        self._ensure_constraints()
        nodes_written = self._write_nodes(nodes or [])
        relationships_written = self._write_relationships(relationships or [])
        logger.info(
            "Neo4jGraphWriter: {} nós e {} arestas confirmados",
            nodes_written,
            relationships_written,
        )
        return {
            "nodes_written": nodes_written,
            "relationships_written": relationships_written,
        }

    def _ensure_constraints(self) -> None:
        statements = (
            "CREATE CONSTRAINT table_node_id IF NOT EXISTS "
            "FOR (n:Table) REQUIRE n.node_id IS UNIQUE",
            "CREATE CONSTRAINT column_node_id IF NOT EXISTS "
            "FOR (n:Column) REQUIRE n.node_id IS UNIQUE",
            "CREATE CONSTRAINT table_om_id IF NOT EXISTS "
            "FOR (n:Table) REQUIRE n.om_id IS UNIQUE",
        )
        for statement in statements:
            self.store.execute_write(statement)

    @staticmethod
    def _confirmed_count(result: list[dict[str, Any]]) -> int:
        if len(result) != 1 or "written" not in result[0]:
            raise RuntimeError("Neo4j não retornou a contagem de escrita")
        return int(result[0]["written"])

    def _write_nodes(self, nodes: list[dict[str, Any]]) -> int:
        by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node in nodes:
            label = _safe_ident(node["label"])
            if label not in {"Table", "Column"}:
                raise ValueError(f"Label fora do contrato v1: {label}")
            by_label[label].append(
                {"key": node["key"], "props": node.get("props", {})}
            )

        total = 0
        for label, rows in sorted(by_label.items()):
            cypher = (
                f"UNWIND $rows AS row "
                f"MERGE (n:`{label}` {{node_id: row.key}}) "
                "SET n += row.props "
                "RETURN count(n) AS written"
            )
            for chunk in self._chunks(rows):
                confirmed = self._confirmed_count(
                    self.store.execute_write(cypher, {"rows": chunk})
                )
                if confirmed != len(chunk):
                    raise RuntimeError(
                        f"Neo4j confirmou {confirmed}/{len(chunk)} nós {label}"
                    )
                total += confirmed
        return total

    def _write_relationships(self, relationships: list[dict[str, Any]]) -> int:
        by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for relationship in relationships:
            relationship_type = _safe_ident(relationship["type"])
            if relationship_type not in _RELATION_ENDPOINTS:
                raise ValueError(
                    f"Tipo de relação fora do contrato v1: {relationship_type}"
                )
            relationship_id = str(
                relationship.get("relationship_id") or ""
            ).strip()
            if not relationship_id:
                raise ValueError("relationship_id é obrigatório no Neo4j")
            by_type[relationship_type].append(
                {
                    "start": relationship["start"],
                    "end": relationship["end"],
                    "relationship_id": relationship_id,
                    "props": relationship.get("props", {}),
                }
            )

        total = 0
        for relationship_type, rows in sorted(by_type.items()):
            start_label, end_label = _RELATION_ENDPOINTS[relationship_type]
            cypher = (
                "UNWIND $rows AS row "
                f"MATCH (a:`{start_label}` {{node_id: row.start}}) "
                f"MATCH (b:`{end_label}` {{node_id: row.end}}) "
                f"MERGE (a)-[rel:`{relationship_type}` "
                "{relationship_id: row.relationship_id}]->(b) "
                "SET rel += row.props "
                "RETURN count(rel) AS written"
            )
            for chunk in self._chunks(rows):
                confirmed = self._confirmed_count(
                    self.store.execute_write(cypher, {"rows": chunk})
                )
                if confirmed != len(chunk):
                    raise RuntimeError(
                        "Neo4j não encontrou todas as pontas de "
                        f"{relationship_type}: {confirmed}/{len(chunk)}"
                    )
                total += confirmed
        return total

    def _chunks(self, rows: list[dict[str, Any]]):
        for index in range(0, len(rows), self.batch_size):
            yield rows[index : index + self.batch_size]

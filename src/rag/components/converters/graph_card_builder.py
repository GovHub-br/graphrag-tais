"""Projeção do modelo canônico em nós Neo4j e cards Qdrant.

O componente recebe somente os records normalizados da ingestão. Não conhece o
JSON bruto do OpenMetadata e não consulta valores de linha.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Dict, List
from uuid import NAMESPACE_URL, uuid5

from haystack import Document, component

from rag.settings.configs import settings


@component
class GraphCardBuilder:
    """Gera um TableCard e um ColumnCard determinístico por entidade."""

    def __init__(
        self,
        card_schema_version: str | None = None,
        embedding_profile: str | None = None,
    ) -> None:
        self.card_schema_version = (
            card_schema_version or settings.OM_CARD_SCHEMA_VERSION
        )
        self.embedding_profile = (
            embedding_profile or settings.OM_EMBEDDING_PROFILE
        )
        if not self.card_schema_version or not self.embedding_profile:
            raise ValueError(
                "OM_CARD_SCHEMA_VERSION e OM_EMBEDDING_PROFILE são obrigatórios"
            )

    @staticmethod
    def _point_id(scope_id: str, card_id: str) -> str:
        namespace = uuid5(NAMESPACE_URL, f"urn:graphrag-tais:{scope_id}")
        return str(uuid5(namespace, card_id))

    @staticmethod
    def _content_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _relationship_summary(
        relationship: dict[str, Any],
        *,
        node_id: str,
    ) -> str:
        source = relationship["source_node_id"]
        target = relationship["target_node_id"]
        direction = "->" if node_id in {source, relationship["source_table_node_id"]} else "<-"
        other = target if direction == "->" else source
        details = [
            str(relationship["relationship_type"]).lower(),
        ]
        for key in (
            "semantic_key",
            "join_rule",
            "cardinality",
            "confidence",
            "validation",
        ):
            if relationship.get(key):
                details.append(str(relationship[key]))
        details.append(
            f"sql_join_eligible={str(bool(relationship['sql_join_eligible'])).lower()}"
        )
        return f"{direction} {other} [{', '.join(details)}]"

    def _base_meta(
        self,
        *,
        records: dict[str, Any],
        card_id: str,
        card_type: str,
        node_id: str,
        relationship_ids: list[str],
        content: str,
    ) -> dict[str, Any]:
        return {
            "scope_id": records["scope_id"],
            "card_id": card_id,
            "card_type": card_type,
            "card_schema_version": self.card_schema_version,
            "node_id": node_id,
            "fqn": node_id,
            "relationship_ids": relationship_ids,
            "source_system": "openmetadata",
            "source_version": records["source_version"],
            "embedding_profile": self.embedding_profile,
            "content_hash": self._content_hash(content),
            "content_truncated": False,
            "omitted_column_count": 0,
            "omitted_relationship_count": 0,
            "ingestion_run_id": records["ingestion_run_id"],
        }

    @component.output_types(
        documents=List[Document],
        nodes=List[Dict[str, Any]],
        relationships=List[Dict[str, Any]],
        summary=Dict[str, Any],
    )
    def run(self, records: Dict[str, Any]) -> Dict[str, Any]:
        tables = list(records.get("tables") or [])
        columns = list(records.get("columns") or [])
        relationships = list(records.get("relationships") or [])
        scope_id = str(records.get("scope_id") or "")
        if not scope_id:
            raise ValueError("records.scope_id é obrigatório")

        columns_by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for column in columns:
            columns_by_table[column["table_node_id"]].append(column)
        for table_columns in columns_by_table.values():
            table_columns.sort(
                key=lambda column: (
                    column["ordinal_position"],
                    column["node_id"],
                )
            )

        relationships_by_column: dict[str, list[dict[str, Any]]] = defaultdict(list)
        relationships_by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for relationship in relationships:
            if relationship["relationship_type"] == "HAS_COLUMN":
                continue
            relationships_by_column[relationship["source_node_id"]].append(
                relationship
            )
            relationships_by_column[relationship["target_node_id"]].append(
                relationship
            )
            relationships_by_table[relationship["source_table_node_id"]].append(
                relationship
            )
            if (
                relationship["target_table_node_id"]
                != relationship["source_table_node_id"]
            ):
                relationships_by_table[relationship["target_table_node_id"]].append(
                    relationship
                )

        documents: list[Document] = []
        nodes: list[dict[str, Any]] = []

        for table in sorted(tables, key=lambda item: item["node_id"]):
            table_fqn = table["node_id"]
            table_columns = columns_by_table.get(table_fqn, [])
            table_relationships = sorted(
                relationships_by_table.get(table_fqn, []),
                key=lambda item: item["relationship_id"],
            )
            lines = [
                "Tipo: tabela",
                f"FQN: {table_fqn}",
                f"Nome: {table['name']}",
            ]
            if table.get("display_name") and table["display_name"] != table["name"]:
                lines.append(f"Nome de exibição: {table['display_name']}")
            lines.extend(
                [
                    f"Descrição: {table.get('description') or ''}",
                    f"Schema: {table['schema']}",
                    "Colunas: "
                    + "; ".join(
                        f"{column['name']}:{column['data_type']}"
                        for column in table_columns
                    ),
                ]
            )
            if table_relationships:
                lines.append("Relacionamentos:")
                lines.extend(
                    "- "
                    + self._relationship_summary(
                        relationship, node_id=table_fqn
                    )
                    for relationship in table_relationships
                )
            content = "\n".join(lines)
            relationship_ids = [
                relationship["relationship_id"]
                for relationship in table_relationships
            ]
            card_id = f"{scope_id}|table:{table_fqn}"
            meta = self._base_meta(
                records=records,
                card_id=card_id,
                card_type="table",
                node_id=table_fqn,
                relationship_ids=relationship_ids,
                content=content,
            )
            meta["om_id"] = table["om_id"]
            documents.append(
                Document(
                    id=self._point_id(scope_id, card_id),
                    content=content,
                    meta=meta,
                )
            )
            nodes.append(
                {
                    "label": "Table",
                    "key": table_fqn,
                    "props": dict(table),
                }
            )

        for column in sorted(columns, key=lambda item: item["node_id"]):
            column_fqn = column["node_id"]
            column_relationships = sorted(
                relationships_by_column.get(column_fqn, []),
                key=lambda item: item["relationship_id"],
            )
            lines = [
                "Tipo: coluna",
                f"FQN: {column_fqn}",
                f"Tabela: {column['table_node_id']}",
                f"Nome: {column['name']}",
                f"Tipo de dado: {column['data_type_display'] or column['data_type']}",
                f"Descrição: {column.get('description') or ''}",
                "Chave primária: "
                + ("sim" if column["is_primary_key"] else "não"),
            ]
            if column_relationships:
                lines.append("Relacionamentos:")
                lines.extend(
                    "- "
                    + self._relationship_summary(
                        relationship, node_id=column_fqn
                    )
                    for relationship in column_relationships
                )
            content = "\n".join(lines)
            relationship_ids = [
                relationship["relationship_id"]
                for relationship in column_relationships
            ]
            card_id = f"{scope_id}|column:{column_fqn}"
            meta = self._base_meta(
                records=records,
                card_id=card_id,
                card_type="column",
                node_id=column_fqn,
                relationship_ids=relationship_ids,
                content=content,
            )
            meta["table_node_id"] = column["table_node_id"]
            meta["table_om_id"] = column["table_om_id"]
            documents.append(
                Document(
                    id=self._point_id(scope_id, card_id),
                    content=content,
                    meta=meta,
                )
            )
            nodes.append(
                {
                    "label": "Column",
                    "key": column_fqn,
                    "props": dict(column),
                }
            )

        graph_relationships = [
            {
                "type": relationship["relationship_type"],
                "start": relationship["source_node_id"],
                "end": relationship["target_node_id"],
                "relationship_id": relationship["relationship_id"],
                "props": dict(relationship),
            }
            for relationship in relationships
        ]
        return {
            "documents": documents,
            "nodes": nodes,
            "relationships": graph_relationships,
            "summary": {
                "tables": len(tables),
                "columns": len(columns),
                "relationships": len(relationships),
                "cards": len(documents),
                "table_cards": len(tables),
                "column_cards": len(columns),
            },
        }

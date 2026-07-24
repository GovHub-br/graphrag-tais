"""Normalização do snapshot OpenMetadata no contrato canônico da ADR-0011."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, Dict

from haystack import component
from loguru import logger

from rag.components.sources.relationship_adapters import (
    RelationshipSourceAdapter,
    build_relationship_adapter,
)
from rag.settings.configs import settings


def _tag_fqns(tags: Any) -> list[str]:
    if not isinstance(tags, list):
        return []
    return sorted(
        {
            str(tag.get("tagFQN")).strip()
            for tag in tags
            if isinstance(tag, Mapping) and tag.get("tagFQN")
        }
    )


def _reference_fqn(reference: Any) -> str:
    if not isinstance(reference, Mapping):
        return ""
    return str(reference.get("fullyQualifiedName") or "").strip()


def _relationship_id(record: Mapping[str, Any], scope_id: str) -> str:
    parts = (
        "v1",
        scope_id,
        str(record["relationship_type"]),
        str(record["source_node_id"]),
        str(record["target_node_id"]),
        str(record["source_kind"]),
        str(record.get("constraint_group_id") or ""),
        str(record.get("semantic_key") or ""),
        str(record.get("join_rule") or ""),
    )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


@component
class MetadataRecordBuilder:
    """Valida e transforma JSON da fonte em Table/Column/Relationship records."""

    def __init__(
        self,
        relationship_adapter: RelationshipSourceAdapter | None = None,
    ) -> None:
        self.relationship_adapter = (
            relationship_adapter
            if relationship_adapter is not None
            else build_relationship_adapter(
                settings.OM_RELATIONSHIP_ADAPTER,
                markdown_property=settings.OM_SEMANTIC_RELATIONSHIPS_PROPERTY,
                related_tables_property=settings.OM_RELATED_TABLES_PROPERTY,
            )
        )

    @staticmethod
    def _constraint_columns(constraint: Mapping[str, Any]) -> list[str]:
        columns = constraint.get("columns") or []
        if not isinstance(columns, list):
            raise ValueError("tableConstraints.columns deve ser uma lista")
        normalized = [str(column).strip() for column in columns]
        if any(not column for column in normalized):
            raise ValueError("tableConstraints.columns contém referência vazia")
        return normalized

    @staticmethod
    def _column_fqn(table_fqn: str, reference: str) -> str:
        return reference if "." in reference else f"{table_fqn}.{reference}"

    @staticmethod
    def _constraint_group_id(
        table_fqn: str,
        local_columns: list[str],
        referred_columns: list[str],
    ) -> str:
        pairs = "|".join(
            f"{local}->{target}"
            for local, target in zip(local_columns, referred_columns, strict=True)
        )
        return hashlib.sha256(
            f"v1|foreign_key_group|{table_fqn}|{pairs}".encode("utf-8")
        ).hexdigest()

    @component.output_types(records=Dict[str, Any])
    def run(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        scope_id = str(snapshot.get("scope_id") or "").strip()
        ingestion_run_id = str(snapshot.get("ingestion_run_id") or "").strip()
        source_version = str(snapshot.get("source_version") or "").strip()
        service = str(snapshot.get("service") or "").strip()
        database_name = str(snapshot.get("database") or "").strip()
        raw_tables = snapshot.get("tables")
        if not all((scope_id, ingestion_run_id, source_version, service, database_name)):
            raise ValueError("Snapshot sem identidade/escopo obrigatório")
        if not isinstance(raw_tables, list):
            raise ValueError("snapshot.tables deve ser uma lista")

        tables: list[dict[str, Any]] = []
        columns: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []
        table_by_fqn: dict[str, dict[str, Any]] = {}
        column_by_fqn: dict[str, dict[str, Any]] = {}
        raw_by_fqn: dict[str, dict[str, Any]] = {}

        for raw_table in raw_tables:
            if not isinstance(raw_table, Mapping):
                raise ValueError("Tabela do snapshot não é um objeto")
            table_fqn = str(raw_table.get("fullyQualifiedName") or "").strip()
            om_id = str(raw_table.get("id") or "").strip()
            name = str(raw_table.get("name") or "").strip()
            database_fqn = _reference_fqn(raw_table.get("database"))
            schema_fqn = _reference_fqn(raw_table.get("databaseSchema"))
            service_fqn = _reference_fqn(raw_table.get("service"))
            if not all(
                (table_fqn, om_id, name, database_fqn, schema_fqn, service_fqn)
            ):
                raise ValueError(f"Tabela sem campos obrigatórios: {table_fqn or name}")
            if table_fqn in table_by_fqn:
                raise ValueError(f"Tabela canônica duplicada: {table_fqn}")

            table_record = {
                "scope_id": scope_id,
                "node_id": table_fqn,
                "om_id": om_id,
                "name": name,
                "display_name": raw_table.get("displayName"),
                "description": raw_table.get("description") or "",
                "service": service_fqn,
                "database": database_fqn,
                "schema": schema_fqn,
                "table_type": raw_table.get("tableType"),
                "tags": _tag_fqns(raw_table.get("tags")),
                "source_updated_at": raw_table.get("updatedAt")
                or raw_table.get("updated"),
                "ingestion_run_id": ingestion_run_id,
            }
            tables.append(table_record)
            table_by_fqn[table_fqn] = table_record
            raw_by_fqn[table_fqn] = dict(raw_table)

            raw_columns = raw_table.get("columns") or []
            if not isinstance(raw_columns, list):
                raise ValueError(f"columns inválido em {table_fqn}")

            primary_key_names: set[str] = set()
            table_constraints = raw_table.get("tableConstraints") or []
            if not isinstance(table_constraints, list):
                raise ValueError(f"tableConstraints inválido em {table_fqn}")
            for constraint in table_constraints:
                if not isinstance(constraint, Mapping):
                    raise ValueError(f"Constraint inválida em {table_fqn}")
                if str(constraint.get("constraintType") or "").upper() in {
                    "PRIMARY_KEY",
                    "PRIMARY KEY",
                }:
                    primary_key_names.update(self._constraint_columns(constraint))

            for position, raw_column in enumerate(raw_columns, start=1):
                if not isinstance(raw_column, Mapping):
                    raise ValueError(f"Coluna inválida em {table_fqn}")
                column_name = str(raw_column.get("name") or "").strip()
                if not column_name:
                    raise ValueError(f"Coluna sem nome em {table_fqn}")
                column_fqn = str(
                    raw_column.get("fullyQualifiedName")
                    or f"{table_fqn}.{column_name}"
                ).strip()
                if not column_fqn.startswith(f"{table_fqn}."):
                    raise ValueError(f"Coluna fora da tabela pai: {column_fqn}")
                if column_fqn in column_by_fqn:
                    raise ValueError(f"Coluna canônica duplicada: {column_fqn}")

                ordinal = raw_column.get("ordinalPosition")
                if not isinstance(ordinal, int) or ordinal < 0:
                    ordinal = position
                constraint_name = str(raw_column.get("constraint") or "").upper()
                is_primary_key = (
                    constraint_name in {"PRIMARY_KEY", "PRIMARY KEY", "PK"}
                    or column_name in primary_key_names
                    or column_fqn in primary_key_names
                )
                data_type = str(
                    raw_column.get("dataType")
                    or raw_column.get("dataTypeDisplay")
                    or ""
                ).strip()
                if not data_type:
                    raise ValueError(f"Coluna sem tipo lógico: {column_fqn}")

                column_record = {
                    "scope_id": scope_id,
                    "node_id": column_fqn,
                    "table_node_id": table_fqn,
                    "table_om_id": om_id,
                    "name": column_name,
                    "description": raw_column.get("description") or "",
                    "data_type": data_type,
                    "data_type_display": raw_column.get("dataTypeDisplay"),
                    "ordinal_position": ordinal,
                    "constraint": raw_column.get("constraint"),
                    "is_primary_key": is_primary_key,
                    "tags": _tag_fqns(raw_column.get("tags")),
                    "ingestion_run_id": ingestion_run_id,
                }
                columns.append(column_record)
                column_by_fqn[column_fqn] = column_record

                relationships.append(
                    {
                        "relationship_type": "HAS_COLUMN",
                        "source_node_id": table_fqn,
                        "target_node_id": column_fqn,
                        "source_table_node_id": table_fqn,
                        "target_table_node_id": table_fqn,
                        "source_kind": "structure",
                        "ordinal_position": ordinal,
                        "sql_join_eligible": False,
                        "sql_eligibility_reason": "Relação estrutural, não é JOIN",
                    }
                )

        for table_fqn, raw_table in sorted(raw_by_fqn.items()):
            constraints = raw_table.get("tableConstraints") or []
            for constraint_position, constraint in enumerate(constraints):
                constraint_type = str(
                    constraint.get("constraintType") or ""
                ).upper()
                if constraint_type not in {"FOREIGN_KEY", "FOREIGN KEY"}:
                    continue
                local_refs = self._constraint_columns(constraint)
                referred_raw = constraint.get("referredColumns") or []
                if not isinstance(referred_raw, list):
                    raise ValueError(
                        f"referredColumns inválido na FK de {table_fqn}"
                    )
                referred_refs = [
                    str(column).strip() for column in referred_raw
                ]
                if any(not column for column in referred_refs):
                    raise ValueError(
                        f"referredColumns contém referência vazia em {table_fqn}"
                    )
                if not local_refs or len(local_refs) != len(referred_refs):
                    raise ValueError(
                        f"FK composta inconsistente em {table_fqn}: "
                        f"{len(local_refs)} colunas locais e "
                        f"{len(referred_refs)} referenciadas"
                    )
                local_fqns = [
                    self._column_fqn(table_fqn, reference)
                    for reference in local_refs
                ]
                group_id = self._constraint_group_id(
                    table_fqn, local_fqns, referred_refs
                )
                for local_fqn, referred_fqn in zip(
                    local_fqns, referred_refs, strict=True
                ):
                    if local_fqn not in column_by_fqn:
                        raise ValueError(f"FK local sem coluna: {local_fqn}")
                    if referred_fqn not in column_by_fqn:
                        raise ValueError(
                            f"FK aponta para coluna fora do snapshot: {referred_fqn}"
                        )
                    relationships.append(
                        {
                            "relationship_type": "FOREIGN_KEY",
                            "source_node_id": local_fqn,
                            "target_node_id": referred_fqn,
                            "source_table_node_id": table_fqn,
                            "target_table_node_id": column_by_fqn[referred_fqn][
                                "table_node_id"
                            ],
                            "source_kind": "table_constraint",
                            "constraint_group_id": group_id,
                            "constraint_position": constraint_position,
                            "sql_join_eligible": True,
                            "sql_eligibility_reason": None,
                        }
                    )

        semantic_stats: dict[str, int] = {}
        if self.relationship_adapter is not None:
            semantic, semantic_stats = self.relationship_adapter.extract(
                raw_tables,
                service=service,
                database=database_name,
                column_node_ids=set(column_by_fqn),
            )
            relationships.extend(semantic)

        canonical_relationships: dict[str, dict[str, Any]] = {}
        for relationship in relationships:
            source_node_id = relationship["source_node_id"]
            target_node_id = relationship["target_node_id"]
            if (
                source_node_id not in table_by_fqn
                and source_node_id not in column_by_fqn
            ):
                raise ValueError(f"Ponta de origem ausente: {source_node_id}")
            if (
                target_node_id not in table_by_fqn
                and target_node_id not in column_by_fqn
            ):
                raise ValueError(f"Ponta de destino ausente: {target_node_id}")
            relationship["scope_id"] = scope_id
            relationship["ingestion_run_id"] = ingestion_run_id
            relationship["relationship_id"] = _relationship_id(
                relationship, scope_id
            )
            relationship_id = relationship["relationship_id"]
            previous = canonical_relationships.get(relationship_id)
            if previous is not None and previous != relationship:
                raise ValueError(f"Colisão de relacionamento: {relationship_id}")
            canonical_relationships[relationship_id] = relationship

        records = {
            "scope_id": scope_id,
            "source_version": source_version,
            "ingestion_run_id": ingestion_run_id,
            "tables": sorted(tables, key=lambda item: item["node_id"]),
            "columns": sorted(columns, key=lambda item: item["node_id"]),
            "relationships": [
                canonical_relationships[key]
                for key in sorted(canonical_relationships)
            ],
            "stats": {
                **dict(snapshot.get("stats") or {}),
                **semantic_stats,
                "columns_accepted": len(columns),
                "relationships_accepted": len(canonical_relationships),
                "relationships_by_type": {
                    relationship_type: sum(
                        1
                        for relation in canonical_relationships.values()
                        if relation["relationship_type"] == relationship_type
                    )
                    for relationship_type in (
                        "HAS_COLUMN",
                        "FOREIGN_KEY",
                        "SEMANTIC_JOIN",
                    )
                },
            },
        }
        logger.info(
            "Records {}: {} tabelas, {} colunas, {} relações",
            ingestion_run_id,
            len(tables),
            len(columns),
            len(canonical_relationships),
        )
        return {"records": records}

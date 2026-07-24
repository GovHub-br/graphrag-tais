"""Adapters de relações semânticas publicadas no OpenMetadata.

O núcleo da ingestão conhece apenas o contrato lógico de relacionamento. A
gramática Markdown específica do MCID fica isolada neste módulo.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Protocol

from loguru import logger


class RelationshipSourceAdapter(Protocol):
    """Converte metadados específicos da fonte em relações semânticas."""

    def extract(
        self,
        tables: list[dict[str, Any]],
        *,
        service: str,
        database: str,
        column_node_ids: set[str],
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        ...


class MCDMarkdownRelationshipAdapter:
    """Parser da gramática produzida por ``render_table_markdown`` do MCID."""

    CATALOG_ID = "mcid-semantic-relationships"
    _HEADER = re.compile(
        r"Catálogo `(?P<catalog>[^`]+)`, evidência agregada de "
        r"`(?P<snapshot>[^`]+)`\."
    )
    _RELATION = re.compile(
        r"^- \*\*(?P<classification>[^*]+)\*\* "
        r"\((?P<role>origem|destino)\): "
        r"`(?P<source>[^`]+)` → `(?P<target>[^`]+)`; "
        r"chave `(?P<semantic_key>[^`]+)`; "
        r"JOIN `(?P<join_rule>[^`]+)`; "
        r"cardinalidade `(?P<cardinality>[^`]+)`; "
        r"confiança `(?P<confidence>[^`]+)`"
        r"(?P<coverage>.*)\.$"
    )
    _VALIDATIONS = {
        "Contrato validado por teste dbt": "dbt_relationships_test",
        "Relação validada por agregados": "aggregate_validated",
    }
    _CANDIDATE = "Relação candidata"

    def __init__(
        self,
        markdown_property: str,
        related_tables_property: str,
    ) -> None:
        self.markdown_property = markdown_property
        self.related_tables_property = related_tables_property

    @staticmethod
    def _explicit_lines(markdown: str) -> list[str]:
        in_section = False
        lines: list[str] = []
        for line in markdown.splitlines():
            stripped = line.strip()
            if stripped == "### Relações explícitas":
                in_section = True
                continue
            if in_section and stripped.startswith("### "):
                break
            if in_section and stripped.startswith("- **"):
                lines.append(stripped)
        return lines

    @staticmethod
    def _normalize_column_reference(
        reference: str,
        *,
        service: str,
        database: str,
    ) -> str:
        parts = [part.strip() for part in reference.split(".")]
        if len(parts) == 3 and all(parts):
            return ".".join((service, database, *parts))
        if (
            len(parts) == 5
            and all(parts)
            and parts[0] == service
            and parts[1] == database
        ):
            return ".".join(parts)
        raise ValueError(
            "Referência semântica inválida ou fora do escopo: "
            f"{reference!r}"
        )

    @staticmethod
    def _related_table_fqns(extension: Mapping[str, Any], property_name: str) -> set[str]:
        values = extension.get(property_name) or []
        if not isinstance(values, list):
            raise ValueError(f"{property_name} deve ser uma lista")
        return {
            str(item.get("fullyQualifiedName")).strip()
            for item in values
            if isinstance(item, Mapping) and item.get("fullyQualifiedName")
        }

    def extract(
        self,
        tables: list[dict[str, Any]],
        *,
        service: str,
        database: str,
        column_node_ids: set[str],
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        extracted: dict[tuple[str, ...], dict[str, Any]] = {}
        candidate_keys: set[tuple[str, ...]] = set()
        stats = {
            "semantic_lines_read": 0,
            "semantic_candidates_skipped": 0,
            "semantic_relationships_deduplicated": 0,
        }

        for table in tables:
            table_fqn = str(table.get("fullyQualifiedName") or "").strip()
            extension = table.get("extension") or {}
            if not isinstance(extension, Mapping):
                raise ValueError(f"extension inválida em {table_fqn}")
            markdown = extension.get(self.markdown_property)
            if not isinstance(markdown, str) or not markdown.strip():
                continue

            header = self._HEADER.search(markdown)
            if not header or header.group("catalog") != self.CATALOG_ID:
                raise ValueError(
                    f"Contrato Markdown desconhecido em {table_fqn}"
                )
            evidence_snapshot = header.group("snapshot")
            related_table_fqns = self._related_table_fqns(
                extension, self.related_tables_property
            )

            for line in self._explicit_lines(markdown):
                stats["semantic_lines_read"] += 1
                match = self._RELATION.match(line)
                if not match:
                    raise ValueError(
                        f"Linha de relação semântica malformada em {table_fqn}: {line}"
                    )

                classification = match.group("classification")
                if classification == self._CANDIDATE:
                    candidate_keys.add(
                        (
                            match.group("source"),
                            match.group("target"),
                            match.group("semantic_key"),
                            match.group("join_rule"),
                        )
                    )
                    continue
                validation = self._VALIDATIONS.get(classification)
                if validation is None:
                    raise ValueError(
                        f"Classificação semântica desconhecida: {classification!r}"
                    )

                source = self._normalize_column_reference(
                    match.group("source"),
                    service=service,
                    database=database,
                )
                target = self._normalize_column_reference(
                    match.group("target"),
                    service=service,
                    database=database,
                )
                if source not in column_node_ids or target not in column_node_ids:
                    raise ValueError(
                        "Relação semântica aponta para coluna fora do snapshot: "
                        f"{source} -> {target}"
                    )

                source_table = source.rsplit(".", 1)[0]
                target_table = target.rsplit(".", 1)[0]
                if table_fqn not in {source_table, target_table}:
                    raise ValueError(
                        f"Relação publicada em tabela que não é uma ponta: {table_fqn}"
                    )
                other_table = target_table if table_fqn == source_table else source_table
                if other_table not in related_table_fqns:
                    raise ValueError(
                        f"{self.related_tables_property} não confirma {other_table} "
                        f"em {table_fqn}"
                    )

                join_rule = match.group("join_rule")
                coverage = match.group("coverage").strip()
                if coverage.startswith(";"):
                    coverage = coverage[1:].strip()
                key = (
                    source,
                    target,
                    match.group("semantic_key"),
                    join_rule,
                    validation,
                )
                relationship = {
                    "relationship_type": "SEMANTIC_JOIN",
                    "source_node_id": source,
                    "target_node_id": target,
                    "source_table_node_id": source_table,
                    "target_table_node_id": target_table,
                    "source_kind": "mcid_markdown_v1",
                    "semantic_key": match.group("semantic_key"),
                    "join_rule": join_rule,
                    "normalization_hint": (
                        None
                        if join_rule == "exact"
                        else "A relação exige normalização documentada fora do SQL automático."
                    ),
                    "cardinality": match.group("cardinality"),
                    "confidence": match.group("confidence"),
                    "validation": validation,
                    "evidence_snapshot": evidence_snapshot,
                    "coverage_summary": coverage or None,
                    "sql_join_eligible": join_rule == "exact",
                    "sql_eligibility_reason": (
                        None
                        if join_rule == "exact"
                        else f"join_rule={join_rule} não possui DSL SQL versionada"
                    ),
                }
                previous = extracted.get(key)
                if previous is not None:
                    stats["semantic_relationships_deduplicated"] += 1
                    if previous != relationship:
                        raise ValueError(
                            f"Publicações divergentes da relação {source} -> {target}"
                        )
                    continue
                extracted[key] = relationship

        stats["semantic_candidates_skipped"] = len(candidate_keys)
        logger.info(
            "Adapter MCID: {} relações semânticas, {} candidatas ignoradas",
            len(extracted),
            stats["semantic_candidates_skipped"],
        )
        return [extracted[key] for key in sorted(extracted)], stats


def build_relationship_adapter(
    name: str,
    *,
    markdown_property: str,
    related_tables_property: str,
) -> RelationshipSourceAdapter | None:
    normalized = (name or "").strip().lower()
    if normalized in {"", "none", "disabled"}:
        return None
    if normalized == "mcid_markdown_v1":
        return MCDMarkdownRelationshipAdapter(
            markdown_property=markdown_property,
            related_tables_property=related_tables_property,
        )
    raise ValueError(f"OM_RELATIONSHIP_ADAPTER desconhecido: {name!r}")

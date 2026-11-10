"""Adapters de relações semânticas publicadas no OpenMetadata.

O núcleo da ingestão conhece apenas o contrato lógico de relacionamento. A
gramática Markdown específica de cada escopo fica isolada neste módulo.

MCid e MinC publicam a MESMA gramática — mudam só o identificador do catálogo e
os nomes das Custom Properties. Por isso os dois usam a mesma classe, com o
`catalog_id` parametrizado, em vez de um parser por escopo.
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
        column_data_types: Mapping[str, str],
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        ...


class MCDMarkdownRelationshipAdapter:
    """Parser da gramática produzida por ``render_table_markdown``.

    Serve MCid (`mcid-semantic-relationships`) e MinC
    (`minc-semantic-relationships`): a gramática das linhas é idêntica — medido
    contra as 408 tabelas anotadas do MinC, 703 linhas, zero malformadas.
    """

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
        catalog_id: str,
        eligible_join_rules: str = "exact",
        source_kind: str | None = None,
        lineage_only_markers: str = "",
    ) -> None:
        self.markdown_property = markdown_property
        self.related_tables_property = related_tables_property
        # O identificador do catálogo é o que separa um escopo do outro; sem
        # parametrizar, o MinC bate no `Contrato Markdown desconhecido`.
        if not (catalog_id or "").strip():
            raise ValueError("catalog_id é obrigatório: é ele que separa um escopo do outro")
        self.catalog_id = catalog_id.strip()
        # Procedência da aresta no grafo. Sem isto, relação do MinC era gravada
        # como `mcid_markdown_v1` — rótulo errado num grafo que pode ter os dois
        # escopos ao mesmo tempo.
        self.source_kind = source_kind or "markdown_v1"
        self.eligible_join_rules = {
            rule.strip().lower()
            for rule in eligible_join_rules.split(",")
            if rule.strip()
        }
        # Prosa de LINHAGEM que um processo do escopo publica na mesma
        # propriedade quando a tabela não tem relação medida (no MCid, o sync
        # de governança do dbt: ~220 de 253 tabelas de prata+ouro, out/2026).
        # Linhagem diz quem deriva de quem, não por qual coluna cruzar — então
        # não vira aresta, mas também não é contrato desconhecido. Os
        # marcadores vêm do escopo (OM_LINEAGE_ONLY_MARKERS); sem eles, nada é
        # tolerado. Reconhecimento POSITIVO pela abertura: qualquer outra
        # gramática continua abortando.
        markers = [m.strip() for m in (lineage_only_markers or "").split(",") if m.strip()]
        self._lineage_only = (
            re.compile(
                r"^\s*\*\*(?:" + "|".join(re.escape(m) for m in markers) + r"):\*\*"
            )
            if markers
            else None
        )

    @staticmethod
    def _type_family(data_type: str) -> str:
        """Agrupa aliases SQL cuja igualdade direta é compatível.

        A liberação só é necessária para regras não-exatas. Tipos desconhecidos
        permanecem comparáveis apenas quando o nome base é idêntico, mantendo a
        decisão fail-closed para a divergência VARCHAR↔INTEGER encontrada no MinC.
        """
        base = re.split(r"[\s(<]", data_type.strip().upper(), maxsplit=1)[0]
        families = {
            "text": {
                "CHAR", "CHARACTER", "NCHAR", "NVARCHAR", "STRING", "TEXT",
                "TINYTEXT", "MEDIUMTEXT", "LONGTEXT", "VARCHAR",
            },
            "integer": {
                "BIGINT", "BYTE", "INT", "INTEGER", "LONG", "SHORT",
                "SMALLINT", "TINYINT",
            },
            "decimal": {"DECIMAL", "NUMBER", "NUMERIC"},
            "float": {"DOUBLE", "FLOAT", "REAL"},
            "boolean": {"BOOL", "BOOLEAN"},
            "date": {"DATE"},
            "timestamp": {"DATETIME", "TIMESTAMP", "TIMESTAMPZ"},
        }
        return next(
            (family for family, members in families.items() if base in members),
            base,
        )

    @classmethod
    def _types_compatible(cls, source_type: str, target_type: str) -> bool:
        if not source_type.strip() or not target_type.strip():
            return False
        return cls._type_family(source_type) == cls._type_family(target_type)

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
        column_data_types: Mapping[str, str],
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        extracted: dict[tuple[str, ...], dict[str, Any]] = {}
        candidate_keys: set[tuple[str, ...]] = set()
        # Tabelas presentes no snapshot, derivadas das colunas: é o que separa
        # "fora do escopo ingerido" de "coluna que não existe".
        table_node_ids = {ref.rsplit(".", 1)[0] for ref in column_node_ids}
        stats = {
            "semantic_lines_read": 0,
            "semantic_relationships_out_of_scope": 0,
            "semantic_candidates_skipped": 0,
            "semantic_relationships_deduplicated": 0,
            "semantic_lineage_only_tables": 0,
            "semantic_relationships_unconfirmed": 0,
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
            if not header and self._lineage_only and self._lineage_only.match(markdown):
                stats["semantic_lineage_only_tables"] += 1
                continue
            if not header or header.group("catalog") != self.catalog_id:
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
                # Duas situações MUITO diferentes se escondem em "coluna ausente":
                #
                # (a) a TABELA da outra ponta não está no snapshot — é fronteira
                #     de escopo, não erro. `OM_REQUIRE_DOCUMENTED_TABLES` é uma
                #     restrição NOSSA; o catálogo descreve o banco inteiro e cita
                #     legitimamente tabelas que não ingerimos. No MinC são 45
                #     relações apontando para 32 tabelas reais (quase todas views
                #     `vwpainel*`) que ninguém anotou. Abortar a ingestão por isso
                #     deixaria as outras 243 relações válidas de fora.
                #
                # (b) a tabela ESTÁ no snapshot mas a coluna não — aí é erro de
                #     verdade (nome errado no catálogo) e continua estourando.
                missing = [
                    ref for ref in (source, target) if ref not in column_node_ids
                ]
                if missing:
                    out_of_scope = [
                        ref for ref in missing
                        if ref.rsplit(".", 1)[0] not in table_node_ids
                    ]
                    if len(out_of_scope) == len(missing):
                        stats["semantic_relationships_out_of_scope"] += 1
                        continue
                    raise ValueError(
                        "Relação semântica aponta para coluna inexistente em "
                        "tabela do snapshot: "
                        f"{source} -> {target}"
                    )

                source_table = source.rsplit(".", 1)[0]
                target_table = target.rsplit(".", 1)[0]
                if table_fqn not in {source_table, target_table}:
                    raise ValueError(
                        f"Relação publicada em tabela que não é uma ponta: {table_fqn}"
                    )
                other_table = target_table if table_fqn == source_table else source_table
                # As duas propriedades podem ser publicadas por processos
                # diferentes (MCid, out/2026: relações avulsas de setembro, lista
                # de tabelas reescrita depois pelo sync do dbt). Divergência é
                # catálogo inconsistente: a aresta não confirmada fica de FORA,
                # mas não derruba a ingestão das outras.
                if other_table not in related_table_fqns:
                    stats["semantic_relationships_unconfirmed"] += 1
                    logger.warning(
                        "{} não confirma {} em {}; relação ignorada",
                        self.related_tables_property,
                        other_table,
                        table_fqn,
                    )
                    continue

                join_rule = match.group("join_rule")
                normalized_join_rule = join_rule.strip().lower()
                source_type = str(column_data_types.get(source) or "")
                target_type = str(column_data_types.get(target) or "")
                rule_is_allowed = normalized_join_rule in self.eligible_join_rules
                # `exact` já era elegível antes desta mudança; não alteramos o
                # comportamento do MCid. Regras adicionais exigem tipos
                # comparáveis para não transformar VARCHAR↔INTEGER em SQL inválido.
                types_are_compatible = (
                    normalized_join_rule == "exact"
                    or self._types_compatible(source_type, target_type)
                )
                sql_join_eligible = rule_is_allowed and types_are_compatible
                if not rule_is_allowed:
                    sql_eligibility_reason = (
                        f"join_rule={join_rule} não está autorizada em "
                        "OM_SQL_ELIGIBLE_JOIN_RULES"
                    )
                elif not types_are_compatible:
                    sql_eligibility_reason = (
                        "Tipos incompatíveis para JOIN direto: "
                        f"{source_type or 'desconhecido'} -> "
                        f"{target_type or 'desconhecido'}"
                    )
                else:
                    sql_eligibility_reason = None
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
                    "source_kind": self.source_kind,
                    "semantic_key": match.group("semantic_key"),
                    "join_rule": join_rule,
                    "normalization_hint": (
                        None
                        if sql_join_eligible
                        else "A relação exige normalização documentada fora do SQL automático."
                    ),
                    "cardinality": match.group("cardinality"),
                    "confidence": match.group("confidence"),
                    "validation": validation,
                    "evidence_snapshot": evidence_snapshot,
                    "coverage_summary": coverage or None,
                    "sql_join_eligible": sql_join_eligible,
                    "sql_eligibility_reason": sql_eligibility_reason,
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
        stats["semantic_relationships_sql_eligible"] = sum(
            bool(relationship["sql_join_eligible"])
            for relationship in extracted.values()
        )
        stats["semantic_relationships_type_incompatible"] = sum(
            str(relationship.get("sql_eligibility_reason") or "").startswith(
                "Tipos incompatíveis"
            )
            for relationship in extracted.values()
        )
        logger.info(
            "Adapter {}: {} relações semânticas, {} candidatas ignoradas, "
            "{} fora do escopo ingerido",
            self.catalog_id,
            len(extracted),
            stats["semantic_candidates_skipped"],
            stats["semantic_relationships_out_of_scope"],
        )
        return [extracted[key] for key in sorted(extracted)], stats


_CONVENCAO = re.compile(r"^([a-z0-9]+)_markdown_v\d+$")


def _catalog_id_por_convencao(nome: str) -> str:
    """`<escopo>_markdown_v1` -> `<escopo>-semantic-relationships`.

    Evita registro hardcoded de escopo no código: escopo novo entra só por
    `.env`. `OM_SEMANTIC_CATALOG_ID` sobrepõe, para catálogo que não siga a
    convenção.
    """
    m = _CONVENCAO.match(nome)
    return f"{m.group(1)}-semantic-relationships" if m else ""


def build_relationship_adapter(
    name: str,
    *,
    markdown_property: str,
    related_tables_property: str,
    eligible_join_rules: str = "exact",
    catalog_id: str = "",
    lineage_only_markers: str = "",
) -> RelationshipSourceAdapter | None:
    normalized = (name or "").strip().lower()
    if normalized in {"", "none", "disabled"}:
        return None
    if not (markdown_property or "").strip() or not (related_tables_property or "").strip():
        raise ValueError(
            f"OM_RELATIONSHIP_ADAPTER={name!r} exige OM_SEMANTIC_RELATIONSHIPS_PROPERTY "
            "e OM_RELATED_TABLES_PROPERTY"
        )
    resolvido = (catalog_id or "").strip() or _catalog_id_por_convencao(normalized)
    if not resolvido:
        raise ValueError(
            f"OM_RELATIONSHIP_ADAPTER desconhecido: {name!r}. Use "
            "`<escopo>_markdown_v1` ou declare OM_SEMANTIC_CATALOG_ID."
        )
    return MCDMarkdownRelationshipAdapter(
        markdown_property=markdown_property,
        related_tables_property=related_tables_property,
        catalog_id=resolvido,
        eligible_join_rules=eligible_join_rules,
        source_kind=normalized,
        lineage_only_markers=lineage_only_markers,
    )

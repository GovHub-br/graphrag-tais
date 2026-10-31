"""Projeção do modelo canônico em nós Neo4j e cards Qdrant.

O componente recebe somente os records normalizados da ingestão. Não conhece o
JSON bruto do OpenMetadata e não consulta valores de linha.
"""

from __future__ import annotations

import hashlib
import json
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
        embed_relationships: bool | None = None,
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
        self.embed_relationships = (
            embed_relationships
            if embed_relationships is not None
            else settings.OM_CARD_EMBED_RELATIONSHIPS
        )
        mode = str(settings.OM_CARD_PRODUCT_LINE or "off").strip().lower()
        self.product_line = {"true": "full", "false": "off", "": "off"}.get(mode, mode)
        if self.product_line not in {"off", "label", "full"}:
            raise ValueError(f"OM_CARD_PRODUCT_LINE desconhecido: {mode}")
        if self.card_schema_version not in {"v1", "v2"}:
            raise ValueError(
                f"OM_CARD_SCHEMA_VERSION desconhecida: {self.card_schema_version}"
            )
        self._domain_labels = self._parse_json_map(
            settings.OM_DOMAIN_LABELS, "OM_DOMAIN_LABELS"
        )
        self._grain_keys = self._parse_json_map(
            settings.OM_GRAIN_KEYS, "OM_GRAIN_KEYS"
        )
        self._value_vocab = self._parse_nested_map(
            settings.OM_VALUE_VOCAB, "OM_VALUE_VOCAB"
        )

    @staticmethod
    def _point_id(scope_id: str, card_id: str) -> str:
        namespace = uuid5(NAMESPACE_URL, f"urn:graphrag-tais:{scope_id}")
        return str(uuid5(namespace, card_id))

    @staticmethod
    def _content_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _short_schema(schema_fqn: str) -> str:
        """Último segmento do FQN do schema — o nome físico, que é o que casa."""
        return (schema_fqn or "").rsplit(".", 1)[-1]

    def _domain(self, schema_fqn: str) -> str:
        """Domínio de negócio do card v2.

        Sem mapa configurado devolve o nome curto do schema, que já é um token
        discriminante e funciona em qualquer catálogo. Com mapa, devolve o
        rótulo de negócio — que é o que casa com a pergunta do usuário, escrita
        em termos de negócio ("MCMV Entidades") e não de schema ("entidades_fds").
        """
        short = self._short_schema(schema_fqn)
        return self._domain_labels.get(short, short)

    def _grain(self, table_fqn: str) -> str:
        return self._grain_keys.get(table_fqn, "")

    def _vocab_line(self, table_fqn: str) -> str:
        """Vocabulário de VALOR da tabela, para o texto indexado.

        Em tabela EAV o termo da pergunta ("situação das obras", "execução
        física") é um VALOR de linha, não um nome de coluna — e o card indexava
        só os nomes. Sem esta linha, nenhum embedding e nenhum BM25 conseguem
        casar, porque o termo não existe no índice.
        """
        cols = self._value_vocab.get(table_fqn) or {}
        if not cols:
            return ""
        partes = [f"{col} = {', '.join(vals)}" for col, vals in cols.items() if vals]
        return "Valores: " + "; ".join(partes) if partes else ""

    @staticmethod
    def _parse_nested_map(raw: str, field: str) -> dict[str, dict[str, list]]:
        if not (raw or "").strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} não é JSON válido: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{field} precisa ser um objeto JSON")
        return {
            str(fqn): {str(c): [str(v) for v in vals] for c, vals in (cols or {}).items()}
            for fqn, cols in parsed.items()
            if isinstance(cols, dict)
        }

    @staticmethod
    def _parse_json_map(raw: str, field: str) -> dict[str, str]:
        if not (raw or "").strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} não é JSON válido: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{field} precisa ser um objeto JSON")
        return {str(k): str(v) for k, v in parsed.items()}

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

    # ------------------------------------------------------------------ #
    # Conteúdo dos cards
    #
    # ATENÇÃO: este texto é usado APENAS para gerar o embedding. O contexto que
    # vai ao LLM é remontado do Neo4j pelo Neo4jSubgraphRetriever, que lê só
    # `meta["node_id"]` e `meta["card_type"]` do card. Portanto o texto pode ser
    # otimizado livremente para recuperação sem afetar a resposta.
    #
    # v1 mantido intacto para reprodutibilidade dos runs já coletados.
    # ------------------------------------------------------------------ #

    def _table_content_v1(self, table, table_columns, table_relationships) -> str:
        lines = [
            "Tipo: tabela",
            f"FQN: {table['node_id']}",
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
                "- " + self._relationship_summary(rel, node_id=table["node_id"])
                for rel in table_relationships
            )
        return "\n".join(lines)

    def _column_content_v1(self, column, column_relationships) -> str:
        lines = [
            "Tipo: coluna",
            f"FQN: {column['node_id']}",
            f"Tabela: {column['table_node_id']}",
            f"Nome: {column['name']}",
            f"Tipo de dado: {column['data_type_display'] or column['data_type']}",
            f"Descrição: {column.get('description') or ''}",
            "Chave primária: " + ("sim" if column["is_primary_key"] else "não"),
        ]
        if column_relationships:
            lines.append("Relacionamentos:")
            lines.extend(
                "- " + self._relationship_summary(rel, node_id=column["node_id"])
                for rel in column_relationships
            )
        return "\n".join(lines)

    def _table_content_v2(self, table, table_columns, table_relationships) -> str:
        """Card de tabela denso em significado.

        Sai do texto: `Tipo:` (está em meta.card_type), `FQN:` (meta.node_id),
        `Schema:` (meta + grafo) e o tipo de dado de cada coluna (grafo). Entram
        domínio de negócio e grão. Os NOMES de coluna ficam — são o que casa
        lexicalmente quando a pergunta cita uma coluna.
        """
        name = table["name"]
        display = table.get("display_name")
        head = f"{name} — {display}" if display and display != name else name
        lines = [head]
        if table.get("description"):
            lines.append(str(table["description"]).strip())
        lines.append(f"Domínio: {self._domain(table.get('schema', ''))}")
        if self.product_line != "off" and table.get("data_product_label"):
            product = f"Produto: {table['data_product_label']}"
            if self.product_line == "full" and table.get("data_product_description"):
                product += f" — {table['data_product_description']}"
            lines.append(product)
        # O grão NÃO entra no card. Medido em 2026-08-12: incluí-lo piora MRR
        # (0,875 -> 0,792) e infla o prompt, porque "um registro por apf" se
        # repete em dezenas de tabelas e não discrimina nada. O valor do grão é
        # gerar SQL correto, e o card é só embedding — ele nunca chega ao LLM.
        # Por isso o grão é serializado pelo Neo4jSubgraphRetriever, no contexto.
        if table_columns:
            lines.append(
                "Colunas: " + ", ".join(c["name"] for c in table_columns)
            )
        vocab = self._vocab_line(table["node_id"])
        if vocab:
            lines.append(vocab)
        # Só v2: o v1 fica congelado para reprodutibilidade dos runs antigos.
        if table_relationships and self.embed_relationships:
            lines.append("Relacionamentos:")
            lines.extend(
                "- " + self._relationship_summary(rel, node_id=table["node_id"])
                for rel in table_relationships
            )
        return "\n".join(lines)

    def _column_content_v2(self, column, column_relationships, table=None) -> str:
        """Card de coluna denso em significado.

        O caminho completo saía DUAS vezes (`FQN:` e `Tabela:`) e sozinho valia
        metade do card. Aqui fica só o nome curto da tabela — que é o que casa —
        e o tipo de dado migra para o payload/grafo. `Chave primária: não`, que
        aparecia em todos os 1.215 cards, some quando é falso.
        """
        table_name = str(column["table_node_id"]).rsplit(".", 1)[-1]
        description = str(column.get("description") or "").strip()
        head = f"{column['name']} — {description}" if description else column["name"]
        lines = [head, f"Tabela: {table_name}"]
        schema_fqn = str(column["table_node_id"]).rsplit(".", 1)[0]
        lines.append(f"Domínio: {self._domain(schema_fqn)}")
        # Só o rótulo: a descrição do produto repetida em milhares de cards de
        # coluna diluiria o que é próprio de cada coluna.
        if self.product_line != "off" and (table or {}).get("data_product_label"):
            lines.append(f"Produto: {table['data_product_label']}")
        # Grão fora do card, pelo mesmo motivo do card de tabela — ver acima.
        if column.get("is_primary_key"):
            lines.append("Chave primária")
        if column_relationships and self.embed_relationships:
            lines.append("Relacionamentos:")
            lines.extend(
                "- " + self._relationship_summary(rel, node_id=column["node_id"])
                for rel in column_relationships
            )
        return "\n".join(lines)

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
            content = (
                self._table_content_v2(table, table_columns, table_relationships)
                if self.card_schema_version == "v2"
                else self._table_content_v1(table, table_columns, table_relationships)
            )
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
            # Produto no payload (não no texto): é o que o roteamento de
            # sementes por produto lê, sem reingestão quando ele mudar.
            meta["data_product"] = table.get("data_product") or ""
            meta["data_product_label"] = table.get("data_product_label") or ""
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

        table_by_fqn = {table["node_id"]: table for table in tables}
        for column in sorted(columns, key=lambda item: item["node_id"]):
            column_fqn = column["node_id"]
            parent = table_by_fqn.get(column["table_node_id"]) or {}
            column_relationships = sorted(
                relationships_by_column.get(column_fqn, []),
                key=lambda item: item["relationship_id"],
            )
            content = (
                self._column_content_v2(column, column_relationships, parent)
                if self.card_schema_version == "v2"
                else self._column_content_v1(column, column_relationships)
            )
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
            meta["data_product"] = parent.get("data_product") or ""
            meta["data_product_label"] = parent.get("data_product_label") or ""
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

"""
Neo4jSubgraphRetriever — o retriever CANÔNICO do GraphRAG (a "costura").

Recebe os Documents que o QdrantHybridRetriever achou (cada um com `node_id` no
meta) e, a partir desses nós-semente, percorre o grafo por Cypher para montar o
SUBGRAFO DE SCHEMA relevante: as tabelas dos cards, suas colunas e as tabelas
joináveis por `SEMANTIC_JOIN`. Devolve um Document por tabela (schema
serializado) + a lista de JOINs disponíveis, pronto pro PromptBuilder montar o
SQL.

    query → (hybrid Qdrant) → seeds → [ESTE componente] → subgrafo → LLM

Nota de correção (ADR-0011/CLAUDE.md, gap nº1): o MCid **não declara FK**
(FOREIGN_KEY=0, LINEAGE=0). A joinabilidade vem de `SEMANTIC_JOIN` (coluna↔coluna),
extraído das Custom Properties do OpenMetadata. Por isso a travessia é por
`SEMANTIC_JOIN` (+ `HAS_COLUMN`), não por FK — que era o motivo de o passo de
grafo não expandir nada antes.
"""
from typing import Any, Dict, List

from loguru import logger
from haystack import Document, component

from rag.components.graphstore.neo4j import Neo4jGraphStore

# A partir dos node_ids semente, sobe pra Tabela (se o seed for Column), expande
# 1 hop por SEMANTIC_JOIN e retorna cada tabela com suas colunas (nome, tipo, PK),
# em ordem de posição ordinal. Usa `node_id` (não existe prop `fqn` no nó) e
# `is_primary_key` (não `is_pk`).
_TABLES_CYPHER = """
UNWIND $ids AS nid
MATCH (seed {node_id: nid})
OPTIONAL MATCH (seed)<-[:HAS_COLUMN]-(parent:Table)
WITH collect(DISTINCT CASE WHEN seed:Table THEN seed.node_id END)
   + collect(DISTINCT parent.node_id) AS seed_ids
UNWIND seed_ids AS sid
MATCH (t:Table {node_id: sid})
OPTIONAL MATCH (t)-[:HAS_COLUMN]->(:Column)-[:SEMANTIC_JOIN]-(:Column)<-[:HAS_COLUMN]-(nbr:Table)
WITH seed_ids, collect(DISTINCT nbr.node_id) AS nbr_ids
WITH seed_ids + nbr_ids AS all_ids
UNWIND all_ids AS tid
WITH DISTINCT tid WHERE tid IS NOT NULL
MATCH (tab:Table {node_id: tid})-[:HAS_COLUMN]->(col:Column)
WITH tab, col ORDER BY col.ordinal_position
RETURN tab.node_id AS table_fqn, tab.name AS table_name, tab.schema AS schema_fqn,
       tab.description AS table_desc,
       collect({
         name: col.name,
         type: coalesce(col.data_type_display, col.data_type),
         is_pk: col.is_primary_key,
         description: col.description
       }) AS columns
ORDER BY table_fqn
"""

# JOINs (SEMANTIC_JOIN) entre pares de tabelas do subgrafo. `sql_join_eligible`
# separa o JOIN direto (join_rule=exact) do que exige normalização documentada
# fora do SQL automático (normalized_exact). `t.node_id < t2.node_id` deduplica
# a aresta não-direcionada.
_JOINS_CYPHER = """
UNWIND $tids AS tid
MATCH (t:Table {node_id: tid})-[:HAS_COLUMN]->(c1:Column)
      -[r:SEMANTIC_JOIN]-(c2:Column)<-[:HAS_COLUMN]-(t2:Table)
WHERE t2.node_id IN $tids AND t.node_id < t2.node_id
RETURN DISTINCT
       t.node_id AS left_table_fqn, t.name AS left_table, c1.name AS left_col,
       t2.node_id AS right_table_fqn, t2.name AS right_table, c2.name AS right_col,
       r.join_rule AS join_rule, r.semantic_key AS semantic_key,
       r.sql_join_eligible AS sql_join_eligible, r.cardinality AS cardinality,
       r.confidence AS confidence
ORDER BY left_table, right_table
"""


def _schema_short(schema_fqn: str | None, table_fqn: str) -> str:
    """Último segmento do FQN do schema (ex.: 'Cidades.cidades.conjuntura_gold'
    → 'conjuntura_gold'). É o schema físico usado no SQL do Postgres."""
    if schema_fqn:
        return schema_fqn.rsplit(".", 1)[-1]
    # Fallback pelo FQN da tabela: service.database.schema.table
    parts = table_fqn.split(".")
    return parts[-2] if len(parts) >= 2 else ""


@component
class Neo4jSubgraphRetriever:
    def __init__(self, graph_store: Neo4jGraphStore, max_seeds: int = 10):
        self.store = graph_store
        self.max_seeds = max_seeds

    @component.output_types(documents=List[Document], joins=List[Dict[str, Any]])
    def run(self, documents: List[Document]) -> dict:
        seed_ids: List[str] = []
        for doc in documents or []:
            nid = (doc.meta or {}).get("node_id")
            if nid and nid not in seed_ids:
                seed_ids.append(nid)
        seed_ids = seed_ids[: self.max_seeds]

        if not seed_ids:
            logger.warning("Neo4jSubgraphRetriever: nenhum node_id nos documents-semente")
            return {"documents": [], "joins": []}

        rows = self.store.execute_read(_TABLES_CYPHER, {"ids": seed_ids})
        table_ids = [row["table_fqn"] for row in rows]
        joins = self.store.execute_read(_JOINS_CYPHER, {"tids": table_ids}) if table_ids else []

        logger.info(
            "Neo4jSubgraphRetriever: {} seeds → {} tabelas, {} joins no subgrafo",
            len(seed_ids), len(rows), len(joins),
        )

        out: List[Document] = []
        for row in rows:
            schema = _schema_short(row.get("schema_fqn"), row["table_fqn"])
            sql_name = f"{schema}.{row['table_name']}" if schema else row["table_name"]
            columns = [c for c in (row.get("columns") or []) if c and c.get("name")]
            out.append(Document(
                content=self._serialize_table(row, sql_name, columns),
                meta={
                    "node_id": row["table_fqn"],
                    "fqn": row["table_fqn"],
                    "label": "Table",
                    "schema": schema,
                    "table": row["table_name"],
                    "sql_name": sql_name,
                    "description": row.get("table_desc") or "",
                    # Dicionário de dados por coluna (nome, tipo, PK, descrição),
                    # consumido pelo SqlAnswerBuilder para a tabela de referência.
                    "columns": [
                        {
                            "name": c["name"],
                            "type": c.get("type") or "",
                            "is_pk": bool(c.get("is_pk")),
                            "description": c.get("description") or "",
                        }
                        for c in columns
                    ],
                },
            ))
        return {"documents": out, "joins": joins}

    @staticmethod
    def _serialize_table(row: dict, sql_name: str, columns: list) -> str:
        """DDL-compacto pro contexto do LLM. Mostra o nome físico (schema.table)
        que deve ir no SQL e, entre parênteses, o FQN completo do catálogo."""
        lines = [f"Tabela: {sql_name}   (FQN: {row['table_fqn']})"]
        if row.get("table_desc"):
            lines.append(f"  Descrição: {row['table_desc']}")
        lines.append("  Colunas:")
        for col in columns:
            pk = " [PK]" if col.get("is_pk") else ""
            desc = f"  — {col['description']}" if col.get("description") else ""
            lines.append(f"    - {col['name']} {col.get('type', '')}{pk}{desc}")
        return "\n".join(lines)

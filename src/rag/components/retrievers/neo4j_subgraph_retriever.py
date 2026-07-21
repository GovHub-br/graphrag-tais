"""
Neo4jSubgraphRetriever — o retriever CANÔNICO do GraphRAG (a "costura").

Recebe os Documents que o QdrantHybridRetriever achou (cada um com `node_id` no
meta) e, a partir desses nós-semente, percorre o grafo por Cypher para montar o
SUBGRAFO DE SCHEMA relevante: as tabelas dos cards, suas colunas, tabelas
joináveis por FK e vizinhos de lineage. Devolve um Document por tabela, com o
schema serializado, pronto pro PromptBuilder montar o SQL.

    query → (hybrid Qdrant) → seeds → [ESTE componente] → subgrafo → LLM
"""
from typing import List

from loguru import logger
from haystack import Document, component

from rag.components.graphstore.neo4j import Neo4jGraphStore

# Cypher tunável: dado os node_ids semente, sobe pra Tabela (se o seed for
# Column/Metric), expande 1 hop por FK e por LINEAGE, e retorna cada tabela com
# suas colunas (nome, tipo, PK, FK destino).
_EXPAND_CYPHER = """
UNWIND $ids AS nid
MATCH (seed {node_id: nid})
OPTIONAL MATCH (seed)<-[:HAS_COLUMN]-(t_from_col:Table)
WITH collect(DISTINCT CASE WHEN seed:Table THEN seed END)
   + collect(DISTINCT t_from_col) AS seeds
UNWIND seeds AS t
WITH DISTINCT t WHERE t IS NOT NULL
OPTIONAL MATCH (t)-[:HAS_COLUMN]->(:Column)-[:FOREIGN_KEY]-(:Column)<-[:HAS_COLUMN]-(t_fk:Table)
OPTIONAL MATCH (t)-[:LINEAGE]-(t_lin:Table)
WITH collect(DISTINCT t) + collect(DISTINCT t_fk) + collect(DISTINCT t_lin) AS tables
UNWIND tables AS tab
WITH DISTINCT tab WHERE tab IS NOT NULL
MATCH (tab)-[:HAS_COLUMN]->(col:Column)
OPTIONAL MATCH (col)-[:FOREIGN_KEY]->(ref:Column)
RETURN tab.fqn AS table_fqn, tab.name AS table_name, tab.description AS table_desc,
       collect({name: col.name, type: col.data_type, is_pk: col.is_pk, fk_to: ref.fqn}) AS columns
"""


@component
class Neo4jSubgraphRetriever:
    def __init__(self, graph_store: Neo4jGraphStore, max_seeds: int = 10):
        self.store = graph_store
        self.max_seeds = max_seeds

    @component.output_types(documents=List[Document])
    def run(self, documents: List[Document]) -> dict:
        seed_ids = []
        for doc in documents or []:
            nid = (doc.meta or {}).get("node_id")
            if nid and nid not in seed_ids:
                seed_ids.append(nid)
        seed_ids = seed_ids[: self.max_seeds]

        if not seed_ids:
            logger.warning("Neo4jSubgraphRetriever: nenhum node_id nos documents-semente")
            return {"documents": []}

        rows = self.store.execute_read(_EXPAND_CYPHER, {"ids": seed_ids})
        logger.info("Neo4jSubgraphRetriever: %d seeds → %d tabelas no subgrafo",
                    len(seed_ids), len(rows))

        out: List[Document] = []
        for row in rows:
            out.append(Document(
                content=self._serialize_table(row),
                meta={"node_id": row["table_fqn"], "label": "Table", "fqn": row["table_fqn"]},
            ))
        return {"documents": out}

    @staticmethod
    def _serialize_table(row: dict) -> str:
        """Serializa a tabela num formato tipo-DDL compacto pro contexto do LLM."""
        lines = [f"Tabela: {row['table_fqn']}"]
        if row.get("table_desc"):
            lines.append(f"  Descrição: {row['table_desc']}")
        lines.append("  Colunas:")
        for col in row.get("columns", []):
            if not col or not col.get("name"):
                continue
            pk = " [PK]" if col.get("is_pk") else ""
            fk = f" [FK -> {col['fk_to']}]" if col.get("fk_to") else ""
            lines.append(f"    - {col['name']} {col.get('type', '')}{pk}{fk}")
        return "\n".join(lines)

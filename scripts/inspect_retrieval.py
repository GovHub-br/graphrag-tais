#!/usr/bin/env python3
"""Inspeciona o retrieval (sementes Qdrant + subgrafo Neo4j) de uma pergunta,
SEM passar pelo LLM. Só para depuração — não faz parte da pipeline.

Uso (dentro do container hayhooks, que tem as libs e o .env):

    docker exec hayhooks python /app/src/../scripts/inspect_retrieval.py "sua pergunta"

ou, com o caminho montado em ./src:

    docker exec hayhooks python -c "import sys; sys.path.insert(0,'/app/src'); \
        exec(open('/app/scripts/inspect_retrieval.py').read())" "sua pergunta"
"""
import sys

sys.path.insert(0, "/app/src")

from rag.components.embedders.ollama_text_embedder import OllamaTextEmbedderFactory
from rag.components.embedders.fastembed_sparse_text_embedder import (
    FastembedSparseTextEmbedderFactory,
)
from rag.components.vectorstore.qdrant import QdrantStoreFactory
from rag.components.retrievers.qdrant_hybrid_retriever import QdrantHybridRetrieverFactory
from rag.components.graphstore.neo4j import Neo4jGraphStoreFactory
from rag.components.retrievers.neo4j_subgraph_retriever import Neo4jSubgraphRetriever
from rag.settings.configs import settings


def inspect(question: str, top_k: int = 5) -> None:
    dense = OllamaTextEmbedderFactory().build()
    sparse = FastembedSparseTextEmbedderFactory().build()
    sparse.warm_up()
    store = QdrantStoreFactory(index=settings.QDRANT_COLLECTION).build()
    retriever = QdrantHybridRetrieverFactory(store, top_k=top_k).build()
    subgraph = Neo4jSubgraphRetriever(graph_store=Neo4jGraphStoreFactory().build())

    de = dense.run(text=question)["embedding"]
    se = sparse.run(text=question)["sparse_embedding"]
    seeds = retriever.run(query_embedding=de, query_sparse_embedding=se)["documents"]

    print("=" * 80)
    print("PERGUNTA:", question)
    print(f"--- SEMENTES Qdrant (top-{top_k}, híbrido denso+BM25) ---")
    for d in seeds:
        m = d.meta or {}
        print(f"  {round(d.score or 0, 4):<8} [{m.get('card_type', '?'):6}] {m.get('node_id')}")

    out = subgraph.run(documents=seeds)
    print(f"--- SUBGRAFO Neo4j: {len(out['documents'])} tabelas, {len(out['joins'])} joins ---")
    for doc in out["documents"]:
        print(f"  tabela: {doc.meta['sql_name']} ({len(doc.meta.get('columns', []))} colunas)")
    for j in out["joins"]:
        elig = "DIRETO" if j["sql_join_eligible"] else "requer-normalização"
        print(f"  join: {j['left_table']}.{j['left_col']} = {j['right_table']}.{j['right_col']}  [{elig}]")


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "Qual o saldo da caderneta de poupança do SBPE ao longo do tempo?"
    inspect(q)

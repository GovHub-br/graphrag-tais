"""
Pipeline de query (GraphRAG de metadados → SQL).

    pergunta ─┬─► dense_text_embedder  ─┐
              └─► sparse_text_embedder ─┴─► qdrant_hybrid_retriever (acha os cards)
                                              └─► neo4j_subgraph_retriever (expande o grafo)
                                                     └─► prompt_builder ─► llm ─► SQL

Passo 1 (Qdrant, híbrido denso+esparso): acha os cards de schema mais relevantes
para a pergunta. Passo 2 (Neo4j, Cypher): a partir desses nós, percorre FK e
lineage e monta o subgrafo de schema. Passo 3 (LLM): escreve o SQL.
"""
from haystack import Pipeline
from haystack.components.builders import PromptBuilder

from rag.settings.configs import settings
from rag.components.embedders.ollama_text_embedder import OllamaTextEmbedderFactory
from rag.components.embedders.fastembed_sparse_text_embedder import FastembedSparseTextEmbedderFactory
from rag.components.vectorstore.qdrant import QdrantStoreFactory
from rag.components.retrievers.qdrant_hybrid_retriever import QdrantHybridRetrieverFactory
from rag.components.graphstore.neo4j import Neo4jGraphStoreFactory
from rag.components.retrievers.neo4j_subgraph_retriever import Neo4jSubgraphRetriever
from rag.components.generators.ollama_generator import OllamaGeneratorFactory

PROMPT_TEMPLATE = """
Você é um especialista em SQL. Gere uma consulta SQL que responda à pergunta do
usuário usando APENAS as tabelas e colunas do schema abaixo. Respeite as chaves
estrangeiras (FK) indicadas para montar os JOINs. Se faltar informação no schema,
diga o que falta em vez de inventar tabelas/colunas.

# Schema disponível (subgrafo recuperado)
{% for doc in documents %}
{{ doc.content }}
{% endfor %}

# Pergunta
{{ query }}

# SQL
"""


def build_query_pipeline(collection: str | None = None, top_k: int = 5) -> Pipeline:
    pipeline = Pipeline()

    # Embeddings da query (denso + esparso)
    pipeline.add_component("dense_text_embedder", OllamaTextEmbedderFactory().build())
    pipeline.add_component("sparse_text_embedder", FastembedSparseTextEmbedderFactory().build())

    # Passo 1: busca híbrida no Qdrant
    qdrant_store = QdrantStoreFactory(index=collection or settings.QDRANT_COLLECTION).build()
    pipeline.add_component("hybrid_retriever", QdrantHybridRetrieverFactory(qdrant_store, top_k=top_k).build())

    # Passo 2: expansão do subgrafo no Neo4j
    graph_store = Neo4jGraphStoreFactory().build()
    pipeline.add_component("subgraph_retriever", Neo4jSubgraphRetriever(graph_store=graph_store))

    # Passo 3: geração do SQL
    pipeline.add_component("prompt_builder", PromptBuilder(template=PROMPT_TEMPLATE))
    pipeline.add_component("llm", OllamaGeneratorFactory().build())

    # Conexões
    pipeline.connect("dense_text_embedder.embedding", "hybrid_retriever.query_embedding")
    pipeline.connect("sparse_text_embedder.sparse_embedding", "hybrid_retriever.query_sparse_embedding")
    pipeline.connect("hybrid_retriever.documents", "subgraph_retriever.documents")
    pipeline.connect("subgraph_retriever.documents", "prompt_builder.documents")
    pipeline.connect("prompt_builder.prompt", "llm.prompt")

    return pipeline

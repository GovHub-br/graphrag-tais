"""
Pipeline de query (GraphRAG de metadados → SQL).

    pergunta ─┬─► dense_text_embedder  ─┐
              └─► sparse_text_embedder ─┴─► hybrid_retriever (acha os cards)
                                              └─► subgraph_retriever (expande o grafo)
                                                     ├─► prompt_builder ─► llm ─┐
                                                     └───────────────────► answer_builder

Passo 1 (Qdrant, híbrido denso+esparso): acha os cards de schema mais relevantes
para a pergunta. Passo 2 (Neo4j, Cypher): a partir desses nós, percorre
`SEMANTIC_JOIN` e monta o subgrafo de schema (tabelas, colunas e JOINs). Passo 3
(LLM OpenAI, saída estruturada): interpreta a pergunta (IR mínima) e escreve o
SQL. Passo 4 (answer_builder): formaliza explicação + SQL + referências.

Modelos: embeddings iguais aos da ingestão (Ollama gte-qwen2 denso + BM25
esparso — ADR-0009 "plano B" on-prem). Só a GERAÇÃO de SQL usa a OpenAI. A IR é
gerada junto do SQL numa única chamada estruturada: entrega o ponto de controle
inspecionável do ADR-0006/0007 sem uma chamada de LLM extra nem reescrever o
roteamento da recuperação (fica para quando houver volume real de perguntas).
"""
from haystack import Pipeline
from haystack.components.builders import ChatPromptBuilder
from haystack.dataclasses import ChatMessage

from rag import prompts
from rag.settings.configs import settings
from rag.components.embedders.ollama_text_embedder import OllamaTextEmbedderFactory
from rag.components.embedders.fastembed_sparse_text_embedder import FastembedSparseTextEmbedderFactory
from rag.components.vectorstore.qdrant import QdrantStoreFactory
from rag.components.retrievers.qdrant_hybrid_retriever import QdrantHybridRetrieverFactory
from rag.components.graphstore.neo4j import Neo4jGraphStoreFactory
from rag.components.retrievers.neo4j_subgraph_retriever import Neo4jSubgraphRetriever
from rag.components.generators.openai_generator import OpenAIChatGeneratorFactory
from rag.components.answer.sql_answer_builder import SqlAnswerBuilder

# Os prompts da geração de SQL vivem em `src/rag/prompts/query_system.md` e
# `query_user.md` (este é template Jinja, renderizado pelo ChatPromptBuilder).
SYSTEM_PROMPT = prompts.load("query_system")
USER_PROMPT = prompts.load("query_user")


def build_query_pipeline(collection: str | None = None, top_k: int = 5) -> Pipeline:
    pipeline = Pipeline()

    # Embeddings da query (iguais à ingestão: denso Ollama + esparso BM25)
    pipeline.add_component("dense_text_embedder", OllamaTextEmbedderFactory().build())
    pipeline.add_component("sparse_text_embedder", FastembedSparseTextEmbedderFactory().build())

    # Passo 1: busca híbrida no Qdrant
    qdrant_store = QdrantStoreFactory(index=collection or settings.QDRANT_COLLECTION).build()
    pipeline.add_component("hybrid_retriever", QdrantHybridRetrieverFactory(qdrant_store, top_k=top_k).build())

    # Passo 1b (opcional): rerank cross-encoder do pool antes do corte.
    if settings.RERANK_ENABLED:
        from rag.components.retrievers.reranker import FastembedReranker
        pipeline.add_component("reranker", FastembedReranker(top_k=top_k))

    # Passo 2: expansão do subgrafo no Neo4j (SEMANTIC_JOIN + HAS_COLUMN)
    graph_store = Neo4jGraphStoreFactory().build()
    pipeline.add_component("subgraph_retriever", Neo4jSubgraphRetriever(graph_store=graph_store))

    # Passo 3: geração do SQL (OpenAI, saída estruturada em JSON)
    prompt_builder = ChatPromptBuilder(
        template=[
            ChatMessage.from_system(SYSTEM_PROMPT),
            ChatMessage.from_user(USER_PROMPT),
        ],
        required_variables=["documents", "query"],
        variables=["documents", "joins", "query"],
    )
    pipeline.add_component("prompt_builder", prompt_builder)
    pipeline.add_component("llm", OpenAIChatGeneratorFactory().build())

    # Passo 4: formalização da resposta (explicação + SQL + referências)
    pipeline.add_component("answer_builder", SqlAnswerBuilder())

    # Conexões
    pipeline.connect("dense_text_embedder.embedding", "hybrid_retriever.query_embedding")
    pipeline.connect("sparse_text_embedder.sparse_embedding", "hybrid_retriever.query_sparse_embedding")
    if settings.RERANK_ENABLED:
        pipeline.connect("hybrid_retriever.documents", "reranker.documents")
        pipeline.connect("reranker.documents", "subgraph_retriever.documents")
    else:
        pipeline.connect("hybrid_retriever.documents", "subgraph_retriever.documents")
    pipeline.connect("subgraph_retriever.documents", "prompt_builder.documents")
    pipeline.connect("subgraph_retriever.joins", "prompt_builder.joins")
    pipeline.connect("prompt_builder.prompt", "llm.messages")
    pipeline.connect("llm.replies", "answer_builder.replies")
    pipeline.connect("subgraph_retriever.documents", "answer_builder.documents")
    pipeline.connect("subgraph_retriever.joins", "answer_builder.joins")

    return pipeline

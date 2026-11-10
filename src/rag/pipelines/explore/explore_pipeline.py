"""Pipeline de EXPLORAÇÃO — a Persona 1 (ADR-0014).

    pergunta ─┬─► dense_text_embedder  ─┐
              └─► sparse_text_embedder ─┴─► hybrid_retriever (top_k amplo)
                                              └─► catalog_retriever (Neo4j)
                                                     ├─► prompt_builder ─► llm ─┐
                                                     └───────────────────► answer_builder

Mesma primeira metade da pipeline da Persona 2 (embedding híbrido + Qdrant), com
duas diferenças deliberadas e um componente de grafo próprio:

- **`top_k` amplo** (`EXPLORE_TOP_K`, default 15 contra 5): quem explora precisa
  de cobertura, não de precisão sobre uma tabela.
- **`Neo4jCatalogRetriever`** no lugar do `Neo4jSubgraphRetriever`: expansão
  ampla (todos os vizinhos de 1 hop), ordenação por relevância e panorama de
  domínios por Cypher agregado.

Nada aqui toca a Persona 2: os componentes compartilhados são instanciados por
factory, com parâmetros próprios, e nenhum default de `settings` é alterado.
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
from rag.components.retrievers.neo4j_catalog_retriever import Neo4jCatalogRetriever
from rag.components.generators.openai_generator import OpenAIChatGeneratorFactory
from rag.components.answer.explore_answer_builder import ExploreAnswerBuilder

# Os prompts do guia do catálogo vivem em `src/rag/prompts/explore_system.md` e
# `explore_user.md` (este é template Jinja, renderizado pelo ChatPromptBuilder).
SYSTEM_PROMPT = prompts.load("explore_system")
USER_PROMPT = prompts.load("explore_user")


def build_explore_pipeline(collection: str | None = None, top_k: int | None = None) -> Pipeline:
    pipeline = Pipeline()
    top_k = top_k or settings.EXPLORE_TOP_K

    pipeline.add_component("dense_text_embedder", OllamaTextEmbedderFactory().build())
    pipeline.add_component("sparse_text_embedder", FastembedSparseTextEmbedderFactory().build())

    qdrant_store = QdrantStoreFactory(index=collection or settings.QDRANT_COLLECTION).build()
    pipeline.add_component(
        "hybrid_retriever", QdrantHybridRetrieverFactory(
            qdrant_store, top_k=top_k, route_pool=settings.EXPLORE_PRODUCT_ROUTING).build()
    )

    graph_store = Neo4jGraphStoreFactory().build()
    # Opcional: prioriza o produto citado (mesmo componente da P2, flag própria).
    if settings.EXPLORE_PRODUCT_ROUTING:
        from rag.components.retrievers.product_router import ProductSeedRouter
        pipeline.add_component("product_router", ProductSeedRouter(top_k=top_k))

    pipeline.add_component("catalog_retriever", Neo4jCatalogRetriever(
        graph_store=graph_store,
        scope_id=(settings.OM_SCOPE_ID or None) if settings.SCOPE_FILTER_ENABLED else None,
        max_seeds=top_k,
        max_tables=settings.EXPLORE_MAX_TABLES,
        group_by=settings.EXPLORE_GROUP_BY,
        max_columns=settings.EXPLORE_MAX_COLUMNS,
        max_columns_expansion=settings.EXPLORE_MAX_COLUMNS_EXPANSION,
        expand_neighbors=settings.EXPLORE_EXPAND_NEIGHBORS,
    ))

    pipeline.add_component("prompt_builder", ChatPromptBuilder(
        template=[
            ChatMessage.from_system(SYSTEM_PROMPT),
            ChatMessage.from_user(USER_PROMPT),
        ],
        required_variables=["query"],
        variables=["documents", "joins", "domains", "query"],
    ))
    pipeline.add_component("llm", OpenAIChatGeneratorFactory().build())
    pipeline.add_component("answer_builder", ExploreAnswerBuilder(
        max_questions=settings.EXPLORE_QUESTIONS_PER_TABLE,
        llm_reorder=settings.EXPLORE_LLM_REORDER,
        max_listed_tables=settings.EXPLORE_MAX_LISTED_TABLES,
    ))

    pipeline.connect("dense_text_embedder.embedding", "hybrid_retriever.query_embedding")
    pipeline.connect("sparse_text_embedder.sparse_embedding", "hybrid_retriever.query_sparse_embedding")
    if settings.EXPLORE_PRODUCT_ROUTING:
        pipeline.connect("hybrid_retriever.documents", "product_router.documents")
        pipeline.connect("product_router.documents", "catalog_retriever.documents")
    else:
        pipeline.connect("hybrid_retriever.documents", "catalog_retriever.documents")
    pipeline.connect("catalog_retriever.documents", "prompt_builder.documents")
    pipeline.connect("catalog_retriever.joins", "prompt_builder.joins")
    pipeline.connect("catalog_retriever.domains", "prompt_builder.domains")
    pipeline.connect("prompt_builder.prompt", "llm.messages")
    pipeline.connect("llm.replies", "answer_builder.replies")
    pipeline.connect("catalog_retriever.documents", "answer_builder.documents")
    pipeline.connect("catalog_retriever.joins", "answer_builder.joins")
    pipeline.connect("catalog_retriever.domains", "answer_builder.domains")
    pipeline.connect("catalog_retriever.catalog", "answer_builder.catalog")

    return pipeline

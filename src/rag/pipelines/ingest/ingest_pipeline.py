"""
Pipeline de ingestão (GraphRAG de metadados).

    OpenMetadataSource ─► GraphCardBuilder ─┬─(nodes, relationships)─► Neo4jGraphWriter
                                            │
                                            └─(documents)─► sparse_embedder
                                                              └─► dense_embedder ─► qdrant_writer

O GraphCardBuilder gera, para cada entidade do OpenMetadata: um Document (card,
p/ busca híbrida no Qdrant) e as specs de nó/aresta (p/ o grafo no Neo4j). Cards
são curtos → não há splitter.
"""
from loguru import logger
from haystack import Pipeline

from rag.settings.configs import settings
from rag.components.sources.openmetadata_source import OpenMetadataSource
from rag.components.converters.graph_card_builder import GraphCardBuilder
from rag.components.graphstore.neo4j import Neo4jGraphStoreFactory
from rag.components.writers.neo4j_graph_writer import Neo4jGraphWriter
from rag.components.embedders.fastembed_sparse_document_embedder import FastembedSparseDocumentEmbedderFactory
from rag.components.embedders.ollama_document_embedder import OllamaDocumentEmbedderFactory
from rag.components.vectorstore.qdrant import QdrantStoreFactory
from rag.components.writers.safe_document_writer import SafeDocumentWriter


def build_ingest_pipeline(collection: str | None = None, with_lineage: bool = True) -> Pipeline:
    pipeline = Pipeline()

    # Fonte + transformação em cards/nós/arestas
    pipeline.add_component("om_source", OpenMetadataSource(with_lineage=with_lineage))
    pipeline.add_component("card_builder", GraphCardBuilder())

    # Grafo (Neo4j)
    graph_store = Neo4jGraphStoreFactory().build()
    pipeline.add_component("neo4j_writer", Neo4jGraphWriter(graph_store=graph_store))

    # Vetor híbrido (Qdrant): esparso → denso → writer
    qdrant_store = QdrantStoreFactory(index=collection or settings.QDRANT_COLLECTION).build()
    pipeline.add_component("sparse_embedder", FastembedSparseDocumentEmbedderFactory().build())
    pipeline.add_component("dense_embedder", OllamaDocumentEmbedderFactory().build())
    pipeline.add_component("qdrant_writer", SafeDocumentWriter(document_store=qdrant_store))

    # Conexões
    pipeline.connect("om_source.entities", "card_builder.entities")
    pipeline.connect("card_builder.nodes", "neo4j_writer.nodes")
    pipeline.connect("card_builder.relationships", "neo4j_writer.relationships")
    pipeline.connect("card_builder.documents", "sparse_embedder.documents")
    pipeline.connect("sparse_embedder.documents", "dense_embedder.documents")
    pipeline.connect("dense_embedder.documents", "qdrant_writer.documents")

    logger.info("Ingest pipeline (GraphRAG de metadados) construída")
    return pipeline

"""
Pipeline de ingestão (GraphRAG de metadados).

    OpenMetadataSource ─► MetadataRecordBuilder ─► GraphCardBuilder
                                                     │
                  ┌──────────────────────────────────┴───────────────┐
                  │                                                  │
                  └─► Neo4jGraphWriter ─► GraphWriteGate ◄─ documents
                                                │
                                                └─► sparse ─► dense ─► Qdrant

O gate impede embeddings e escrita no Qdrant antes da confirmação do Neo4j.
"""
from loguru import logger
from haystack import Pipeline

from rag.settings.configs import settings
from rag.components.sources.openmetadata_source import OpenMetadataSource
from rag.components.converters.metadata_record_builder import MetadataRecordBuilder
from rag.components.converters.graph_card_builder import GraphCardBuilder
from rag.components.graphstore.neo4j import Neo4jGraphStoreFactory
from rag.components.writers.neo4j_graph_writer import Neo4jGraphWriter
from rag.components.writers.graph_write_gate import GraphWriteGate
from rag.components.embedders.fastembed_sparse_document_embedder import FastembedSparseDocumentEmbedderFactory
from rag.components.embedders.ollama_document_embedder import OllamaDocumentEmbedderFactory
from rag.components.vectorstore.qdrant import QdrantStoreFactory
from rag.components.writers.safe_document_writer import SafeDocumentWriter


def build_ingest_pipeline() -> Pipeline:
    pipeline = Pipeline()

    # Fonte completa -> contrato canônico -> projeções.
    pipeline.add_component("om_source", OpenMetadataSource())
    pipeline.add_component("record_builder", MetadataRecordBuilder())
    pipeline.add_component("card_builder", GraphCardBuilder())

    # Grafo primeiro.
    graph_store = Neo4jGraphStoreFactory().build()
    pipeline.add_component("neo4j_writer", Neo4jGraphWriter(graph_store=graph_store))
    pipeline.add_component("graph_write_gate", GraphWriteGate())

    # Vetor híbrido: esparso -> denso -> upsert confirmado.
    qdrant_store = QdrantStoreFactory(
        index=settings.QDRANT_COLLECTION,
        wait_result_from_api=True,
    ).build()
    pipeline.add_component("sparse_embedder", FastembedSparseDocumentEmbedderFactory().build())
    pipeline.add_component("dense_embedder", OllamaDocumentEmbedderFactory().build())
    pipeline.add_component("qdrant_writer", SafeDocumentWriter(document_store=qdrant_store))

    pipeline.connect("om_source.snapshot", "record_builder.snapshot")
    pipeline.connect("record_builder.records", "card_builder.records")
    pipeline.connect("card_builder.nodes", "neo4j_writer.nodes")
    pipeline.connect("card_builder.relationships", "neo4j_writer.relationships")
    pipeline.connect("card_builder.documents", "graph_write_gate.documents")
    pipeline.connect("neo4j_writer.nodes_written", "graph_write_gate.nodes_written")
    pipeline.connect(
        "neo4j_writer.relationships_written",
        "graph_write_gate.relationships_written",
    )
    pipeline.connect("graph_write_gate.documents", "sparse_embedder.documents")
    pipeline.connect("sparse_embedder.documents", "dense_embedder.documents")
    pipeline.connect("dense_embedder.documents", "qdrant_writer.documents")

    logger.info("Ingest pipeline (GraphRAG de metadados) construída")
    return pipeline

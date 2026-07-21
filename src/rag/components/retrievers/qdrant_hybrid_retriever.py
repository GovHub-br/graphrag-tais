from haystack_integrations.components.retrievers.qdrant import QdrantHybridRetriever
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore

from rag.components.base import ComponentFactory


class QdrantHybridRetrieverFactory(ComponentFactory):
    """
    Factory para o QdrantHybridRetriever (denso + esparso).

    O store precisa ter sido criado com `use_sparse_embeddings=True`
    (QDRANT_USE_SPARSE). No `run`, o retriever espera DOIS inputs:
      - query_embedding        (denso, do OllamaTextEmbedder)
      - query_sparse_embedding (esparso, do FastembedSparseTextEmbedder)
    e funde os dois rankings internamente (RRF).
    """

    def __init__(self, document_store: QdrantDocumentStore, top_k: int = 5):
        self.document_store = document_store
        self.top_k = top_k

    def build(self) -> QdrantHybridRetriever:
        return QdrantHybridRetriever(
            document_store=self.document_store,
            top_k=self.top_k,
        )

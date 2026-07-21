from haystack_integrations.components.retrievers.qdrant import QdrantEmbeddingRetriever
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore

from rag.components.base import ComponentFactory


class QdrantRetrieverFactory(ComponentFactory):
    """
    Factory para o QdrantEmbeddingRetriever. Recebe um store já construído
    (via QdrantStoreFactory) e o top_k desejado.
    """

    def __init__(self, document_store: QdrantDocumentStore, top_k: int = 5):
        self.document_store = document_store
        self.top_k = top_k

    def build(self) -> QdrantEmbeddingRetriever:
        return QdrantEmbeddingRetriever(
            document_store=self.document_store,
            top_k=self.top_k,
        )

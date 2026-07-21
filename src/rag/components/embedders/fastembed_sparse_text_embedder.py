from haystack_integrations.components.embedders.fastembed import FastembedSparseTextEmbedder

from rag.components.base import ComponentFactory
from rag.settings.configs import settings


class FastembedSparseTextEmbedderFactory(ComponentFactory):
    """
    Factory para o embedder ESPARSO (SPLADE via fastembed) da query.
    Par do FastembedSparseDocumentEmbedderFactory, mas para uma única string.
    Alimenta o `query_sparse_embedding` do QdrantHybridRetriever.
    """

    def __init__(self, model: str | None = None):
        self.model = model or settings.QDRANT_SPARSE_MODEL

    def build(self) -> FastembedSparseTextEmbedder:
        return FastembedSparseTextEmbedder(model=self.model)

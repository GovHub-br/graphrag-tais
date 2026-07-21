from haystack_integrations.components.embedders.fastembed import FastembedSparseDocumentEmbedder

from rag.components.base import ComponentFactory
from rag.settings.configs import settings


class FastembedSparseDocumentEmbedderFactory(ComponentFactory):
    """
    Factory para o embedder ESPARSO (SPLADE via fastembed) usado na ingestão.

    Roda local em CPU (baixa o modelo na 1ª vez), sem servidor. Complementa o
    embedder denso do Ollama: o esparso casa termos/identificadores exatos
    (ex.: `user_id`, `revenue`), o denso casa semântica.
    """

    def __init__(self, model: str | None = None, batch_size: int | None = None):
        self.model = model or settings.QDRANT_SPARSE_MODEL
        self.batch_size = batch_size or settings.EMBED_BATCH_SIZE

    def build(self) -> FastembedSparseDocumentEmbedder:
        return FastembedSparseDocumentEmbedder(
            model=self.model,
            batch_size=self.batch_size,
        )

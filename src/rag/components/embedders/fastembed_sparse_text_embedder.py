from haystack import component
from haystack.dataclasses import SparseEmbedding
from haystack_integrations.components.embedders.fastembed import FastembedSparseTextEmbedder

from rag.components.base import ComponentFactory
from rag.settings.configs import settings


class FastembedSparseTextEmbedderFactory(ComponentFactory):
    """
    Factory para o embedder ESPARSO via FastEmbed da query.
    Par do FastembedSparseDocumentEmbedderFactory, mas para uma única string.
    Alimenta o `query_sparse_embedding` do QdrantHybridRetriever.
    """

    def __init__(self, model: str | None = None, language: str | None = None):
        self.model = model or settings.QDRANT_SPARSE_MODEL
        self.language = language or settings.SPARSE_LANGUAGE

    def build(self):
        # O default do fastembed é `english`: stemmer e stopwords inglesas sobre
        # corpus e perguntas em PT-BR. O wrapper do Haystack não expõe
        # `language`, então vai por `model_kwargs`. Precisa casar com o lado do
        # documento, senão os tokens não caem no mesmo espaço.
        inner = FastembedSparseTextEmbedder(
            model=self.model,
            model_kwargs={"language": self.language},
        )
        if not settings.SPARSE_FOLD_ACCENTS:
            return inner
        from rag.components.embedders.text_normalization import fold

        @component
        class _FoldingSparseTextEmbedder:
            """Dobra acento/underscore antes do BM25 (ver text_normalization)."""

            def __init__(self, inner):
                self._inner = inner

            def warm_up(self):
                warm = getattr(self._inner, "warm_up", None)
                if callable(warm):
                    warm()

            @component.output_types(sparse_embedding=SparseEmbedding)
            def run(self, text: str):
                return self._inner.run(text=fold(text))

        return _FoldingSparseTextEmbedder(inner)

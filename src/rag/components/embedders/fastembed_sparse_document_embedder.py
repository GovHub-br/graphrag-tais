from typing import List

from haystack import Document, component
from haystack_integrations.components.embedders.fastembed import FastembedSparseDocumentEmbedder

from rag.components.base import ComponentFactory
from rag.settings.configs import settings


class FastembedSparseDocumentEmbedderFactory(ComponentFactory):
    """
    Factory para o embedder ESPARSO via FastEmbed usado na ingestão.

    Roda local em CPU (baixa o modelo na 1ª vez), sem servidor. Complementa o
    embedder denso do Ollama: o esparso casa termos/identificadores exatos
    (ex.: `user_id`, `revenue`), o denso casa semântica.
    """

    def __init__(
        self,
        model: str | None = None,
        batch_size: int | None = None,
        language: str | None = None,
    ):
        self.model = model or settings.QDRANT_SPARSE_MODEL
        self.batch_size = batch_size or settings.EMBED_BATCH_SIZE
        self.language = language or settings.SPARSE_LANGUAGE

    def build(self):
        # Par do embedder de query: o idioma TEM de ser o mesmo nos dois lados,
        # senão "habitacionais" e "habitacional" viram tokens distintos e o
        # casamento lexical se perde. Trocar o idioma exige reindexar o esparso.
        inner = FastembedSparseDocumentEmbedder(
            model=self.model,
            batch_size=self.batch_size,
            model_kwargs={"language": self.language},
        )
        if not settings.SPARSE_FOLD_ACCENTS:
            return inner
        from rag.components.embedders.text_normalization import fold

        @component
        class _FoldingSparseDocumentEmbedder:
            """Dobra acento/underscore antes do BM25, preservando o card original.

            Embute cópias normalizadas e devolve o vetor esparso nos documentos
            ORIGINAIS — o texto indexado no Qdrant continua acentuado e legível.
            """

            def __init__(self, inner):
                self._inner = inner

            def warm_up(self):
                warm = getattr(self._inner, "warm_up", None)
                if callable(warm):
                    warm()

            @component.output_types(documents=List[Document])
            def run(self, documents: List[Document]):
                copies = [
                    Document(id=d.id, content=fold(d.content or ""), meta=dict(d.meta or {}))
                    for d in documents
                ]
                embedded = self._inner.run(documents=copies)["documents"]
                by_id = {d.id: d.sparse_embedding for d in embedded}
                for d in documents:
                    d.sparse_embedding = by_id.get(d.id)
                return {"documents": documents}

        return _FoldingSparseDocumentEmbedder(inner)

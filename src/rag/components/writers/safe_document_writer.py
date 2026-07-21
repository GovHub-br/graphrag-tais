from typing import Optional

from haystack import Document, component
from haystack.components.writers import DocumentWriter
from haystack.document_stores.types import DuplicatePolicy


@component
class SafeDocumentWriter(DocumentWriter):
    """
    Versão do DocumentWriter que engole kwargs inesperados vindos de outros
    componentes (evita quebrar o pipeline por um output extra). Funciona com
    qualquer DocumentStore Haystack (Qdrant, Neo4j, etc.).
    """

    @component.output_types(documents_written=int)
    def run(self, documents: list[Document], policy: Optional[DuplicatePolicy] = None, **kwargs):
        if kwargs:
            print(f"SafeDocumentWriter received unexpected kwargs: {kwargs}")
        return DocumentWriter.run(self, documents=documents, policy=policy)

    @component.output_types(documents_written=int)
    async def run_async(self, documents: list[Document], policy: Optional[DuplicatePolicy] = None, **kwargs):
        if kwargs:
            print(f"SafeDocumentWriter received unexpected kwargs in async: {kwargs}")
        return await DocumentWriter.run_async(self, documents=documents, policy=policy)

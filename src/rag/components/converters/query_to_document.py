from typing import List

from haystack import Document, component


@component
class QueryToDocument:
    """
    Componente custom simples: transforma uma string de query em um Document.
    Útil quando você quer reaproveitar o DocumentEmbedder/Splitter no caminho da
    query (ex.: textos longos) em vez do TextEmbedder.
    """

    @component.output_types(documents=List[Document])
    def run(self, query: str):
        return {"documents": [Document(content=query)]}

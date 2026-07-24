"""Barreira que garante Neo4j antes de embeddings/Qdrant."""

from __future__ import annotations

from typing import Any, Dict, List

from haystack import Document, component


@component
class GraphWriteGate:
    """Libera os cards apenas após as contagens confirmadas do grafo."""

    @component.output_types(documents=List[Document])
    def run(
        self,
        documents: List[Document],
        nodes_written: int,
        relationships_written: int,
    ) -> Dict[str, Any]:
        if nodes_written < 0 or relationships_written < 0:
            raise RuntimeError("Contagens inválidas retornadas pelo Neo4j")
        return {"documents": documents}

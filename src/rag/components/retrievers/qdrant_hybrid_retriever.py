"""Retriever híbrido do Qdrant com escopo e corte determinístico (Fase 1).

Envolve o `QdrantHybridRetriever` da integração (denso + esparso, fusão RRF) para
adicionar duas garantias que o audit de retrieval pediu:

- **Filtro de scope** (ADR-0011): toda consulta filtra `meta.scope_id`, evitando
  vazamento entre escopos quando a collection tiver mais de um.
- **Corte determinístico**: recupera um POOL maior e reordena por `(-score,
  node_id)` antes de cortar o top-k. Isso elimina a instabilidade do RRF na
  fronteira do top-5 (empates em 0.5/0.333/0.25), que trocava o subgrafo entre
  runs idênticos.

Mantém a mesma interface de `run(query_embedding, query_sparse_embedding, top_k)`,
então pipeline e collector de avaliação continuam chamando igual.
"""
from typing import List, Optional

from haystack import Document, component
from haystack.dataclasses import SparseEmbedding
from haystack_integrations.components.retrievers.qdrant import QdrantHybridRetriever
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore

from rag.components.base import ComponentFactory
from rag.settings.configs import settings


def _scope_filter() -> Optional[dict]:
    if not settings.SCOPE_FILTER_ENABLED or not settings.OM_SCOPE_ID:
        return None
    return {"field": "meta.scope_id", "operator": "==", "value": settings.OM_SCOPE_ID}


@component
class DeterministicHybridRetriever:
    """Corte estável do híbrido: pool → ordena por (-score, node_id) → top_k."""

    def __init__(
        self,
        document_store: QdrantDocumentStore,
        top_k: int = 5,
        pool_size: int | None = None,
        route_pool: bool = False,
    ):
        self.top_k = top_k
        # True quando um roteador (por produto) reordena o pool depois daqui.
        # Parâmetro, não setting global: a P1 e a P2 dividem este componente.
        self.route_pool = route_pool
        self.pool_size = pool_size or settings.RETRIEVAL_POOL_SIZE
        self._inner = QdrantHybridRetriever(
            document_store=document_store,
            filters=_scope_filter(),
            top_k=self.pool_size,
        )

    @component.output_types(documents=List[Document])
    def run(
        self,
        query_embedding: List[float],
        query_sparse_embedding: SparseEmbedding,
        top_k: Optional[int] = None,
    ) -> dict:
        # Com rerank ligado o corte deixa de ser aqui: o pool inteiro segue para
        # o cross-encoder, que é quem sabe comparar pergunta e card. Cortar antes
        # jogaria fora justamente os candidatos que o rerank existe para resgatar.
        # Idem com o roteamento por produto: ele reordena o pool inteiro.
        widen = settings.RERANK_ENABLED or self.route_pool
        k = self.pool_size if widen else (top_k or self.top_k)
        pool = max(self.pool_size, k)
        docs = self._inner.run(
            query_embedding=query_embedding,
            query_sparse_embedding=query_sparse_embedding,
            top_k=pool,
        )["documents"]
        # Desempate estável: score desc, depois node_id asc (determinístico).
        docs.sort(key=lambda d: (-(d.score if d.score is not None else 0.0),
                                 (d.meta or {}).get("node_id", d.id or "")))
        return {"documents": docs[:k]}


class QdrantHybridRetrieverFactory(ComponentFactory):
    """
    Factory do retriever híbrido (denso + esparso) com scope + determinismo.

    O store precisa ter sido criado com `use_sparse_embeddings=True`
    (QDRANT_USE_SPARSE). No `run`, espera:
      - query_embedding        (denso, do OllamaTextEmbedder)
      - query_sparse_embedding (esparso, do FastembedSparseTextEmbedder)
    """

    def __init__(self, document_store: QdrantDocumentStore, top_k: int = 5,
                 route_pool: bool = False):
        self.document_store = document_store
        self.top_k = top_k
        self.route_pool = route_pool

    def build(self) -> DeterministicHybridRetriever:
        return DeterministicHybridRetriever(
            document_store=self.document_store,
            top_k=self.top_k,
            route_pool=self.route_pool,
        )

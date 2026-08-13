"""Reranker cross-encoder sobre o pool do híbrido (plano, item 3.1).

O `DeterministicHybridRetriever` já recupera um POOL (default 30) e hoje o
descarta ordenando por score do RRF e cortando o top-k. O RRF só sabe *posição*
em cada canal — ele nunca compara a pergunta com o card. Um cross-encoder lê os
dois juntos e pontua relevância de verdade, que é o que separa candidatos quase
idênticos (o caso FAR × FDS, schemas espelhados).

Roda local via fastembed (CPU, ONNX), sem servidor e sem sair da infra — mantém
a restrição on-prem dos ADRs 0004/0009 para o caminho da query.

Desligado por default: ligar com `RERANK_ENABLED=true`. O ganho depende do
modelo e do corpus, então precisa passar pelo gate antes de virar padrão.
"""
from typing import List, Optional

from haystack import Document, component
from loguru import logger

from rag.settings.configs import settings


@component
class FastembedReranker:
    """Reordena o pool por relevância pergunta↔card e corta o top-k."""

    def __init__(
        self,
        model: str | None = None,
        top_k: int | None = None,
    ) -> None:
        self.model = model or settings.RERANK_MODEL
        self.top_k = top_k or settings.RERANK_TOP_K
        self._encoder = None

    def warm_up(self) -> None:
        if self._encoder is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            logger.info("Carregando reranker {}", self.model)
            self._encoder = TextCrossEncoder(model_name=self.model)

    @component.output_types(documents=List[Document])
    def run(
        self,
        documents: List[Document],
        query: str,
        top_k: Optional[int] = None,
    ) -> dict:
        k = top_k or self.top_k
        docs = list(documents or [])
        if not docs or not (query or "").strip():
            return {"documents": docs[:k]}

        self.warm_up()
        try:
            scores = list(self._encoder.rerank(query, [d.content or "" for d in docs]))
        except Exception as exc:  # noqa: BLE001 — reranker é aditivo
            logger.warning("Reranker falhou ({}); mantendo ordem do híbrido", exc)
            return {"documents": docs[:k]}

        for doc, score in zip(docs, scores):
            doc.score = float(score)
        # Mesmo desempate estável do corte do híbrido: score desc, node_id asc.
        docs.sort(key=lambda d: (-(d.score if d.score is not None else 0.0),
                                 (d.meta or {}).get("node_id", d.id or "")))
        logger.info("Reranker: {} candidatos → top {}", len(docs), k)
        return {"documents": docs[:k]}

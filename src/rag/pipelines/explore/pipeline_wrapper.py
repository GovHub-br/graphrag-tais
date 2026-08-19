"""
Wrapper de API (Hayhooks) da PERSONA 1 — explorar o catálogo (ADR-0014).

O diretório da pipeline vira o **nome do modelo** no OpenWebUI: este é o modelo
**`Explorar`**, ao lado do **`Indicador`** (Persona 2).
A escolha é explícita no dropdown — sem classificador de intenção, que erraria em
silêncio e hoje não teria como ser medido.

Expõe:

- `run_api`            : JSON estruturado — panorama, tabelas, perguntas
                         sugeridas, SQL de amostra e lacunas.
- `run_chat_completion`: contrato OpenAI-compat para o OpenWebUI.

Diferente da Persona 2, aqui **não há camada de entrega**: sem execução de SQL,
sem gráfico e sem relatório. A Persona 1 pediu bases, SQL e explicação — e a
resposta sai em uma única chamada de LLM, o que a torna mais barata e mais
rápida que a da Persona 2 mesmo com um contexto maior.
"""
import sys

from hayhooks import BasePipelineWrapper, get_last_user_message
from loguru import logger

from rag.pipelines.explore.explore_pipeline import build_explore_pipeline
from rag.settings.configs import settings

_OUTPUTS = ["answer_builder", "catalog_retriever"]


class PipelineWrapper(BasePipelineWrapper):

    def setup(self) -> None:
        self.pipeline = build_explore_pipeline()
        logger.info("Wrapper de Exploração (catálogo → panorama) inicializado")

    def _run(self, query: str, top_k: int | None = None) -> dict:
        if not self.pipeline:
            raise RuntimeError("Pipeline não inicializado no setup.")
        inputs = {
            "dense_text_embedder": {"text": query},
            "sparse_text_embedder": {"text": query},
            "hybrid_retriever": {"top_k": top_k or settings.EXPLORE_TOP_K},
            "catalog_retriever": {"query": query},
            "prompt_builder": {"query": query},
            "answer_builder": {"query": query},
        }
        return self.pipeline.run(inputs, include_outputs_from=_OUTPUTS)

    def run_api(self, query: str, top_k: int | None = None) -> dict:
        """Contrato JSON: panorama do catálogo para a pergunta (Persona 1)."""
        logger.info(">>> [explore/api] '{}...' (top_k={})", query[:60], top_k)
        try:
            result = self._run(query, top_k=top_k)
            return result["answer_builder"]["answer"]
        except Exception as e:  # noqa: BLE001 — devolve erro amigável na API
            logger.error("!!! [explore/api] ERRO: {}", e)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return {"status": "erro", "mensagem": str(e)}

    def run_chat_completion(self, model: str, messages: list, body: dict) -> str:
        """Contrato OpenAI-compat para o OpenWebUI."""
        query = get_last_user_message(messages) or ""
        logger.info(">>> [explore/chat] model={} '{}...'", model, query[:60])
        if not query.strip():
            return (
                "Me diga um assunto e eu mostro o que existe sobre ele no catálogo "
                "— por exemplo: _\"o que vocês têm sobre financiamento habitacional?\"_"
            )
        try:
            return self._run(query)["answer_builder"]["markdown"]
        except Exception as e:  # noqa: BLE001
            logger.error("!!! [explore/chat] ERRO: {}", e)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return f"**Erro ao explorar o catálogo:** {e}"

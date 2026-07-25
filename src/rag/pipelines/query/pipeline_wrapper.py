"""
Wrapper de API (Hayhooks) para a query (GraphRAG de metadados → SQL).

Expõe dois contratos sobre a MESMA pipeline:

- `run_api`            : JSON estruturado — {"query": "..."} → explicação, SQL,
                        IR e referências (bases/tabelas/colunas). Bom para
                        integrações e depuração (Swagger em /docs).
- `run_chat_completion`: contrato OpenAI-compat que o **OpenWebUI** consome —
                        devolve a resposta já formatada em Markdown legível.
                        É isso que faz o modelo "query" aparecer no chat.
"""
import sys

from hayhooks import BasePipelineWrapper, get_last_user_message
from loguru import logger

from rag.pipelines.query.query_pipeline import build_query_pipeline

_OUTPUTS = ["answer_builder"]


class PipelineWrapper(BasePipelineWrapper):

    def setup(self) -> None:
        self.pipeline = build_query_pipeline()
        logger.info("Wrapper de Query (GraphRAG → SQL) inicializado")

    def _run(self, query: str, top_k: int = 5) -> dict:
        """Roda a pipeline e devolve os outputs relevantes (answer + subgrafo)."""
        if not self.pipeline:
            raise RuntimeError("Pipeline não inicializado no setup.")
        result = self.pipeline.run(
            {
                "dense_text_embedder": {"text": query},
                "sparse_text_embedder": {"text": query},
                "hybrid_retriever": {"top_k": top_k},
                "prompt_builder": {"query": query},
            },
            include_outputs_from=_OUTPUTS,
        )
        return result

    def run_api(self, query: str, top_k: int = 5) -> dict:
        """Contrato JSON: resposta estruturada (explicação, SQL, IR, referências)."""
        logger.info(">>> [query/api] '{}...' (top_k={})", query[:60], top_k)
        try:
            result = self._run(query, top_k=top_k)
            return result["answer_builder"]["answer"]
        except Exception as e:  # noqa: BLE001 — devolve erro amigável na API
            logger.error("!!! [query/api] ERRO: {}", e)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return {"status": "erro", "mensagem": str(e)}

    def run_chat_completion(self, model: str, messages: list, body: dict) -> str:
        """Contrato OpenAI-compat para o OpenWebUI: devolve Markdown legível.

        Sem streaming no v1 — a resposta é pós-processada (JSON → Markdown) e
        entregue inteira, priorizando uma apresentação organizada e legível.
        """
        query = get_last_user_message(messages) or ""
        logger.info(">>> [query/chat] model={} '{}...'", model, query[:60])
        if not query.strip():
            return "Envie uma pergunta sobre os dados para eu gerar o SQL."
        try:
            result = self._run(query)
            return result["answer_builder"]["markdown"]
        except Exception as e:  # noqa: BLE001
            logger.error("!!! [query/chat] ERRO: {}", e)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return f"**Erro ao processar a pergunta:** {e}"

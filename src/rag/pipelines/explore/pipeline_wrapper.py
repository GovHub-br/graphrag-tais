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

Diferente da Persona 2, aqui não há camada de entrega (sem gráfico, sem
relatório) e a resposta sai em **uma única chamada de LLM**. A exceção é a
**amostra de dados** (`EXPLORE_EXECUTE_SAMPLE`): o `sample_sql` é executado
read-only para a pessoa VER linhas reais e baixar o CSV. O caminho normal dessa
execução não gasta LLM nenhum (postgres-mcp direto); só a autocorreção gasta, e
só quando o banco recusa a consulta do modelo.
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
        # Amostra de dados: instanciada sob demanda para não acoplar o boot do
        # wrapper à disponibilidade do postgres-mcp / MinIO.
        self._sample_runner = None
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
        """Contrato JSON: panorama do catálogo para a pergunta (Persona 1).

        Traz `sample` junto: é o que permite o eval medir se o `sample_sql`
        sugerido de fato RODA — sinal bem mais forte que "cita colunas que
        existem"."""
        logger.info(">>> [explore/api] '{}...' (top_k={})", query[:60], top_k)
        try:
            result = self._run(query, top_k=top_k)
            answer = result["answer_builder"]["answer"]
            self._attach_sample(answer)
            return answer
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
            result = self._run(query)
            answer = result["answer_builder"]["answer"]
            markdown = result["answer_builder"]["markdown"]
            if not self._attach_sample(answer):
                return markdown
            # Re-renderiza com a amostra no lugar do bloco de "SQL de amostra".
            joins = result.get("catalog_retriever", {}).get("joins", [])
            return self.pipeline.get_component("answer_builder").render(answer, joins)
        except Exception as e:  # noqa: BLE001
            logger.error("!!! [explore/chat] ERRO: {}", e)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return f"**Erro ao explorar o catálogo:** {e}"

    # ------------------------------------------------------------------ #
    def _attach_sample(self, answer: dict) -> bool:
        """Executa o SQL de amostra e pendura o resultado em `answer["sample"]`.

        Aditivo e defensivo, como o relatório da Persona 2: se o banco, o MCP ou
        o MinIO estiverem fora, a resposta sai como sempre saiu — com o SQL de
        amostra, sem as linhas. Nunca levanta."""
        if not settings.EXPLORE_EXECUTE_SAMPLE:
            return False
        try:
            if self._sample_runner is None:
                from rag.components.samples import SampleRunner

                self._sample_runner = SampleRunner()
            sample = self._sample_runner.run(answer)
        except Exception as e:  # noqa: BLE001 — amostra é extra, nunca quebra a resposta
            logger.warning("[explore] amostra de dados falhou: {}", e)
            return False
        if not sample.get("ok"):
            logger.info("[explore] sem amostra: {}", sample.get("reason", ""))
            return False
        answer["sample"] = sample
        return True

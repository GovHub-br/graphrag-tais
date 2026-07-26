"""
Wrapper de API (Hayhooks) para a query (GraphRAG de metadados → SQL → gráfico).

Expõe dois contratos:

- `run_api`            : JSON estruturado — {"query": "..."} → explicação, SQL,
                        IR e referências (bases/tabelas/colunas). É o **motor**
                        puro (não executa SQL) — rápido e reusável.
- `run_chat_completion`: contrato OpenAI-compat que o **OpenWebUI** consome.
                        Compõe o motor com a **camada de entrega** (ADR-0013):
                        executa o SQL via postgres-mcp (autocorreção) e embute o
                        **gráfico** no Markdown. Aditivo — se a execução/gráfico
                        falharem, ainda devolve o texto do motor.
"""
import sys

from hayhooks import BasePipelineWrapper, get_last_user_message
from loguru import logger

from rag.pipelines.query.query_pipeline import build_query_pipeline
from rag.pipelines.delivery.delivery_pipeline import build_delivery_pipeline

_MOTOR_OUTPUTS = ["answer_builder", "subgraph_retriever"]
_DELIVERY_OUTPUTS = ["mcp_executor", "chart_renderer"]


class PipelineWrapper(BasePipelineWrapper):

    def setup(self) -> None:
        self.pipeline = build_query_pipeline()
        self.delivery = build_delivery_pipeline()
        logger.info("Wrapper de Query (GraphRAG → SQL → gráfico) inicializado")

    def _run(self, query: str, top_k: int = 5) -> dict:
        """Roda o motor e devolve os outputs relevantes (answer + subgrafo)."""
        if not self.pipeline:
            raise RuntimeError("Pipeline não inicializado no setup.")
        return self.pipeline.run(
            {
                "dense_text_embedder": {"text": query},
                "sparse_text_embedder": {"text": query},
                "hybrid_retriever": {"top_k": top_k},
                "prompt_builder": {"query": query},
            },
            include_outputs_from=_MOTOR_OUTPUTS,
        )

    def run_api(self, query: str, top_k: int = 5) -> dict:
        """Contrato JSON: resposta estruturada (só o motor, sem executar SQL)."""
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
        """Contrato OpenAI-compat para o OpenWebUI: texto do motor + gráfico."""
        query = get_last_user_message(messages) or ""
        logger.info(">>> [query/chat] model={} '{}...'", model, query[:60])
        if not query.strip():
            return "Envie uma pergunta sobre os dados para eu gerar o SQL."
        try:
            result = self._run(query)
            answer = result["answer_builder"]["answer"]
            markdown = result["answer_builder"]["markdown"]
            docs = result.get("subgraph_retriever", {}).get("documents", [])
            return self._with_delivery(query, answer, markdown, docs)
        except Exception as e:  # noqa: BLE001
            logger.error("!!! [query/chat] ERRO: {}", e)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return f"**Erro ao processar a pergunta:** {e}"

    # ------------------------------------------------------------------ #
    def _with_delivery(self, query: str, answer: dict, markdown: str, docs: list) -> str:
        """Executa o SQL (via MCP) e anexa o gráfico ao Markdown do motor.
        Nunca levanta: se a entrega falhar, devolve só o texto."""
        sql = (answer or {}).get("sql", "")
        if not sql.strip():
            return markdown  # sem SQL (ex.: fora de domínio) → nada a executar

        schema_context = "\n\n".join(getattr(d, "content", "") for d in docs)
        try:
            out = self.delivery.run(
                {
                    "mcp_executor": {"sql": sql, "query": query, "schema_context": schema_context},
                    "chart_spec": {"query": query, "ir": answer.get("ir", {})},
                },
                include_outputs_from=_DELIVERY_OUTPUTS,
            )
        except Exception as e:  # noqa: BLE001 — gráfico é aditivo
            logger.warning("[query/chat] entrega/gráfico falhou: {}", e)
            return markdown

        execu = out.get("mcp_executor", {})
        image = out.get("chart_renderer", {}).get("image", "")
        parts = [markdown]

        if image:
            parts.append("### Gráfico\n\n![gráfico](" + image + ")")

        sql_final = execu.get("sql_final", "")
        if execu.get("exec_ok") and sql_final and sql_final.strip() != sql.strip():
            parts.append(
                "> O SQL foi **ajustado na execução** para rodar:\n```sql\n"
                + sql_final.strip() + "\n```"
            )
        elif not execu.get("exec_ok") and execu.get("error"):
            parts.append(
                "> _Não consegui executar o SQL para gerar o gráfico "
                f"({execu['error']})._"
            )
        return "\n\n".join(parts)

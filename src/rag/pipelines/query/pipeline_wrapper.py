"""
Wrapper de API (Hayhooks) para a query (GraphRAG de metadados → SQL → gráfico).

Expõe dois contratos:

- `run_api`            : JSON estruturado — {"query": "..."} → explicação, SQL,
                        IR e referências (bases/tabelas/colunas). É o **motor**
                        puro (não executa SQL) — rápido e reusável.
- `run_chat_completion`: contrato OpenAI-compat que o **OpenWebUI** consome.
                        Compõe o motor com a **camada de entrega** (ADR-0013):
                        executa o SQL via postgres-mcp (autocorreção), escreve o
                        parágrafo **"Resultado"** sobre o que voltou (ADR-0010
                        item c) e embute o **gráfico** no Markdown. Aditivo — se
                        a execução, a explicação ou o gráfico falharem, ainda
                        devolve o texto do motor.
"""
import sys
from datetime import datetime

from hayhooks import BasePipelineWrapper, get_last_user_message
from loguru import logger

from rag.components.answer.sql_answer_builder import render_markdown
from rag.guardrails import guardrail_verdict
from rag.pipelines.query.query_pipeline import build_query_pipeline
from rag.pipelines.delivery.delivery_pipeline import build_delivery_pipeline
from rag.settings.configs import settings

_MOTOR_OUTPUTS = ["answer_builder", "subgraph_retriever"]
_DELIVERY_OUTPUTS = ["mcp_executor", "chart_renderer", "result_explainer"]


class PipelineWrapper(BasePipelineWrapper):

    def setup(self) -> None:
        self.pipeline = build_query_pipeline()
        self.delivery = build_delivery_pipeline()
        # Ferramentas de relatório: instanciadas sob demanda (lazy) para não
        # acoplar o boot do wrapper à disponibilidade do MinIO.
        self._report_builder = None
        self._minio = None
        logger.info("Wrapper de Query (GraphRAG → SQL → resultado + gráfico) inicializado")

    def _run(self, query: str, top_k: int = 5) -> dict:
        """Roda o motor e devolve os outputs relevantes (answer + subgrafo)."""
        if not self.pipeline:
            raise RuntimeError("Pipeline não inicializado no setup.")
        inputs = {
            "dense_text_embedder": {"text": query},
            "sparse_text_embedder": {"text": query},
            "hybrid_retriever": {"top_k": top_k},
            "subgraph_retriever": {"query": query},
            "prompt_builder": {"query": query},
        }
        # Com rerank ligado, o corte do top-k migra do híbrido para o reranker.
        if "reranker" in self.pipeline.graph.nodes:
            inputs["reranker"] = {"query": query, "top_k": top_k}
        return self.pipeline.run(inputs, include_outputs_from=_MOTOR_OUTPUTS)

    def run_api(self, query: str, top_k: int = 5) -> dict:
        """Contrato JSON: resposta estruturada (só o motor, sem executar SQL)."""
        logger.info(">>> [query/api] '{}...' (top_k={})", query[:60], top_k)
        verdict = guardrail_verdict(query)
        if verdict:
            logger.info("[query/api] guardrail: {}", verdict.reason)
            return {"status": "recusado", "guardrail": verdict.reason,
                    "mensagem": verdict.message}
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
        # Guardrail ANTES do retrieval (ADR-0016): pedido de dado pessoal,
        # pergunta sobre acesso ao dado e pergunta fora do objetivo do sistema
        # não gastam embedding, grafo, LLM nem execução de SQL.
        verdict = guardrail_verdict(query)
        if verdict:
            logger.info("[query/chat] guardrail: {}", verdict.reason)
            return verdict.message
        try:
            result = self._run(query)
            answer = result["answer_builder"]["answer"]
            markdown = result["answer_builder"]["markdown"]
            docs = result.get("subgraph_retriever", {}).get("documents", [])
            chat_markdown, delivery = self._with_delivery(query, answer, markdown, docs)
            # Relatório .docx montado do `answer` ESTRUTURADO + gráfico (ADR-0010 opção D).
            return self._attach_report(query, answer, delivery, chat_markdown)
        except Exception as e:  # noqa: BLE001
            logger.error("!!! [query/chat] ERRO: {}", e)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return f"**Erro ao processar a pergunta:** {e}"

    # ------------------------------------------------------------------ #
    def _with_delivery(self, query: str, answer: dict, markdown: str, docs: list):
        """Executa o SQL (via MCP), RE-RENDERIZA a resposta com o que a execução
        apurou (o SQL exibido passa a ser o que rodou), anexa o gráfico e devolve
        também os resultados ESTRUTURADOS da entrega (para o relatório).
        Nunca levanta: se a entrega falhar, devolve o texto + entrega vazia.

        Retorna: (markdown_do_chat, delivery) onde
          delivery = {chart_image, result_summary, sql_final, exec_ok, exec_error}."""
        delivery = {"chart_image": "", "result_summary": "", "sql_final": "",
                    "exec_ok": False, "exec_error": ""}
        sql = (answer or {}).get("sql", "")
        if not sql.strip():
            return markdown, delivery  # sem SQL (ex.: fora de domínio)

        schema_context = "\n\n".join(getattr(d, "content", "") for d in docs)
        try:
            out = self.delivery.run(
                {
                    "mcp_executor": {"sql": sql, "query": query, "schema_context": schema_context},
                    "chart_spec": {"query": query, "ir": answer.get("ir", {})},
                    "result_explainer": {"query": query, "ir": answer.get("ir", {})},
                },
                include_outputs_from=_DELIVERY_OUTPUTS,
            )
        except Exception as e:  # noqa: BLE001 — gráfico é aditivo
            logger.warning("[query/chat] entrega/gráfico falhou: {}", e)
            delivery["exec_error"] = f"execução indisponível: {e}"
            return render_markdown(answer, delivery), delivery

        execu = out.get("mcp_executor", {})
        image = out.get("chart_renderer", {}).get("image", "")
        delivery.update(
            chart_image=image,
            result_summary=out.get("result_explainer", {}).get("summary", ""),
            sql_final=execu.get("sql_final", ""),
            exec_ok=bool(execu.get("exec_ok")),
            exec_error=execu.get("error", "") or "",
        )

        # A falha de execução NÃO vai para a resposta (decisão de produto: o
        # usuário leva o SQL, não o diagnóstico) — então ela precisa ficar
        # visível aqui, senão some.
        if not delivery["exec_ok"]:
            logger.warning(
                "[query/chat] SQL não executado ({}) — resposta sai sem gráfico",
                delivery["exec_error"] or "sem detalhe",
            )

        # Re-renderiza AGORA que se sabe o que rodou: o bloco de SQL da resposta
        # passa a ser o SQL executado (e o dicionário de dados, derivado dele).
        # Antes, o Markdown vinha pronto da pipeline — montado antes da execução
        # — e a correção do SQL só aparecia como nota no fim.
        parts = [render_markdown(answer, delivery)]
        if image:
            parts.append("### Gráfico\n\n![gráfico](" + image + ")")
        return "\n\n".join(parts), delivery

    # ------------------------------------------------------------------ #
    def _attach_report(self, query: str, answer: dict, delivery: dict, chat_markdown: str) -> str:
        """Gera o relatório .docx do `answer` ESTRUTURADO + gráfico e anexa o link
        de download. Aditivo e defensivo: se algo falhar, devolve só o texto."""
        if not settings.REPORT_ENABLED:
            return chat_markdown
        # Recusa não vira relatório: não há indicador para documentar (ADR-0016).
        if (answer.get("refusal") or "").strip():
            return chat_markdown
        try:
            from rag.components.report import ReportBuilder
            from rag.components.storage import MinIOUploader

            if self._report_builder is None:
                self._report_builder = ReportBuilder()
            if self._minio is None:
                self._minio = MinIOUploader()

            data = datetime.now().strftime("%d/%m/%Y")
            subtitle = f'Pergunta: "{query.strip()[:140]}"   ·   {data}'
            path = self._report_builder.build_from_answer(
                answer,
                chart_data_uri=delivery.get("chart_image", ""),
                exec_info=delivery,
                subtitle=subtitle,
            )
            url = self._minio.upload(path)
        except Exception as e:  # noqa: BLE001 — relatório é aditivo, nunca quebra o chat
            logger.warning("[query/chat] geração/upload do relatório falhou: {}", e)
            return chat_markdown

        return chat_markdown + f"\n\n---\n\n📥 **[Baixar relatório (.docx)]({url})**"

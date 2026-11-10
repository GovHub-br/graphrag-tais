"""
Wrapper de API (Hayhooks) da JORNADA DO USUÁRIO (ADR-0020) — modelo `jornada`.

No OpenWebUI aparece como **"Assistente"** (nome aplicado por
`scripts/setup_webui_models.sh`; o id continua sendo o nome do diretório). É o
modelo em que a pessoa não escolhe persona:

1. pergunta → a resposta já sai no formato da **População geral**;
2. debaixo dela, **botões** (os follow-ups do OpenWebUI) para as personas
   específicas — técnica, especialista em políticas, gestor — e para "Contar a
   história" e "Baixar";
3. o clique volta como mensagem do usuário e abre a trilha.

Compõe peças que já existem, sem alterá-las: o motor da Persona 2
(`build_query_pipeline`), o `McpSqlExecutor` e a pipeline do modo Explorar. A
regra da jornada mora em `rag.journey`; aqui só se liga uma coisa à outra.

`run_chat_completion` atende também os pedidos de bastidor do OpenWebUI (título,
tags e follow-ups) sem chamar LLM — é a chamada de follow-up que desenha os
botões (`rag.journey.openwebui_tasks`).
"""
import sys
from typing import Any, Dict, Generator, List, Tuple

from hayhooks import BasePipelineWrapper, get_last_user_message
from haystack.dataclasses import ChatMessage
from loguru import logger

from rag import prompts
from rag.guardrails import guardrail_verdict
from rag.journey import marker as marker_mod
from rag.journey import openwebui_tasks, tracks
from rag.journey.catalog_probe import load_index
from rag.journey.publish import Publisher
from rag.journey.service import ABOUT, JourneyService
from rag.journey.state import JourneyStore
from rag.journey.text import strip_emoji
from rag.settings.configs import settings

_MOTOR_OUTPUTS = ["answer_builder", "subgraph_retriever"]
# Tamanho de cada pedaço do stream. O OpenWebUI lê o SSE com o `readline` do
# aiohttp, que recusa linha acima de 128 KB ("Got more than 131072 bytes"). Uma
# resposta com três PNGs em base64 passa disso num chunk só — a da Persona 2,
# com um gráfico, ficava logo abaixo do limite.
_CHUNK = 16_000
_EXPLORE_OUTPUTS = ["answer_builder", "catalog_retriever"]
_MOTOR_TOP_K = 5


# --------------------------------------------------------------------------- #
# Inputs das pipelines reusadas
# --------------------------------------------------------------------------- #
# Os componentes OPCIONAIS (reranker, product_router) só existem no grafo quando
# a flag deles está ligada, e cada um exige `query`. Aqui se monta o mesmo dict
# que os wrappers do Indicador e do Explorar montam, olhando o grafo construído:
# antes a Jornada tinha a sua cópia, e o `product_router` que entrou depois
# derrubava a pipeline com "Missing mandatory input 'query'" — só com a flag
# ligada, então a suíte (que roda nos defaults) não via.
# `tests/test_journey_inputs.py` constrói as pipelines com TODAS as flags e cobra
# que nenhum input obrigatório fique de fora.
def motor_inputs(pipeline: Any, query: str, top_k: int = _MOTOR_TOP_K) -> Dict[str, Dict[str, Any]]:
    nodes = pipeline.graph.nodes
    inputs: Dict[str, Dict[str, Any]] = {
        "dense_text_embedder": {"text": query},
        "sparse_text_embedder": {"text": query},
        "hybrid_retriever": {"top_k": top_k},
        "subgraph_retriever": {"query": query},
        "prompt_builder": {"query": query},
    }
    # Com rerank, o corte do top-k migra do híbrido para o reranker; com o
    # roteamento e sem rerank depois dele, o corte é do roteador.
    if "reranker" in nodes:
        inputs["reranker"] = {"query": query, "top_k": top_k}
    if "product_router" in nodes:
        inputs["product_router"] = {"query": query}
        if "reranker" not in nodes:
            inputs["product_router"]["top_k"] = top_k
    return inputs


def explore_inputs(pipeline: Any, query: str, top_k: int | None = None) -> Dict[str, Dict[str, Any]]:
    k = top_k or settings.EXPLORE_TOP_K
    inputs: Dict[str, Dict[str, Any]] = {
        "dense_text_embedder": {"text": query},
        "sparse_text_embedder": {"text": query},
        "hybrid_retriever": {"top_k": k},
        "catalog_retriever": {"query": query},
        "prompt_builder": {"query": query},
        "answer_builder": {"query": query},
    }
    if "product_router" in pipeline.graph.nodes:
        inputs["product_router"] = {"query": query, "top_k": k}
    return inputs


class PipelineWrapper(BasePipelineWrapper):

    def setup(self) -> None:
        from rag.components.executors.mcp_sql_executor import McpSqlExecutor
        from rag.pipelines.explore.explore_pipeline import build_explore_pipeline
        from rag.pipelines.query.query_pipeline import build_query_pipeline

        self.motor = build_query_pipeline()
        self.explore = build_explore_pipeline()
        self.executor = McpSqlExecutor()
        self._sample_runner = None
        self.service = JourneyService(
            motor=self._run_motor, executor=self._execute, explorer=self._run_explore,
            store=JourneyStore(), phrase=self._phrase,
            catalog=lambda: load_index(settings.OM_SCOPE_ID), publisher=Publisher(),
        )
        logger.info("Wrapper da Jornada (População geral + trilhas por botão) inicializado")

    # ------------------------------------------------------------------ #
    # Colaboradores injetados no serviço
    # ------------------------------------------------------------------ #
    def _run_motor(self, query: str) -> Tuple[Dict[str, Any], List[Any]]:
        out = self.motor.run(motor_inputs(self.motor, query),
                             include_outputs_from=_MOTOR_OUTPUTS)
        return (out["answer_builder"]["answer"],
                out.get("subgraph_retriever", {}).get("documents", []))

    def _execute(self, sql: str, query: str, schema_context: str) -> Dict[str, Any]:
        return self.executor.run(sql=sql, query=query, schema_context=schema_context)

    def _run_explore(self, query: str) -> Tuple[str, Dict[str, Any]]:
        out = self.explore.run(explore_inputs(self.explore, query),
                               include_outputs_from=_EXPLORE_OUTPUTS)
        answer = out["answer_builder"]["answer"]
        self._attach_sample(answer)
        # O Explorar fecha mandando trocar para o modelo Indicador; aqui quem
        # mede é o próprio Assistente, pelos botões. Só o fecho muda — o
        # componente do Explorar segue intocado (re-render com outro next_step).
        if not (answer.get("refusal") or "").strip():
            answer["next_step"] = ("Toque numa das perguntas abaixo e eu calculo o número — "
                                   "ou escreva a sua.")
        joins = out.get("catalog_retriever", {}).get("joins", [])
        markdown = self.explore.get_component("answer_builder").render(answer, joins)
        return markdown, answer

    def _attach_sample(self, answer: dict) -> bool:
        """Mesma amostra de dados reais do modo Explorar (ADR-0015). Aditiva."""
        if not settings.EXPLORE_EXECUTE_SAMPLE or (answer.get("refusal") or "").strip():
            return False
        try:
            if self._sample_runner is None:
                from rag.components.samples import SampleRunner

                self._sample_runner = SampleRunner()
            sample = self._sample_runner.run(answer)
        except Exception as e:  # noqa: BLE001
            logger.warning("[jornada] amostra de dados falhou: {}", e)
            return False
        if not sample.get("ok"):
            return False
        answer["sample"] = sample
        return True

    def _phrase(self, query: str, headline: str, caption: str, digest: str) -> str:
        from rag.components.generators.openai_generator import OpenAIChatGeneratorFactory

        gen = OpenAIChatGeneratorFactory(json_mode=True,
                                         max_tokens=settings.JOURNEY_PLAIN_MAX_TOKENS).build()
        reply = gen.run(messages=[
            ChatMessage.from_system(prompts.load("jornada_frase_system")),
            ChatMessage.from_user(prompts.render("jornada_frase_user", query=query,
                                                 headline=headline, caption=caption,
                                                 digest=digest)),
        ])
        return reply["replies"][0].text

    # ------------------------------------------------------------------ #
    # Contratos do Hayhooks
    # ------------------------------------------------------------------ #
    def run_api(self, query: str, trilha: str = "", ref: str = "") -> dict:
        """Contrato JSON (testes e eval): a resposta da jornada + os botões.

        Sem `trilha`, é uma pergunta nova. Com `trilha` (técnica, política,
        gestor, história, baixar) e o `ref` de uma resposta anterior, abre a
        trilha daquele resultado."""
        try:
            if trilha:
                track = tracks.get(trilha)
                if track is None:
                    return {"status": "erro", "mensagem": f"trilha desconhecida: {trilha}"}
                reply = self.service.track(track, marker_mod.Marker(ref=ref, rota="numero"), query)
            else:
                verdict = guardrail_verdict(query)
                if verdict:
                    return {"status": "recusado", "guardrail": verdict.reason,
                            "mensagem": verdict.message}
                reply = self.service.answer(query)
            return {"status": "sucesso", "markdown": reply.markdown, "ref": reply.marker.ref,
                    "rota": reply.marker.rota, "trilha": reply.marker.trilha,
                    "follow_ups": reply.follow_ups}
        except Exception as e:  # noqa: BLE001
            logger.error("!!! [jornada/api] ERRO: {}", e)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return {"status": "erro", "mensagem": str(e)}

    def run_chat_completion(self, model: str, messages: list, body: dict) -> Generator[str, None, None]:
        # Sem emoji em nada que o Assistente devolve — inclusive recusa do
        # guardrail e mensagens prontas, que não passam pelo `Reply`.
        return _in_chunks(strip_emoji(self._chat(messages)))

    def _chat(self, messages: list) -> str:
        text = get_last_user_message(messages) or ""

        # 1) Bastidor do OpenWebUI: título, tags e — o principal — os botões.
        tarefa = openwebui_tasks.detect(text)
        if tarefa:
            return self._answer_task(tarefa, text)

        logger.info(">>> [jornada/chat] '{}...'", text[:60])
        if not text.strip():
            return "Me faça uma pergunta sobre os dados — por exemplo, *\"quanto foi contratado por estado?\"*."
        try:
            # 2) Clique num botão: abre a trilha sobre o resultado anterior.
            track = tracks.match_track(text)
            if track:
                ultimo = marker_mod.last_in_messages(messages)
                return self.service.track(track, ultimo, _previous_question(messages)).text()

            # 3) Pergunta nova. Guardrail antes de qualquer custo (ADR-0016).
            verdict = guardrail_verdict(text)
            if verdict:
                logger.info("[jornada/chat] guardrail: {}", verdict.reason)
                return ABOUT if verdict.reason == "about_system" else verdict.message
            return self.service.answer(text).text()
        except Exception as e:  # noqa: BLE001
            logger.error("!!! [jornada/chat] ERRO: {}", e)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return f"**Erro ao processar a pergunta:** {e}"

    def _answer_task(self, tarefa: str, text: str) -> str:
        if tarefa == openwebui_tasks.FOLLOW_UPS:
            ultimo = marker_mod.parse(openwebui_tasks.chat_history(text))
            botoes = self.service.follow_ups(ultimo)
            logger.info("[jornada/botoes] {} botão(ões) para ref={}", len(botoes),
                        ultimo.ref if ultimo else "-")
            return openwebui_tasks.json_follow_ups(botoes)
        if tarefa == openwebui_tasks.TITLE:
            return openwebui_tasks.json_title(text)
        if tarefa == openwebui_tasks.TAGS:
            return openwebui_tasks.json_tags()
        return "{}"


def _in_chunks(text: str) -> Generator[str, None, None]:
    for inicio in range(0, len(text), _CHUNK):
        yield text[inicio:inicio + _CHUNK]


def _previous_question(messages: list) -> str:
    """A última pergunta de verdade do usuário (que não foi clique de botão)."""
    for message in reversed(messages or []):
        if message.get("role") != "user":
            continue
        texto = marker_mod.content_of(message).strip()
        if texto and not tracks.match_track(texto) and not openwebui_tasks.detect(texto):
            return texto
    return ""

"""Factory do LLM de geração de SQL via OpenAI (camada de query).

A ingestão e o embedding da query permanecem on-prem (Ollama + BM25); só a
geração de SQL usa a OpenAI, que oferece modelos melhores (ADR-0009 registra a
migração; aqui ela vale apenas para o gerador, não para os embeddings). O que
sai para a nuvem é a pergunta + os metadados do schema recuperado — nunca as
linhas do banco (ADR-0008/0009).

Haystack 3 só expõe o gerador de chat (`OpenAIChatGenerator`); ele recebe uma
lista de `ChatMessage` e devolve `replies`. Saída estruturada é garantida com
`response_format={"type": "json_object"}` nos `generation_kwargs`.
"""
from loguru import logger
from haystack.components.generators.chat import OpenAIChatGenerator
from haystack.utils import Secret

from rag.components.base import ComponentFactory
from rag.settings.configs import settings


class OpenAIChatGeneratorFactory(ComponentFactory):
    """Factory do `OpenAIChatGenerator`. Defaults vêm do settings (.env)."""

    def __init__(
        self,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: int | None = None,
        json_mode: bool = True,
    ):
        self.model = model or settings.OPENAI_CHAT_MODEL
        self.temperature = (
            temperature if temperature is not None else settings.OPENAI_TEMPERATURE
        )
        self.max_tokens = max_tokens or settings.OPENAI_MAX_TOKENS
        self.timeout = timeout or settings.OPENAI_TIMEOUT
        self.json_mode = json_mode

    def build(self) -> OpenAIChatGenerator:
        generation_kwargs: dict = {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.json_mode:
            # Decodificação restrita a JSON válido — o prompt precisa citar "JSON".
            generation_kwargs["response_format"] = {"type": "json_object"}

        # A chave é lida do ambiente (OPENAI_API_KEY já vem do .env no container),
        # evitando embuti-la no componente.
        api_key = Secret.from_env_var("OPENAI_API_KEY")

        logger.info(
            "Building OpenAIChatGenerator: model=%s json_mode=%s",
            self.model, self.json_mode,
        )
        return OpenAIChatGenerator(
            api_key=api_key,
            model=self.model,
            timeout=self.timeout,
            generation_kwargs=generation_kwargs,
        )

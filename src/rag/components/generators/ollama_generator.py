from loguru import logger
from haystack_integrations.components.generators.ollama import OllamaGenerator

from rag.components.base import ComponentFactory
from rag.settings.configs import settings


class OllamaGeneratorFactory(ComponentFactory):
    """
    Factory para o OllamaGenerator (LLM generativa via Ollama).

    Todos os parâmetros de geração (temperature, top_p, num_predict, etc.) vêm
    do settings (.env) via `settings.ollama_generation_kwargs`, com override
    pontual via __init__.
    """

    def __init__(
        self,
        model: str | None = None,
        url: str | None = None,
        timeout: int | None = None,
        generation_kwargs: dict | None = None,
    ):
        self.model = model or settings.OLLAMA_CHAT_MODEL
        self.url = url or settings.OLLAMA_URL
        self.timeout = timeout or settings.OLLAMA_TIMEOUT
        self.generation_kwargs = generation_kwargs or settings.ollama_generation_kwargs

    def build(self) -> OllamaGenerator:
        logger.info(
            "Building OllamaGenerator: model=%s url=%s options=%s",
            self.model, self.url, self.generation_kwargs,
        )
        return OllamaGenerator(
            model=self.model,
            url=self.url,
            timeout=self.timeout,
            generation_kwargs=self.generation_kwargs,
        )

import os

from loguru import logger
from haystack_integrations.components.embedders.ollama import OllamaTextEmbedder

from rag.components.base import ComponentFactory
from rag.settings.configs import settings


class OllamaTextEmbedderFactory(ComponentFactory):
    """
    Factory para o OllamaTextEmbedder (embedding de uma única string de query,
    usado na etapa de retrieval). Lê defaults do settings (.env).
    """

    def __init__(
        self,
        model: str | None = None,
        url: str | None = None,
        timeout: int | None = None,
    ):
        self.model = model or settings.OLLAMA_EMBED_MODEL
        self.url = url or settings.OLLAMA_URL
        self.timeout = timeout or settings.OLLAMA_TIMEOUT

    def build(self):
        if settings.OLLAMA_API_KEY:
            os.environ["OLLAMA_API_KEY"] = settings.OLLAMA_API_KEY
            logger.info("OLLAMA_API_KEY set from settings for text embedder auth")

        embedder = OllamaTextEmbedder(
            model=self.model,
            url=self.url,
            timeout=self.timeout,
        )
        if not settings.QUERY_INSTRUCTION_ENABLED:
            return embedder

        # Modelos instruction-tuned esperam prefixo SÓ na query (documento vai
        # cru), então isto não invalida a collection. Ver InstructedTextEmbedder.
        from rag.components.embedders.instructed_text_embedder import (
            InstructedTextEmbedder,
        )

        logger.info("Query embedder com prefixo de instrução: '{}'",
                    settings.QUERY_INSTRUCTION_TASK[:60])
        return InstructedTextEmbedder(embedder)

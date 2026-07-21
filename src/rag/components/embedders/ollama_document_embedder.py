import os

from loguru import logger
from haystack_integrations.components.embedders.ollama import OllamaDocumentEmbedder

from rag.components.base import ComponentFactory
from rag.settings.configs import settings


class OllamaDocumentEmbedderFactory(ComponentFactory):
    """
    Factory para o OllamaDocumentEmbedder (embedding de listas de Document,
    usado na ingestão). Lê defaults do settings (.env) com override via __init__.
    """

    def __init__(
        self,
        model: str | None = None,
        url: str | None = None,
        timeout: int | None = None,
        batch_size: int | None = None,
    ):
        self.model = model or settings.OLLAMA_EMBED_MODEL
        self.url = url or settings.OLLAMA_URL
        self.timeout = timeout or settings.OLLAMA_TIMEOUT
        self.batch_size = batch_size or settings.EMBED_BATCH_SIZE

    def build(self) -> OllamaDocumentEmbedder:
        if settings.OLLAMA_API_KEY:
            os.environ["OLLAMA_API_KEY"] = settings.OLLAMA_API_KEY
            logger.info("OLLAMA_API_KEY set from settings for document embedder auth")

        return OllamaDocumentEmbedder(
            model=self.model,
            url=self.url,
            timeout=self.timeout,
            batch_size=self.batch_size,
        )

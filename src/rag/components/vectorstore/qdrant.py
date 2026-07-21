from loguru import logger
from haystack.utils.auth import Secret
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore

from rag.components.base import ComponentFactory
from rag.settings.configs import settings


class QdrantStoreFactory(ComponentFactory):
    """
    Factory para o QdrantDocumentStore (vector store). Defaults vêm do
    settings (.env); `index` (collection) é obrigatório por chamada.
    """

    def __init__(
        self,
        index: str,
        recreate_index: bool = False,
        timeout: int | None = None,
        wait_result_from_api: bool = False,
        write_batch_size: int | None = None,
        dim: int | None = None,
        similarity: str | None = None,
        grpc: bool | None = None,
        use_sparse_embeddings: bool | None = None,
    ):
        self.index = index
        self.recreate_index = recreate_index
        self.timeout = timeout or settings.QDRANT_TIMEOUT
        self.wait_result_from_api = wait_result_from_api
        self.write_batch_size = write_batch_size or settings.WRITE_BATCH_SIZE
        self.dim = dim or settings.QDRANT_EMBEDDING_DIM
        self.similarity = similarity or settings.QDRANT_SIMILARITY
        self.grpc = grpc if grpc is not None else settings.QDRANT_GRPC
        self.use_sparse_embeddings = (
            use_sparse_embeddings if use_sparse_embeddings is not None else settings.QDRANT_USE_SPARSE
        )

        if not settings.QDRANT_HOST or not settings.QDRANT_PORT:
            raise ValueError("Missing required Qdrant settings: QDRANT_HOST or QDRANT_PORT")

    def build(self) -> QdrantDocumentStore:
        api_key = Secret.from_token(settings.QDRANT_API_KEY) if settings.QDRANT_API_KEY else None
        url = f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}"

        logger.info(
            "Initializing QdrantDocumentStore: url=%s index=%s dim=%d similarity=%s grpc=%s sparse=%s",
            url, self.index, self.dim, self.similarity, self.grpc, self.use_sparse_embeddings,
        )

        try:
            return QdrantDocumentStore(
                url=url,
                api_key=api_key,
                index=self.index,
                embedding_dim=self.dim,
                similarity=self.similarity,
                prefer_grpc=self.grpc,
                recreate_index=self.recreate_index,
                timeout=self.timeout,
                wait_result_from_api=self.wait_result_from_api,
                write_batch_size=self.write_batch_size,
                use_sparse_embeddings=self.use_sparse_embeddings,
            )
        except Exception as e:
            logger.error("Failed to initialize QdrantDocumentStore: %s", str(e))
            raise

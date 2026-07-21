from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configuração central do projeto. Tudo é parametrizado via variáveis de
    ambiente (.env) e possui default sensato para subir em dev sem fricção.

    Os defaults apontam para os nomes de serviço do docker-compose
    (qdrant, neo4j, redis) e para o Ollama no host.
    """

    # ------------------------------------------------------------------ #
    # Qdrant (vector store)
    # ------------------------------------------------------------------ #
    QDRANT_HOST: str = Field("qdrant", description="Qdrant host")
    QDRANT_PORT: int = Field(6333, description="Qdrant port")
    QDRANT_API_KEY: Optional[str] = Field(None, description="Qdrant API key")
    QDRANT_COLLECTION: str = Field("documents", description="Default collection name")
    QDRANT_GRPC: bool = Field(False, description="Use gRPC protocol")
    QDRANT_EMBEDDING_DIM: int = Field(768, description="Embedding dimension")
    QDRANT_SIMILARITY: str = Field("cosine", description="Similarity metric")
    QDRANT_TIMEOUT: int = Field(30, description="Request timeout in seconds")
    QDRANT_USE_SPARSE: bool = Field(True, description="Enable SPLADE sparse embeddings (retrieval híbrido)")
    QDRANT_SPARSE_MODEL: str = Field(
        "prithivida/Splade_PP_en_v1",
        description="Modelo esparso (SPLADE) do fastembed para o retrieval híbrido",
    )

    # ------------------------------------------------------------------ #
    # Neo4j (knowledge graph + vector index)
    # ------------------------------------------------------------------ #
    NEO4J_URL: str = Field("bolt://neo4j:7687", description="Neo4j Bolt URL")
    NEO4J_USERNAME: str = Field("neo4j", description="Neo4j username")
    NEO4J_PASSWORD: str = Field("password123", description="Neo4j password")
    NEO4J_DATABASE: str = Field("neo4j", description="Neo4j database name")
    NEO4J_INDEX: str = Field("document-embeddings", description="Neo4j vector index name")
    NEO4J_NODE_LABEL: str = Field("Document", description="Label used for Document nodes")
    NEO4J_EMBEDDING_DIM: int = Field(768, description="Embedding dimension for the graph vector index")
    NEO4J_SIMILARITY: str = Field("cosine", description="Vector similarity function")

    # ------------------------------------------------------------------ #
    # Ollama (embeddings + generation)
    # ------------------------------------------------------------------ #
    OLLAMA_URL: str = Field("http://host.docker.internal:11434", description="Ollama base URL")
    OLLAMA_API_KEY: Optional[str] = Field(None, description="Ollama API key (se atrás de proxy)")
    OLLAMA_EMBED_MODEL: str = Field("nomic-embed-text", description="Ollama embedding model name")
    OLLAMA_CHAT_MODEL: str = Field("llama3.1", description="Ollama chat/generation model name")
    OLLAMA_TIMEOUT: int = Field(120, description="Ollama request timeout in seconds")

    # Ollama generation parameters
    OLLAMA_NUM_PREDICT: int = Field(4096, description="Max number of tokens to generate")
    OLLAMA_TEMPERATURE: float = Field(0.1, description="Sampling temperature (0.0-1.0)")
    OLLAMA_TOP_P: float = Field(0.9, description="Nucleus sampling threshold")
    OLLAMA_TOP_K: int = Field(40, description="Top-k sampling parameter")
    OLLAMA_REPEAT_PENALTY: float = Field(1.1, description="Repetition penalty (1.0 = no penalty)")
    OLLAMA_REPEAT_LAST_N: int = Field(64, description="Tokens to consider for repeat penalty")
    OLLAMA_STOP_SEQUENCES: Optional[str] = Field(None, description="Comma-separated stop sequences")

    # ------------------------------------------------------------------ #
    # Redis (memory manager para loops entre componentes Haystack)
    # ------------------------------------------------------------------ #
    REDIS_HOST: str = Field("redis", description="Redis host")
    REDIS_PORT: int = Field(6379, description="Redis port")
    REDIS_PASSWORD: Optional[str] = Field("redis_password", description="Redis password")
    REDIS_DB: int = Field(0, description="Redis database index")

    # ------------------------------------------------------------------ #
    # OpenMetadata (fonte de metadados para a ingestão do grafo)
    # ------------------------------------------------------------------ #
    OM_HOST: str = Field("http://localhost:8585", description="OpenMetadata base URL")
    OM_API_PATH: str = Field("/api/v1", description="Prefixo da API do OpenMetadata")
    OM_JWT_TOKEN: Optional[str] = Field(None, description="OpenMetadata JWT (Bearer) token")
    OM_PAGE_SIZE: int = Field(100, description="Tamanho de página da API do OpenMetadata")
    OM_TIMEOUT: int = Field(60, description="Timeout (s) das chamadas ao OpenMetadata")

    # ------------------------------------------------------------------ #
    # Ingest / batching / splitting
    # ------------------------------------------------------------------ #
    INGEST_BATCH_DOCS: int = Field(200, description="Number of documents per ingest batch")
    EMBED_BATCH_SIZE: int = Field(32, description="Batch size for document embedding")
    WRITE_BATCH_SIZE: int = Field(128, description="Batch size for vector store writes")
    SPLIT_BY: str = Field("word", description="DocumentSplitter split unit")
    SPLIT_LENGTH: int = Field(250, description="DocumentSplitter split length")
    SPLIT_OVERLAP: int = Field(37, description="DocumentSplitter split overlap")

    # ------------------------------------------------------------------ #
    # Pydantic v2 config
    # ------------------------------------------------------------------ #
    model_config = SettingsConfigDict(
        extra="ignore",        # ignora envs desconhecidas (ex.: hayhooks_*)
        env_file=".env",       # carrega do .env
        case_sensitive=False,  # vars de ambiente não sensíveis a maiúsc/minúsc
        env_prefix="",         # defina um prefixo aqui se quiser (ex.: "rag_")
    )

    @property
    def ollama_generation_kwargs(self) -> dict:
        """Monta o dict `generation_options` do OllamaGenerator a partir dos envs."""
        options = {
            "num_predict": self.OLLAMA_NUM_PREDICT,
            "temperature": self.OLLAMA_TEMPERATURE,
            "top_p": self.OLLAMA_TOP_P,
            "top_k": self.OLLAMA_TOP_K,
            "repeat_penalty": self.OLLAMA_REPEAT_PENALTY,
            "repeat_last_n": self.OLLAMA_REPEAT_LAST_N,
        }
        if self.OLLAMA_STOP_SEQUENCES:
            options["stop"] = [s.strip() for s in self.OLLAMA_STOP_SEQUENCES.split(",") if s.strip()]
        return options


settings = Settings()

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
    QDRANT_USE_SPARSE: bool = Field(True, description="Enable sparse embeddings (retrieval híbrido)")
    QDRANT_SPARSE_MODEL: str = Field(
        "Qdrant/bm25",
        description="Modelo esparso do FastEmbed para o retrieval híbrido",
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
    # OpenAI (LLM da geração de SQL na query)
    # ------------------------------------------------------------------ #
    # A ingestão e o embedding da query continuam on-prem (Ollama gte-qwen2 +
    # BM25, ADR-0009 "plano B"); só a GERAÇÃO de SQL usa a OpenAI, que tem
    # modelos melhores. Nada do dado das linhas trafega aqui — só a pergunta e
    # os metadados do schema recuperado (ADR-0009 §4).
    OPENAI_API_KEY: Optional[str] = Field(None, description="OpenAI API key (LLM da query)")
    OPENAI_CHAT_MODEL: str = Field("gpt-4o", description="Modelo OpenAI para geração de SQL")
    OPENAI_TEMPERATURE: float = Field(0.1, description="Temperatura da geração (determinismo)")
    OPENAI_MAX_TOKENS: int = Field(2048, description="Máx. de tokens na resposta do LLM")
    OPENAI_TIMEOUT: int = Field(60, description="Timeout (s) das chamadas à OpenAI")

    # ------------------------------------------------------------------ #
    # Camada de entrega (ADR-0013): execução via postgres-mcp + gráfico
    # ------------------------------------------------------------------ #
    # O postgres-mcp (crystaldba) já roda em modo RESTRICTED (read-only) e é o
    # que executa/autocorrige o SQL. Dentro da rede do compose ele é alcançável
    # como `postgres-mcp:8000`; o transporte é SSE (endpoint /sse).
    POSTGRES_MCP_URL: str = Field(
        "http://postgres-mcp:8000/sse",
        description="Endpoint SSE do postgres-mcp para o Agent executar o SQL",
    )
    AGENT_MAX_STEPS: int = Field(
        6, description="Máx. de passos do Agent na execução/autocorreção do SQL"
    )
    MAX_EXEC_ROWS: int = Field(
        1000, description="LIMIT default aplicado à consulta de dados do gráfico"
    )
    CHART_DPI: int = Field(
        120, description="DPI do PNG inline no chat (menor = resposta mais leve)"
    )
    CHART_MAX_CATEGORIES: int = Field(
        20, description="Máx. de categorias/barras num gráfico de barras"
    )

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
    OM_EXPECTED_VERSION: str = Field("1.12.1", description="Versão esperada da API OpenMetadata")
    OM_SCOPE_ID: str = Field("mcid", description="Identificador estável do escopo de ingestão")
    OM_SERVICE: Optional[str] = Field(None, description="FQN/nome do Database Service permitido")
    OM_DATABASE: Optional[str] = Field(None, description="FQN/nome do database permitido")
    OM_SCHEMAS: Optional[str] = Field(
        None,
        description="Allowlist de FQNs de schemas, separada por vírgula",
    )
    OM_RELATIONSHIP_ADAPTER: str = Field(
        "mcid_markdown_v1",
        description="Adapter configurável para relações semânticas",
    )
    OM_SEMANTIC_RELATIONSHIPS_PROPERTY: str = Field(
        "mcidSemanticRelationships",
        description="Custom Property com relações semânticas documentadas",
    )
    OM_RELATED_TABLES_PROPERTY: str = Field(
        "mcidRelatedTables",
        description="Custom Property com referências para tabelas relacionadas",
    )
    OM_REQUIRE_DOCUMENTED_TABLES: bool = Field(
        True,
        description=(
            "Aceita somente tabelas que tenham a Custom Property documental "
            "configurada em OM_SEMANTIC_RELATIONSHIPS_PROPERTY"
        ),
    )
    OM_CARD_SCHEMA_VERSION: str = Field("v1", description="Versão do contrato dos cards")
    OM_EMBEDDING_PROFILE: str = Field(
        "dev-nomic-embed-text__qdrant-bm25-v1",
        description="Identificador imutável do perfil de embeddings desta collection",
    )
    OM_PAGE_SIZE: int = Field(100, description="Tamanho de página da API do OpenMetadata")
    OM_TIMEOUT: int = Field(60, description="Timeout (s) das chamadas ao OpenMetadata")
    OM_MAX_RETRIES: int = Field(3, description="Retries para timeout, HTTP 429 e HTTP 5xx")

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

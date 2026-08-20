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
    # Idioma do BM25 (stemmer + stopwords). O default do fastembed é "english",
    # que sobre corpus e perguntas em PT-BR não relaciona "habitacionais" com
    # "habitacional" e não remove "quais/são/com/maior" — que viram termos de
    # IDF alto e puxam ruído. Vale para os DOIS lados; trocar exige reindexar o
    # vetor esparso (barato: fastembed roda local, sem tocar no denso).
    # Dobra acento e underscore antes do BM25, nos DOIS lados. Medido: o stemmer
    # português do fastembed leva 'situação'->'situaçã' e 'situacao'->'situaca',
    # então acento QUEBRA o casamento — e o corpus mistura as duas formas,
    # enquanto os valores do banco vêm sem acento e as perguntas com. Só afeta o
    # vetor esparso; o denso e o texto do card ficam intactos. Exige reindexar.
    # Default DESLIGADO: medido em 2026-08-13 e NÃO melhorou a recuperação
    # (as 2 falhas restantes continuaram). O mecanismo fica disponível para
    # re-teste, mas ligar exige reindexar — os dois lados têm de casar.
    SPARSE_FOLD_ACCENTS: bool = Field(
        False, description="Normaliza acento/underscore no canal esparso"
    )
    SPARSE_LANGUAGE: str = Field(
        "portuguese", description="Idioma do BM25 (stemmer/stopwords do fastembed)"
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
    # Tuning do retrieval (Fase 1: scope + determinismo + poda de colunas)
    # ------------------------------------------------------------------ #
    # Isola o scope na busca vetorial (ADR-0011). Vazio/none desliga o filtro.
    SCOPE_FILTER_ENABLED: bool = Field(True, description="Aplica filtro scope_id no Qdrant")
    # Pool recuperado antes do corte determinístico do top-k (desempate estável).
    RETRIEVAL_POOL_SIZE: int = Field(30, description="Pool do híbrido antes do corte estável")
    # Modo de expansão do subgrafo (Fase 2). "all": expande todos os vizinhos de
    # 1 hop por SEMANTIC_JOIN (comportamento atual). "evidence": só mantém o
    # vizinho se ele também for semente OU o join for elegível (sql_join_eligible).
    # Default "evidence" (medido seguro no gold: 0 tabelas-gold perdidas,
    # over-expansion 27→16.6, prompt p95 acumulado −52%). "all" = fallback seguro.
    EXPANSION_MODE: str = Field("evidence", description="Expansão do subgrafo: all | evidence")
    # Reranker cross-encoder sobre o pool do híbrido (item 3.1 do plano). O RRF
    # só conhece POSIÇÃO em cada canal; o cross-encoder lê pergunta e card juntos
    # e pontua relevância — é o que separa candidatos quase idênticos. Roda local
    # via fastembed (CPU/ONNX), sem sair da infra. Desligado por default.
    RERANK_ENABLED: bool = Field(False, description="Reordena o pool com cross-encoder")
    RERANK_MODEL: str = Field(
        "jinaai/jina-reranker-v2-base-multilingual",
        description="Modelo de rerank do fastembed (⚠️ jina-v2 é CC-BY-NC)",
    )
    RERANK_TOP_K: int = Field(5, description="Quantos documentos sobrevivem ao rerank")
    # Poda de colunas na serialização do subgrafo. Sempre mantém PK, chaves de
    # JOIN, colunas-semente e colunas casadas com a pergunta; o teto limita o
    # "resto" (colunas por ordinal). Tabela-semente recebe teto maior.
    PRUNE_SEED_TABLE_MAX_COLS: int = Field(25, description="Teto de colunas por tabela-semente")
    PRUNE_EXPANSION_TABLE_MAX_COLS: int = Field(6, description="Teto de colunas por tabela só de expansão")
    # Prefixo de instrução no embedding da QUERY (modelos instruction-tuned).
    # Só o lado da query muda → NÃO exige reindexação. Default desligado: o
    # ganho depende do modelo (medido positivo no gte-qwen2 com tarefa em
    # inglês, negativo no qwen3-embedding), então precisa ser re-medido a cada
    # troca de modelo, nunca assumido.
    QUERY_INSTRUCTION_ENABLED: bool = Field(
        False, description="Prefixa 'Instruct: {tarefa}\\nQuery: ...' no embedding da query"
    )
    QUERY_INSTRUCTION_TASK: str = Field(
        "Given a business question in Portuguese, retrieve the database table "
        "or column metadata needed to answer it",
        description="Descrição da tarefa usada no prefixo de instrução da query",
    )

    # ------------------------------------------------------------------ #
    # Persona 1 — modo Explorar (ADR-0014)
    # ------------------------------------------------------------------ #
    # Knobs EXCLUSIVOS da pipeline `explore`. Nenhum deles é lido pelo caminho
    # da Persona 2: os dois modos querem coisas opostas do retrieval (cobertura
    # vs. precisão), e o baseline medido da Persona 2 vive nos defaults acima.
    EXPLORE_TOP_K: int = Field(
        15, description="Sementes do híbrido no modo Explorar (cobertura > precisão)"
    )
    EXPLORE_MAX_TABLES: int = Field(
        25, description="Teto de tabelas no panorama (sementes + expansão)"
    )
    EXPLORE_MAX_COLUMNS: int = Field(
        10, description="Colunas no card de uma tabela-SEMENTE (a que a busca elegeu)"
    )
    # Orçamento diferenciado, como na Persona 2 (25/6): a tabela-semente é a
    # resposta e merece mais colunas; a de expansão é contexto de vizinhança.
    # Diferenciar redistribui o MESMO custo — há mais tabelas de expansão que
    # sementes, então apertar lá paga o alargamento aqui.
    EXPLORE_MAX_COLUMNS_EXPANSION: int = Field(
        6, description="Colunas no card de uma tabela que entrou só por expansão"
    )
    # Nomes das colunas PODADAS listados no card (só o nome, sem tipo/descrição).
    # É o que impede a resposta de negar uma coluna que existe. Custa tokens, então
    # é teto por tabela — e vale só para tabela-semente (ver `_serialize`).
    EXPLORE_MAX_OMITTED_NAMES: int = Field(
        0, description="Nomes de colunas omitidas listados por tabela-semente (0 = nenhum)"
    )
    EXPLORE_EXPAND_NEIGHBORS: bool = Field(
        True, description="Inclui vizinhos de 1 hop por SEMANTIC_JOIN (elegíveis ou não)"
    )
    EXPLORE_QUESTIONS_PER_TABLE: int = Field(
        3, description="Máx. de perguntas sugeridas por tabela"
    )
    EXPLORE_MAX_LISTED_TABLES: int = Field(
        12, description="Tabelas detalhadas na resposta (o resto vira menção curta)"
    )
    # O LLM promove as tabelas que julgou mais pertinentes para o topo da lista.
    # Muda MRR/nDCG para melhor ou pior — é knob para poder medir os dois lados
    # contra o ranking puro do retrieval.
    EXPLORE_LLM_REORDER: bool = Field(
        True, description="LLM reordena o topo do panorama (vs. ordem do retrieval)"
    )
    # Amostra de dados reais na resposta da Persona 1: executa o `sample_sql`,
    # mostra as primeiras linhas no chat e publica o CSV para download.
    EXPLORE_EXECUTE_SAMPLE: bool = Field(
        True, description="Executa o SQL de amostra e mostra linhas reais na resposta"
    )
    EXPLORE_SAMPLE_ROWS: int = Field(
        100, description="Linhas buscadas na amostra (vão inteiras para o CSV)"
    )
    EXPLORE_SAMPLE_PREVIEW_ROWS: int = Field(
        10, description="Linhas da amostra exibidas na tabela do chat"
    )
    # A escada da amostra tenta a consulta do LLM, depois a autocorreção e, por
    # fim, reconstrói a consulta pelo catálogo (sem LLM). Desligar a
    # autocorreção não tira a amostra — tira a preservação da INTENÇÃO da
    # consulta quando o banco recusa a versão do modelo.
    EXPLORE_SAMPLE_AUTOCORRECT: bool = Field(
        True, description="Deixa o Agent corrigir o SQL da amostra quando o banco recusa"
    )
    EXPLORE_SAMPLE_AGENT_STEPS: int = Field(
        3, description="Teto de passos do Agent na autocorreção da amostra"
    )
    # Lacunas conhecidas de escopo: {conceito: "o que existe no lugar"}. É o que
    # transforma "refine a pergunta" em recusa informativa ("não tenho execução
    # orçamentária; tenho valor contratado e desembolsado"). Mesmo padrão do
    # OM_GRAIN_KEYS: conhecimento de escopo mora em env, não em código.
    SCOPE_GAPS: str = Field(
        "", description='JSON {"<conceito>": "<mensagem de lacuna>"} para o modo Explorar'
    )

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
    # Escolha do gráfico sem LLM quando a forma do resultado já decide
    # (temporal+métrica = série; categórica+métrica = barras). Custava ~5s por
    # resposta — 25% da latência. Ambíguo ainda vai ao LLM.
    CHART_SPEC_HEURISTIC: bool = Field(
        True, description="Decide o gráfico por regra quando a forma é clara"
    )
    # Executa o SQL direto pelo MCP (sem Agent/LLM) e só cai na autocorreção se
    # o banco recusar. O Agent cobrava ~2 chamadas de LLM mesmo com SQL correto.
    MCP_DIRECT_FIRST: bool = Field(
        True, description="Tenta execução direta antes de acionar o Agent"
    )
    MCP_DIRECT_TIMEOUT: int = Field(
        60, description="Timeout (s) da execução direta via MCP"
    )
    CHART_DPI: int = Field(
        120, description="DPI do PNG inline no chat (menor = resposta mais leve)"
    )
    CHART_MAX_CATEGORIES: int = Field(
        20, description="Máx. de categorias/barras num gráfico de barras"
    )

    # ------------------------------------------------------------------ #
    # Relatório .docx (ADR-0010): resposta Markdown → Word editável + MinIO
    # ------------------------------------------------------------------ #
    REPORT_ENABLED: bool = Field(
        True, description="Anexa link de download do relatório .docx na resposta do chat"
    )
    REPORT_OUTPUT_DIR: str = Field(
        "/app/reports", description="Diretório local onde o .docx é gerado antes do upload"
    )
    REPORT_TITLE: str = Field(
        "Relatório de Consulta", description="Título exibido na capa do relatório"
    )
    REPORT_KICKER: str = Field(
        "GovHub · Assistente de Dados",
        description="Eyebrow (linha de contexto em maiúsculas) na capa",
    )
    REPORT_FOOTER: str = Field(
        "GovHub · Assistente de Dados · Metodologia GovHub",
        description="Assinatura da marca (base da capa e rodapé)",
    )
    REPORT_PRESIGN_EXPIRY: int = Field(
        604800, description="Validade (s) da URL assinada de download (default 7 dias)"
    )
    # MinIO (S3-compatível) — armazenamento do .docx.
    # ENDPOINT: alcançável de dentro da rede do compose (upload).
    # PUBLIC_ENDPOINT: host que o NAVEGADOR do usuário acessa (assinatura da URL).
    MINIO_ENDPOINT: str = Field("http://minio:9000", description="Endpoint interno do MinIO")
    MINIO_PUBLIC_ENDPOINT: str = Field(
        "http://localhost:9000",
        description="Endpoint público do MinIO (para assinar o link de download)",
    )
    MINIO_ACCESS_KEY: str = Field("minioadmin", description="MinIO access key")
    MINIO_SECRET_KEY: str = Field("minioadmin", description="MinIO secret key")
    MINIO_BUCKET: str = Field("relatorios", description="Bucket dos relatórios")
    # Linhas do banco não moram junto com relatórios, e a URL delas expira
    # antes: é o primeiro conteúdo do sistema que não é metadado.
    SAMPLE_BUCKET: str = Field("amostras", description="Bucket dos CSVs de amostra")
    SAMPLE_PRESIGN_EXPIRY: int = Field(
        900, description="Validade (s) da URL do CSV de amostra (default 15 min)"
    )
    MINIO_REGION: str = Field("us-east-1", description="Região (formalidade do SigV4)")

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
    OM_CARD_SCHEMA_VERSION: str = Field(
        "v1",
        description=(
            "Versão do contrato dos cards. 'v1' = layout original (rotulado, "
            "com FQN e caminho repetidos). 'v2' = card denso em significado: "
            "campos estruturais saem do texto embutido (continuam no payload e "
            "no grafo) e entram domínio de negócio e grão."
        ),
    )
    # Rótulo humano por schema, usado na linha "Domínio:" do card v2. O default
    # é vazio: sem mapa, o card emite o nome curto do schema, que funciona em
    # qualquer catálogo. O mapa existe para dar nome de negócio ao domínio
    # (ex.: "empreendimento_far" -> "FAR — Fundo de Arrendamento Residencial"),
    # sem acoplar o componente genérico a um catálogo específico (ADR-0000).
    OM_DOMAIN_LABELS: str = Field(
        "", description='JSON {"<schema_curto>": "<rótulo de negócio>"} para o card v2'
    )
    # Grão declarado por tabela (FQN -> descrição do grão). O MCid não declara
    # PK (0 de 74 tabelas) nem FK, então o grão NÃO é inferível dos metadados:
    # precisa ser declarado. Caminho de longo prazo é uma Custom Property no
    # OpenMetadata, igual às relações semânticas; este mapa é a ponte.
    # Vocabulário de VALOR por tabela: {fqn: {coluna: [valores]}}.
    # Em tabela EAV/formato longo o significado está nos VALORES, não nos nomes
    # de coluna — `panorama_estadual` guarda 'situacao' e 'execucao_fisica' como
    # valores de `secao`, e o card só indexava `secao`. A pergunta cita termos
    # que não existem em lugar nenhum do texto indexado, e nenhum embedding casa
    # com o que não está no índice. Medido: 26 tabelas afetadas.
    OM_VALUE_VOCAB: str = Field(
        "", description='JSON {fqn: {coluna: [valores]}} injetado no card v2'
    )
    OM_GRAIN_KEYS: str = Field(
        "", description='JSON {"<fqn da tabela>": "<descrição do grão>"} para o card v2'
    )
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

# graphrag-tais — guia rápido

GraphRAG de metadados para **text-to-SQL**: pergunta em PT → recupera o subgrafo de
schema (Qdrant + Neo4j) → gera SQL. Os metadados vêm do **OpenMetadata**. É um RAG
**genérico do GovHub**; entrega imediata é o MinC, mas o **desenvolvimento/validação
é sobre o MCid** (ADR-0000).

## Verdade da arquitetura: os ADRs

Todas as decisões estão em `docs/adr/` (0000–0012) — índice em
`docs/adr/README.md`. **Leia os ADRs antes de decidir algo de arquitetura.** Regra:
um ADR só cita ADR de número menor. Antes de mexer em retrieval, veja o gate de
avaliação em `AGENTS.md` + `skills/evaluate-retrieval-change/` (rode `make eval-quick`
antes/depois e `eval-full` pra concluir).

## Stack

- **Haystack 3 + Hayhooks** (serve as pipelines como REST).
- **Qdrant** (vetor híbrido: denso + esparso), **Neo4j** (grafo puro, Cypher), **Redis** (cache).
- Embedding **denso**: Ollama `gte-qwen2-1.5b` (remoto, via `OLLAMA_URL`), multilíngue/on-prem.
  Esparso: **BM25** (`Qdrant/bm25`, fastembed). Costura Qdrant↔Neo4j por `node_id`.
  > O ADR-0009 decide migrar o denso p/ OpenAI `text-embedding-3-large`, mas **ainda
  > não está implementado** — a branch usa Ollama (equivale ao "plano B on-prem" do 0009).
- Fonte: **OpenMetadata** REST (`OM_*` no `.env`). Escopo MCid: service `Cidades`,
  db `cidades`, 5 schemas.

## Como subir e rodar

1. Docker Desktop ligado. `.env` na raiz. ⚠️ **Sem cercas markdown (```) no `.env`** — quebra o `docker compose`.
2. `make up` → sobe **qdrant, neo4j, hayhooks, postgres-mcp** (NÃO sobe redis nem webui).
3. **Ingestão**: `curl -X POST http://localhost:8000/ingest/run -d '{}'` (sem params;
   escopo vem do `.env`). Grava grafo no Neo4j primeiro; o `GraphWriteGate` só libera
   embeddings→Qdrant após o Neo4j confirmar. É o que o Airflow chama em produção.
4. **Query (preview)**: `POST http://localhost:8000/query/run` com `{"query":"..."}`,
   ou a Swagger em `http://localhost:8000/docs`.

## Acessos (dev — trocar em prod)

- Hayhooks: http://localhost:8000 (`/docs`, `/status`) — sem login.
- Qdrant: http://localhost:6333/dashboard — `api-key` em `.env` (`QDRANT_API_KEY`).
- Neo4j: http://localhost:7474 — user `neo4j`, senha em `.env` (`NEO4J_PASSWORD`, dev `password123`).
- open-webui (chat): não sobe no `make up` → `docker compose up -d webui` (:3000).
  Já **funciona como chat**: a pipeline `query` tem `run_chat_completion`
  (OpenAI-compat) e o modelo `query` aparece no open-webui, devolvendo a resposta
  em Markdown (explicação + SQL + referências).

## Estado atual (2026-07-23, branch `feat/ingest`)

- **Ingestão OK e verificada**: 74 tabelas, 1.215 colunas → 1.289 nós/cards; Qdrant com
  1.289 points (denso 1536 + esparso). Relações: `HAS_COLUMN` 1215,
  **`SEMANTIC_JOIN` 24 (coluna↔coluna)**, **`FOREIGN_KEY` 0**.
- O MCid **não declara FK**: a joinabilidade vem da Custom Property "Relações
  semânticas MCID" do OpenMetadata, consumida pelo adapter `mcid_markdown_v1`.
- ADR-0011 (contrato da ingestão, **substitui o 0005**) + ADR-0012 (eval orientado a
  métricas) + framework `eval/` entraram nesta branch.

## Estado da query (Retrieval v1 — branch `feat/ingest`)

Pipeline de query implementada e funcional ponta a ponta (pergunta → Qdrant híbrido
→ subgrafo Neo4j → LLM → resposta):

- ✅ **Retrieval alinhado (fix nº1 feito)**: `Neo4jSubgraphRetriever` agora expande por
  `SEMANTIC_JOIN` (+ `HAS_COLUMN`) e devolve tabelas + JOINs (com `sql_join_eligible`).
- ✅ **LLM = OpenAI** (`OpenAIChatGenerator`, `gpt-4o`, saída JSON) só na geração de SQL;
  os embeddings da query seguem on-prem (Ollama gte-qwen2 + BM25), iguais à ingestão.
- ✅ **IR mínima** (intent/metric/filters) gerada junto do SQL numa única chamada e
  **exposta na resposta** (ponto de controle do ADR-0006/0007); ainda **não** reescreve
  o roteamento da recuperação por conceito.
- ✅ **`run_chat_completion`** (OpenAI-compat) + `SqlAnswerBuilder` → resposta em Markdown
  (explicação + SQL + referências ancoradas no subgrafo) no open-webui.

### Gaps conhecidos / próximos passos

1. Sem **validador estrutural de SQL** (`sqlglot`) nem loop de reescrita (ADR-0007);
   `top_k=5` fixo, sem reranker. A ressalva de grain aparece na resposta, mas a
   correção semântica não é garantida.
2. IR **completa** (`group_by`/`time_range`) + roteamento de recuperação por conceito.
3. Camada de entrega (execução read-only, gráfico, explicação, docx — ADR-0008/0010).
4. Backlog (ADRs): grain/fan-out, `CAN_JOIN` derivado, normalização por glossário.
5. **Medir com `eval/`** (`make eval-quick`/`eval-full`): ainda em bootstrap (gold `draft`,
   sem baseline aceita, retrieval não emite `RetrievalTrace` completo) — gate real
   pendente. Obrigatório p/ mudanças de retrieval.

## Camada de entrega (ADRs 0008/0010, ainda não implementada)

Do SQL saem: (a) tabelas/colunas, (b) gráfico (matplotlib no v1; Superset MCP no v2),
(c) explicação (LLM, com ressalva de grain), (d) relatório docx (`docxtpl` + MinIO).
Execução do SQL: conexão read-only direta no v1 (ADR-0008).

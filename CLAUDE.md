# graphrag-tais — guia rápido

GraphRAG de metadados para **text-to-SQL**: pergunta em PT → recupera o subgrafo de
schema (Qdrant + Neo4j) → gera SQL → executa (read-only) → devolve explicação + SQL
+ **gráfico**. Os metadados vêm do **OpenMetadata**. É um RAG **genérico do GovHub**;
entrega imediata é o MinC, mas o **desenvolvimento/validação é sobre o MCid**
(ADR-0000). Servido pelo **open-webui** (chat).

## Verdade da arquitetura: os ADRs

Todas as decisões estão em `docs/adr/` (**0000–0013**) — índice em `docs/adr/README.md`.
**Leia os ADRs antes de decidir algo de arquitetura.** Regra: um ADR só cita ADR de
número menor. Destaques recentes: **0011** (contrato da ingestão, substitui o 0005),
**0012** (eval orientado a métricas), **0013** (execução via postgres-mcp + gráfico por
chart-spec — revisa a execução do 0008). Antes de mexer em **retrieval**, respeite o
gate MDD: `AGENTS.md` + `skills/evaluate-retrieval-change/SKILL.md` (medir antes/depois).

## Stack

- **Haystack 3.0 + Hayhooks 1.22** (serve as pipelines como REST OpenAI-compat).
- **Qdrant** (vetor híbrido denso+esparso), **Neo4j 5** (grafo puro, Cypher), **Redis** (cache, ocioso).
- **postgres-mcp** (crystaldba, modo RESTRICTED read-only) — executa o SQL gerado.
- Embedding **denso**: Ollama `aroxima/gte-qwen2-1.5b-instruct:f16` (1536 dims, remoto via
  `OLLAMA_URL`, multilíngue/on-prem). **Esparso**: BM25 (`Qdrant/bm25`, fastembed).
  Costura Qdrant↔Neo4j por `node_id`.
  > ADR-0009 quer migrar o denso p/ OpenAI `text-embedding-3-large`, mas **não implementado** — usa Ollama ("plano B on-prem").
- **LLM de geração de SQL/gráfico = OpenAI `gpt-4o`** (só a geração; embeddings seguem on-prem).
- Fonte de metadados: **OpenMetadata** REST (`OM_*` no `.env`). Escopo MCid: service
  `Cidades`, db `cidades`, 5 schemas (conjuntura_bronze/silver/gold, empreendimento_far, entidades_fds).

## Onde ficam as coisas (3 lugares distintos)

| Serviço | Guarda | Contém as LINHAS? |
|---|---|---|
| **Qdrant** (`localhost:6333`) | embeddings dos cards de schema | ❌ só metadados |
| **Neo4j** (`localhost:7687`) | grafo (tabelas, colunas, joins) | ❌ só metadados |
| **PostgreSQL `cidades`** (`10.0.0.50:5432`) | **os dados reais** | ✅ **sim** — banco analítico à parte |

O RAG só conhece o *schema*. As **linhas** vivem num **PostgreSQL separado** (`cidades` @
`10.0.0.50`, ~35 schemas, só 5 ingeridos). Os nomes físicos `schema.tabela` batem com o
SQL gerado (a serialização usa o nome físico, não o FQN do catálogo).

## Como subir e rodar

1. Docker Desktop ligado. **VPN/rede da lab conectada** (ver Gotchas). `.env` na raiz —
   ⚠️ **sem cercas markdown (```) no `.env`** (quebra o `docker compose`).
2. `make up` → rebuild + sobe **qdrant, neo4j, hayhooks, postgres-mcp** (NÃO sobe redis nem webui).
3. **Ingestão**: `curl -X POST http://localhost:8000/ingest/run -d '{}'` (escopo vem do `.env`).
   Grava Neo4j primeiro; o `GraphWriteGate` só libera embeddings→Qdrant após o Neo4j confirmar.
4. **Query (JSON, só motor)**: `POST http://localhost:8000/query/run` `{"query":"..."}` — devolve
   explicação + SQL + IR + referências (NÃO executa SQL).
5. **Chat (motor + execução + gráfico)**: `docker compose up -d webui` (:3000) → modelo **`query`**.
   O `run_chat_completion` compõe motor + entrega e embute o gráfico (PNG base64) no Markdown.

## Acessos (dev — trocar em prod)

- Hayhooks: http://localhost:8000 (`/docs`, `/status`, `/v1/models`) — sem login.
- Qdrant: http://localhost:6333/dashboard — `api-key` em `.env`.
- Neo4j: http://localhost:7474 — user `neo4j`, senha `.env` (`NEO4J_PASSWORD`, dev `password123`).
- open-webui: http://localhost:3000 (`WEBUI_AUTH=false`), aponta pro hayhooks como OpenAI API.

## Estado atual (2026-08-04, branch `feat/ingest`, **nada commitado**)

**Ingestão OK e verificada**: 74 tabelas, 1.215 colunas → 1.289 nós/cards; Qdrant 1.289 points
(denso 1536 + esparso). Relações: `HAS_COLUMN` 1215, **`SEMANTIC_JOIN` 24** (coluna↔coluna),
**`FOREIGN_KEY` 0**. O MCid **não declara FK**: a joinabilidade vem da Custom Property
"Relações semânticas MCID" do OM, via adapter `mcid_markdown_v1`.

**Grafo real (importante p/ retrieval)**: **55/74 tabelas são ilhas** (sem `SEMANTIC_JOIN`);
joins só existem **dentro de FAR/FDS** (nenhum cruza schema); toda a conjuntura é ilha; hubs
`cadastro_pj` (grau 8) e `fds_cadastro_pj` (grau 6); **só 6 joins são elegíveis** (`sql_join_eligible=true`),
**FAR não tem nenhum**.

### Motor de query (Retrieval v1 + tuning Fase 1/2) — implementado ✅

Fluxo: `pergunta → dense(Ollama)+sparse(BM25) → Qdrant híbrido → subgrafo Neo4j → LLM(OpenAI) → SqlAnswerBuilder`.

- **`Neo4jSubgraphRetriever`** expande por `SEMANTIC_JOIN` (+`HAS_COLUMN`), devolve tabelas + JOINs.
  Fix nº1 (antes percorria `FOREIGN_KEY`/`LINEAGE`, que não existem → não expandia nada).
- **Tuning Fase 1/2** (medido com `eval/`, ver `experiments/20260804-*.json` e `-comparison.md`):
  - `DeterministicHybridRetriever` (`qdrant_hybrid_retriever.py`): filtro `scope_id` + pool 30 +
    corte estável `(-score, node_id)` → **top-5 determinístico 8/8**.
  - **Poda de colunas** na serialização (mantém PK + chaves de JOIN + colunas-semente + casadas
    com a pergunta; recebe `query`).
  - **`EXPANSION_MODE=evidence`** (default): vizinho só entra se for semente OU join elegível.
  - Resultado: prompt p95 **35k→16.8k (−52%)**, over-expansion **27×→16.6×**, MRR@10 +25%,
    latência p95 −71%, com **suficiência/JOIN/colunas-gold/tabelas-gold 100% preservados**.
  - 🔴 **Seed recall ~30% segue o gargalo** (não tocado) → próximo lever é prefixo de instrução no GTE.
- **LLM = OpenAI `gpt-4o`** (saída JSON) só na geração. **IR mínima** (intent/metric/filters) gerada
  junto e exposta na resposta (ponto de controle ADR-0006/0007), ainda não roteia a recuperação.
- **`SqlAnswerBuilder`**: explicação + SQL + dicionário de dados (só colunas usadas) + IR + JOINs.

### Camada de entrega — execução + gráfico (ADR-0013) ✅

Pipeline SEPARADA `pipelines/delivery/` que o `run_chat_completion` compõe (motor fica reusável):
- **`McpSqlExecutor`**: roda o SQL via **Haystack `Agent` + `MCPToolset(postgres-mcp)`** (SSE
  `http://postgres-mcp:8000/sse`, read-only, **autocorreção no erro** até `AGENT_MAX_STEPS`).
  O `execute_sql` devolve envelope MCP JSON cujo `content[].text` é **repr Python** (Decimal/datetime) → parser contido.
- **`ChartSpecBuilder`** (LLM JSON): escolhe bar/line/none, validado nas colunas reais.
- **renderers matplotlib** (Agg) → PNG **base64** embutido como `![gráfico](data:image/png;base64,…)`.
- Aditivo e tolerante a falha: sem execução/gráfico → devolve só o texto.

## Como rodar o eval de retrieval (gate MDD)

O collector ao vivo (`eval/retrieval/live.py`) usa os **componentes de produção** e precisa dos
serviços + Ollama. **Roda dentro do container** (o `eval/` não é montado):

```bash
# 1) copiar eval pro container  ⚠️ docker cp ANINHA se /app/eval já existe → apague antes
docker exec hayhooks rm -rf /app/eval && docker cp eval hayhooks:/app/eval
# 2) coletar run ao vivo (canal híbrido)
docker exec -w /app -e PYTHONPATH=/app:/app/src hayhooks python -m eval.retrieval.live \
  --dataset eval/datasets/retrieval/mcid-business-v0.1.0.json \
  --output eval/reports/RUN.json --channel hybrid
# 3) pontuar
docker exec -w /app -e PYTHONPATH=/app:/app/src hayhooks python -m eval.retrieval.cli score \
  --dataset eval/datasets/retrieval/mcid-business-v0.1.0.json \
  --run eval/reports/RUN.json --output-json eval/reports/REPORT.json --output-md eval/reports/REPORT.md
# 4) puxar de volta pro host: docker cp hayhooks:/app/eval/reports/REPORT.json eval/reports/
```

- `make eval-quick` roda offline (unittests do motor de métricas + smoke em fixtures) — não bate nos serviços.
- Dataset gold `mcid-business-v0.1.0.json` é **`draft` / 8 casos** → é **bootstrap diagnóstico**,
  não gate verde (sem baseline aceita). Testar `EXPANSION_MODE=all` vs `evidence` via `-e EXPANSION_MODE=...`.

## Gaps conhecidos / próximos passos

1. 🔴 **Subir seed recall** (~30%): prefixo de instrução no GTE (lado da query, sem reindexar; ADR-0009).
   É o lever de maior alavancagem → deixa a expansão por evidência à prova de bala.
2. **Ampliar o gold** (8 → 30–50 casos, revisão de domínio) e sair do `draft`.
3. Entrega: faltam **(c) explicação do resultado executado** e **(d) relatório docx** (`docxtpl` + MinIO) — ADR-0010.
4. **Guardrail de grain (E2E)**: `empreendimento`/`ficha_empreendimento` têm APF duplicado (1.646 linhas
   p/ 823 APFs) → o renderer soma duplicatas e **dobra valores** no gráfico. Dedup por APF + não somar.
5. Sem validador `sqlglot` (a validação vem da execução real). IR completa (`group_by`/`time_range`) + roteamento por conceito.

## Gotchas de ambiente (aprendidos na marra)

- **Rede `10.0.0.x`**: Ollama (`10.0.0.10`) e o banco analítico (`10.0.0.50`) só respondem com a
  **VPN/rede da lab conectada**. Se der timeout nesses dois mas a internet funcionar, é a VPN caída —
  bloqueia embeddings (retrieval) e execução de SQL (gráfico).
- **OpenAI quota**: se aparecer `429 insufficient_quota`, a conta OpenAI ficou sem crédito → geração de
  SQL/gráfico quebra (retrieval não usa OpenAI). Recarregar ou trocar `OPENAI_API_KEY` + recriar hayhooks.
- **Memória (OOM)**: Docker Desktop com ~7.75 GiB não roda **Airflow + RAG + open-webui** juntos; o
  webui morre com exit 137. Parar o Airflow libera ~3 GiB: `docker stop $(docker ps -aq --filter name=rag-airflow-)`.
- **`docker cp`**: se o destino já existe, aninha (`/app/eval/eval`). Sempre `rm -rf` antes.
- **hayhooks `--reload`** observa `src/` (mudança de componente recarrega), mas mudança no **`.env`**
  exige recriar: `docker compose up -d --no-deps hayhooks`.
- **`.env`**: sem cercas markdown; segredos reais (OPENAI/DB/OM_JWT) ficam nele e ele é gitignored.

## Mapa de arquivos (novos/tocados na branch, não commitados)

- `src/rag/components/retrievers/` — `neo4j_subgraph_retriever.py` (SEMANTIC_JOIN + poda + evidence),
  `qdrant_hybrid_retriever.py` (scope + determinismo).
- `src/rag/components/generators/openai_generator.py` — LLM OpenAI.
- `src/rag/components/answer/sql_answer_builder.py` — resposta em Markdown.
- `src/rag/components/executors/mcp_sql_executor.py` — Agent + postgres-mcp.
- `src/rag/components/charts/` — `chart_spec.py`, `renderers.py`, `chart_renderer.py`.
- `src/rag/pipelines/query/` (motor) e `pipelines/delivery/` (execução+gráfico); `settings/configs.py` (tudo tunável por env).
- `scripts/inspect_retrieval.py` — debug do retrieval (sementes + subgrafo, sem LLM).
- `docs/adr/0013-*.md`, `eval/experiments/20260804-*` (Fase 1/2 + comparativo).

# graphrag-tais — guia rápido

GraphRAG de metadados para **text-to-SQL**: pergunta em PT → recupera o subgrafo de
schema (Qdrant + Neo4j) → gera SQL → executa (read-only) → devolve explicação + SQL
+ **gráfico**. Os metadados vêm do **OpenMetadata**. É um RAG **genérico do GovHub**;
entrega imediata é o MinC, mas o **desenvolvimento/validação é sobre o MCid**
(ADR-0000). Servido pelo **open-webui** (chat).

**Duas personas = dois modelos no dropdown do open-webui** (ADR-0014). Escolha
explícita, sem classificador de intenção:

| nome exibido | id / pipeline / rota | persona | entrega |
|---|---|---|---|
| **Indicador** | `query` · `/query/run` | 2 — já sabe o que medir | bases, SQL, explicação, **leitura do resultado**, **gráfico**, relatório `.docx` |
| **Explorar** | `explore` · `/explore/run` | 1 — não conhece as bases | panorama, tabelas + **grão**, **amostra de linhas reais + CSV**, **lacunas** |

⚠️ **Nome exibido ≠ id.** O Hayhooks deriva o id do modelo do NOME DO DIRETÓRIO da
pipeline (`pipeline_name = pipeline_dir.name`), sem hook de override — renomear o
diretório mudaria as rotas HTTP e quebraria o histórico de conversas do open-webui
(que guarda o id). Por isso os nomes bonitos ficam na **camada do open-webui**
(Workspace → Models → Edit → Name), e o código mantém `query` e `explore`.

⚠️ **A Persona 2 é território calibrado e medido** — `pipelines/query`,
`pipelines/delivery`, `Neo4jSubgraphRetriever`, poda, `EXPANSION_MODE`. O modo
Explorar é **estritamente aditivo** (componentes e pipeline próprios; só
`configs.py` foi tocado, e só para acrescentar campos `EXPLORE_*`). Mudança no
modo Explorar **não** deve alterar arquivo do caminho da Persona 2.

## Verdade da arquitetura: os ADRs

Todas as decisões estão em `docs/adr/` (**0000–0014**) — índice em `docs/adr/README.md`.
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
2. `make up` → rebuild + sobe **qdrant, neo4j, minio, hayhooks, postgres-mcp** (NÃO sobe redis nem webui).
   MinIO (armazenamento do relatório, ADR-0010): API/S3 `localhost:9000`, console `localhost:9001`
   (user/senha = `MINIO_ACCESS_KEY`/`SECRET`).
3. **Ingestão**: `curl -X POST http://localhost:8000/ingest/run -d '{}'` (escopo vem do `.env`).
   Grava Neo4j primeiro; o `GraphWriteGate` só libera embeddings→Qdrant após o Neo4j confirmar.
4. **Query (JSON, só motor)**: `POST http://localhost:8000/query/run` `{"query":"..."}` — devolve
   explicação + SQL + IR + referências (NÃO executa SQL).
5. **Chat (motor + execução + gráfico)**: `docker compose up -d webui` (:3000) → modelo **`Indicador`**.
   O `run_chat_completion` compõe motor + entrega e embute o gráfico (PNG base64) no Markdown.
6. **Explorar (Persona 1)**: modelo **`Explorar`** no mesmo webui, ou
   `POST http://localhost:8000/explore/run` `{"query":"..."}`. Sem gráfico e sem `.docx`;
   **executa só o SQL de amostra** (ADR-0015), que no caminho normal não gasta LLM.

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
  junto, ainda não roteia a recuperação. ⚠️ Desde 21/08 a IR **não é renderizada** (nem no chat, nem
  no `.docx`): segue no `answer` de `/query/run` e no log como ponto de controle (ADR-0006/0007),
  mas era jargão sem uso para quem pergunta.
- **`SqlAnswerBuilder`**: explicação + SQL + dicionário de dados (só colunas usadas) + JOINs +
  **Resultado** (o parágrafo da entrega, quando o SQL rodou).
  O Markdown é montado por `render_markdown(answer, exec_info)` — **função de módulo**, porque o
  wrapper do chat a chama DE NOVO depois da execução (ver invariante do SQL abaixo).

### Camada de entrega — execução + gráfico (ADR-0013) ✅

Pipeline SEPARADA `pipelines/delivery/` que o `run_chat_completion` compõe (motor fica reusável):
- **`McpSqlExecutor`**: roda o SQL via **Haystack `Agent` + `MCPToolset(postgres-mcp)`** (SSE
  `http://postgres-mcp:8000/sse`, read-only, **autocorreção no erro** até `AGENT_MAX_STEPS`).
  O `execute_sql` devolve envelope MCP JSON cujo `content[].text` é **repr Python** (Decimal/datetime) → parser contido.
- **`ChartSpecBuilder`** (LLM JSON): escolhe bar/line/none, validado nas colunas reais.
- **`ResultExplainer`** (ADR-0010 item c, 21/08): 1 parágrafo dizendo o que o resultado mostra —
  vai na seção **`### Resultado`**, imediatamente acima do gráfico (é a legenda dele) e no `.docx`.
  O LLM **não vê as linhas**: lê um `digest` determinístico (`build_digest`) com contagem,
  soma/média/mín/máx, maiores e menores categorias e começo/fim da série. Isso é LGPD (ADR-0010)
  e é também o guardrail: número fora do digest é número inventado. Vale para resultado ESCALAR,
  onde não há gráfico nenhum e antes a resposta terminava sem dizer o número.
- **renderers matplotlib** (Agg) → PNG **base64** embutido como `![gráfico](data:image/png;base64,…)`.
- Aditivo e tolerante a falha: sem execução/gráfico → devolve só o texto.

⚠️ **Invariante do SQL exibido: UM bloco só, o do SQL que RODOU.** O `_with_delivery`
**re-renderiza** a resposta com `exec_info` depois de executar (`render_markdown(answer,
exec_info)`). Executou → `### SQL executado`, com a versão **ajustada** quando a autocorreção
mexeu (a tentativa recusada não aparece, só a nota "ajustado na execução"); não executou →
`### SQL gerado`, **limpo**. O **dicionário de dados deriva do SQL efetivo**. Antes, o Markdown
vinha pronto da pipeline (montado ANTES da execução): a primeira tentativa do LLM aparecia como
"SQL gerado" mesmo recusada pelo banco, e o dicionário documentava as colunas dela.

⚠️ **Erro de execução NÃO vai para a resposta** (decisão de produto, 19/08): sem mensagem de
banco no chat nem callout no `.docx` — quem lê quer a consulta, não o diagnóstico. O erro fica
no `delivery["exec_error"]` e num `logger.warning` do `_with_delivery`; **é lá que se debuga**
uma resposta que saiu sem gráfico. Mesma regra no `.docx` (`report_content.render_answer`).

⚠️ **`_scan_executions` (era `_last_successful_execution`)**: o postgres-mcp devolve erro de banco
como TEXTO com `isError=False`, então filtrar por `res.error` fazia "última bem-sucedida" virar
"última tentativa" — um resultado bom seguido de uma tentativa recusada era descartado (resposta
sem gráfico à toa) e o `sql_final` podia ser um SQL que nunca rodou. Agora a função devolve
`(ok, failed)`, decidindo o que é utilizável pelo `_parse_rows` (fonte única).

### Relatório .docx editável (ADR-0010 item d) ✅

A **resposta completa** do chat (o Markdown final, já com o gráfico base64) vira um
`.docx` **editável no Word** no **estilo editorial GovHub** (o dos PDFs de dicionário de
dados / livro do projeto: capa roxa full-bleed com logo branca + kicker + título, tabelas
sem grade com header roxo + zebra, badges de tipo Texto/Número/Data, chips de código,
callout boxes, rodapé com o símbolo). O **link de download** é anexado à resposta.
`_attach_report` (em `pipelines/query/pipeline_wrapper.py`) é **aditivo/defensivo**
(MinIO fora → devolve só o texto). Mecanismo: **python-docx + tema por código**
(`components/report/theme.py`). O corpo é montado do **`answer` ESTRUTURADO** do motor
(`report_content.render_answer` → `ReportBuilder.build_from_answer`), **não** parseando o
Markdown do chat — isso eliminou marcação crua (crases/underscores) e dá controle total
(badges do tipo real da coluna, descrição em itálico, espaçamento enxuto). `docx_writer.py`
(conversor Markdown→docx) vira **fallback**. `_with_delivery` devolve `(markdown_chat, delivery)`
onde `delivery={chart_image, sql_final, exec_ok, exec_error}`.
**Capa** = **imagem A4 de página inteira** (PIL + fonte Inter em `assets/fonts/`), inserida
full-page numa seção de margem 0. Isso garante **1 página** (a versão anterior em tabela
transbordava p/ uma 2ª página fantasma) e rende a borda arredondada + Inter exatas. ⚠️ Para
verificar `.docx`, **use LibreOffice** (`soffice --headless --convert-to pdf`) + poppler — o
**QuickLook do macOS NÃO pagina** (renderiza contínuo) e esconde bugs de quebra de página. **Não usa docxtpl** (o ADR previa; a
entrada é uma string Markdown única, mais simples converter direto). Armazenamento: **MinIO**
(adicionado ao stack agora) via `boto3`, URL assinada. ⚠️ **Gotcha**: `MINIO_ENDPOINT`
(interno, upload) ≠ `MINIO_PUBLIC_ENDPOINT` (assina a URL p/ o navegador do usuário) —
senão o link quebra fora do Docker.

### Modo Explorar — Persona 1 (ADR-0014) ✅

Pipeline SEPARADA `pipelines/explore/`, exposta como o modelo **`Explorar`**. Reusa a
primeira metade da Persona 2 (embedding híbrido + Qdrant) via factory, com `top_k`
**15** (cobertura > precisão), e troca o passo de grafo:

- **`Neo4jCatalogRetriever`** (não é variante do `Neo4jSubgraphRetriever` — é outro
  componente, para não tocar no caminho medido da Persona 2): (a) devolve os documentos
  **ordenados por relevância** (sementes na ordem do híbrido, depois vizinhos herdando a
  posição de quem os puxou) — a ordem É o contrato que o eval pontua, não reordenar por
  FQN; (b) expansão **ampla** (todos os vizinhos de 1 hop, elegíveis ou não); (c)
  **panorama de domínios** por Cypher agregado → "quais bases existem" tem resposta
  determinística, sem passar por embedding. Importa (só leitura) `_tokenize`,
  `_grain_for` e `_schema_short` do retriever da Persona 2: fonte da verdade única.
- **Poda de colunas própria (19/08, `eval/experiments/20260819-poda-colunas-explore.md`)**:
  a P1 **não usa mais o `_col_matches` da P2** (substring crua). Numa pergunta sobre
  *localização*, `municipio` ficava de fora (a descrição diz "localiza**do**") e
  `situacao_empreendimento` entrava por ter "**dados**" na prosa — palavra que estava na
  pergunta como enchimento. Agora: acento removido, casamento por **radical**, ruído de
  catálogo descartado (`dados`, `tabela`, `base`, `contem`…) e relevância por **IDF local**
  (peso = 1/nº de colunas que o termo atinge naquela tabela; match no NOME pesa 3×). ⚠️ O
  **orçamento é estrito**: o teto vale para o card inteiro, inclusive estruturais e
  relevantes (antes "essencial" entrava sem teto) — é o que garante que melhorar a
  relevância **não engorde o prompt**. Prioridade: estruturais → relevantes → sementes →
  ordinal. Tetos: semente `EXPLORE_MAX_COLUMNS=10`, expansão `EXPLORE_MAX_COLUMNS_EXPANSION=6`.
  Medido: recall de coluna **0,489 → 0,575** com prompt médio −0,3% e p95 −0,9%
  (`scripts/measure_explore_context.py`). `EXPLORE_MAX_OMITTED_NAMES` (listar o NOME das
  colunas podadas) existe e está **desligado**: custa ~10% de prompt e o ganho ainda não é
  mensurável. 🔴 **A P2 tem o mesmo defeito de casamento** (tetos 25/6) — corrigir lá exige
  o gate do ADR-0012.
- **`ExploreAnswerBuilder`**: resposta em ordem de LEITURA — resumo direto →
  **tabela-resumo** (Markdown, agrupada por domínio, uma linha por tabela) → detalhe
  longo **só das destacadas** → JOINs (tabela) → perguntas sugeridas (tabela) → SQL de
  amostra → **o que não existe**. Duas invariantes: **nada fora do catálogo** (tabela
  citada pelo LLM que não existe é descartada e contada em `off_catalog_tables`) e
  **cobertura não limitada pelo LLM** (ele prioriza, não filtra — o resto do recuperado
  segue listado). `ranked_tables` é a ordem de apresentação e é o que o eval pontua.
  ⚠️ Célula de tabela passa por `_cell` (escapa `|`, corta em fronteira de palavra).
- **Pergunta de catálogo** ("quais bases vocês têm?"): o retriever devolve top-k para
  QUALQUER texto, então o caminho "sem sementes" **nunca dispara na prática** — sem
  tratamento, uma amostra do índice sairia apresentada como se fosse o catálogo.
  `_is_catalog_question` reconhece pela frase (com guarda negativa em "sobre/isso",
  que denuncia tema) e põe o **panorama determinístico** no topo; as tabelas
  recuperadas viram "uma amostra".
- **`SCOPE_GAPS`** (env JSON `{conceito: mensagem}`): transforma "refine a pergunta" na
  recusa informativa do item C2 ("não tenho execução orçamentária; tenho contratado e
  desembolsado"). **Vazio por default** → `gap_disclosure` mede pouco até ser preenchido.
- **Amostra de dados reais (ADR-0015)** ✅: o `sample_sql` é executado e a resposta traz
  **10 linhas** numa tabela + link do **CSV** (100 linhas) + a consulta que rodou.
  Escada em `components/samples/`: guarda `sqlglot` (uma instrução, leitura só, envelope
  `SELECT * FROM (…) LIMIT n`) → execução **direta** pelo MCP (0 LLM) → autocorreção do
  `Agent` com contexto **enxuto** (só a tabela alvo; `EXPLORE_SAMPLE_AUTOCORRECT`) →
  **reconstrução pelo catálogo** (`SELECT <colunas do Neo4j> FROM <tabela> LIMIT n`, 0 LLM),
  que é o degrau que **garante** amostra. `origin` ∈ {llm, corrigido, catalogo} vai no
  `answer["sample"]` e é o que o eval pontua. CSV `utf-8-sig` + `;` (abre no Excel pt-BR),
  bucket **`amostras`** e URL assinada **curta** (15 min) — separado dos relatórios porque
  é o primeiro conteúdo do sistema que **não é metadado**. ⚠️ Sem filtro de PII: a ingestão
  guarda `tags` por coluna, o gancho existe, não está implementado (escopo MCid é público).
- Sem gráfico e sem `.docx`. O caminho normal segue em **1 chamada de LLM** (a amostra usa o
  MCP direto); só a autocorreção da amostra gasta modelo, e só quando o banco recusa.

## Como rodar o eval do modo Explorar (Persona 1)

Benchmark próprio em `eval/explore/` — separado porque a **unidade do gabarito muda**
(conjunto graduado de tabelas, não subgrafo mínimo). Métricas de ranking
(`recall@k`, `core_recall@k`, `MRR@k`, `nDCG@k` graduado, `MAP@k` em k ∈ 5/10/15) +
contrato da persona (`catalog_fidelity`, `gap_disclosure`, `domain_recall`, …).

```bash
make explore-quick    # offline: contrato do gold + 42 testes do motor de métricas
make eval-explore     # live: chama o LLM, roda no container, já pontua
make explore-score    # repontua um run salvo, sem gastar LLM
```

⚠️ **`table_recall` é gameável** — listar o catálogo inteiro dá 1,0. Ler sempre com
`table_precision@k` e `listed_ratio` ao lado (assertado em `TestAntiGaming`).
Gold `mcid-explore-v0.1.0` = **`draft`, 18 casos, sem revisão de domínio** → diagnóstico,
não gate; o CLI marca o gate como não conclusivo enquanto não for `accepted`.

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
3. Entrega: **(a) a (d) implementados** — (c) explicação do resultado entrou em 21/08
   (`ResultExplainer`), (d) relatório `.docx` em python-docx + tema-em-código + MinIO.
   Round-trip do upload/URL validado ponta a ponta em 21/08 (link do MinIO baixado e o `.docx`
   conferido em PDF via LibreOffice).
4. **Guardrail de grain (E2E)**: `empreendimento`/`ficha_empreendimento` têm APF duplicado (1.646 linhas
   p/ 823 APFs) → o renderer soma duplicatas e **dobra valores** no gráfico. Dedup por APF + não somar.
5. Sem validador `sqlglot` (a validação vem da execução real). IR completa (`group_by`/`time_range`) + roteamento por conceito.

## Prompts (21/08)

Nenhum prompt mora mais em constante de módulo: todos estão em **`src/rag/prompts/*.md`**, um
arquivo por prompt, carregados por `prompts.load("<nome>")` (texto cru) ou
`prompts.render("<nome>", **vars)` (`str.format`). Os `*_user.md` das pipelines continuam sendo
**templates Jinja** renderizados pelo `ChatPromptBuilder`; os montados em Python usam
`{placeholder}` — por isso não podem ter chave literal (o `tests/test_prompts.py` cobra isso,
junto com "todo `load` aponta para arquivo que existe" e "nenhum arquivo ficou órfão").

⚠️ A migração foi **byte a byte** (os arquivos foram extraídos dos literais por AST e conferidos
contra o HEAD): nada do texto medido da Persona 2 mudou junto. Mexer num `query_*.md` é mudar
comportamento e pede o gate do ADR-0012, não é edição de formatação.

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
- **`src/rag/prompts/`** — **todo texto que vai para o LLM** (um `.md` por prompt) + o loader
  (`prompts.load` / `prompts.render`). Ver o `README.md` de lá para a tabela prompt → quem usa.
- `src/rag/components/answer/sql_answer_builder.py` — resposta em Markdown.
- `src/rag/components/answer/result_explainer.py` — parágrafo do resultado + `build_digest`.
- `src/rag/components/executors/mcp_sql_executor.py` — Agent + postgres-mcp.
- `src/rag/components/charts/` — `chart_spec.py`, `renderers.py`, `chart_renderer.py`.
- `src/rag/components/report/` — relatório `.docx` (estilo editorial): `theme.py` (capa
  full-bleed, tabelas, callouts, rodapé), `docx_writer.py` (Markdown→docx + badges de tipo),
  `report_content.py` (renderização estruturada), `report_builder.py`, `assets/`
  (`logo_govhub_light.png`, `symbol_govhub.png`, `fonts/Inter-*.ttf`).
- `src/rag/components/storage/minio_uploader.py` — upload do `.docx` no MinIO + URL assinada.
- `src/rag/pipelines/query/` (motor) e `pipelines/delivery/` (execução+gráfico); `settings/configs.py` (tudo tunável por env).
- **Persona 1 (ADR-0014)**: `components/retrievers/neo4j_catalog_retriever.py`,
  `components/answer/explore_answer_builder.py`, `pipelines/explore/`,
  `eval/explore/` (métricas + gold + política), `tests/test_explore_eval.py` (host) e
  `tests/test_explore_components.py` (precisa do Haystack → container).
- `scripts/inspect_retrieval.py` — debug do retrieval (sementes + subgrafo, sem LLM).
- `docs/adr/0013-*.md`, `eval/experiments/20260804-*` (Fase 1/2 + comparativo).

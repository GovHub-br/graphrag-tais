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

## Jornada do usuário — modelo `jornada` / "Assistente" (ADR-0020) ✅

Terceiro modelo, **padrão** no open-webui. A pessoa não escolhe persona: a
primeira resposta já sai para a **População geral** (frase com o número, número
em destaque com escala humana, pictograma, mapa por UF/série/comparação, fonte)
e os **botões** abaixo abrem as trilhas: técnica, política, gestor, história e
baixar. `Indicador` e `Explorar` seguem no seletor, intactos.
- **Sem emoji no Assistente** (decisão de produto, 01/11): rótulos e títulos são
  texto puro, e `text.strip_emoji` limpa a saída inteira — inclusive o que vem do
  dicionário do Indicador e do panorama do Explorar. Rótulo antigo com emoji
  ainda abre a trilha (`fold` ignora emoji).

- **Regra em `src/rag/journey/`, wrapper em `pipelines/jornada/`.** O motor da
  P2, o `McpSqlExecutor` e a pipeline do Explorar são **reusados como caixa-preta**
  (injetados no `JourneyService`) — nenhum arquivo do caminho medido mudou.
- **Botões = follow-ups do open-webui.** Ele chama o próprio modelo com
  `### Task: … "follow_ups"`; o wrapper responde sem LLM (`openwebui_tasks`). O
  clique volta como mensagem com o rótulo exato → `tracks.match_track` (igualdade
  do texto normalizado, **nunca** "contém"). ⚠️ Renomear um botão quebra o clique
  em conversas antigas. Título e tags também saem sem LLM.
- **Estado**: comentário Markdown invisível no fim de cada resposta
  (`[//]: # (tais:v1 ref=… rota=… trilha=… vistas=…)`) + resultado no **Redis**
  (`JOURNEY_STATE_TTL`, 7 dias). O clique reusa O MESMO resultado (sem SQL novo,
  exceto a série histórica da trilha de política). Estado vencido → recalcula e avisa.
- **Números só em Python** (`journey/facts.py`). LLM escreve 1 frase + legenda
  (`prompts/jornada_frase_*`), com guarda: a frase tem de conter o número
  formatado, a legenda não pode ter dígito. Escala humana = tabela fixa
  (`config/escala_humana.yml`) + IBGE Censo 2022 (`journey/data/`).
- **Unidade vem do catálogo, não da base** (`journey/units.py`, 02/11): a
  descrição da coluna que o motor devolve em `answer["references"]` diz "em
  milhões de reais", "número índice" etc. — regra de língua, sem nada de órgão.
  Escala multiplica os valores (a linha crua fica como veio, e a técnica avisa);
  índice não é somado nem abreviado ("1.283", não "1,3 mil"); `COUNT` não herda
  unidade. Sem descrição, vale o nome da coluna.
- **Guarda do total mal atribuído** (`render.misreads_total`): resultado somado
  + superlativo na frase do total ("o estado com mais tem 878") → frase determinística.
- **Relatório = o que a pessoa VIU** (03/11): cada trilha guarda seu Markdown em
  `state["sections"]`, e o `.docx` é montado delas, na ordem em que foram abertas
  (`render.report_markdown` → `ReportBuilder.build`). É refeito quando entra trilha
  nova. Antes era o relatório da P2 (SQL + dicionário), que não batia com a tela.
- **UI no celular** (webui): link para `/files/…` vira botão (`custom.css` §12);
  `loader.js` prende a mensagem enviada no topo enquanto a resposta chega (o app
  rolava até o FIM, e a pessoa caía nos botões). ⚠️ O Open WebUI copia o
  `static/` no boot: editar os arquivos montados pede `docker restart open-webui`,
  e o navegador guarda cache deles.
- ⚠️ **Tabela "mapa" com UF + região na mesma coluna** (`ouro_far_mapa_nacional`
  tem N/NE/CO/S/SE junto das UFs): linhas de região/total saem da soma e "SE"
  (Sergipe ou Sudeste?) sai junto — a resposta avisa.
- ⚠️ **Stream em pedaços de 16 KB**: com vários PNG base64 a resposta passa de
  128 KB e o open-webui recusa a linha SSE (`Got more than 131072 bytes`).
- Gráficos novos (PNG, desenhados para celular): `components/charts/infographics.py`.
- `JOURNEY_OPEN_DATA_URL` vazio = link de dados abertos oculto (o portal não existe ainda).
- Testes: `tests/test_journey_rules.py` (host) e `tests/test_journey_service.py` (container).
- Setup da UI: `scripts/setup_webui_models.sh` cria o "Assistente", põe como
  padrão e liga os follow-ups. Botões estilizados na seção 11 do `webui/custom.css`.

## Verdade da arquitetura: os ADRs

Todas as decisões estão em `docs/adr/` (**0000–0020**) — índice em `docs/adr/README.md`.
**Leia os ADRs antes de decidir algo de arquitetura.** Regra: um ADR só cita ADR de
número menor. Destaques recentes: **0011** (contrato da ingestão, substitui o 0005),
**0012** (eval orientado a métricas), **0013** (gráfico por chart-spec — revisa a
execução do 0008), **0017** (backend de execução configurável) e **0019** (volta ao
**postgres-mcp** com túnel SSH, porque o Trino descartava toda coluna `numeric` —
revisa a execução do 0013), **0018** (mascaramento de dado sensível na saída —
reabre a Alternativa C que o 0016 recusou). Antes de mexer em **retrieval**, respeite o
gate MDD: `AGENTS.md` + `skills/evaluate-retrieval-change/SKILL.md` (medir antes/depois).

## Stack

- **Haystack 3.0 + Hayhooks 1.22** (serve as pipelines como REST OpenAI-compat).
- **Qdrant** (vetor híbrido denso+esparso), **Neo4j 5** (grafo puro, Cypher), **Redis** (cache, ocioso).
- **postgres-mcp** (crystaldba, `--access-mode=restricted`) — executa o SQL gerado contra o
  PostgreSQL analítico (**ADR-0019**), alcançado por um **túnel SSH** que o serviço
  `postgres-tunnel` mantém (`ssh -N -L`, chave do host em bind read-only, healthcheck `nc -z`;
  o `postgres-mcp` só sobe com o túnel `healthy`). O RAG **não tem credencial de banco**:
  quem a segura é o container do MCP. O **trino-mcp** ficou atrás do profile `trino`.
- ⚠️ **O SQL gerado é PostgreSQL** (voltou a ser, no ADR-0019). `SQL_DIALECT` governa o
  `sqlglot`; as regras de dialeto que o LLM segue são TEXTO em `prompts/query_system.md`.
  Trocar de backend é trocar `SQL_MCP_*` + `SQL_DIALECT` **e** os prompts — não só a URL.
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
| **PostgreSQL** analítico (via `postgres-mcp` + túnel SSH) | **os dados reais** | ✅ **sim** — banco analítico à parte |

O RAG só conhece o *schema*. As **linhas** vivem no **banco analítico**, alcançado pelo
**postgres-mcp através de um túnel SSH** (ADR-0019; o ADR-0017 passou pelo Trino no meio do
caminho). Os nomes físicos `schema.tabela` batem com o SQL gerado (a serialização usa o nome
físico, não o FQN do catálogo).
⚠️ **A premissa a vigiar é que o catálogo descreve o MESMO banco que executa o SQL.** Ela já
falhou uma vez, em silêncio: com o Trino no meio, 390 colunas que o OpenMetadata publicava
não existiam para quem executava (ADR-0019). Divergência aí se conserta na fonte, não no
prompt — e um gate que compare o catálogo com o `information_schema` do alvo continua sendo
a forma barata de descobrir isso em tempo de ingestão, não em produção.

## Como subir e rodar

1. Docker Desktop ligado. **VPN/rede da lab conectada** (ver Gotchas). `.env` na raiz —
   ⚠️ **sem cercas markdown (```) no `.env`** (quebra o `docker compose`).
2. `make up` → rebuild + sobe **qdrant, neo4j, minio, postgres-tunnel, postgres-mcp, hayhooks**
   (NÃO sobe redis nem webui). O túnel precisa de `PRIVATE_KEY_PATH` e do bloco `TUNNEL_*` no `.env`.
   O `postgres-mcp` está atrás do profile `postgres` e **não sobe** — de propósito: sem
   `ANALYTICS_DB_*` preenchidas ele fica em loop de restart.
   MinIO (armazenamento do relatório, ADR-0010): API/S3 `localhost:9000`, console `localhost:9001`
   (user/senha = `MINIO_ACCESS_KEY`/`SECRET`).
   ⚠️ Imagem `pgsty/minio` (fork comunitário): a `minio/minio` saiu do Docker Hub e do quay.io em set/2026.
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

### Camada de entrega — execução + gráfico (ADR-0013/0017/0019) ✅

Pipeline SEPARADA `pipelines/delivery/` que o `run_chat_completion` compõe (motor fica reusável):
- **`McpSqlExecutor`**: roda o SQL via **Haystack `Agent` + `MCPToolset`**, read-only, com
  **autocorreção no erro** até `AGENT_MAX_STEPS`. **Agnóstico de backend** (ADR-0017): URL,
  transporte, nome da tool, nome do argumento e dialeto vêm de `SQL_MCP_*`/`SQL_DIALECT`.
  Default = **postgres-mcp** (`http://postgres-mcp:8000/sse`, `sse`, `execute_sql(sql=…)`),
  alcançando o banco por **túnel SSH** (ADR-0019). O trino-mcp continua disponível atrás do
  profile `trino`, para comparação explícita.
  ⚠️ **Por que voltou do Trino** (ADR-0019): o conector `postgresql` do Trino **descarta em
  silêncio** coluna `numeric` sem precisão/escala declaradas. No escopo MinC isso apagava
  **390 colunas (100% das NUMERIC, 34% das tabelas)** — ou seja, TODA coluna de valor
  monetário. O catálogo prometia a coluna, o LLM escrevia SQL correto e o banco respondia
  `Column cannot be resolved`; a resposta saía como "SQL gerado", sem gráfico, parecendo
  erro do modelo.
  ⚠️ **Transporte não se deduz do path**: o `/sse` do trino-mcp é a MESMA rota Streamable
  HTTP do `/mcp` — não é o SSE clássico que o postgres-mcp (legado, `execute_sql(sql=…)`) fala.
  ⚠️ **Duas serializações, um parser**: trino devolve **JSON** e marca `isError=True`;
  postgres devolve **repr Python** (Decimal/datetime) e erro como TEXTO. `_parse_rows` aceita as duas.
  ⚠️ **`_order_columns`**: o trino-mcp serializa cada linha de um `map` do Go, e o
  `encoding/json` ordena chave **alfabeticamente** — `SELECT uf, total` voltaria `total, uf`
  na tabela, no CSV e no `.docx`. A ordem é reconstruída da projeção do próprio SQL (inclusive
  desembrulhando o envelope da amostra); em `SELECT *` ou parse falho, fica o que veio.
  ⚠️ **`DECIMAL` chega como string** (o driver Go do Trino não tem decimal). NÃO convertemos
  em `_normalize_row`: "12345" tanto é `SUM` quanto código de IBGE. Quem precisa de número já
  coage — `_infer_kinds`, `pd.to_numeric`, `_as_float`.
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

⚠️ **Resultado truncado AVISA** (08/09). O corte em `MAX_EXEC_ROWS` (1.000) era
mudo: gráfico, parágrafo do resultado e `.docx` eram montados sobre a primeira
fatia com a mesma confiança de sempre — "gastos por município" (5.570 linhas)
saía com 1.000 e cara de completo. O executor devolve `truncated` (calculado
ANTES do corte, então `LIMIT 100` que devolve 100 não é truncamento) e a
resposta ganha uma frase. Não confundir com o teto do SERVIDOR
(`TRINO_MAX_ROWS`, 5.000, no compose): esse fica acima do da aplicação e nunca
morde primeiro. Nenhum dos dois limita **agregação** — `COUNT`/`SUM` são feitos
pelo Trino sobre a tabela inteira e devolvem poucas linhas.

⚠️ **Erro de execução NÃO vai para a resposta** (decisão de produto, 19/08): sem mensagem de
banco no chat nem callout no `.docx` — quem lê quer a consulta, não o diagnóstico. O erro fica
no `delivery["exec_error"]` e num `logger.warning` do `_with_delivery`; **é lá que se debuga**
uma resposta que saiu sem gráfico. Mesma regra no `.docx` (`report_content.render_answer`).

⚠️ **`_scan_executions` (era `_last_successful_execution`)**: o postgres-mcp devolve erro de banco
como TEXTO com `isError=False`, então filtrar por `res.error` fazia "última bem-sucedida" virar
"última tentativa" — um resultado bom seguido de uma tentativa recusada era descartado (resposta
sem gráfico à toa) e o `sql_final` podia ser um SQL que nunca rodou. Agora a função devolve
`(ok, failed)`, decidindo o que é utilizável pelo `_parse_rows` (fonte única). Com o trino-mcp o
erro VEM marcado, e o Haystack o entrega como `error=True` com a mensagem no `result` — a
tentativa falha carrega o texto do banco, não um genérico. A varredura casa os nomes de tool
dos DOIS backends, para um trace antigo continuar legível.

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
(adicionado ao stack agora) via `boto3`.

⚠️ **Download é servido pelo PRÓPRIO RAG, não pelo MinIO** (27/08). O navegador do usuário
**não alcança o MinIO**: ele só existe na rede do compose, sem URL pública — e a URL assinada
do S3 embute o host, então o link apontava para um endereço inalcançável fora do Docker
(quebrava em produção, onde só o RAG tem URL pública). Agora o RAG é o **proxy**:
`GET /files/{bucket}/{objeto}?exp=…&sig=…` (`src/rag/server/downloads.py`), com assinatura
**própria** (HMAC-SHA256 de bucket+objeto+expiração, `components/storage/download_links.py`),
`PUBLIC_BASE_URL` = a URL pública do RAG que entra no link, e a validade seguindo nas configs
de sempre (`REPORT_PRESIGN_EXPIRY` 7 dias, `SAMPLE_PRESIGN_EXPIRY` 15 min). Vale para os dois
buckets (`relatorios` e `amostras`). Assinatura inválida → 403, vencida → 410, bucket fora do
par conhecido → 404. **`MINIO_PUBLIC_ENDPOINT` deixou de existir.**

⚠️ **Por isso o compose não sobe mais `hayhooks run`**, e sim `uvicorn rag.server.app:create_app
--factory`: o Hayhooks não tem gancho para rota custom, e `src/rag/server/app.py` é o app dele
(`create_app()`) + as nossas rotas. É a mesma execução que o `hayhooks run --reload` faz por
baixo; a config vai por env (`HAYHOOKS_PIPELINES_DIR`, `HAYHOOKS_ADDITIONAL_PYTHON_PATH`,
`PYTHONPATH=/app/src`). Rota nova do projeto entra em `rag/server/`.

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
  Escada em `components/samples/`: guarda `sqlglot` **no dialeto de `SQL_DIALECT`** (uma
  instrução, leitura só, envelope `SELECT * FROM (…) LIMIT n`) — como ela REEMITE a consulta,
  ela também traduz Postgres-ismo simples (`uf::text` → `CAST(uf AS VARCHAR)`) →
  execução **direta** pelo MCP (0 LLM) → autocorreção do
  `Agent` com contexto **enxuto** (só a tabela alvo; `EXPLORE_SAMPLE_AUTOCORRECT`) →
  **reconstrução pelo catálogo** (`SELECT <colunas do Neo4j> FROM <tabela> LIMIT n`, 0 LLM),
  que é o degrau que **garante** amostra. `origin` ∈ {llm, corrigido, catalogo} vai no
  `answer["sample"]` e é o que o eval pontua. CSV `utf-8-sig` + `;` (abre no Excel pt-BR),
  bucket **`amostras`** e link assinado **curto** (15 min, servido pelo RAG) — separado dos relatórios porque
  é o primeiro conteúdo do sistema que **não é metadado**. ⚠️ A amostra **passa pelo mascaramento do
  ADR-0018** como qualquer outro resultado (o executor é o mesmo): coluna listada no
  `config/sensitive_columns.yml` sai pseudonimizada na tabela E no CSV. As `tags` de
  coluna da ingestão seguem **não persistidas** — a fonte da sensibilidade é o
  arquivo, não o catálogo (fase 2 do 0018).
- Sem gráfico e sem `.docx`. O caminho normal segue em **1 chamada de LLM** (a amostra usa o
  MCP direto); só a autocorreção da amostra gasta modelo, e só quando o banco recusa.

### Guardrails de resposta (ADR-0016) ✅

Duas camadas, valendo para as **duas personas**:

1. **Gate determinístico** (`src/rag/guardrails/`, função pura — roda no host, sem
   Haystack) chamado no topo do `run_api`/`run_chat_completion` dos dois wrappers,
   **antes** de embedding, grafo, LLM e execução. Quatro veredictos:
   `pii_request` (recusa pedido de CPF/CNPJ/nome/endereço/contato + oferece o
   recorte agregado), `data_access` ("você acessa o banco?" → o modelo lê
   metadados e **escreve o SQL**; quem executa é o banco, read-only; a explicação
   do resultado vem do digest, não das linhas), `off_scope` (fora de explorar/medir,
   inclui tentativa de reprogramar o assistente) e `about_system` ("o que você
   faz?" → apresenta os dois modos). Pergunta barrada **não custa nada**.
   ⚠️ O filtro de PII é **termo + contexto**: `cpf`/`nome de pessoa`/`endereço`
   recusam, **exceto** com marcador de metadado ("quais tabelas têm CPF?" é o
   trabalho da P1) ou de agregação. Acima das exceções há uma lista curta que
   recusa sempre (`liste os CPFs…`, `relação nominal`).
   `GUARDRAIL_EXTRA_PII_TERMS` (env) acrescenta vocabulário por base.
   ⚠️ **CNPJ/razão social de PJ não bloqueiam sozinhos** — empresa contratada
   pelo poder público é informação pública, e barrar isso quebrava um caso do
   gold de negócio ("as 10 EOs de alto risco; mostre nome, CNPJ…"). Bloqueia só
   colado em pessoa física (sócio, representante legal, titular).
   ⚠️ Os **textos** ficam em `guardrails/messages.py`, **não** em `prompts/`:
   `prompts/` é o que vai para o modelo; recusa é resposta pronta, e o valor dela
   é não depender de o modelo obedecer.
2. **Recusa em banda**: os prompts de sistema das duas personas ganharam bloco de
   RECUSAS e o campo `refusal` (`"" | pii | escopo`) no JSON. Preenchido → o
   answer builder renderiza **só a explicação** (sem SQL, sem dicionário, sem
   amostra, sem `.docx`). `result_summary_system` e `sql_executor_system` também
   proíbem dizer que o modelo consultou/acessou o dado.

`tests/test_guardrails.py` (host) cobre os dois lados: o que **tem** de ser
recusado e o que **não pode** ser — "mostre nome, CNPJ das EOs", "quais tabelas
têm CPF", "nome do município" continuam passando.

✅ **Validado em ambiente real (31/08)**: 50 chamadas no contrato de chat, nas
duas personas, **50/50**. Os testes reais acharam 3 defeitos que a suíte não
pegava (CNPJ de PJ bloqueando, `refusal` usado para falta de dado, verbos de
posse faltando em `data_access`) — todos corrigidos, cada um com seu caso na
suíte. Ver a seção de validação do ADR-0016.

🔴 **Pendências**: (a) o gate do ADR-0012 — `query_system.md` é prompt calibrado e
a mudança **não está medida**; (b) na autocorreção do `McpSqlExecutor` (só quando
o banco recusa o SQL) o retorno da ferramenta volta ao contexto do modelo, então
ali ele vê linhas — o caminho normal (`MCP_DIRECT_FIRST`) não passa por lá.
✅ (b) deixou de valer nas tabelas do catálogo de sensibilidade: ver ADR-0018.

### Mascaramento de dado sensível na saída (ADR-0018) ✅

Terceira camada, complementar às duas acima: o guardrail protege a **conversa**
(recusa o pedido antes da pipeline); isto protege o **valor**. Existe porque o
acesso mascarado do banco — que o ADR-0016 tratou como a proteção de fato — é
configurado **por base**, e o RAG passa a responder sobre uma base antes de ela
entrar naquele controle. O 0016 tinha deixado a cláusula de reabertura escrita.

- **Ponto único: `McpSqlExecutor._protect`.** Toda linha real do sistema passa
  por ali (duas personas, caminho direto e do agente), então mascarar no
  executor cobre de uma vez tabela do chat, gráfico, digest do
  `ResultExplainer`, CSV da amostra, `.docx` — e o **LLM**, que hoje recebe 3
  linhas no prompt do `ChartSpecBuilder`. Mascarar no renderer protegeria só a
  tela, em cinco lugares diferentes.
- **Catálogo em arquivo**: `config/sensitive_columns.yml` (YAML ou JSON),
  montado read-only (`./config:/app/config:ro`), `SENSITIVE_MAP_PATH`. Relido
  quando o `mtime` muda. Aceita três formatos na leitura (canônico, mapa cru
  `{"schema.tabela": [colunas]}`, lista de registros) porque o mapeamento nasce
  fora do repo. ⚠️ **Nome físico `schema.tabela`**, não FQN do OpenMetadata (o
  FQN é aceito e reduzido aos 2 últimos segmentos). **Vazio = nada mascarado**,
  que é o comportamento anterior ao ADR.
- **`hash` (default) pseudonimiza, não apaga**: HMAC-SHA256 truncado,
  `#a3f9c1d20b`, escopo **global** — mesmo valor, mesmo código em qualquer
  tabela, então `COUNT(DISTINCT)`, `GROUP BY` e cruzamento continuam
  funcionando. `redact` (`[protegido]`) para o que não deve nem ser contado.
  ⚠️ **Sem `SENSITIVE_HASH_PEPPER` não gera hash** — CPF tem 10¹¹ valores e
  hash sem segredo é reversível; degrada para `redact` e loga erro.
- ⚠️ **Agregação NUNCA é mascarada.** `COUNT(cpf)` é a resposta certa para
  "quantas pessoas" — mascarar isso não protegeria ninguém e quebraria a
  Persona 2. O `resolver` lê a projeção com `sqlglot`: alias e expressão
  escalar (`CAST(cpf AS VARCHAR)`) **carregam** a sensibilidade; agregação não.
  Na dúvida (`SELECT *`, coluna sem qualificador em JOIN, parse falho) **fecha
  em favor da privacidade**.
- **O retorno da tool é mascarado antes de virar contexto do modelo**
  (`_mask_toolset`): fecha a pendência (b) acima SEM abrir mão da autocorreção —
  o agente corrige lendo a mensagem de erro, nunca precisou dos valores. A
  mensagem de erro passa intacta e falha no mascaramento FECHA (devolve erro).
  ⚠️ **Instalar só DEPOIS do `agent.warm_up()`**: o `MCPToolset` nasce com uma
  tool placeholder e a SUBSTITUI ao conectar (`self.tools = self._connect_and_load_tools()`)
  — envolver antes protege o objeto descartado e o log mente dizendo que
  envolveu. `_protect` então usa `apply_values=False` nesse caminho, senão sai
  hash de hash e o mesmo CPF ganha códigos diferentes conforme o caminho.
  `SENSITIVE_INHIBIT_AUTOCORRECT` (default **false**) é cinto além do
  suspensório: ligar custa a autocorreção em toda consulta às tabelas do
  catálogo, inclusive nas agregadas.
- **Coluna mascarada não vira eixo de gráfico** (20 barras de hash não são um
  gráfico).
- **A resposta diz que mascarou** (`render_masking_note`, texto único para as
  duas personas + callout no `.docx`). Ao contrário do erro de execução, que
  fica no log de propósito, esta limitação **tem** de aparecer: `#a3f9c1d20b`
  sem explicação é lido como defeito.
- **Latência**: nula com catálogo vazio (`plan` retorna antes de parsear o SQL),
  poucos ms com catálogo cheio (1 parse + HMAC memoizado por valor). **Não toca
  retrieval nem `query_system.md` → não dispara o gate do ADR-0012**, e isso é
  razão de projeto: é por isso que a proteção entra na execução, não na geração.
- `SENSITIVE_MASK_DRY_RUN=true` calcula e **loga** o plano sem alterar valor —
  é como se calibra um catálogo novo em produção.

🔴 **Lacuna de cobertura conhecida** (`scripts/audit_sensitive_map.py`, 08/09):
o catálogo cobre o que o **dbt modela**; o Trino tem mais. Das 19.132 colunas dos
schemas do MinC, **380 sensíveis cobertas e 249 descobertas** — `telefone`/`e-mail`
(natureza que o inventário não classifica) e `cgccpf`/`cnpjcpf` em views e
temporárias fora do dbt. Fecha-se com a lista `columns:`, mas preenchê-la é
decisão de domínio: telefone de prefeitura é público. ⚠️ Ao reauditar, **pagine**
— o trino-mcp corta em 5.000 linhas sem avisar.

`tests/test_sensitivity.py` (host, 59 casos) cobre os dois lados, como o do
guardrail: o que **tem** de ser mascarado (alias, `CAST`, `SELECT *`, envelope
da amostra, JOIN sem qualificador) e o que **não pode** ser (`COUNT(cpf)`,
tabela fora do catálogo, coluna resolvida por apelido para outra tabela).
Mascarar demais não vaza nada, mas transforma indicador em hash em silêncio.

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

- **Rede `10.0.0.x` / VPN**: Ollama (`10.0.0.10`) e o Trino só respondem com a **VPN/rede da lab
  conectada**. Se der timeout nesses dois mas a internet funcionar, é a VPN caída — bloqueia
  embeddings (retrieval) e execução de SQL (gráfico).
- **trino-mcp**: ele faz `Ping` no Trino no boot e **sai com `log.Fatal`** se não conectar —
  com `restart: unless-stopped` isso vira loop de restart (sintoma de VPN caída, host errado ou
  senha errada; o motivo está em `docker logs trino-mcp`). De pé,
  `curl localhost:8002/status` responde `{"status":"ok"}`.
  Erro de certificado ⇒ `TRINO_SSL_INSECURE` (a INVERSA de `TRINO_VERIFY_TLS`).
  ⚠️ Reiniciar o `trino-mcp` NÃO basta se você mudou `SQL_MCP_*`: essas são do hayhooks
  (`docker compose up -d --no-deps hayhooks`).
- **OpenAI quota**: se aparecer `429 insufficient_quota`, a conta OpenAI ficou sem crédito → geração de
  SQL/gráfico quebra (retrieval não usa OpenAI). Recarregar ou trocar `OPENAI_API_KEY` + recriar hayhooks.
- **Memória (OOM)**: Docker Desktop com ~7.75 GiB não roda **Airflow + RAG + open-webui** juntos; o
  webui morre com exit 137. Parar o Airflow libera ~3 GiB: `docker stop $(docker ps -aq --filter name=rag-airflow-)`.
- **`docker cp`**: se o destino já existe, aninha (`/app/eval/eval`). Sempre `rm -rf` antes.
- **hayhooks `--reload`** observa `src/` (mudança de componente recarrega), mas mudança no **`.env`**
  exige recriar: `docker compose up -d --no-deps hayhooks`.
- **`.env`**: sem cercas markdown; segredos reais (OPENAI/DB/OM_JWT) ficam nele e ele é gitignored.
- 🔴 **`PUBLIC_BASE_URL` ausente = download quebrado, e em silêncio.** É a única
  peça do download que o RAG não descobre sozinho: o link é montado dentro da
  pipeline, longe da requisição, então não dá para deduzir o host do `Host:`
  recebido. Faltando no `.env`, o default `http://localhost:8000` entra no link —
  o `.docx` é gerado, sobe no MinIO, a rota `/files` responde, **e ainda assim
  ninguém baixa**, porque no navegador de quem clicou `localhost` é a máquina
  dele. Desde 01/09 o boot **avisa** (`create_app` → `_check_public_base_url`,
  com `download_links.base_url_looks_local`); antes o sintoma era só "o botão não
  faz nada". Em produção: `PUBLIC_BASE_URL=https://<domínio público do RAG>`, e o
  proxy reverso precisa encaminhar **`/files`** junto com as rotas de pipeline.
  Diagnóstico rápido: **link some** = MinIO/upload fora (`_attach_report` é
  defensivo e engole); **link 404/inalcançável** = `PUBLIC_BASE_URL` errado.

## Mapa de arquivos (novos/tocados na branch, não commitados)

- `src/rag/components/retrievers/` — `neo4j_subgraph_retriever.py` (SEMANTIC_JOIN + poda + evidence),
  `qdrant_hybrid_retriever.py` (scope + determinismo).
- `src/rag/components/generators/openai_generator.py` — LLM OpenAI.
- **`src/rag/guardrails/`** (ADR-0016) — gate determinístico de PII/escopo/acesso
  (`policy.py`, puro) + os textos das recusas (`messages.py`, resposta ao usuário,
  fora de `prompts/`). Chamado pelos DOIS wrappers antes da pipeline.
- **`src/rag/sensitivity/`** (ADR-0018) — mascaramento na saída, tudo função pura:
  `catalog.py` (lê/indexa o `config/sensitive_columns.yml`), `resolver.py` (`sqlglot`:
  de qual `(tabela, coluna)` veio cada coluna do resultado) e `mask.py` (decide a regra
  e aplica HMAC). Chamado de UM lugar só: `McpSqlExecutor._protect`.
- **`config/sensitive_columns.yml`** — o mapeamento de colunas sensíveis. Fora da imagem,
  montado read-only; **é o único arquivo do repo que muda a proteção sem mudar código**.
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
- `src/rag/components/storage/` — `minio_uploader.py` (upload no MinIO) e `download_links.py`
  (assina/valida o link `/files` que o RAG serve).
- `src/rag/server/` — app do projeto: `app.py` (Hayhooks + rotas próprias) e `downloads.py`
  (rota `/files/{bucket}/{objeto}`).
- `src/rag/pipelines/query/` (motor) e `pipelines/delivery/` (execução+gráfico); `settings/configs.py` (tudo tunável por env).
- **Persona 1 (ADR-0014)**: `components/retrievers/neo4j_catalog_retriever.py`,
  `components/answer/explore_answer_builder.py`, `pipelines/explore/`,
  `eval/explore/` (métricas + gold + política), `tests/test_explore_eval.py` (host) e
  `tests/test_explore_components.py` (precisa do Haystack → container).
- `scripts/inspect_retrieval.py` — debug do retrieval (sementes + subgrafo, sem LLM).
- `docs/adr/0013-*.md`, `eval/experiments/20260804-*` (Fase 1/2 + comparativo).

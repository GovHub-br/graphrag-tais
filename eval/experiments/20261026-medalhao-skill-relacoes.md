# MCid medalhão (prata+ouro): relações da skill e ajustes de consumo no retrieval

Iniciado em 2026-10-26 · última medição em 2026-11-03 (rodada 10) · **atualizado em 2026-11-03**

**Pergunta.** O catálogo de relações gerado pela skill `mapear-joins-semanticos`
(porte do gerador do MinC para PostgreSQL, rodado no banco do MCid) melhora o
retrieval sobre o layout medalhão? Se não, o que no consumo precisa mudar?

**Resposta curta (atualizada).** Sozinhas, as relações **pioram** a Persona 2:
o texto dos cards muda e a expansão inunda o prompt (rodada 1). Com quatro
ajustes de consumo, todos atrás de flag, passam a **resolver um caso a mais**
com o prompt sob controle: card sem a lista de relacionamentos, roteamento de
sementes por produto, expansão por PageRank com orçamento 1 e poda estrita
25/15. O resultado se manteve na rodada 7, com o código já sem nada específico
do MCid e sobre um catálogo que cresceu de 253 para 325 tabelas. No Explorar o
efeito é neutro. A linha "Produto:" no card **não** deve ser ligada.

**Atualização (rodadas 8 e 9).** Um gold de 59 casos derivado do Superset
mostrou que o gargalo é a **busca**: em metade das perguntas nenhuma tabela
certa entra nas sementes. Dois ajustes na busca — fusão RRF com peso 0,7 para
o canal denso e peso para a camada `ouro` — levam o gold de negócio a **7 de
8** (era 5 de 8 na baseline de 30/10) e o do Superset de 34 para **37 de 59**,
com MRR +0,08 e sem crescer o prompt. Linhagem e reranker foram medidos e não
compensam agora.

**Atualização (rodada 10).** Metade das "falhas" do Superset era do **gabarito**:
8 casos tinham outra tabela que dá o mesmo resultado no banco, e 3 SQLs de
referência estavam errados (somavam a linha de total, 2× ou 4×). Com o gold
corrigido (v0.2.0), a configuração da rodada 9 resolve **45 de 51** casos na
leitura principal. O BM25 não separava palavras unidas por `_` (`ouro_far_contrapartida`
era um termo só); com `SPARSE_FOLD_ACCENTS` o canal BM25 sozinho acha a tabela
certa em **45 de 59** casos (eram 20), a fusão volta ao peso igual, e o gold
sobe para **46 de 51** com MRR +0,05 e prompt p95 −7%.

Golds em `draft` (sem revisão de domínio, 8 casos na P2 e 18 no Explorar):
números são **diagnóstico, não gate**. Na P2 cada caso vale 0,125 de
suficiência.

## Resumo consolidado (2026-10-31)

### O que as métricas medem

Persona 2 (Indicador), por pergunta do gold, médias sobre os 8 casos:

| métrica | o que é | melhor |
|---|---|---|
| **suficiência** (`structural_sufficiency`) | fração dos casos em que o contexto entregue ao LLM tem **todas** as tabelas e JOINs do gold — dá para escrever o SQL certo | maior |
| **hit@5** (`seed_hit_at_5`) | fração dos casos em que alguma tabela do gold está entre as 5 sementes da busca | maior |
| **MRR@10** (`mrr_at_10`) | *Mean Reciprocal Rank*: para cada caso, 1 ÷ posição da primeira tabela certa no ranking da busca (1º → 1; 2º → 0,5; 3º → 0,33; fora do top-10 → 0), e a média dos casos. Mede se a tabela certa vem **no topo** | maior |
| **tabelas** (`context_document_count_mean`) | tabelas que chegam ao prompt (sementes + expansão) | menor, sem perder suficiência |
| **prompt p50 / p95** | tamanho do prompt em caracteres na mediana e nos 5% mais pesados — custo e latência do LLM | menor |
| `pcrS` (só bancada) | fração das colunas do gold que sobrevivem à poda nos casos suficientes | maior |

Persona 1 (Explorar), 18 casos, a ordem da resposta é o ranking: **recall@10**
(fração das tabelas relevantes entre as 10 primeiras listadas), **core@10**
(o mesmo, só para as tabelas essenciais), **MRR@10** (como acima),
**precisão@10** e **`listed_ratio`** (fração do catálogo listada; vigia que o
recall não suba só por listar tudo).

### Antes × depois — Persona 2

"Baseline" é o que roda hoje: relações do OM (11), card com a lista de
relacionamentos, sementes pela busca, expansão `evidence`, poda `legacy` 25/6.
"Final" é a recomendação: relações da skill + card sem lista + roteamento por
produto + `ppr` orçamento 1 + poda `strict` 25/15, sem linha de produto.

| métrica | baseline 26/10 (253 tabelas) | baseline 30/10 (325 tabelas) | **final 30/10** | Δ final × baseline 30/10 |
|---|--:|--:|--:|--:|
| suficiência | 0,750 (6/8) | 0,625 (5/8) | **0,750 (6/8)** | **+0,125 (+1 caso)** |
| hit@5 | 0,750 | 0,625 | 0,625 | 0 |
| MRR@10 | 0,588 | 0,521 | **0,580** | **+0,059 (+11%)** |
| tabelas no prompt | 4,6 | 4,8 | 5,2 | +0,4 |
| prompt p50 | 15.017 | 14.858 | 18.820 | +27% |
| prompt p95 | 22.238 | 23.445 | 25.783 | +10% |

Leitura:

- **Baseline 26/10 → 30/10 (mesmo código, catálogo novo): piorou sozinha.** O
  OM ganhou 79 tabelas e perdeu 7. Uma delas,
  `ouro_reloginho_resumo_gargalo_desempenho_dashboard`, tirou
  `ouro_fds_panorama_entidade` do top-5 em `fds-eo-risco-001`; outra tirou a
  tabela certa do 1º lugar em `conjuntura-custos-001`.
- **Baseline 30/10 → final:** +1 caso suficiente e MRR +0,059. O prompt cresce
  10% no p95, porque entra a tabela de expansão que resolve o caso do FDS e as
  tabelas do produto certo têm mais colunas.
- **Contra as relações da skill com a configuração atual** (o que aconteceria
  se só as arestas fossem publicadas), o ganho dos ajustes de consumo é maior:
  suficiência 0,625 → 0,750, MRR 0,518 → 0,580, tabelas 8,1 → 5,2, prompt p95
  33,0k → 25,8k (−22%).
- O melhor resultado do dia 26 (0,875 / MRR 0,750, com linha `label`) **não se
  repetiu** no catálogo novo: ver rodada 7.

Por caso (suficiência · MRR@10):

| caso | baseline 26/10 | baseline 30/10 | final 30/10 | o que mudou |
|---|---|---|---|---|
| `conjuntura-cbic-mcmv-001` | ✅ 1,00 | ✅ 1,00 | ✅ 1,00 | — |
| `conjuntura-custos-001` | ✅ 1,00 | ✅ 0,50 | ✅ 0,50 | tabela nova do catálogo passou à frente |
| `conjuntura-pnad-ocupacao-renda-001` | ✅ 1,00 | ✅ 1,00 | ✅ 1,00 | — |
| `far-ranking-valor-001` | ✅ 0,50 | ✅ 0,50 | ✅ **1,00** | roteamento por produto pôs a tabela do FAR em 1º |
| `far-ufs-uh-001` | ❌ 0 | ❌ 0 | ❌ 0 | `ouro_far_mapa_nacional` nunca vira semente (card) |
| `fds-entrega-ts-001` | ✅ 1,00 | ✅ 1,00 | ✅ 1,00 | — |
| `fds-eo-risco-001` | ✅ 0,20 | ❌ 0,17 | ❌ 0,14 | tabela `reloginho` nova empurrou a certa para 6º |
| `fds-evolucao-ritmo-001` | ❌ 0 | ❌ 0 | ✅ 0 | **resolvido**: aresta da skill + expansão PPR trazem o chart |

`fds-evolucao` fica suficiente com MRR 0: a tabela que faltava não vem da busca,
vem da expansão pelo grafo — que é exatamente o papel das relações.

### Antes × depois — Persona 1

| métrica | baseline 26/10 | baseline 30/10 | final 30/10 (skill + roteamento) | Δ × baseline 30/10 |
|---|--:|--:|--:|--:|
| recall@10 | 0,418 | 0,459 | 0,496 | +0,037 |
| core@10 | 0,639 | 0,667 | 0,694 | +0,027 |
| MRR@10 | 0,593 | 0,607 | 0,580 | −0,027 |
| precisão@10 | 0,131 | 0,140 | 0,148 | +0,008 |
| `listed_ratio` | 0,045 | 0,038 | 0,054 | +0,016 |
| `catalog_fidelity` | 1,000 | 1,000 | 1,000 | 0 |

Diferenças de ±0,03 com 18 casos e o LLM ordenando as tabelas: **ruído**. Na
rodada 7 o roteamento mudou o ranking em 11 de 18 casos, inclusive nos que não
citam produto. O Explorar não ganha nem perde com os ajustes.

### Recomendação

| alavanca | flag | ligar? |
|---|---|---|
| relações da skill | publicação no OM (fora do RAG) | sim, **depois de regerar** sobre o catálogo atual |
| card sem lista de relacionamentos | `OM_CARD_EMBED_RELATIONSHIPS=false` | sim, junto com as relações |
| roteamento por produto na P2 | `SEED_PRODUCT_ROUTING=true` (+ `SEED_PRODUCT_LABEL_STOPWORDS=empreendimento` no MCid) | sim |
| expansão PPR, orçamento 1 | `EXPANSION_MODE=ppr`, `EXPANSION_BUDGET=1` | sim, junto com as relações |
| poda estrita 25/15 | `PRUNE_MODE=strict`, `PRUNE_EXPANSION_TABLE_MAX_COLS=15` | sim |
| roteamento no Explorar | `EXPLORE_PRODUCT_ROUTING` | indiferente (neutro) |
| linha "Produto:" no card | `OM_CARD_PRODUCT_LINE` | **não** (`off`) |
| fusão ponderada denso/BM25 | `HYBRID_DENSE_WEIGHT=0.7` | só **sem** o fold; com o fold, peso igual (deixar sem a variável) (rodada 10) |
| BM25 sobre texto sem acento e sem `_` | `SPARSE_FOLD_ACCENTS=true` (ingestão **e** consulta; exige reindexar) | **sim** (rodada 10) |
| peso para a camada de consumo | `SEED_SCHEMA_BOOST={"ouro":1.0}` (valor do MCid, no `.env`) | **sim** (rodada 9) |
| uma semente por tabela | `SEED_DISTINCT_TABLES` | não (neutro) |
| reranker cross-encoder | `RERANK_ENABLED` | não nesta infraestrutura (memória; MRR caiu) |

Tudo isto passa pelo gate do ADR-0012, e o gate pede o gold revisado.

### Código ao fim da rodada 7

- Tudo atrás de flag, com o comportamento padrão inalterado.
- Nenhum vocabulário do MCid no código: o que é do escopo mora no `.env`
  (`OM_SCOPE_ID`, adapter e propriedades de relação,
  `OM_LINEAGE_ONLY_MARKERS`, `SEED_PRODUCT_LABEL_STOPWORDS`). Os padrões são
  neutros.
- 372 testes OK sem `.env`, com o `.env` do MCid e com o `minc.env`.

### Mudanças por causa do contrato de dados do OpenMetadata

Com a migração do MCid para o medalhão, o que o OpenMetadata publica mudou de
forma. Parte disso impedia a ingestão; parte não quebrava nada, mas mudava o
que o retrieval consegue distinguir.

| o que mudou no OM | efeito no RAG antes | o que mudou no código |
|---|---|---|
| A propriedade de relações (`mcidSemanticRelationships`) passou a trazer só **prosa de linhagem** ("**Lê de:** …", "**É lido por:** …"), escrita pelo `sincronizar_governanca.py` do repositório de cidades, em ~220 de 253 tabelas (26/10) | a ingestão **abortava** com `Contrato Markdown desconhecido` | a prosa é reconhecida pelos marcadores de `OM_LINEAGE_ONLY_MARKERS`, contada em `semantic_lineage_only_tables` e pulada sem virar aresta. Sem a variável, o contrato continua estrito: qualquer outra gramática segue abortando |
| A lista de tabelas relacionadas (`mcidRelatedTables`) passou a ser reescrita por outro processo e **diverge** das relações publicadas (2 relações em 26/10 e 30/10) | a ingestão **abortava** | a relação não confirmada fica de fora com aviso no log e conta em `semantic_relationships_unconfirmed`; as outras entram. Vale para qualquer escopo, sem flag |
| O **schema virou camada** (`prata`/`ouro`), compartilhada por todos os programas; o programa passou a ser o **Data Product** (238 de 253 tabelas) | o card dizia "Domínio: Camada ouro" e a busca não distinguia FAR de Rural | a ingestão pede `dataProducts` e grava `data_product`, `data_product_label` e `data_product_description` no payload dos cards e no nó `Table`. O roteamento e a expansão PPR leem daí. A linha "Produto:" no texto do card existe (`OM_CARD_PRODUCT_LINE`) mas fica `off` |
| O catálogo publica tabelas que **não existem no banco** (18 em 26/10, todo o histórico e o reloginho) | entram na busca e podem tomar o lugar da tabela certa (`fds-eo-risco-001` na rodada 7) | nada: a correção é na fonte, não no RAG |
| Tabelas **renomeadas e acrescentadas** entre 26/10 e 30/10 (`prt_ts_*` → `prata_ts_*`; 253 → 325 tabelas) | relação que aponta para tabela que saiu do escopo é pulada (`semantic_relationships_out_of_scope`) | nada: as relações da skill precisam ser regeradas sobre o catálogo atual |
| As relações medidas pela skill **ainda não estão publicadas** no OM, que é compartilhado e de produção | não havia como medi-las sem publicar | `OM_EXTENSION_OVERRIDES_FILE` (**só desenvolvimento**) sobrepõe a `extension` lida do OM em memória, com aviso no log e contagem em `extension_overrides_applied` |

O que é do MCid nessas regras (nomes das propriedades, marcadores de linhagem,
o prefixo "Empreendimento" dos rótulos de produto) entra pelo `.env`, não pelo
código. Detalhes no [README da ingestão](../../src/rag/pipelines/ingest/README.md).

---

# Histórico das rodadas

As seções abaixo registram cada rodada como foi feita, inclusive conclusões que
rodadas posteriores revisaram. Os números das rodadas 1–6 são do catálogo de
253 tabelas.

## Setup (rodada 1, 2026-10-26)

| | baseline | candidata A | candidata B |
|---|---|---|---|
| Escopo | `Cidades.cidades.prata`, `.ouro` (253 tabelas, 4.866 colunas, 5.119 cards) | idem | idem |
| Relações | o que o OM publica hoje | `mcid.yaml` gerado, via `OM_EXTENSION_OVERRIDES_FILE` | idem, `HUB_GLOBAL=0` |
| `SEMANTIC_JOIN` no grafo | 11 | 156 (145 da skill + 11 antigas) | 127 (116 + 11) |
| Tabelas ilha | 240 (95%) | 176 (70%) | — |
| Tabelas com grão | 64 | 215 | 215 |
| Grafo / collection | `neo4j_data_mcid_prata_ouro` / `documents_mcid_prata_ouro` | `…_skill` / `documents_mcid_prata_ouro_skill` | idem (recriados) |

Mesmo modelo, mesmo `.env`, mesmos golds (hash idêntico entre os runs):
`eval/datasets/retrieval/mcid-medalhao-business-v0.1.0.json` (8 casos) e
`eval/datasets/explore/mcid-medalhao-explore-v0.1.0.json` (18 casos), ambos
remapeados nesta data a partir dos golds do layout antigo.

## Rodada 1 — retrieval (Persona 2)

| métrica | baseline | A (hub global) | B (só produto) |
|---|--:|--:|--:|
| `structural_sufficiency` | **0,750** | 0,625 | 0,625 |
| `table_recall` | 0,750 | 0,625 | 0,625 |
| `mrr_at_10` | 0,588 | 0,562 | 0,562 |
| `seed_recall_at_10` | 0,208 | 0,208 | 0,208 |
| `overexpansion` | **17,2** | 33,4 | 34,3 |
| prompt p50 / p95 (chars) | 15.017 / 22.238 | 22.858 / 33.312 | 22.427 / 31.328 |
| joins no contexto (média) | 0,25 | 10,1 | 7,6 |
| `unauthorized_join_rate` | 0 | 0 | 0 |

Casos que falham:

- `far-ufs-uh-001` (todas): `ouro_far_mapa_nacional` nunca vira semente.
- `fds-evolucao-ritmo-001` (todas): nenhuma semente cai na ouro do FDS. A
  aresta `ouro_fds_ficha_empreendimento.apf → ouro_fds_evolucao_financeira_chart.apf`
  existe na candidata, mas sem semente no produto não há o que expandir.
- `fds-eo-risco-001` (**regressão** nas candidatas): `ouro_fds_panorama_entidade`
  sai do top-5 de sementes. O grafo não mexe em semente — o que mudou foi o
  card: o v2 lista "Relacionamentos:", e as arestas novas mudaram o texto
  embedado de dezenas de tabelas (as de entidade organizadora do Rural
  ganharam várias e passaram à frente).

Diagnóstico comum às duas baselines: **confusão entre produtos**. Com tudo em
dois schemas compartilhados e as colunas da ouro de FAR/FDS/LF com >90% de
descrição genérica ("Atributo do indicador…"), pergunta de FAR traz sementes de
Trabalho Social, Pró-Moradia e Rural.

## Rodada 1 — Explorar (Persona 1)

| métrica | baseline | A |
|--:|--:|--:|
| recall@10 | 0,445 | 0,427 |
| core recall@10 | 0,639 | 0,639 |
| MRR@10 | 0,560 | 0,543 |
| `catalog_fidelity` | 1,000 | 1,000 |
| `listed_ratio` | 0,045 | 0,071 |
| latência p95 | 7,8 s | 9,8 s |

## Achados de catálogo (independentes do RAG)

- **18 tabelas publicadas no OM não existem no banco** `cidades` (histórico e
  reloginho inteiros). `prata_historico_snh_apf_mes` é o hub de 10 das 11
  relações publicadas hoje.
- 6 colunas existem no banco e não no OM.
- ~220 das 253 tabelas têm, na `mcidSemanticRelationships`, só prosa de
  linhagem do sync do dbt; 2 relações publicadas não são confirmadas pela
  `mcidRelatedTables`.
- O sync do cidades não publica o grão (`mcidTableGrain`); o publicado é de
  set/2026.

## Rodada 2 — card com e sem a lista de relacionamentos (`OM_CARD_EMBED_RELATIONSHIPS`)

2×2: relações (OM de hoje | skill, variante B) × card v2 (com | sem a linha
"Relacionamentos:" no texto embedado). Cada célula com collection própria.

| retrieval (P2) | OM · com | OM · sem | skill · com | skill · sem |
|---|--:|--:|--:|--:|
| `structural_sufficiency` | 0,750 | 0,750 | 0,625 | **0,750** |
| `seed_hit_at_5` | 0,750 | 0,750 | 0,625 | 0,750 |
| `mrr_at_10` | 0,588 | 0,588 | 0,562 | 0,588 |
| `overexpansion` | 17,2 | 17,2 | 34,3 | 38,7 |
| prompt p50 (chars) | 15.017 | 15.017 | 22.427 | 26.154 |
| **explorar** recall / core / MRR @10 | 0,445 / 0,639 / 0,560 | 0,418 / 0,639 / 0,597 | 0,441 / 0,667 / 0,594 | 0,459 / 0,667 / 0,569 |

- Com 11 arestas a lista não muda nada (idêntico).
- Com as relações da skill, tirar a lista **devolve as sementes exatamente à
  baseline** — confirma que a regressão de semente era o texto do card.
- A superexpansão continua (é a expansão, não o card).
- Explorar: diferenças de ±0,03 com 18 casos e LLM no meio — dentro do ruído.

## Rodada 3 — alavancas de semente já implementadas (sobre "OM · sem lista")

| | sem instrução | `QUERY_INSTRUCTION_ENABLED` |
|---|--:|--:|
| `structural_sufficiency` | 0,750 | 0,625 |
| `seed_hit_at_5` | 0,750 | 0,625 |
| `mrr_at_10` | 0,588 | 0,372 |

- O prefixo de instrução padrão (em inglês) **piora** no qwen3-embedding.
- `RERANK_ENABLED` (jina-v2 multilingual): o coletor recusa os traces — ele
  exige que as sementes finais estejam no top-10 do híbrido, e o reranker
  escolhe do pool de 30. Amostra do 1º caso (FAR): o reranker trouxe histórico,
  Linha Financiada e Pró-Moradia. Medir exige ajustar o coletor.

## Linhagem no OpenMetadata (não ingerida pelo RAG)

222 de 253 tabelas têm linhagem; 322 arestas dentro de prata+ouro, muitas com
mapeamento de coluna. **310 de 322 ficam dentro do mesmo produto.** Das 240
ilhas da baseline, 209 têm linhagem no escopo; das 178 ilhas com a skill, 147.
Linhagem não autoriza JOIN, mas é o sinal de produto que falta às sementes e à
expansão.

## Rodada 4 — hipóteses de semente, expansão e poda (bancada)

**Como foi medido.** VPN caída (Ollama remoto e banco fora), então o embedding
da pergunta veio de um Ollama local com o mesmo `qwen3-embedding:4b` (os
vetores dos documentos são os do Qdrant). O local troca a ordem de cards
empatados na 4ª casa: MRR da baseline 0,525 aqui contra 0,588 no remoto. Por
isso **toda linha abaixo usa o embedder local**, inclusive as referências;
confirmar os vencedores no remoto antes do gate. A bancada (scratchpad,
`bancada/bancada.py`) chama o coletor e o score OFICIAIS e só troca o seletor de
sementes (envolve o híbrido de produção) e `settings.*` da expansão/poda. As
sementes também foram medidas no gold do Explorar (17 perguntas com
relevância de tabela), que tem mais casos que o da P2.

**Métrica nova: recall de coluna pós-poda (`pcr`).** O coletor conta TODAS as
colunas das tabelas do contexto, então `column_recall` e `overexpansion` não
enxergam a poda — mudar o teto de 25 para 8 não move nenhum dos dois. `pcr` =
colunas do gold que chegam ao prompt; `pcrS` = o mesmo, só nos casos
suficientes. **A poda atual (`legacy`) já perde coluna do gold em silêncio**
(`pcrS` 0,917–0,944).

### Sementes (grafo do OM, 11 arestas)

| variante | suf | hit@5 | MRR | P1 recall@5 | P1 core@5 | P1 hit@5 | P1 MRR |
|---|--:|--:|--:|--:|--:|--:|--:|
| base | 0,750 | 0,750 | 0,525 | 0,252 | 0,471 | 0,71 | 0,537 |
| **produto na pergunta (léxico, ×2)** | 0,750 | 0,750 | **0,604** | **0,321** | **0,588** | **0,77** | **0,602** |
| votação por produto no pool | 0,625 | 0,625 | 0,379 | 0,257 | 0,500 | 0,59 | 0,535 |
| CombSUM coluna→tabela | 0,750 | 0,750 | 0,400 | 0,291 | 0,471 | 0,77 | 0,458 |
| instrução pt-BR ("tabela do catálogo…") | 0,625 | 0,625 | 0,405 | 0,342 | 0,500 | 0,82 | 0,607 |

- **Roteamento por produto** (a pergunta cita "FAR", "FDS", "MCMV Entidades",
  "Rural"…; o card cujo Data Product do OM bate ganha ×2 no score) é o único
  que melhora tudo nos dois golds. Os aliases vêm do `displayName` do Data
  Product + siglas; "entidades" → `entidades_fds` foi acrescentado à mão
  (sem ele o MRR da P2 fica em 0,525).
- **Votação no pool piora**: é pseudo-relevância no nível de produto, e o pool
  costuma ser dominado pelo produto errado — reforça o erro.
- **Instrução em pt-BR**: melhor recall no Explorar, mas perde o caso da PNAD
  na P2. Não ligar.
- **Reranker (jina-v2)**: não medido — o DNS do container caiu junto com a
  VPN e o modelo não baixou.

### Expansão e poda (grafo da skill, 127 arestas)

| sementes · expansão · poda (teto semente/expansão) | suf | `pcrS` | tabelas | joins | prompt p50 | p95 |
|---|--:|--:|--:|--:|--:|--:|
| base · `evidence` · legacy 25/6 (**o que roda hoje**) | 0,750 | 0,944 | 9,1 | 8,8 | 28.250 | 34.570 |
| produto · `evidence` · legacy 25/6 | 0,875 | 0,917 | 8,1 | 7,5 | 23.084 | 34.413 |
| produto · sem expansão · strict 25/6 | 0,750 | — | 4,5 | 0,8 | 16.986 | 23.816 |
| produto · `ppr` orçamento 1 · strict 25/6 | 0,875 | 0,964 | 5,1 | 1,9 | 18.012 | 24.982 |
| **produto · `ppr` orçamento 1 · strict 25/15** | **0,875** | **0,982** | **5,1** | **1,9** | **18.560** | **25.677** |
| produto · `ppr` orçamento 3 · strict 25/15 | 0,875 | 0,982 | 6,4 | 3,8 | 22.547 | 30.098 |
| produto · `prize` orçamento 3 · strict 25/6 | 0,750 | — | 5,8 | 3,1 | 20.572 | 24.736 |
| produto · `ppr` orçamento 1 · strict 12/4 | 0,875 | 0,899 | 5,1 | 1,9 | 13.404 | 17.717 |

- **Primeira configuração acima de 0,75.** `fds-evolucao-ritmo-001` passa
  quando as três peças estão juntas: o roteamento põe
  `ouro_fds_ficha_empreendimento` nas sementes, a aresta da skill
  `ficha.apf → evolucao_financeira_chart.apf` existe, e a expansão a segue.
  No grafo do OM (sem essa aresta) o roteamento sozinho não fecha o caso.
- **PPR com orçamento 1** mantém a suficiência do `evidence` com metade das
  tabelas (5,1 × 8,1), um quarto dos joins, prompt p50 −20% e p95 −25%. β
  (desconto de hub) e γ (salto entre produtos) não mudaram nada nesta escala:
  com sementes já no produto certo, os vizinhos também estão.
- **`prize` perde o caso** — o chart não tem evidência na busca, então o
  prêmio o barra. O grafo carrega informação que a busca não tem; é o
  argumento para não condicionar a expansão só à similaridade.
- **Poda `strict`** (teto vale para tudo, relevância radical+IDF da P1)
  domina a `legacy` no mesmo teto: `pcrS` 0,917 → 0,964 sem crescer o prompt.
  Teto 15 para a tabela de expansão recupera a coluna que faltava no chart
  (0,982, +3% de prompt). Tetos menores trocam prompt por coluna perdida.
- No grafo do OM, a configuração vencedora não regride nada: mesma
  suficiência, `pcrS` 0,944 → 1,000, prompt p50 +10% (as tabelas do produto
  certo têm mais colunas).

### `far-ufs-uh-001`: é o card

Não passa em nenhuma variante. `ouro_far_mapa_nacional` fica em 108º no denso e
360º no esparso, e o card diz por quê: "Agregação geográfica por UF para o mapa
do Superset… **Domínio: Camada ouro**…". Não menciona FAR nem unidades
habitacionais. No medalhão o schema virou camada, então a linha "Domínio:" é
igual em 145 cards e não distingue nada. **Hipótese seguinte**: trocar por
"Produto: Empreendimento FAR — <descrição do Data Product>". Exige reembedar
a collection, inviável no Ollama local em CPU — fica para quando a VPN voltar.

### O que foi para o código (tudo atrás de flag, default inalterado)

> Estado ao fim da rodada 4. Superado na rodada 5: a ingestão passou a gravar
> o produto e o roteamento entrou no código de produção.

- `EXPANSION_MODE=ppr|prize` + `EXPANSION_BUDGET`, `EXPANSION_PPR_ALPHA`,
  `EXPANSION_DEGREE_BETA`, `EXPANSION_CROSS_PRODUCT`, `EXPANSION_MIN_RATIO`,
  `EXPANSION_PRIZE_FLOOR`, `EXPANSION_ELIGIBLE_ONLY`
  (`components/retrievers/expansion.py`, funções puras, `tests/test_expansion.py`).
  Lê `data_product` do nó `Table` — **a ingestão ainda não grava**; na bancada
  foi gravado à mão no Neo4j local a partir do `dataProducts` do OM.
- `PRUNE_MODE=strict`.
- O roteamento por produto **não** está no código de produção (só na bancada).

## Rodada 5/6 — confirmação no Ollama remoto, com o código de produção (2026-10-27)

VPN de volta. Cada linha é uma collection reingerida (≈260 s) com o produto no
payload e no grafo (ingestão nova), avaliada logo em seguida pelo coletor
oficial com o **roteador de produção** (`SEED_PRODUCT_ROUTING`,
`product_router.py`) e embedding remoto. Card sem a lista de relacionamentos
em todas. Aliases extras: `{"entidades_fds": ["mcmv entidades", "entidades"]}`.

### Persona 2

| grafo · linha "Produto:" no card · sementes · expansão | suf | MRR | tabelas | prompt p50 | p95 | `pcrS` |
|---|--:|--:|--:|--:|--:|--:|
| OM · off · base · `evidence` (**baseline**) | 0,750 | 0,588 | 4,6 | 15.016 | 22.238 | 0,944 |
| OM · off · produto · ppr1 + strict 25/15 | 0,750 | 0,667 | 4,6 | 16.539 | 23.730 | 1,000 |
| OM · label · produto · ppr1 + strict 25/15 | 0,750 | **0,750** | 4,0 | 15.808 | 19.985 | 1,000 |
| OM · full · produto · ppr1 + strict 25/15 | 0,750 | 0,556 | 4,4 | 12.938 | — | — |
| skill · off · base · `evidence` (skill como está hoje) | 0,750 | 0,588 | 9,1 | 26.154 | 34.570 | 0,944 |
| skill · off · produto · `evidence` | 0,875 | 0,667 | 7,9 | 22.628 | — | — |
| skill · off · produto · ppr1 + strict 25/15 | **0,875** | 0,667 | 5,1 | 18.625 | 25.820 | 0,982 |
| **skill · label · produto · ppr1 + strict 25/15** | **0,875** | **0,750** | **4,5** | 17.810 | **21.917** | 0,982 |
| skill · full · produto · ppr1 + strict 25/15 | 0,750 | 0,556 | 5,0 | 15.394 | — | — |

- Os números da bancada local se confirmam no remoto com o código de
  produção (o MRR da baseline volta a 0,588, igual ao run oficial anterior).
- **`full`** (rótulo + descrição do produto) resolve `far-ufs-uh-001` pela
  primeira vez (`ouro_far_mapa_nacional` entra em 3º), mas quebra
  `fds-entrega-ts-001`: a descrição do Trabalho Social diz "no FAR, no
  Entidades e no Rural" e puxa as tabelas de TS para a frente da ficha do FDS.
  Na P2, saldo negativo.
- **`label`** (só "Produto: Empreendimento FAR") dá o melhor ranking da P2
  (MRR 0,688 sem roteamento, 0,750 com) e o menor prompt p95. Não resolve
  `far-ufs`.

### Persona 1 (eval oficial do Explorar, com LLM)

| grafo · linha "Produto:" · roteamento | recall@10 | core@10 | MRR@10 | precisão@10 | listed |
|---|--:|--:|--:|--:|--:|
| OM · off · não (**baseline**) | 0,418 | 0,639 | 0,593 | 0,131 | 0,045 |
| OM · off · sim | 0,459 | 0,667 | **0,646** | 0,142 | 0,046 |
| OM · label · sim | 0,473 | 0,667 | 0,549 | 0,180 | 0,040 |
| OM · full · sim | **0,510** | **0,722** | 0,631 | 0,177 | 0,044 |
| skill · off · sim | 0,487 | 0,667 | 0,590 | 0,144 | 0,069 |
| skill · label · sim | 0,501 | 0,667 | 0,517 | 0,156 | 0,063 |
| skill · full · sim | **0,510** | **0,722** | 0,576 | 0,155 | 0,069 |

- O **roteamento** melhora o Explorar em todas as configurações, sem listar
  mais (`listed` estável, `catalog_fidelity` 1,0 em todas).
- A linha de produto aumenta recall e precisão do Explorar, mas baixa o MRR.
  Com 18 casos, 0,05 de MRR é ~1 caso mudando de posição: ruído provável.

### Leitura

| alavanca | P2 | P1 | veredito |
|---|---|---|---|
| Roteamento por produto | MRR +0,08; com a skill, +1 caso suficiente | todas as métricas sobem | **ligar** (nas duas personas) |
| PPR orçamento 1 + poda strict 25/15 | mesma suficiência, prompt p95 −25% com a skill | não se aplica | **ligar** junto com relações densas |
| Card sem lista de relacionamentos | devolve as sementes | neutro | **ligar** junto com relações densas |
| Linha "Produto:" `label` | melhor MRR da P2 | +recall, −MRR | decisão: o card é um só para as duas personas |
| Linha "Produto:" `full` | perde caso | melhor recall/core | não, para a P2 |
| Votação por produto, CombSUM, instrução pt-BR | pioram | misto | não |

Ainda falham: `far-ufs-uh-001` (só o `full` resolve, e cobra outro caso) e,
no grafo do OM, `fds-evolucao-ritmo-001` (falta a aresta ficha→chart — vem
com a skill).

## Rodada 7 — código genérico, catálogo de 2026-10-30

Reexecução depois de tirar do código tudo o que era do MCid: os padrões
`OM_SCOPE_ID`, `OM_RELATIONSHIP_ADAPTER`, as duas propriedades de relação e
`OM_REQUIRE_DOCUMENTED_TABLES` ficaram neutros; a palavra "empreendimento" do
roteador virou `SEED_PRODUCT_LABEL_STOPWORDS`; os marcadores de linhagem viraram
`OM_LINEAGE_ONLY_MARKERS`. O que é do MCid mora só no `.env`. Suíte: 372 testes
OK sem `.env`, com o `.env` do MCid e com o `minc.env`.

Três ingestões com coleção e volume próprios (`documents_mcid_po_gen_*`,
`neo4j_data_gen_*`). As relações da skill foram reconstruídas do volume
`neo4j_data_mcid_prata_ouro_skill` (o arquivo de sobreposição original estava no
scratchpad, perdido no reboot): 116 relações + grão de 215 tabelas.

⚠️ **O catálogo mudou desde o dia 26**: 325 tabelas em prata+ouro (eram 253),
79 novas (`prata_hist*`, `ouro_reloginho*`, `prata_financiado*`, `*_far_*`…) e
7 removidas (`prt_ts_*`, renomeadas para `prata_ts_*`). Por isso a skill entra
com **123** arestas (eram 127): as 4 que faltam apontam para `prt_ts_*`. Os
números não são comparáveis em nível absoluto com as rodadas 1–6; a comparação
vale dentro desta rodada.

### Persona 2

| grafo · card | configuração | suf | MRR | tabelas | p50 | p95 |
|---|---|--:|--:|--:|--:|--:|
| OM · com lista | atual | 0,625 | 0,521 | 4,8 | 14.858 | 23.445 |
| OM · com lista | produto + ppr1 + strict 25/15 | 0,625 | 0,583 | 4,8 | 16.389 | 24.428 |
| skill · sem lista · off | atual | 0,625 | 0,518 | 8,1 | 23.454 | 32.982 |
| skill · sem lista · off | produto + ppr1 + strict 25/15 | **0,750** | **0,580** | 5,2 | 18.820 | 25.783 |
| skill · sem lista · label | atual | 0,625 | 0,562 | 6,9 | 20.348 | 32.850 |
| skill · sem lista · label | produto + ppr1 + strict 25/15 | **0,750** | 0,562 | **4,8** | 17.280 | **22.840** |

- **Reproduz:** a combinação resolve `fds-evolucao-ritmo-001` (+1 caso), corta
  as tabelas no prompt de 8,1 para 5,2 e o p95 de 33,0k para 25,8k — o mesmo
  25,8k da rodada 5/6. O roteamento sozinho sobe o MRR no grafo do OM (0,521 →
  0,583).
- **Não reproduz:** a vantagem de MRR da linha `label` (0,667 → 0,750 no dia
  26). Agora `label` empata em suficiência, perde MRR (0,580 → 0,562) e só
  ganha em prompt (p95 25,8k → 22,8k).
- **Caso novo que falha:** `fds-eo-risco-001`. A tabela nova
  `ouro_reloginho_resumo_gargalo_desempenho_dashboard` (coluna
  `qtd_obras_paralisadas`) entra em 5º e empurra `ouro_fds_panorama_entidade`
  para 6º. No dia 26 as tabelas `reloginho` não existiam no banco: conferir se
  existem hoje (premissa catálogo = banco).

### Persona 1

| grafo · card | roteamento | recall@10 | core@10 | MRR@10 | nDCG@10 | prec@10 | listed |
|---|---|--:|--:|--:|--:|--:|--:|
| OM · com lista | não | 0,459 | 0,667 | 0,607 | 0,485 | 0,140 | 0,038 |
| OM · com lista | sim | 0,459 | 0,667 | 0,607 | 0,488 | 0,140 | 0,038 |
| skill · off | sim | 0,496 | 0,694 | 0,580 | 0,489 | 0,148 | 0,054 |
| skill · label | sim | 0,501 | 0,667 | 0,526 | 0,456 | 0,152 | 0,050 |

- O roteamento muda o ranking em 11 de 18 casos, inclusive nos que não citam
  produto — é o LLM reordenando. Ganho líquido zero no grafo do OM: **o ganho
  da rodada 5/6 no Explorar não se sustenta**; fica como neutro.
- `label` volta a baixar o MRR do Explorar (0,580 → 0,526), agora junto com a
  P2 sem compensação de MRR. `catalog_fidelity` 1,0 em todas.

### Leitura

| alavanca | veredito depois da rodada 7 |
|---|---|
| relações da skill + card sem lista + ppr1 + strict 25/15 | **mantém**: +1 caso, prompt −22% (p95) |
| roteamento por produto na P2 | **mantém**: MRR +0,06 no OM; necessário para o caso do FDS |
| roteamento por produto no Explorar | neutro (ruído do LLM) |
| linha "Produto:" `label` | **não ligar**: só economiza prompt e perde MRR nas duas personas |

## Rodada 8 — gold derivado do Superset e teste da linhagem (2026-11-02)

### O gold

Os 436 charts do Superset de produção (`govhub.mcid.lablivre.rocks`) foram lidos
pelo MCP, **só leitura**, e classificados:

| tier | charts | critério |
|---|--:|---|
| A | 143 | tabela do catálogo prata/ouro, agregação simples, filtros simples |
| B | 105 | regra de negócio no Superset: métrica, filtro ou dimensão em SQL livre, métrica salva composta, gráfico combinado |
| fora | 188 | 98 listas de linhas, 48 templates de texto, 42 charts sem dataset |

- Os 108 datasets são tabelas físicas, sem SQL no Superset: não há regra
  escondida em dataset virtual.
- Todo SQL do tier A parseia em PostgreSQL e só cita colunas que existem no
  OpenMetadata.
- **Nenhum chart faz JOIN**: o gold testa semente, colunas e agregação, não o
  grafo. Complementa o gold de negócio, não o substitui.

`eval/datasets/retrieval/mcid-medalhao-superset-v0.1.0.json`: **59 casos**, no
máximo 2 por tabela (um número único e um agrupado), 8 programas. A pergunta
foi escrita a partir do SQL e da descrição do chart, não do título (o título
engana: "Evolução Financeira por EO" calcula a média por mês). O SQL de
referência fica em `metadata.reference_sql`, para a execution accuracy. 13
casos têm o slice `needs_review`: outros charts da mesma tabela filtram por
uma coluna que o caso não filtra (ex.: `portaria = 'Todas as portarias'` em
`ouro_far_mapa_nacional`, sinal de linha de total). O filtro pode vir do
dashboard, que o chart não guarda, e a ferramenta de dashboard do MCP falha em
4 de 5 dashboards (erro de validação no servidor), então não deu para conferir.

### Retrieval no gold do Superset

| grafo · configuração | suf | hit@5 | MRR@10 | recall de coluna | tabelas | p50 | p95 |
|---|--:|--:|--:|--:|--:|--:|--:|
| OM · atual | 0,576 | 0,492 | 0,389 | 0,627 | 4,6 | 14.399 | 21.122 |
| OM · final | 0,593 | 0,508 | 0,411 | 0,644 | 4,3 | 13.381 | 22.730 |
| skill · atual | 0,627 | 0,492 | 0,384 | 0,661 | 8,8 | 21.649 | 32.761 |
| skill · final | 0,593 | 0,525 | 0,406 | 0,644 | 5,0 | 16.359 | 23.547 |

- **A busca é o gargalo**: em metade das perguntas nenhuma tabela certa entra
  nas 5 sementes. O gold de negócio, com 8 casos, mostrava 0,625.
- A configuração final ganha pouco aqui (+1 caso, MRR +0,02): sem JOIN, o que
  ela tem de melhor (expansão e poda) quase não entra em jogo.
- A skill na configuração atual resolve 2 casos a mais que a final, mas com
  8,8 tabelas no prompt: a expansão larga acerta tabelas irmãs por acaso.
- Por programa: FAR com MRR 0,10–0,17 e Trabalho Social 0 de 3 são os piores;
  FNHIS (4/4) e Pró-Moradia (4/5) os melhores.
- **As 24 falhas têm o mesmo formato**: a semente cai no programa certo, mas
  numa tabela vizinha (muitas vezes a versão `prata` em vez da `ouro`). Parte
  disso pode ser gold incompleto: só a tabela do chart conta como certa, e uma
  irmã pode responder a mesma pergunta. A revisão de domínio deve olhar.

### Linhagem: não vale implementar agora

Teste direto: para cada falha, os vizinhos de linhagem a 1 salto das sementes
(OpenMetadata, só leitura), ordenados pela semelhança densa com a pergunta.

| conjunto | falhas | tabela certa a 1 salto | entre as 3 primeiras dos vizinhos |
|---|--:|--:|--:|
| gold de negócio (rodada 7, configuração final) | 3 | 2 (`far-ufs`, `pnad`) | 1 (`far-ufs`, em 3º) |
| gold do Superset (OM, configuração final) | 24 | 6 | 2 |

São de 4 a 21 vizinhos por pergunta (mediana 10). A expansão por linhagem
recuperaria ~2 casos em 59 e pagaria isso em prompt em todas as perguntas.
Arestas de "mesma chave" ficam de fora pelo mesmo motivo e pelos hubs (`uf`,
`ano`). **O problema que sobra é escolher a tabela certa dentro do programa**,
que é card (descrição), ranking (reranker) e gold (alternativas válidas), não
grafo.

## Rodada 9 — hipóteses de semente nos dois golds (2026-11-02)

Alvo: "programa certo, tabela errada" (rodada 8). Grafo do OM (coleção
`documents_mcid_po_gen_om`), referência = configuração final (roteamento +
`ppr` 1 + `strict` 25/15). Golds: Superset (59) e negócio (8). Código novo,
tudo atrás de flag e com padrão inalterado: `SEED_SCHEMA_BOOST` (peso por
schema no roteador), `SEED_DISTINCT_TABLES` (uma semente por tabela) e
`HYBRID_DENSE_WEIGHT` (RRF ponderada entre denso e BM25). O coletor do eval
passou a montar busca → roteador → reranker na ordem da produção e aceita
`RETRIEVAL_SELECTED_TOP_K` (só experimento).

| hipótese | Superset suf / hit@5 / MRR | negócio suf / MRR | prompt p95 (S / N) |
|---|---|---|---|
| atual (sem roteamento etc.) | 0,576 / 0,492 / 0,389 | 0,625 / 0,521 | 21,1k / 23,4k |
| só denso (sem roteamento) | 0,627 / 0,559 / 0,412 | 0,500 / 0,542 | 19,6k / 30,7k |
| só BM25 (sem roteamento) | 0,271 / 0,271 / 0,212 | 0,375 / 0,375 | 24,4k / 18,0k |
| **final (referência)** | 0,593 / 0,508 / 0,411 | 0,625 / 0,583 | 22,7k / 24,4k |
| + 8 sementes | 0,644 / 0,508 / 0,411 | 0,750 / 0,583 | 28,1k / 30,4k |
| + uma semente por tabela (+ ouro) | 0,627 / 0,542 / 0,449 | 0,750 / 0,594 | 22,9k / 20,9k |
| + peso `ouro` 0,5 | 0,593 / 0,508 / 0,447 | 0,750 / 0,588 | 21,4k / 24,7k |
| + peso `ouro` 1,0 | 0,627 / 0,542 / 0,454 | 0,750 / 0,594 | 21,0k / 20,9k |
| + fusão 0,5 (controle) | 0,593 / 0,508 / 0,405 | 0,625 / 0,580 | 22,7k / 23,8k |
| + fusão 0,7 | 0,627 / 0,559 / 0,419 | 0,750 / 0,569 | 22,8k / 28,8k |
| + fusão 0,85 | 0,610 / 0,542 / 0,415 | 0,625 / 0,563 | 21,9k / 28,9k |
| **+ peso `ouro` 1,0 + fusão 0,7** | **0,627 / 0,576 / 0,494** | **0,875 / 0,619** | **21,4k / 23,4k** |
| + reranker Jina v2 | não coube na memória | 0,750 / 0,427 | — / 30,3k |
| + reranker bge-v2-m3 int8 | não coube na memória | não terminou | — |

- **Vence a combinação peso `ouro` + fusão 0,7**: +2 casos no Superset
  (`far-560`, `reforma-466`), +2 no negócio (`fds-eo-risco`, `fds-evolucao`),
  nenhum caso perdido, MRR +0,08 no Superset, prompt igual ou menor.
- **O BM25 é o canal fraco** (27% sozinho no Superset). Na RRF de peso igual
  ele empurra cards errados para o top-5; 0,7 para o denso corrige, 0,85 já
  passa do ponto. O controle 0,5 reproduz a referência (implementação certa).
- **Peso por camada**: a `ouro` é feita para consumo e é onde estão as tabelas
  que os dashboards e as perguntas usam. Mecanismo genérico (`{"schema":
  peso}` no `.env`); o valor `ouro` é do escopo MCid.
- **8 sementes** resolvem casos, mas o prompt cresce 25–40%. **Uma semente por
  tabela** não acrescenta nada.
- **Reranker**: na CPU desta máquina (22 GB) os dois cross-encoders
  multilíngues passaram de 9 GiB no gold de 59 casos e o eval foi encerrado
  pela proteção de memória. No único resultado completo (Jina, negócio) o MRR
  caiu de 0,583 para 0,427. Além disso, o Jina v2 é CC-BY-NC. Fica fora até
  haver GPU ou serviço dedicado.

**No grafo da skill** (coleção `documents_mcid_po_gen_skill_off`), a mesma
combinação dá o mesmo resultado: Superset 0,627 / MRR 0,485, negócio 0,875 /
MRR 0,556. Com sementes melhores, as relações da skill deixam de somar nesses
golds: `fds-evolucao` passa porque a tabela de evolução entra direto como
semente. Os dois golds quase não têm pergunta com JOIN, então o valor das
relações para perguntas que cruzam tabelas **não está medido**.

## Rodada 10 — gold do Superset conferido no banco e BM25 com fold (2026-11-03)

Ponto de partida: as 22 falhas do gold do Superset na configuração da rodada 9
(roteamento + `ppr` 1 + `strict` 25/15 + fusão 0,7 + peso `ouro`). Para cada
uma, comparei a tabela do gabarito, as tabelas recuperadas, as colunas delas e
o card no Qdrant.

### Por que as perguntas falhavam

| motivo | casos |
|---|---|
| outra tabela recuperada responde a mesma pergunta (gold incompleto) | 380, 390, 370, 436, 507, 53, 54, 149, 376 |
| a tabela do gabarito não tem descrição no catálogo | 529, 572, 506, 596, 597, 606 |
| sigla e grão (ver abaixo) | 119, 150, 134, 135 |
| o conceito está no **valor** de uma coluna, não no schema | 128, 129, 355 |

**Sigla e grão (150).** A pergunta diz "unidades habitacionais"; a tabela certa
(`ouro_rural_ficha_empreendimento`, uma linha por empreendimento) só diz
`quantidade_uh`, sem descrição. A recuperada (`prata_rural_historico_empreendimento`)
descreve a coluna por extenso ("Unidades habitacionais contratadas no
snapshot"), mas tem grão empreendimento × mês: somar ali multiplica o total
pelo número de meses. Nome certo, significado certo, grão errado.

### Gold v0.2.0, conferido no banco

`eval/datasets/retrieval/mcid-medalhao-superset-v0.2.0.json`. Cada alternativa
entra como um segundo `gold_subgraph` (o eval escolhe a melhor) com o SQL dela
em `metadata.alternatives`. O critério para aceitar foi **resultado igual**:
rodei o SQL de referência e o da alternativa no banco do MCid e comparei. Só
agregados, conexão com `default_transaction_read_only=on` e `statement_timeout`.

| caso | alternativa | resultado |
|---|---|---|
| 376 | `prata_linha_financiada_contrato` (`COUNT(*)`) | igual (499.055) |
| 436 | `ouro_pro_moradia_ficha_contrato` | igual (686) |
| 149 | `prata_rural_empreendimento` (`COUNT(DISTINCT apf)`) | igual (10.402) |
| 507 | `ouro_rural_entidade_organizadora` | igual (2.549) |
| 390 | `ouro_reforma_casa_brasil_mapa_uf` | igual nas 27 UFs |
| 370 | `ouro_linha_financiada_ranking_territorial` | igual; só o rótulo da UF ausente muda (`NULL` × "Não informado") |
| 53, 54 | `ouro_far_ficha_empreendimento` (colunas `metodo`, `portaria_selecao`) | igual em todos os grupos, **depois de corrigir o SQL do gabarito** |
| 380 | `ouro_linha_financiada_mapa_uf` | **rejeitada**: recorte diferente, 142.054 de 499.055 contratos (só Fundo Social, Pró-Moradia e MCMV Cidades) |

**Três SQLs de referência estavam errados.** O SQL do gold é montado do
`form_data` do chart, e o chart não guarda o filtro nativo do dashboard. Nas
tabelas de painel há linhas de total ("Todas as portarias", "Todos os
municípios") que o dashboard filtra:

| caso | SQL do chart | com o filtro de total |
|---|---|---|
| 53, 54 (`ouro_far_panorama_estadual`) | 527.340 UH | 131.835 UH (4×) |
| 64 (`ouro_far_mapa_nacional`, por UF) | soma "Todas as portarias" + cada portaria | metade (2×) |

131.835 é o total da ficha e do mapa nacional com `portaria = 'Todas as
portarias'`: as três fontes batem. Os SQLs corrigidos ficam em
`metadata.reference_sql` (o do chart em `reference_sql_chart`), com a slice
`sql_corrigido`. A varredura por valores de total nas outras tabelas do gold só
achou falsos positivos ("Totalmente Entregue", nomes de empreendimento).

**Dois charts estão quebrados.** 128 e 129 filtram `secao IN ('metodo',
'portaria')` na `ouro_rural_panorama_estadual`, mas a tabela hoje só tem as
seções `header`, `agente_financeiro`, `programa`, `situacao` e
`execucao_fisica`, como a descrição do catálogo diz. O SQL volta vazio
(provavelmente charts copiados do painel do FAR). Slice `chart_invalido`.

**Leitura principal** = 51 casos: sem `sem_descricao` (6) e sem
`chart_invalido` (2). As outras slices continuam pontuadas.

### BM25 com fold

O tokenizador do BM25 (fastembed) só quebra em caractere que não é letra,
número ou `_`. Nomes em snake_case viram um termo só. No caso 572 ("valor total
das contrapartidas pactuadas nos empreendimentos do FAR"), o card de
`ouro_far_contrapartida` tem nome e colunas, sem descrição:

| | termos em comum entre pergunta e card |
|---|---|
| sem fold | `valor` (presente em centenas de tabelas) |
| com `SPARSE_FOLD_ACCENTS` | `contrapart`, `empreend`, `far`, `valor` |

O fold não resolve sigla: "unidades habitacionais" × `quantidade_uh` continua
sem termo em comum.

**Como foi medido sem reingerir.** Uma cópia da coleção
(`documents_mcid_po_gen_om_fold`) com o mesmo denso e o mesmo payload, e o
esparso recalculado pela factory da ingestão com o fold ligado. Nenhuma chamada
ao Ollama. Controle: a mesma cópia com o fold desligado
(`documents_mcid_po_gen_om_ctrl`) reproduz a referência exatamente (45/59, mesmo
MRR, mesmo prompt).

| configuração | Superset (59) suf / hit@5 / MRR | leitura principal (51) | negócio (8) suf / MRR | prompt p50 / p95 (S) | prompt p50 / p95 (N) |
|---|---|---|---|---|---|
| rodada 9, gold v0.1.0 | 37/59 · 0,576 · 0,494 | — | 7/8 · 0,619 | 13,2k / 21,3k | 15,8k / 24,8k |
| rodada 9, gold v0.2.0 | 45/59 · 0,695 · 0,585 | 45/51 · 0,804 · 0,673 | 7/8 · 0,619 | 13,2k / 21,3k | 15,8k / 24,8k |
| + fold, fusão 0,7 | 47/59 · 0,729 · 0,600 | 46/51 · 0,824 · 0,689 | 7/8 · 0,667 | 12,1k / 20,6k | 17,3k / 25,0k |
| **+ fold, fusão 0,5** | **47/59 · 0,746 · 0,630** | **46/51 · 0,843 · 0,723** | **7/8 · 0,875** | 12,4k / 19,9k | 18,1k / 25,1k |

Prompt calculado por caso (mediana e p95 dos 59 e dos 8). O canal BM25 sozinho:

| | Superset: tabela certa no top-10 | negócio |
|---|---|---|
| BM25 sem fold | 20/59 | 3/8 |
| BM25 com fold | **45/59** | **7/8** |
| denso (referência) | 46/59 | 6/8 |

Leitura:

- **O fold conserta o canal, e o peso 0,7 deixa de ser necessário.** O 0,7 da
  rodada 9 compensava um BM25 quebrado; com o fold os dois canais acertam quase
  o mesmo, e o peso igual (o comportamento padrão, sem `HYBRID_DENSE_WEIGHT`) é
  o melhor.
- **Superset:** +1 na leitura principal (`far-355`) e +1 fora dela (`ts-597`,
  sem descrição: é onde o nome da tabela é tudo o que o card tem). Nenhum caso
  perdido. Quatro casos perdem posição no ranking (436, 149, 475, 483) sem
  perder suficiência.
- **Negócio:** suficiência igual; três casos sobem para o 1º lugar
  (`fds-eo-risco`, `fds-evolucao`, `fds-entrega-ts`). O prompt p50 cresce 15%
  (`far-ranking-valor`: 12,7k → 19,7k); o p95 não muda.
- Em 13/08 o fold tinha sido medido sem ganho no catálogo antigo (74 tabelas,
  8 casos). Lá os cards eram descritivos; no medalhão boa parte do card é nome
  em snake_case, e é aí que o fold pesa.

**Para ligar:** `SPARSE_FOLD_ACCENTS=true` na ingestão **e** na consulta (os dois
lados têm de casar), reingerindo o esparso; e retirar `HYBRID_DENSE_WEIGHT`.

### Exemplo para o artigo

Os casos do motivo 4 mostram uma falha que não é da busca. No painel estadual,
"método de seleção" existe só como valor de uma coluna `secao`; numa tabela
descrita como "uma linha por UF" existe uma linha de total por UF que o SQL tem
de filtrar, senão a soma sai 2× ou 4× maior. Nenhuma busca sobre metadados
recupera um conceito ou uma regra que o catálogo não declara; a correção é
documentar os domínios categóricos e o grão no catálogo.

## Próximos passos (atualizado em 2026-11-03)

1. ✅ Ingestão grava o Data Product (rodada 5).
2. ✅ Roteamento por produto como componente das duas personas
   (`SEED_PRODUCT_ROUTING`, `EXPLORE_PRODUCT_ROUTING`).
3. ✅ Card com a linha de produto (`OM_CARD_PRODUCT_LINE`): medido, **não
   ligar** (rodada 7).
4. ✅ Código sem vocabulário do MCid; padrões neutros (rodada 7).
5. **Regerar as relações da skill sobre o catálogo atual** (325 tabelas): as
   de hoje não cobrem as 79 tabelas novas e 4 apontam para `prt_ts_*`, que
   foram renomeadas.
6. **Conferir se as tabelas `reloginho` existem no banco.** No dia 26 não
   existiam e hoje uma delas tira semente de `fds-eo-risco-001`. Se continuarem
   fora do banco, a correção é no catálogo, não no RAG.
7. Revisão de domínio dos golds (saem de `draft`) e, depois, gate do ADR-0012
   sobre a combinação final do resumo.
8. Promover o `pcr` (recall de coluna pós-poda) para o eval oficial: hoje a
   poda é invisível para as métricas.
9. ✅ Hipóteses de semente medidas (rodada 9): **fusão 0,7 + peso `ouro`**
   vence. Reranker fora por memória e MRR.
10. ✅ Gold derivado do Superset (rodada 8) e v0.2.0 conferido no banco
    (rodada 10): 8 alternativas, 3 SQLs corrigidos, 2 charts inválidos.
    Pendente: revisão de domínio dos `needs_review` restantes.
11. Execution accuracy: o SQL de referência e o das alternativas já foram
    conferidos no banco (rodada 10). Falta o lado da geração: rodar o SQL que
    o LLM escreve e comparar o resultado com o de referência. Com isso o eval
    separa "contexto suficiente" (retrieval) de "resultado certo" (geração).
12. ✗ Linhagem e "mesma chave" como arestas de recuperação: medido, não vale
    agora (rodada 8).
13. Gold com perguntas de JOIN (cruzar programas ou tabelas): é o único jeito de
    medir se as relações da skill ainda valem depois das sementes melhores.
14. Melhorar as descrições das tabelas e colunas da ouro no catálogo (FAR e
    Trabalho Social são os piores no gold do Superset): é o que sobra para a
    busca depois da rodada 9. Inclui as 6 tabelas sem descrição, os valores
    de `secao` dos painéis e as linhas de total (rodada 10).
15. **Ligar `SPARSE_FOLD_ACCENTS`** (rodada 10): reingerir com a flag e tirar
    `HYBRID_DENSE_WEIGHT`. Medido em cópia da coleção; falta confirmar numa
    ingestão de verdade.
16. Montar o SQL de referência de chart **com** os filtros nativos do
    dashboard (o `form_data` do chart não os guarda; foi o que inflou 53, 54 e
    64).

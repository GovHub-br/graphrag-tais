# MCid medalhão (prata+ouro): relações da skill e ajustes de consumo no retrieval

Iniciado em 2026-10-26 · última medição em 2026-10-30 (rodada 7) · **atualizado em 2026-10-31**

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

## Próximos passos (atualizado em 2026-10-31)

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
9. Reranker: medir (na rodada 3 o modelo não baixou e o coletor recusa
   sementes fora do top-10).

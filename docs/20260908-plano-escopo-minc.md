# Plano — mudar o RAG para o escopo MinC

Levantamento feito em 08/09/2026 contra o OpenMetadata e o Trino de verdade
(nada aqui é suposição de leitura de código). Branch `main`, working tree limpa.

## 1. O que existe hoje no OpenMetadata

O service **`MinC`** tem duas databases. Só uma tem camada semântica:

| database | tabelas | com anotação semântica |
|---|---:|---:|
| `MinC.dbanalytics` | 1.774 | **408** |
| `MinC.minc` | 647 | **0** |

`MinC.minc` está fora — não tem uma única tabela anotada. **O escopo é
`MinC.dbanalytics`.**

Das 15 schemas de `dbanalytics`, 8 têm anotação:

| schema | tabelas anotadas | colunas |
|---|---:|---:|
| `salic` | 383 | 3.696 |
| `agentes` | 7 | 54 |
| `transferegov` | 4 | 55 |
| `bbagil` | 3 | 55 |
| `ibge_sidra` | 3 | 18 |
| `ibge_sidra_bronze` | 3 | 18 |
| `transferegov_bronze` | 3 | 48 |
| `bbagil_bronze` | 2 | 13 |
| **total** | **408** | **3.957** |

As outras (`bronze` 465, `salic_bronze` 674, `ancine`, `bacen`, `bacen_bronze`,
`cotas`, `metadata`) têm zero anotação e ficam fora — com
`OM_REQUIRE_DOCUMENTED_TABLES=true` elas seriam lidas e descartadas, só custando
paginação.

**Volume: 408 tabelas + 3.957 colunas = 4.365 nós/cards** — 3,4× o MCid (1.289).

**Qualidade da documentação: 99% das tabelas e 95% das colunas têm descrição
preenchida.** Isso é materialmente melhor que o MCid e favorece o retrieval
denso.

## 2. O Trino já está apontando para o lugar certo

Este é o ponto do teu adendo, e a notícia é boa: **não há nada para mudar em
`TRINO_*` nem em `SQL_MCP_*`.**

O catálogo `hml_analytics` — o que o `trino-mcp` já usa — **é** o
`MinC.dbanalytics`. Os schemas batem um a um (`salic`, `agentes`, `transferegov`,
`bbagil`, `ibge_sidra`, `cotas`, `bronze`, …). Conferido com dado real:

```
SELECT count(*) FROM hml_analytics.salic.agentes__agentes  →  997.756
```

e o `mincTableGrain` da mesma tabela no OM diz *"997511 chaves distintas em
997756 linhas"*. **A camada semântica foi medida contra este mesmo catálogo, via
Trino** (todo grão termina com "medição: contagem exata via Trino"). Nome físico
`schema.tabela` do SQL gerado casa direto, sem prefixo de catálogo, como já é a
regra.

Portanto: `TRINO_CATALOG=hml_analytics`, `SQL_MCP_URL`, `SQL_MCP_TOOL`,
`SQL_DIALECT=trino` — **todos permanecem**. A execução do SQL do MinC sai pelo
trino-mcp sem tocar em nada.

## 3. O formato do MinC vs. o adapter do MCid

O MinC publica **três** Custom Properties próprias (as do MCid continuam
existindo em paralelo, para o escopo Cidades):

- `mincSemanticRelationships` (markdown) — mesma gramática do MCid
- `mincRelatedTables` (entityReferenceList)
- **`mincTableGrain` (string) — novo, não existe no MCid**

Rodei o parser do MCid (`MCDMarkdownRelationshipAdapter`) contra as 408 tabelas
reais. Resultado:

- **703 linhas de relação, 0 malformadas** para o regex atual
- **0 referências de coluna inválidas**, **0 divergências de `relatedTables`**,
  **0 relações publicadas fora da ponta**
- Classificação: **192 "Contrato validado por teste dbt" + 32 "Relação validada
  por agregados" = 224 validadas**; 479 candidatas (ignoradas por contrato)
- As 224 validadas cobrem **104 pares de tabela distintos** (~112 arestas
  `SEMANTIC_JOIN` únicas depois da dedup) — contra **24** no MCid

A gramática é a mesma. **Não precisa de adapter novo** — precisa de dois ajustes.

### Blocker 1 — `CATALOG_ID` hardcoded (a ingestão morre na primeira tabela)

`relationship_adapters.py:32`:

```python
CATALOG_ID = "mcid-semantic-relationships"
```

e mais abaixo, `ValueError(f"Contrato Markdown desconhecido em {table_fqn}")`.
O MinC publica `minc-semantic-relationships`. Confirmado na prática:

```
❌ extract falhou: ValueError -> Contrato Markdown desconhecido em
   MinC.dbanalytics.agentes.bbagil_extrato_filtrado
```

**Correção:** promover `catalog_id` a parâmetro do construtor e registrar
`minc_markdown_v1` no `build_relationship_adapter` apontando para a **mesma
classe**. Risco baixo, isolado, e o caminho `mcid_markdown_v1` fica intacto.

### Blocker 2 — nenhum JOIN fica elegível para SQL

**Todas as 703 relações têm `join_rule = normalized_exact`.** O adapter só marca
`sql_join_eligible` quando a regra é `exact`:

```python
"sql_join_eligible": join_rule == "exact",
```

→ **0 joins elegíveis em 408 tabelas.**

**Investiguei o que `normalized_exact` significa.** O gerador do catálogo está em
`data-application-minc/scripts/semantica/`. Achado central
(`catalogo.py:149`):

```python
"join": "normalized_exact",
```

É um **literal hardcoded** — toda relação recebe esse valor, incondicionalmente.
Não é uma afirmação de que aquela relação precisa de normalização; é o único
valor que o gerador emite. Por isso 703/703.

A normalização real são **quatro expressões Trino determinísticas**
(`conceitos.py`), todas reproduzíveis em SQL gerado:

| normalizador | SQL | quando muda algo |
|---|---|---|
| `inteiro` | `try_cast(nullif(trim(cast(c as varchar)),'') as bigint)` | só se um lado for texto e o outro número |
| `texto` | `nullif(trim(lower(cast(c as varchar))),'')` | se houver espaço/caixa divergente |
| `documento` | tira pontuação + `lpad` 11/14 | CPF/CNPJ gravado como número perde o zero à esquerda |
| `ibge` | `substr(regexp_replace(...),1,6)` | código de município 7 vs 6 dígitos |

**Só dois conceitos usam normalizador não-trivial**: `documento_pf_pj` e
`municipio`. Todo o resto é `inteiro` ou `texto`.

Cruzei as **112 relações validadas** (as 224 linhas são elas publicadas nas duas
pontas) com os tipos reais das colunas no Trino:

| situação | relações | a normalização muda o resultado? |
|---|---:|---|
| `integer` ↔ `integer` | **96** | **não** — `inteiro` sobre integer é identidade |
| `varchar` ↔ `varchar`, chave CPF/CNPJ | 6 | possivelmente (medindo) |
| `varchar` ↔ `varchar`, outras chaves | 9 | possivelmente (medindo) |
| `varchar` ↔ `integer` | 1 | **sim** — join cru é impossível, precisa `CAST` |

**86% das relações validadas dispensam normalização** — a regra `normalized_exact`
é rótulo do gerador, não exigência. As 5 relações de `municipio` são
`loc_codigo` interno do SALIC (`integer↔integer`, views sobre a mesma tabela): o
normalizador `ibge` nem se aplica. O risco 7-vs-6 dígitos entre TransfereGov e
SALIC existe **apenas em relações candidatas**, que o adapter descarta por
contrato.

O único caso que quebra hoje:

```
salic.sac__vwprogramarouanetavaliacaofinalizada.idprogramarouanet (varchar)
  -> salic.sac__tbprogramarouanet.idprogramarouanet (integer)
```

> **Veredito da medição dos 16 pares `varchar`: PENDENTE** — teste em curso
> comparando, no dado real, a sobreposição de chaves distintas com `=` cru vs.
> com a expressão normalizada.

### Blocker 2b — mesmo com JOIN elegível, 70% do MinC é ilha

Montei o grafo das 112 relações validadas: elas tocam **121 das 408 tabelas**.
As outras **287 (70%) não têm nenhuma aresta**. São **25 componentes conexos**, o
maior com 33 tabelas; hubs: `tabelas__orgaos` (12), `sac__tbprogramarouanet`
(11), `sac__verificacao` (9).

Isso **limita o impacto do blocker 2 a ~30% do catálogo** e muda a prioridade: no
MCid a expansão do subgrafo era o lever principal; no MinC ela alcança um terço
das tabelas. O que decide a qualidade aqui é **grão e precisão de retrieval**,
não expansão.

### Blocker 3 — duas colunas no mesmo par de tabelas: metade é composta, metade não

Dos **104 pares** de tabela validados, **8 têm duas colunas**. Mas eles são de
**duas naturezas opostas**, e tratá-los igual gera erro nos dois sentidos:

**Chave composta (4 pares)** — as duas condições precisam ser unidas por `AND`;
usar uma só produz produto cartesiano:

| par | colunas | cardinalidade |
|---|---|---|
| `sac__historicoautuacaox ↔ sac__autuacaox` | `sequencial` + `anoprojeto` | `many_to_many` |
| `sac__acompanhamentobandasx ↔ sac__kitentregue` | `sequencial` + `anoprojeto` | `many_to_many` |
| `sac__editaldesembolso ↔ sac__editalparcelas` | `idedital` + `nrparcela` | `many_to_many` |
| `sac__editalrelatorio ↔ sac__editalparcelas` | `idedital` + `nrparcela` | `many_to_many` |

**FK alternativa (4 pares)** — dois caminhos *diferentes* para a mesma tabela;
uni-los com `AND` daria resultado **vazio ou errado**:

| par | colunas | leitura |
|---|---|---|
| `sac__verificacaopecaxveiculo ↔ sac__verificacao` | `idverificacaopeca` **ou** `idverificacaoveiculo` → `idverificacao` | peça e veículo são papéis distintos |
| `sac__vgerentes ↔ sac__kitbanda` | `org_codigo` **ou** `usu_codigo` → `codigo` | conceitos distintos no mesmo alvo |
| `sac__vusuarios ↔ sac__kitbanda` | idem | idem |
| `sac__vusuarios ↔ tabelas__orgaos` | `org_codigo` **ou** `usu_orgao` → `org_codigo` | dois papéis de órgão |

**O discriminador é limpo e mecânico:** se as duas relações **compartilham a
coluna-alvo** (ou a de origem), são alternativas; se as colunas são **disjuntas
nos dois lados**, é chave composta. Corrobora: as 4 compostas são todas
`many_to_many_on_matching_population` nas duas pernas.

Daí a regra mais geral, e mais barata que detectar composta:
**`many_to_many` numa perna de JOIN é o sinal de chave incompleta** — sozinha ela
multiplica linhas. Das 112 validadas: 72 `many_to_one`, 28 `one_to_one`, **12
`many_to_many`**.

O `_JOINS_CYPHER` do `Neo4jSubgraphRetriever` devolve **uma linha por par de
colunas**, sem agrupar por par de tabelas — então hoje o prompt recebe as duas
condições soltas, sem dizer se são `AND` ou `ou`.

## 4. Mudanças no `.env`

Todas com `# ANTES:` para reversão, como já é o padrão do arquivo.

| chave | de | para |
|---|---|---|
| `OM_SCOPE_ID` | `mcid` | `minc` |
| `OM_SERVICE` | `Cidades` | `MinC` |
| `OM_DATABASE` | `cidades` | `dbanalytics` |
| `OM_SCHEMAS` | 5 schemas Cidades | as 8 de `MinC.dbanalytics` da §1 |
| `OM_RELATIONSHIP_ADAPTER` | `mcid_markdown_v1` | `minc_markdown_v1` |
| `OM_SEMANTIC_RELATIONSHIPS_PROPERTY` | `mcidSemanticRelationships` | `mincSemanticRelationships` |
| `OM_RELATED_TABLES_PROPERTY` | `mcidRelatedTables` | `mincRelatedTables` |
| `QDRANT_COLLECTION` | `documents_mcid_ptbm25` | `documents_minc_ptbm25` |
| `OM_DOMAIN_LABELS` | 5 rótulos MCid | 8 rótulos MinC |
| `OM_GRAIN_KEYS` | 52 entradas MCid | ver decisão C |
| `OM_EMBEDDING_PROFILE` | `dev-qwen3-…` | mesmo perfil, sufixo `minc` |

**Permanecem como estão:** `OM_REQUIRE_DOCUMENTED_TABLES=true` (é o que filtra
2.421 → 408), `OM_CARD_SCHEMA_VERSION=v2`, todo o bloco `TRINO_*`/`SQL_MCP_*`,
embedding (`qwen3-embedding:4b`, 2560 dims — confirmei que o modelo está no
Ollama), `SPARSE_LANGUAGE=portuguese`.

**Letra morta:** `OM_TABLE_AUTHORITY` está no `.env` mas **não existe em
`configs.py` na `main`** — veio da branch `feat/otimizacao`. Não precisa ser
reescrito para o MinC; ou fica ignorado, ou sai.

**Sobre apagar o MCid:** sim, Qdrant e Neo4j são containers locais com volumes
na tua máquina — nada disso é ambiente compartilhado (a VM `10.0.0.210` é cópia
à parte e não é tocada). Como pode apagar, a ingestão fica mais simples: em vez
de coleção nova, dá para limpar a coleção e o grafo antes de ingerir, evitando
carregar 1.289 cards do MCid que nunca mais serão consultados.

Ainda assim vale saber: o `Neo4jGraphWriter` é `MERGE` puro, sem `DELETE`, e os
`node_id` são FQN (`MinC.dbanalytics.*` vs `Cidades.cidades.*`). Ou seja, **os
dois escopos coexistiriam sem colidir** se um dia for útil. O que é exclusivo é o
`.env` — um escopo ativo por vez.

## 5. Mudanças no código

| # | mudança | arquivo | risco | toca caminho medido da P2? |
|---|---|---|---|---|
| C1 | `catalog_id` parametrizado + registro `minc_markdown_v1` | `sources/relationship_adapters.py` | baixo | não |
| C2 | elegibilidade de JOIN por lista configurável de `join_rule` | `sources/relationship_adapters.py` + `configs.py` | médio | indireto (muda o subgrafo) |
| C3 | agrupar JOIN por par de tabelas + marcar `AND` vs alternativa, e sinalizar `many_to_many` | `retrievers/neo4j_subgraph_retriever.py` | **alto** | **sim** |
| C4 | grão vindo de `mincTableGrain` em vez do JSON no `.env` | `sources/` + `converters/` + `retrievers/` | médio | sim (serialização) |
| C5 | 8 rótulos de domínio | só `.env` | nenhum | não |

**C2 em detalhe.** Em vez de `join_rule == "exact"`, ler de
`OM_SQL_ELIGIBLE_JOIN_RULES` (default `exact`, preservando o MCid byte a byte);
no `.env` do MinC, `exact,normalized_exact`. Opt-in explícito e medível, sem
mexer no comportamento do escopo Cidades. Justificado pela medição do blocker 2:
`normalized_exact` é rótulo do gerador, e 86% das relações validadas são
`integer↔integer`, onde a normalização é identidade.

**C3 em detalhe.** Três coisas, em ordem de valor:
1. **agrupar** os JOINs por par de tabelas na serialização (hoje saem soltos);
2. **rotular** o grupo como composta (`AND`) ou alternativa (`ou`), pelo
   discriminador da coluna-alvo compartilhada — é determinístico, não precisa de
   LLM;
3. **sinalizar `many_to_many`** na perna, com a instrução de deduplicar antes de
   agregar. Pela lição do MCid (**instrução o LLM obedece, dado ele ignora**),
   este item 3 provavelmente vale mais que os outros dois.

**C4 em detalhe — e é o item de maior retorno do plano.** O `mincTableGrain`
existe em **407 das 408** tabelas, medido por contagem exata no Trino. Fui contar
o que ele diz:

- **211 tabelas (52%) são mais finas que a entidade** e já trazem escrita a
  instrução anti-inflação: *"Somar medidas após JOIN por esta chave duplica
  valores."*
- 174 são 1:1 com a entidade; a razão chave/linha mediana é 0,989, e **194 de 385
  estão abaixo de 0,99** — ou seja, um JOIN por essa chave multiplica linhas.

Para comparar: no MCid a gente descobriu **8** tabelas duplicadas, medindo na
mão, e foi o que consertou os números errados. No MinC **mais da metade do
catálogo** tem esse alerta pronto para uso. Manter isso como JSON no `.env` daria
~90 KB numa linha só e nasceria desatualizado.

Proposta concreta: `MetadataRecordBuilder` acrescenta
`"grain": extension.get(OM_TABLE_GRAIN_PROPERTY, "")` ao `table_record`
(`metadata_record_builder.py:134`), o writer persiste como propriedade do nó
`Table`, e `_grain_for` passa a preferir a propriedade do nó com fallback para
`OM_GRAIN_KEYS`. Aditivo, reversível, e o `.env` do MCid continua valendo.

Lembrando o que a gente aprendeu três vezes no MCid: **dado no contexto o LLM
ignora; instrução no prompt ele obedece.** O grão do MinC já vem escrito como
instrução — é o maior ganho de graça deste escopo.

## 6. Decisões que preciso de ti

**A — escopo.** Confirmo as 8 schemas anotadas e deixo `bronze`, `salic_bronze`,
`ancine`, `bacen`, `cotas`, `metadata` de fora? (elas têm 0 anotação; entrar só
custa paginação)

**B — `normalized_exact` vale como JOIN direto? → investigado, resposta: sim,
com uma exceção.** Não precisa mais de ti. O valor é literal hardcoded no
gerador; a normalização real são 4 expressões Trino determinísticas; e 96 das
112 relações validadas são `integer↔integer`, onde ela é identidade. Sigo com
C2 marcando `normalized_exact` como elegível, **exceto** quando os tipos das duas
colunas divergem — hoje 1 caso, que precisa de `CAST` explícito ou de ficar
inelegível. Confirma esse recorte?

**C — grão:** C4 (ler do OM, mudança de código) ou gerar o JSON gigante no
`.env` por script (sem mexer em código, mas insustentável a 408 tabelas)?
Minha recomendação é C4.

**D — gold do MinC.** Sem dataset gold do MinC **não há gate MDD** (ADR-0012)
para C2/C3/C4, que mexem no caminho calibrado da Persona 2. O gold do MCid não
serve: não tem chave composta nem `normalized_exact`. Quem revisa as perguntas
de domínio do MinC?

## 7. Ordem de execução

1. **C1 + `.env`** (§4) — desbloqueia. Ingestão roda e a gente vê números reais.
2. **Ingestão de medição** com `EXPANSION_MODE` atual: contar nós, cards, e
   quantos `SEMANTIC_JOIN` sobrevivem. Confirma na prática o blocker 2.
3. **Decisão B** → C2 → reingestão → comparar expansão e tamanho de prompt.
4. **C4 (grão)** → reingestão → é aqui que se espera o maior ganho de
   assertividade, pelos 52% de tabelas com alerta de duplicação.
5. **C3 (agrupamento de JOIN)** — por último, é o de maior risco e o que mais
   precisa do gold (decisão D). Vale só para os 8 pares afetados, então é o
   último em prioridade apesar de ser o pior modo de falha.

Nota de prioridade que mudou com a medição: o blocker 2b (70% de ilhas) mostra
que **expansão de subgrafo importa menos no MinC que no MCid**. Se houver que
cortar escopo, corta-se C2 antes de C4.

Passos 3–5 só fecham com medição antes/depois, como sempre.

## 8. Riscos

- **Densidade do `salic`**: 383 das 408 tabelas num único schema, muitas sendo
  views (`sac__v*`) sobre a mesma base. Rótulo de domínio não desambigua nada aí
  dentro. Espero **queda de precisão do retrieval** em relação ao MCid — é o
  risco número um do escopo, e só aparece medindo.
- **Volume 3,4×** na ingestão: 4.365 cards para embutir com `qwen3-embedding:4b`.
  Não é problema de qualidade, é de tempo de ingestão.
- **Chave composta e FK alternativa** (blocker 3): produto cartesiano
  silencioso — responde com número inflado, sem erro. É o pior modo de falha
  porque parece certo. E o erro simétrico também existe: unir com `AND` duas FKs
  alternativas devolve vazio.
- **Grão ignorado**: 52% das tabelas são mais finas que a entidade. Sem C4, toda
  soma sobre elas sai inflada — o mesmo bug do MCid, mas em 211 tabelas em vez
  de 8.
- **`.env` é um escopo por vez**: virar para MinC tira o MCid do ar (embora o
  Neo4j preserve os dois grafos).

## 9. Resultado executado em 2026-09-08

O escopo foi ingerido com sucesso: **408 tabelas, 3.957 colunas, 4.069
relações, 112 `SEMANTIC_JOIN` e 4.365 cards**. C1, C4 e C5 foram aplicados. O
dataset `minc-business-v0.1.0` tem 13 casos e permanece `draft`, aguardando
revisão de domínio; portanto os gates desta seção são diagnósticos.

### Experimento C2 — rejeitado pela medição

Hipótese: autorizar `normalized_exact` apenas entre tipos compatíveis recuperaria
o caso multi-table sem regressão de ruído. A implementação fail-closed confirmou
no grafo real **111 relações compatíveis e uma exceção `TEXT → INT` bloqueada**.

Comparação no mesmo dataset/hash (`f9f646...`):

| métrica | antes | C2 ativa | resultado |
|---|---:|---:|---|
| `table_recall` | 0,6154 | 0,6154 | sem ganho |
| `structural_sufficiency` | 0,6154 | 0,6154 | sem ganho |
| `overexpansion` | 14,1026 | 15,6154 | **+10,7%, reprovado** |
| prompt médio (chars) | 13.802 | 14.279 | +3,5% |
| `unauthorized_join_rate` | 0 | 0 | preservado |

O gate oficial registrou reprovação por overexpansion além da tolerância de 10%,
além do bloqueio esperado por dataset `draft`. A ativação foi revertida para
`OM_SQL_ELIGIBLE_JOIN_RULES=exact`; o código configurável e a proteção de tipos
ficam disponíveis para um experimento futuro com expansão mais seletiva e gold
de JOIN revisado. Relatórios: `eval/reports/minc-c2-after-report.json`,
`minc-c2-gate.json` e `minc-c2-rollback-gate.json`.

### Próximo gargalo medido

Os cinco casos SALIC ainda insuficientes não recuperam a tabela gold nem no
top-10. C3 não resolve isso e não deve ser ativada ainda: o dataset atual tem
somente um gold de `SEMANTIC_JOIN` simples e **nenhum caso de chave composta ou
FK alternativa**, logo não cobre o risco que C3 pretende alterar.

A auditoria também encontrou alternativas plausíveis que precisam de decisão de
domínio antes de otimizar o ranking. Exemplos: `sac__vw_captacao_ano_uf` para
captação por UF e `sac__tbapiaprovacoes` para série de valores aprovados. Ajustar
retrieval antes dessa revisão pode otimizar contra um gold incompleto. O próximo
passo seguro é revisar essas alternativas, promover uma nova versão do dataset e
só então medir uma mudança de seed retrieval ou C3.

---

# EXECUTADO em 08–09/09/2026

## Ingestão do MinC — concluída

`POST /ingest/run` → HTTP 200 em **8m44s**:

| | |
|---|---:|
| tabelas | **408** |
| colunas | **3.957** |
| cards (nós) | **4.365** |
| `HAS_COLUMN` | 3.957 |
| **`SEMANTIC_JOIN`** | **112** |
| rejeitadas por falta de anotação | 222 |
| tabelas com grão gravado | **407** |
| grãos com alerta de duplicação | **211** |

Bate com a previsão do plano em todos os números.

### O que foi mudado no código

- **C1** — `catalog_id` parametrizado em `relationship_adapters.py` + registro
  `minc_markdown_v1`. Mesma classe, mesma gramática. MCid intacto.
- **C4** — grão vindo do catálogo: `OM_TABLE_GRAIN_PROPERTY` novo em `configs.py`,
  capturado no `MetadataRecordBuilder`, e `_grain_for` passa a preferir a
  propriedade do nó com fallback para `OM_GRAIN_KEYS`. Feito JUNTO com a ingestão
  de propósito — deixar para depois custaria reingerir 4.365 cards.

### Tropeços de ambiente (todos resolvidos)

1. **Portas 9000 e 8000 ocupadas** pelo `data-application-minc`. Não parei
   container de outro projeto: hayhooks subiu em **8010** por override fora do
   compose versionado. MinIO ficou fora (só serve `.docx`/CSV).
2. **OpenMetadata atualizado para 1.13.3**; `.env` travava em 1.12.1.
3. **`trino-mcp` em loop de restart** — `x509: certificate signed by unknown
   authority`. Causa: **faltava `certs/trino.crt`** (é gitignored, não vem no
   clone), e o compose aponta `SSL_CERT_FILE` para ele — apontar para arquivo
   inexistente derruba a confiança em TODAS as CAs. Resolvido pelo procedimento
   do `certs/README.md`.

## `normalized_exact` — investigado e medido

O valor é **literal hardcoded** no gerador (`catalogo.py:149`), não uma exigência
por relação. A normalização real são 4 expressões Trino determinísticas, e só
duas são não-triviais (`documento`, `ibge`).

**Medição no dado real** (sobreposição de chaves distintas, `=` cru vs. expressão
normalizada), nos 15 pares `varchar` das relações validadas: **13 pares +0, 2
pares −1** (a normalização *perde* 1 chave, por colapso de `trim`/`lower`).
**Em nenhum caso o join cru perdeu chave.** Somando aos 96 pares
`integer↔integer`, onde `inteiro` é identidade por construção: `normalized_exact`
não exige normalização neste catálogo. Único caso real é o par de tipos
divergentes (`idprogramarouanet` varchar → integer), que precisa de `CAST`.

## Gold de retrieval do MinC

`eval/datasets/retrieval/minc-business-v0.1.0.json` — 13 casos, 7 críticos,
`draft` / `pending_domain_review`. Subgrafos gerados a partir dos
`relationship_id` REAIS do Neo4j (o gerador falha se citar coluna ou JOIN
inexistente).

**O primeiro run serviu para calibrar o GOLD, não o sistema.** Três defeitos meus
que a execução expôs:

1. **Par de registro não era par mínimo** — eu trocara "repassado" por "pago", e
   "pago" puxa a tabela de pagamentos do BB Ágil. Media sentido, não registro.
   Corrigido para palavras de conteúdo idênticas; passou a casar.
2. **Gold estreito em tabelas irmãs** — `sac__dba_relacao_projetos` é
   **superconjunto** de `sac__dba_relacao_projetos_geral` (mesmas 17 colunas +
   `cidade_execucao`). Exigir uma era arbitrário; ambas entraram como
   alternativas.
3. **Gold pior que a resposta do sistema** — para captação por UF, o sistema
   achou `sac__vw_captacao_ano_uf` (pré-agregada: `ano`, `uf`, `valor`,
   `quantidade`), melhor que o meu `sac__dba_captacaoanoufmunicipio`, que está no
   grão pronac×município.

Critério usado para aceitar alternativa: **só entra o que responde com o grão
certo**. `sac__tbapiaprovacoes` foi recusada mesmo sendo recuperada — é mais fina
que projeto (2,7×) e inflaria a contagem.

### Resultado

`table_recall` **0,769** (10/13), `structural_sufficiency` 0,769,
`seed_table_recall@5` 0,615, over-expansion 12,6×.

O corte é por domínio, exatamente como o plano previu:

- **Meta 5 / fomento (schema `agentes`): 6/6**
- **SALIC / Rouanet: 4/7**

As 3 falhas restantes são todas no SALIC: o caso de JOIN
(`captacao-uf-incentivador`, crítico), o de segmento (o sistema prefere a tabela
com armadilha de grão) e a pergunta vaga.

## 🔴 Achado de protocolo: a comparação exige janela curta

Três execuções consecutivas do mesmo dataset deram **fingerprint idêntico e 0
casos instáveis** — a recuperação é determinística. Mas duas execuções separadas
por ~4,5 h divergiram em 2 casos, **sem mudança de código, de corpus nem do texto
daquelas perguntas**. Testei e descartei efeito de composição de lote (o caso
rodado sozinho reproduz o resultado do lote de 13 exatamente).

Ou seja, a variação vem de fora do RAG — o suspeito é o Ollama, que é remoto e
compartilhado.

**Consequência para o gate do ADR-0012:** baseline e candidato precisam rodar
**na mesma janela, um atrás do outro**. Um baseline de horas antes NÃO é
comparador válido. Isso não aparecia no MCid, onde o corpus é 3,4× menor e há
menos quase-empate para desempatar.

---

# Varredura do catálogo e estado final (09/09/2026)

## Ingestão definitiva

O catálogo foi republicado durante o trabalho e cresceu de **408 → 547** tabelas
anotadas (+34%). As duas primeiras tentativas de reingestão falharam por estar
lendo **alvo móvel** (408 → 426 → 443 → 452 tabelas em minutos, com dois
snapshots de evidência convivendo). Esperar a convergência resolveu.

| | primeira | final |
|---|---:|---:|
| tabelas | 408 | **547** |
| colunas | 3.957 | **6.874** |
| cards | 4.365 | **7.421** |
| `SEMANTIC_JOIN` | 112 | **250** |
| tabelas com grão | 407 | **546** |
| grãos com alerta de duplicação | 211 | **277** |
| relações fora do escopo | — | **0** |

⚠️ **Correção**: as 45 relações apontando para tabelas sem anotação, que eu havia
diagnosticado como inconsistência de contrato do catálogo, **eram artefato da
publicação pela metade**. No catálogo convergido são zero. A tolerância no
adapter continua correta como defesa, mas não havia defeito do lado do gerador.

## C2 (elegibilidade de JOIN) — medido e REVERTIDO

A/B limpo (mesmo catálogo, mesma janela, alternando `sql_join_eligible` no grafo
por Cypher para não confundir com reingestão):

| métrica | C2 off | C2 on |
|---|---:|---:|
| `table_recall` | 0,769 | 0,769 |
| `node_recall` | 0,769 | 0,769 |
| `relationship_recall` | 0,769 | 0,769 |
| `seed_table_recall@5` | 0,577 | 0,577 |
| **over-expansão** | **23,6** | **28,1** |

**Zero ganho de recall, +19% de contexto.** Pior caso: o de segmento passou de
34,3 para 78,7 de over-expansão — e era um caso que falhava, então é ruído puro.
`OM_SQL_ELIGIBLE_JOIN_RULES` voltou para `exact`, com a medição registrada no
`.env`.

⚠️ **Limite desta medição**: ela mede o CUSTO bem e o BENEFÍCIO mal. A
elegibilidade serve para expandir o subgrafo (medido) e para dizer ao LLM quais
JOINs são seguros no SQL (não medido por um eval de retrieval). A decisão
definitiva espera um eval de RESPOSTA.

## Varredura das tabelas novas

Duas tabelas que não existiam no catálogo de 408 mudaram três casos:

- **`sac__projetos`** — a descrição a chama de "**tabela-núcleo do domínio SAC**".
  277.187 projetos, grão 1:1 por `anoprojeto`+`sequencial`, com `segmento`,
  `ufprojeto`, `mecanismo`, `nomeprojeto`. Responde "projetos por segmento" e
  "projetos por UF" melhor que qualquer coisa do gold anterior.
- **`sac__tbapiincentivos`** — uma linha por aporte de incentivo, com
  `sgufincentivador` e `vlcaptado` na MESMA tabela. Responde "captação por UF do
  incentivador" **sem JOIN nenhum**, que era o caso crítico que faltava.

Critério de aceitação mantido: **só entra o que responde com o grão certo**.
Ficaram de fora `sac__vpropapreaproexec` e `sac__vwalterarprojeto` (1:1, mesmo
universo, com segmento) porque não entendi o recorte delas — anotado nas
`review_notes` para quem revisar o domínio.

## Resultado final: `table_recall` 1,000 (13/13)

🔴 **E é exatamente por isso que este número NÃO deve ser lido como qualidade do
sistema.** O que subiu de 0,769 para 1,000 foi a **concordância do gold com o
catálogo**, não o comportamento do RAG: em nenhum dos três casos o sistema mudou
o que recupera — ele já trazia `sac__projetos` e `sac__tbapiincentivos` no topo,
e era o gabarito que não as listava.

Consequências práticas:

1. **O dataset ficou sem poder de discriminação.** Todos os 13 casos estão no
   teto. Serve como **detector de regressão** (se algo quebrar, cai de 1,0), não
   como medida de qualidade.
2. **Precisa de casos mais duros** para voltar a discriminar: perguntas que devem
   ser RECUSADAS, e casos onde existe tabela plausível porém ERRADA por grão —
   o sistema já demonstrou preferir `sac__tbapiaprovacoes` (2,7× mais fina que
   projeto) à tabela limpa.
3. **A revisão de domínio segue bloqueante.** Sem ela, 1,000 é concordância
   comigo mesmo, com um gabarito que já se mostrou errado três vezes.

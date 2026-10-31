---
name: mapear-joins-semanticos
description: Descubra por onde duas tabelas se ligam quando o banco não declara FK, medindo evidência no dado, e publique o resultado no OpenMetadata como SEMANTIC_JOIN e grão. Use ao preparar um escopo novo para o GraphRAG, ao trocar de base ou de layout (ex. migração para medalhão bronze/prata/ouro), quando a ingestão reporta SEMANTIC_JOIN=0 ou quase todas as tabelas como ilha, quando o catálogo perdeu as chaves de junção, ou ao auditar cobertura de relações existentes.
---

# Mapear joins semânticos a partir da evidência

O GraphRAG expande o subgrafo por `SEMANTIC_JOIN` (ADR-0011). Sem essa aresta o
grafo vira um conjunto de ilhas e a expansão não faz nada. Quando o banco não
declara FK — que é o caso normal em data warehouse analítico — a joinabilidade
precisa ser **medida no dado** e publicada no OpenMetadata.

Implementações de referência:

| escopo | onde | motor | resultado |
|---|---|---|---|
| MinC | `data-application-minc/scripts/semantica/` | Trino | 1.633 medições em 554 de 640 tabelas, 250 relações, 546 grãos |
| MCid medalhão (prata+ouro) | porte do MinC para PostgreSQL direto | PostgreSQL | 512 chaves em 206 tabelas, **8 testadas + 137 validadas**, 205 grãos, **84 s** |

No MCid o catálogo publicado antes tinha **11** relações para 253 tabelas
(95% ilhas). O porte para PostgreSQL é mecânico e está descrito na seção
"Dialeto: Trino → PostgreSQL".

## Antes de começar: isto é necessário?

Verifique primeiro se o catálogo já publica chave de junção. No OpenMetadata,
leia a Custom Property de relações de uma tabela do escopo e procure seta
coluna→coluna (`schema.tabela.coluna → schema.tabela.coluna`).

- **Tem seta** → o catálogo já declara joins; o trabalho é de adapter, não deste método.
- **Só tem linhagem** (`Lê de`, `É lido por`) → linhagem **não é** chave de junção.
  Ela diz quem deriva de quem, não por qual coluna cruzar. Aplique este método.
- **Não tem nada** → aplique este método.

⚠️ Não confunda os dois. Foi o que aconteceu com o MCid: o catálogo foi
republicado só com linhagem, e reingerir produziria `SEMANTIC_JOIN=0` — trabalho
pago sem recuperar a capacidade. No medalhão (out/2026) a mesma propriedade
`mcidSemanticRelationships` carregava **prosa de linhagem em ~220 de 253
tabelas** (`**Lê de:**`, `**É lido por:**`, `**Granularidade temporal:**`),
escrita pelo sync do dbt. O adapter reconhece essa forma e a conta em
`semantic_lineage_only_tables`, em vez de abortar.

Mais três checagens antes de medir — cada uma já custou caro:

1. **Onde o catálogo de relações é versionado?** O resultado deste método é
   um arquivo (`mcid.yaml`, `minc.yaml`) publicado pela DAG do repositório de
   dados — **não** uma publicação avulsa no OM. Confira se o arquivo está no
   repo, se reflete o layout atual e se a flag do sync está ligada (no
   cidades: `OM_SYNC_RELACOES_SEMANTICAS`, default `false`). No MCid o YAML
   versionado era de jul/2026 (layout antigo) e as relações vivas no OM
   vieram de uma publicação manual de set/2026 — que depois divergiu da
   `mcidRelatedTables` reescrita pelo sync (2 arestas não confirmadas).
2. **O catálogo descreve o MESMO banco?** Compare as tabelas e colunas do
   escopo no OM com o `information_schema` do banco que executa o SQL. No
   MCid, **18 tabelas publicadas não existem no banco** (todo o histórico e
   o reloginho) — e uma delas era o hub de 10 das 11 relações publicadas.
   No sentido inverso, 6 colunas existem no banco e não no OM: o gerador só
   mede o que o catálogo publica, senão a ingestão aborta com "coluna
   inexistente em tabela do snapshot".
3. **O schema ainda separa produto?** Ver a etapa 4 (hub por produto).

## O erro que este método existe para evitar

A primeira versão do mapeamento do MinC casava coluna por **nome igual**.
Mediu 10 de ~90 chaves reais. Dois motivos, e os dois se repetem em qualquer base:

**Nome não é conceito.** `cgccpf`, `cnpjcpf` e `beneficiario_documento` são o
mesmo CPF/CNPJ em grafias diferentes — 36 grafias no MinC. Comparar cada grafia
só consigo mesma faz a ligação entre fomento e Lei Rouanet no nível da pessoa
nunca aparecer. É a pergunta mais óbvia que se faz a esses dados.

**Regex estreita descarta a cauda.** Exigir `^id_` ou `_id$` deixa de fora
`idagente`, `idprogramarouanet`, `nrproposta` e a composta `anoprojeto`+`sequencial`
— esta última é a chave de projeto presente em ~85 tabelas.

## As seis etapas

### 1. Conceito, não nome

Declare **conceitos**, e para cada um as **representações** com seu normalizador
(`conceitos.py`). Quatro normalizadores cobrem quase tudo:

| normalizador | SQL | por quê |
|---|---|---|
| `texto` | `nullif(trim(lower(cast(c as varchar))), '')` | código alfanumérico |
| `inteiro` | `try_cast(nullif(trim(cast(c as varchar)),'') as bigint)` | a MESMA chave é `integer` numa tabela e `varchar` noutra; sem isso nunca casa |
| `documento` | tira pontuação + `lpad` 11/14 | CPF gravado como número **perde o zero à esquerda** |
| `ibge` | trunca para 6 dígitos | município com e sem dígito verificador |

Três distinções que custam caro se ignoradas:

- **Representação ≠ conceito.** `idpronac` (surrogate) e `pronac` (negócio)
  identificam o mesmo projeto, mas `212345` numa não é `212345` na outra. Meça a
  sobreposição **dentro** de cada representação; a tradução entre elas é uma ponte declarada.
- **Sufixo de papel não separa entidade.** `idagenteapoiado`, `idagentedestino` e
  `idagenteexecutor` são o mesmo agente; os valores saem da mesma tabela.
- **Coluna técnica não é chave.** Filtre `_fatia`, `hashregistro`, `dtatualizacao`
  (no MCid: `dbt_*`, `dt_*`, `*_snapshot` e as dimensões do SIDRA `variavel_id`,
  `localidade_id`…).

🔴 **Entidade ≠ dimensão conformada.** `uf`, `mes`, `ano`, `edicao` e o código
IBGE de município estão em quase toda tabela do produto e todos se sobrepõem —
isso não diz nada sobre a tabela, só que ela é recortada por município ou por
mês. Declare esses conceitos com `entidade: False`: são perfilados (viram grupo
de pesquisa e entram no grão), mas **nunca** viram aresta. No catálogo do MCid
publicado em set/2026, **35 de 61 relações** eram por `municipio_ibge` — uma
estrela em volta da tabela de municípios, que a expansão do RAG percorre e que
não autoriza JOIN nenhum que o LLM já não soubesse fazer pelo nome da coluna.
Cruzar por município continua possível; o que não se faz é publicar isso como
evidência de relação entre as tabelas.

**Chave composta declarada.** Contrato de documentação que declara
`chave: [apf, mes]` (PR 213 do cidades, `meta.documentacao.chave`) é uma
representação composta: perfile a combinação como composta (separador que não
ocorre nos valores), não cada parte isolada. `mes` sozinho não identifica nada.

### 2. Deixe a cauda longa entrar automaticamente

Qualquer nome com **forma de identificador** presente em **3+ tabelas** entra sem
curadoria (`auto_chaves.py`, `MIN_TABELAS = 3`). `idreadequacao` está em 22
tabelas, `idparecerista` em 8 — são ligações reais do fluxo interno.

> Quem decide o que sobrevive é a **evidência medida**, não o julgamento sobre o
> nome. Foi o julgamento que errou na primeira versão.

### 3. Perfile: contagem exata + sketch, UMA varredura por tabela

Uma query por tabela mede todas as chaves dela e devolve duas coisas:

- **linhas, não-nulos e distintos EXATOS** → vira granularidade.
  ⚠️ **Não use `approx_distinct`**: erra ~2,3%, e perto da razão 1,0 isso é a
  diferença entre "chave natural" e "quase chave" — exatamente onde a decisão importa.
- **sketch bottom-k**: os `K=2048` menores `xxhash64` dos valores distintos.

Duas razões para o desenho:

**Uma varredura só.** Em schema cheio de view (o `salic` é), cinco chaves não
podem reexecutar a view cinco vezes. Expanda as expressões depois de ler a relação.

**Bottom-k, não amostra aleatória.** Amostra não estima sobreposição: 2.000
valores sorteados de 277 mil contra 2.000 de 313 mil quase não se cruzam mesmo
que o universo seja idêntico. Bottom-k usa o mesmo critério determinístico dos
dois lados. E comparar todo par por `JOIN` seriam dezenas de milhares de
queries; por sketch é operação de conjunto em memória.

### 4. Forme pares, com corte por esparsidade

**Intra-representação:** cada tabela contra o hub global, o hub do **próprio
produto** e o hub do **produto × camada**.
⚠️ Hub só global perde relação intra-produto — erro cometido no MCid, onde o hub
de `apf` caiu noutro schema e os pares fortes de `empreendimento_far` foram descartados.

🔴 **No medalhão, schema não é produto.** Com `bronze`/`prata`/`ouro`
compartilhados, "hub do próprio schema" vira hub da camada inteira e mistura FAR
com Rural. O produto vem do prefixo declarado no repositório (no cidades:
`dbt/mcid/governance/dominios.yml`, `prefixos_de_tabela`), com fallback pelo
nome (`ouro_ts_*` → `ts`). O nível **produto × camada** existe para a tabela de
GRÃO da camada virar hub: na ouro do FDS é a `ficha` (uma linha por APF), e é
contra ela que a série mensal precisa ser medida. Com hub só por produto, o hub
cai na prata (mais distintos) e o par que a pergunta usa —
`ouro_fds_evolucao_financeira_chart.apf → ouro_fds_ficha_empreendimento.apf` —
nunca é comparado.

⚠️ **Espelho exige o mesmo grão.** A regra herdada do MinC descartava como
"espelho" o par com universo idêntico no mesmo schema (lá: view duplicando
tabela). No medalhão isso pega a ficha (uma linha por APF) contra a série
mensal do mesmo produto — mesmos APFs, grãos diferentes, e é justamente por
eles que se cruzam. Espelho agora exige também o **mesmo número de linhas**;
no MCid isso salvou 9 pares.

**Entre representações:** hub contra hub, como descoberta, e **somente entre
chaves esparsas**.

🔴 **O corte por esparsidade é o ponto mais importante desta etapa, e é
estatística, não gosto.** Duas sequências densas `1..N` e `1..M` se sobrepõem em
`min(N,M)` valores **sempre**: o Jaccard delas é N/M e não depende de terem
relação nenhuma. Na medição real isso produziu `idlote` × `pes_codigo` com
Jaccard 0,86 e 292 mil chaves em comum — número grande, significado zero.

Quando as duas chaves são densas, **nada na sobreposição separa coincidência de
relação**. Em chave esparsa o argumento se inverte: CPF e PRONAC vivem num espaço
enorme, onde coincidir centenas de milhares de valores por acaso é impossível.

Efeito medido do corte: descobertas cruzadas de **1.174 → 75**, candidatas de
**1.260 → 240**, tabelas com mais de vinte relações de **60 → 3**.

Limiares de referência: `MIN_DISTINTOS = 20` (abaixo disso é domínio fechado,
não chave), `MIN_JACCARD_DESCOBERTA = 0.05`, `DENSIDADE_SEQUENCIAL = 0.5`.

### 5. Confirme por contagem exata

O sketch tem erro de poucos por cento: bom para varrer, **insuficiente para
afirmar "cobertura 100%"**. As candidatas fortes voltam ao banco e são contadas
de verdade.

- confirmação começa em **0,90** de cobertura estimada (não pague JOIN caro em
  candidato que continuaria parcial)
- corte de validação: **0,95**
- só relação com contagem exata vira `validated_observed`

**Meça também o JOIN cru.** A comparação é feita sobre valores normalizados,
então o par prova `normalized_exact` — e a ingestão só torna elegível o que
`OM_SQL_ELIGIBLE_JOIN_RULES` autoriza (default `exact`). Em vez de afrouxar a
regra, conte a interseção dos valores **crus** nos pares que serão validados:
se ela é igual à normalizada, `a.col = b.col` funciona como está e o JOIN é
publicado como `exact`. No MCid: 182 de 193 pares.

### 6. Meça o grão junto

Razão `distintos / não-nulos` da chave:

| razão | leitura |
|---|---|
| **= 1,00** | a tabela **tem** o grão daquela entidade — uma linha por projeto |
| **< 1,00** | o grão é **mais fino** — cada entidade aparece `1/razão` vezes |

Publique como **propriedade própria** (`mincTableGrain`, `mcidTableGrain`), não
só como prosa, para dar para filtrar e comparar.

**Grão declarado é hipótese; o medido é o fato.** O contrato de documentação
(PR 213 do cidades: `meta.documentacao.grao` e `.chave`, publicados como
`mcidGrao`/`mcidChave`) diz o que a tabela *deveria* ser. Perfile a chave
declarada como qualquer outra e publique o medido **ao lado**, em propriedade
separada — `mcidTableGrain` (medido) não substitui `mcidGrao` (declarado).
Divergência entre os dois é defeito de modelo ou de documentação, e é
exatamente o que ninguém vê sem medir.

⚠️ O sync do cidades (`sync_semantic_relationships`) publica o Markdown e a
`mcidRelatedTables`, **mas não o grão**, embora o catálogo o meça
(`table_grain`). Sem isso o grão publicado envelhece: o que está no OM foi
medido em set/2026 (742 APFs em 6.499 linhas na série do FAR); hoje são 759 em
7.018.

> Isto é o que impede somar medidas depois de um JOIN que multiplica linhas. No
> MinC, **277 de 547 tabelas (52%)** carregam o alerta de duplicação — e a frase
> vai no prompt, onde o LLM a obedece. Vale mais que qualquer aresta.

## A armadilha que o próprio método cria

Normalizar chave inteira faz IDs de **sistemas diferentes** se sobreporem por
coincidência: SALIC e TransfereGov numeram órgãos a partir do 1, então `idorgao`
e `id_orgao_superior` batem quase 100% sem relação nenhuma.

Defesa: meça também a **densidade da faixa** (`distintos / (max - min + 1)`).
Perto de 1,0 é sequência densa; duas densas de sistemas diferentes são rebaixadas
a `candidate_observed` com `confidence: low` e nota explícita.

## Classificação de saída

| classe | significado | vira `SEMANTIC_JOIN`? |
|---|---|---|
| `dbt_relationships_test` | contrato do dbt nas colunas exatas | **sim** |
| `validated_observed` | sobreposição alta **confirmada por contagem exata** | **sim** |
| `candidate_observed` | sobreposição parcial: pista, exige revisão | **não** — o adapter descarta |

As pontas de todo teste `relationships` do dbt entram no perfil **mesmo** quando o
nome é raro ou é chave composta. Contrato não depende da redução por hubs.

## Onde o resultado mora

O produto deste método é um **arquivo** (`mcid.yaml`) versionado no
repositório de dados e publicado pela DAG de ingestão do OpenMetadata
(`helpers/openmetadata/semantic_relationships.py:sync_semantic_relationships`,
task `sync_mcid_semantic_relationships`). Não publique direto no OM a partir
da máquina de alguém: a instância é compartilhada, a publicação avulsa não
deixa rastro, e foi assim que o MCid acabou com relações sem dono, apontando
para tabelas que não existem.

**Testar antes de publicar.** A ingestão do RAG aceita
`OM_EXTENSION_OVERRIDES_FILE` (só desenvolvimento): um JSON
`{fqn: {propriedade: valor}}` aplicado sobre a `extension` lida do OM. Gere o
Markdown com as **funções do próprio repositório de dados**
(`load_semantic_catalog` + `render_table_markdown` + `_related_table_keys`),
para o RAG ler byte a byte o que seria publicado. Ingerir num grafo e numa
collection **separados** da baseline: o writer só faz `MERGE` e misturaria as
arestas.

## Dialeto: Trino → PostgreSQL

| Trino (MinC) | PostgreSQL (MCid) |
|---|---|
| `xxhash64(to_utf8(k))` | `hashtextextended(k, 0)` — o sketch só precisa do MESMO hash dos dois lados |
| `try_cast(x as bigint)` | `case when x ~ '^-?[0-9]{1,18}$' then cast(x as bigint) end` |
| `unnest(array) with ordinality` + `sequence(1, n)` | `cross join lateral unnest(...) with ordinality` + `generate_series(1, n)` |
| `query_max_run_time` | `statement_timeout` na conexão |
| — | `default_transaction_read_only=on` na conexão: o **servidor** recusa escrita |

Banco na mesma rede: conexão direta, sem túnel e sem Trino. Paralelismo 2
continua sendo o default; no MCid a rodada inteira levou 84 s.

## Como o RAG consome

O adapter (`src/rag/components/sources/relationship_adapters.py`) lê a Custom
Property em Markdown e só converte em `SEMANTIC_JOIN` o que for **validado** —
candidatas são contadas e ignoradas. Três pontos de atenção:

1. **`catalog_id`** — o adapter valida o cabeçalho `Catálogo \`<id>\``. Catálogo
   novo exige registrar o id em `build_relationship_adapter`, senão a ingestão
   aborta na primeira tabela.
2. **`sql_join_eligible`** vem de `OM_SQL_ELIGIBLE_JOIN_RULES` (default `exact`).
   Se o gerador emite `normalized_exact` fixo, **nada fica elegível** e a expansão
   por evidência não acha vizinho.
3. **Relação para tabela fora do escopo ingerido** é fronteira, não erro — deve
   ser contada e pulada, não abortar.
4. **Relação que a `*RelatedTables` não confirma** é catálogo inconsistente
   (duas propriedades escritas por processos diferentes): a aresta fica de fora
   e é contada em `semantic_relationships_unconfirmed`, sem derrubar as outras.
5. **Coluna inexistente numa tabela que ESTÁ no snapshot** continua abortando —
   é nome errado no catálogo. Por isso o gerador mede só colunas publicadas.

🔴 **Aresta correta não é, sozinha, retrieval melhor — meça antes de publicar.**
No MCid (out/2026) o catálogo novo levou o grafo de 11 para 145 relações
elegíveis e as ilhas de 95% para 70% — e o eval de retrieval da Persona 2
**piorou**: suficiência 6/8 → 5/8, superexpansão 17× → 33×, prompt p50 +52%.
Cortar o hub global (116 relações) não mudou o quadro. As causas estão no
consumo, não nas arestas:

- a expansão `EXPANSION_MODE=evidence` admite **todo** vizinho com join
  elegível — calibrada quando o MCid tinha 6 joins elegíveis;
- o card v2 de tabela **embeda a lista de relacionamentos**: arestas novas
  mudam o texto de dezenas de cards e deslocam as sementes (uma tabela central
  saiu do top-5 sem que o grafo tivesse nada a ver com a semente).

Então: publique para pessoas e para o catálogo quando a relação for verdade;
para o RAG, rode o gate do ADR-0012 com a candidata (grafo e collection
separados) e ajuste o consumo (expansão, card) **antes** de ligar no
ambiente. Relatório da medição: `eval/experiments/20261026-medalhao-skill-relacoes.md`.

## Verificação — não confie na contagem da ingestão

```bash
# 1. as arestas existem e são elegíveis?
MATCH ()-[r:SEMANTIC_JOIN]->() RETURN r.validation, r.sql_join_eligible, count(*)

# 2. o total BATE com o que a ingestão reportou?
MATCH ()-[r:SEMANTIC_JOIN]->() RETURN r.ingestion_run_id, count(*)
```

🔴 **A contagem da ingestão reporta o que ela ESCREVEU, não o que sobrou no
grafo.** Qualquer propriedade que entre no hash do `relationship_id` (inclusive
`source_kind`) muda o id, e a reingestão **cria aresta nova** em vez de atualizar
— duplicando tudo em silêncio. Aconteceu: 250 escritas, 500 no grafo.

```bash
# 3. quantas tabelas ficaram ilha? (antes/depois — é o número que a skill move)
MATCH (t:Table) OPTIONAL MATCH (t)-[:HAS_COLUMN]->(:Column)-[j:SEMANTIC_JOIN]-()
WITH t, count(j) AS g RETURN t.schema, sum(CASE WHEN g=0 THEN 1 ELSE 0 END), count(*)
```

```bash
# 4. o catálogo descreve o MESMO banco que executa o SQL?
SELECT table_schema||'.'||table_name||'.'||column_name
FROM information_schema.columns WHERE table_schema IN (...)
```
Compare com as colunas do grafo. Divergência aqui já custou 390 colunas
invisíveis (ADR-0019) — e o sintoma é "o modelo errou o SQL", que manda investigar
no lugar errado.

## Limites, ditos na cara

- **Sobreposição é do dado de HOJE.** Não há constraint que a mantenha verdadeira
  amanhã. Por isso `validated_observed` é **evidência**, não FK.
- **Conceito é declarado por pessoa.** Coluna cujo nome não casa com padrão nenhum
  e não está na cauda automática **não é medida**. Ao acrescentar fonte nova, rode
  auditoria de lacuna antes de confiar na cobertura.
- **Nome com forma de identificador em 2 tabelas fica de fora** do automático
  (`MIN_TABELAS = 3`).
- **Paralelismo alto derruba view pesada.** No Trino do MinC, 2 consultas
  simultâneas é o limite estável; 6 fizeram as views do SALIC baterem no teto.
- **Toda etapa deve ser retomável** — grava checkpoint e pula o que já fez.
  Perfilamento de 640 tabelas não pode recomeçar do zero por causa de um timeout.
- **Hub ainda gera estrela.** Cada chave é comparada com até três hubs; a
  tabela com mais distintos de uma representação (no MCid,
  `prt_ts_acompanhamento` para `apf`) acumula grau alto. É evidência real, mas
  a expansão por evidência a percorre — vigiar o `overexpansion` no eval.
- **Relação fica velha.** O catálogo é foto do dado do dia da medição; refazer
  a rodada é barato (minutos), deixar de refazer não é.

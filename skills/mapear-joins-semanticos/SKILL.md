---
name: mapear-joins-semanticos
description: Descubra por onde duas tabelas se ligam quando o banco não declara FK, medindo evidência no dado, e publique o resultado no OpenMetadata como SEMANTIC_JOIN e grão. Use ao preparar um escopo novo para o GraphRAG, ao trocar de base, quando a ingestão reporta SEMANTIC_JOIN=0, quando o catálogo perdeu as chaves de junção, ou ao auditar cobertura de relações existentes.
---

# Mapear joins semânticos a partir da evidência

O GraphRAG expande o subgrafo por `SEMANTIC_JOIN` (ADR-0011). Sem essa aresta o
grafo vira um conjunto de ilhas e a expansão não faz nada. Quando o banco não
declara FK — que é o caso normal em data warehouse analítico — a joinabilidade
precisa ser **medida no dado** e publicada no OpenMetadata.

Implementação de referência: `data-application-minc/scripts/semantica/`.
Resultado no MinC: **1.633 medições em 554 de 640 tabelas**, 250 relações
publicadas, 546 grãos medidos.

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
pago sem recuperar a capacidade.

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
- **Coluna técnica não é chave.** Filtre `_fatia`, `hashregistro`, `dtatualizacao`.

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

**Intra-representação:** cada tabela contra o hub do **próprio schema** *e*
contra o hub global.
⚠️ Hub só global perde relação intra-schema — erro cometido no MCid, onde o hub
de `apf` caiu noutro schema e os pares fortes de `empreendimento_far` foram descartados.

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

### 6. Meça o grão junto

Razão `distintos / não-nulos` da chave:

| razão | leitura |
|---|---|
| **= 1,00** | a tabela **tem** o grão daquela entidade — uma linha por projeto |
| **< 1,00** | o grão é **mais fino** — cada entidade aparece `1/razão` vezes |

Publique como **propriedade própria** (`mincTableGrain`), não só como prosa, para
dar para filtrar e comparar.

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
# 3. o catálogo descreve o MESMO banco que executa o SQL?
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

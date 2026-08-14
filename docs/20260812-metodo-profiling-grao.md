# Método: descobrir o grão das tabelas por profiling empírico

- **Data**: 2026-08-12
- **Contexto**: itens 1.1, 4.1 e 4.2 do
  [plano de melhoria](./20260811-plano-melhoria-retrieval.md)
- **Para quem**: quem for aplicar isso a outro catálogo do GovHub

## Por que isso existe

O grão de uma tabela — *quantos registros ela tem por entidade de negócio* — é o
que impede o gerador de SQL de somar linhas repetidas. Sem ele, "os 10
empreendimentos com maior valor contratado" devolve **5 empreendimentos**
repetidos duas vezes, e o gráfico dobra os valores.

O problema é que **o grão não é inferível dos metadados** neste catálogo:

| declaração | quantidade |
|---|---|
| tabelas com PRIMARY KEY | **0 de 74** |
| colunas com `constraint` preenchido | **0 de 1.215** |
| FOREIGN KEY no grafo | **0** |

Toda a estrutura vem da Custom Property de relações semânticas. Então: se o
catálogo não declara, **meça no dado**. O grão é observável.

> **Definição operacional.** O grão é o menor conjunto de colunas cujo
> `count(DISTINCT ...)` iguala o `count(*)` da tabela.

## O método, em três passadas

### Passada 0 — de onde saem as colunas candidatas

Testar todas as combinações de colunas é combinatorialmente inviável. Duas
fontes bastam, e a primeira é a mais valiosa:

1. **Chaves de negócio que o catálogo já declara.** No MCid, a propriedade
   `semantic_key` das relações `SEMANTIC_JOIN` é literalmente a lista de chaves
   pelas quais as tabelas se ligam. Uma consulta ao grafo devolve:

   ```cypher
   MATCH ()-[r:SEMANTIC_JOIN]->() RETURN DISTINCT r.semantic_key
   ```

   No MCid isso deu `apf`, `cnpj_eo`, `id_proposta`, `periodo` — quatro chaves
   que cobrem a maior parte das tabelas.

2. **Heurístico de nomes**, como rede de segurança: `apf`, `cnpj`, `cpf`, `uf`,
   `municipio`, `regiao`, `mes`, `ano`, `periodo`, `trimestre`,
   `data_referencia`, `codigo`, `*_id`.

Limite a ~6 candidatas por tabela para manter uma query por tabela.

### Passada 1 — a chave é única?

Uma query por tabela:

```sql
SELECT count(*) AS n,
       count(DISTINCT "apf")    AS d_apf,
       count(DISTINCT "uf")     AS d_uf
FROM   empreendimento_far.empreendimento;
```

Classificação:

| condição | classe | conclusão |
|---|---|---|
| alguma candidata com `d == n` | **ÚNICA** | grão = essa coluna |
| melhor candidata com `d < n` | **repetida** | vai para a passada 2 |
| nenhuma candidata | — | precisa de revisão de domínio |

### Passada 2 — repetição legítima ou defeito de dado?

Esta é a passada que **não pode ser pulada**, e é onde está a armadilha. Uma
tabela em que `apf` se repete pode ser duas coisas opostas:

- **série temporal legítima** → grão composto `(apf, mês)`, linhas diferentes;
- **fan-out / defeito de carga** → linhas **idênticas** repetidas.

A distinção é observável comparando a tabela com ela mesma deduplicada:

```sql
SELECT count(*) AS n,
       (SELECT count(*) FROM (SELECT DISTINCT * FROM tbl) d) AS n_distintas
FROM   tbl;
```

- `n > n_distintas` → **linhas exatamente duplicadas**. É defeito de dado, a
  montante do RAG.
- `n == n_distintas` → repetição legítima; procure o par que fecha o grão,
  testando a melhor candidata com a primeira coluna temporal:

```sql
SELECT count(*) AS n, count(DISTINCT ("apf", "mes")) AS d FROM tbl;
```

## O que isso encontrou no MCid

| classe | tabelas |
|---|---:|
| chave única | 32 |
| grão composto confirmado | 10 |
| **linhas exatamente duplicadas** | **8** |
| sem candidata / não fechado | 24 |
| **total** | **74** |

As 8 com duplicação exata — sete delas em `empreendimento_far`:

| tabela | linhas | únicas | fator |
|---|---:|---:|---:|
| `empreendimento_far.evolucao_financeira_chart` | 32.504 | 4.063 | **8,00×** |
| `empreendimento_far.execucao_fisica_financeira_chart` | 17.366 | 4.620 | 3,76× |
| `entidades_fds.fds_dados_prioritarios_entregas` | 23.080 | 11.540 | 2,00× |
| `empreendimento_far.dados_prioritarios_caixa` | 8.886 | 4.443 | 2,00× |
| `empreendimento_far.evolucao_financeira` | 8.126 | 4.063 | 2,00× |
| `empreendimento_far.empreendimento` | 1.646 | 823 | 2,00× |
| `empreendimento_far.ficha_empreendimento` | 1.646 | 823 | 2,00× |
| `empreendimento_far.panorama_estadual` | 1.366 | 683 | 2,00× |

**Qualquer `SUM` ou `COUNT` nessas tabelas sai inflado de 2× a 8×** — não é só o
gráfico. Isso é bug de carga do pipeline FAR e deve ser reportado ao time de
dados; o RAG apenas se defende dele.

## Onde o grão é consumido — e onde NÃO deve ir

Isto foi medido, não suposto:

**❌ Não colocar o grão no card (o texto embutido).** Testado em 2026-08-12: o
MRR cai de 0,875 para 0,792 e o prompt cresce. Faz sentido — "um registro por
apf" se repete em dezenas de tabelas, então não discrimina nada na busca.

**✅ Colocar na serialização do subgrafo**, que é o texto que chega ao LLM.
O `Neo4jSubgraphRetriever._serialize_table` emite uma linha `Grão:` por tabela.
Lembre que o card é **só embedding** — ele nunca chega ao LLM.

A frase deve carregar a **instrução**, não só o fato:

```
Grão: um registro por apf, mas a tabela contém LINHAS DUPLICADAS (2.00x)
      — use DISTINCT ou agregue por apf antes de somar
```

Resultado medido, na mesma pergunta:

```
sem grão : 10 linhas,  5 empreendimentos distintos   ← o bug
com grão : 10 linhas, 10 APFs distintos              ← corrigido
```

O gerador passou a emitir `SELECT DISTINCT apf, ...` sozinho.

## Como replicar em outro catálogo

1. Rode a passada 0 no grafo para extrair as chaves de negócio declaradas.
2. Rode as passadas 1 e 2 (uma query por tabela, sem LLM — determinístico e de
   custo zero). Scripts de referência: `profile_grain.py`, `profile_grain2.py`,
   `build_grain_map.py`, apoiados em `mcp_sql.py`.
3. **Revise com o time de domínio.** O profiling propõe; quem confirma é quem
   conhece o negócio. Uma chave única no dado de hoje pode ser coincidência de
   volume.
4. Publique o mapa em `OM_GRAIN_KEYS` (JSON `FQN -> frase`, **uma linha**, sem
   cercas markdown no `.env`).
5. Reporte as tabelas com duplicação exata ao time de dados.

### Sobre o `mcp_sql.py`

O `McpSqlExecutor` de produção usa um `Agent` com LLM porque precisa autocorrigir
SQL gerado. Para perfilar queremos o oposto: determinismo e custo zero. O helper
fala o protocolo MCP direto, com o transporte **SSE clássico** (`GET /sse` abre o
stream e informa o endpoint de POST).

⚠️ O `MCPToolset` do Haystack tenta `POST /sse` (transporte *Streamable HTTP*) e
recebe **405** do `postgres-mcp` desta versão. Use `mcp.client.sse.sse_client`.

## Limitações honestas

- **O grão medido é do dado de hoje.** Uma tabela pequena pode ter chave única
  por acidente. Revisão de domínio não é opcional.
- **24 tabelas ficaram sem grão** — sem candidata pelos critérios usados, ou grão
  de 3+ colunas. Precisam de revisão manual.
- **A passada 2 é O(n) sobre a tabela inteira** (`SELECT DISTINCT *`). Em tabelas
  grandes, amostre ou rode fora do horário de pico. Aqui a maior tinha 32 mil
  linhas.
- **A casa definitiva do grão é o catálogo**, não o `.env`. O caminho natural é
  uma Custom Property no OpenMetadata, igual às relações semânticas, virando
  propriedade do nó `Table` na ingestão. A config é a ponte até lá.

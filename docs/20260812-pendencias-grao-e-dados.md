# Pendências de grão e defeitos de dado — lista de trabalho

- **Data**: 2026-08-12
- **Origem**: profiling empírico descrito em
  [`20260812-metodo-profiling-grao.md`](./20260812-metodo-profiling-grao.md)
- **Para quem**: time de domínio (parte 1) e time de dados (parte 2)

Este documento é uma **lista de trabalho**, não um diagnóstico fechado. As duas
partes são independentes e podem correr em paralelo.

Estado atual: **50 de 74 tabelas** com grão declarado em `OM_GRAIN_KEYS`.

---

## Parte 1 — 24 tabelas sem grão declarado

Estas tabelas ficaram de fora do mapa porque nenhuma coluna candidata isolada é
chave, e o par testado (melhor candidata + primeira coluna temporal) também não
fechou. Não é erro: em geral significa **grão de três ou mais colunas**, ou uma
chave cujo nome não bate com os padrões usados.

Consequência prática: para essas tabelas o contexto do LLM **não traz a linha
`Grão:`**, então o gerador de SQL não recebe orientação de deduplicação nem de
granularidade. Nenhuma delas está entre as com duplicação exata (parte 2), então
o risco imediato é baixo — mas agregações continuam sem guarda.

| tabela | colunas | hipótese de grão a confirmar |
|---|---:|---|
| `conjuntura_bronze.bronze_bacen_financiamentos_imobiliarios` | 5 | `data` + série/indicador |
| `conjuntura_bronze.bronze_ibge_pib_construcao_civil` | 13 | `categoria_id` + `periodo` |
| `conjuntura_bronze.bronze_ibge_pnadc_ocupados_construcao` | 13 | `periodo` + recorte |
| `conjuntura_bronze.bronze_ibge_pnadc_rendimento_construcao` | 13 | `periodo` + recorte |
| `conjuntura_bronze.bronze_ibge_sinapi` | 13 | `periodo` + UF ou item |
| `conjuntura_bronze.bronze_imob_infomoney` | 8 | sem candidata — definir |
| `conjuntura_gold.gold_abecip_novos_financiamentos_imobiliarios` | 13 | `ano` + mês/indicador |
| `conjuntura_gold.gold_balancos_empresas_lancamentos` | 8 | sem candidata — definir |
| `conjuntura_gold.gold_balancos_empresas_vendas` | 8 | sem candidata — definir |
| `conjuntura_gold.gold_fgts_renda_familiar` | 10 | sem candidata — definir |
| `conjuntura_gold.gold_financiamento_pf_por_faixa` | 9 | faixa de renda |
| `conjuntura_gold.gold_indicadores_financiamento_pf` | 19 | sem candidata — definir |
| `conjuntura_gold.gold_precos_construcao` | 11 | **tabela de 1 linha** (wide) |
| `conjuntura_gold.gold_uh_condicao_uso` | 11 | condição de uso |
| `conjuntura_silver.silver_abecip_novos_financiamentos_imobiliarios` | 7 | `ano` + mês |
| `conjuntura_silver.silver_abecip_sbpe_financiamentos_habitacionais` | 21 | `mes` + `ano` + recorte |
| `conjuntura_silver.silver_bacen_financiamentos_imobiliarios` | 5 | `data_referencia` + série |
| `conjuntura_silver.silver_balancos_empresas` | 7 | empresa + período |
| `conjuntura_silver.silver_ibge_pib_construcao_civil` | 5 | `periodo` + categoria |
| `empreendimento_far.consolidado` | 43 | `id_proposta` + algo |
| `empreendimento_far.financeiro_mensal` | 21 | `apf` + mês |
| `empreendimento_far.mapa_nacional` | 12 | `uf` + `nivel` |
| `empreendimento_far.resumo_gerencial` | 11 | sem candidata — definir |
| `entidades_fds.fds_financeiro_mensal` | 22 | `apf` + mês |

Vários casos são **quase óbvios** (`apf` + mês nas tabelas mensais, `uf` +
`nivel` no `mapa_nacional`) e só não fecharam porque a coluna temporal tem nome
fora do heurístico. Vale uma segunda rodada de profiling com as colunas reais
dessas tabelas antes de escalar para revisão manual.

**Como preencher**: acrescente a entrada em `OM_GRAIN_KEYS` no `.env`
(JSON `FQN -> frase`, uma linha). A frase vai direto ao contexto do LLM, então
escreva-a como **instrução**, não como rótulo:

```
"Cidades.cidades.empreendimento_far.financeiro_mensal":
  "um registro por apf e mês de referência"
```

---

## Parte 2 — 8 tabelas com linhas exatamente duplicadas

Aqui não é ausência de declaração: é **defeito de dado**. A linha inteira está
repetida, verificado comparando `count(*)` com `count(*)` sobre
`SELECT DISTINCT *`.

| tabela | linhas | únicas | fator | chave |
|---|---:|---:|---:|---|
| `empreendimento_far.evolucao_financeira_chart` | 32.504 | 4.063 | **8,00×** | `apf` |
| `empreendimento_far.execucao_fisica_financeira_chart` | 17.366 | 4.620 | **3,76×** | `apf` |
| `entidades_fds.fds_dados_prioritarios_entregas` | 23.080 | 11.540 | 2,00× | `apf` |
| `empreendimento_far.dados_prioritarios_caixa` | 8.886 | 4.443 | 2,00× | `apf` |
| `empreendimento_far.evolucao_financeira` | 8.126 | 4.063 | 2,00× | `apf` |
| `empreendimento_far.empreendimento` | 1.646 | 823 | 2,00× | `apf` |
| `empreendimento_far.ficha_empreendimento` | 1.646 | 823 | 2,00× | `apf` |
| `empreendimento_far.panorama_estadual` | 1.366 | 683 | 2,00× | `uf` |

**Sete das oito estão em `empreendimento_far`.** Conjuntura está limpa. O padrão
— fatores exatos de 2,00× em cinco tabelas — sugere carga executada duas vezes
sem truncar, ou um JOIN com fan-out no pipeline que popula o schema.

### Impacto medido

Sem defesa, qualquer agregação sai inflada pelo fator da tabela:

```
Unidades habitacionais por UF (empreendimento_far.empreendimento)
  BA   32.386  →  16.193
  SP   19.720  →   9.860
  MG   19.270  →   9.635
```

E rankings colapsam: "top 10 empreendimentos" devolvia **5 distintos**,
cada um duas vezes.

### O que já está mitigado no RAG

O grão dessas tabelas carrega a instrução de deduplicar, e o `SYSTEM_PROMPT`
manda respeitá-la. O gerador passou a emitir:

```sql
SELECT uf, SUM(quantidade_uh) AS total_uh
FROM  (SELECT DISTINCT apf, uf, quantidade_uh
       FROM empreendimento_far.empreendimento) d
GROUP BY uf ORDER BY total_uh DESC LIMIT 10;
```

**Isso é defesa, não correção.** Enquanto o dado estiver duplicado:

- toda consulta paga o custo do `DISTINCT`;
- qualquer consumidor que não seja este RAG (BI, notebook, API) continua vendo
  número errado;
- se o fator mudar (de 2× para 3×, por exemplo) a frase de grão fica
  desatualizada silenciosamente.

### Recomendação

Corrigir na origem, no pipeline que popula `empreendimento_far`. Depois de
corrigir, **rodar o profiling de novo** e atualizar `OM_GRAIN_KEYS` — as frases
com aviso de duplicação devem voltar a ser simples (`"um registro por apf"`).

Um teste de unicidade por tabela no pipeline de carga evitaria a reincidência:

```sql
SELECT count(*) - count(DISTINCT apf) AS duplicadas
FROM   empreendimento_far.empreendimento;  -- deve ser 0
```

---

## Como reverificar tudo

Os scripts do profiling são determinísticos e não usam LLM. Ver a seção
"Como replicar em outro catálogo" do
[método](./20260812-metodo-profiling-grao.md).

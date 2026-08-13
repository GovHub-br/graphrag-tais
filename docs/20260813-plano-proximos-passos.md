# Plano de execução — fechar o gap de seleção de tabela

- **Data**: 2026-08-13
- **Contexto**: [diagnóstico operacional](./20260813-plano-melhoria-operacional.md)
  e os resultados de A1/A2 medidos hoje.

## Ponto de partida

Tudo saturou, menos uma coisa:

| eixo | estado |
|---|---|
| suficiência estrutural, fidelidade, grão, JOIN não autorizado | **1,0 / 1,0 / 1,0 / 0,0** |
| contaminação de domínio | 8% |
| latência | 10,8s (era 20,6s) |
| **seleção de tabela no operacional** | **5/12 — 42%** |

Decomposição dos 12 casos operacionais:

```
5  acertam
4  a tabela ESTÁ no contexto e o LLM escolhe a irmã errada   → A3
3  a tabela NÃO chega ao contexto                            → B1
```

---

## D1/D2 — as métricas viram parte do eval

**Por quê primeiro.** Foram elas que revelaram que 2/3 das falhas são de
geração, e hoje eu as calculo num script à parte. Sem elas, A3 e B1 seriam
avaliadas de forma improvisada, e o número não entraria em nenhum relatório.

**O que muda em `eval/answer/`:**

1. `live.py` passa a registrar `context_tables` — as tabelas que a recuperação
   entregou ao LLM. Hoje o dado existe em memória e é descartado.
2. `metrics.py` ganha três métricas, todas determinísticas:

| métrica | definição | lê |
|---|---|---|
| `expected_table_match` | alguma tabela esperada aparece no SQL gerado | gold + SQL |
| `retrieval_miss` | a tabela esperada **não chegou** ao contexto | gold + `context_tables` |
| `generation_miss` | chegou ao contexto, mas **não** foi para o SQL | os dois |

Por construção `expected_table_match + retrieval_miss + generation_miss = 1`
por caso, o que dá uma decomposição limpa de onde cada falha nasce.

3. O gold precisa declarar `expected_tables`. O `v0.2.0-draft` já declara; o
   `v0.1.0` usa `gold_subgraphs` — derivo dele os nós do tipo `Table`, sem
   alterar o arquivo.

**Aceite**: reproduz os números que calculei por fora (5/12 operacional,
4 `generation_miss`, 3 `retrieval_miss`). **Esforço**: ~1h. **Risco**: nenhum,
é instrumentação.

---

## A3 — prior de autoridade do Superset

**A ideia.** Produção já respondeu "qual tabela serve para qual pergunta" 171
vezes. Cada chart é um voto. `empreendimento_far.panorama_estadual` alimenta
"Valor Total Empreendimentos", "Municípios", "Estado", "UHs", "Situação dos
empreendimentos" — isso é conhecimento que o catálogo não tem e o RAG não vê.

**Extração** (somente leitura, já validada hoje): varrer os 171 charts e montar
`tabela física → [títulos dos charts que a usam]`, descartando datasources fora
do escopo de ingestão. Sai um mapa versionado, tipo o `OM_GRAIN_KEYS`.

**Três formas de injetar, testáveis separadamente:**

| # | onde | efeito esperado | custo |
|---|---|---|---|
| A3a | **no card** (lado do documento) — linha `Usada em produção para: …` | melhora recuperação: o card passa a casar com o vocabulário real da pergunta | reindexação |
| A3b | **na serialização** (lado do LLM) — `Em produção, esta tabela alimenta: …` | melhora a escolha entre irmãs | nenhum |
| A3c | **no ranking** — bônus por número de charts | desempata sem depender do LLM | nenhum |

Começo por **A3b**, que é a que ataca diretamente os 4 casos de geração e não
exige reindexar. A3a depois, se A3b render. **A3c fica por último e com
ressalva**: peso por popularidade penaliza tabela correta mas pouco usada, e
pode fossilizar o que produção já faz.

### ⚠️ O problema metodológico que isso cria

**As perguntas do `v0.2.0-draft` foram derivadas dos títulos dos charts. Se eu
injetar "esta tabela alimenta o chart X" e avaliar numa pergunta derivada do
chart X, estou medindo se copiei o gabarito para dentro da entrada.** O número
subiria e não significaria nada.

Por isso A3 **não pode ser aceita pelos números do `v0.2.0-draft`**. A validação
tem que ser independente:

1. **`v0.1.0`** (8 casos escritos à mão, anteriores ao Superset) — não pode
   regredir. É o guardrail.
2. **Perguntas escritas por humanos** na Fase C, sem olhar títulos de chart —
   é o teste de verdade.
3. **Hold-out**, se as duas anteriores não bastarem: construir o prior com
   metade dos charts e avaliar nas perguntas derivadas da outra metade.

Vou reportar o número no `v0.2.0-draft` de qualquer forma, mas **marcado como
contaminado** e sem valor de decisão.

**Aceite**: `expected_table_match` no `v0.1.0` não regride, e nas perguntas
humanas da Fase C o operacional sobe de forma visível. **Esforço**: ~3h.

---

## B1 — os 3 que não chegam ao contexto

Investigação caso a caso, sem solução pré-definida — as hipóteses são
diferentes e o remédio muda conforme a causa:

| hipótese | como verifico | remédio |
|---|---|---|
| vocabulário: a pergunta usa termo que o card não tem | inspecionar o card e o top-30 do pool | glossário (termo → tabela) |
| tabela ilha: sem relações, só chega como semente | consultar o grafo | quota garantida por família |
| top-5 saturado: 4 das 5 sementes são a mesma família | inspecionar as sementes | diversidade por nível de agregação |

**Aceite**: `retrieval_miss` cai de 3 para ≤1. **Esforço**: ~2h.

---

## Ordem, esforço e dependências

| # | item | esforço | depende de | quem |
|---|---|---|---|---|
| 1 | **D1/D2** — métricas no eval | ~1h | — | eu |
| 2 | **A3b** — autoridade na serialização | ~2h | 1 | eu |
| 3 | **B1** — investigar os 3 | ~2h | 1 | eu |
| 4 | **A3a/A3c** — card e ranking | ~2h | 2 render | eu |
| 5 | **Revisão dos 20 casos** | — | — | **vocês** |
| 6 | **Decisão de escopo** | — | — | **vocês** |

Os itens 5 e 6 correm em paralelo e **valem mais que 1–4 somados**.

---

## O que vocês destravam

**Decisão de escopo — o maior lever disponível.** `__dados_brutos.*` e
`empreendimento_rural.*` estão fora da ingestão. "Valor Planejado /
Suplementado / **Executado** FAR" — o conceito de "gastos" — só existe lá.
Enquanto não entrar, o sistema **acerta ao recusar**, e nenhum ajuste de
retrieval muda isso. Se entrar, uma fatia grande do operacional passa a ser
respondível.

Se a resposta for "não entra", então o trabalho vira **C2 — recusa
informativa**: dizer *"não tenho execução orçamentária; tenho valor contratado e
desembolsado"* em vez de "refine a pergunta".

**Revisão dos 20 casos.** Meus 42% podem ser 60% (se as tabelas alternativas
forem aceitáveis) ou 30% (se eu tiver sido generoso). Todo número operacional
que reportei carrega essa incerteza.

---

## Fora de escopo, com motivo

| item | por quê |
|---|---|
| métricas e travessia de JOIN | nenhuma query de produção faz JOIN |
| mais tuning de recuperação | responde por 1/3 das falhas e já rendeu |
| reranker | medido e rejeitado (reach@5 −20%, p50 49,7s) |
| trocar embedding | medido; ganho está no eixo menor |

## Pendências menores anotadas

- `.docx`: página 2 termina com 1/3 vazio e o gráfico fica sozinho na página 3.
- MCP do Superset: `list_dashboards` e `get_chart_info` retornam **500**.
- `Onda 0.2` (medir a serialização, não o grafo) segue aberta.

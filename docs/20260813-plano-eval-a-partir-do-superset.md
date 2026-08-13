# Plano: gold e avaliação a partir dos dashboards de produção (Superset)

- **Data**: 2026-08-13
- **Motivo**: percepção de que as respostas e gráficos não estão bons — confirmada
  pela análise abaixo.
- **Fonte nova**: MCP do Superset de produção (`10.0.0.32:30508`), **somente
  leitura**.

> **Regra operacional inegociável.** O Superset é produção. Este plano usa
> exclusivamente ferramentas com `readOnlyHint: true` (`list_*`, `get_*`).
> **Nada** de `generate_chart`, `update_chart`, `create_virtual_dataset`,
> `save_sql_query`, `generate_dashboard`, `add_chart_to_existing_dashboard`.
> `execute_sql` fica de fora também: já temos leitura ao mesmo banco pelo
> postgres-mcp em modo `RESTRICTED`, sem risco de escrever em produção.

---

## 1. A percepção está certa — e é pior do que parece

Dos 5 exemplos, **3 se recusaram a responder** e **1 respondeu com SQL
estruturalmente errado**. Só 1 de 5 saiu bom.

| # | pergunta | resultado | causa provável |
|---|---|---|---|
| 1 | "valores gastos com unidades habitacionais" | recusou | **vocabulário**: "gastos" não casa com `valor_contratado` / `valor_desembolsado` / "executado" |
| 2 | "valores contratados por semestre de 2020 a 2025" | recusou | **tempo derivado**: semestre não é coluna, sai de uma data. Além disso o modelo *afirmou* que não há dado antes de 2024 — ele não pode saber isso pelo schema |
| 3 | "unidades habitacionais contratadas por ano" | recusou | **recuperação errada**: foi para `conjuntura` (financiamentos) em vez de `empreendimento_far.empreendimento`, que tem `quantidade_uh` |
| 4 | "cbic lançamento por região" | SQL ruim | **tabela wide**: região é *coluna*, não linha. Devolveu 5 colunas sem período nenhum |
| 5 | "saldo CAGED" | ok | — |

### O que mais me preocupa

**A avaliação atual é cega para o modo de falha dominante.** O gold tem 8 casos
que todos respondem, então `answered_rate` dá 1,0000 sempre. Recusa indevida —
que é 60% das falhas reais — **não aparece em métrica nenhuma**. Estávamos
medindo com precisão a parte que já funcionava.

---

## 2. O que o Superset revelou

### 2.1 São 171 charts, e os nomes são as perguntas

Os títulos dos charts são literalmente o vocabulário de negócio da casa:

```
Valor Planejado FAR · Valor Suplementado FAR · Valor Executado FAR
Prazo Médio de Contratação em Dias · Taxa de Enquadramento
Motivos de Não Adequadamento · Situação das obras · Unidades Habitacionais
INCC-M · ICST · IVAR · IGP-M · ICC · FIPEZAP · SINAPI · PIB construção
```

Repare em **"Valor Executado FAR"**: é exatamente o conceito de "gastos" que a
pergunta 1 procurava e o RAG não achou. O vocabulário existe — na camada de
apresentação, que o RAG nunca viu.

### 2.2 Os datasets virtuais contêm o SQL de referência

Boa parte dos 99 datasets é `Query <schema>.<nome>` — SQL escrito por analista.
É a fonte natural de `reference_sql` para execution accuracy, sem inventar nada.

### 2.3 Os idiomas reais que o RAG nunca produz

Do SQL de produção:

```sql
-- pivot por FILTER (o idioma da casa para comparar períodos)
SUM(valor) FILTER (WHERE EXTRACT(YEAR FROM data_referencia) = 2024) AS "2024 (total)"

-- decodificação de período
CASE periodo WHEN '202603' THEN 'JAN-FEV-MAR 2026' END

-- códigos mágicos que definem o SIGNIFICADO da linha
WHERE variavel_id IN (11708, 11710, 11711)
```

### 2.4 O achado mais importante: semântica de VALOR, não de schema

`variavel_id = 11708` significa *"variação percentual mensal"*; `11710` é
*"acumulada no ano"*; `11711` é *"12 meses"*. Isso **não está no schema** — está
no `CASE` que o analista escreveu.

Nenhum embedding, reranker ou card melhor resolve isso. É o item
"**normalização de valores (linguagem → valores de domínio) via glossário**" que
está no backlog de ADR desde o começo, e agora tem evidência de que é bloqueante:
qualquer pergunta sobre PMC/PIM/PNAD é irrespondível sem esse mapa.

### 2.5 Desequilíbrio de corpus

Das 74 tabelas ingeridas, **50 são de conjuntura** e apenas 24 são FAR + FDS.
Perguntas operacionais (que é o que o usuário faz) competem contra um corpus
dominado por indicadores macro. Isso explica as perguntas 1 e 3 caírem em
`conjuntura`.

---

## 3. Causas-raiz, em ordem de impacto

| # | causa | evidência | onde se resolve |
|---|---|---|---|
| **C1** | **Sem glossário de negócio**: "gastos", "executado", "planejado", "enquadramento" não mapeiam para colunas | perguntas 1 e 3; títulos dos charts | camada de sinônimos (ver §5.1) |
| **C2** | **Sem semântica de valor**: códigos como `variavel_id` | SQL dos datasets | glossário de valores |
| **C3** | **Tabelas wide**: métricas como colunas (`perc_mcmv_4t25`, `cbic_lancamentos_regiao_norte`) | pergunta 4 | instrução de unpivot + marcação no card |
| **C4** | **Tempo derivado**: semestre/trimestre/ano a partir de data | pergunta 2 | regra no prompt + exemplos |
| **C5** | **Recusa fácil demais** e afirmação de fatos não verificáveis | perguntas 2 e 3 | prompt + métrica que penalize recusa indevida |
| **C6** | **Desequilíbrio de corpus** conjuntura vs operacional | perguntas 1 e 3 | quota por domínio no ranking |

Note que **nenhuma** dessas se resolve trocando modelo de embedding — que é onde
gastamos boa parte do esforço até aqui. As melhorias de ontem (card v2, grão,
BM25 pt) foram reais e continuam valendo, mas atacavam outra camada.

---

## 4. O plano

### Fase A — Extrair (somente leitura, ~1h)

1. Paginar os **171 charts** e os **99 datasets**, capturando: nome, `viz_type`,
   dataset de origem, e o `sql` dos datasets virtuais.
2. Paginar os **7 dashboards** para agrupar charts por tema.
   ⚠️ `list_dashboards` está com **erro 500 no servidor** hoje; se persistir,
   reconstruo o agrupamento pelos charts.
3. Conferir quais tabelas usadas em produção **não estão no escopo de ingestão**
   (`silver_ibge_pmc_construcao` e `silver_ibge_pim_pf_brasil` aparecem no SQL e
   precisam ser checadas contra as 74 ingeridas).
4. Salvar tudo em `eval/datasets/superset-snapshot-<data>.json` — matéria-prima
   versionada, sem interpretação.

### Fase B — Derivar casos candidatos (~2h)

Para cada chart/dataset virtual relevante, montar um caso candidato com:

- **pergunta em linguagem natural**, derivada do título do chart, escrita como um
  usuário perguntaria (não como o chart se chama);
- **`reference_sql`** = o SQL do dataset virtual, quando existir;
- **tabelas e colunas esperadas**, extraídas do `reference_sql` com o `sqlglot`
  que já temos — **derivação mecânica, não julgamento meu**;
- **slices**: domínio, wide/long, tempo derivado, precisa de glossário, etc.

Meta: **30–50 casos**, cobrindo FAR, FDS e conjuntura na proporção em que as
pessoas realmente perguntam — não na proporção do corpus.

### Fase C — Revisão humana (você e o time)

Entrego como **`mcid-business-v0.2.0-draft.json`**, arquivo separado, marcado
`draft`, sem tocar no v0.1.0. Vocês confirmam ou corrigem caso a caso.

Isto não é formalidade: o `reference_sql` vem de produção e é confiável, mas
**a pergunta que eu escrevo a partir do título do chart é interpretação minha**.
Se eu escrever a pergunta já enviesada para o SQL que existe, construo uma régua
que mede o sistema contra as minhas suposições. A revisão de vocês é o que
quebra esse ciclo.

### Fase D — Medir e bater o martelo

Com o gold revisado, rodar as duas avaliações e produzir o veredito:

- `eval.retrieval` — recuperação (já existe)
- `eval.answer` — resposta (construída ontem), **mais as métricas novas da §5**

---

## 5. O que precisa ser construído

### 5.1 Métricas novas (a lacuna que a §1 expôs)

| métrica | o que mede | por quê |
|---|---|---|
| **`undue_refusal_rate`** | recusou numa pergunta que o gold diz ser respondível | é **60% das falhas reais** e hoje é invisível |
| **`execution_accuracy`** | result-set do SQL gerado == result-set do `reference_sql` | o teste definitivo; agora é viável, temos o SQL de referência |
| **`shape_match`** | o resultado tem a forma pedida (linhas por região, série temporal) | pega o caso 4, que "funcionou" mas devolveu forma errada |
| **`unverifiable_claim_rate`** | afirmou fato sobre os dados que o schema não sustenta | pega o "não há dados antes de 2024" |

### 5.2 Glossário de negócio (ataca C1 e C2)

Duas tabelas, alimentadas do que o Superset já mostra:

- **termo → coluna**: "gastos"/"executado" → `valor_desembolsado`; "contratado" →
  `valor_contratado`; "UH"/"unidades" → `quantidade_uh`
- **código → significado**: `variavel_id=11708` → "variação percentual mensal"

Onde entra: no card (lado do documento, melhora recuperação) **e** na
serialização do subgrafo (lado do LLM, melhora geração). O grão de ontem provou
que os dois lugares têm efeitos diferentes e precisam ser medidos separado.

### 5.3 Marcação de tabela wide (ataca C3)

Detectar mecanicamente tabelas cujas colunas são valores (`*_norte`, `*_4t25`) e
marcar no card + instruir unpivot no prompt.

---

## 6. Cronograma e ordem

| ordem | item | esforço | depende de |
|---|---|---|---|
| 1 | Fase A — extração | ~1h | MCP (ok) |
| 2 | 5.1 — `undue_refusal_rate` e `shape_match` | ~1h | — |
| 3 | Fase B — casos candidatos | ~2h | A |
| 4 | **Fase C — revisão de vocês** | — | B |
| 5 | 5.2 — glossário | ~2h | A |
| 6 | Fase D — medir e decidir | ~1h | C |

Os itens 1, 2 e 3 eu toco sozinho. O **4 é bloqueante e é de vocês**.

---

## 7. Riscos

- **Viés de derivação**: perguntas escritas a partir do SQL que existe medem o
  sistema contra o que já sabemos responder. Mitigação: a revisão da Fase C, e
  incluir deliberadamente perguntas que os dashboards **não** respondem.
- **Cobertura enviesada**: os dashboards são majoritariamente de conjuntura;
  o uso real do chat parece ser mais operacional. Preciso de vocês para calibrar
  a proporção.
- **Superset é produção**: só leitura, sempre. Se alguma extração exigir algo
  além de `list_*`/`get_*`, eu paro e pergunto.
- **`list_dashboards` com erro 500**: já observado. Não bloqueia — dá para
  reconstruir por charts.
- **O gold de 8 casos continua valendo** como regressão: não substituo, adiciono.

---

## 8. RESULTADO — fases A e B executadas (2026-08-13)

Rascunho em `eval/datasets/retrieval/mcid-superset-v0.2.0-draft.json`:
**20 casos** (8 conjuntura / 8 FAR / 4 FDS = 60/40 operacional), em duas camadas:

- **Tier A (8)**: têm `reference_sql` de produção; tabelas e colunas extraídas
  mecanicamente com `sqlglot`. Habilitam execution accuracy.
- **Tier B (12)**: dataset físico, sem SQL; a referência é a **tabela que
  produção usa**. Testam recuperação e não-recusa.

### O veredito

| | respondeu | **acertou a tabela de produção** |
|---|---:|---:|
| **conjuntura** (8) | 8/8 | **8/8 — 100%** |
| **operacional FAR/FDS** (12) | 11/12 | **3/12 — 25%** |
| **total** | 19/20 (95%) | **11/20 — 55%** |

**O sistema é excelente em conjuntura e ruim em operacional.** Isso explica a
percepção: as perguntas que as pessoas fazem no chat são operacionais.

### Por que

Conjuntura tem vocabulário distintivo — CBIC, PNAD, ABECIP, SINAPI, BACEN — que
o BM25 casa direto. Já as tabelas operacionais são **irmãs quase idênticas**,
diferindo só no nível de agregação:

| pergunta | produção usa | o RAG escolheu |
|---|---|---|
| UH por UF | `mapa_nacional` | `dados_prioritarios_caixa` |
| valor total por estado | `panorama_estadual` | `mapa_nacional` |
| execução física por estado | `panorama_estadual` | `empreendimento` |
| valor do empreendimento | `ficha_empreendimento` | `empreendimento` |
| UHs contratadas FDS | `fds_ficha_empreendimento` | `fds_panorama_entidade` |
| valor contratado FDS | `fds_ficha_empreendimento` | `fds_empreendimento` |

Não são erros grosseiros: são **escolhas de nível de agregação errado entre
tabelas irmãs**. É o eixo de **grão** outra vez — não "linhas duplicadas", mas
"qual tabela está na granularidade que a pergunta pede".

⚠️ **Ressalva para a revisão**: em vários desses casos a tabela escolhida pelo
RAG poderia responder igualmente bem. "Valor total por estado" sai de
`panorama_estadual` (pré-agregado) ou de `mapa_nacional` — as duas são
defensáveis. **Só a revisão humana decide** se é erro ou alternativa válida. O
número de 25% é, portanto, um **piso pessimista**.

### O viés que eu previ aconteceu

`answered_rate` deu 95% aqui, contra 40% na sua amostra real. Motivo: eu escrevi
perguntas derivadas de dashboards que **existem**, logo respondíveis. Suas
perguntas eram abertas ("valores gastos com unidades habitacionais"). O gold
precisa incluir perguntas que o sistema **deve** recusar — e outras que ele
**não deveria** recusar mas recusa. Isso é trabalho da Fase C.

### Achados de escopo

1. **`__dados_brutos.*` responde as perguntas operacionais em produção.**
   "Valor Planejado / Suplementado / **Executado** FAR" saem de
   `mart_far_execucao_financeira`; "Unidades Habitacionais" e "% UH Entregues"
   saem de `bronze_snh_empreendimentos`. **Nada disso está no escopo.** A
   pergunta 1 da sua amostra ("valores gastos") foi recusada porque o dado
   **realmente não está lá** — a recusa estava certa.
2. **`empreendimento_rural.*` é um programa inteiro** (Rural), com dashboard
   próprio, fora do escopo.
3. **Nenhuma query de produção faz JOIN.** Todas single-table — as tabelas gold
   já são marts denormalizados. Investimos muito em `SEMANTIC_JOIN`,
   elegibilidade e `join_path_recall`, e o uso real é mono-tabela.

---

## 9. O que eu preciso de você

1. **Ok para seguir** com as fases A e B.
2. **Proporção alvo**: as 30–50 perguntas devem refletir os dashboards
   (conjuntura-pesado) ou o uso real do chat (mais operacional)? Minha
   recomendação: 60% operacional (FAR/FDS), 40% conjuntura — porque é onde as
   falhas estão.
3. **Quem revisa** a Fase C e em que prazo.
4. Confirmar se `silver_ibge_pmc_construcao` e `silver_ibge_pim_pf_brasil`
   **deveriam** estar no escopo de ingestão. Se sim, é lacuna de escopo, não de
   retrieval.

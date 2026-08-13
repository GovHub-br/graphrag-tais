# Plano de melhoria — o gap operacional

- **Data**: 2026-08-13
- **Base**: avaliação dos 20 casos derivados dos dashboards de produção
  ([plano anterior](./20260813-plano-eval-a-partir-do-superset.md), §8) mais dois
  diagnósticos novos feitos hoje.

---

## 1. Onde as falhas realmente estão

O resultado que orienta tudo: **conjuntura 8/8, operacional 3/12**. Fui atrás de
onde o operacional se perde e o resultado contraria a intuição — inclusive a
minha.

| etapa | casos | % das falhas |
|---|---:|---:|
| a tabela de produção **nem chegou ao contexto** | 3 | 33% |
| **chegou, e o LLM escolheu uma irmã** | **6** | **67%** |
| acertou | 3 | — |

> **Dois terços das falhas operacionais são de GERAÇÃO, não de recuperação.**

Isso reposiciona todo o esforço de ontem. Card v2, troca de modelo, BM25 em
português, reranker — tudo isso opera no eixo de recuperação, que responde por
**um terço** do problema. Os ganhos foram reais (MRR +34%, contexto −23%), mas
atacavam a fatia menor.

### Duas hipóteses que testei e derrubei

**"As descrições não discriminam."** Falso. Elas são excelentes e dizem
explicitamente o nível de agregação — até nomeiam o dashboard que alimentam:

```
panorama_estadual    "Visão Gold AGREGADA POR UF para alimentar o dashboard 'Panorama Estadual'"
mapa_nacional        "Visão Gold GEOGRÁFICA para mapas coropléticos... filtradas pela coluna 'nivel'"
ficha_empreendimento "Tabela Gold para alimentar a FICHA INDIVIDUAL do empreendimento"
empreendimento       "Visão unificada SILVER de empreendimentos"
```

**"A poda de colunas enviesa para a tabela-semente."** Só em parte. Existe
desequilíbrio (`empreendimento` com 25 colunas contra `panorama_estadual` com
12), mas em 2 dos 3 casos inspecionados as candidatas chegam **empatadas em 25
colunas** e o LLM erra assim mesmo.

### A conclusão

As tabelas chegam ao contexto em pé de igualdade, com descrições que as
distinguem, e **o prompt não dá critério nenhum de escolha**. A regra atual só
diz "use APENAS as tabelas fornecidas" — nada sobre *qual* usar quando várias
servem. O modelo escolhe praticamente por acaso.

---

## 2. Plano

### Onda A — Seleção de tabela na geração (ataca 6 de 9 falhas)

O maior retorno por esforço de todo o projeto neste momento, e é barato.

| # | item | como | aceite |
|---|---|---|---|
| **A1** | **Regra de escolha no `SYSTEM_PROMPT`** | Instruir: compare o **grão pedido pela pergunta** com o grão de cada tabela; prefira a tabela cuja descrição declara aquele propósito; se a pergunta é "por estado" e existe tabela agregada por UF, use-a em vez de agregar o detalhe | tabela certa ≥ 8/12 no operacional |
| **A2** | **Propósito em destaque na serialização** | Hoje a descrição vai como um parágrafo corrido no meio do bloco. Extrair uma linha `Uso: agregado por UF / ficha individual / série mensal` no topo de cada tabela | idem |
| **A3** | **Prior de autoridade do Superset** | Marcar no card e no contexto as tabelas que **dashboards de produção efetivamente usam**, e para qual tipo de pergunta. É sinal novo, que só temos agora | desempata irmãs sem depender do LLM |

Nota sobre A3: é a alavanca mais original. Produção já resolveu o problema de
"qual tabela responde o quê" — 171 charts são 171 respostas a essa pergunta. Usar
isso como prior é barato e não depende de modelo nenhum.

### Onda B — Recuperação dos 3 que não chegam

| # | item | aceite |
|---|---|---|
| **B1** | Investigar caso a caso os 3 (`far-situacao-obras`, `far-execucao-fisica-estado` e o terceiro). Verificar se é vocabulário, se a tabela é ilha no grafo, ou se o top-5 está saturado por irmãs | 12/12 no contexto |
| **B2** | Quota de diversidade por *nível de agregação*, não só por tabela — evitar que 3 das 5 sementes sejam a mesma família | contaminação mantida ≤ 8% |

### Onda C — Escopo e recusa útil

| # | item | observação |
|---|---|---|
| **C1** | **Decisão de produto de vocês**: ingerir `__dados_brutos.*` e `empreendimento_rural.*`? | "Valor Executado FAR" (= "gastos") só existe lá. Enquanto não entrar, a recusa está **certa** |
| **C2** | **Recusa informativa** | Trocar "Refine a pergunta" por *"não tenho dados de execução orçamentária (planejado/executado); tenho valor contratado e desembolsado"*. Exige saber o que **não** se tem — derivável do escopo |

### Onda D — Realinhar a avaliação

| # | item | por quê |
|---|---|---|
| **D1** | `expected_table_match` como métrica de primeira classe | É a métrica que revelou o problema e não está no eval; hoje calculei por fora |
| **D2** | Separar `retrieval_miss` de `generation_miss` | É a distinção que reorienta o trabalho; sem ela otimizamos o eixo errado |
| **D3** | Casos difíceis no gold (Fase C) | Perguntas que **devem** ser recusadas e outras que **não deveriam** ser |
| **D4** | Despriorizar métricas de JOIN | Nenhuma query de produção faz JOIN; `join_path_recall` mede algo que o uso real não exercita |

---

## 3. Ordem e esforço

| ordem | item | esforço | risco |
|---|---|---|---|
| 1 | **A1 + A2** (prompt e serialização) | ~1h | baixo, reversível |
| 2 | **D1 + D2** (métricas) | ~1h | nenhum |
| 3 | medir A1+A2 | ~20 min | — |
| 4 | **A3** (prior do Superset) | ~3h | médio — novo componente |
| 5 | **B1** (os 3 casos) | ~2h | baixo |
| 6 | **C2** (recusa informativa) | ~2h | baixo |
| 7 | **C1** (escopo) | decisão de vocês | alto impacto |

Começo por A1+A2+D1+D2 porque juntos custam ~2h, atacam 67% das falhas e são
medíveis na mesma tarde.

---

## 4. O que NÃO fazer

- **Trocar modelo de embedding de novo.** O eixo de recuperação responde por 1/3
  das falhas e já foi bastante otimizado.
- **Reranker.** Já medido e rejeitado (reach@5 −20%, p50 de 49,7s).
- **Investir em JOIN.** Produção não usa.
- **Mexer no card antes de A1/A2.** A informação já está lá; o problema é de
  consumo, não de conteúdo.

---

## 5. Expectativa honesta

A1+A2 são instrução de prompt. Podem render muito — é uma decisão que o modelo
hoje toma sem critério — ou pouco, se o modelo simplesmente não seguir a regra
com 4 a 8 tabelas parecidas no contexto. **É hipótese, e por isso vem com
medição na mesma leva.**

A3 é a que eu apostaria para valer, porque não depende do modelo obedecer: é
sinal duro, vindo de como a organização de fato responde essas perguntas.

E o teto continua sendo o gold: 20 casos, com as ressalvas de viés da §8 do plano
anterior. Vários "erros" podem ser alternativas legítimas — a revisão humana é
que decide, e sem ela os 25% são um piso pessimista, não um número final.

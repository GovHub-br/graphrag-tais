# Relatório de testes — duas personas (ADR-0014)

- **Data**: 2026-08-18
- **Escopo**: validação funcional + avaliação de qualidade dos dois modos
  (`Explorar` / Persona 1 e `Indicador` / Persona 2)
- **Ambiente**: stack local completo (Qdrant, Neo4j, MinIO, postgres-mcp,
  hayhooks, open-webui), VPN da lab conectada, catálogo MCid com **74 tabelas /
  1.289 pontos**
- **Configuração medida**: `EXPLORE_TOP_K=15`, `EXPANSION_MODE=evidence`,
  `MCP_DIRECT_FIRST=true`, `SCOPE_GAPS` **vazio**, LLM `gpt-4o`, embedding denso
  `gte-qwen2-1.5b`

---

## Sumário executivo

| | Resultado |
|---|---|
| **Rodada 1 — funcional** | **207 verificações, 0 falhas** (138 unitários + 25 gate offline P2 + 42 do motor de métricas + 22 smoke ao vivo) |
| **Rodada 2 — qualidade** | Contratos das duas personas **íntegros**; ranking da P1 **abaixo do piso**; 1 achado real na P2 |
| **Bloqueadores** | Nenhum |
| **Achados que exigem decisão** | 3 (ver §4) |

O sistema **funciona sem bugs conhecidos**. A qualidade tem um teto claro e
identificado: o **seed recall do retrieval (~33%)**, que é anterior a este
trabalho e limita as duas personas.

---

## 1. Rodada 1 — validação funcional

### 1.1 Testes automatizados (sem serviços)

| suíte | testes | resultado |
|---|--:|---|
| Unitários totais (`tests/`) | 138 | ✅ OK |
| ├─ motor de métricas da P1 (`test_explore_eval`) | 42 | ✅ |
| ├─ componentes da P1 (`test_explore_components`) | 23 | ✅ |
| ├─ parse do postgres-mcp (`test_mcp_executor_parse`) | 7 | ✅ |
| └─ suíte pré-existente (retrieval, answer, relatório, ingestão) | 66 | ✅ |
| Gate MDD offline da Persona 2 (`make -C eval quick`) | 25 + smoke + gate | ✅ OK |
| Contrato do gold + fixtures da P1 (`make explore-quick`) | — | ✅ OK |

### 1.2 Smoke ao vivo contra a API (`scripts/smoke_personas.py`)

**22/22 verificações OK.** Não mede qualidade — verifica que o sistema funciona
e que cada persona respeita o seu contrato:

| bloco | verificações |
|---|---|
| Modelos | ids `query`/`explore` no Hayhooks **e** nomes Indicador/Explorar no open-webui |
| Explorar — temática | responde; apresenta tabelas; **nenhuma fora do catálogo**; traz SQL de amostra; oferece o handoff |
| Explorar — catálogo | reconhece a pergunta de catálogo; panorama traz os 5 domínios do escopo |
| Explorar — render | tabela Markdown; **nenhuma célula truncada em reticências**; colunas consistentes; 5,2s |
| Indicador — completo | SQL + **gráfico embutido** + link do `.docx`; sem aviso de falha; 2,9s |
| Degradação | pergunta sem cobertura não inventa tabela e ainda responde algo útil |

---

## 2. Rodada 2 — qualidade: Persona 1 (`Explorar`)

18 casos, gold `mcid-explore-v0.1.0` (**`draft`**), erro 0%.
Latência **p50 3,9s · p95 5,9s** (era 7,8/10,7 antes do ajuste de prompt).

### 2.1 Ranking das tabelas apresentadas

| k | recall | **core recall** | precision | hit | MRR | nDCG | MAP |
|--:|--:|--:|--:|--:|--:|--:|--:|
| 5 | 0,541 | 0,731 | 0,225 | 0,833 | 0,696 | 0,617 | 0,492 |
| **10** | **0,669** | **0,796** | 0,175 | 0,833 | **0,696** | **0,658** | 0,528 |
| 15 | 0,719 | 0,796 | 0,168 | 0,833 | 0,696 | 0,672 | 0,543 |

> `core recall` = fração das tabelas **centrais** (relevância 3) apresentadas. É
> a que responde "a pessoa saiu sabendo da tabela principal?".
> `precision` e `listed_ratio` são o contrapeso obrigatório do recall — sem eles,
> listar o catálogo inteiro daria recall 1,0.

### 2.2 Contrato da persona

| métrica | valor | leitura |
|---|--:|---|
| **`catalog_fidelity`** | **1,000** | ✅ zero tabelas inventadas em 18 casos — o piso absoluto |
| `off_catalog_rate` | 0,000 | ✅ |
| `question_groundedness` | 1,000 | ✅ |
| `sample_sql_grounded` | 1,000 | ✅ |
| `answered_rate` | 1,000 | ✅ |
| `domain_recall` | 0,912 | ✅ aponta o domínio certo |
| `domain_precision` | 0,582 | ⚠️ aponta também domínios que não vêm ao caso |
| `gap_disclosure` | 0,944 | ⚠️ **enganoso** — ver abaixo |
| `listed_ratio` | 0,160 | ✅ não despeja catálogo |

⚠️ **`gap_disclosure` = 0,944 é inflado por construção.** 16 dos 18 casos não têm
lacuna esperada e pontuam 1,0 (neutro). Nos **2 casos reais o resultado é 1 de 2**:
declarou a lacuna do MCMV rural, não declarou a de execução orçamentária. Com
`SCOPE_GAPS` vazio, isso é só o que o LLM produziu por conta própria.

### 2.3 Por slice — onde a qualidade se concentra

| ✅ recall@10 = 1,000 | ⚠️ recall@10 ≤ 0,50 |
|---|---|
| `cadastro`, `catalogo_puro`, `geografia`, `lacuna`, `precos`, `social` | `temporal` (0,250), `credito` (0,333), **`mercado` (0,333, core 0,000)**, `tema_amplo` (0,369), `multi_dominio` (0,375), `emprego` (0,500) |

O padrão é nítido: **tema estreito e concreto vai bem; tema amplo e transversal
vai mal.** Os piores slices são os de conjuntura econômica.

### 2.4 Política de aceite

`catalog_fidelity`, `answered_rate`, `question_groundedness`, `sample_sql_grounded`,
`mrr@10`, `ndcg@10` e `domain_recall` **passam**. Reprovam:

- `core_recall_at_10` = 0,796 (piso 0,80) — margem de 0,004
- `table_recall_at_10` = 0,669 (piso 0,70)

O gate sai marcado **não conclusivo**: o gold está em `draft` e, por decisão do
ADR-0012, gabarito não revisado não aprova nem reprova.

---

## 3. Rodada 2 — qualidade: Persona 2 (`Indicador`)

### 3.1 Retrieval (8 casos, `mcid-business-v0.1.0`)

| métrica | valor |
|---|--:|
| `seed_recall@5` / `@10` | 0,216 / **0,329** |
| `seed_hit@5` | 1,000 |
| `mrr@10` / `ndcg@10` | 0,448 / 0,315 |
| `table_recall` | 1,000 |
| `structural_sufficiency` | 1,000 |
| `unauthorized_join_rate` (retrieval) | 0,000 |
| `overexpansion` | 14,94 |
| `disconnected_rate` | 0,429 |
| **prompt renderizado p50 / p95** | **11.456 / 18.676 chars** |
| latência do retrieval p50 / p95 | 412 / 1.305 ms |

Nada mudou aqui — e não deveria: `query_pipeline.py` é **byte a byte idêntico**
ao commit anterior. O **seed recall de 33%** segue sendo o gargalo número 1 do
projeto, e é ele que limita as duas personas.

### 3.2 Qualidade da resposta (8 casos, **duas rodadas independentes**)

| métrica | rodada 1 | rodada 2 |
|---|--:|--:|
| `schema_faithfulness` | 1,000 | 1,000 |
| `table_hallucination_rate` | 0,000 | 0,000 |
| `column_hallucination_rate` | 0,000 | 0,000 |
| `grain_compliance` | 1,000 | 1,000 |
| `sql_parse_ok` | 1,000 | 1,000 |
| `answered_rate` | 1,000 | 1,000 |
| **`unauthorized_join_rate`** | **0,125** | **0,125** |

---

## 4. Achados que exigem decisão

### 4.1 🔴 O gold da Persona 1 está errado — e é erro meu

Ele cobre **20 das 74 tabelas**. Foi escrito com o Docker desligado, a partir de
nomes colhidos nos datasets de retrieval existentes, assumindo que eram o
catálogo. Não eram. Ficaram de fora um **schema inteiro** (`conjuntura_bronze`,
11 tabelas) e tabelas obviamente pertinentes:

| pergunta | o sistema devolveu | meu gold exigia | veredito |
|---|---|---|---|
| "tem dado de emprego na construção civil?" | `gold_empregos_construcao` | `gold_ibge_pnad_construcao` | **o sistema acertou** |
| "quero entender o mercado imobiliário" | `gold_indices_mercado_imobiliario` | `gold_cbic_*_regiao` | **o sistema acertou** |
| "algo com série ao longo do tempo?" | `evolucao_financeira` + 3 irmãs | só as variantes `_chart` | **o sistema acertou** |

**Dos 5 casos reprovados, 4 são artefato do gabarito.** Isso explica boa parte
da distância entre `recall@10 = 0,669` e o piso — e significa que **os números
de ranking da §2.1 são um piso, não uma medida.** Reconstruir o gold contra as
74 tabelas reais (~2h) é o pré-requisito para qualquer otimização.

### 4.2 🟡 A única falha genuína da P1 é contaminação entre domínios

Em *"o que existe sobre MCMV FAR?"*, o sistema perde `ficha_empreendimento` e
`panorama_estadual` e traz 5 tabelas de `conjuntura_gold` (CBIC lançamentos e
vendas). Mesma causa dos slices ruins de conjuntura: **seed recall**. É o lever
de maior alavancagem e já está no backlog (prefixo de instrução no GTE, ADR-0009).

### 4.3 🟡 O JOIN não autorizado da P2 é lacuna de metadado, não erro do LLM

Caso `fds-evolucao-ritmo-001`, **reproduzível nas duas rodadas**:

```sql
FROM entidades_fds.fds_evolucao_financeira ef
JOIN entidades_fds.fds_ficha_empreendimento fe ON ef.apf = fe.apf
```

Não existe `SEMANTIC_JOIN` declarado entre essas duas tabelas no grafo. O JOIN é
semanticamente correto — `apf` é a chave natural do FDS —, mas **o catálogo não
o declara**, então a métrica o acusa, e com razão: um JOIN não declarado é um
produto cartesiano em potencial.

O conserto **não é no prompt**: é declarar a relação na Custom Property
"Relações semânticas MCID" do OpenMetadata. Enquanto não for declarada, o
sistema está certo em ser acusado — e a métrica está certa em acusar.

> Contexto: o diagnóstico de 13/08 registrava `0,0` nesta métrica. Aquele número
> vem de outra composição de casos; não trate isto como regressão sem
> reconciliar os dois conjuntos.

---

## 5. Correções entregues nesta rodada

| # | correção | evidência |
|---|---|---|
| 1 | **Erro de banco silencioso na P2**: o postgres-mcp devolve erro como texto com `isError: False`; isso passava por sucesso, a autocorreção do ADR-0013 nunca era acionada e a resposta saía sem gráfico **e sem aviso** | 7 testes novos + prova ao vivo: SQL quebrado → autocorreção → 5 linhas |
| 2 | **"Quais bases vocês têm?" respondia com uma amostra** apresentada como se fosse o catálogo (o caminho "sem sementes" nunca dispara, porque o retriever devolve top-k para qualquer texto) | 2 testes + resposta ao vivo com os 5 domínios |
| 3 | **Resposta da P1 muito textual** → resumo direto, tabela Markdown por domínio, detalhe só das destacadas | 6 testes de render |
| 4 | **Células cortadas em `…`** → corte em fim de frase/oração | 0 células truncadas na resposta atual |
| 5 | `rg` ausente quebrava `make eval-live` | fallback para `find` |
| 6 | **Nomes no dropdown** (`Indicador` / `Explorar`) aplicados na camada do open-webui, **sem tocar em ids nem rotas** — o histórico de conversas e `/query/run` seguem válidos | `scripts/setup_webui_models.sh`, idempotente |

---

## 6. Recomendação

**Não otimizar retrieval agora.** Ajustar contra um gabarito que eu sei estar
errado produz número que sobe sem significar nada — exatamente o risco
metodológico que o plano de 13/08 já havia levantado.

Ordem sugerida:

1. **Reconstruir o gold da P1** contra as 74 tabelas reais (~2h, eu) e **revisão
   de domínio** dos 18 casos (vocês) — sem isso, nenhum número de ranking vale.
2. **Preencher `SCOPE_GAPS`** no `.env` (~15 min) — ataca direto o caso de
   execução orçamentária que falhou.
3. **Declarar o `SEMANTIC_JOIN` de `apf`** no OpenMetadata para o FDS (vocês) —
   zera o `unauthorized_join_rate`.
4. Só então: **seed recall** (prefixo de instrução no GTE), que é o teto real
   das duas personas.

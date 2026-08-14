# Plano de melhoria do retrieval e da resposta — graphrag-tais

- **Data**: 2026-08-11 (rev. 2 — após os experimentos de modelo e a auditoria da KB)
- **Base**: traces reais de `eval/reports/`, código em `src/rag/`, ADRs 0006–0013,
  e três experimentos executados ao vivo hoje.
- **Snapshot**: 1.289 cards · 74 tabelas · 1.215 colunas · `HAS_COLUMN` 1215 ·
  `SEMANTIC_JOIN` 24 · `FOREIGN_KEY` 0.
- **Gold**: `mcid-business-retrieval 0.1.0` (**draft**, 8 casos).

> **Status**: diagnóstico e plano. O dataset segue `draft` e não há baseline
> aceita. Nada aqui foi promovido a gate verde.

---

## 0. O que já foi feito e medido (2026-08-11)

| Item | Estado |
|---|---|
| Stack subido e verificado ao vivo (`make up`) | ✅ |
| Baseline coletado — `eval/reports/20260811-live-run.json` | ✅ |
| **Onda 0.1 — correção da régua** | ✅ **implementado** |
| A/B do prefixo de instrução (gte-qwen2) | ✅ medido |
| Comparação de 7 configurações de modelo denso | ✅ medido |
| Validação híbrida do qwen3-4b-fp16 | ✅ medido — **reprovou o gate** |
| Auditoria da composição da KB | ✅ medido |
| Todos os 8 arms validados no híbrido | ✅ medido |
| `InstructedTextEmbedder` (item 1.3) | ✅ implementado, **desligado** |
| **Modelo denso fixado em `qwen3-embedding:4b`** | ✅ **em produção** |
| **Onda 0.5 — destravar o `eval-quick`** | ✅ **feito** — gate verde, 25 testes |
| **Onda 1.1 — card v2** | ✅ **em produção** |
| Reingestão real do OpenMetadata (token renovado) | ✅ |
| **Onda 4.1 — grão** | ✅ **medido e em produção** — ver [método](./20260812-metodo-profiling-grao.md) |
| **Onda 1.2 — BM25 em português** | ✅ **em produção** (nDCG do canal esparso +23%) |
| **Onda 3.1 — reranker** | ❌ **medido e REJEITADO** — reach@5 −20% e p50 de 49,7s |
| **Onda 0.3 — eval de resposta** | ✅ **implementado** (`make eval-answer`) |
| **Onda 4.2 — `sqlglot`** | ✅ base implementada (compartilhada com a 0.3) |

### Onda 2.1 (roteador de domínio) — provavelmente DESNECESSÁRIA

Re-medida a contaminação depois do card v2 + BM25 pt, antes de construir o
componente:

| | slots fora do domínio (top-5) |
|---|---:|
| antes (gte + card v1) | 10/40 — **25%** |
| agora | 3/40 — **8%** |

O critério de aceite do item 2.1 era **< 10%**, e já estamos abaixo **sem o
roteador**. Mais: dos 3 slots restantes, **2 são de camada, não de domínio** —
`conjuntura-pnad` traz `conjuntura_bronze` quando o gold é `conjuntura_silver`,
mesmo domínio de negócio. Um roteador FAR/FDS/conjuntura trataria os dois como
iguais e não resolveria. Sobra **1 slot** de contaminação genuína entre domínios.

**Recomendação: não construir o roteador.** Se o problema de camada
(bronze/silver/gold) incomodar, o remédio é outro — preferência por camada no
ranking ou filtro por `meta.schema`, muito mais barato que um classificador.

### Validação E2E do chat (2026-08-12)

O caminho completo foi exercitado pelo endpoint OpenAI-compat que o webui
consome:

- execução via postgres-mcp: **ok**, sem erro;
- **gráfico com os valores corretos** — BA ≈ 16.200, o valor deduplicado
  (verificado abrindo o PNG e conferindo contra o banco);
- `.docx` no MinIO com URL assinada: baixa (131 KB), zip íntegro, **3 páginas**
  renderizadas no LibreOffice (capa full-bleed + conteúdo + gráfico);
- a explicação em linguagem natural **menciona a deduplicação**.

Latência total do chat: ~58s (motor + agente MCP + gráfico + docx).

Ponto estético a melhorar: a página 2 termina com 1/3 vazio e o gráfico fica
sozinho na página 3 com 2/3 em branco.

### Primeira medição de qualidade da resposta (2026-08-12)

Nunca houve uma. `eval/reports/20260812-answer-baseline.json`:

| métrica | valor |
|---|---:|
| `answered_rate` | 1,0000 |
| `schema_faithfulness` | 1,0000 (zero alucinação de tabela/coluna) |
| `grain_compliance` | 1,0000 (a dedup da 4.1 segura nos 8 casos) |
| `sql_parse_ok` | 1,0000 |
| **`unauthorized_join_rate`** | **0,1250** ⚠️ |

O caso `fds-evolucao-ritmo-001` une `fds_evolucao_financeira` com
`fds_ficha_empreendimento` — par **não declarado** no catálogo. O LLM confundiu
com a variante `_chart`, que é a declarada. Era invisível: a métrica homônima do
eval de retrieval mede as relações do **subgrafo**, não os JOINs do **SQL**.

**Configuração vigente** (`.env`, valores antigos comentados como `# ANTES:`):

```
OLLAMA_EMBED_MODEL=qwen3-embedding:4b          # antes: aroxima/gte-qwen2-1.5b-instruct:f16
QDRANT_COLLECTION=documents_cardv2             # antes: documents
QDRANT_EMBEDDING_DIM=2560                      # antes: 1536
OM_CARD_SCHEMA_VERSION=v2                      # antes: v1
OM_DOMAIN_LABELS={...}                         # 5 schemas do MCid
OM_GRAIN_KEYS=                                 # VAZIO — pendente do time de domínio
OM_EMBEDDING_PROFILE=dev-qwen3-embedding-4b__cardv2__qdrant-bm25-v1
QUERY_INSTRUCTION_ENABLED=false                # prefixo não se sustentou no híbrido
```

### Efeito acumulado das duas mudanças

De `gte-qwen2 + card v1` para `qwen3-embedding:4b + card v2`, tudo medido pelo
`eval.retrieval.live` completo:

| métrica | antes | agora | |
|---|---:|---:|---|
| MRR@10 | 0,6042 | **0,8750** | +45% |
| nDCG@10 | 0,4223 | **0,5551** | +31% |
| over-expansion | 16,60× | **12,01×** | −28% |
| prompt p95 | 16.826 | **13.073** | −22% |
| `seed_table_reachable@5` | 0,9375 | 0,9375 | preservado |
| suficiência estrutural | 1,000 | 1,000 | preservado |
| joins elegíveis / não autorizados | 1,0 / 0,0 | 1,0 / 0,0 | preservado |

Zero regressão de guardrail. O alvo do audit de julho — prompt p95 abaixo de
12.000 — está a 8% de distância (13.073), faltando só a Onda 3.

⚠️ A collection `documents_v2_q4` foi construída **relendo o `content` dos cards
existentes e reembutindo**, não pela pipeline de ingestão a partir do
OpenMetadata. Os payloads são cópia literal e o esparso é bit a bit idêntico, e
ela passou pelo `eval.retrieval.live` completo — mas a próxima reingestão real
(que virá com o card v2) deve gerar a collection definitiva com nome próprio.

O collector reproduziu exatamente os números documentados da Fase 2
(`seed_recall@5` 0,30516, `overexpansion` 16,60, `node_precision` 0,1377,
prompt médio 10.518), confirmando que o código em disco é o da Fase 2.

⚠️ **O `make eval-quick` está vermelho hoje**, com 5 falhas em
`tests/test_live_retrieval.py` (`failed_stage` esperado `ranking_consistency`,
recebido `graph_expansion`, e correlatos). Verifiquei com `git stash` que são
**pré-existentes** e não vieram da 0.1. Precisam ser consertadas ou o gate do
`AGENTS.md` não é executável.

---

## 1. Tese: a métrica perseguida tinha teto de 74,5%

`seed_recall@5 = 0,3052` foi tratado como "a causa-raiz, parada". Ele está
**estruturalmente capado** e **mede a coisa errada**.

**Teto.** A métrica divide por *todas* as sementes-gold, e os casos declaram de
5 a 14 sementes para 5 posições. Em `far-ranking-valor-001` (14 sementes-gold) o
máximo é 35,7%. Média dos 8 casos: **74,5%**.

**Alvo.** O `Neo4jSubgraphRetriever` sobe de coluna para tabela-pai em
`_SEED_TABLES_CYPHER` — uma coluna no top-5 já resolve a tabela. Medindo assim,
**a tabela-gold chega em 93,75% dos casos**. A métrica oficial reportava 62,5%.

Em 8 casos, **uma única** tabela-gold é realmente perdida
(`fds_ficha_empreendimento` em `fds-evolucao-ritmo-001`).

**Conclusão que inverte o diagnóstico anterior: o gargalo não é recall — é
precisão, roteamento e grão.**

### 1.1 Contaminação entre domínios

25% dos slots do top-5 (10 de 40) caem em schemas sem relação com a pergunta.
Os dois casos limpos são os de vocabulário distintivo (SINAPI, INCC, CBIC). Os
piores são FAR e FDS, **schemas espelhados**: `empreendimento_far.evolucao_financeira`
e `entidades_fds.fds_evolucao_financeira` são vizinhos imediatos no espaço denso.

Não há controle de diversidade: o top-5 tanto colapsa em 1 tabela quanto se
espalha em 5, e 1.215 cards de coluna disputam 5 posições com 74 de tabela (16:1).

---

## 2. A KB está degradada — medido

Auditoria dos 1.289 cards embutidos (423.445 caracteres):

| bucket | chars | % |
|---|---:|---:|
| **estrutural** (`FQN:`, `Tabela:`, `Tipo:`, `Chave primária:`, `Tipo de dado:`, `Schema:`) | 249.026 | **58,8%** |
| semântico (nome, descrição) | 127.542 | 30,1% |
| lista de colunas (cards de tabela) | 28.508 | 6,7% |
| relacionamentos | 18.283 | 4,3% |

Por tipo de card:

| tipo | cards | estrutural | semântico |
|---|---:|---:|---:|
| tabela | 74 | 13,3% | 29,5% |
| **coluna** | **1.215** | **67,2%** | **30,2%** |

**20,3% do corpus inteiro é a segunda cópia do caminho** — a linha `Tabela:`
repetindo o que já está em `FQN:`, que já está em `meta.node_id`, `meta.fqn`,
`meta.table_node_id` e no `node_id` do Neo4j.

**Boa notícia: 0% de descrições vazias**, mediana de 215 chars em tabelas e 54
em colunas. O sinal existe e é bom — está afogado, não ausente.

### Exemplo real

O card gold de `fds_ficha_empreendimento.pct_execucao_ts` tem 274 caracteres:

- **137 (50%)** são `FQN:` + `Tabela:` — o mesmo caminho, duas vezes
- **57 (21%)** são rótulos fixos (`Tipo: coluna`, `Tipo de dado:`, `Chave primária: não`)
- **42 (15%)** são a descrição, a única frase que diz o que a coluna significa

O intruso que venceu no denso, `empreendimento.percentual_execucao_fisica`, tem
**97 caracteres de descrição** — quase 3× mais prosa. Entre registros quase
idênticos, o que se parece mais com uma pergunta é o que tem mais frase.

---

## 3. Experimentos de modelo — o que ficou provado

### 3.1 Prefixo de instrução (canal denso isolado)

| variante | gold@5 | gold@10 | fora/40 | tabela@5 |
|---|---:|---:|---:|---:|
| cru | 12 | 17 | 6 | 0,875 |
| instruct **pt** | 15 | 21 | 8 ⚠️ | 0,750 ⚠️ |
| instruct **en** | **15** | **21** | 6 | 0,875 |

O prefixo vale **+25% de sementes-gold**, e a descrição da tarefa em **inglês** é
estritamente melhor que em português. Mas **não conserta** `fds-entrega-ts-001`.

### 3.2 Comparação de modelos (canal denso isolado)

| arm | gold@5 | gold@10 | fora/40 | tab@5 | MRR@10 | nDCG@10 |
|---|---:|---:|---:|---:|---:|---:|
| gte-qwen2 f16 cru | 12 | 17 | 6 | 0,875 | 0,625 | 0,368 |
| gte-qwen2 f16 instr | 15 | 21 | 6 | 0,875 | 0,583 | 0,400 |
| qwen3-4b Q4 cru | 15 | 23 | 5 | 0,875 | 0,854 | 0,570 |
| **qwen3-4b fp16 cru** | **17** | 23 | **4** | 0,875 | 0,688 | 0,529 |
| qwen3-4b fp16 instr | 14 | 20 | 7 | 0,875 | 0,771 | 0,479 |
| qwen3-8b q8 cru | 15 | **25** | 5 | 0,875 | 0,713 | 0,549 |
| qwen3-8b q8 instr | 17 | 22 | 6 | **0,938** | 0,754 | 0,523 |

**Todo** arm qwen3 supera **todo** arm gte em nDCG (pior qwen3 0,479 > melhor gte
0,400). A quantização importava: fp16 melhorou gold@5 de 15→17 e contaminação de
5→4 sobre o Q4. O 8B não justifica os 4096 dims.

⚠️ Os GGUF `dengcao/Qwen3-Embedding-*:f16` **não servem** como embedder: foram
convertidos sem `pooling_type`, o Ollama os registra como `capabilities:
["completion"]` e recusa `/api/embeddings`. Usar as tags oficiais
(`qwen3-embedding:4b-fp16`, `8b-q8_0`, …), que trazem `pooling_type=3`.

### 3.3 Validação híbrida — o teste decisivo

Collection `documents_v2` com denso do qwen3-4b-fp16 e **esparso copiado bit a
bit** (BM25 não depende do modelo denso), rodando `eval.retrieval.live` completo:

| | gte (baseline) | qwen3-4b-fp16 | |
|---|---:|---:|---|
| seed_recall@5 | 0,3052 | 0,3552 | +16% |
| MRR@10 | 0,6042 | 0,6991 | +16% |
| nDCG@10 | 0,4223 | 0,4880 | +16% |
| prompt p95 | 16.918 | 14.763 | −13% |
| **`seed_table_reachable_at_5`** | **0,9375** | 0,8125 | 🔴 |
| **`seed_table_reachable_at_10`** | **1,0000** | 0,8750 | 🔴 |
| **structural_sufficiency** | **1,000** | 0,875 | 🔴 **gate** |
| **table_recall** | **1,000** | 0,875 | 🔴 |
| eligible_join_recall / join_path_recall / unauthorized_join | 1,0 / 1,0 / 0,0 | 1,0 / 1,0 / 0,0 | = |

Esse arm **reprova o gate**, e o caso que colapsou — `far-ranking-valor-001` — é
`critical: true`, indo a **zero** em suficiência, table_recall e node_recall.

Note que a métrica antiga `seed_table_recall_at_5` dizia que o qwen3 era **melhor**
(0,6875 vs 0,6250). Só a métrica nova da Onda 0.1 revela a regressão e explica a
queda de suficiência. É a justificativa prática da 0.1.

> ⚠️ **Esta seção sozinha induz a erro.** Validar só o fp16 e generalizar dele
> "o qwen3 reprova" foi falha de método — cada variante se comporta de forma
> diferente. Ver §3.5.

### 3.5 Todos os arms no híbrido — a correção

Os 8 arms passaram pelo `eval.retrieval.live` completo. Testar os `instr`
exigiu implementar o `InstructedTextEmbedder` (item 1.3), atrás de
`QUERY_INSTRUCTION_ENABLED` (default desligado).

| arm | suf | reach@5 | reach@10 | seed@5 | MRR | nDCG | gate |
|---|---:|---:|---:|---:|---:|---:|---|
| gte f16 cru *(produção)* | 1,000 | 0,938 | 1,000 | 0,305 | 0,604 | 0,422 | ✅ |
| gte f16 instr | 1,000 | 0,938 | 1,000 | 0,268 | 0,625 | 0,457 | ✅ |
| **qwen3-4b Q4 cru** | **1,000** | 0,875 | 0,875 | **0,364** | **0,760** | **0,506** | ✅ |
| qwen3-4b Q4 instr | 0,875 | 0,750 | 0,750 | 0,259 | 0,698 | 0,436 | ❌ |
| qwen3-4b fp16 cru | 0,875 | 0,812 | 0,875 | 0,355 | 0,699 | 0,488 | ❌ |
| qwen3-4b fp16 instr | 1,000 | 0,875 | 0,875 | 0,309 | 0,723 | 0,467 | ✅ |
| qwen3-8b q8 cru | 0,875 | 0,812 | 0,875 | 0,287 | 0,745 | 0,478 | ❌ |
| qwen3-8b q8 instr | 0,750 | 0,688 | 0,938 | 0,250 | 0,721 | 0,473 | ❌ |

**`qwen3-embedding:4b` (Q4_K_M) sem prefixo passa no gate e supera a produção em
tudo**: seed@5 +19%, MRR +26%, nDCG +20%.

**Todas as falhas, de todos os arms, estão no slice FAR:**

| caso | arms que falham |
|---|---|
| `far-ranking-valor-001` | fp16 cru, 8b cru, 8b instr |
| `far-ufs-uh-001` | Q4 instr, 8b instr |
| conjuntura e FDS | **nenhuma falha em nenhum arm** |

As duas perguntas FAR que quebram são de **agregação** ("10 empreendimentos com
maior valor contratado", "10 UFs com mais UH"), e o schema FAR tem tabelas de
detalhe (`empreendimento`, `ficha_empreendimento`) competindo com agregadas
(`mapa_nacional`, `consolidado`, `dados_prioritarios_caixa`). **O eixo é grão, e
nenhuma variante de embedding é robusta ali.**

**Correção sobre o prefixo de instrução:** no canal denso isolado ele rendia +25%
de sementes-gold no gte-qwen2. **No híbrido não se confirma** — seed@5 cai de
0,305 para 0,268 (MRR e nDCG sobem). O RRF já recuperava o que o prefixo
adicionava. Lição de método: ganho medido em canal isolado não se transfere
automaticamente para o híbrido.

**Fragilidade:** `structural_sufficiency` de 0,875 vs 1,000 é **um caso em oito**.
O veredito passa/reprova depende de um único caso, então "Q4 cru passa" não é
evidência forte. O sinal robusto é o padrão — todas as falhas em FAR, todas em
agregação.

### 3.4 A causa raiz — e o achado central

*"Quais são os 10 empreendimentos FAR com maior valor contratado?"*

```
gte    denso top-2:  empreendimento.valor_contratado
                     ficha_empreendimento.valor_contratado     ← as duas gold
qwen3  denso top-2:  mapa_nacional.total_valor_contratado
                     dados_prioritarios_caixa.valor_contratado ← nenhuma
```

O qwen3 preferiu tabelas **agregadas** (`total_valor_contratado` casa muito bem
com "maior valor contratado"), mas `mapa_nacional` é agregado por UF e a pergunta
pede ranking **por empreendimento**. É erro de **grão** manifestado no retrieval.

> **Os dois modelos falham em eixos complementares.** O gte-qwen2 erra
> **domínio** (FAR vs FDS). O qwen3 erra **grão** (agregado vs detalhe).
> Trocar de modelo troca de defeito.

Nem domínio nem grão estão escritos nos cards. São exatamente os sinais que a
Onda 1.3 acrescenta. **Por isso a refatoração da KB passa à frente da troca de
modelo** — sem ela, a escolha do modelo é uma loteria entre dois defeitos.

---

## 4. Defeitos no código

| # | Defeito | Onde |
|---|---|---|
| **D1** | BM25 em **inglês** sobre corpus e perguntas PT — `FastembedSparse*Embedder` sem `language`, default `english`. "habitacionais" não casa "habitacional"; "quais/são/com/maior" viram termos de IDF alto | `fastembed_sparse_text_embedder.py:18`, `..._document_embedder.py:21` |
| **D2** | GTE-Qwen2-**instruct** recebendo pergunta crua (medido: +25% com prefixo EN) | `ollama_text_embedder.py:31-35` |
| **D3** | **58,8% do corpus é estrutural**; 67,2% nos cards de coluna; 20,3% é a 2ª cópia do caminho | `graph_card_builder.py:160-187, 223-242` |
| **D4** | Poda de colunas completa por `ordinal_position` — o que sobra no contexto é o que vem primeiro no DDL | `neo4j_subgraph_retriever.py:254-258` |
| **D5** | Subgrafo inteiro vai ao LLM **duas vezes** (prompt + `schema_context` do executor); 3 chamadas por pergunta | `pipeline_wrapper.py:100-106`, `mcp_sql_executor.py:99-103` |
| **D6** | IR gerada na **mesma chamada** do SQL, depois da recuperação — não pode rotear | `query_pipeline.py:51-62` |
| **D7** | Eval mede o grafo, não a serialização; e **não mede a resposta** (sem execution accuracy nem faithfulness) | `eval/README.md:7-17` |
| **D8** | `make eval-quick` vermelho: 5 falhas pré-existentes | `tests/test_live_retrieval.py` |

---

## 5. Plano por ondas (revisado)

### Onda 0 — Corrigir a régua

| # | Item | Estado |
|---|---|---|
| **0.1** | `seed_table_reachable_at_{5,10}` (resolve coluna→pai como o runtime), `seed_recall_ceiling_at_*`, `seed_recall_headroom_at_*`. Aditivas; nenhuma métrica existente mudou | ✅ **feito** |
| 0.2 | Medir a **serialização** (`serialized_context_node_ids`), não só o grafo | pendente |
| 0.3 | **Eval de resposta**: execution accuracy, faithfulness (determinística, por interseção com o trace), answer relevancy por juiz | pendente |
| 0.4 | Ampliar o gold de 8 → 30–50 casos e sair de `draft` | pendente |
| **0.5** | **Consertar as 5 falhas de `test_live_retrieval.py`** — sem isso o gate não roda | **novo, bloqueante** |

### Onda 1 — KB e recuperação (reordenada)

A 1.3 subiu para **primeiro item**: é a causa-raiz por trás das falhas dos dois
modelos e o pré-requisito do roteador.

| # | Item | Reindex | Aceite |
|---|---|---|---|
| **1.1** | **Card v2** — ver §6 | **total** | estrutural < 25% do corpus; contaminação < 15%; zero tabela-gold perdida |
| 1.2 | `language="portuguese"` no BM25, **dos dois lados** | esparso | canal sparse isolado > 21,5%, sem regressão do híbrido |
| 1.3 | Prefixo de instrução **EN** na query — **só se o modelo final for o gte-qwen2** | não | +25% gold@5 já medido no denso; validar no híbrido |
| 1.4 | Quota de diversidade por tabela-pai no corte do top-k | não | `seed_table_reachable@5` ≥ 0,9375 mantido |

### Onda 2 — Roteamento

| # | Item | Aceite |
|---|---|---|
| 2.1 | `DomainRouter` — léxico determinístico (FAR, FDS, "Entidade Organizadora", SINAPI, CBIC, PNAD) → filtro `meta.schema`. **Filtro suave antes de duro**, fallback para nenhum filtro sem evidência | contaminação < 10%; **zero** tabela-gold perdida; caso adversarial FAR×FDS no gold |
| 2.2 | `QueryInterpreter` — IR **antes** da recuperação, incluindo mono/multi-tabela; cache no Redis | p95 ≤ 2× o atual; expansão em mono-tabela para de trazer vizinhos |

### Onda 3 — Densidade do contexto

| # | Item | Aceite |
|---|---|---|
| 3.1 | Reranker cross-encoder — **`qllama/bge-reranker-v2-m3` já está baixado no Ollama** | nDCG@10 > 0,4880; p95 < 800 ms |
| 3.2 | Podar colunas por relevância, não por ordinal | `PRUNE_SEED_TABLE_MAX_COLS` 25→15 com colunas-gold em 50/50 |
| 3.3 | Orçamento de contexto global por prioridade | prompt p95 < 12.000 chars |
| 3.4 | Parar de reenviar o schema ao `McpSqlExecutor` — mandar só `tables_used` | tokens ↓, autocorreção estável |

### Onda 4 — Correção da resposta

| # | Item | Aceite |
|---|---|---|
| 4.1 | **Guardrail de grão** — ver §6.3 | "Top 10 empreendimentos FAR" devolve 10 APFs distintos com valores conferidos |
| 4.2 | Validação com `sqlglot` antes de executar | zero JOIN não autorizado |
| 4.3 | Explicação sobre o resultado executado, com ressalva de grão | revisão humana |

### Onda 5 — Segunda rodada de modelos, sobre a KB nova

**Obrigatória, não opcional.** A escolha atual (`qwen3-embedding:4b` Q4) foi
feita sobre a KB degradada — 58,8% estrutural, sem domínio, sem grão. Nada
garante que o mesmo modelo vença quando o card mudar; o ranking entre modelos
pode inclusive se inverter, já que cada um falha exatamente onde falta o sinal
que o card v2 acrescenta.

Repetir §3.2 (denso isolado) e §3.5 (híbrido completo) sobre o **card v2**, com
os mesmos 8 arms e o mesmo protocolo:

| arm | papel na rodada 2 |
|---|---|
| `qwen3-embedding:4b` Q4 cru | **novo baseline** — é a produção a partir de agora |
| `qwen3-embedding:4b-fp16` cru/instr | reprovou por grão; o card v2 deve consertar |
| `qwen3-embedding:8b-q8_0` cru/instr | reprovou; tinha o melhor gold@10 no denso |
| `gte-qwen2` cru/instr | controle — mede quanto do ganho veio do card e quanto do modelo |
| `text-embedding-3-large` | só com sign-off de LGPD (ADR-0009) |

**Critério de decisão:** manter o modelo atual salvo se outro arm passar no gate
*e* superar o baseline em seed@5, MRR e nDCG simultaneamente.

**Pré-requisitos:** Onda 1.1 (card v2) e, idealmente, Onda 0.4 (gold ampliado) —
sem ela o veredito continua pendurado num único caso.

Ferramental pronto e reutilizável: `build_hybrid_v2.py` monta uma collection
híbrida em ~2,5 min copiando o esparso, e `run_arms.sh` roda os 8 arms de ponta a
ponta. A rodada 2 custa cerca de meia hora de máquina.

---

## 6. A refatoração da KB (detalhamento da 1.1)

### 6.1 Princípio

**O texto embutido carrega significado; o resto vai para o payload e para o
grafo.** Tudo que é caminho, rótulo ou tipo já existe em `meta` (filtrável pelo
Qdrant) e em Neo4j (consultável por Cypher). Ocupar 58,8% do vetor com isso é
pagar duas vezes e degradar a busca.

### 6.2 O que sai, o que fica, o que entra

**Sai do vetor** (permanece no payload/grafo, sem perda de informação):

- `FQN:` → já em `meta.node_id` / `meta.fqn`
- `Tabela:` → já em `meta.table_node_id` (é a 2ª cópia, 20,3% do corpus)
- `Tipo: coluna|tabela` → já em `meta.card_type`
- `Chave primária: não` → vira payload booleano; no texto, só aparece quando é PK
- `Tipo de dado:` → payload; continua indo ao LLM na serialização do subgrafo,
  que é onde ele importa, mas não polui o vetor

**Entra** (os dois sinais que faltavam, conforme §3.4):

- **Domínio de negócio**, derivado do schema: `FAR — Fundo de Arrendamento
  Residencial (Novo MCMV)`, `FDS — MCMV Entidades`, `Conjuntura — indicadores
  macro da construção`. Resolve o eixo em que o **gte** falha e dá ao
  `DomainRouter` (2.1) em que filtrar.
- **Grão da tabela**: `um registro por APF (empreendimento)` vs `um registro por
  UF`. Resolve o eixo em que o **qwen3** falha.

**Fica**: nome, nome de exibição, descrição — os 30,1% que já são bons.

### 6.3 Grão: resolver no grafo

O grão não é enfeite de texto; é propriedade da tabela e pertence ao **Neo4j**:

- `Table.grain_key` (ex.: `apf`) e `Table.grain_description`
- Serializado no contexto do LLM, para ele agregar/deduplicar corretamente
- Verificável pelo `SqlValidator` (4.2): um `SUM` sobre tabela cujo grão não é o
  da pergunta vira aviso
- Checável no eval como novo eixo
- E é o que conserta o gráfico dobrado de `empreendimento`/`ficha_empreendimento`
  (1.646 linhas para 823 APFs)

Fonte: pode vir de Custom Property do OpenMetadata, como já é feito com as
relações semânticas via `mcid_markdown_v1` — mesmo mecanismo, adapter novo.

### 6.4 Antes e depois (card de coluna)

```
ANTES — 274 chars, 15% de descrição
  Tipo: coluna
  FQN: Cidades.cidades.entidades_fds.fds_ficha_empreendimento.pct_execucao_ts
  Tabela: Cidades.cidades.entidades_fds.fds_ficha_empreendimento
  Nome: pct_execucao_ts
  Tipo de dado: numeric(6,2)
  Descrição: Percentual de execução do trabalho social.
  Chave primária: não

DEPOIS — proposta
  pct_execucao_ts — Percentual de execução do trabalho social.
  Tabela: ficha de empreendimentos (um registro por APF)
  Domínio: FDS — MCMV Entidades
```

Meta: **estrutural abaixo de 25%** do corpus (hoje 58,8%), com domínio e grão
presentes em 100% dos cards.

### 6.5 Riscos

- Exige bump de `OM_CARD_SCHEMA_VERSION` para v2 e novo `content_hash`
  (o ADR-0011 já prevê versionamento de card).
- Tirar o FQN do texto **reduz o casamento lexical** de quem pergunta usando o
  nome físico. Mitigação: manter o nome curto da tabela e da coluna no texto — é
  o que casa —, e não o caminho completo do catálogo. Medir com o canal esparso
  isolado, que é onde o efeito apareceria.
- A quota de diversidade (1.4) e o card v2 mexem no mesmo lugar: medir separado.

---

## 7. Qual modelo se saiu melhor

**Vencedor medido: `qwen3-embedding:4b` (Q4_K_M), sem prefixo.** Único arm que
passa o gate *e* supera a produção em todas as métricas de qualidade — seed@5
0,364 vs 0,305 (+19%), MRR 0,760 vs 0,604 (+26%), nDCG 0,506 vs 0,422 (+20%).

Quatro arms passam no gate (`gte cru`, `gte instr`, `qwen3-4b Q4 cru`,
`qwen3-4b fp16 instr`) e entre eles o Q4 cru domina. Curiosamente o **fp16 cru
reprova** e o **Q4 cru passa**: o modelo de menor precisão se saiu melhor no
fluxo completo, o oposto do que o teste denso isolado sugeria.

**O prefixo de instrução não se sustentou.** No denso valia +25%; no híbrido
derruba seed@5 de 0,305 para 0,268 no gte. Fica implementado e **desligado**
(`QUERY_INSTRUCTION_ENABLED=false`), para re-medição a cada troca de modelo.

### Decisão tomada

**Adotado em produção em 2026-08-11**, por decisão do time, com as ressalvas
abaixo registradas e aceitas:

1. O veredito passa/reprova depende de **um caso em oito** — evidência frágil
   até a Onda 0.4 ampliar o gold.
2. **Todas** as falhas de **todos** os arms estão no slice FAR, em perguntas de
   agregação. O fator limitante é **grão**, não o modelo, e grão se resolve no
   card v2 (1.1) e no `Table.grain_key` (4.1), não trocando embedding.

A recomendação técnica original era esperar o card v2. O time optou por fixar o
melhor arm medido agora e reavaliar na Onda 5 — o que é defensável: o Q4 domina
a produção em todas as métricas de qualidade sem violar guardrail, e a troca é
reversível pelas linhas `# ANTES:` do `.env`. O que **não** muda é que a Onda 5
continua obrigatória: a escolha foi feita sobre a KB degradada de hoje.

---

## 8. Por onde seguir

1. **0.5** — consertar as 5 falhas do `eval-quick`; sem gate executável nada
   depois disso é verificável.
2. **1.1 (card v2)** — maior alavanca isolada; ataca D3 e habilita 2.1 e 4.1.
3. **1.2 e 1.3** — baratas, medíveis em isolamento.
4. **2.1** — o roteador; resolve `fds-entrega-ts-001` independentemente do modelo.
5. **Onda 5** — repetir a comparação de modelos sobre o card v2.

**4.1 corre em paralelo com tudo**: é o único item que um usuário do chat percebe
hoje, e não depende do retrieval.

Registrar cada onda em `eval/experiments/` no formato da equipe e rodar
`eval-quick` antes e depois, `eval-full` para concluir. Nenhum gold, policy ou
baseline promovido automaticamente.

---

### Artefatos desta rodada

| Arquivo | Conteúdo |
|---|---|
| `eval/reports/20260811-live-run.json` | baseline gte, híbrido, 8/8 sem erro |
| `eval/reports/20260811-qwen3-4b-fp16-{run,report}.json` | candidato qwen3, híbrido |
| `eval/reports/rescored-{gte,qwen3}.json` | ambos re-pontuados com as métricas da 0.1 |
| `eval/experiments/20260811-dense-model-instruct-ab.json` | registro completo do experimento |

Collections de teste no Qdrant (podem ser apagadas): `documents_qwen3`,
`documents_q3_4b_fp16`, `documents_q3_8b_q8`, `documents_v2`.

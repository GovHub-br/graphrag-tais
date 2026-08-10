# Comparativo de métricas — Retrieval baseline → Fase 1 → Fase 2 (2026-08-04)

Avaliação do efeito das mudanças de retrieval (Fase 1 + Fase 2) sobre o dataset
`mcid-business-retrieval 0.1.0` (**draft**, 8 casos de dashboard). Medido ao vivo
com o collector `eval.retrieval.live` (canal híbrido), contra o mesmo snapshot
(1.289 cards no Qdrant, 74 tabelas / 1.239 relações no Neo4j).

> **Status honesto:** dataset `draft`, sem baseline aceita → é **diagnóstico
> bootstrap**, não gate verde. As colunas comparam runs do mesmo snapshot; o delta
> mede o efeito do código, não uma aprovação de qualidade.

- **Baseline** = audit `eval/reports/candidate.json` (código antes das fases; idêntico ao before coletado).
- **Fase 1** = `eval/reports/fase1-after-report.json` — scope filter + fusão determinística + poda de colunas.
- **Fase 2** = `eval/reports/fase2-evidence-report.json` — Fase 1 + expansão por evidência (`EXPANSION_MODE=evidence`).

## 1. Guardrails (não podem regredir) — todos mantidos ✅

| Métrica | Baseline | Fase 1 | Fase 2 | |
|---|--:|--:|--:|---|
| `error_rate` | 0.0000 | 0.0000 | 0.0000 | ✅ |
| `structural_sufficiency` | 1.0000 | 1.0000 | 1.0000 | ✅ |
| `eligible_join_relationship_recall` | 1.0000 | 1.0000 | 1.0000 | ✅ |
| `join_path_recall` | 1.0000 | 1.0000 | 1.0000 | ✅ |
| `unauthorized_join_rate` | 0.0000 | 0.0000 | 0.0000 | ✅ |
| `table_recall` | 1.0000 | 1.0000 | 1.0000 | ✅ |
| `node_recall` | 1.0000 | 1.0000 | 1.0000 | ✅ |
| `weighted_dependency_recall` | 1.0000 | 1.0000 | 1.0000 | ✅ |

**Checagens suplementares (fora do summary, calculadas dos traces):**
- Cobertura de **colunas-gold** na serialização podada: **50/50 = 100%** (nenhuma perdida).
- Cobertura de **tabelas-gold** no modo evidence: **8/8 casos, 0 perdidas**.
- **Determinismo** do top-5 entre runs: **8/8 em ordem e membros** (baseline: 6/8 ordem, 7/8 membros).

## 2. O que MELHOROU

| Métrica | Baseline | Fase 1 | Fase 2 | Δ total | Onde |
|---|--:|--:|--:|--:|---|
| **Prompt p95 (chars)** | 35.310 | 23.355 | **16.826** | **−52,4%** | poda colunas + evidência |
| **Prompt médio (chars)** | 21.453 | 14.261 | **10.518** | **−51,0%** | idem |
| **Schema context médio** | 19.023 | 11.831 | **8.662** | **−54,5%** | idem |
| **Over-expansion** | 27,14× | 27,14× | **16,60×** | **−38,9%** | expansão por evidência |
| `subgraph_size_ratio` | 28,14× | 28,14× | **17,60×** | −37,5% | evidência |
| **Tabelas no contexto (média)** | 5,75 | 5,75 | **4,13** | −28,3% | evidência |
| Colunas no subgrafo (grafo) | 186,4 | 186,4 | **115,0** | −38,3% | evidência (menos tabelas) |
| `node_precision` | 0,1273 | 0,1273 | **0,1377** | +8,2% | evidência |
| `node_f1` | 0,2000 | 0,2000 | **0,2193** | +9,7% | evidência |
| **`mrr_at_10`** | 0,5312 | **0,6667** | 0,6667 | +25,5% | fusão determinística |
| `ndcg_at_10` | 0,4058 | **0,4345** | 0,4345 | +7,1% | fusão determinística |
| **Latência total p95 (ms)** | 745 | 275 | **216** | **−71%** | pool menor + menos nós no grafo |
| Latência total p50 (ms) | 259 | 180 | **131** | −49,6% | idem |

## 3. O que NÃO melhorou / ainda falta

| Métrica | Baseline | Fase 2 | Situação |
|---|--:|--:|---|
| **`seed_recall_at_5`** | 0,3052 | **0,3052** | 🔴 **parado** — a recuperação vetorial ainda acha só ~30% das sementes-gold no top-5. É a causa-raiz; as fases cortaram contexto mas não subiram recall. |
| `seed_recall_at_10` | 0,3927 | 0,3927 | 🔴 parado (mesma causa). |
| `seed_table_recall_at_5` | 0,6250 | 0,6250 | 🟠 ~37% das tabelas-gold ainda chegam **só por expansão**, não como semente — é o que torna a poda de expansão uma heurística (não garantia). |
| `overexpansion` | 27,14× | 16,60× | 🟠 caiu, mas **ainda alto** (alvo saudável ~1–3×). O restante é estrutural: hubs `cadastro_pj`/`fds_cadastro_pj` e a serialização de todas as colunas das tabelas-semente. |
| `disconnected_rate` | 0,3017 | 0,3146 | 🟠 leve piora (mais proporção de nós desconectados) — efeito colateral de ter menos tabelas; métrica de ruído, baixo impacto. |
| `node_precision` | 0,1273 | 0,1377 | 🟠 subiu pouco — porque o eval conta nós do **grafo** (todas as colunas das tabelas), não a serialização podada; só cai de verdade com menos **tabelas**. |
| Prompt p95 vs alvo do audit | — | 16.826 | 🟠 alvo era **<12.000**; chegamos a 16,8k. Falta um passo (poda mais agressiva ou mais expansão cortada com segurança). |

### Limitações estruturais conhecidas (não são regressão)

- **O eval mede o subgrafo do GRAFO, não a serialização.** `node_precision`,
  `overexpansion`, `node_recall` vêm de `_introspect_graph` (todas as colunas das
  tabelas do contexto). A poda de **colunas** aparece só nas métricas de **chars**
  (o prompt real do LLM). Por isso a poda de colunas deu −51% no prompt mas 0 em
  over-expansion. Para o eval creditar a poda de colunas, seria preciso medir
  recall/precisão sobre os **nós serializados** (extensão futura do framework).
- **Grain/fan-out (E2E, fora deste benchmark):** `empreendimento`/`ficha_empreendimento`
  têm cada APF duplicado (1.646 linhas p/ 823 APFs) → o renderer soma duplicatas e
  dobra valores no gráfico. É bug de execução/grain, não de retrieval.
- **Gold `draft` / 8 casos:** confiança estatística baixa. `evidence` foi medido
  seguro aqui, mas não é garantia universal.

## 4. Leitura geral

As fases entregaram **contexto ~52% menor, ranking determinístico e melhor
ordenado (MRR +25%), latência −71%, com zero regressão de correção** (suficiência,
JOIN, recall e cobertura de gold em 100%). O ganho é real e do lado seguro.

**O gargalo que sobra é o `seed_recall` (~30%)** — não foi tocado pelas fases. Ele
é a causa de:
- a expansão do grafo ainda precisar existir para "resgatar" ~37% das tabelas-gold;
- a poda de expansão ser heurística (segura no gold, mas sem garantia);
- o over-expansion não chegar perto de 1–3×.

## 5. Próximos passos priorizados

1. **Subir o seed recall — prefixo de instrução no GTE** (ADR-0009, lado da query,
   sem reindexar). É o lever de maior alavancagem: mais tabelas-gold viram semente
   → a expansão por evidência fica à prova de bala e o over-expansion cai de verdade.
   Medir Recall@5/@10 com fingerprint próprio (A/B contra pergunta crua).
2. **Ampliar o gold** (de 8 para ~30–50 casos, revisão de domínio) para firmar a
   confiança e sair do estado `draft`.
3. **Empurrar o prompt p95 < 12k**: baixar `PRUNE_SEED_TABLE_MAX_COLS` (25→~15) e
   re-medir a cobertura de colunas-gold; e/ou poda mais fina de colunas por relevância.
4. **Estender o eval** para medir recall/precisão sobre os **nós serializados**
   (não só o grafo) — assim a poda de colunas é creditada nas métricas de nó.
5. **Guardrail E2E de grain** (dedup por APF; renderer não somar duplicatas) — resolve
   o gráfico dobrado, independente do retrieval.

### Comandos para reproduzir

```bash
# collector (dentro do container; ⚠️ apague /app/eval antes de re-copiar — docker cp aninha)
docker exec hayhooks rm -rf /app/eval && docker cp eval hayhooks:/app/eval
docker exec -w /app -e PYTHONPATH=/app:/app/src -e EXPANSION_MODE=evidence hayhooks \
  python -m eval.retrieval.live --dataset eval/datasets/retrieval/mcid-business-v0.1.0.json \
  --output eval/reports/fase2-evidence-run.json --channel hybrid
# score
docker exec -w /app -e PYTHONPATH=/app:/app/src hayhooks python -m eval.retrieval.cli score \
  --dataset eval/datasets/retrieval/mcid-business-v0.1.0.json \
  --run eval/reports/fase2-evidence-run.json --output-json eval/reports/fase2-evidence-report.json
```

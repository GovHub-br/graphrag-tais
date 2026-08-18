# Avaliação do modo Explorar (Persona 1)

Benchmark do modo `Explorar` — a pessoa que **não conhece as bases** e quer
descobrir o que existe ([ADR-0014](../../docs/adr/0014-personas-e-modos-de-uso-do-assistente.md)).

Separado de [`eval/retrieval`](../retrieval/README.md) e de `eval/answer` porque
a **unidade do gabarito muda**. Lá a pergunta é "a tabela certa foi escolhida?";
aqui é "**o conjunto** de tabelas relevantes foi apresentado, e em que ordem?".
São objetivos opostos — precisão vs. cobertura — e medi-los no mesmo contrato
esconderia justamente a diferença que define as personas.

## O que é medido

**Ranking** (k ∈ 5, 10, 15) sobre `ranked_tables`, a ordem em que a resposta
apresenta as tabelas:

| métrica | pergunta que responde |
|---|---|
| `table_recall_at_k` | as tabelas relevantes apareceram? |
| `core_recall_at_k` | **a tabela principal** apareceu? (só relevância 3) |
| `table_precision_at_k` | o que apareceu era relevante? |
| `mrr_at_k` | quanto a pessoa lê até o primeiro acerto? |
| `ndcg_at_k` | os mais relevantes vieram antes? (ganho graduado) |
| `map_at_k` | **todos** os relevantes estão bem ordenados? |

**Contrato da persona**:

| métrica | alvo | por quê |
|---|---|---|
| `catalog_fidelity` | **1,0** | tabela inventada é entregue como verdade a quem não tem como conferir |
| `gap_disclosure` | maximizar | recusar é certo; recusar mal ("refine a pergunta") não |
| `domain_recall` / `_precision` | maximizar | antes da tabela, a pessoa precisa saber em que domínio procurar |
| `question_groundedness`, `sample_sql_grounded` | 1,0 | detectores de regressão: caem se texto do LLM começar a vazar sem ancoragem |
| `listed_ratio` | ler junto | **recall é gameável**: listar o catálogo inteiro dá 1,0 |

> `table_recall` sem `table_precision` e `listed_ratio` ao lado não significa
> nada. Está assertado em `tests/test_explore_eval.py::TestAntiGaming`.

## Como rodar

```bash
# offline — contrato do gold + motor de métricas em fixtures (segundos, sem serviços)
make explore-quick

# live — CHAMA O LLM, precisa dos serviços + VPN; roda no container e já pontua
make eval-explore

# repontuar um run já coletado (sem gastar LLM de novo)
make explore-score EXPLORE_RUN=eval/reports/explore-run.json

# política de aceite (não conclusiva enquanto o gold for `draft`)
make explore-gate [EXPLORE_BASELINE=eval/baselines/mcid-explore-v1.json]
```

`live.py` guarda a **resposta estruturada inteira**, não só as métricas: mudar
uma métrica depois e reavaliar runs antigos é possível — e ajustar a métrica
olhando para uma rodada nova a cada tentativa, não.

## Estado atual

**Bootstrap, igual ao benchmark de retrieval.** O gold `mcid-explore-v0.1.0`
está em **`draft`**: 18 casos escritos a partir do catálogo ingerido, **sem
revisão de domínio**. Enquanto o status não for `accepted`, o CLI marca o gate
como não conclusivo — um gabarito que ninguém revisou não deve aprovar nem
reprovar mudança nenhuma ([ADR-0012](../../docs/adr/0012-desenvolvimento-orientado-a-metricas-no-retrieval.md)).

Limitações declaradas:

- As perguntas foram escritas por quem implementou o modo. Vale como diagnóstico,
  não como voz do usuário — o mesmo cuidado metodológico do item A3 do
  [plano de 13/08](../../docs/20260813-plano-proximos-passos.md).
- `expected_gaps` só mede de verdade com `SCOPE_GAPS` preenchido no `.env`; sem
  isso, mede apenas o que o LLM declarar por conta própria.
- Não existe baseline aceita. A primeira rodada com serviços produz a linha de
  partida diagnóstica, não um gate verde.

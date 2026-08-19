"""Métricas do modo EXPLORAR (Persona 1) — ADR-0014.

O benchmark da Persona 2 pergunta "**a** tabela certa foi escolhida?". Aqui a
pergunta é outra: "**o conjunto** de tabelas relevantes foi apresentado, e em que
ordem?". Por isso este módulo é independente de `eval/retrieval` — a unidade do
gabarito muda (conjunto graduado em vez de subgrafo mínimo), e misturar os dois
contratos esconderia justamente a diferença que define as personas.

O que é medido, em três famílias:

1. **Ranking** — `recall@k`, `precision@k`, `hit@k`, `MRR@k`, `nDCG@k`, `MAP@k`
   sobre a lista de tabelas que a resposta apresenta, na ordem em que apresenta.
2. **Anti-gaming** — recall sozinho é trivial de maximizar: basta listar o
   catálogo inteiro. `precision@k` e `listed_ratio` são o contrapeso, e nenhuma
   leitura de recall vale sem eles ao lado.
3. **Contrato da persona** — fidelidade ao catálogo (não inventar tabela),
   cobertura de domínios, declaração de lacunas e ancoragem das perguntas
   sugeridas e do SQL de amostra.

Convenção herdada do projeto: 1.0 é bom em todas as métricas; taxas onde 0.0 é
bom levam o sufixo `_rate`. Campos que começam com `_` são diagnóstico e não
entram na agregação.
"""
from __future__ import annotations

import math
import unicodedata
from typing import Iterable, Mapping, Sequence

# Cortes avaliados. 5 = o que cabe na tela sem rolar; 10 = leitura confortável;
# 15 = o teto default de sementes do modo (`EXPLORE_TOP_K`).
CUTOFFS = (5, 10, 15)

# Nota de relevância que define uma tabela "central" para a pergunta.
CORE_RELEVANCE = 3

RANKING_METRIC_NAMES = tuple(
    f"{name}_at_{k}"
    for k in CUTOFFS
    for name in ("table_recall", "core_recall", "table_precision", "hit", "mrr", "ndcg", "map")
)

PERSONA_METRIC_NAMES = (
    "catalog_fidelity",
    "off_catalog_rate",
    "domain_recall",
    "domain_precision",
    "domain_f1",
    "gap_disclosure",
    "question_groundedness",
    "questions_per_table",
    "sample_sql_present",
    "sample_sql_grounded",
    "answered_rate",
    "listed_ratio",
)

CASE_METRIC_NAMES = RANKING_METRIC_NAMES + PERSONA_METRIC_NAMES


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _safe(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _fold(text: str) -> str:
    """Minúsculas sem acento — 'execução' e 'execucao' têm de casar."""
    norm = unicodedata.normalize("NFKD", str(text or "").lower())
    return "".join(ch for ch in norm if not unicodedata.combining(ch))


def physical_name(name: str) -> str:
    """Normaliza para `schema.tabela` em minúsculas.

    O gold pode declarar o FQN do catálogo (`Cidades.cidades.schema.tabela`) ou o
    nome físico; a resposta sempre usa o físico. Comparar em nome físico evita
    que a métrica dependa de qual das duas formas quem escreveu o gold usou.
    """
    parts = [p for p in str(name or "").strip().strip("`").split(".") if p]
    if not parts:
        return ""
    return ".".join(parts[-2:]).lower() if len(parts) >= 2 else parts[-1].lower()


def _dedup(names: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        name = physical_name(raw)
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


# --------------------------------------------------------------------------- #
# Família 1 — ranking
# --------------------------------------------------------------------------- #
def ranking_metrics(
    ranked: Sequence[str],
    relevance: Mapping[str, float],
    cutoffs: Sequence[int] = CUTOFFS,
) -> dict[str, float]:
    """Métricas de ranking sobre a lista apresentada.

    `relevance` é graduada (3 = central, 2 = útil, 1 = tangencial). `recall` e
    `precision` contam qualquer nota > 0; `core_recall` só as centrais — é a
    métrica que responde "a pessoa saiu sabendo da tabela principal?", que é o
    resultado que interessa para a Persona 1.

    `nDCG` usa ganho `2^rel - 1` e ideal calculado sobre o gold inteiro, não
    sobre o top-k: se uma tabela relevante nunca foi apresentada, isso tem de
    aparecer como perda no nDCG, e não ser normalizado para longe.
    """
    graded = {physical_name(k): float(v) for k, v in relevance.items() if float(v) > 0}
    ranked = _dedup(ranked)
    relevant = set(graded)
    core = {name for name, rel in graded.items() if rel >= CORE_RELEVANCE}

    ideal_gains = sorted((2.0 ** rel - 1.0 for rel in graded.values()), reverse=True)
    out: dict[str, float] = {}

    for k in cutoffs:
        top = ranked[:k]
        hits = [name for name in top if name in relevant]

        out[f"table_recall_at_{k}"] = _safe(len(hits), len(relevant)) if relevant else 1.0
        out[f"core_recall_at_{k}"] = (
            _safe(len([n for n in top if n in core]), len(core)) if core else 1.0
        )
        out[f"table_precision_at_{k}"] = _safe(len(hits), len(top))
        out[f"hit_at_{k}"] = 1.0 if hits else 0.0

        # MRR: posição da PRIMEIRA tabela relevante. Numa resposta que a pessoa
        # lê de cima para baixo, é o custo de leitura até o primeiro acerto.
        rr = 0.0
        for position, name in enumerate(top, start=1):
            if name in relevant:
                rr = 1.0 / position
                break
        out[f"mrr_at_{k}"] = rr

        # nDCG graduado.
        dcg = sum(
            (2.0 ** graded.get(name, 0.0) - 1.0) / math.log2(position + 1)
            for position, name in enumerate(top, start=1)
        )
        idcg = sum(
            gain / math.log2(position + 1)
            for position, gain in enumerate(ideal_gains[:k], start=1)
        )
        out[f"ndcg_at_{k}"] = _safe(dcg, idcg) if idcg else 1.0

        # MAP: precisão média nas posições de acerto — sensível a ordenar bem
        # TODOS os relevantes, não só o primeiro.
        found = 0
        precision_sum = 0.0
        for position, name in enumerate(top, start=1):
            if name in relevant:
                found += 1
                precision_sum += found / position
        out[f"map_at_{k}"] = _safe(precision_sum, min(len(relevant), k)) if relevant else 1.0

    return out


# --------------------------------------------------------------------------- #
# Família 3 — contrato da persona
# --------------------------------------------------------------------------- #
def catalog_fidelity(cited: Sequence[str], off_catalog: Sequence[str]) -> dict[str, float]:
    """Alguma tabela apresentada não existe no catálogo?

    É a métrica mais importante da Persona 1 e a única com alvo absoluto: 1,0.
    Na Persona 2 uma tabela inventada morre na execução do SQL; aqui ela é
    entregue como verdade a quem está explorando **porque não conhece o
    catálogo** e não tem como conferir.
    """
    total = len(cited) + len(off_catalog)
    return {
        "catalog_fidelity": 1.0 - _safe(len(off_catalog), total) if total else 1.0,
        "off_catalog_rate": _safe(len(off_catalog), total),
        "_off_catalog": sorted(set(off_catalog)),
    }


def domain_metrics(
    presented: Sequence[str], expected: Sequence[str]
) -> dict[str, float]:
    """Os domínios certos foram apontados (e só eles)?

    Antes de escolher a tabela, a Persona 1 precisa saber em que bairro procurar.
    Sem `expected_domains` no gold, o caso não penaliza (1.0) — ausência de
    gabarito não é evidência de acerto nem de erro.
    """
    got = {_fold(d) for d in presented if d}
    want = {_fold(d) for d in expected if d}
    if not want:
        return {"domain_recall": 1.0, "domain_precision": 1.0, "domain_f1": 1.0}
    hits = len(got & want)
    recall = _safe(hits, len(want))
    precision = _safe(hits, len(got)) if got else 0.0
    f1 = _safe(2 * precision * recall, precision + recall)
    return {
        "domain_recall": recall,
        "domain_precision": precision,
        "domain_f1": f1,
        "_domains_missed": sorted(want - got),
    }


def gap_disclosure(declared: Sequence[str], expected: Sequence[str]) -> dict[str, float]:
    """As lacunas conhecidas foram ditas?

    Quando o conceito está fora da ingestão, o sistema **acerta ao recusar** — o
    que se mede aqui é se ele recusou de forma útil. O casamento é por termo
    dobrado (sem acento) contido no texto declarado: o gold guarda o conceito
    ("execução orçamentária"), não a frase exata da resposta.
    """
    want = [g for g in expected if str(g).strip()]
    if not want:
        return {"gap_disclosure": 1.0}
    blob = " ".join(_fold(d) for d in declared)
    hits = [g for g in want if _fold(g) in blob]
    return {
        "gap_disclosure": _safe(len(hits), len(want)),
        "_gaps_missed": sorted(set(want) - set(hits)),
    }


def groundedness(
    tables: Sequence[Mapping],
    sample_sql: str,
    presented: Sequence[str],
) -> dict[str, float]:
    """Perguntas sugeridas e SQL de amostra apontam para tabelas apresentadas?

    Deve ser 1,0 por construção (o builder ancora tudo no catálogo). Fica no
    conjunto como **detector de regressão**: se um dia cair, alguma coisa passou
    a vazar texto do LLM direto para a resposta.
    """
    known = {physical_name(name) for name in presented}
    with_questions = [t for t in tables if t.get("questions")]
    grounded = [t for t in with_questions if physical_name(t.get("table", "")) in known]
    total_questions = sum(len(t.get("questions") or []) for t in tables)

    sql_tables = _sql_tables(sample_sql)
    unknown_sql = [t for t in sql_tables if t not in known]
    return {
        "question_groundedness": _safe(len(grounded), len(with_questions)) if with_questions else 1.0,
        "questions_per_table": _safe(total_questions, len(tables)) if tables else 0.0,
        "sample_sql_present": 1.0 if (sample_sql or "").strip() else 0.0,
        "sample_sql_grounded": (
            1.0 - _safe(len(unknown_sql), len(sql_tables)) if sql_tables else
            (1.0 if not (sample_sql or "").strip() else 0.0)
        ),
        "_sql_tables_unknown": sorted(set(unknown_sql)),
    }


def _sql_tables(sql: str) -> list[str]:
    """Tabelas citadas no SQL de amostra.

    Parse deliberadamente simples (`FROM`/`JOIN` + identificador pontuado): a
    amostra é, por contrato, uma consulta de uma tabela com `LIMIT`. Um parser
    completo já existe em `eval/answer/sql_introspection.py` e serve à Persona 2,
    onde o SQL é arbitrário.
    """
    import re

    found = re.findall(r"\b(?:from|join)\s+([a-zA-Z_][\w.]*)", sql or "", flags=re.I)
    return _dedup(found)


# --------------------------------------------------------------------------- #
# Pontuação de um caso
# --------------------------------------------------------------------------- #
def score_case(answer: Mapping, gold: Mapping, catalog_size: int = 0) -> dict:
    """Pontua uma resposta do modo Explorar contra o gold. Não levanta:
    resposta malformada vira métrica ruim, não exceção — um run parcial ainda
    é informação, e abortar perderia os casos que rodaram."""
    ranked = list(answer.get("ranked_tables") or [])
    tables = list(answer.get("tables") or [])
    presented = ranked or [t.get("table", "") for t in tables]
    domains = [d.get("domain", "") for d in (answer.get("domains") or [])]
    domains += [d.get("schema", "") for d in (answer.get("domains") or [])]

    out: dict = {
        "_ranked": presented,
        "_summary_len": len((answer.get("summary") or "").strip()),
    }
    out.update(ranking_metrics(presented, gold.get("table_relevance") or {}))
    out.update(catalog_fidelity(presented, answer.get("off_catalog_tables") or []))
    out.update(domain_metrics(domains, gold.get("expected_domains") or []))
    out.update(gap_disclosure(answer.get("gaps") or [], gold.get("expected_gaps") or []))
    out.update(groundedness(tables, answer.get("sample_sql") or "", presented))

    out["answered_rate"] = 1.0 if (out["_summary_len"] > 0 and presented) else 0.0
    # Guarda anti-gaming: apresentar o catálogo inteiro garante recall 1,0 e não
    # ajuda ninguém. Sem tamanho de catálogo conhecido, fica neutro.
    size = catalog_size or int(answer.get("catalog_size") or 0)
    out["listed_ratio"] = _safe(len(presented), size)
    return out


def aggregate(cases: Sequence[Mapping[str, float]]) -> dict[str, float]:
    """Média por métrica, ignorando diagnóstico (`_`)."""
    if not cases:
        return {name: 0.0 for name in CASE_METRIC_NAMES}
    return {
        name: sum(float(c.get(name, 0.0)) for c in cases) / len(cases)
        for name in CASE_METRIC_NAMES
    }


def aggregate_by_slice(cases: Sequence[Mapping]) -> dict[str, dict[str, float]]:
    """Agregação por slice do gold (`far`, `conjuntura`, `catalogo_puro`, …).

    A média global esconde o modo de falha: um tema pode ir a 1,0 e outro a 0,3
    e a média parecer aceitável. Slice é onde a decisão do próximo passo nasce.
    """
    buckets: dict[str, list[Mapping]] = {}
    for case in cases:
        for name in case.get("slices") or ["_all"]:
            buckets.setdefault(name, []).append(case.get("metrics") or {})
    return {name: aggregate(items) for name, items in sorted(buckets.items())}


def percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(float(v) for v in values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]

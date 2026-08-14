"""Métricas de QUALIDADE DA RESPOSTA (item 0.3 do plano).

O benchmark de retrieval termina no subgrafo, por decisão do ADR-0012. Isso
deixou a jusante sem medição nenhuma: não havia como saber se o SQL gerado usa
o que foi recuperado, se respeita os JOINs autorizados ou se agrega sobre tabela
com linhas duplicadas.

As três primeiras métricas são **determinísticas** — puro parse contra o trace,
sem LLM e sem banco. A quarta (execution accuracy) precisa de `reference_sql` no
gold, que é trabalho de domínio.

Convenção: 1.0 é bom em todas; `unauthorized_*` são taxas onde 0.0 é bom.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from .sql_introspection import SqlShape, introspect

ANSWER_METRIC_NAMES = (
    "schema_faithfulness",
    "table_hallucination_rate",
    "column_hallucination_rate",
    "unauthorized_join_rate",
    "grain_compliance",
    "sql_parse_ok",
    "answered_rate",
)


def _safe(n: float, d: float) -> float:
    return n / d if d else 0.0


def schema_faithfulness(
    shape: SqlShape,
    available_tables: Iterable[str],
    available_columns: Iterable[str],
) -> dict[str, float]:
    """Toda tabela e coluna citada existe no subgrafo que foi recuperado?

    É a métrica de alucinação: o LLM foi instruído a usar APENAS o schema
    fornecido, e aqui isso deixa de ser confiança e vira verificação. `columns`
    usa o conjunto de nomes recuperados (sem qualificador) porque o gerador nem
    sempre qualifica — é conservador de propósito: só acusa o que com certeza
    não existe em lugar nenhum do contexto.
    """
    tables = {t.lower() for t in available_tables}
    columns = {c.lower() for c in available_columns}

    bad_tables = {t for t in shape.tables if t not in tables}
    bad_columns = {c for c in shape.columns if c not in columns}

    total = len(shape.tables) + len(shape.columns)
    bad = len(bad_tables) + len(bad_columns)
    return {
        "schema_faithfulness": 1.0 - _safe(bad, total) if total else 1.0,
        "table_hallucination_rate": _safe(len(bad_tables), len(shape.tables)),
        "column_hallucination_rate": _safe(len(bad_columns), len(shape.columns)),
        "_hallucinated_tables": sorted(bad_tables),
        "_hallucinated_columns": sorted(bad_columns),
    }


def join_authorization(
    shape: SqlShape,
    authorized_pairs: Iterable[tuple[str, str]],
) -> dict[str, float]:
    """Todo JOIN do SQL está entre os declarados como elegíveis?

    O prompt manda usar somente os JOINs listados; um JOIN inventado entre
    tabelas sem chave declarada produz produto cartesiano silencioso.
    """
    allowed = {tuple(sorted((a.lower(), b.lower()))) for a, b in authorized_pairs}
    bad = {p for p in shape.join_pairs if p not in allowed}
    return {
        "unauthorized_join_rate": _safe(len(bad), len(shape.join_pairs)),
        "_unauthorized_joins": sorted(bad),
    }


def grain_compliance(
    shape: SqlShape,
    duplicated_tables: Mapping[str, str],
) -> dict[str, float]:
    """Agregou sobre tabela com linhas duplicadas sem deduplicar?

    `duplicated_tables` mapeia nome físico -> chave do grão, e vem das tabelas
    cujo grão declara duplicação. A regra é deliberadamente simples: se houve
    agregação tocando uma dessas tabelas, o SQL precisa ter DISTINCT em algum
    lugar. É grosseiro, mas pega o modo de falha real (SUM direto sobre a
    tabela) sem falso positivo em consulta que já deduplica.
    """
    risky = {t for t in shape.aggregated_tables if t in duplicated_tables}
    if not risky:
        return {"grain_compliance": 1.0, "_grain_risk_tables": []}
    ok = 1.0 if shape.has_distinct else 0.0
    return {"grain_compliance": ok, "_grain_risk_tables": sorted(risky)}


def score_answer(
    sql: str,
    *,
    available_tables: Iterable[str],
    available_columns: Iterable[str],
    authorized_pairs: Iterable[tuple[str, str]] = (),
    duplicated_tables: Mapping[str, str] | None = None,
) -> dict:
    """Pontua uma resposta. Não levanta: SQL inválido vira métrica, não exceção."""
    shape = introspect(sql)
    out: dict = {
        "sql_parse_ok": 0.0 if shape.parse_error else 1.0,
        "answered_rate": 1.0 if (sql or "").strip() else 0.0,
        "_parse_error": shape.parse_error,
        "_tables": sorted(shape.tables),
        "_has_distinct": shape.has_distinct,
        "_has_aggregate": shape.has_aggregate,
    }
    if shape.parse_error:
        # Sem parse não há o que afirmar; marcar como falha explícita e seguir.
        out.update(
            schema_faithfulness=0.0,
            table_hallucination_rate=1.0,
            column_hallucination_rate=1.0,
            unauthorized_join_rate=0.0,
            grain_compliance=0.0,
        )
        return out
    out.update(schema_faithfulness(shape, available_tables, available_columns))
    out.update(join_authorization(shape, authorized_pairs))
    out.update(grain_compliance(shape, duplicated_tables or {}))
    return out


def aggregate(cases: Sequence[Mapping[str, float]]) -> dict[str, float]:
    """Média por métrica sobre os casos, ignorando campos de diagnóstico (`_`)."""
    if not cases:
        return {name: 0.0 for name in ANSWER_METRIC_NAMES}
    return {
        name: sum(float(c.get(name, 0.0)) for c in cases) / len(cases)
        for name in ANSWER_METRIC_NAMES
    }

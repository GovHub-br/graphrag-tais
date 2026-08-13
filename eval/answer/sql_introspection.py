"""Extração determinística do que o SQL gerado referencia.

É a base de três coisas que hoje ninguém mede: fidelidade ao subgrafo, JOIN
autorizado e conformidade de grão. Tudo aqui é parse — nenhuma chamada de LLM,
nenhum acesso ao banco —, então roda em CI e é reproduzível.

O parser é o `sqlglot` com dialeto `postgres`, o mesmo banco que executa.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp


@dataclass
class SqlShape:
    """O que o SQL referencia, normalizado."""

    tables: set[str] = field(default_factory=set)
    """Nomes físicos `schema.tabela` (minúsculos). Aliases já resolvidos."""

    columns: set[str] = field(default_factory=set)
    """Nomes de coluna citados, sem qualificador."""

    qualified_columns: set[str] = field(default_factory=set)
    """`schema.tabela.coluna` quando o qualificador permitiu resolver."""

    join_pairs: set[tuple[str, str]] = field(default_factory=set)
    """Pares de tabelas unidas, ordenados, incluindo JOIN implícito por vírgula."""

    has_distinct: bool = False
    has_aggregate: bool = False
    aggregated_tables: set[str] = field(default_factory=set)
    parse_error: str = ""


_AGGREGATES = (exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max)


def _physical_name(table: exp.Table) -> str:
    parts = [p.name for p in (table.args.get("db"), table.this) if p is not None]
    return ".".join(x for x in parts if x).lower()


def introspect(sql: str) -> SqlShape:
    """Devolve a forma do SQL. Nunca levanta: erro de parse vira campo."""
    shape = SqlShape()
    if not (sql or "").strip():
        shape.parse_error = "SQL vazio"
        return shape
    try:
        tree = sqlglot.parse_one(sql, dialect="postgres")
    except Exception as exc:  # noqa: BLE001 — SQL vem de LLM, pode ser inválido
        shape.parse_error = f"{type(exc).__name__}: {exc}"
        return shape
    if tree is None:
        shape.parse_error = "parse devolveu vazio"
        return shape

    # alias -> nome físico, para resolver `d.valor` em subqueries e JOINs.
    alias_to_table: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        name = _physical_name(table)
        if not name:
            continue
        shape.tables.add(name)
        alias = table.alias_or_name
        if alias:
            alias_to_table[alias.lower()] = name

    # Aliases de saída (`SUM(x) AS total`) são referenciáveis em ORDER BY/HAVING
    # e o parser os devolve como Column. Não são colunas do schema: contá-los
    # como tal acusaria alucinação em SQL perfeitamente válido.
    output_aliases = {
        (alias.alias or "").lower()
        for alias in tree.find_all(exp.Alias)
        if alias.alias
    }

    for column in tree.find_all(exp.Column):
        col = (column.name or "").lower()
        if not col or col == "*" or col in output_aliases:
            continue
        shape.columns.add(col)
        qualifier = (column.table or "").lower()
        resolved = alias_to_table.get(qualifier)
        if resolved:
            shape.qualified_columns.add(f"{resolved}.{col}")

    shape.has_distinct = bool(list(tree.find_all(exp.Distinct)))
    aggregates = [n for kind in _AGGREGATES for n in tree.find_all(kind)]
    shape.has_aggregate = bool(aggregates)

    # Uma agregação "toca" as tabelas do SELECT em que ela vive. Sem isso não dá
    # para dizer se um SUM caiu sobre tabela com linhas duplicadas.
    for node in aggregates:
        select = node.find_ancestor(exp.Select)
        scope = select if select is not None else tree
        for table in scope.find_all(exp.Table):
            name = _physical_name(table)
            if name:
                shape.aggregated_tables.add(name)

    for join in tree.find_all(exp.Join):
        right = join.this
        if not isinstance(right, exp.Table):
            continue
        right_name = _physical_name(right)
        select = join.find_ancestor(exp.Select)
        if select is None or not right_name:
            continue
        for table in select.find_all(exp.Table):
            left_name = _physical_name(table)
            if left_name and left_name != right_name:
                shape.join_pairs.add(tuple(sorted((left_name, right_name))))

    # JOIN implícito: FROM a, b — o mesmo risco de produto cartesiano.
    for select in tree.find_all(exp.Select):
        from_clause = select.args.get("from")
        if from_clause is None:
            continue
        names = {
            _physical_name(t)
            for t in from_clause.find_all(exp.Table)
        } - {""}
        if len(names) > 1:
            ordered = sorted(names)
            for i, left in enumerate(ordered):
                for right_name in ordered[i + 1:]:
                    shape.join_pairs.add((left, right_name))

    return shape

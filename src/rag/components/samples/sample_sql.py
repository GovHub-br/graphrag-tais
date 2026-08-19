"""Preparo determinístico do SQL da amostra (Persona 1) — sem LLM.

A amostra é a primeira feature do RAG que devolve **linhas reais** do banco, e o
SQL que a produz vem de um LLM. Este módulo é a parte que não depende de modelo
nenhum: valida a consulta, força o teto de linhas, e sabe reconstruir uma
consulta equivalente **a partir do catálogo** quando a do LLM não roda.

Tudo aqui é função pura sobre strings — roda no host, sem Haystack, sem MCP.
"""
from __future__ import annotations

import csv
from typing import Any, Dict, List, Sequence, Tuple

import sqlglot
from sqlglot import exp

_DIALECT = "postgres"

#: Colunas exibidas na prévia do chat (o arquivo leva todas).
MAX_PREVIEW_COLUMNS = 6
#: Colunas projetadas quando a consulta é reconstruída do catálogo.
MAX_FALLBACK_COLUMNS = 8

# Nós que denunciam escrita. `Command` cobre o que o sqlglot não parseia em
# detalhe (GRANT, COPY, …) — na dúvida, recusa.
_WRITE_NODES = tuple(
    node
    for node in (
        getattr(exp, name, None)
        for name in ("Insert", "Update", "Delete", "Drop", "Create", "Alter",
                     "Command", "TruncateTable", "Merge")
    )
    if node is not None
)


def guard(sql: str, limit: int) -> Tuple[str, str]:
    """Valida e envelopa o SQL da amostra. Devolve `(sql_executável, recusa)`.

    Duas garantias, ambas independentes do modelo:

    1. **É uma leitura só.** Uma instrução, `SELECT`/`WITH`, sem nó de escrita
       em lugar nenhum (nem dentro de CTE). O postgres-mcp já roda em modo
       RESTRICTED — isto é o cinto além do suspensório, e devolve motivo legível.
    2. **Tem teto.** O `LIMIT` do LLM não é confiável (pode não existir): a
       consulta vira subconsulta de um `SELECT * FROM (...) LIMIT n`, então o
       teto vale mesmo que a original não tenha nenhum.
    """
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        return "", "SQL de amostra vazio"
    try:
        statements = [s for s in sqlglot.parse(text, read=_DIALECT) if s is not None]
    except Exception:  # noqa: BLE001 — sqlglot levanta várias; qualquer uma recusa
        return "", "não consegui interpretar o SQL"
    if len(statements) != 1:
        return "", "mais de uma instrução"

    statement = statements[0]
    if not isinstance(statement, (exp.Select, exp.Union, exp.Subquery)):
        return "", "não é uma consulta de leitura"
    if any(isinstance(node, _WRITE_NODES) for node in statement.walk()):
        return "", "contém instrução de escrita"

    return f"SELECT * FROM ({statement.sql(dialect=_DIALECT)}) AS amostra LIMIT {int(limit)}", ""


def target_table(sql: str, known_tables: Sequence[str]) -> str:
    """Tabela do catálogo que o SQL referencia (`schema.tabela`), ou "".

    Casa pelo nome físico completo e, se não achar, pelo nome curto — o LLM às
    vezes escreve `mapa_nacional` onde o catálogo tem `far.mapa_nacional`."""
    try:
        statement = sqlglot.parse_one(sql, read=_DIALECT)
    except Exception:  # noqa: BLE001
        return ""
    referenced = []
    for node in statement.find_all(exp.Table):
        name = node.name
        schema = node.db
        referenced.append(f"{schema}.{name}".lower() if schema else name.lower())

    by_full = {t.lower(): t for t in known_tables}
    by_short = {t.split(".")[-1].lower(): t for t in known_tables}
    for ref in referenced:
        if ref in by_full:
            return by_full[ref]
    for ref in referenced:
        if ref.split(".")[-1] in by_short:
            return by_short[ref.split(".")[-1]]
    return ""


def referenced_columns(sql: str) -> List[str]:
    """Colunas citadas no SQL, em minúsculas e sem repetição (ordem preservada)."""
    try:
        statement = sqlglot.parse_one(sql, read=_DIALECT)
    except Exception:  # noqa: BLE001
        return []
    seen: Dict[str, None] = {}
    for node in statement.find_all(exp.Column):
        name = (node.name or "").lower()
        if name and name != "*":
            seen.setdefault(name, None)
    return list(seen)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def catalog_sql(table: str, columns: Sequence[str], limit: int) -> str:
    """Consulta reconstruída do catálogo: projeção simples da tabela alvo.

    É o degrau que **garante** amostra. As colunas vêm do Neo4j (portanto
    existem, salvo catálogo desatualizado — e aí a falha é informação sobre o
    catálogo). Perde a intenção da consulta do LLM (uma contagem vira linhas
    cruas); em troca, roda."""
    if not table:
        return ""
    projection = ", ".join(_quote(c) for c in columns[:MAX_FALLBACK_COLUMNS]) or "*"
    physical = ".".join(_quote(part) for part in table.split(".") if part)
    return f"SELECT {projection} FROM {physical} LIMIT {int(limit)}"


def preview(columns: Sequence[str], rows: Sequence[Dict[str, Any]],
            max_columns: int = MAX_PREVIEW_COLUMNS,
            max_rows: int = 10) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Recorte da amostra que cabe numa tabela Markdown de chat.

    Prioriza colunas que **têm valor** nas linhas mostradas: uma coluna toda
    nula gasta largura e não informa nada."""
    shown_rows = list(rows[:max_rows])
    ordered = [c for c in columns if any(r.get(c) not in (None, "") for r in shown_rows)]
    ordered += [c for c in columns if c not in ordered]
    return ordered[:max_columns], shown_rows


def is_numeric_column(column: str, rows: Sequence[Dict[str, Any]]) -> bool:
    """Coluna numérica alinha à direita na tabela Markdown."""
    values = [r.get(column) for r in rows if r.get(column) is not None]
    return bool(values) and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                for v in values)


def write_csv(path: str, columns: Sequence[str], rows: Sequence[Dict[str, Any]]) -> str:
    """Escreve o CSV da amostra e devolve o caminho.

    `utf-8-sig` + `;`: é o par que faz o Excel em português abrir o arquivo com
    dois cliques, acento certo e colunas separadas. Quem for para o pandas passa
    `sep=';'`."""
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(columns), delimiter=";", lineterminator="\r\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c) for c in columns})
    return path

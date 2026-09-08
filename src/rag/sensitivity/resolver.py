"""De qual coluna de qual tabela veio cada coluna do resultado.

O catálogo de sensibilidade fala em `(tabela, coluna)`; o que chega do banco é
uma lista de nomes de saída, já passados por alias, `CAST` e envelope. Este
módulo faz a ponte, lendo o SQL que de fato rodou com `sqlglot` — o mesmo
parser que a guarda da amostra e o `_order_columns` já usam.

Três decisões que valem mais que o código:

1. **Agregação não é dado pessoal.** `COUNT(cpf)` conta pessoas, não as
   identifica — mascarar isso destruiria o caso de uso do sistema (medir) sem
   proteger ninguém. Projeção que contém função de agregação sai marcada como
   `aggregated` e o mascarador a ignora.
2. **Expressão escalar CARREGA a sensibilidade.** `CAST(cpf AS VARCHAR)`,
   `upper(nome)`, `concat(nome, ' ', sobrenome)` continuam sendo o dado
   pessoal. Por isso a busca é por qualquer `exp.Column` dentro da projeção,
   não só pela projeção que É uma coluna.
3. **Dúvida fecha.** Sem qualificador numa consulta com JOIN, com `SELECT *`,
   ou com o parse falhando, a coluna sai com TODAS as tabelas da consulta como
   candidatas — se ela é sensível em alguma, é mascarada. Mascarar demais
   estraga uma amostra; mascarar de menos vaza.

Funções puras sobre strings: rodam no host, sem Haystack e sem serviço.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from loguru import logger


@dataclass(frozen=True)
class Projection:
    """Uma coluna do resultado, já ligada à sua origem."""

    #: nome como volta do banco (chave das linhas), normalizado em minúscula
    output: str
    #: candidatos `(tabela, coluna)` — mais de um quando o qualificador falta
    sources: Tuple[Tuple[str, str], ...]
    #: a projeção passa por função de agregação (`COUNT`, `SUM`, …)
    aggregated: bool = False


@dataclass(frozen=True)
class Resolution:
    """O que se conseguiu apurar do SQL. `parsed=False` = tudo é dúvida."""

    tables: Tuple[str, ...]
    projections: Tuple[Projection, ...]
    #: a projeção é `*` — os nomes de saída não estão no SQL
    star: bool
    parsed: bool

    def by_output(self, name: str) -> Optional[Projection]:
        key = (name or "").strip().strip('"').lower()
        for projection in self.projections:
            if projection.output == key:
                return projection
        return None


_UNRESOLVED = Resolution(tables=(), projections=(), star=True, parsed=False)


def resolve(sql: str, dialect: str = "trino") -> Resolution:
    """Lê o SQL e devolve tabelas de origem + projeções resolvidas.

    Nunca levanta: SQL de LLM quebra parser, e o preço disso é cair no caminho
    conservador (tudo vira dúvida), não perder a resposta."""
    if not (sql or "").strip():
        return _UNRESOLVED
    try:
        import sqlglot
        from sqlglot import exp

        tree = sqlglot.parse_one(sql, read=dialect)
        if tree is None:
            return _UNRESOLVED
        tree = _unwrap(tree, exp)
        tables = _source_tables(tree, exp)
        selects = list(getattr(tree, "selects", []) or [])
        star = not selects or any(isinstance(e, exp.Star) for e in selects)
        alias_map = _alias_map(tree, exp)
        projections = tuple(
            _projection(expression, alias_map, tables, exp)
            for expression in selects
            if not isinstance(expression, exp.Star)
        )
        return Resolution(
            tables=tables, projections=projections, star=star, parsed=True
        )
    except Exception as exc:  # noqa: BLE001 — qualquer falha de parse é dúvida
        logger.debug("[sensibilidade] SQL não interpretado ({}) — modo conservador", exc)
        return _UNRESOLVED


def _unwrap(tree, exp):
    """Desce pelos envelopes `SELECT * FROM (…) AS x` até achar quem projeta.

    A amostra da Persona 1 chega envelopada por `sample_sql.guard`, e a
    projeção de fora é `*` — quem conhece as colunas é a consulta de dentro.
    Mesma mecânica do `_order_columns` do executor; três níveis bastam e evitam
    laço infinito em árvore estranha."""
    for _ in range(3):
        selects = list(getattr(tree, "selects", []) or [])
        if selects and not all(isinstance(e, exp.Star) for e in selects):
            break
        source = tree.args.get("from_") or tree.args.get("from")
        inner = getattr(source, "this", None) if source is not None else None
        if not isinstance(inner, exp.Subquery):
            break
        tree = inner.this
    return tree


def _table_name(node) -> str:
    """`schema.tabela` de um nó `exp.Table` (ignora o catálogo, que o SQL
    gerado não prefixa — ver ADR-0017)."""
    parts = [
        (node.text("db") or "").strip('"'),
        (node.name or "").strip('"'),
    ]
    return ".".join(p for p in parts if p).lower()


def _source_tables(tree, exp) -> Tuple[str, ...]:
    """Todas as tabelas físicas da consulta, inclusive dentro de CTE/subquery.

    Amplo de propósito: é a chave do curto-circuito (nenhuma tabela conhecida →
    nada a fazer) e o conjunto de candidatas quando o qualificador falta."""
    seen: List[str] = []
    for node in tree.find_all(exp.Table):
        name = _table_name(node)
        if name and name not in seen:
            seen.append(name)
    return tuple(seen)


def _alias_map(tree, exp) -> Dict[str, str]:
    """apelido/nome curto → `schema.tabela`, para resolver `t.cpf`."""
    mapping: Dict[str, str] = {}
    for node in tree.find_all(exp.Table):
        name = _table_name(node)
        if not name:
            continue
        mapping.setdefault(name, name)
        mapping.setdefault(name.rsplit(".", 1)[-1], name)
        alias = (node.alias or "").strip('"').lower()
        if alias:
            mapping[alias] = name
    return mapping


def _projection(expression, alias_map, tables, exp) -> Projection:
    output = (expression.alias_or_name or "").strip('"').lower()
    inner = expression.unalias() if hasattr(expression, "unalias") else expression

    # Agregação: o valor projetado é sobre um CONJUNTO de linhas, não sobre
    # uma pessoa. `COUNT(DISTINCT cpf)` é a resposta certa para "quantas
    # pessoas" — mascarar isso protegeria ninguém e quebraria o indicador.
    if isinstance(inner, exp.AggFunc) or any(
        isinstance(node, exp.AggFunc) for node in inner.walk()
    ):
        return Projection(output=output, sources=(), aggregated=True)

    sources: List[Tuple[str, str]] = []
    for column in inner.find_all(exp.Column):
        name = (column.name or "").strip('"').lower()
        if not name:
            continue
        qualifier = (column.table or "").strip('"').lower()
        if qualifier:
            table = alias_map.get(qualifier, qualifier)
            candidates = [table]
        else:
            # Sem qualificador: uma tabela só é certeza; várias é dúvida, e
            # dúvida fecha (todas entram como candidatas).
            candidates = list(tables) if len(tables) != 1 else [tables[0]]
        for table in candidates:
            pair = (table, name)
            if pair not in sources:
                sources.append(pair)
    return Projection(output=output, sources=tuple(sources), aggregated=False)


def source_tables(sql: str, dialect: str = "trino") -> Sequence[str]:
    """Atalho para quem só precisa saber em que tabelas a consulta bate."""
    return resolve(sql, dialect=dialect).tables

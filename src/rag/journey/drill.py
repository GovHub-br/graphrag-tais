"""Detalhar por município a resposta por UF — no MESMO SQL (módulo puro).

O botão "Ver os municípios da Bahia" do mapa interativo manda ao chat a
pergunta "… por município na Bahia?". Se ela fosse ao motor como pergunta nova,
o retrieval podia escolher OUTRA tabela, e o total dos municípios não bateria
com o valor do estado que a pessoa acabou de tocar (visto em 10/11: "por
município no Maranhão" trouxe o cadastro de beneficiários em vez da ficha).

Aqui o detalhamento reaproveita o SQL que gerou o mapa por UF: a coluna de UF
vira a de município da mesma tabela, entra o filtro do estado e o resto (a
métrica, os filtros, a tabela) fica igual. Sem LLM. Quando não dá (junção,
subconsulta, tabela sem município), devolve None e a pergunta segue para o
motor como qualquer outra.
"""
from __future__ import annotations

import re
from typing import Optional, Sequence, Tuple

# "por município na Bahia", "por municípios em São Paulo", "por cidade do Pará"
_PEDIDO = re.compile(r"\bpor\s+(?:munic[ií]pios?|cidades?)\s+(?:no|na|em|de|do|da|dos|das)\s+([^?.,;]+)",
                     re.I)
# Coluna de município, na ordem de preferência: código IBGE primeiro (casa com
# a malha sem depender de grafia), nome depois.
_COL_CODIGO = re.compile(r"^(cod|codigo|co|cd)_?(ibge|mun|municipio)(_municipio|_mun)?$|^(ibge|geocodigo)$")
_COL_NOME = re.compile(r"^(nome_|no_|nm_|ds_)?munic[ií]pio(_nome)?$")


def requested_uf(question: str) -> Optional[str]:
    """Sigla da UF de "… por município na Bahia?", ou None."""
    from rag.journey import ibge

    achado = _PEDIDO.search(question or "")
    if not achado:
        return None
    uf = ibge.resolve(achado.group(1).strip())
    return uf["sigla"] if uf else None


def municipio_column(columns: Sequence[str]) -> Optional[str]:
    cols = list(columns)
    for padrao in (_COL_CODIGO, _COL_NOME):
        achada = next((c for c in cols if padrao.match(c.lower())), None)
        if achada:
            return achada
    return None


def table_of(sql: str, dialect: str = "postgres") -> Optional[Tuple[str, str]]:
    """(schema, tabela) do SQL, se houver UMA tabela e nenhuma subconsulta."""
    import sqlglot
    from sqlglot import exp

    try:
        arvore = sqlglot.parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001 — SQL que não parseia não se reescreve
        return None
    tabelas = list(arvore.find_all(exp.Table))
    if len(tabelas) != 1 or list(arvore.find_all(exp.Subquery)) or arvore.find(exp.Join):
        return None
    return tabelas[0].db or "", tabelas[0].name


def uf_source_column(sql: str, output: str, dialect: str = "postgres") -> Optional[str]:
    """A coluna da TABELA por trás da coluna de saída `output` (`uf AS estado`
    → `uf`). None se a saída não é uma coluna simples."""
    import sqlglot
    from sqlglot import exp

    try:
        arvore = sqlglot.parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001
        return None
    for projecao in getattr(arvore, "expressions", []):
        if projecao.alias_or_name.lower() != (output or "").lower():
            continue
        alvo = projecao.this if isinstance(projecao, exp.Alias) else projecao
        return alvo.name if isinstance(alvo, exp.Column) else None
    return None


def rewrite(sql: str, uf_column: str, uf_value: str, municipio: str,
            dialect: str = "postgres") -> Optional[str]:
    """O SQL por UF reescrito para município, filtrado na UF.

    `uf_column` é a coluna da tabela que dava o recorte por UF; `uf_value`, o
    valor dela para o estado tocado, como está na base ("BA" ou "Bahia")."""
    import sqlglot
    from sqlglot import exp

    try:
        arvore = sqlglot.parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(arvore, exp.Select):
        return None
    trocou = False
    for coluna in list(arvore.find_all(exp.Column)):
        if coluna.name.lower() != uf_column.lower():
            continue
        # Só na projeção, no GROUP BY e no ORDER BY: um filtro por UF que já
        # existisse no WHERE fica como está (e o novo filtro entra junto).
        if coluna.find_ancestor(exp.Where):
            continue
        coluna.replace(exp.column(municipio))
        trocou = True
    if not trocou:
        return None
    # O apelido de UF ("uf", "estado") num valor de município confundiria quem
    # lê o resultado — e os fatos, que reconhecem o município pelo nome da coluna.
    for apelido in arvore.find_all(exp.Alias):
        if isinstance(apelido.this, exp.Column) and apelido.this.name == municipio:
            apelido.set("alias", exp.to_identifier(municipio))
    filtro = exp.EQ(this=exp.column(uf_column), expression=exp.Literal.string(uf_value))
    arvore = arvore.where(filtro, append=True)
    return arvore.sql(dialect=dialect)

"""Mascaramento das linhas antes de elas existirem para o resto do sistema.

O que este módulo garante: **nenhum valor de coluna sensível sai do executor de
SQL em claro**. Como ele roda dentro do `McpSqlExecutor`, isso vale de uma vez
para a tabela do chat, o gráfico, o digest do `ResultExplainer`, o CSV da
amostra, o `.docx` — e para o próprio LLM, que recebe 3 linhas de exemplo no
`ChartSpecBuilder` e o retorno da tool na autocorreção.

**Pseudonimização, não anonimização.** O default é `hash`: HMAC-SHA256 com um
segredo (`SENSITIVE_HASH_PEPPER`), truncado, com escopo GLOBAL — o mesmo CPF
vira o mesmo código em qualquer tabela. Isso preserva o que o sistema existe
para fazer (contar distintos, cruzar, agrupar) e destrói o que ele não deve
fazer (identificar). Quem quiser impedir até a correlação entre colunas usa
`SENSITIVE_MASK_SCOPE=column`.

⚠️ **Hash sem segredo não protege CPF.** São 10¹¹ valores possíveis: uma tabela
arco-íris completa se constrói em minutos. Sem `SENSITIVE_HASH_PEPPER`, este
módulo **não** gera hash — degrada para `redact` e grita no log. É a única
decisão aqui que não é configurável.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from loguru import logger

from rag.sensitivity.catalog import Catalog
from rag.sensitivity.resolver import Resolution, resolve

#: Valor exibido no lugar do dado quando a estratégia é `redact` (ou quando o
#: hash é impossível por falta de segredo).
REDACTED = "[protegido]"
#: Prefixo do pseudônimo. Marca a célula como mascarada a olho nu — sem ele, um
#: hash hexadecimal passa por código de sistema legítimo.
HASH_PREFIX = "#"
HASH_LENGTH = 10


@dataclass(frozen=True)
class Rule:
    """O que fazer com UMA coluna do resultado."""

    column: str          # o nome como o banco devolveu (chave da linha)
    strategy: str        # "hash" | "redact"
    table: str           # tabela de origem, "" quando não foi possível saber
    source: str          # coluna de origem (antes do alias)
    certain: bool        # a origem foi resolvida no SQL, ou é conservadorismo?


@dataclass(frozen=True)
class Plan:
    """As regras da consulta inteira. Vazio = nada a fazer (caminho comum)."""

    rules: Tuple[Rule, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.rules)

    @property
    def columns(self) -> List[str]:
        return [rule.column for rule in self.rules]


EMPTY_PLAN = Plan()


# ---------------------------------------------------------------------- #
# Decidir
# ---------------------------------------------------------------------- #
def plan(
    sql: str,
    columns: Sequence[str],
    catalog: Catalog,
    dialect: str = "trino",
    resolution: Optional[Resolution] = None,
) -> Plan:
    """Decide, por coluna do resultado, se e como mascarar. Função pura.

    `resolution` existe para reaproveitar um parse que o chamador já fez — o
    executor parseia o mesmo SQL para reordenar colunas, e pagar `sqlglot` duas
    vezes no caminho quente seria desperdício."""
    if not catalog or not columns:
        return EMPTY_PLAN

    if resolution is None:
        resolution = resolve(sql, dialect=dialect)

    # Curto-circuito: a consulta não toca nenhuma tabela do catálogo e não há
    # regra global por nome → nada a decidir, e nem o laço abaixo roda. É o
    # caso da esmagadora maioria das perguntas.
    if (
        resolution.parsed
        and not catalog.by_column
        and not any(catalog.knows_table(t) for t in resolution.tables)
    ):
        return EMPTY_PLAN

    rules: List[Rule] = []
    for column in columns:
        rule = _rule_for(column, resolution, catalog)
        if rule is not None:
            rules.append(rule)
    return Plan(rules=tuple(rules))


def _rule_for(column: str, resolution: Resolution, catalog: Catalog) -> Optional[Rule]:
    projection = resolution.by_output(column)

    # A projeção é uma agregação: o valor é sobre um conjunto, não sobre uma
    # pessoa. `COUNT(cpf)`, `SUM(valor)` passam em claro, de propósito.
    if projection is not None and projection.aggregated:
        return None

    if projection is not None and projection.sources:
        for table, source in projection.sources:
            strategy = catalog.strategy(table, source)
            if strategy:
                return Rule(
                    column=column,
                    strategy=strategy,
                    table=table,
                    source=source,
                    certain=len(projection.sources) == 1,
                )
        return None

    # Aqui não se sabe de onde a coluna veio: `SELECT *`, parse falho, ou
    # projeção que o resolvedor não casou. Fecha em favor da privacidade —
    # o nome de saída é conferido contra TODAS as tabelas da consulta e contra
    # as regras globais por nome.
    strategy = catalog.strategy_anywhere(resolution.tables, column)
    if not strategy:
        return None
    return Rule(
        column=column,
        strategy=strategy,
        table="",
        source=column,
        certain=False,
    )


# ---------------------------------------------------------------------- #
# Aplicar
# ---------------------------------------------------------------------- #
def apply(
    rows: Sequence[Mapping[str, Any]],
    plan_: Plan,
    pepper: str = "",
    scope: str = "global",
) -> List[Dict[str, Any]]:
    """Devolve as linhas com as colunas do plano substituídas.

    Não muta a entrada e não muda a FORMA do resultado: mesmas chaves, mesma
    ordem, mesmo número de linhas. Só o valor troca — o `_order_columns` do
    executor, o CSV e o gráfico continuam vendo o que esperam."""
    if not plan_ or not rows:
        return [dict(row) for row in rows]

    peppered = (pepper or "").encode("utf-8")
    if not peppered:
        # Sem segredo não existe hash defensável (ver docstring do módulo).
        logger.error(
            "[sensibilidade] SENSITIVE_HASH_PEPPER vazio — as colunas {} saem "
            "REDIGIDAS em vez de pseudonimizadas",
            ", ".join(plan_.columns),
        )

    by_column = {rule.column: rule for rule in plan_.rules}
    memo: Dict[Tuple[str, str], str] = {}
    out: List[Dict[str, Any]] = []
    for row in rows:
        masked = dict(row)
        for key, rule in by_column.items():
            if key not in masked:
                continue
            masked[key] = _mask_value(masked[key], rule, peppered, scope, memo)
        out.append(masked)
    return out


def _mask_value(value: Any, rule: Rule, pepper: bytes, scope: str, memo) -> Any:
    # Nulo continua nulo: ausência de dado não é dado pessoal, e inventar um
    # pseudônimo para `None` faria o gráfico e o digest contarem uma categoria
    # que não existe.
    if value is None or value == "":
        return value
    if rule.strategy == "redact" or not pepper:
        return REDACTED

    # O escopo decide se o pseudônimo é comparável entre colunas. `global` (o
    # default) mantém o cruzamento: mesma pessoa, mesmo código em toda parte.
    namespace = "" if scope != "column" else f"{rule.table}.{rule.source}"
    text = str(value).strip()
    key = (namespace, text)
    hit = memo.get(key)
    if hit is None:
        digest = hmac.new(
            pepper, f"{namespace}|{text}".encode("utf-8"), hashlib.sha256
        ).hexdigest()
        hit = HASH_PREFIX + digest[:HASH_LENGTH]
        memo[key] = hit
    return hit


# ---------------------------------------------------------------------- #
# Entrada de produção
# ---------------------------------------------------------------------- #
def mask_rows(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[str],
    sql: str,
    resolution: Optional[Resolution] = None,
    apply_values: bool = True,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """`plan` + `apply` com a configuração do ambiente aplicada.

    Devolve `(linhas, colunas_mascaradas)`. Nunca levanta: um defeito aqui não
    pode derrubar a resposta — mas também não pode vazar em silêncio, então a
    falha REDIGE tudo o que o catálogo conhece e registra o erro.

    `SENSITIVE_MASK_DRY_RUN=true` calcula e loga o plano sem alterar valor
    nenhum: é como se calibra o mapeamento em produção sem risco de estragar
    uma amostra legítima.

    `apply_values=False` devolve só o PLANO (as colunas), sem tocar nas linhas.
    Existe porque no caminho do agente as linhas já foram mascaradas lá atrás,
    no retorno da ferramenta — e mascarar de novo geraria hash de hash, dando
    códigos diferentes para o mesmo valor conforme o caminho percorrido."""
    from rag.settings.configs import settings

    if not settings.SENSITIVE_MASKING_ENABLED or not rows:
        return [dict(row) for row in rows], []

    from rag.sensitivity import catalog as catalog_mod

    try:
        catalog = catalog_mod.load(settings.SENSITIVE_MAP_PATH)
        decided = plan(
            sql,
            columns or list(rows[0].keys()),
            catalog,
            dialect=settings.SQL_DIALECT,
            resolution=resolution,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[sensibilidade] falha ao montar o plano ({}) — redigindo por precaução", exc
        )
        return _panic_redact(rows), []

    if not decided:
        return [dict(row) for row in rows], []

    detail = ", ".join(
        f"{r.column}<-{r.table or '?'}.{r.source}:{r.strategy}"
        f"{'' if r.certain else ' (inferido)'}"
        for r in decided.rules
    )
    if settings.SENSITIVE_MASK_DRY_RUN:
        logger.warning("[sensibilidade] DRY-RUN — mascararia: {}", detail)
        return [dict(row) for row in rows], []

    if not apply_values:
        logger.info("[sensibilidade] plano sem aplicar (linhas já mascaradas): {}", detail)
        return [dict(row) for row in rows], decided.columns

    logger.info("[sensibilidade] mascarando {} coluna(s): {}", len(decided.rules), detail)
    try:
        masked = apply(
            rows,
            decided,
            pepper=settings.SENSITIVE_HASH_PEPPER,
            scope=settings.SENSITIVE_MASK_SCOPE,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[sensibilidade] falha ao aplicar ({}) — redigindo", exc)
        return _panic_redact(rows), decided.columns
    return masked, decided.columns


def _panic_redact(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Último recurso: sem plano confiável, nenhuma linha sai.

    Devolver lista vazia degrada a resposta para só-texto (a entrega inteira é
    tolerante a isso) — que é o comportamento certo quando o sistema não sabe
    dizer o que é seguro mostrar."""
    return []

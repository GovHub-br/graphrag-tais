"""Catálogo de sensibilidade — quais colunas de quais tabelas são dado pessoal.

O mapeamento é **conhecimento de escopo**, como o `SCOPE_GAPS` e o
`GUARDRAIL_EXTRA_PII_TERMS`: muda por base, é revisado por gente de domínio e
não deveria exigir deploy de código. Mas, ao contrário daqueles dois, ele é
grande demais para caber em env — então mora num arquivo versionado
(`config/sensitive_columns.yml`), montado read-only no container e alterado por
PR.

Formato canônico (YAML ou JSON, escolhido pela extensão):

    version: 1
    default_strategy: hash
    tables:
      entidades_fds.fds_cadastro_pj:
        - cpf_socio                      # forma curta: usa a estratégia default
        - {column: email, strategy: redact}
    columns:                             # regra por NOME, vale em qualquer tabela
      - num_cpf

`tables` é a forma preferida: dado sensível é propriedade de uma coluna de uma
tabela, não de um nome solto. `columns` existe para o identificador que aparece
com o mesmo nome em dezenas de tabelas e para o caso em que o resolvedor não
consegue dizer de que tabela veio a coluna (ver `resolver.py`) — é a rede de
segurança, não a regra.

⚠️ **Nome FÍSICO `schema.tabela`**, o mesmo que aparece no SQL gerado — não o
FQN do OpenMetadata (`servico.database.schema.tabela`). O FQN é aceito na
leitura e reduzido aos dois últimos segmentos, porque é o formato em que os
catálogos costumam exportar.

Tudo aqui é função pura sobre um dict já lido do disco: roda no host, sem
Haystack, sem serviço.
"""
from __future__ import annotations

import json
import os
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from loguru import logger

#: Estratégias que o mascarador sabe aplicar (ver `mask.py`).
STRATEGIES = ("hash", "redact")
DEFAULT_STRATEGY = "hash"

#: Chaves de topo que denunciam o formato canônico. Sem nenhuma delas, o
#: documento é tratado como o mapa cru `{"schema.tabela": [colunas]}` — que é
#: como a maioria dos catálogos exporta, e não vale obrigar a reescrever à mão.
_CANONICAL_KEYS = {"tables", "columns", "version", "default_strategy"}


def normalize(text: Any) -> str:
    """Chave de comparação: sem acento, sem espaço nas pontas, minúscula.

    Vale para nome de tabela e de coluna dos dois lados (catálogo e SQL). O
    banco é case-insensitive na prática e o catálogo é escrito por gente."""
    raw = "" if text is None else str(text)
    folded = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    return folded.strip().strip('"').strip("`").lower()


def normalize_table(name: Any) -> str:
    """`schema.tabela` normalizado. FQN do OpenMetadata cai para os 2 últimos
    segmentos (`Cidades.cidades.far.mapa` → `far.mapa`); nome curto sem schema
    fica como está e casa por sufixo em `Catalog.strategy`."""
    parts = [p for p in normalize(name).split(".") if p]
    if not parts:
        return ""
    return ".".join(parts[-2:])


@dataclass(frozen=True)
class Catalog:
    """Índice imutável, montado uma vez e consultado por lookup de dict."""

    by_table: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    by_column: Mapping[str, str] = field(default_factory=dict)
    default_strategy: str = DEFAULT_STRATEGY
    source: str = ""

    def __bool__(self) -> bool:
        return bool(self.by_table or self.by_column)

    @property
    def tables(self) -> frozenset:
        return frozenset(self.by_table)

    def knows_table(self, table: str) -> bool:
        """Aceita o nome curto quando o catálogo guarda `schema.tabela` (e
        vice-versa): o SQL nem sempre qualifica a tabela com o schema."""
        return self._entry(table) is not None

    def _entry(self, table: str) -> Optional[Mapping[str, str]]:
        key = normalize_table(table)
        if not key:
            return None
        entry = self.by_table.get(key)
        if entry is not None:
            return entry
        # `mapa_nacional` procurando por `far.mapa_nacional`, e o inverso.
        if "." not in key:
            for known, columns in self.by_table.items():
                if known.rsplit(".", 1)[-1] == key:
                    return columns
        else:
            return self.by_table.get(key.rsplit(".", 1)[-1])
        return None

    def strategy(self, table: str, column: str) -> str:
        """Estratégia para `(tabela, coluna)`, ou "" se não é sensível.

        A regra da tabela ganha da regra global por nome — ela é mais
        específica e é a que passou por revisão de domínio."""
        col = normalize(column)
        if not col:
            return ""
        entry = self._entry(table) if table else None
        if entry:
            hit = entry.get(col)
            if hit:
                return hit
        return self.by_column.get(col, "")

    def strategy_by_name(self, column: str) -> str:
        """Só a regra global por nome — o degrau de fallback do resolvedor."""
        return self.by_column.get(normalize(column), "")

    def strategy_anywhere(self, tables: Iterable[str], column: str) -> str:
        """A coluna é sensível em ALGUMA das tabelas da consulta?

        É o caminho de `SELECT *` e o de coluna sem qualificador em consulta com
        JOIN: sem saber de qual tabela ela veio, mascarar de menos é vazar e
        mascarar demais é uma amostra feia. Fecha em favor da privacidade."""
        for table in tables:
            hit = self.strategy(table, column)
            if hit:
                return hit
        return self.strategy_by_name(column)


EMPTY = Catalog()


# ---------------------------------------------------------------------- #
# Leitura
# ---------------------------------------------------------------------- #
def parse(document: Any, source: str = "") -> Catalog:
    """Constrói o `Catalog` do documento já desserializado. Função pura.

    Tolera três formatos, porque o mapeamento nasce fora daqui (planilha, PDF
    convertido, export de catálogo) e reescrevê-lo à mão é onde se erra:

    1. canônico  — `{tables: {...}, columns: [...]}`;
    2. mapa cru  — `{"schema.tabela": ["cpf", "email"]}`;
    3. lista     — `[{table: ..., column: ..., strategy: ...}, ...]`.
    """
    default = DEFAULT_STRATEGY
    tables_raw: Any = {}
    columns_raw: Any = []

    if isinstance(document, list):
        tables_raw = _tables_from_records(document)
    elif isinstance(document, dict):
        if _CANONICAL_KEYS & set(document):
            default = _strategy(document.get("default_strategy"), DEFAULT_STRATEGY)
            tables_raw = document.get("tables") or {}
            columns_raw = document.get("columns") or []
        else:
            tables_raw = document
    elif document:
        logger.warning("[sensibilidade] formato não reconhecido em {}", source or "?")

    by_table: Dict[str, Dict[str, str]] = {}
    for table, columns in (tables_raw or {}).items():
        key = normalize_table(table)
        if not key:
            continue
        entry = by_table.setdefault(key, {})
        for name, strategy in _columns(columns, default):
            entry[name] = strategy
        if not entry:
            by_table.pop(key, None)

    by_column = {name: strategy for name, strategy in _columns(columns_raw, default)}

    catalog = Catalog(
        by_table=by_table, by_column=by_column, default_strategy=default, source=source
    )
    logger.info(
        "[sensibilidade] catálogo carregado de {}: {} tabelas, {} colunas, "
        "{} regras globais por nome",
        source or "(memória)",
        len(by_table),
        sum(len(v) for v in by_table.values()),
        len(by_column),
    )
    return catalog


def _strategy(value: Any, default: str) -> str:
    name = normalize(value)
    if not name:
        return default
    if name not in STRATEGIES:
        logger.warning(
            "[sensibilidade] estratégia '{}' desconhecida; usando '{}'", value, default
        )
        return default
    return name


def _columns(raw: Any, default: str) -> List[Tuple[str, str]]:
    """Normaliza a lista de colunas de uma tabela. Aceita string (`cpf`), dict
    (`{column: cpf, strategy: redact}`) e o mapa `{cpf: redact}`."""
    out: List[Tuple[str, str]] = []
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, dict):
        raw = [{"column": k, "strategy": v} for k, v in raw.items()]
    for item in raw or []:
        if isinstance(item, str):
            name, strategy = normalize(item), default
        elif isinstance(item, dict):
            name = normalize(item.get("column") or item.get("name") or item.get("coluna"))
            strategy = _strategy(item.get("strategy") or item.get("estrategia"), default)
        else:
            continue
        if name:
            out.append((name, strategy))
    return out


def _tables_from_records(records: Iterable[Any]) -> Dict[str, List[dict]]:
    """Formato 3: lista de registros achatada, um por coluna sensível."""
    out: Dict[str, List[dict]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        table = record.get("table") or record.get("tabela") or record.get("fqn") or ""
        column = record.get("column") or record.get("coluna") or record.get("name")
        if not column:
            continue
        out.setdefault(normalize_table(table), []).append(
            {"column": column, "strategy": record.get("strategy") or record.get("estrategia")}
        )
    out.pop("", None)
    return out


def read(path: str) -> Catalog:
    """Lê o arquivo do disco. Ausente ou ilegível → catálogo VAZIO + aviso.

    Vazio é o comportamento de hoje (nada mascarado), então um arquivo faltando
    nunca derruba a resposta — mas ele grita no log, porque rodar sem catálogo
    quando se espera mascaramento é exatamente o silêncio que não se quer."""
    if not path or not os.path.exists(path):
        logger.warning(
            "[sensibilidade] catálogo não encontrado em '{}' — nada será mascarado", path
        )
        return EMPTY
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        if path.lower().endswith(".json"):
            document = json.loads(text) if text.strip() else {}
        else:
            import yaml

            document = yaml.safe_load(text) or {}
    except Exception as exc:  # noqa: BLE001 — catálogo quebrado não derruba o RAG
        logger.error("[sensibilidade] falha ao ler '{}': {}", path, exc)
        return EMPTY
    return parse(document, source=path)


# ---------------------------------------------------------------------- #
# Cache de processo
# ---------------------------------------------------------------------- #
_cache: Dict[str, Tuple[float, Catalog]] = {}


def load(path: str, reload_on_change: bool = True) -> Catalog:
    """`read` com cache por caminho, invalidado pelo `mtime`.

    O caminho quente lê o catálogo em toda execução de SQL: a leitura de disco
    fica no boot, e o `stat` (microssegundos) é o preço de poder corrigir o
    mapeamento sem reiniciar o serviço."""
    if not path:
        return EMPTY
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        stamp = -1.0
    cached = _cache.get(path)
    if cached is not None and (not reload_on_change or cached[0] == stamp):
        return cached[1]
    catalog = read(path)
    _cache[path] = (stamp, catalog)
    return catalog


def clear_cache() -> None:
    """Descarta o cache de processo (usado nos testes)."""
    _cache.clear()

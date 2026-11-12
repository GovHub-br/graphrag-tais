"""O que o resultado executado diz — calculado em Python, nunca pelo LLM.

Toda entrega da jornada (a frase da População geral, os cartões do gestor, o
mapa, o pictograma) parte deste resumo. É a mesma regra do `ResultExplainer`
(ADR-0010): o modelo de linguagem não vê as linhas e não faz conta. O número
que aparece na tela foi somado aqui; o LLM, quando entra, só escreve a frase em
volta de um número que já chega pronto.

Decisões que o resumo toma, e por quê:

- **Métrica** = a primeira coluna numérica que não é identificador. Código de
  IBGE, ano e `id` são numéricos e não são medida.
- **Dimensão** = a primeira coluna que não é a métrica. Ela é classificada como
  `uf` (sigla, nome ou código que o IBGE reconhece), `tempo` ou `categoria` — é
  isso que decide se há mapa, série ou ranking. Coluna de **município** (nome ou
  código IBGE que `journey.municipios` reconhece) tem prioridade sobre a de UF:
  `SELECT uf, municipio, SUM(…)` é uma pergunta por município, e vira o mapa
  municipal.
- **Aditiva?** Soma de valor e contagem somam; média, taxa e percentual não. Para
  métrica não aditiva não existe "total": o destaque vira o maior valor.
- **Dinheiro?** Pelo nome da coluna ou da coluna somada no SQL
  (`SUM(valor_contratado) AS total` é dinheiro, mesmo com alias neutro).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rag.journey import ibge, measure, units

_ID = re.compile(r"^(id|cod|codigo|co|cd|nu|num|seq|cnpj|cpf|ibge|geocod)(_|$)|(_id|_cod|_codigo|_ibge)$")
_TEMPORAL = ("ano", "mes", "periodo", "data", "dt_", "trimestre", "competencia",
             "referencia", "date", "time", "semestre")
_NAO_ADITIVA = re.compile(r"(media|avg|mean|percent|pct|perc|taxa|indice|razao|proporc|mediana|share|rate)")
_DINHEIRO = re.compile(
    r"(valor|vlr|vl_|montante|custo|investiment|recurso|repasse|gasto|despesa|orcament|"
    r"preco|pago|empenh|liquidad|desembols|financiament|subsidio|receita|reais|r\$|dotacao)"
)


@dataclass
class Facts:
    columns: List[str]
    rows: List[Dict[str, Any]]
    kinds: Dict[str, str]
    metric: Optional[str] = None
    dimension: Optional[str] = None
    dimension_kind: str = ""          # uf | municipio | tempo | categoria | ""
    money: bool = False
    additive: bool = True
    #: (rótulo, valor) por categoria da dimensão, na ordem do resultado.
    values: List[Tuple[str, float]] = field(default_factory=list)
    headline: Optional[float] = None
    headline_kind: str = ""           # total | valor | ultimo | maior
    headline_label: str = ""
    uf_values: Dict[str, float] = field(default_factory=dict)
    #: Recorte por município: valor por código IBGE (só os que a malha
    #: reconhece) e as UFs que eles cobrem — o mapa desenha essas UFs.
    mun_values: Dict[str, float] = field(default_factory=dict)
    mun_ufs: List[str] = field(default_factory=list)
    #: Rótulo exibido → código IBGE (o ranking mostra o nome; o mapa usa o código).
    mun_label: Dict[str, str] = field(default_factory=dict)
    coverage: Dict[str, float] = field(default_factory=dict)
    truncated: bool = False
    #: Rótulos de região/total que vieram misturados às UFs e ficaram FORA das
    #: contas (somá-los com as UFs conta o mesmo valor duas vezes).
    mixed_levels: List[str] = field(default_factory=list)
    #: Nome de exibição da métrica ("empregos formais na construção civil").
    #: Vem da legenda que a frase da População geral produz; vazio = nome da
    #: coluna humanizado.
    label: str = ""
    #: "" | dinheiro | indice — muda só a forma de mostrar (`render.money_fmt`).
    unit: str = ""
    #: Nota sobre a escala da base ("em milhões de reais"), para a trilha técnica.
    unit_note: str = ""
    #: O apelido da métrica promete o que a coluna somada não é
    #: (`SUM(renda_familiar) AS total_contratado`) — ver `measure`.
    misnamed: Optional[measure.Misnamed] = None

    @property
    def metric_label(self) -> str:
        from rag.journey import fmt

        return self.label or fmt.humanize(self.metric or "")

    # ------------------------------------------------------------------ #
    @property
    def ok(self) -> bool:
        return self.metric is not None and self.headline is not None

    @property
    def n(self) -> int:
        return len(self.values)

    def top(self, k: int = 5, reverse: bool = True) -> List[Tuple[str, float]]:
        return sorted(self.values, key=lambda kv: kv[1], reverse=reverse)[:k]

    def total(self) -> Optional[float]:
        if not self.values or not self.additive:
            return None
        return sum(v for _, v in self.values)

    def share_of_top(self) -> Optional[Tuple[str, float]]:
        """(rótulo, fração) da maior categoria — base do pictograma."""
        total = self.total()
        if not total or total <= 0 or self.n < 2 or self.dimension_kind == "tempo":
            return None
        rotulo, valor = self.top(1)[0]
        return rotulo, valor / total

    def mean(self) -> Optional[float]:
        if not self.values:
            return None
        return sum(v for _, v in self.values) / len(self.values)

    def outliers(self) -> List[Tuple[str, float]]:
        """Pontos fora da curva pela regra do IQR (Tukey), com 6+ valores."""
        if self.n < 6:
            return []
        ordenados = sorted(v for _, v in self.values)
        q1, q3 = _quantile(ordenados, 0.25), _quantile(ordenados, 0.75)
        iqr = q3 - q1
        # IQR zero (metade ou mais dos valores iguais) não é "sem dispersão": as
        # cercas de Tukey colapsam em q1 = q3 e tudo que difere delas destoa.
        baixo, alto = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        return [(r, v) for r, v in self.values if v < baixo or v > alto]

    def region_values(self) -> Dict[str, Dict[str, float]]:
        """{região: {sigla: valor}} — só para dimensão UF."""
        regioes: Dict[str, Dict[str, float]] = {}
        for sigla, valor in self.uf_values.items():
            uf = ibge.by_sigla(sigla)
            if uf:
                regioes.setdefault(uf["regiao"], {})[sigla] = valor
        return regioes

    def per_capita_uf(self) -> Dict[str, float]:
        if not self.additive:
            return {}
        saida = {}
        for sigla, valor in self.uf_values.items():
            uf = ibge.by_sigla(sigla)
            if uf and uf["populacao_2022"]:
                saida[sigla] = valor / uf["populacao_2022"]
        return saida

    def series(self) -> List[Tuple[str, float]]:
        """Pontos da série na ordem do tempo (só para dimensão tempo)."""
        if self.dimension_kind != "tempo":
            return []
        return sorted(self.values, key=lambda kv: _sort_key(kv[0]))


# --------------------------------------------------------------------------- #
def build(rows: Sequence[Dict[str, Any]], columns: Sequence[str] | None = None,
          sql: str = "", kinds: Dict[str, str] | None = None,
          truncated: bool = False, prefer_time: bool = False,
          references: Sequence[Dict[str, Any]] = ()) -> Facts:
    """Resumo do resultado. `prefer_time=True` escolhe a coluna de TEMPO como
    dimensão mesmo havendo uma de texto (ano × UF vira a série do total por ano)
    — é o que a série histórica da trilha de política precisa.

    `references` são as do motor (`answer["references"]`): a descrição de
    coluna que elas trazem do catálogo dá a unidade (`units.resolve`)."""
    rows = list(rows or [])
    columns = list(columns or (rows[0].keys() if rows else []))
    if kinds is None:
        # Fonte da verdade única da tipagem de coluna: a mesma que decide o
        # gráfico da Persona 2 e o digest do ResultExplainer.
        from rag.components.charts.chart_spec import _infer_kinds

        kinds = _infer_kinds(columns, rows)
    facts = Facts(columns=columns, rows=rows, kinds=kinds, truncated=truncated)
    facts.coverage = {c: _coverage(rows, c) for c in columns}
    if not rows:
        return facts

    facts.metric = _pick_metric(columns, kinds, rows)
    if facts.metric is None:
        return facts
    facts.misnamed = measure.misnamed(facts.metric, sql, references)
    facts.additive = _is_additive(facts.metric, sql)
    facts.money = _is_money(facts.metric, sql)
    unidade = units.resolve(facts.metric, _alias_source(facts.metric, sql), references)
    if unidade.kind == units.DINHEIRO:
        facts.money = True
    elif unidade.kind == units.INDICE:
        # Nível de índice não se soma nem é dinheiro, diga o nome o que disser.
        facts.money, facts.additive, facts.unit = False, False, units.INDICE
    municipio = None if prefer_time else _municipio_column(columns, rows, facts.metric, sql)
    facts.dimension = (_pick_time_dimension(columns, kinds, rows, facts.metric) if prefer_time
                       else None) or (municipio and municipio[0]) \
        or _pick_dimension(columns, kinds, facts.metric)
    if municipio and facts.dimension == municipio[0]:
        facts.dimension_kind = "municipio"
        rows = _with_municipio_key(rows, *municipio)
        facts.dimension = "_municipio"
        facts.values = _aggregate(rows, "_municipio", facts.metric, facts.additive)
        _municipio_labels(facts)
    elif facts.dimension:
        facts.dimension_kind = _dimension_kind(facts.dimension, kinds, rows)
        sub = _subperiod(columns, rows, facts.dimension, facts.metric) \
            if facts.dimension_kind == "tempo" else None
        if sub:
            col, formato = sub
            ano = facts.dimension
            rows = [{**r, "_periodo": formato.format(ano=_label(r.get(ano)),
                                                    v=int(as_float(r.get(col)) or 0))}
                    for r in rows]
            facts.dimension = "_periodo"
        facts.values = _aggregate(rows, facts.dimension, facts.metric, facts.additive)
        if facts.dimension_kind == "uf":
            facts.mixed_levels = [r for r, _ in facts.values if fold_label(r) in _NIVEL_AGREGADO]
            if facts.mixed_levels:
                # Com mistura de níveis, "SE" deixa de ser confiável: pode ser
                # Sergipe, a região Sudeste ou a soma dos dois. Fica de fora —
                # um número a menos é melhor que um número errado com nome certo.
                if any(fold_label(r) == "se" for r, _ in facts.values):
                    facts.mixed_levels.append("SE")
                facts.values = [(r, v) for r, v in facts.values if r not in facts.mixed_levels]
            for rotulo, valor in facts.values:
                uf = ibge.resolve(rotulo)
                if uf:
                    facts.uf_values[uf["sigla"]] = facts.uf_values.get(uf["sigla"], 0) + valor
    _headline(facts)
    if unidade.scaled:
        _scale(facts, unidade.factor)
        facts.unit_note = unidade.note()
    return facts


def _scale(facts: Facts, factor: float) -> None:
    """Leva a métrica da escala da base ("em milhões") para a unidade base.
    As linhas cruas ficam como vieram: são o dado da base, para a trilha técnica."""
    facts.values = [(r, v * factor) for r, v in facts.values]
    facts.uf_values = {k: v * factor for k, v in facts.uf_values.items()}
    facts.mun_values = {k: v * factor for k, v in facts.mun_values.items()}
    if facts.headline is not None:
        facts.headline *= factor


# --------------------------------------------------------------------------- #
def as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


# Siglas e nomes de REGIÃO e de total que tabelas "mapa" costumam empilhar junto
# com as UFs (coluna `nivel`). "SE" fica de fora de propósito: é Sergipe E é a
# sigla do Sudeste — quando há mistura, o aviso de `mixed_levels` cobre o caso.
_NIVEL_AGREGADO = {"n", "ne", "co", "s", "norte", "nordeste", "centro oeste", "sudeste", "sul",
                   "br", "brasil", "total", "nacional", "total geral"}


def fold_label(label: str) -> str:
    from rag.journey.text import fold

    return fold(label)


def _coverage(rows, col) -> float:
    if not rows:
        return 0.0
    cheios = sum(1 for r in rows if r.get(col) not in (None, ""))
    return cheios / len(rows)


def _is_temporal_name(col: str) -> bool:
    nome = col.lower()
    return any(nome == t or nome.startswith(t) or f"_{t}" in nome or nome.endswith(t)
               for t in _TEMPORAL)


def _looks_like_year(rows, col) -> bool:
    vals = [as_float(r.get(col)) for r in rows[:50] if r.get(col) is not None]
    vals = [v for v in vals if v is not None]
    return bool(vals) and all(1900 <= v <= 2100 and v == int(v) for v in vals)


def _pick_metric(columns, kinds, rows) -> Optional[str]:
    numericas = [c for c in columns if kinds.get(c) == "numeric" and not _ID.search(c.lower())]
    # `ano`, `mes`, `trimestre` são numéricos e são eixo, não medida. Só viram
    # métrica se não houver outra coluna numérica.
    for col in numericas:
        if not _is_temporal_name(col):
            return col
    for col in numericas:
        if not _looks_like_year(rows, col):
            return col
    return None


# Segunda coluna de tempo que REFINA a primeira (ano + mês = um período só).
_SUBPERIODO = {"mes": (1, 12, "{ano}-{v:02d}"), "trimestre": (1, 4, "{ano}-T{v}"),
               "semestre": (1, 2, "{ano}-S{v}")}


def _subperiod(columns, rows, dim, metric) -> Optional[Tuple[str, str]]:
    """(coluna, formato) quando a dimensão é ANO e outra coluna é mês/trimestre."""
    if not _looks_like_year(rows, dim):
        return None
    for col in columns:
        if col in (dim, metric):
            continue
        for nome, (lo, hi, formato) in _SUBPERIODO.items():
            if col.lower() == nome or col.lower().startswith(nome + "_") or col.lower().endswith("_" + nome):
                vals = [as_float(r.get(col)) for r in rows if r.get(col) is not None]
                if vals and all(v is not None and lo <= v <= hi for v in vals):
                    return col, formato
    return None


def _pick_dimension(columns, kinds, metric) -> Optional[str]:
    resto = [c for c in columns if c != metric]
    # Texto/data primeiro; numérico (ano, código) só se não houver outro rótulo.
    for col in resto:
        if kinds.get(col) in ("text", "datetime"):
            return col
    for col in resto:
        if not _ID.search(col.lower()) or _is_temporal_name(col) or "uf" in col.lower():
            return col
    return resto[0] if resto else None


def _pick_time_dimension(columns, kinds, rows, metric) -> Optional[str]:
    for col in columns:
        if col == metric:
            continue
        if kinds.get(col) == "datetime" or _looks_like_year(rows, col) or (
                _is_temporal_name(col) and col.lower() not in _SUBPERIODO):
            return col
    return None


_MUNICIPIO = re.compile(
    r"^(nome_|no_|nm_|ds_)?munic[ií]pio(_nome|_uf)?$"
    r"|^(cod|codigo|co|cd)_?(ibge|mun|municipio)(_municipio|_mun)?$|^(ibge|geocodigo|cod_ibge_municipio)$")
_UF_FILTRO = re.compile(r"\b(?:\w+\.)?(?:sg_|sigla_)?uf\w*\s*=\s*'([A-Za-z]{2})'", re.I)


def _municipio_column(columns, rows, metric, sql) -> Optional[Tuple[str, Optional[str], str]]:
    """(coluna do município, coluna da UF ou None, UF do filtro do SQL ou "").

    Só vale se 70% dos rótulos resolvem para um município do IBGE — dentro da
    UF da linha, ou da UF do `WHERE uf = 'SP'`, quando houver."""
    from rag.journey import municipios

    candidatas = [c for c in columns if c != metric and _MUNICIPIO.match(c.lower())]
    if not candidatas or not rows:
        return None
    uf_col = next((c for c in columns if c not in candidatas and c != metric
                   and re.search(r"(^|_)(uf|sg_uf|sigla_uf|estado)($|_)", c.lower())), None)
    filtro = _UF_FILTRO.search(sql or "")
    uf_sql = filtro.group(1).upper() if filtro else ""
    for col in candidatas:
        rotulos = [(r.get(col), r.get(uf_col) if uf_col else None) for r in rows
                   if r.get(col) not in (None, "")]
        if not rotulos:
            continue
        ok = sum(1 for v, u in rotulos if municipios.resolve(v, _sigla(u) or uf_sql or None))
        if ok / len(rotulos) >= 0.7:
            return col, uf_col, uf_sql
    return None


def _sigla(valor) -> str:
    uf = ibge.resolve(valor) if valor not in (None, "") else None
    return uf["sigla"] if uf else ""


def _with_municipio_key(rows, col, uf_col, uf_sql):
    """Cada linha ganha `_municipio`: o código IBGE, ou o rótulo cru se não resolve."""
    from rag.journey import municipios

    saida = []
    for r in rows:
        bruto = r.get(col)
        cod = municipios.resolve(bruto, _sigla(r.get(uf_col)) if uf_col else (uf_sql or None))
        saida.append({**r, "_municipio": cod or _label(bruto)})
    return saida


def _municipio_labels(facts: "Facts") -> None:
    """Código → nome para exibir; com mais de uma UF, "Nome (UF)" (há "Bom
    Jesus" em 5 estados). `mun_values` fica com o código, que é a chave da malha."""
    from rag.journey import municipios

    codigos = {k for k, _ in facts.values if municipios.name(k)}
    facts.mun_values = {k: v for k, v in facts.values if k in codigos}
    facts.mun_ufs = sorted({municipios.uf_of(k) for k in codigos})
    varias = len(facts.mun_ufs) > 1
    facts.values = [
        ((f"{municipios.name(k)} ({municipios.uf_of(k)})" if varias else municipios.name(k))
         if k in codigos else k, v)
        for k, v in facts.values]
    facts.mun_label = {(f"{municipios.name(k)} ({municipios.uf_of(k)})" if varias
                        else municipios.name(k)): k for k in codigos}


def _dimension_kind(col, kinds, rows) -> str:
    # Linhas de região/total empilhadas junto (tabela "mapa") não contam contra:
    # elas saem das contas depois, em `mixed_levels`.
    rotulos = [r.get(col) for r in rows if r.get(col) not in (None, "")
               and fold_label(str(r.get(col))) not in _NIVEL_AGREGADO]
    if len(rotulos) >= 2:
        resolvidos = sum(1 for v in rotulos if ibge.resolve(v))
        if resolvidos / len(rotulos) >= 0.7 and not _looks_like_year(rows, col):
            return "uf"
    if kinds.get(col) == "datetime" or _is_temporal_name(col) or _looks_like_year(rows, col):
        return "tempo"
    return "categoria"


def _alias_source(metric: str, sql: str) -> str:
    """Expressão que gera a coluna `metric` no SQL (`SUM(valor_x) AS metric`)."""
    if not sql:
        return ""
    achado = re.search(
        rf"([\w\.\(\)\s\*,'\"]+?)\s+(?:as\s+)?\"?{re.escape(metric)}\"?\s*(?:,|from\b|$)",
        sql, flags=re.IGNORECASE,
    )
    return achado.group(1).lower() if achado else ""


def _is_money(metric: str, sql: str) -> bool:
    alvo = metric.lower() + " " + _alias_source(metric, sql)
    if re.search(r"\bcount\s*\(", alvo):
        return False
    return bool(_DINHEIRO.search(alvo))


def _is_additive(metric: str, sql: str) -> bool:
    origem = _alias_source(metric, sql)
    if re.search(r"\b(avg|percentile_cont|percentile_disc|stddev|variance)\s*\(", origem):
        return False
    if re.search(r"\b(sum|count)\s*\(", origem):
        return True
    return not _NAO_ADITIVA.search(metric.lower())


def _aggregate(rows, dim, metric, additive) -> List[Tuple[str, float]]:
    """Valor por rótulo da dimensão. Rótulo repetido (outra coluna de corte no
    resultado) soma se a métrica é aditiva; senão, fica a média."""
    acumulado: Dict[str, List[float]] = {}
    ordem: List[str] = []
    for row in rows:
        valor = as_float(row.get(metric))
        if valor is None:
            continue
        rotulo = _label(row.get(dim))
        if rotulo not in acumulado:
            acumulado[rotulo] = []
            ordem.append(rotulo)
        acumulado[rotulo].append(valor)
    saida = []
    for rotulo in ordem:
        vals = acumulado[rotulo]
        saida.append((rotulo, sum(vals) if additive else sum(vals) / len(vals)))
    return saida


def _label(value: Any) -> str:
    if value is None or value == "":
        return "(sem valor)"
    texto = str(value)
    return texto[:-2] if texto.endswith(".0") and texto[:-2].isdigit() else texto


def _headline(facts: Facts) -> None:
    metrica = facts.metric
    if len(facts.rows) == 1 or not facts.values:
        valor = as_float(facts.rows[0].get(metrica))
        facts.headline, facts.headline_kind = valor, "valor"
        return
    if facts.dimension_kind == "tempo":
        serie = facts.series()
        facts.headline, facts.headline_label = serie[-1][1], serie[-1][0]
        facts.headline_kind = "ultimo"
        return
    if facts.additive:
        facts.headline, facts.headline_kind = facts.total(), "total"
        return
    rotulo, valor = facts.top(1)[0]
    facts.headline, facts.headline_label, facts.headline_kind = valor, rotulo, "maior"


def _quantile(ordenados: List[float], q: float) -> float:
    pos = (len(ordenados) - 1) * q
    base = int(pos)
    resto = pos - base
    if base + 1 < len(ordenados):
        return ordenados[base] + resto * (ordenados[base + 1] - ordenados[base])
    return ordenados[base]


def _sort_key(label: str):
    numero = as_float(label)
    return (0, numero, label) if numero is not None else (1, 0, label)

"""ChartSpecBuilder — o LLM escolhe o gráfico; o matplotlib desenha (ADR-0013/0010).

A partir da pergunta, da IR e das colunas REAIS do resultado, um LLM emite uma
`chart-spec` estruturada (tipo, eixos, título). A spec é **validada contra as
colunas reais** — se o LLM escolher algo inválido, cai num fallback determinístico
por heurística de tipos. Não desenha nada aqui: só decide o quê plotar.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from haystack import component
from haystack.dataclasses import ChatMessage
from pydantic import BaseModel, ValidationError

from rag.components.generators.openai_generator import OpenAIChatGeneratorFactory
from rag.settings.configs import settings


class ChartSpec(BaseModel):
    chart_type: str = "none"          # bar | line | none
    x: Optional[str] = None           # coluna de categoria / tempo
    y: Optional[str] = None           # coluna de valor (numérica)
    orientation: str = "horizontal"   # horizontal | vertical (para bar)
    title: str = ""
    reason: str = ""


_SYSTEM = """\
Você escolhe o melhor gráfico para um resultado tabular. NÃO desenha — só decide.
Responda em JSON válido:
{"chart_type": "bar|line|none", "x": "<coluna>", "y": "<coluna numérica>",
 "orientation": "horizontal|vertical", "title": "<curto>", "reason": "<1 frase>"}

Regras:
- Use SOMENTE nomes de coluna que existem na lista fornecida.
- "line" para série temporal (x é data/tempo); "bar" para comparar categorias;
  "none" se for um único número, sem categoria, ou se nada fizer sentido.
- y deve ser uma coluna NUMÉRICA; x é a categoria ou o tempo.
- barras: use "horizontal" quando os rótulos de categoria forem longos.\
"""


def _infer_kinds(columns: List[str], rows: List[Dict[str, Any]]) -> Dict[str, str]:
    """Classifica cada coluna como numeric | datetime | text pela amostra."""
    kinds: Dict[str, str] = {}
    for col in columns:
        kind = "text"
        for row in rows[:25]:
            v = row.get(col)
            if v is None or v == "":
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                kind = "numeric"
            elif isinstance(v, (datetime, date)):
                kind = "datetime"
            else:
                s = str(v)
                try:
                    float(s.replace(",", "."))
                    kind = "numeric"
                except ValueError:
                    if any(sep in s for sep in ("-", "/", ":")) and any(c.isdigit() for c in s):
                        kind = "datetime"
                    else:
                        kind = "text"
            break
        kinds[col] = kind
    return kinds


@component
class ChartSpecBuilder:
    @component.output_types(spec=Dict[str, Any])
    def run(
        self,
        query: str = "",
        columns: Optional[List[str]] = None,
        rows: Optional[List[Dict[str, Any]]] = None,
        ir: Optional[Dict[str, Any]] = None,
    ) -> dict:
        columns = columns or []
        rows = rows or []
        kinds = _infer_kinds(columns, rows)

        # Sem dado plotável → nenhum gráfico.
        if len(rows) < 2 or len(columns) < 2:
            return {"spec": ChartSpec(chart_type="none", reason="poucos dados").model_dump()}

        # Caminho determinístico: a forma do resultado quase sempre decide o
        # gráfico sozinha (uma coluna temporal + uma numérica é série; uma
        # categórica + uma numérica é barra). Custava ~5s de LLM — 25% da
        # latência da resposta — para uma escolha que uma regra faz em 0ms.
        # O LLM continua sendo o desempate quando a forma é ambígua.
        spec = self._confident_spec(columns, kinds) if settings.CHART_SPEC_HEURISTIC else None
        if spec is None:
            spec = self._ask_llm(query, columns, kinds, ir or {}, rows)
        spec = self._validate(spec, columns, kinds)
        logger.info("ChartSpecBuilder: {} (x={}, y={})", spec.chart_type, spec.x, spec.y)
        return {"spec": spec.model_dump()}

    # ------------------------------------------------------------------ #
    _TEMPORAL = ("mes", "ano", "periodo", "data", "dt_", "trimestre", "competencia",
                 "referencia", "date", "time")

    @classmethod
    def _confident_spec(cls, columns, kinds) -> Optional[ChartSpec]:
        """Decide o gráfico pela FORMA do resultado, ou None se for ambíguo.

        Distinto do `_heuristic` abaixo, que é o fallback do LLM e sempre devolve
        algo: aqui só respondemos quando há UMA candidata clara a eixo x e uma
        métrica numérica. Ambíguo (várias categóricas, várias temporais, tudo
        numérico) cai no LLM, que continua sendo o desempate.

        `_infer_kinds` classifica como "numeric" | "datetime" | "text"; nomes de
        coluna também contam, porque período costuma vir como texto ('202603').
        """
        numeric = [c for c in columns if kinds.get(c) == "numeric"]
        if not numeric:
            return None

        def temporal(col: str) -> bool:
            return kinds.get(col) == "datetime" or any(
                tok in col.lower() for tok in cls._TEMPORAL
            )

        rest = [c for c in columns if c not in numeric]
        time_cols = [c for c in rest if temporal(c)]
        cat_cols = [c for c in rest if not temporal(c)]

        if len(time_cols) == 1 and not cat_cols:
            return ChartSpec(chart_type="line", x=time_cols[0], y=numeric[0],
                             reason="uma coluna temporal e uma métrica: série")
        if not time_cols and len(cat_cols) == 1:
            return ChartSpec(chart_type="bar", x=cat_cols[0], y=numeric[0],
                             orientation="horizontal",
                             reason="uma coluna categórica e uma métrica: barras")
        return None

    def _ask_llm(self, query, columns, kinds, ir, rows) -> ChartSpec:
        cols_desc = ", ".join(f"{c} ({kinds[c]})" for c in columns)
        sample = rows[:3]
        user = (
            f"Pergunta: {query}\n"
            f"Intenção (IR): {ir.get('intent', '?')}\n"
            f"Colunas: {cols_desc}\n"
            f"Amostra: {json.dumps(sample, ensure_ascii=False, default=str)[:800]}"
        )
        try:
            gen = OpenAIChatGeneratorFactory(json_mode=True, max_tokens=300).build()
            reply = gen.run(messages=[ChatMessage.from_system(_SYSTEM), ChatMessage.from_user(user)])
            text = reply["replies"][0].text
            return ChartSpec.model_validate(json.loads(text))
        except (KeyError, IndexError, json.JSONDecodeError, ValidationError, Exception) as exc:  # noqa: BLE001
            logger.warning("ChartSpecBuilder: LLM falhou ({}); usando heurística", exc)
            return self._heuristic(columns, kinds)

    @staticmethod
    def _heuristic(columns, kinds) -> ChartSpec:
        """Fallback determinístico: 1ª numérica = y; tempo/1ª texto = x."""
        numeric = [c for c in columns if kinds[c] == "numeric"]
        temporal = [c for c in columns if kinds[c] == "datetime"]
        text = [c for c in columns if kinds[c] == "text"]
        if not numeric:
            return ChartSpec(chart_type="none", reason="sem coluna numérica")
        y = numeric[0]
        if temporal:
            return ChartSpec(chart_type="line", x=temporal[0], y=y, title="", reason="série temporal")
        if text:
            return ChartSpec(chart_type="bar", x=text[0], y=y, orientation="horizontal",
                             title="", reason="categorias × valor")
        return ChartSpec(chart_type="none", reason="sem eixo categórico")

    @staticmethod
    def _validate(spec: ChartSpec, columns, kinds) -> ChartSpec:
        if spec.chart_type not in {"bar", "line", "none"}:
            spec.chart_type = "none"
        if spec.chart_type == "none":
            return spec
        # x e y precisam existir; y precisa ser numérica.
        if spec.x not in columns or spec.y not in columns or kinds.get(spec.y) != "numeric":
            return ChartSpecBuilder._heuristic(columns, kinds)
        if spec.orientation not in {"horizontal", "vertical"}:
            spec.orientation = "horizontal"
        return spec

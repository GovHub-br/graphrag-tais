"""ChartRenderer — chart-spec + linhas → PNG embutível (data URI base64).

Despacha a chart-spec (do ChartSpecBuilder) para o renderer certo e devolve a
imagem como `data:image/png;base64,…`, pronta pra embutir no Markdown do
OpenWebUI (ADR-0013). Tolerante a falha: qualquer erro/`none` → sem imagem, e a
entrega segue só com texto.
"""
from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

from loguru import logger
from haystack import component

from rag.settings.configs import settings
from rag.components.charts import renderers


@component
class ChartRenderer:
    def __init__(self, dpi: int | None = None, max_categories: int | None = None):
        self.dpi = dpi or settings.CHART_DPI
        self.max_categories = max_categories or settings.CHART_MAX_CATEGORIES

    @component.output_types(image=str, chart_type=str)
    def run(
        self,
        spec: Dict[str, Any],
        rows: Optional[List[Dict[str, Any]]] = None,
    ) -> dict:
        chart_type = (spec or {}).get("chart_type", "none")
        rows = rows or []
        if chart_type == "none" or not rows:
            return {"image": "", "chart_type": "none"}

        try:
            import pandas as pd
            df = pd.DataFrame(rows)
            x, y = spec.get("x"), spec.get("y")
            if x not in df.columns or y not in df.columns:
                return {"image": "", "chart_type": "none"}

            if chart_type == "bar":
                png = renderers.render_bar(
                    df, x, y, orientation=spec.get("orientation", "horizontal"),
                    title=spec.get("title", ""), max_categories=self.max_categories, dpi=self.dpi,
                )
            elif chart_type == "line":
                png = renderers.render_line(df, x, y, title=spec.get("title", ""), dpi=self.dpi)
            else:
                png = None
        except Exception as exc:  # noqa: BLE001 — gráfico é aditivo, nunca derruba a resposta
            logger.warning("ChartRenderer: falha ao renderizar ({})", exc)
            return {"image": "", "chart_type": "none"}

        if not png:
            return {"image": "", "chart_type": "none"}
        b64 = base64.b64encode(png).decode("ascii")
        logger.info("ChartRenderer: gráfico {} gerado ({} bytes)", chart_type, len(png))
        return {"image": f"data:image/png;base64,{b64}", "chart_type": chart_type}

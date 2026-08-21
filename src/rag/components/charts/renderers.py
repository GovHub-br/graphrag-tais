"""Renderers de gráfico (matplotlib, backend Agg) — desenho determinístico.

Adaptado do padrão de plotagem interno do GovHub (ADR-0010): barra horizontal com
rótulo de valor e série temporal (linha + área), com um helper de estilo
compartilhado (spines off, grid suave, dpi/tight). Recebe um DataFrame já pronto
(x/y) e devolve os BYTES do PNG — quem hospeda/embute é o componente de cima.
"""
from __future__ import annotations

import io
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # headless, sem display
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import font_manager
import pandas as pd

from pathlib import Path

# Paleta GovHub (webui/custom.css e docs/graphrag-tais-overview.html são a
# referência). O gráfico chega ao usuário embutido na resposta do chat e dentro
# do .docx — usar a cor da marca nos dois é o que faz a entrega parecer UMA coisa.
_ACCENT = "#7c3aed"   # roxo GovHub — linha/área da série temporal
_BAR = "#7c3aed"      # roxo GovHub — barras
_INK = "#221f33"
_MUTED = "#5b596f"
_GRID = "#eceaf5"
_BORDER = "#e2ddf1"

# A Inter já vem com o relatório (OFL); registrada aqui, o gráfico do chat e a
# capa do .docx passam a usar a mesma tipografia.
_INTER_DIR = Path(__file__).resolve().parents[1] / "report" / "assets" / "fonts"
_FONT_FAMILY = "sans-serif"
try:
    for _ttf in _INTER_DIR.glob("Inter-*.ttf"):
        font_manager.fontManager.addfont(str(_ttf))
    if any(_INTER_DIR.glob("Inter-*.ttf")):
        _FONT_FAMILY = "Inter"
except Exception:  # noqa: BLE001 — fonte é cosmético, nunca derruba o gráfico
    pass


def _style(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(_BORDER)
    ax.grid(True, color=_GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=_MUTED, labelsize=9, length=0)
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontfamily(_FONT_FAMILY)
        label.set_color(_MUTED)
    if ax.get_title():
        ax.title.set_fontfamily(_FONT_FAMILY)
        ax.title.set_color(_INK)
        ax.title.set_fontweight("semibold")


def _to_png(fig, dpi: int) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    return buf.getvalue()


def render_bar(
    df: pd.DataFrame, x: str, y: str, *, orientation: str = "horizontal",
    title: str = "", max_categories: int = 20, dpi: int = 120,
) -> Optional[bytes]:
    data = df[[x, y]].copy()
    data[y] = pd.to_numeric(data[y], errors="coerce")
    data = data.dropna(subset=[y])
    if data.empty:
        return None
    data[x] = data[x].astype(str)
    # agrega se houver categorias repetidas, ordena e limita
    data = data.groupby(x, as_index=False)[y].sum()
    data = data.sort_values(y, ascending=False).head(max_categories)
    if data.empty:
        return None

    fig, ax = plt.subplots(figsize=(10, max(3, 0.42 * len(data) + 1.5)))
    if orientation == "horizontal":
        d = data.iloc[::-1]  # maior no topo
        bars = ax.barh(d[x], d[y], color=_BAR)
        max_val = d[y].max()
        ax.set_xlim(0, max_val * 1.14)
        for bar, val in zip(bars, d[y]):
            ax.text(bar.get_width() + max_val * 0.02,
                    bar.get_y() + bar.get_height() / 2,
                    _fmt(val), va="center", fontsize=9,
                    color=_INK, fontfamily=_FONT_FAMILY)
        ax.grid(axis="x", color=_GRID, linewidth=0.8)
        ax.grid(axis="y", visible=False)
        _compact_axis(ax.xaxis)
    else:
        bars = ax.bar(data[x], data[y], color=_BAR)
        plt.setp(ax.get_xticklabels(), rotation=35, ha="right")
        ax.grid(axis="y", color=_GRID, linewidth=0.8)
        ax.grid(axis="x", visible=False)
        _compact_axis(ax.yaxis)
    if title:
        ax.set_title(title, fontsize=12, pad=10)
    _style(ax)
    fig.tight_layout()
    return _to_png(fig, dpi)


def render_line(
    df: pd.DataFrame, x: str, y: str, *, title: str = "", dpi: int = 120,
) -> Optional[bytes]:
    data = df[[x, y]].copy()
    data[y] = pd.to_numeric(data[y], errors="coerce")
    data[x] = pd.to_datetime(data[x], errors="coerce")
    data = data.dropna(subset=[x, y]).sort_values(x)
    if data.empty:
        return None

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(data[x], data[y], color=_ACCENT, linewidth=2.2, marker="o",
            markersize=3.5, markerfacecolor="white",
            markeredgecolor=_ACCENT, markeredgewidth=1.3)
    ax.fill_between(data[x], data[y], alpha=0.15, color=_ACCENT)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
    _compact_axis(ax.yaxis)
    if title:
        ax.set_title(title, fontsize=12, pad=10)
    _style(ax)
    fig.tight_layout()
    return _to_png(fig, dpi)


def _compact_axis(axis) -> None:
    """Escala do eixo em '2,0 bi' em vez do offset científico '1e10'.

    Os rótulos de valor já saem formatados por _fmt; deixar o eixo em notação
    científica fazia o mesmo número aparecer de duas formas no mesmo gráfico.
    """
    from matplotlib.ticker import FuncFormatter
    axis.set_major_formatter(FuncFormatter(lambda v, _pos: _fmt(v)))


def _fmt(v: float) -> str:
    """Rótulo de valor compacto (2,3 mi / 45 mil / 128)."""
    v = float(v)
    for div, suf in ((1e9, " bi"), (1e6, " mi"), (1e3, " mil")):
        if abs(v) >= div:
            return f"{v / div:.1f}".replace(".", ",") + suf
    return f"{v:.0f}"

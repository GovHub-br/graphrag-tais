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
import pandas as pd

_ACCENT = "#0d8f80"   # mesma família visual da identidade do projeto
_BAR = "#4E79A7"


def _style(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, alpha=0.25, linewidth=0.6)


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
                    _fmt(val), va="center", fontsize=9)
        ax.grid(axis="x", alpha=0.25)
        ax.grid(axis="y", visible=False)
    else:
        bars = ax.bar(data[x], data[y], color=_BAR)
        plt.setp(ax.get_xticklabels(), rotation=35, ha="right")
        ax.grid(axis="y", alpha=0.25)
        ax.grid(axis="x", visible=False)
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
    if title:
        ax.set_title(title, fontsize=12, pad=10)
    _style(ax)
    fig.tight_layout()
    return _to_png(fig, dpi)


def _fmt(v: float) -> str:
    """Rótulo de valor compacto (2,3 mi / 45 mil / 128)."""
    v = float(v)
    for div, suf in ((1e9, " bi"), (1e6, " mi"), (1e3, " mil")):
        if abs(v) >= div:
            return f"{v / div:.1f}".replace(".", ",") + suf
    return f"{v:.0f}"

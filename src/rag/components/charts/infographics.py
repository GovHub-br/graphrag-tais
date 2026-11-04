"""Gráficos da jornada (ADR-0020) — matplotlib (Agg), PNG embutível.

O board da jornada pede quatro gráficos por trilha. Aqui estão todos os que o
dado disponível sustenta, desenhados com a mesma identidade dos gráficos da
Persona 2 (`renderers.py`: roxo GovHub, Inter, grade suave) — o que muda é o
tamanho da letra, pensado para a tela do celular.

| função                 | pergunta que responde              | trilha            |
|------------------------|------------------------------------|-------------------|
| `highlight`            | Quanto foi?                        | população, história |
| `pictogram`            | Isso é muito ou pouco?             | população, história |
| `uf_map`               | Onde foi? (malha do IBGE)          | população, história |
| `comparison`           | Comparado à média do Brasil?       | população         |
| `kpi_cards`            | Como estamos?                      | gestor            |
| `ranking`              | Quem está melhor e pior?           | gestor            |
| `alerts`               | Tem algo fora da curva?            | gestor            |
| `histogram`            | Como os valores se distribuem?     | técnica           |
| `coverage`             | O que falta no dado?               | técnica           |
| `lineage`              | De onde veio cada coluna?          | técnica           |
| `policy_journey`       | Lei → programa → … → resultado     | política          |
| `regions_stacked`      | Comparação entre regiões           | política          |

Por que PNG e não gráfico interativo (gpt-vis/ECharts, como o board sugere): o
OpenWebUI não renderiza esses blocos sem mudar o frontend, e o mesmo PNG serve
ao chat, ao celular e ao `.docx`. A troca está registrada no ADR-0020.

Toda função devolve os BYTES do PNG, ou None quando não há o que desenhar.
"""
from __future__ import annotations

import json
import math
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.collections import PatchCollection
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Polygon

from rag.components.charts.renderers import _FONT_FAMILY, _to_png

# Paleta: a mesma de `renderers.py` + os tons do tema do OpenWebUI (custom.css).
ROXO = "#7c3aed"
ROXO_ESCURO = "#4c1d95"
ROXO_CLARO = "#ddd3fb"
LILAS = "#f2eefe"
INK = "#221f33"
MUTED = "#5b596f"
FAINT = "#8a889b"
GRID = "#eceaf5"
BORDA = "#e2ddf1"
LARANJA = "#f97316"
VERDE = "#0f9d58"
CINZA = "#e7e5ee"

_MAPA_CORES = LinearSegmentedColormap.from_list("govhub", [LILAS, "#b9a3f6", ROXO, ROXO_ESCURO])

Fmt = Callable[[float], str]

#: Largura de TODA figura, em polegadas. Pensada para o celular: o chat exibe a
#: imagem com ~330 px de largura; com 8" a letra de 11 pt virava 7 px na tela.
#: Com 6,2" e as fontes abaixo, o menor texto fica perto de 10 px no celular e o
#: gráfico continua nítido no desktop (dpi 130 → ~800 px).
_W = 6.2

#: UFs pequenas demais no mapa do Brasil inteiro: a sigla cobriria a vizinha.
_SEM_ROTULO = {"DF", "SE", "AL", "PB", "RN"}


def _fig(w: float, h: float):
    fig = plt.figure(figsize=(w, h))
    fig.patch.set_facecolor("white")
    return fig


def _titulo(fig, titulo: str, subtitulo: str = "", y: float = 0.97) -> None:
    if titulo:
        fig.text(0.04, y, titulo, fontsize=15, fontweight="bold", color=INK,
                 fontfamily=_FONT_FAMILY, va="top")
    if subtitulo:
        fig.text(0.04, y - 0.075 * (6 / fig.get_figheight()), subtitulo, fontsize=11.5,
                 color=MUTED, fontfamily=_FONT_FAMILY, va="top")


def _rodape(fig, texto: str) -> None:
    if texto:
        fig.text(0.04, 0.015, texto, fontsize=9, color=FAINT, fontfamily=_FONT_FAMILY,
                 va="bottom")


def _eixo_limpo(ax) -> None:
    for lado in ("top", "right", "left"):
        ax.spines[lado].set_visible(False)
    ax.spines["bottom"].set_color(BORDA)
    ax.tick_params(colors=MUTED, labelsize=11, length=0)
    for rotulo in ax.get_xticklabels() + ax.get_yticklabels():
        rotulo.set_fontfamily(_FONT_FAMILY)


def _png(fig, dpi: Optional[int]) -> bytes:
    if dpi is None:
        from rag.settings.configs import settings

        dpi = settings.JOURNEY_CHART_DPI
    return _to_png(fig, dpi)


# --------------------------------------------------------------------------- #
# População geral / história
# --------------------------------------------------------------------------- #
def highlight(value: str, caption: str, detail: str = "", *, dpi: Optional[int] = None) -> Optional[bytes]:
    """Número em destaque: o valor grande, o que ele é e a escala humana."""
    if not value:
        return None
    fig = _fig(_W, 3.1 if detail else 2.5)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.add_patch(FancyBboxPatch((0.02, 0.06), 0.96, 0.88, boxstyle="round,pad=0,rounding_size=0.06",
                                transform=ax.transAxes, facecolor=LILAS, edgecolor=BORDA, lw=1.2))
    ax.add_patch(FancyBboxPatch((0.05, 0.22), 0.009, 0.56, boxstyle="square,pad=0",
                                transform=ax.transAxes, facecolor=ROXO, edgecolor="none"))
    ax.text(0.09, 0.70 if detail else 0.64, value, fontsize=40, fontweight="bold", color=ROXO,
            fontfamily=_FONT_FAMILY, va="center", transform=ax.transAxes)
    ax.text(0.09, 0.42 if detail else 0.28, _quebra(caption, 44), fontsize=14, color=INK,
            fontfamily=_FONT_FAMILY, va="center", transform=ax.transAxes, wrap=True)
    if detail:
        ax.text(0.09, 0.20, detail, fontsize=13, color=MUTED, fontfamily=_FONT_FAMILY,
                va="center", transform=ax.transAxes, style="italic")
    return _png(fig, dpi)


def _pessoa(ax, x: float, y: float, cor: str, escala: float = 1.0) -> None:
    ax.add_patch(Circle((x, y + 0.62 * escala), 0.17 * escala, color=cor, lw=0))
    ax.add_patch(FancyBboxPatch((x - 0.26 * escala, y - 0.05 * escala), 0.52 * escala,
                                0.5 * escala, boxstyle="round,pad=0,rounding_size=0.2",
                                color=cor, lw=0))


def pictogram(filled: int, caption: str, *, total: int = 10, title: str = "",
              dpi: Optional[int] = None) -> Optional[bytes]:
    """Pictograma "N em cada 10": isso é muito ou pouco?"""
    if total <= 0:
        return None
    filled = max(0, min(total, int(filled)))
    fig = _fig(_W, 2.5)
    _titulo(fig, title)
    ax = fig.add_axes([0.04, 0.28, 0.92, 0.42])
    ax.set_xlim(-0.5, total - 0.5)
    ax.set_ylim(-0.2, 1.0)
    ax.set_aspect("equal")
    ax.set_axis_off()
    for i in range(total):
        _pessoa(ax, i, 0.05, ROXO if i < filled else CINZA, escala=0.95)
    fig.text(0.04, 0.07, caption, fontsize=13, color=INK, fontfamily=_FONT_FAMILY, wrap=True)
    return _png(fig, dpi)


def _poligonos(geometria: dict) -> List[List[Tuple[float, float]]]:
    if geometria["type"] == "Polygon":
        return [geometria["coordinates"][0]]
    if geometria["type"] == "MultiPolygon":
        return [poly[0] for poly in geometria["coordinates"]]
    return []


def uf_map(values: Dict[str, float], fmt: Fmt, *, title: str = "", subtitle: str = "",
           source: str = "Malha: IBGE", dpi: Optional[int] = None) -> Optional[bytes]:
    """Mapa coroplético por UF, desenhado sobre a malha oficial do IBGE."""
    from rag.journey import ibge

    if not values:
        return None
    with open(ibge.geojson_path(), encoding="utf-8") as fh:
        malha = json.load(fh)
    codigo_para_sigla = {str(u["codigo"]): u["sigla"] for u in ibge.ufs()}
    minimo, maximo = min(values.values()), max(values.values())
    norma = Normalize(vmin=minimo, vmax=maximo if maximo > minimo else minimo + 1)

    fig = _fig(_W, 7.2)
    _titulo(fig, title, subtitle)
    ax = fig.add_axes([0.02, 0.11, 0.96, 0.76])
    ax.set_aspect("equal")
    ax.set_axis_off()
    patches, cores, centros = [], [], {}
    for feature in malha["features"]:
        sigla = codigo_para_sigla.get(str(feature["properties"].get("codarea")))
        cor = _MAPA_CORES(norma(values[sigla])) if sigla in values else CINZA
        maior = None
        for anel in _poligonos(feature["geometry"]):
            patches.append(Polygon(anel, closed=True))
            cores.append(cor)
            if maior is None or len(anel) > len(maior):
                maior = anel
        if sigla and maior:
            xs, ys = zip(*maior)
            centros[sigla] = (sum(xs) / len(xs), sum(ys) / len(ys))
    ax.add_collection(PatchCollection(patches, facecolor=cores, edgecolor="white", linewidth=0.8))
    ax.autoscale_view()
    # Sigla em toda UF; em branco sobre os tons escuros, para ler no celular.
    for sigla, (cx, cy) in centros.items():
        if sigla in _SEM_ROTULO:
            continue
        escuro = sigla in values and norma(values[sigla]) > 0.55
        ax.text(cx, cy, sigla, ha="center", va="center", fontsize=8.5,
                color="white" if escuro else INK, fontfamily=_FONT_FAMILY, fontweight="bold")
    # Legenda: barra de cor com mínimo e máximo já formatados.
    cax = fig.add_axes([0.2, 0.075, 0.6, 0.022])
    gradiente = [[i / 255 for i in range(256)]]
    cax.imshow(gradiente, aspect="auto", cmap=_MAPA_CORES)
    cax.set_axis_off()
    fig.text(0.2, 0.05, fmt(minimo), fontsize=10, color=MUTED, ha="left", va="top",
             fontfamily=_FONT_FAMILY)
    fig.text(0.8, 0.05, fmt(maximo), fontsize=10, color=MUTED, ha="right", va="top",
             fontfamily=_FONT_FAMILY)
    if len(values) < 27:
        fig.text(0.5, 0.05, "cinza = sem dado", fontsize=9.5, color=FAINT, ha="center",
                 va="top", fontfamily=_FONT_FAMILY)
    _rodape(fig, source)
    return _png(fig, dpi)


def comparison(items: Sequence[Tuple[str, float]], reference: float, reference_label: str,
               fmt: Fmt, *, title: str = "", subtitle: str = "", max_items: int = 10,
               dpi: Optional[int] = None) -> Optional[bytes]:
    """Barras com a linha da média: quem está acima e quem está abaixo."""
    dados = sorted(items, key=lambda kv: kv[1], reverse=True)[:max_items]
    if len(dados) < 2:
        return None
    dados = dados[::-1]
    altura = max(3.4, 0.46 * len(dados) + 1.9)
    fig = _fig(_W, altura)
    _titulo(fig, title, subtitle)
    ax = fig.add_axes([0.26, 0.1, 0.62, 1 - 1.55 / altura])
    rotulos = [_curto(r) for r, _ in dados]
    valores = [v for _, v in dados]
    cores = [ROXO if v >= reference else ROXO_CLARO for v in valores]
    barras = ax.barh(rotulos, valores, color=cores, height=0.66)
    topo = max(valores + [reference])
    for barra, valor in zip(barras, valores):
        ax.text(barra.get_width() + topo * 0.015, barra.get_y() + barra.get_height() / 2,
                fmt(valor), va="center", fontsize=10.5, color=INK, fontfamily=_FONT_FAMILY)
    ax.axvline(reference, color=LARANJA, lw=2, ls=(0, (4, 3)))
    ax.text(reference, len(dados) - 0.35, f" {reference_label}: {fmt(reference)}", color=LARANJA,
            fontsize=10.5, fontfamily=_FONT_FAMILY, va="bottom", fontweight="bold")
    ax.set_xlim(0, topo * 1.22)
    ax.set_xticks([])
    _eixo_limpo(ax)
    return _png(fig, dpi)


# --------------------------------------------------------------------------- #
# Gestor
# --------------------------------------------------------------------------- #
def kpi_cards(kpis: Sequence[Tuple[str, str, str]], *, title: str = "",
              dpi: Optional[int] = None) -> Optional[bytes]:
    """Cartões de KPI: (valor, rótulo, detalhe). Até 4, em grade 2×2."""
    kpis = list(kpis)[:4]
    if not kpis:
        return None
    linhas = math.ceil(len(kpis) / 2)
    altura = 0.9 + 1.75 * linhas
    fig = _fig(_W, altura)
    _titulo(fig, title)
    topo = 1 - 0.75 / altura
    alto_card = (topo - 0.04) / linhas
    for i, (valor, rotulo, detalhe) in enumerate(kpis):
        col, lin = i % 2, i // 2
        x0 = 0.03 + col * 0.485
        y0 = topo - (lin + 1) * alto_card + 0.02
        ax = fig.add_axes([x0, y0, 0.455, alto_card - 0.04])
        ax.set_axis_off()
        ax.add_patch(FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0,rounding_size=0.08",
                                    transform=ax.transAxes, facecolor=LILAS,
                                    edgecolor=BORDA, lw=1.2))
        ax.text(0.07, 0.66, valor, fontsize=24, fontweight="bold", color=ROXO,
                fontfamily=_FONT_FAMILY, transform=ax.transAxes, va="center")
        ax.text(0.07, 0.36, _quebra(rotulo, 24), fontsize=11.5, color=INK, fontfamily=_FONT_FAMILY,
                transform=ax.transAxes, va="center", fontweight="semibold")
        if detalhe:
            ax.text(0.07, 0.15, _curto(detalhe, 30), fontsize=10, color=MUTED, fontfamily=_FONT_FAMILY,
                    transform=ax.transAxes, va="center")
    return _png(fig, dpi)


def ranking(items: Sequence[Tuple[str, float]], fmt: Fmt, *, title: str = "",
            subtitle: str = "", highlight_n: int = 3, max_items: int = 27,
            dpi: Optional[int] = None) -> Optional[bytes]:
    """Ranking completo: os primeiros em roxo, os últimos em laranja."""
    dados = sorted(items, key=lambda kv: kv[1], reverse=True)[:max_items]
    if len(dados) < 2:
        return None
    n = len(dados)
    altura = max(3.6, 0.44 * n + 1.9)
    fig = _fig(_W, altura)
    _titulo(fig, title, subtitle)
    ax = fig.add_axes([0.24, 0.06, 0.64, 1 - 1.5 / altura])
    marca = min(highlight_n, n // 2)
    cores = []
    for i in range(n):
        if i < marca:
            cores.append(ROXO)
        elif i >= n - marca:
            cores.append(LARANJA)
        else:
            cores.append(ROXO_CLARO)
    ordem = dados[::-1]
    cores = cores[::-1]
    barras = ax.barh([f"{n - i}º  {_curto(r)}" for i, (r, _) in enumerate(ordem)],
                     [v for _, v in ordem], color=cores, height=0.7)
    topo = max(v for _, v in dados) or 1
    for barra, (_, valor) in zip(barras, ordem):
        ax.text(barra.get_width() + topo * 0.015, barra.get_y() + barra.get_height() / 2,
                fmt(valor), va="center", fontsize=12.5, color=INK, fontfamily=_FONT_FAMILY)
    ax.set_xlim(0, topo * 1.25)
    ax.set_xticks([])
    _eixo_limpo(ax)
    ax.tick_params(labelsize=13)
    return _png(fig, dpi)


def alerts(items: Sequence[Tuple[str, float]], flagged: Sequence[str], fmt: Fmt, *,
           temporal: bool = False, title: str = "", subtitle: str = "",
           dpi: Optional[int] = None) -> Optional[bytes]:
    """Tendência com alerta (série) ou pontos fora da curva (categorias)."""
    dados = list(items)
    if len(dados) < 3:
        return None
    marcados = set(flagged)
    fig = _fig(_W, 4.6)
    _titulo(fig, title, subtitle)
    ax = fig.add_axes([0.1, 0.16, 0.85, 0.6])
    if temporal:
        xs = list(range(len(dados)))
        ys = [v for _, v in dados]
        # Escala no intervalo dos dados, não a partir de zero: um estoque de
        # 3 milhões que oscila 10% vira uma reta com o eixo em zero.
        lo, hi = min(ys), max(ys)
        folga = (hi - lo) * 0.15 or abs(hi) * 0.05 or 1
        ax.set_ylim(lo - folga, hi + folga)
        ax.plot(xs, ys, color=ROXO, lw=2.6, marker="o", ms=4.5, mfc="white", mec=ROXO)
        ax.fill_between(xs, ys, lo - folga, color=ROXO, alpha=0.1)
        passo = max(1, math.ceil(len(dados) / 5))
        ax.set_xticks(xs[::passo])
        ax.set_xticklabels([_periodo(r) for r, _ in dados][::passo], rotation=0)
    else:
        dados = sorted(dados, key=lambda kv: kv[1], reverse=True)
        xs = list(range(len(dados)))
        ys = [v for _, v in dados]
        ax.scatter(xs, ys, s=60, color=[LARANJA if r in marcados else ROXO_CLARO for r, _ in dados],
                   zorder=3, edgecolor="white", linewidth=0.8)
        ax.set_xticks([])
    for x, (rotulo, valor) in zip(xs, dados):
        if rotulo in marcados:
            ax.scatter([x], [valor], s=140, facecolor="none", edgecolor=LARANJA, lw=2.2, zorder=4)
            ax.annotate(f"{_curto(_periodo(rotulo), 14)}\n{fmt(valor)}", (x, valor), textcoords="offset points",
                        xytext=(8, 6), fontsize=10, color=LARANJA, fontfamily=_FONT_FAMILY,
                        fontweight="bold")
    # Poucas marcas: o formato compacto tem uma casa ("2,9 mi"), e com marcas
    # demais num intervalo estreito o eixo repetia "3 mi, 3 mi, 3 mi".
    ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=4))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _p: fmt(v)))
    ax.grid(axis="y", color=GRID)
    ax.set_axisbelow(True)
    _eixo_limpo(ax)
    return _png(fig, dpi)


# --------------------------------------------------------------------------- #
# Técnica
# --------------------------------------------------------------------------- #
def histogram(values: Sequence[float], fmt: Fmt, *, title: str = "", subtitle: str = "",
              dpi: Optional[int] = None) -> Optional[bytes]:
    vals = [v for v in values if v is not None]
    if len(vals) < 8:
        return None
    caixas = min(20, max(5, int(math.log2(len(vals)) + 1)))
    fig = _fig(_W, 4.2)
    _titulo(fig, title, subtitle)
    ax = fig.add_axes([0.1, 0.17, 0.85, 0.58])
    ax.hist(vals, bins=caixas, color=ROXO, edgecolor="white", linewidth=1.2)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _p: fmt(v)))
    ax.set_ylabel("quantidade", color=MUTED, fontsize=11, fontfamily=_FONT_FAMILY)
    ax.grid(axis="y", color=GRID)
    ax.set_axisbelow(True)
    _eixo_limpo(ax)
    return _png(fig, dpi)


def coverage(fractions: Dict[str, float], *, title: str = "", subtitle: str = "",
             dpi: Optional[int] = None) -> Optional[bytes]:
    """% preenchido por coluna do resultado."""
    if not fractions:
        return None
    itens = list(fractions.items())[:12][::-1]
    altura = max(2.6, 0.45 * len(itens) + 1.4)
    fig = _fig(_W, altura)
    _titulo(fig, title, subtitle)
    ax = fig.add_axes([0.32, 0.06, 0.58, 1 - (1.35 if subtitle else 1.0) / altura])
    nomes = [_curto(n, 22) for n, _ in itens]
    ax.barh(nomes, [1] * len(itens), color=CINZA, height=0.6)
    ax.barh(nomes, [f for _, f in itens], color=[ROXO if f >= 0.95 else LARANJA for _, f in itens],
            height=0.6)
    for i, (_, f) in enumerate(itens):
        ax.text(1.02, i, f"{f * 100:.0f}%".replace(".", ","), va="center", fontsize=10.5,
                color=INK, fontfamily=_FONT_FAMILY)
    ax.set_xlim(0, 1.15)
    ax.set_xticks([])
    _eixo_limpo(ax)
    return _png(fig, dpi)


def lineage(steps: Sequence[Tuple[str, Sequence[str]]], *, title: str = "",
            dpi: Optional[int] = None) -> Optional[bytes]:
    """Jornada do dado em caixas empilhadas: fonte → tabela → colunas → cálculo.

    Vertical de propósito: no celular, quatro caixas lado a lado viram texto
    ilegível."""
    passos = [(t, list(linhas)[:4]) for t, linhas in steps if t]
    if not passos:
        return None
    alturas = [0.55 + 0.32 * max(1, len(l)) for _, l in passos]
    total = sum(alturas) + 0.45 * (len(passos) - 1) + 0.9
    fig = _fig(_W, total)
    _titulo(fig, title, y=1 - 0.12 / total)
    ax = fig.add_axes([0, 0, 1, 1 - 0.75 / total])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, total - 0.75)
    ax.set_axis_off()
    y = total - 0.75
    for i, ((titulo, linhas), h) in enumerate(zip(passos, alturas)):
        y -= h
        ax.add_patch(FancyBboxPatch((0.05, y), 0.9, h, boxstyle="round,pad=0,rounding_size=0.12",
                                    facecolor=LILAS if i < len(passos) - 1 else ROXO,
                                    edgecolor=BORDA, lw=1.2))
        cor_titulo = ROXO if i < len(passos) - 1 else "white"
        cor_texto = INK if i < len(passos) - 1 else "white"
        ax.text(0.09, y + h - 0.3, f"{i + 1}. {titulo}", fontsize=13, fontweight="bold",
                color=cor_titulo, fontfamily=_FONT_FAMILY, va="center")
        for j, linha in enumerate(linhas):
            ax.text(0.09, y + h - 0.62 - 0.3 * j, _curto(linha, 70), fontsize=10.5,
                    color=cor_texto, fontfamily="monospace" if "`" not in linha else _FONT_FAMILY,
                    va="center")
        if i < len(passos) - 1:
            ax.add_patch(FancyArrowPatch((0.5, y - 0.04), (0.5, y - 0.41), arrowstyle="-|>",
                                         mutation_scale=18, color=ROXO, lw=2))
            y -= 0.45
    return _png(fig, dpi)


# --------------------------------------------------------------------------- #
# Especialista em políticas
# --------------------------------------------------------------------------- #
_ESTADO_COR = {"usado": ROXO, "catalogo": "#b9a3f6", "lacuna": "white"}


def policy_journey(steps: Sequence[Tuple[str, str, str]], *, title: str = "",
                   dpi: Optional[int] = None) -> Optional[bytes]:
    """Lei → programa → orçamento → execução → resultado, com o que tem dado.

    steps: (etapa, estado, detalhe), estado ∈ usado | catalogo | lacuna."""
    passos = list(steps)
    if not passos:
        return None
    fig = _fig(_W, 3.6)
    _titulo(fig, title)
    ax = fig.add_axes([0.02, 0.12, 0.96, 0.62])
    n = len(passos)
    ax.set_xlim(-0.6, n - 0.4)
    ax.set_ylim(-1.25, 0.75)
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_axis_off()
    ax.plot([0, n - 1], [0.2, 0.2], color=BORDA, lw=3, zorder=1)
    for i, (etapa, estado, detalhe) in enumerate(passos):
        cor = _ESTADO_COR.get(estado, "white")
        ax.add_patch(Circle((i, 0.2), 0.3, facecolor=cor, edgecolor=ROXO if estado != "lacuna" else FAINT,
                            lw=2, ls="-" if estado != "lacuna" else (0, (3, 2)), zorder=2,
                            clip_on=False))
        simbolo = {"usado": "✓", "catalogo": "•", "lacuna": "?"}.get(estado, "")
        ax.text(i, 0.2, simbolo, ha="center", va="center", fontsize=14, fontweight="bold",
                color="white" if estado == "usado" else (ROXO if estado == "catalogo" else FAINT),
                zorder=3)
        ax.text(i, -0.32, etapa, ha="center", va="center", fontsize=11.5, fontweight="bold",
                color=INK, fontfamily=_FONT_FAMILY)
        ax.text(i, -0.72, _curto(detalhe, 16), ha="center", va="center", fontsize=10,
                color=MUTED, fontfamily=_FONT_FAMILY)
    legenda = "✓ usado nesta resposta    • existe no catálogo    ? lacuna"
    fig.text(0.04, 0.05, legenda, fontsize=10, color=MUTED, fontfamily=_FONT_FAMILY)
    return _png(fig, dpi)


def regions_stacked(regions: Dict[str, Dict[str, float]], fmt: Fmt, *, title: str = "",
                    subtitle: str = "", dpi: Optional[int] = None) -> Optional[bytes]:
    """Barras empilhadas: total de cada região, segmentado pelas suas UFs."""
    if not regions:
        return None
    ordem = sorted(regions.items(), key=lambda kv: sum(kv[1].values()))
    altura = max(3.4, 0.7 * len(ordem) + 1.6)
    fig = _fig(_W, altura)
    _titulo(fig, title, subtitle)
    ax = fig.add_axes([0.2, 0.06, 0.68, 1 - 1.35 / altura])
    topo = max(sum(v.values()) for _, v in ordem) or 1
    tons = [ROXO_ESCURO, ROXO, "#9d7af0", "#b9a3f6", ROXO_CLARO, LILAS]
    for y, (regiao, ufs_) in enumerate(ordem):
        esquerda = 0.0
        for k, (sigla, valor) in enumerate(sorted(ufs_.items(), key=lambda kv: -kv[1])):
            cor = tons[min(k, len(tons) - 1)]
            ax.barh(y, valor, left=esquerda, color=cor, edgecolor="white", height=0.62)
            if valor / topo > 0.06:
                ax.text(esquerda + valor / 2, y, sigla, ha="center", va="center", fontsize=9.5,
                        color="white" if k < 3 else INK, fontfamily=_FONT_FAMILY, fontweight="bold")
            esquerda += valor
        ax.text(esquerda + topo * 0.015, y, fmt(esquerda), va="center", fontsize=10.5,
                color=INK, fontfamily=_FONT_FAMILY, fontweight="bold")
    ax.set_yticks(range(len(ordem)))
    ax.set_yticklabels([r for r, _ in ordem])
    ax.set_xlim(0, topo * 1.2)
    ax.set_xticks([])
    _eixo_limpo(ax)
    return _png(fig, dpi)


def _periodo(rotulo: str) -> str:
    from rag.journey.fmt import period

    return period(rotulo)


def _quebra(texto: str, largura: int) -> str:
    """Quebra em fronteira de palavra (o `wrap=True` do matplotlib ignora a
    borda do cartão e deixa o texto encostar nela)."""
    import textwrap

    return "\n".join(textwrap.wrap(texto, largura)[:2])


def _curto(texto: str, n: int = 24) -> str:
    texto = str(texto)
    return texto if len(texto) <= n else texto[: n - 1] + "…"

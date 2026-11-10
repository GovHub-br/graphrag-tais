"""Gráfico interativo da jornada: HTML autocontido com o valor no cursor (módulo puro).

O PNG da resposta não deixa a pessoa ler o valor de cada UF ou barra (pedido de
produto, 10/11/2026). O Open WebUI 0.11 mostra HTML com script dentro da
mensagem ("embeds", iframe com `allow-scripts`), e uma Function de filtro dele
(`webui/functions/grafico_interativo.py`) põe este HTML embaixo da resposta.

Uma página por resultado, três formas, escolhidas pelo recorte — as mesmas do
PNG (`render.where_chart`):

- UF     → mapa do Brasil (malha do IBGE) + aba de ranking;
- tempo  → linha, com o ponto mais perto do cursor destacado;
- resto  → barras ordenadas.

O valor que aparece é o EXATO (`render.money_fmt`), com a fatia do total quando
somar faz sentido. Nada de biblioteca externa: SVG + ~80 linhas de JS, para
funcionar no iframe sem rede, sem CDN e sem `allow-same-origin`. A altura vai
para o Open WebUI por `postMessage({type: "iframe:height"})`, que é como ele
dimensiona iframe de outra origem.

O PNG continua na resposta: é o que vai para o `.docx`, para a API e para quem
não tem a Function instalada.
"""
from __future__ import annotations

import html
import json
import math
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rag.journey import fmt, render
from rag.journey.facts import Facts

ONDE = "onde"          # resposta e "Contar a história": mapa primeiro
RANKING = "ranking"    # "Números para decisão": ranking primeiro
KINDS = (ONDE, RANKING)

_MAPA_W = 560
# Mesma rampa do mapa PNG (`infographics._MAPA_CORES`): lilás → roxo → roxo escuro.
_RAMPA = ((0.0, (0xF2, 0xEE, 0xFE)), (0.33, (0xB9, 0xA3, 0xF6)),
          (0.66, (0x7C, 0x3A, 0xED)), (1.0, (0x4C, 0x1D, 0x95)))
_SEM_DADO = "#e7e5ee"
_SEM_DADO_MUNICIPAL = "#f3f2f7"


def color(t: float) -> str:
    t = min(1.0, max(0.0, t))
    for (a, ca), (b, cb) in zip(_RAMPA, _RAMPA[1:]):
        if t <= b:
            u = (t - a) / (b - a) if b > a else 0
            return "#%02x%02x%02x" % tuple(round(ca[i] + (cb[i] - ca[i]) * u) for i in range(3))
    return "#4c1d95"


def _project(aneis: Dict[str, List[List[Tuple[float, float]]]]) -> Tuple[Dict[str, str], int]:
    """Caminho SVG de cada chave e a altura do desenho. Projeção lon·cos(lat)
    × lat, com a latitude média do próprio desenho: num coroplético de tela a
    distorção não aparece, e o estado do Sul não sai achatado."""
    pts = [p for lista in aneis.values() for anel in lista for p in anel]
    if not pts:
        return {}, 0
    k = math.cos(math.radians(sum(p[1] for p in pts) / len(pts)))
    x0, x1 = min(p[0] for p in pts) * k, max(p[0] for p in pts) * k
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    escala = (_MAPA_W - 20) / max(x1 - x0, 1e-9)
    altura = round((y1 - y0) * escala + 20)
    caminhos = {}
    for chave, lista in aneis.items():
        if chave:
            caminhos[chave] = "".join(
                "M" + "L".join(f"{10 + (x * k - x0) * escala:.1f},{10 + (y1 - y) * escala:.1f}"
                               for x, y in anel) + "Z"
                for anel in lista)
    return caminhos, altura


@lru_cache(maxsize=1)
def _map_paths() -> Tuple[Dict[str, str], int]:
    """As 27 UFs (malha do IBGE)."""
    from rag.journey import ibge

    with open(ibge.geojson_path(), encoding="utf-8") as fh:
        malha = json.load(fh)
    sigla_de = {str(u["codigo"]): u["sigla"] for u in ibge.ufs()}
    aneis: Dict[str, List[List[Tuple[float, float]]]] = {}
    for feature in malha["features"]:
        sigla = sigla_de.get(str(feature["properties"].get("codarea")))
        geo = feature["geometry"]
        polys = geo["coordinates"] if geo["type"] == "MultiPolygon" else [geo["coordinates"]]
        aneis.setdefault(sigla or "", []).extend([(p[0], p[1]) for p in poly[0]] for poly in polys)
    return _project(aneis)


@lru_cache(maxsize=32)
def _municipal_paths(ufs: Tuple[str, ...]) -> Tuple[Dict[str, str], int]:
    """Os municípios das UFs do resultado (código IBGE → caminho)."""
    from rag.journey import municipios

    aneis: Dict[str, List[List[Tuple[float, float]]]] = {}
    for uf in ufs:
        aneis.update(municipios.geometry(uf))
    return _project(aneis)


# --------------------------------------------------------------------------- #
# Dados de cada item: o que o tooltip mostra
# --------------------------------------------------------------------------- #
def _items(facts: Facts) -> List[Dict[str, Any]]:
    f = render.money_fmt(facts)
    tempo = facts.dimension_kind == "tempo"
    if tempo:
        base = facts.series()
    elif facts.dimension_kind == "uf" and facts.uf_values:
        base = sorted(facts.uf_values.items(), key=lambda kv: kv[1], reverse=True)
    else:
        base = sorted(facts.values, key=lambda kv: kv[1], reverse=True)
    total = sum(v for _, v in base) if (facts.additive and not tempo) else 0
    itens = []
    for rotulo_bruto, valor in base:
        # Município: a chave é o código IBGE (é ela que casa com a malha), o
        # nome vai para o tooltip e o ranking.
        chave = facts.mun_label.get(rotulo_bruto, rotulo_bruto)
        rotulo = fmt.period(chave) if tempo else render._display(facts, rotulo_bruto)
        detalhe = f"{fmt.percent(valor / total, 1)} do total" if total else ""
        itens.append({"k": str(chave), "nome": str(rotulo), "v": float(valor),
                      "txt": f(valor), "det": detalhe})
    return itens


def _js_data(obj: Any) -> str:
    """JSON seguro dentro de <script>: `</` não fecha a tag."""
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


# --------------------------------------------------------------------------- #
# Peças SVG
# --------------------------------------------------------------------------- #
def color_positions(values: Dict[str, float], floor: float = 0.0) -> Dict[str, float]:
    """Posição de cada valor na rampa de cor (0 = mais claro, 1 = mais escuro).

    Linear, como no mapa por UF, quando os valores são parecidos. Quando um
    item domina (o maior passa de 10× a mediana — é o caso de capital contra
    município pequeno), a escala linear deixa todo o resto quase branco: aí a
    posição é a do RANKING, e cada faixa de valores ganha um tom. `floor` é o
    tom mínimo de quem TEM dado, para não se confundir com "sem dado"."""
    if not values:
        return {}
    ordenados = sorted(values.values())
    vmin, vmax = ordenados[0], ordenados[-1]
    mediana = ordenados[len(ordenados) // 2]
    por_ranking = mediana > 0 and vmax / mediana > 10
    distintos = sorted(set(ordenados))
    posicao = {v: i / max(1, len(distintos) - 1) for i, v in enumerate(distintos)}
    saida = {}
    for k, v in values.items():
        if por_ranking:
            t = posicao[v]
        else:
            t = (v - vmin) / (vmax - vmin) if vmax > vmin else 1.0
        saida[k] = floor + (1 - floor) * t
    return saida


def _map_svg(itens: Sequence[Dict[str, Any]], paths: Optional[Tuple[Dict[str, str], int]] = None,
             label: str = "Mapa do Brasil por UF", municipal: bool = False) -> str:
    caminhos, altura = paths or _map_paths()
    # No municipal, a maioria dos municípios não tem dado: eles ficam num cinza
    # quase branco e quem tem dado começa num lilás já visível (`floor`).
    tons = color_positions({i["k"]: i["v"] for i in itens}, floor=0.18 if municipal else 0.0)
    vazio = _SEM_DADO_MUNICIPAL if municipal else _SEM_DADO
    partes = []
    for chave, d in caminhos.items():
        cor = color(tons[chave]) if chave in tons else vazio
        partes.append(f'<path d="{d}" fill="{cor}" data-k="{html.escape(chave)}"/>')
    classe = "mapa municipal" if municipal else "mapa"
    return (f'<svg class="{classe}" viewBox="0 0 {_MAPA_W} {altura}" role="img" '
            f'aria-label="{html.escape(label)}">{"".join(partes)}</svg>')


def _bars_html(itens: Sequence[Dict[str, Any]]) -> str:
    topo = max((abs(i["v"]) for i in itens), default=0) or 1
    linhas = []
    for i in itens:
        largura = max(0.5, abs(i["v"]) / topo * 100)
        rotulo = i["k"] if len(i["k"]) <= 4 else i["nome"]
        linhas.append(
            f'<div class="linha" data-k="{html.escape(i["k"])}">'
            f'<span class="rot" title="{html.escape(i["nome"])}">{html.escape(rotulo)}</span>'
            f'<span class="trilho"><span class="barra" style="width:{largura:.1f}%"></span></span>'
            f'<span class="val">{html.escape(i["txt"])}</span></div>')
    # Coluna do rótulo com largura fixa (a do maior rótulo, até 22 caracteres):
    # sem ela cada barra começa num x diferente ("MA" é mais largo que "RJ").
    maior = max((len(i["k"]) if len(i["k"]) <= 4 else len(i["nome"]) for i in itens), default=2)
    return f'<div class="barras" style="--rot:{min(maior, 22) + 1}ch">{"".join(linhas)}</div>'


def _line_svg(itens: Sequence[Dict[str, Any]], fmt_eixo) -> str:
    w, h, esq, dir_, cima, baixo = 600, 240, 10, 10, 12, 12
    valores = [i["v"] for i in itens]
    vmin, vmax = min(valores), max(valores)
    if vmax == vmin:
        vmin, vmax = vmin - 1, vmax + 1
    n = len(itens)

    def x(j):
        return esq + (w - esq - dir_) * (j / (n - 1) if n > 1 else 0.5)

    def y(v):
        return cima + (h - cima - baixo) * (1 - (v - vmin) / (vmax - vmin))

    pontos = " ".join(f"{x(j):.1f},{y(i['v']):.1f}" for j, i in enumerate(itens))
    passo = (w - esq - dir_) / max(1, n - 1)
    alvos = "".join(
        f'<rect class="alvo" x="{x(j) - passo / 2:.1f}" y="0" width="{passo:.1f}" height="{h}" '
        f'data-k="{html.escape(i["k"])}" data-x="{x(j):.1f}" data-y="{y(i["v"]):.1f}"/>'
        for j, i in enumerate(itens))
    grade = "".join(f'<line class="grade" x1="{esq}" x2="{w - dir_}" y1="{yy:.1f}" y2="{yy:.1f}"/>'
                    for yy in (y(vmax), y((vmin + vmax) / 2), y(vmin)))
    return (f'<svg class="serie" viewBox="0 0 {w} {h}" role="img" aria-label="Série no tempo">'
            f'{grade}<polyline points="{pontos}"/>'
            f'<line id="guia" class="guia" x1="0" x2="0" y1="{cima}" y2="{h - baixo}" style="display:none"/>'
            f'<circle id="ponto" r="5" style="display:none"/>{alvos}</svg>'
            # Eixos em HTML, não em <text> do SVG: o SVG encolhe com a tela e,
            # no celular, a letra do eixo virava 6 px.
            f'<div class="eixo"><span>{html.escape(itens[0]["nome"])}</span>'
            f'<span>{html.escape(itens[-1]["nome"])}</span></div>'
            f'<div class="eixo"><span>menor: {html.escape(fmt_eixo(min(valores)))}</span>'
            f'<span>maior: {html.escape(fmt_eixo(max(valores)))}</span></div>')


# --------------------------------------------------------------------------- #
# Página
# --------------------------------------------------------------------------- #
_CSS = """
:root{--roxo:#7c3aed;--ink:#221f33;--muted:#5b596f;--borda:#e2ddf1;--lilas:#f2eefe}
*{box-sizing:border-box}html,body{margin:0;background:transparent;overflow:hidden}
body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink)}
.card{position:relative;background:#fff;border:1px solid var(--borda);border-radius:16px;padding:14px 14px 10px}
h1{font-size:15px;margin:0 0 2px;font-weight:700}.sub{color:var(--muted);font-size:12.5px;margin:0 0 10px}
.abas{display:flex;gap:6px;margin-bottom:8px}
.abas button{border:1px solid var(--borda);background:#fff;color:var(--ink);border-radius:99px;padding:4px 12px;font:inherit;font-size:12.5px;cursor:pointer}
.abas button.on{background:var(--roxo);color:#fff;border-color:var(--roxo)}
svg{width:100%;height:auto;display:block;touch-action:manipulation}
.mapa path{stroke:#fff;stroke-width:.8;cursor:pointer}
.mapa.municipal path{stroke:#dcd8e8;stroke-width:.35}
.mapa path:hover,.mapa path.sel{stroke:var(--ink);stroke-width:1.6;filter:brightness(1.06)}
#mapa{max-width:520px;margin:0 auto}
.legenda{display:flex;justify-content:space-between;color:var(--muted);font-size:11.5px;margin-top:4px}
.rampa{height:7px;border-radius:4px;margin-top:6px;background:linear-gradient(90deg,#f2eefe,#b9a3f6,#7c3aed,#4c1d95)}
.barras{max-height:520px;overflow:auto}
.linha{display:flex;align-items:center;gap:8px;padding:2px 4px;border-radius:6px;cursor:pointer}
.linha:hover,.linha.sel{background:var(--lilas)}
.rot{flex:0 0 var(--rot,30px);max-width:40%;font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.trilho{flex:1;height:13px;min-width:40px}.barra{display:block;height:100%;background:var(--roxo);border-radius:0 4px 4px 0}
.val{flex:0 0 auto;font-size:11.5px;color:var(--muted);font-variant-numeric:tabular-nums}
.serie polyline{fill:none;stroke:var(--roxo);stroke-width:2.4}
.eixo{display:flex;flex-wrap:wrap;gap:0 12px;justify-content:space-between;color:var(--muted);font-size:11.5px;margin-top:2px}.serie .grade{stroke:#eceaf5}
.serie .guia{stroke:var(--ink);stroke-dasharray:3 3}.serie circle{fill:var(--roxo);stroke:#fff;stroke-width:2}
.serie .alvo{fill:transparent;cursor:pointer}
#tip{position:absolute;pointer-events:none;background:var(--ink);color:#fff;border-radius:8px;padding:7px 9px;font-size:12.5px;line-height:1.35;display:none;box-shadow:0 4px 14px #0003;z-index:2;max-width:240px}
#tip b{font-size:13px}
.dica{color:var(--muted);font-size:11.5px;margin-top:6px}
#painel{margin-top:10px;padding:10px 12px;border:1px solid var(--borda);border-radius:12px;background:var(--lilas);display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between}
#painel .pinfo{font-size:13px}#painel .pdet{color:var(--muted);font-size:12px}
#painel button{border:0;background:var(--roxo);color:#fff;border-radius:99px;padding:7px 14px;font:inherit;font-size:13px;font-weight:600;cursor:pointer}
#painel button:disabled{background:#b9a3f6;cursor:default}
"""

_JS = """
const D=DATA,card=document.querySelector('.card'),tip=document.getElementById('tip');
const porK=Object.fromEntries(D.itens.map(i=>[i.k,i]));
function altura(){parent.postMessage({type:'iframe:height',height:Math.ceil(card.getBoundingClientRect().bottom)+4},'*')}
function mostra(k,x,y){const i=porK[k]||(D.nomes&&D.nomes[k]?{nome:D.nomes[k],txt:'sem dado no resultado',det:''}:null);
 if(!i){tip.style.display='none';return}
 tip.innerHTML='<b></b><br><span></span>'+(i.det?'<br><span></span>':'');
 const s=tip.querySelectorAll('b,span');s[0].textContent=i.nome;s[1].textContent=i.txt;if(i.det)s[2].textContent=i.det;
 tip.style.display='block';const r=card.getBoundingClientRect(),w=tip.offsetWidth,h=tip.offsetHeight;
 let l=x-r.left+12,t=y-r.top+12;if(l+w>r.width-6)l=Math.max(6,x-r.left-w-12);if(t+h>r.height-6)t=Math.max(6,y-r.top-h-12);
 tip.style.left=l+'px';tip.style.top=t+'px'}
function marca(k){document.querySelectorAll('.sel').forEach(e=>e.classList.remove('sel'));
 document.querySelectorAll('[data-k]').forEach(e=>{if(e.dataset.k===k)e.classList.add('sel')});
 const a=document.querySelector('.alvo[data-k="'+CSS.escape(k)+'"]'),g=document.getElementById('guia'),p=document.getElementById('ponto');
 if(a&&g){g.setAttribute('x1',a.dataset.x);g.setAttribute('x2',a.dataset.x);g.style.display='';
  p.setAttribute('cx',a.dataset.x);p.setAttribute('cy',a.dataset.y);p.style.display=''}}
document.querySelectorAll('[data-k]').forEach(el=>{
 el.addEventListener('mousemove',e=>{marca(el.dataset.k);mostra(el.dataset.k,e.clientX,e.clientY)});
 el.addEventListener('mouseleave',()=>{tip.style.display='none'});
 el.addEventListener('click',e=>{e.stopPropagation();marca(el.dataset.k);
  if(D.drill[el.dataset.k]){tip.style.display='none';painel(el.dataset.k)}else mostra(el.dataset.k,e.clientX,e.clientY)})});
function painel(k){const p=document.getElementById('painel'),i=porK[k];if(!p||!i)return;
 p.innerHTML='<div class="pinfo"><b></b> <span></span> <span class="pdet"></span></div><button type="button"></button>';
 p.querySelector('b').textContent=i.nome;p.querySelectorAll('span')[0].textContent=i.txt;
 p.querySelector('.pdet').textContent=i.det?'('+i.det+')':'';
 const b=p.querySelector('button');b.textContent=D.drill[k].b;
 b.addEventListener('click',ev=>{ev.stopPropagation();
  parent.postMessage({type:'input:prompt:submit',text:D.drill[k].q},'*');
  b.textContent='Pergunta enviada ao chat';b.disabled=true});
 p.style.display='';altura()}
document.addEventListener('click',()=>{tip.style.display='none'});
document.querySelectorAll('.abas button').forEach(b=>b.addEventListener('click',e=>{e.stopPropagation();
 document.querySelectorAll('.abas button').forEach(x=>x.classList.toggle('on',x===b));
 document.querySelectorAll('.vista').forEach(v=>v.style.display=v.id===b.dataset.v?'':'none');
 tip.style.display='none';altura()}));
addEventListener('load',altura);addEventListener('resize',altura);
if(window.ResizeObserver)new ResizeObserver(altura).observe(document.body);altura();
"""


_POR_UF = re.compile(r"\bpor\s+(?:estados?|ufs?|unidades?\s+da\s+federa[cç][aã]o)\b", re.I)


# "na Bahia", "no Pará", "em São Paulo": o artigo de cada estado.
_NO = {"AC", "AP", "AM", "CE", "DF", "ES", "MA", "MT", "MS", "PA", "PR", "PI", "RJ", "RN", "RS", "TO"}
_NA = {"BA", "PB"}


def in_uf(sigla: str) -> str:
    from rag.journey import ibge

    nome = (ibge.by_sigla(sigla) or {}).get("nome", sigla)
    return f"{'no' if sigla in _NO else 'na' if sigla in _NA else 'em'} {nome}"


def of_uf(sigla: str) -> str:
    """"da Bahia", "do Pará", "de São Paulo"."""
    from rag.journey import ibge

    nome = (ibge.by_sigla(sigla) or {}).get("nome", sigla)
    return f"{'do' if sigla in _NO else 'da' if sigla in _NA else 'de'} {nome}"


def drill_question(question: str, uf_sigla: str) -> str:
    """A pergunta que o botão "Ver os municípios" manda ao chat.

    "Quanto foi contratado no FAR por estado?" → "… no FAR por município na
    Bahia?". Sem "por estado" na pergunta, o recorte vai no fim. A pergunta
    passa pelo caminho normal (com o contexto da conversa), então o motor
    escolhe a tabela que tem município — ou responde que não há."""
    onde = in_uf(uf_sigla)
    base = (question or "").strip().rstrip("?.! ")
    if not base:
        return f"E por município {onde}?"
    if _POR_UF.search(base):
        base = _POR_UF.sub(f"por município {onde}", base, count=1)
    else:
        base = f"{base}, por município {onde}"
    return base + "?"


def _abas(kind: str, mapa: str, itens: Sequence[Dict[str, Any]]) -> str:
    menor, maior = min(itens, key=lambda i: i["v"]), max(itens, key=lambda i: i["v"])
    legenda = (f'<div class="rampa"></div><div class="legenda"><span>{html.escape(menor["txt"])}</span>'
               f'<span>{html.escape(maior["txt"])}</span></div>')
    primeiro = "rank" if kind == RANKING else "mapa"
    return (f'<div class="abas"><button data-v="mapa" class="{"on" if primeiro == "mapa" else ""}">Mapa'
            f'</button><button data-v="rank" class="{"on" if primeiro == "rank" else ""}">Ranking</button></div>'
            f'<div class="vista" id="mapa" style="{"" if primeiro == "mapa" else "display:none"}">{mapa}{legenda}'
            f'<div id="painel" style="display:none"></div></div>'
            f'<div class="vista" id="rank" style="{"" if primeiro == "rank" else "display:none"}">{_bars_html(itens)}</div>')


def page(facts: Facts, kind: str = ONDE, title: str = "", question: str = "") -> Optional[str]:
    """O HTML do gráfico interativo, ou None quando não há o que desenhar.

    `question` é a pergunta do resultado: com ela, o mapa por UF ganha, no
    clique num estado, o botão que pergunta o mesmo número por município."""
    from rag.journey import municipios

    if facts.n < 2:
        return None
    itens = _items(facts)
    if len(itens) < 2:
        return None
    metrica = render.cap(facts.metric_label or "valor")
    municipal = (facts.dimension_kind == "municipio" and facts.mun_values
                 and len(facts.mun_ufs) <= municipios.MAX_UFS_MAPA)
    from rag.journey import ibge
    from rag.journey.text import fold

    ufs_nomes = [(ibge.by_sigla(u) or {}).get("nome", u) for u in facts.mun_ufs]
    # "(SP)" só quando o título ainda não diz o estado, por nome ou sigla.
    palavras = set(fold(metrica).split())
    onde = ("" if all(fold(n) in fold(metrica) or u.lower() in palavras
                      for n, u in zip(ufs_nomes, facts.mun_ufs))
            else f" ({', '.join(facts.mun_ufs)})")
    titulo = title or (f"{metrica} por UF" if facts.dimension_kind == "uf" else
                       f"{metrica} por município{onde}" if municipal else
                       f"{metrica} ao longo do tempo" if facts.dimension_kind == "tempo" else
                       f"{metrica} por {fmt.humanize(facts.dimension or '') or 'categoria'}")
    toque = "Passe o mouse ou toque num item para ver o valor exato."
    drill: Dict[str, Dict[str, str]] = {}
    nomes: Dict[str, str] = {}
    if facts.dimension_kind == "uf" and facts.uf_values:
        if question:
            drill = {i["k"]: {"q": drill_question(question, i["k"]),
                              "b": f"Ver os municípios {of_uf(i['k'])}"} for i in itens}
            toque = "Passe o mouse para ver o valor; toque num estado para abrir os municípios."
        corpo = _abas(kind, _map_svg(itens), itens)
    elif municipal:
        caminhos = _municipal_paths(tuple(facts.mun_ufs))
        mapa = _map_svg(itens, caminhos, label=f"Mapa dos municípios de {', '.join(facts.mun_ufs)}",
                        municipal=True)
        # Município sem linha no resultado também responde ao toque: o nome e
        # "sem dado", em vez de um toque que não faz nada.
        com_valor = {i["k"] for i in itens}
        nomes = {cod: municipios.name(cod) for cod in caminhos[0] if cod not in com_valor}
        corpo = _abas(kind, mapa, itens)
    elif facts.dimension_kind == "tempo":
        corpo = _line_svg(itens, render.money_fmt(facts))
    else:
        corpo = _bars_html(itens)
    total = facts.total() if (facts.additive and facts.dimension_kind != "tempo") else None
    sub = toque + (f" Total: {render.money_fmt(facts)(total)}." if total else "")
    dados = {"itens": [{k: i[k] for k in ("k", "nome", "txt", "det")} for i in itens], "drill": drill, "nomes": nomes}
    return ("<!doctype html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{html.escape(titulo)}</title><style>{_CSS}</style></head><body>"
            f'<div class="card"><h1>{html.escape(titulo)}</h1><p class="sub">{html.escape(sub)}</p>'
            f'{corpo}<div id="tip"></div></div>'
            f"<script>const DATA={_js_data(dados)};{_JS}</script></body></html>")

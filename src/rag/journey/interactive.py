"""Gráfico interativo da jornada: HTML autocontido com o valor no cursor (módulo puro).

O PNG da resposta não deixa a pessoa ler o valor de cada UF ou barra (pedido de
produto, 10/11/2026). O Open WebUI 0.11 mostra HTML com script dentro da
mensagem ("embeds", iframe com `allow-scripts`), e uma Function de filtro dele
(`webui/functions/grafico_interativo.py`) põe este HTML embaixo da resposta.

Uma página por gráfico. O "onde"/"ranking" tem três formas, escolhidas pelo
recorte — as mesmas do PNG (`render.where_chart`):

- UF     → mapa do Brasil (malha do IBGE) + aba de ranking;
- tempo  → linha, com o ponto mais perto do cursor destacado (no ranking do
  gestor, os pontos fora da curva em laranja);
- resto  → barras ordenadas.

E as versões interativas dos outros gráficos de dado das trilhas (11/11):
pontos fora da curva com a faixa esperada (`outliers_page`), o histograma com
quem está em cada faixa (`distribution_page`) e as regiões empilhadas por UF
(`regions_page`). Diagrama (jornada do dado, jornada da política), cartão,
pictograma e cobertura de colunas não têm valor a ler no cursor além do que o
PNG já escreve, e seguem só em PNG.

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
FORA = "fora"          # "Números para decisão": pontos fora da curva
DISTRIBUICAO = "distribuicao"  # "Como esse dado foi calculado": histograma
REGIOES = "regioes"    # "Contexto da política": regiões empilhadas por UF
SERIE = "serie"        # "Contexto da política": a série histórica
KINDS = (ONDE, RANKING, FORA, DISTRIBUICAO, REGIOES, SERIE)

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


def _line_svg(itens: Sequence[Dict[str, Any]], fmt_eixo, flagged: Sequence[str] = ()) -> str:
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
    marcados = set(flagged)
    alertas = "".join(f'<circle class="alerta" cx="{x(j):.1f}" cy="{y(i["v"]):.1f}" r="6"/>'
                      for j, i in enumerate(itens) if i["k"] in marcados)
    return (f'<svg class="serie" viewBox="0 0 {w} {h}" role="img" aria-label="Série no tempo">'
            f'{grade}<polyline points="{pontos}"/>{alertas}'
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
.serie .alerta{fill:#f97316;stroke:#fff;stroke-width:2;pointer-events:none}
.plot{position:relative}.plot .yl{position:absolute;left:0;transform:translateY(-50%);color:var(--muted);font-size:11px;font-variant-numeric:tabular-nums;pointer-events:none}
.plot .fl{position:absolute;right:2px;transform:translateY(-110%);color:#b45309;font-size:11px;font-weight:600;pointer-events:none;background:#fffc;padding:0 3px;border-radius:4px}
.plot .ml{position:absolute;top:0;transform:translateX(4px);color:#c2410c;font-size:11px;font-weight:700;white-space:nowrap;pointer-events:none}
.plot .ml.esq{transform:translateX(calc(-100% - 4px))}.hplot .ml{top:-17px}.hplot .yl{left:-34px}
.pontos{position:relative;height:240px}.pontos>span{position:absolute;display:block}
.pontos .gl{left:0;right:0;border-top:1px solid #eceaf5}
.pontos .faixa{left:0;right:0;background:#f2eefe}.pontos .cerca{left:0;right:0;border-top:1.5px dashed #f59e0b}
.pontos .col{top:0;bottom:0;cursor:pointer}.pontos .col:hover,.pontos .col.sel{background:#7c3aed14}
.pontos .pt{width:calc(var(--r)*2);height:calc(var(--r)*2);margin:calc(var(--r)*-1) 0 0 calc(var(--r)*-1);border-radius:50%;background:#c4b5fd;border:1.5px solid #fff;pointer-events:none;box-sizing:border-box}
.pontos .pt.fora{background:#f97316}.pontos .pt.sel{border:2.5px solid var(--ink);transform:scale(1.25)}
svg.hist{height:200px}.hist line{vector-effect:non-scaling-stroke}.hist .grade{stroke:#eceaf5}.hist rect.bin{fill:var(--roxo);cursor:pointer}.hist rect.bin:hover,.hist rect.bin.sel{fill:#5b21b6}
.hist .mediana{stroke:#f97316;stroke-width:2;stroke-dasharray:5 4}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.chip{border:1px solid #fed7aa;background:#fff7ed;color:#9a3412;border-radius:99px;padding:4px 10px;font:inherit;font-size:12px;cursor:pointer}
.chip.sel,.chip:hover{background:#f97316;color:#fff;border-color:#f97316}
.chip b{font-weight:700}
.leg{display:flex;flex-wrap:wrap;gap:4px 14px;color:var(--muted);font-size:11.5px;margin-top:6px;align-items:center}
.leg i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:-1px}
.leg i.faixa{border-radius:2px;background:#f2eefe;border:1px dashed #f59e0b}
.eixox{position:relative;height:16px;color:var(--muted);font-size:11px;margin-top:2px}
.eixox span{position:absolute;transform:translateX(-50%);white-space:nowrap}
.eixox span:first-child{transform:none}.eixox span:last-child{transform:translateX(-100%)}
#lista{margin-top:10px;border:1px solid var(--borda);border-radius:12px;padding:8px 10px;background:var(--lilas)}
#lista h2{font-size:13px;margin:0 0 6px}#lista .lrow{display:flex;justify-content:space-between;gap:10px;font-size:12px;padding:2px 0;border-top:1px solid #e2ddf1}
#lista .lrow:first-of-type{border-top:0}#lista .lrow span:last-child{color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}
#lista .lbox{max-height:220px;overflow:auto}
.reg{display:flex;align-items:center;gap:8px;padding:3px 0}
.reg .rot{flex:0 0 11ch;max-width:none;cursor:pointer}.reg .pilha{flex:1;display:flex;height:22px;min-width:60px}
.reg .seg{height:100%;border-right:1.5px solid #fff;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:10.5px;font-weight:700;overflow:hidden;color:#fff}
.reg .seg.claro{color:var(--ink)}.reg .seg:hover,.reg .seg.sel{filter:brightness(.88);outline:2px solid var(--ink);outline-offset:-2px}
.reg .val{flex:0 0 8ch;text-align:right}
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
  if(D.drill[el.dataset.k]){tip.style.display='none';painel(el.dataset.k)}else mostra(el.dataset.k,e.clientX,e.clientY);
  if(D.lista&&D.lista[el.dataset.k])lista(el.dataset.k)})});
function lista(k){const p=document.getElementById('lista'),l=D.lista[k];if(!p||!l)return;
 p.innerHTML='<h2></h2><div class="lbox"></div>';p.querySelector('h2').textContent=l.t;
 const box=p.querySelector('.lbox');l.r.forEach(r=>{const d=document.createElement('div');d.className='lrow';
  d.innerHTML='<span></span><span></span>';d.children[0].textContent=r[0];d.children[1].textContent=r[1];box.appendChild(d)});
 p.style.display='';altura()}
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
        # No gestor ("Números para decisão"), a série mostra os pontos fora da
        # curva — é a versão interativa da "Tendência com alerta".
        fora = {k for k, _ in facts.outliers()} if kind == RANKING else set()
        if fora:
            for i in itens:
                if i["k"] in fora:
                    i["det"] = "Fora da curva (regra do IQR)"
            toque = "Pontos fora da curva em laranja. " + toque
        corpo = _line_svg(itens, render.money_fmt(facts), fora)
    else:
        corpo = _bars_html(itens)
    total = facts.total() if (facts.additive and facts.dimension_kind != "tempo") else None
    sub = toque + (f" Total: {render.money_fmt(facts)(total)}." if total else "")
    return _html(titulo, sub, corpo, itens, drill=drill, nomes=nomes)


def _html(titulo: str, sub: str, corpo: str, itens: Sequence[Dict[str, Any]], **extra: Any) -> str:
    """A página: cartão, título, dica, o desenho e o tooltip. Todo elemento com
    `data-k` mostra no cursor/toque o item de mesma chave em `itens`."""
    dados = {"itens": [{k: i[k] for k in ("k", "nome", "txt", "det")} for i in itens],
             "drill": {}, "nomes": {}, **extra}
    return ("<!doctype html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{html.escape(titulo)}</title><style>{_CSS}</style></head><body>"
            f'<div class="card"><h1>{html.escape(titulo)}</h1><p class="sub">{html.escape(sub)}</p>'
            f'{corpo}<div id="tip"></div></div>'
            f"<script>const DATA={_js_data(dados)};{_JS}</script></body></html>")


# --------------------------------------------------------------------------- #
# Os outros gráficos de dado das trilhas
# --------------------------------------------------------------------------- #
def _ticks(lo: float, hi: float, n: int = 4) -> List[float]:
    """Marcas "redondas" do eixo entre `lo` e `hi` (1, 2, 2,5 ou 5 × 10^k)."""
    if hi <= lo:
        return [lo]
    bruto = (hi - lo) / n
    base = 10 ** math.floor(math.log10(bruto))
    passo = next(m * base for m in (1, 2, 2.5, 5, 10) if m * base >= bruto)
    primeiro = math.ceil(lo / passo) * passo
    return [primeiro + i * passo for i in range(int((hi - primeiro) / passo + 1e-9) + 1)]


def _y_labels(marcas: Sequence[float], y, h: float, fmt_eixo) -> str:
    """Rótulos do eixo em HTML, por cima do SVG: o SVG encolhe com a tela e,
    em <text>, a letra virava 6 px no celular."""
    return "".join(f'<span class="yl" style="top:{y(m) / h * 100:.2f}%">{html.escape(fmt_eixo(m))}</span>'
                   for m in marcas)


def outliers_page(facts: Facts) -> Optional[str]:
    """"Tem algo fora da curva?": cada valor é um ponto, do maior para o menor,
    com a faixa esperada (regra do IQR) pintada atrás. Os pontos fora dela
    ficam em laranja e ganham um botão embaixo do gráfico com o nome e o
    valor — o rótulo ao lado do ponto encavalava quando dois maiores eram
    vizinhos (pedido de produto, 11/11/2026)."""
    fora, cercas = facts.outliers(), facts.fences()
    if not fora or not cercas or facts.dimension_kind == "tempo":
        return None
    f, curto = render.money_fmt(facts), render.chart_fmt(facts)
    baixo, alto = cercas
    marcados = {r for r, _ in fora}
    base = sorted(facts.values, key=lambda kv: kv[1], reverse=True)
    itens = []
    for rotulo, valor in base:
        if valor > alto:
            det = f"Fora da curva: {f(valor - alto)} acima do limite de {f(alto)}"
        elif valor < baixo:
            det = f"Fora da curva: {f(baixo - valor)} abaixo do limite de {f(baixo)}"
        else:
            det = "Dentro da faixa esperada"
        itens.append({"k": str(rotulo), "nome": str(render._display(facts, rotulo)), "v": float(valor),
                      "txt": f(valor), "det": det})

    h, cima, baixo_px = 240, 12, 8
    valores = [i["v"] for i in itens]
    lo = min(min(valores), baixo) if min(valores) < 0 else 0.0
    lo = max(lo, min(valores) - (max(valores) - min(valores)) * 0.1) if min(valores) < 0 else lo
    hi = max(max(valores), alto)
    hi += (hi - lo) * 0.06 or 1

    def y(v):
        return cima + (h - cima - baixo_px) * (1 - (v - lo) / (hi - lo))

    n = len(itens)
    marcas = _ticks(lo, hi)
    if len(marcas) > 1 and marcas[-1] < hi:
        # O eixo fecha numa marca redonda acima do maior ponto.
        marcas.append(marcas[-1] + (marcas[1] - marcas[0]))
        hi = marcas[-1]
    topo_faixa, pe_faixa = y(alto), y(max(baixo, lo))
    rotulos_cerca = (f'<span class="fl" style="top:{topo_faixa / h * 100:.2f}%">limite de cima: '
                     f'{html.escape(curto(alto))}</span>')
    if baixo > lo:
        rotulos_cerca += (f'<span class="fl" style="top:{pe_faixa / h * 100 + 6:.2f}%">limite de baixo: '
                          f'{html.escape(curto(baixo))}</span>')
    raio = 6 if n <= 40 else 4.5 if n <= 90 else 3

    def pct_y(v):
        return y(v) / h * 100

    # Em HTML de altura fixa, não em SVG: o SVG encolhe com a tela e, no
    # celular, o gráfico virava uma faixa de 110 px com pontos de 3 px. Aqui a
    # altura é a mesma em qualquer largura e o ponto continua redondo.
    colunas = "".join(f'<span class="col" style="left:{j / n * 100:.3f}%;width:{100 / n:.3f}%" '
                      f'data-k="{html.escape(i["k"])}"></span>' for j, i in enumerate(itens))
    pontos = "".join(f'<span class="pt{" fora" if i["k"] in marcados else ""}" '
                     f'style="left:{(j + 0.5) / n * 100:.3f}%;top:{pct_y(i["v"]):.2f}%;'
                     f'--r:{raio}px" data-k="{html.escape(i["k"])}"></span>'
                     for j, i in enumerate(itens))
    grade = "".join(f'<span class="gl" style="top:{pct_y(m):.2f}%"></span>' for m in marcas)
    faixa = (f'<span class="faixa" style="top:{pct_y(alto):.2f}%;'
             f'height:{max(0.0, pct_y(max(baixo, lo)) - pct_y(alto)):.2f}%"></span>'
             f'<span class="cerca" style="top:{pct_y(alto):.2f}%"></span>')
    if baixo > lo:
        faixa += f'<span class="cerca" style="top:{pct_y(baixo):.2f}%"></span>'
    svg = (f'<div class="pontos" role="img" aria-label="Valores do maior para o menor, com a faixa '
           f'esperada">{faixa}{grade}{colunas}{pontos}</div>')
    chips = "".join(f'<button type="button" class="chip" data-k="{html.escape(i["k"])}">'
                    f'{html.escape(i["nome"])} <b>{html.escape(i["txt"])}</b></button>'
                    for i in itens if i["k"] in marcados)
    legenda = (f'<div class="leg"><span><i style="background:#f97316"></i>fora da curva ({len(fora)})</span>'
               f'<span><i style="background:#c4b5fd"></i>dentro do esperado ({n - len(fora)})</span>'
               f'<span><i class="faixa"></i>faixa esperada: {html.escape(render.expected_range(facts))}</span></div>')
    corpo = (f'<div class="plot" style="padding-left:var(--yl,64px)">{svg}'
             f'{_y_labels(marcas, y, h, curto)}{rotulos_cerca}</div>{legenda}'
             f'<div class="chips">{chips}</div>')
    sub = ("Cada ponto é um valor, do maior para o menor. Passe o mouse ou toque num ponto "
           "para ver o nome e o valor exato; os botões abaixo destacam quem está fora da curva.")
    return _html("Tem algo fora da curva?", sub, corpo, itens)


def distribution_page(facts: Facts) -> Optional[str]:
    """"Como os valores se distribuem?": o histograma da trilha técnica. No
    cursor, a faixa, quantos itens há nela e os primeiros nomes; no toque, a
    lista completa da faixa embaixo do gráfico."""
    faixas = facts.bins()
    if not faixas:
        return None
    f, curto = render.money_fmt(facts), render.chart_fmt(facts)
    itens, lista = [], {}
    for j, (de, ate, dentro) in enumerate(faixas):
        k = f"b{j}"
        qtd = len(dentro)
        nomes = [str(render._display(facts, r)) for r, _ in dentro]
        det = ", ".join(nomes[:5]) + (f" e mais {qtd - 5}" if qtd > 5 else "")
        itens.append({"k": k, "nome": f"De {f(de)} a {f(ate)}", "v": qtd,
                      "txt": f"{qtd} {'valor' if qtd == 1 else 'valores'}", "det": det})
        if dentro:
            lista[k] = {"t": f"{qtd} {'valor' if qtd == 1 else 'valores'} de {f(de)} a {f(ate)}",
                        "r": [[nome, f(v)] for nome, (_, v) in zip(nomes, dentro)]}

    w, h, cima = 600, 200, 12
    topo = max(len(d) for _, _, d in faixas) or 1
    marcas = [m for m in _ticks(0, topo, 4) if float(m).is_integer()] or [0, topo]
    escala_topo = max(topo, marcas[-1])

    def y(c):
        return cima + (h - cima) * (1 - c / escala_topo)

    lo, hi = faixas[0][0], faixas[-1][1]
    largura = w / len(faixas)
    barras = "".join(
        f'<rect class="bin" x="{j * largura + 1:.1f}" y="{y(len(d)):.1f}" width="{largura - 2:.1f}" '
        f'height="{max(0.0, h - y(len(d))):.1f}" data-k="b{j}"/>'
        # Faixa vazia ainda responde ao toque (um alvo fino no pé do gráfico).
        if d else f'<rect class="bin" x="{j * largura + 1:.1f}" y="{h - 3}" width="{largura - 2:.1f}" '
                  f'height="3" style="fill:#eceaf5" data-k="b{j}"/>'
        for j, (_, _, d) in enumerate(faixas))
    grade = "".join(f'<line class="grade" x1="0" x2="{w}" y1="{y(m):.1f}" y2="{y(m):.1f}"/>' for m in marcas)
    mediana = facts.median()
    xm = (mediana - lo) / (hi - lo) * w if hi > lo else w / 2
    linha_mediana = f'<line class="mediana" x1="{xm:.1f}" x2="{xm:.1f}" y1="0" y2="{h}"/>'
    pos = xm / w * 100
    rotulo_mediana = (f'<span class="ml{" esq" if pos > 65 else ""}" style="left:{pos:.2f}%">'
                      f'mediana {html.escape(curto(mediana))}</span>')
    # Altura fixa e `preserveAspectRatio="none"`: barra esticada continua
    # barra, e no celular o gráfico não vira uma faixa fina.
    svg = (f'<svg class="hist" viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img" '
           f'aria-label="Histograma dos valores">'
           f'{grade}{barras}{linha_mediana}</svg>')
    meio = len(faixas) // 2
    eixo_x = (f'<div class="eixox"><span style="left:0">{html.escape(curto(lo))}</span>'
              f'<span style="left:{meio / len(faixas) * 100:.2f}%">{html.escape(curto(faixas[meio][0]))}</span>'
              f'<span style="left:100%">{html.escape(curto(hi))}</span></div>')
    # O rótulo da mediana tem uma linha só dele, acima do desenho: dentro,
    # ele encostava no topo da barra mais alta.
    corpo = (f'<div class="plot hplot" style="padding-left:34px;padding-top:18px">'
             f'<div style="position:relative">{svg}{rotulo_mediana}'
             f'{_y_labels(marcas, y, h, lambda c: str(int(c)))}</div></div>'
             f'<div style="padding-left:34px">{eixo_x}</div>'
             f'<div class="leg"><span>quantidade de valores em cada faixa</span>'
             f'<span><i style="background:#f97316;border-radius:0;width:12px;height:2px"></i>'
             f'mediana: {html.escape(f(mediana))}</span></div>'
             f'<div id="lista" style="display:none"></div>')
    sub = (f"{facts.n} valores de {facts.metric_label}. Cada barra junta os itens com valor naquela "
           "faixa, e a altura diz quantos são. Toque numa barra para ver quem está nela.")
    return _html("Como os valores se distribuem?", sub, corpo, itens, lista=lista)


_TONS_REGIAO = ("#4c1d95", "#7c3aed", "#9d7af0", "#b9a3f6", "#ddd3fb", "#f2eefe")


def regions_page(facts: Facts) -> Optional[str]:
    """Comparação entre regiões: o total de cada uma, dividido pelas suas UFs.
    No cursor, a UF com o valor exato e a fatia dela na região e no total."""
    from rag.journey import ibge

    regioes = facts.region_values() if facts.dimension_kind == "uf" and facts.additive else {}
    if len(regioes) < 2:
        return None
    f = render.money_fmt(facts)
    total = sum(sum(u.values()) for u in regioes.values()) or 1
    ordem = sorted(regioes.items(), key=lambda kv: -sum(kv[1].values()))
    topo = sum(ordem[0][1].values()) or 1
    itens, linhas = [], []
    for regiao, ufs in ordem:
        soma = sum(ufs.values())
        chave = f"r:{regiao}"
        itens.append({"k": chave, "nome": regiao, "v": soma, "txt": f(soma),
                      "det": f"{fmt.percent(soma / total, 1)} do total, em {len(ufs)} "
                             f"{'UF' if len(ufs) == 1 else 'UFs'}"})
        segmentos = []
        for k, (sigla, valor) in enumerate(sorted(ufs.items(), key=lambda kv: -kv[1])):
            nome = (ibge.by_sigla(sigla) or {}).get("nome", sigla)
            itens.append({"k": sigla, "nome": f"{nome} ({regiao})", "v": valor, "txt": f(valor),
                          "det": f"{fmt.percent(valor / soma, 1) if soma else '0%'} da região, "
                                 f"{fmt.percent(valor / total, 1)} do total"})
            largura = valor / topo * 100
            claro = " claro" if k >= 3 else ""
            texto = html.escape(sigla) if largura > 7 else ""
            segmentos.append(f'<span class="seg{claro}" data-k="{html.escape(sigla)}" '
                             f'style="width:{largura:.2f}%;background:{_TONS_REGIAO[min(k, 5)]}">{texto}</span>')
        linhas.append(f'<div class="reg"><span class="rot" data-k="{html.escape(chave)}">{html.escape(regiao)}</span>'
                      f'<span class="pilha">{"".join(segmentos)}</span>'
                      f'<span class="val">{html.escape(render.chart_fmt(facts)(soma))}</span></div>')
    sub = ("Total de cada região, dividido pelas suas UFs (da maior para a menor). Passe o mouse "
           "ou toque numa UF para ver o valor exato e a fatia dela.")
    return _html("Comparação entre regiões", sub, "".join(linhas), itens)


def available(facts: Facts, kind: str) -> bool:
    """O resultado tem o gráfico deste tipo? Mesma condição do PNG da trilha:
    o serviço só deixa na resposta a linha escondida de um gráfico que existe."""
    if kind == FORA:
        return facts.dimension_kind != "tempo" and bool(facts.outliers())
    if kind == DISTRIBUICAO:
        return bool(facts.bins())
    if kind == REGIOES:
        return (facts.dimension_kind == "uf" and facts.additive
                and len(facts.region_values()) >= 2)
    if kind == SERIE:
        return facts.dimension_kind == "tempo" and facts.n >= 2
    return facts.n >= 2


def page_for(facts: Facts, kind: str, question: str = "") -> Optional[str]:
    """O gráfico interativo de um tipo (`KINDS`), ou None quando o resultado
    não tem o que desenhar nele. `SERIE` é desenhada por quem tem o resultado
    da série (`rag/server/charts.py`), com `page`."""
    if kind == FORA:
        return outliers_page(facts)
    if kind == DISTRIBUICAO:
        return distribution_page(facts)
    if kind == REGIOES:
        return regions_page(facts)
    return page(facts, kind, question=question)

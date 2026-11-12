"""Markdown de cada trilha da jornada (ADR-0020).

Uma função por trilha, todas a partir de dados já calculados (`Facts`, escala
humana, varredura do catálogo). Nenhuma chama LLM nem banco: quem orquestra é o
`service`. Os gráficos entram como PNG em data URI, como o da Persona 2.

Regras de texto que valem para todas:

- **Nada de jargão na População geral.** Tabela e coluna só aparecem na fonte,
  no fim, em letra pequena.
- **Lacuna é dita.** Se a trilha pede algo que o catálogo não tem, a resposta
  diz que não tem — nunca preenche com texto plausível.
- **O link de dados abertos só aparece configurado** (`JOURNEY_OPEN_DATA_URL`).
"""
from __future__ import annotations

import base64
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from rag.journey import fmt, units
from rag.journey.catalog_probe import Item, Stage
from rag.journey.facts import Facts
from rag.journey.human_scale import Scale

_DIM_PLURAL = {"uf": "UFs", "municipio": "municípios", "tempo": "períodos"}


# --------------------------------------------------------------------------- #
# Peças
# --------------------------------------------------------------------------- #
def image(png: Optional[bytes], alt: str) -> str:
    if not png:
        return ""
    alt = (alt or "gráfico").replace("[", "(").replace("]", ")").replace("\n", " ")
    return f"![{alt}](data:image/png;base64,{base64.b64encode(png).decode('ascii')})"


def safe_chart(fn, *args, **kwargs) -> Optional[bytes]:
    """Gráfico é aditivo: erro de desenho vira ausência, nunca resposta quebrada."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[jornada] gráfico {} falhou: {}", getattr(fn, "__name__", fn), exc)
        return None


def cap(texto: str) -> str:
    """Primeira letra maiúscula SEM baixar o resto (`str.capitalize` faria
    "valor contratado no FAR" virar "Valor contratado no far")."""
    return texto[:1].upper() + texto[1:] if texto else texto


def money_fmt(facts: Facts):
    """O formatador da métrica: dinheiro, índice ou contagem. Todo número da
    métrica que vai para o TEXTO passa por aqui — inteiro, sem "mi"/"bi"."""
    if facts.unit == units.INDICE:
        return fmt.plain
    return lambda v: fmt.exact(v, money=facts.money)


def chart_fmt(facts: Facts):
    """O formatador de DENTRO do gráfico (eixo, rótulo de barra, legenda do
    mapa): compacto, porque "R$ 5.938.123.456" não cabe ao lado de uma barra no
    celular. O valor exato de cada item vai em `values_table`, embaixo."""
    if facts.unit == units.INDICE:
        return fmt.plain
    return lambda v: fmt.compact(v, money=facts.money)


def headline_text(facts: Facts) -> str:
    return money_fmt(facts)(facts.headline)


def caption(facts: Facts) -> str:
    """O que o número em destaque é, em palavras."""
    metrica = facts.metric_label
    if facts.headline_kind == "total":
        n = facts.n
        rotulo = _DIM_PLURAL.get(facts.dimension_kind) or "categorias"
        return f"{metrica}, somando {n} {rotulo}" if n > 1 else metrica
    if facts.headline_kind == "ultimo":
        return f"{metrica} em {fmt.period(facts.headline_label)}, o período mais recente"
    if facts.headline_kind == "maior":
        return f"maior {metrica}, em {facts.headline_label}"
    return metrica


def digest(facts: Facts) -> str:
    """Resumo compacto para a frase do LLM — o teto do que ela pode afirmar."""
    f = money_fmt(facts)
    linhas = [f"Métrica: {fmt.humanize(facts.metric or '')}"
              + (" (em reais)" if facts.money else "")]
    if facts.dimension:
        linhas.append(f"Recorte: {fmt.humanize(facts.dimension)} ({facts.n} valores)")
    if facts.dimension_kind == "tempo":
        serie = facts.series()
        linhas.append(f"Começa em {fmt.period(serie[0][0])} com {f(serie[0][1])} e termina em "
                      f"{fmt.period(serie[-1][0])} com {f(serie[-1][1])}")
    elif facts.n > 1:
        linhas.append("Maiores: " + "; ".join(f"{r}: {f(v)}" for r, v in facts.top(3)))
        share = facts.share_of_top()
        if share:
            linhas.append(f"A maior concentra {fmt.percent(share[1])} do total")
    if facts.truncated:
        linhas.append("Atenção: o resultado foi cortado no limite de linhas — leitura parcial.")
    return "\n".join(linhas)


def fallback_phrase(facts: Facts) -> str:
    """Frase sem LLM, usada quando a geração falha ou desobedece."""
    numero = headline_text(facts)
    metrica = facts.metric_label or "o indicador"
    if facts.headline_kind == "total":
        frase = f"Ao todo, {metrica} soma {numero}."
        if facts.n > 1 and facts.dimension_kind != "tempo":
            rotulo, valor = facts.top(1)[0]
            frase += f" Quem lidera é {_display(facts, rotulo)}, com {money_fmt(facts)(valor)}."
        return frase
    if facts.headline_kind == "ultimo":
        return f"Em {fmt.period(facts.headline_label)}, {metrica} foi de {numero}."
    if facts.headline_kind == "maior":
        return f"O maior valor de {metrica} é {numero}, em {facts.headline_label}."
    return f"{cap(metrica)}: {numero}."


def misnamed_message(misnamed, tables: Sequence[str] = ()) -> str:
    """Por que a resposta NÃO mostra o número (ver `measure`).

    O número calculado é de outra coisa; mostrá-lo com o nome da pergunta é o
    erro mais caro que a resposta pode cometer, porque ninguém desconfia de um
    número bem formatado. O texto diz o que foi somado, para quem quiser
    conferir, e como seguir."""
    coluna = f"`{misnamed.column}`"
    if tables:
        coluna += f", da tabela `{tables[0]}`"
    promessa = fmt.humanize(misnamed.alias)
    return (
        "Não vou mostrar um número para essa pergunta, porque o que foi calculado não "
        f"responde a ela. A consulta somou a coluna {coluna}, e chamou o resultado de "
        f"*{promessa}*. Essa coluna não mede isso: nas tabelas que o sistema encontrou, "
        "não há uma medida com esse nome.\n\n"
        "Dá para reformular a pergunta dizendo a medida com outras palavras, ou perguntar "
        "*\"o que vocês têm sobre …?\"* para ver quais bases têm essa informação. "
        "Abaixo está o que o sistema tentou."
    )


def _display(facts: Facts, rotulo: str) -> str:
    """Nome por extenso quando a dimensão é UF ("BA" → "Bahia")."""
    if facts.dimension_kind == "uf":
        from rag.journey import ibge

        uf = ibge.resolve(rotulo)
        return uf["nome"] if uf else rotulo
    return rotulo


_SUPERLATIVO = re.compile(r"\b(com mais|com maior|o maior|a maior|os maiores|as maiores|"
                          r"lider\w*|primeiro lugar|no topo|campe\w*|mais \w+ (?:tem|possui|soma))\b")
_FIM_DE_FRASE = re.compile(r"(?<=[.!?])\s+")


def misreads_total(facts: Facts, phrase: str, numero: str) -> bool:
    """A frase atribui o TOTAL a um item do recorte?

    "O estado com mais empreendimentos tem 878" quando 878 é a soma das 27 UFs:
    o número bate com o cartão, a guarda do número passa, e a frase está
    errada. Vale para qualquer pergunta de ranking sobre resultado somado, por
    isso a regra olha a FORMA da frase (superlativo na mesma frase do total),
    não a base. Falso positivo só troca a frase pela determinística.
    """
    if facts.headline_kind != "total" or facts.n <= 1:
        return False
    from rag.journey.text import fold

    for frase in _FIM_DE_FRASE.split(phrase or ""):
        if numero in frase and _SUPERLATIVO.search(fold(frase)):
            return True
    return False


def source_line(tables: Sequence[str], service: str, open_data_url: str = "") -> str:
    nomes = ", ".join(f"`{t}`" for t in tables[:3]) or "catálogo"
    # Sem <sub>: o Markdown do OpenWebUI escapa a tag e ela aparece crua.
    # Frase corrida, sem "·": separador de tópico na resposta soa a texto de
    # máquina (pedido de produto, 10/11/2026).
    linha = (f"*Fonte: {nomes}, do catálogo OpenMetadata ({service}), "
             "calculado agora no banco analítico.*")
    if open_data_url:
        linha += f" [Ver nos dados abertos]({open_data_url})."
    return linha


CHART_TAG = "tais:grafico"


def chart_marker(path: str) -> str:
    """Linha invisível com o caminho assinado do gráfico interativo
    (`interactive.page`), no mesmo formato do marcador de estado. A Function
    do Open WebUI a lê e põe o gráfico embaixo da resposta; sem a Function,
    nada aparece (é comentário de Markdown)."""
    return f"[//]: # ({CHART_TAG} {path})" if path else ""


# Cada gráfico interativo (`interactive.page_for`) substitui, no chat, o PNG e
# a tabela de valores do mesmo desenho. Chave = o tipo do gráfico (o mesmo nome
# da rota `/charts/<ref>/<tipo>`); valor = (início do `alt` do PNG, título da
# tabela). O `alt` de "onde" e "ranking" é também a âncora da seção 13 do
# `webui/custom.css`, que esconde o PNG nas conversas antigas: mudar um destes
# textos pede mudar lá também. Os dos tipos novos ficam FORA do CSS de
# propósito: numa conversa antiga a mensagem tem o embed do ranking e não o
# deles, e o CSS esconderia o único desenho que ela tem.
EMBED_PARTS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "onde": (("Mapa do Brasil", "Mapa dos municípios", "Série:", "Barras:"),
             ("Os valores do gráfico",)),
    "ranking": (("Ranking de", "Tendência de"), ("Os valores do gráfico", "Os valores do ranking")),
    "fora": (("Pontos fora da curva",), ("Os valores fora da curva",)),
    "distribuicao": (("Histograma dos valores",), ("Os valores da distribuição",)),
    "regioes": (("Barras empilhadas por região",), ("Os valores por região",)),
    "serie": (("Série histórica",), ("Os valores da série",)),
}
INTERACTIVE_ALTS = EMBED_PARTS["onde"][0] + EMBED_PARTS["ranking"][0]


def _image_re(alts: Sequence[str]):
    return re.compile(r"^!\[(?:" + "|".join(re.escape(a) for a in alts)
                      + r")[^\]]*\]\(data:[^)]*\)[ \t]*$", re.M)


def _table_re(titles: Sequence[str]):
    nomes = "|".join(re.escape(t) for t in titles)
    return re.compile(rf"<details>\n<summary>(?:{nomes})[^<]*</summary>.*?</details>"
                      rf"|\*\*(?:{nomes})[^*\n]*\*\*\n\n(?:\|[^\n]*(?:\n|$))+", re.S)


def for_chat(markdown: str, placeholder: str = "", kinds: Sequence[str] = ("onde", "ranking")) -> str:
    """A versão de CHAT de uma trilha que tem gráfico interativo.

    No chat, quem mostra o gráfico é o embed da Function (com o valor no
    cursor): o PNG e a tabela de valores ali seriam o mesmo dado três vezes
    (pedido de produto, 10/11/2026). Só saem os dos `kinds` que têm embed
    nesta resposta. O Markdown COMPLETO, com o PNG e a tabela, é o que fica
    guardado em `state["sections"]` e vai para o relatório .docx, onde não há
    cursor: lá vale "o desenho e, embaixo, os valores"."""
    alts = [a for k in kinds for a in EMBED_PARTS[k][0]]
    titulos = [t for k in kinds for t in EMBED_PARTS[k][1]]
    texto = _image_re(alts).sub(placeholder, markdown or "") if alts else (markdown or "")
    texto = _table_re(titulos).sub("", texto) if titulos else texto
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


# Até aqui a tabela de valores fica à mostra; acima, recolhida num <details>
# (27 UFs abertas empurram os botões para longe no celular).
_TABELA_ABERTA_ATE = 12


def values_table(facts: Facts, title: str = "Os valores do gráfico") -> str:
    """Os números que o gráfico desenha, um por linha, por extenso.

    O PNG não deixa passar o cursor para ler o valor de uma UF; sem isto, o
    mapa só mostrava a cor e o mínimo e o máximo. Série no tempo vai em ordem
    cronológica; o resto, do maior para o menor, com a fatia do total quando
    somar faz sentido."""
    if facts.n < 2:
        return ""
    f = money_fmt(facts)
    tempo = facts.dimension_kind == "tempo"
    if tempo:
        itens = facts.series()
    else:
        # UF: as mesmas linhas do mapa (sem a de região/total que a tabela "mapa" mistura).
        base = (list(facts.uf_values.items()) if facts.dimension_kind == "uf" and facts.uf_values
                else facts.values)
        itens = sorted(base, key=lambda kv: kv[1], reverse=True)
    total = sum(v for _, v in itens) if (facts.additive and not tempo) else 0
    cabeca = {"uf": "UF", "municipio": "Município", "tempo": "Período"}.get(facts.dimension_kind) or cap(
        fmt.humanize(facts.dimension or "")) or "Item"
    metrica = cap(facts.metric_label or "valor")
    linhas = [f"| {cabeca} | {metrica} |" + (" % do total |" if total else ""),
              "| :-- | --: |" + (" --: |" if total else "")]
    for rotulo, valor in itens:
        nome = fmt.period(rotulo) if tempo else _display(facts, rotulo)
        linha = f"| {str(nome).replace('|', '/')} | {f(valor)} |"
        if total:
            linha += f" {fmt.percent(valor / total, 1)} |"
        linhas.append(linha)
    tabela = "\n".join(linhas)
    if len(itens) <= _TABELA_ABERTA_ATE:
        return f"**{title}**\n\n{tabela}"
    return f"<details>\n<summary>{title} ({len(itens)})</summary>\n\n{tabela}\n\n</details>"


def _tabela(title: str, linhas: List[str], n: int) -> str:
    """Tabela com título: à mostra até 12 linhas, recolhida acima disso."""
    tabela = "\n".join(linhas)
    if n <= _TABELA_ABERTA_ATE:
        return f"**{title}**\n\n{tabela}"
    return f"<details>\n<summary>{title} ({n})</summary>\n\n{tabela}\n\n</details>"


def _names(facts: Facts, itens: Sequence[Tuple[str, float]], limite: int = 4) -> str:
    nomes = [str(_display(facts, r)).replace("|", "/") for r, _ in itens[:limite]]
    resto = len(itens) - limite
    return ", ".join(nomes) + (f" e mais {resto}" if resto > 0 else "")


def expected_range(facts: Facts) -> str:
    """A faixa esperada da regra do IQR, em palavras: "de A a B", ou "até B"
    quando o limite de baixo fica abaixo do menor valor possível (negativo
    para quem não tem valor negativo)."""
    cercas = facts.fences()
    if not cercas:
        return ""
    f = money_fmt(facts)
    baixo, alto = cercas
    if baixo < 0 <= min(v for _, v in facts.values):
        return f"até {f(alto)}"
    return f"de {f(baixo)} a {f(alto)}"


def outliers_text(facts: Facts) -> str:
    """A frase do "fora da curva", que fica no chat e no relatório."""
    fora = facts.outliers()
    if not fora:
        return "**Fora da curva:** nenhum valor destoa dos demais (regra do IQR)."
    f = money_fmt(facts)
    itens = sorted(fora, key=lambda kv: kv[1], reverse=True)
    nomes = [f"{_display(facts, r)} ({f(v)})" for r, v in itens]
    lista = nomes[0] if len(nomes) == 1 else ", ".join(nomes[:-1]) + " e " + nomes[-1]
    return (f"**Fora da curva:** {lista}. Pela regra do IQR, o esperado para estes dados é "
            f"um valor {expected_range(facts)}; fora disso, o valor destoa dos demais.")


def outliers_table(facts: Facts, title: str = "Os valores fora da curva") -> str:
    """Quem está fora da faixa esperada e por quanto."""
    fora, cercas = facts.outliers(), facts.fences()
    if not fora or not cercas:
        return ""
    f = money_fmt(facts)
    baixo, alto = cercas
    cabeca = {"uf": "UF", "municipio": "Município", "tempo": "Período"}.get(facts.dimension_kind) or cap(
        fmt.humanize(facts.dimension or "")) or "Item"
    linhas = [f"| {cabeca} | {cap(facts.metric_label or 'valor')} | Comparado à faixa esperada |",
              "| :-- | --: | :-- |"]
    for rotulo, valor in sorted(fora, key=lambda kv: kv[1], reverse=True):
        nome = fmt.period(rotulo) if facts.dimension_kind == "tempo" else _display(facts, rotulo)
        quanto = (f"{f(valor - alto)} acima do limite de {f(alto)}" if valor > alto
                  else f"{f(baixo - valor)} abaixo do limite de {f(baixo)}")
        linhas.append(f"| {str(nome).replace('|', '/')} | {f(valor)} | {quanto} |")
    return _tabela(title, linhas, len(fora))


def distribution_text(facts: Facts) -> str:
    """Como ler o histograma, com a mediana por extenso."""
    mediana = facts.median()
    if mediana is None or not facts.bins():
        return ""
    return (f"Cada barra junta os itens com valor naquela faixa, e a altura diz quantos são. "
            f"A mediana é {money_fmt(facts)(mediana)}: metade dos {facts.n} valores fica abaixo dela.")


def distribution_table(facts: Facts, title: str = "Os valores da distribuição") -> str:
    """As faixas do histograma, quantos itens há em cada uma e quais são."""
    faixas = facts.bins()
    if not faixas:
        return ""
    f = money_fmt(facts)
    linhas = ["| Faixa | Quantidade | Quem está nela |", "| :-- | --: | :-- |"]
    for de, ate, dentro in faixas:
        linhas.append(f"| {f(de)} a {f(ate)} | {len(dentro)} | {_names(facts, dentro) or '—'} |")
    return _tabela(title, linhas, len(faixas))


def regions_table(facts: Facts, title: str = "Os valores por região") -> str:
    """Total de cada região e o de cada UF dentro dela (o gráfico empilhado)."""
    regioes = facts.region_values()
    if len(regioes) < 2:
        return ""
    f = money_fmt(facts)
    total = sum(sum(u.values()) for u in regioes.values()) or 1
    linhas = ["| Região | Total | % do total | UFs |", "| :-- | --: | --: | :-- |"]
    for regiao, ufs in sorted(regioes.items(), key=lambda kv: -sum(kv[1].values())):
        soma = sum(ufs.values())
        detalhe = "; ".join(f"{s} {f(v)}" for s, v in sorted(ufs.items(), key=lambda kv: -kv[1]))
        linhas.append(f"| {regiao} | {f(soma)} | {fmt.percent(soma / total, 1)} | {detalhe} |")
    return _tabela(title, linhas, len(regioes))


def where_chart(facts: Facts, charts, line_renderer=None) -> Tuple[Optional[bytes], str]:
    """O gráfico do "onde?": mapa para UF, série para tempo, comparação no resto."""
    f = chart_fmt(facts)
    metrica = facts.metric_label
    if facts.dimension_kind == "uf" and facts.uf_values:
        # No mapa o formatador só escreve o mínimo e o máximo da legenda: aí
        # cabe o número inteiro (e é o que vai impresso no relatório).
        png = safe_chart(charts.uf_map, facts.uf_values, money_fmt(facts), title="Onde?",
                         subtitle=f"{cap(metrica)} por UF")
        return png, f"Mapa do Brasil: {metrica} por UF"
    if facts.dimension_kind == "municipio" and facts.mun_values:
        ufs = ", ".join(facts.mun_ufs)
        png = safe_chart(charts.municipal_map, facts.mun_values, facts.mun_ufs, money_fmt(facts),
                         title="Onde?", subtitle=f"{cap(metrica)} por município ({ufs})")
        if png:
            return png, f"Mapa dos municípios: {metrica} ({ufs})"
    if facts.dimension_kind == "tempo" and facts.n >= 2 and line_renderer:
        png = safe_chart(line_renderer, facts)
        return png, f"Série: {metrica} ao longo do tempo"
    if facts.n >= 3:
        ref = facts.mean()
        png = safe_chart(charts.comparison, facts.values, ref, "Média", f,
                         title="Comparado à média", subtitle=f"{cap(metrica)} por "
                         f"{fmt.humanize(facts.dimension or '')}")
        return png, f"Barras: {metrica} comparado à média"
    return None, ""


def _pictogram(facts: Facts, charts) -> str:
    share = facts.share_of_top()
    if not share:
        return ""
    rotulo, fracao = share
    if facts.dimension_kind == "uf":
        from rag.journey import ibge

        uf = ibge.resolve(rotulo)
        rotulo = uf["nome"] if uf else rotulo
    em_dez = max(1, round(fracao * 10))
    unidade = "reais" if facts.money else "do total"
    frase = (f"{rotulo} concentra {em_dez} em cada 10 {unidade}"
             if facts.money else f"{rotulo} concentra {em_dez} em cada 10 ({fmt.percent(fracao)} do total)")
    png = safe_chart(charts.pictogram, em_dez, frase + ".", title="Isso é muito ou pouco?")
    return image(png, frase)


# --------------------------------------------------------------------------- #
# Trilhas
# --------------------------------------------------------------------------- #
def populacao(facts: Facts, phrase: str, scale: Optional[Scale], *, charts,
              tables: Sequence[str], service: str, open_data_url: str = "",
              notes: Sequence[str] = (), line_renderer=None) -> str:
    partes = [phrase.strip()]
    destaque = safe_chart(charts.highlight, headline_text(facts), caption(facts),
                          scale.text if scale else "")
    partes.append(image(destaque, f"{headline_text(facts)}: {caption(facts)}"))
    picto = _pictogram(facts, charts)
    if picto:
        partes.append(picto)
    png, alt = where_chart(facts, charts, line_renderer)
    if png:
        partes.append(image(png, alt))
        partes.append(values_table(facts))
    partes.extend(n for n in notes if n)
    partes.append(source_line(tables, service, open_data_url))
    return "\n\n".join(p for p in partes if p)


def _paragraphs(items: Sequence[str]) -> str:
    """Itens como parágrafos curtos, sem marcador de lista ("- ")."""
    return "\n\n".join(i for i in items if i)


def tecnica(facts: Facts, motor_markdown: str, *, charts, steps, preview_table: str,
            limitations: Sequence[str], csv_url: str = "", open_data_url: str = "") -> str:
    f = chart_fmt(facts)
    partes = ["## Como esse dado foi calculado"]
    partes.append(image(safe_chart(charts.lineage, steps, title="Jornada do dado"),
                        "Jornada do dado: fonte, tabela, colunas e cálculo"))
    partes.append(motor_markdown.strip())
    if preview_table:
        partes.append("### O resultado\n\n" + preview_table)
    valores = [v for _, v in facts.values]
    hist = safe_chart(charts.histogram, valores, f, title="Como os valores se distribuem?",
                      subtitle=f"{len(valores)} valores de {facts.metric_label}")
    if hist:
        partes.append(image(hist, "Histograma dos valores"))
        partes.append(distribution_text(facts))
        partes.append(distribution_table(facts))
    partes.append(image(safe_chart(charts.coverage, facts.coverage, title="O que falta no dado?",
                                   subtitle="% de linhas preenchidas por coluna"),
                        "Cobertura: porcentagem preenchida por coluna"))
    partes.append("### Limitações\n\n" + _paragraphs(limitations))
    links = []
    if csv_url:
        links.append(f"[Baixar o resultado completo (CSV)]({csv_url})")
    if open_data_url:
        links.append(f"[Dataset nos dados abertos]({open_data_url})")
    if links:
        partes.append("\n\n".join(links))
    return "\n\n".join(p for p in partes if p)


def gestor(facts: Facts, *, charts, kpis, region_table: str, planned_text: str) -> str:
    f = chart_fmt(facts)
    metrica = facts.metric_label
    partes = ["## Números para decisão"]
    partes.append(image(safe_chart(charts.kpi_cards, kpis, title="Como estamos?"),
                        "Cartões: " + "; ".join(f"{k[1]} {k[0]}" for k in kpis)))
    if facts.dimension_kind == "tempo":
        serie = facts.series()
        anomalias = [r for r, _ in facts.outliers()]
        png = safe_chart(charts.alerts, serie, anomalias, f, temporal=True,
                         title="Tendência com alerta",
                         subtitle="Pontos fora da curva em laranja" if anomalias else metrica)
        partes.append(image(png, f"Tendência de {metrica}"))
        partes.append(values_table(facts))
    elif facts.n >= 3:
        partes.append(image(safe_chart(charts.ranking, facts.values, f,
                                       title="Quem está melhor e pior?",
                                       subtitle=f"{cap(metrica)}: 3 primeiros em roxo, "
                                                "3 últimos em laranja"),
                            f"Ranking de {metrica}"))
        partes.append(values_table(facts, "Os valores do ranking"))
        fora = facts.outliers()
        if fora:
            partes.append(image(safe_chart(charts.alerts, facts.values, [r for r, _ in fora], f,
                                           title="Tem algo fora da curva?",
                                           subtitle="Valores muito acima ou abaixo dos demais"),
                                "Pontos fora da curva"))
            partes.append(outliers_table(facts))
        partes.append(outliers_text(facts))
    if region_table:
        partes.append("### Comparação com regiões parecidas\n\n" + region_table)
    partes.append("### Executado vs. previsto\n\n" + planned_text)
    return "\n\n".join(p for p in partes if p)


def report_link(url: str) -> str:
    return f"[Baixar o relatório (.docx)]({url})" if url else ""


# --------------------------------------------------------------------------- #
# Relatório
# --------------------------------------------------------------------------- #
_LINHA_DE_LINK = re.compile(r"\]\([^)]*/files/[^)]*\)")      # link assinado de download
_LINK_MD = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
_SUMMARY = re.compile(r"^\s*<summary>(.*?)</summary>\s*$")
_TAG_SOLTA = re.compile(r"^\s*</?\w+[^>]*>\s*$")


def report_markdown(sections: Sequence[Tuple[str, str]]) -> str:
    """Markdown do relatório: as seções que a pessoa abriu, na ordem.

    É o MESMO texto do chat, com o que só faz sentido na tela tirado: links de
    download (o relatório não se baixa de dentro dele), o marcador de estado,
    e o HTML do `<details>` (vira um subtítulo — no Word não há o que recolher).
    """
    from rag.journey.text import plain_typography, strip_emoji

    partes = []
    for track_id, markdown in sections:
        linhas = []
        for linha in (markdown or "").splitlines():
            if linha.startswith("[//]: #") or _LINHA_DE_LINK.search(linha):
                continue
            if linha.strip() == "*Os links valem por 7 dias.*":
                continue
            resumo = _SUMMARY.match(linha)
            if resumo:
                linhas.append(f"### {resumo.group(1).strip()}")
                continue
            if _TAG_SOLTA.match(linha):
                continue
            linhas.append(_LINK_MD.sub(r"\1 (\2)", linha))
        texto = "\n".join(linhas).strip()
        if not texto:
            continue
        if track_id == "populacao":
            texto = "## Resposta\n\n" + texto
        partes.append(texto)
    return plain_typography(strip_emoji("\n\n---\n\n".join(partes)))


_ESTADO_TEXTO = {"usado": "usado nesta resposta", "catalogo": "existe no catálogo",
                 "lacuna": "lacuna, o catálogo não tem"}


def politica(facts: Facts, *, charts, stages: Sequence[Stage], complementary: Sequence[Item],
             series_png: Optional[bytes], series_text: str, series_sql: str,
             methodology: Sequence[str], series_table: str = "") -> str:
    f = chart_fmt(facts)
    partes = ["## Contexto da política"]
    if stages:
        # Embaixo de cada círculo, o ESTADO em duas palavras — nome de tabela
        # não cabe em cinco colunas no celular, e a lista logo abaixo já o traz.
        rotulo = {"usado": "nesta resposta", "catalogo": "no catálogo", "lacuna": "lacuna"}
        steps = [(s.name, s.state, rotulo[s.state]) for s in stages]
        partes.append(image(safe_chart(charts.policy_journey, steps, title="Jornada da política"),
                            "Jornada da política: lei, programa, orçamento, execução e resultado"))
        partes.append(_paragraphs([
            f"**{s.name}:** {_ESTADO_TEXTO[s.state]}"
            + (" (" + ", ".join(f"`{t.split('.')[-1]}`" for t in s.tables[:2]) + ")"
               if s.tables else "") + "."
            for s in stages]))
    partes.append("### Série histórica")
    if series_png:
        partes.append(image(series_png, "Série histórica"))
        partes.append(series_table)
    partes.append(series_text)
    if facts.dimension_kind == "uf" and facts.additive and len(facts.region_values()) >= 2:
        partes.append("### Comparação entre regiões")
        partes.append(image(safe_chart(charts.regions_stacked, facts.region_values(), f,
                                       title="Comparação entre regiões",
                                       subtitle="Total de cada região, dividido pelas suas UFs"),
                            "Barras empilhadas por região"))
        partes.append(regions_table(facts))
    partes.append("### Para quem? Indicadores complementares")
    partes.append(_paragraphs([_complementary_line(i) for i in complementary])
                  or "_Catálogo indisponível agora._")
    partes.append("### Ressalvas de metodologia\n\n" + _paragraphs(methodology))
    lacunas = [s.name for s in stages if s.state == "lacuna"]
    lacunas += [i.name for i in complementary if not i.found]
    partes.append("### O que o sistema não tem\n\n" + (
        "Não há, no catálogo ingerido, dado sobre: " + ", ".join(lacunas)
        + ". Sem dado, o sistema não afirma contexto nem causa."
        if lacunas else "Todas as etapas e recortes procurados têm alguma tabela no catálogo."))
    if series_sql:
        titulo = "SQL da série histórica" if series_png else "SQL tentado para a série histórica"
        partes.append(f"<details>\n<summary>{titulo}</summary>\n\n```sql\n"
                      + series_sql.strip() + "\n```\n</details>")
    return "\n\n".join(p for p in partes if p)


def _complementary_line(item: Item) -> str:
    """Só o nome da coluna (o `tabela.coluna` inteiro estoura a largura do
    celular); as tabelas entram como contagem."""
    if item.found:
        colunas = list(dict.fromkeys(c.split(".")[-1] for c in item.columns))[:3]
        tabelas = len(item.tables)
        return (f"**{item.name}:** há no catálogo "
                + ", ".join(f"`{c}`" for c in colunas)
                + f" (em {tabelas} tabela{'s' if tabelas > 1 else ''}).")
    return f"**{item.name}:** lacuna, nenhuma coluna no catálogo."


def historia(facts: Facts, phrase: str, scale: Optional[Scale], *, charts,
             complementary: Sequence[Item], line_renderer=None) -> str:
    partes = ["## A história do dado"]
    partes.append("### 1. Quanto?\n\n" + phrase.strip())
    partes.append(image(safe_chart(charts.highlight, headline_text(facts), caption(facts)),
                        f"{headline_text(facts)}: {caption(facts)}"))
    significado = ["### 2. O que significa?"]
    if scale:
        significado.append(scale.text)
    picto = _pictogram(facts, charts)
    if picto:
        significado.append(picto)
    if len(significado) == 1:
        significado.append("_Para esta métrica não há comparação em escala humana honesta "
                           "(é uma taxa ou média)._")
    partes.append("\n\n".join(significado))
    png, alt = where_chart(facts, charts, line_renderer)
    partes.append("### 3. Onde?\n\n" + (image(png, alt) + "\n\n" + values_table(facts) if png else
                                         "_O resultado não tem recorte por lugar._"))
    achados = [i for i in complementary if i.found]
    if achados:
        quem = ("O catálogo tem recortes que ajudam a responder **para quem**:\n\n"
                + _paragraphs([_complementary_line(i) for i in achados])
                + "\n\nPergunte, por exemplo, o mesmo número separado por um desses recortes.")
    else:
        quem = ("Para dizer **para quem** foi, faltam dados: o catálogo não tem raça, gênero, "
                "emprego nem renda das pessoas atendidas. Sem dado, a história para aqui.")
    partes.append("### 4. Para quem?\n\n" + quem)
    return "\n\n".join(p for p in partes if p)


def baixar(*, rows: int, csv_url: str, report_url: str, open_data_url: str = "",
           report_parts: Sequence[str] = ()) -> str:
    """Cada arquivo é um link sozinho no parágrafo — o `custom.css` (seção 12)
    transforma link de `/files/` em botão de largura cheia — com a descrição
    logo abaixo, fora do link (dentro dele, o botão ficaria com duas linhas)."""
    blocos = ["## Baixar dados e relatório"]
    if csv_url:
        blocos.append(f"[Resultado completo (CSV)]({csv_url})")
        blocos.append(f"{rows} linhas, separadas por `;` — abre direto no Excel.")
    if report_url:
        blocos.append(f"[Relatório (.docx)]({report_url})")
        conteudo = (" Traz o que você abriu nesta conversa: " + ", ".join(report_parts) + "."
                    if report_parts else "")
        blocos.append("Editável no Word." + conteudo)
    if open_data_url:
        blocos.append(f"[Dados abertos]({open_data_url})")
    if len(blocos) == 1:
        blocos.append("_Não foi possível gerar os arquivos agora. Tente de novo em instantes._")
    else:
        blocos.append("*Os links valem por 7 dias.*")
    return "\n\n".join(blocos)


def preview_table(columns: Sequence[str], rows: Sequence[Dict[str, Any]], max_rows: int = 10,
                  max_columns: int = 6) -> str:
    if not rows:
        return ""
    cols = list(columns)[:max_columns]
    cabecalho = "| " + " | ".join(f"`{c}`" for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    corpo = []
    for row in rows[:max_rows]:
        corpo.append("| " + " | ".join(_cell(row.get(c)) for c in cols) + " |")
    nota = (f"\n\n*Mostrando {min(max_rows, len(rows))} de {len(rows)} linhas"
            + (f" e {len(cols)} de {len(columns)} colunas" if len(columns) > len(cols) else "")
            + ".*")
    return "\n".join([cabecalho, sep] + corpo) + nota


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return fmt.full(value)
    return str(value).replace("|", "\\|").replace("\n", " ")[:40]

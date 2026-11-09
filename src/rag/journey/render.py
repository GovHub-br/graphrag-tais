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

_DIM_PLURAL = {"uf": "UFs", "tempo": "períodos"}


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
    métrica que vai para a tela passa por aqui."""
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
        linhas.append("Maiores: " + " · ".join(f"{r}: {f(v)}" for r, v in facts.top(3)))
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
    linha = (f"*De onde veio:* {nomes} · *catálogo OpenMetadata ({service}) · "
             "calculado agora no banco analítico*")
    if open_data_url:
        linha += f" · [ver nos dados abertos]({open_data_url})"
    return linha


def where_chart(facts: Facts, charts, line_renderer=None) -> Tuple[Optional[bytes], str]:
    """O gráfico do "onde?": mapa para UF, série para tempo, comparação no resto."""
    f = money_fmt(facts)
    metrica = facts.metric_label
    if facts.dimension_kind == "uf" and facts.uf_values:
        png = safe_chart(charts.uf_map, facts.uf_values, f, title="Onde?",
                         subtitle=f"{cap(metrica)} por UF")
        return png, f"Mapa do Brasil: {metrica} por UF"
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
    partes.extend(n for n in notes if n)
    partes.append(source_line(tables, service, open_data_url))
    return "\n\n".join(p for p in partes if p)


def tecnica(facts: Facts, motor_markdown: str, *, charts, steps, preview_table: str,
            limitations: Sequence[str], csv_url: str = "", open_data_url: str = "") -> str:
    f = money_fmt(facts)
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
    partes.append(image(safe_chart(charts.coverage, facts.coverage, title="O que falta no dado?",
                                   subtitle="% de linhas preenchidas por coluna"),
                        "Cobertura: porcentagem preenchida por coluna"))
    partes.append("### Limitações\n\n" + "\n".join(f"- {l}" for l in limitations))
    links = []
    if csv_url:
        links.append(f"[Baixar o resultado completo (CSV)]({csv_url})")
    if open_data_url:
        links.append(f"[Dataset nos dados abertos]({open_data_url})")
    if links:
        partes.append("\n\n".join(links))
    return "\n\n".join(p for p in partes if p)


def gestor(facts: Facts, *, charts, kpis, region_table: str, planned_text: str) -> str:
    f = money_fmt(facts)
    metrica = facts.metric_label
    partes = ["## Números para decisão"]
    partes.append(image(safe_chart(charts.kpi_cards, kpis, title="Como estamos?"),
                        "Cartões: " + " · ".join(f"{k[1]} {k[0]}" for k in kpis)))
    if facts.dimension_kind == "tempo":
        serie = facts.series()
        anomalias = [r for r, _ in facts.outliers()]
        png = safe_chart(charts.alerts, serie, anomalias, f, temporal=True,
                         title="Tendência com alerta",
                         subtitle="Pontos fora da curva em laranja" if anomalias else metrica)
        partes.append(image(png, f"Tendência de {metrica}"))
    elif facts.n >= 3:
        partes.append(image(safe_chart(charts.ranking, facts.values, f,
                                       title="Quem está melhor e pior?",
                                       subtitle=f"{cap(metrica)} — 3 primeiros em roxo, "
                                                "3 últimos em laranja"),
                            f"Ranking de {metrica}"))
        fora = facts.outliers()
        if fora:
            partes.append(image(safe_chart(charts.alerts, facts.values, [r for r, _ in fora], f,
                                           title="Tem algo fora da curva?",
                                           subtitle="Valores muito acima ou abaixo dos demais"),
                                "Pontos fora da curva"))
            partes.append("**Fora da curva:** " + ", ".join(f"{r} ({f(v)})" for r, v in fora) + ".")
        else:
            partes.append("**Fora da curva:** nenhum valor destoa dos demais (regra do IQR).")
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
    from rag.journey.text import strip_emoji

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
    return strip_emoji("\n\n---\n\n".join(partes))


_ESTADO_TEXTO = {"usado": "usado nesta resposta", "catalogo": "existe no catálogo",
                 "lacuna": "lacuna — o catálogo não tem"}


def politica(facts: Facts, *, charts, stages: Sequence[Stage], complementary: Sequence[Item],
             series_png: Optional[bytes], series_text: str, series_sql: str,
             methodology: Sequence[str]) -> str:
    f = money_fmt(facts)
    partes = ["## Contexto da política"]
    if stages:
        # Embaixo de cada círculo, o ESTADO em duas palavras — nome de tabela
        # não cabe em cinco colunas no celular, e a lista logo abaixo já o traz.
        rotulo = {"usado": "nesta resposta", "catalogo": "no catálogo", "lacuna": "lacuna"}
        steps = [(s.name, s.state, rotulo[s.state]) for s in stages]
        partes.append(image(safe_chart(charts.policy_journey, steps, title="Jornada da política"),
                            "Jornada da política: lei, programa, orçamento, execução e resultado"))
        partes.append("\n".join(
            f"- **{s.name}** — {_ESTADO_TEXTO[s.state]}"
            + (": " + ", ".join(f"`{t.split('.')[-1]}`" for t in s.tables[:2]) if s.tables else "")
            for s in stages))
    partes.append("### Série histórica")
    if series_png:
        partes.append(image(series_png, "Série histórica"))
    partes.append(series_text)
    if facts.dimension_kind == "uf" and facts.additive and len(facts.region_values()) >= 2:
        partes.append("### Comparação entre regiões")
        partes.append(image(safe_chart(charts.regions_stacked, facts.region_values(), f,
                                       title="Comparação entre regiões",
                                       subtitle="Total de cada região, dividido pelas suas UFs"),
                            "Barras empilhadas por região"))
    partes.append("### Para quem? Indicadores complementares")
    partes.append("\n".join(_complementary_line(i) for i in complementary)
                  or "_Catálogo indisponível agora._")
    partes.append("### Ressalvas de metodologia\n\n" + "\n".join(f"- {m}" for m in methodology))
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
        return (f"- **{item.name}** — há no catálogo: "
                + ", ".join(f"`{c}`" for c in colunas)
                + f" (em {tabelas} tabela{'s' if tabelas > 1 else ''})")
    return f"- **{item.name}** — lacuna: nenhuma coluna no catálogo"


def historia(facts: Facts, phrase: str, scale: Optional[Scale], *, charts,
             complementary: Sequence[Item], line_renderer=None) -> str:
    partes = ["## A história do dado"]
    partes.append("### 1 · Quanto?\n\n" + phrase.strip())
    partes.append(image(safe_chart(charts.highlight, headline_text(facts), caption(facts)),
                        f"{headline_text(facts)}: {caption(facts)}"))
    significado = ["### 2 · O que significa?"]
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
    partes.append("### 3 · Onde?\n\n" + (image(png, alt) if png else
                                          "_O resultado não tem recorte por lugar._"))
    achados = [i for i in complementary if i.found]
    if achados:
        quem = ("O catálogo tem recortes que ajudam a responder **para quem**:\n\n"
                + "\n".join(_complementary_line(i) for i in achados)
                + "\n\nPergunte, por exemplo, o mesmo número separado por um desses recortes.")
    else:
        quem = ("Para dizer **para quem** foi, faltam dados: o catálogo não tem raça, gênero, "
                "emprego nem renda das pessoas atendidas. Sem dado, a história para aqui.")
    partes.append("### 4 · Para quem?\n\n" + quem)
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

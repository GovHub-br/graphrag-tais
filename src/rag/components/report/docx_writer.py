"""Primitivas de renderização `.docx` com o tema GovHub.

O caminho principal do relatório é **estruturado** (`report_content.render_answer`,
a partir do `answer` do motor), não parsing de Markdown. As primitivas aqui
(`add_heading`, `add_code_box`, `add_dictionary_table`, `add_callout`, `add_chart`,
`add_inline`, `add_mono_chip`, `add_type_badge`) são usadas por esse caminho e
também pelo conversor de Markdown legado (`render_markdown`), mantido como
fallback e para testes.

No caminho Markdown (o da jornada), `## Título` abre um CAPÍTULO
(`theme.add_chapter`: página nova, numeral, eyebrow, título roxo) e tabelas e
figuras ganham legenda numerada no documento inteiro ("Tabela 3: …",
"Figura 2. …"), como no relatório sóbrio da IDV (print-table.md).

`add_inline` é **recursivo e robusto**: `**negrito**` pode conter `` `código` ``, e
`_` NÃO é tratado como itálico (identificadores como `api_ibge_uf` quebravam o
parser antigo, deixando marcação crua no documento).
"""
from __future__ import annotations

import base64
import re
from io import BytesIO
from typing import List, Optional

from docx.document import Document as _Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Mm, Pt, RGBColor
from loguru import logger

from rag.components.report import theme

def _next(doc: _Document, kind: str) -> int:
    """Numeração sequencial no documento inteiro (Tabela 1, 2, 3…; não
    reinicia por capítulo — print-table.md)."""
    counters = doc.__dict__.setdefault("_gh_counters", {})
    counters[kind] = counters.get(kind, 0) + 1
    return counters[kind]


# --------------------------------------------------------------------------- #
# Inline (recursivo)
# --------------------------------------------------------------------------- #
_INLINE_RE = re.compile(r"(\*\*.+?\*\*|__.+?__|`[^`]+`|\*[^*]+\*)")


def _emit_run(paragraph, text: str, color, bold: bool, italic: bool):
    run = paragraph.add_run(text)
    theme.set_run_font(run, theme.FONT_BASE)
    run.bold = bold
    run.italic = italic
    if color is not None:
        run.font.color.rgb = color
    return run


def add_inline(paragraph, text: str, color: RGBColor | None = None,
               mono_color: RGBColor | None = None, bold: bool = False,
               italic: bool = False) -> None:
    """Escreve `text` resolvendo ênfases inline em runs, recursivamente."""
    mono = mono_color if mono_color is not None else theme.PURPLE_DARK
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            _emit_run(paragraph, text[pos:m.start()], color, bold, italic)
        tok = m.group(0)
        if tok.startswith("**") or tok.startswith("__"):
            add_inline(paragraph, tok[2:-2], color, mono_color, True, italic)
        elif tok.startswith("`"):
            add_mono_chip(paragraph, tok[1:-1], mono,
                          shaded=(mono != theme.WHITE), bold=bold, italic=italic)
        else:  # *itálico*
            add_inline(paragraph, tok[1:-1], color, mono_color, bold, True)
        pos = m.end()
    if pos < len(text):
        _emit_run(paragraph, text[pos:], color, bold, italic)


def add_mono_chip(paragraph, text: str, color: RGBColor | None = None,
                  shaded: bool = True, bold: bool = False, italic: bool = False):
    """Run de código monoespaçado, com fundo lavanda (chip)."""
    run = paragraph.add_run(" " + text.strip() + " ")  # thin spaces = respiro
    theme.set_run_font(run, theme.FONT_MONO)
    run.font.size = Pt(9)
    run.font.color.rgb = color if color is not None else theme.PURPLE_DARK
    run.bold = bold
    run.italic = italic
    if shaded:
        theme.set_run_shading(run, theme.FILL_CODE)
    return run


# --------------------------------------------------------------------------- #
# Badges de tipo (Texto/Número/Data/Booleano), como no PDF de referência do IPEA
# --------------------------------------------------------------------------- #
_TYPE_CATEGORY = {
    "texto": {"text", "varchar", "char", "character", "string", "uuid", "json", "jsonb", "citext"},
    "numero": {"int", "integer", "bigint", "smallint", "numeric", "decimal", "float",
               "double", "real", "money", "serial", "number"},
    "data": {"date", "timestamp", "timestamptz", "time", "datetime", "interval"},
    "booleano": {"bool", "boolean"},
}


def type_category(token: str) -> str | None:
    t = (token or "").strip().lower().strip("`")
    # normaliza "varchar(255)" → "varchar", "numeric(10,2)" → "numeric"
    t = re.sub(r"\(.*\)$", "", t).strip()
    for cat, names in _TYPE_CATEGORY.items():
        if t in names:
            return cat
    return None


def add_type_badge(paragraph, type_str: str) -> None:
    cat = type_category(type_str)
    if not cat:
        _emit_run(paragraph, (type_str or "—").strip(), theme.TEXT_BODY, False, False)
        return
    fill, color = theme.CATEGORY_FILLS[cat]
    run = paragraph.add_run(" " + type_str.strip() + " ")
    run.bold = True
    run.font.size = Pt(8)
    run.font.color.rgb = color
    theme.set_run_font(run, theme.FONT_BASE)
    theme.set_run_shading(run, fill)


# --------------------------------------------------------------------------- #
# Blocos
# --------------------------------------------------------------------------- #
def _thin_gap(doc: _Document) -> None:
    """Separador mínimo entre blocos/tabelas (evita fusão de tabelas adjacentes
    no docx sem deixar uma linha em branco cheia)."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.0
    run = p.add_run()
    run.font.size = Pt(4)


def add_heading(doc: _Document, text: str, level: int = 3):
    """Subtítulo do corpo. `###` (o nível que o motor e as trilhas usam) vira
    Heading 2; `####`, Heading 3. O Heading 1 é do título de capítulo."""
    style = "Heading 2" if level <= 3 else "Heading 3"
    p = doc.add_paragraph(style=style)
    # bold explícito: `add_inline` grava bold=False em cada run, e o run vence
    # o negrito do estilo — o subtítulo saía em peso normal
    add_inline(p, text, color=theme.NAVY, mono_color=theme.PURPLE_DARK, bold=True)
    return p


def add_body_text(doc: _Document, text: str) -> None:
    """Texto livre (explicação): parágrafos separados por linha em branco."""
    for block in re.split(r"\n\s*\n", (text or "").strip()):
        block = " ".join(ln.strip() for ln in block.splitlines()).strip()
        if block:
            _body_paragraph(doc, block)


def _body_paragraph(doc: _Document, text: str, justify: bool = True):
    """Parágrafo de texto corrido. Justificado como na IDV — menos quando tem
    código inline ou uma palavra muito longa (nome de tabela sem crase): ela
    não quebra, e o Word abria buracos enormes entre as palavras da linha. Um parágrafo inteiro entre `_…_` (a
    descrição de tabela do catálogo) sai em itálico, sem os sublinhados — o
    `_` não é ênfase dentro do texto, só nas pontas."""
    p = doc.add_paragraph()
    longa = max((len(w) for w in text.split()), default=0) > 24   # nome de tabela solto
    if justify and "`" not in text and not longa:
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    if len(text) > 2 and text[0] == text[-1] == "_" and not text.startswith("__"):
        add_inline(p, text[1:-1], italic=True, color=theme.TEXT_MUTED)
    else:
        add_inline(p, text)
    return p


def add_muted(doc: _Document, text: str, italic: bool = True, size: float = 9.5):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.italic = italic
    r.font.size = Pt(size)
    r.font.color.rgb = theme.TEXT_MUTED
    theme.set_run_font(r, theme.FONT_BASE)
    return p


def add_code_box(doc: _Document, code) -> None:
    """Caixa de código monoespaçada com fundo (cell shading → renderiza sempre)."""
    lines = code.split("\n") if isinstance(code, str) else list(code)
    table = doc.add_table(rows=1, cols=1)
    theme.clear_table_borders(table)
    cell = table.cell(0, 0)
    theme.set_table_width(table, theme.BODY_WIDTH, [theme.BODY_WIDTH])
    theme.shade_cell(cell, theme.FILL_CODE)
    theme.set_cell_borders(cell, theme.FILL_CODE, size=4, left_accent_size=18,
                           edges=(), left_color=theme.FILL_HEADER)
    theme.set_cell_margins(cell, top=110, bottom=110, left=180, right=180)
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.25
    for idx, line in enumerate(lines):
        if idx > 0:
            p.add_run().add_break()
        run = p.add_run(line)
        theme.set_run_font(run, theme.FONT_MONO)
        run.font.size = Pt(8.5)
        run.font.color.rgb = theme.NAVY
    _thin_gap(doc)


def add_callout(doc: _Document, text: str | None = None, build=None,
                fill: str = theme.FILL_SOFT) -> None:
    """Callout: fundo #F2F1F6, contorno fino e barra de acento navy à esquerda
    (a cor de seção do PDF sóbrio é sempre navy)."""
    table = doc.add_table(rows=1, cols=1)
    theme.clear_table_borders(table)
    theme.set_table_width(table, theme.BODY_WIDTH, [theme.BODY_WIDTH])
    cell = table.cell(0, 0)
    theme.shade_cell(cell, fill)
    theme.set_cell_borders(cell, theme.BORDER_SOFT, size=4, left_accent_size=24,
                           left_color=theme.FILL_HEADER)
    theme.set_cell_margins(cell, top=120, bottom=120,
                           left=int(Inches(0.18).twips), right=int(Inches(0.18).twips))
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    if build is not None:
        build(p)
    elif text is not None:
        add_inline(p, text, color=theme.TEXT_BODY)
    _thin_gap(doc)


#: Teto de altura de figura: um ranking de 27 UFs em largura cheia passaria
#: de uma página e seria cortado; acima disto, a figura encolhe na proporção.
_MAX_FIG_H_MM = 175.0


def add_chart(doc: _Document, data_uri: str, caption: str = "") -> bool:
    """Embute um PNG base64 (data-uri), centrado, com legenda numerada embaixo
    ("Figura N. …") quando há `caption`. Devolve True se conseguiu."""
    if not data_uri or "base64," not in data_uri:
        return False
    b64 = data_uri.split(",", 1)[1]
    try:
        raw = base64.b64decode(b64)
        width_mm = 160.0
        try:
            from PIL import Image

            w, h = Image.open(BytesIO(raw)).size
            if w and h * width_mm / w > _MAX_FIG_H_MM:
                width_mm = _MAX_FIG_H_MM * w / h
        except Exception:  # noqa: BLE001 — sem medir, vale a largura padrão
            pass
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(6)
        p.paragraph_format.space_after = Pt(4 if caption else 10)
        p.paragraph_format.keep_with_next = bool(caption)
        p.add_run().add_picture(BytesIO(raw), width=Mm(width_mm))
        if caption:
            cap = doc.add_paragraph()
            cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
            cap.paragraph_format.space_after = Pt(12)
            theme.styled_run(cap, f"Figura {_next(doc, 'figura')}. ", size=9,
                             color=theme.NAVY, bold=True)
            theme.styled_run(cap, caption.strip().rstrip("."), size=9, color=theme.TEXT_MUTED)
        return True
    except Exception as e:  # noqa: BLE001 — gráfico é aditivo
        logger.warning("[report] falha ao embutir gráfico: {}", e)
        return False


def add_table_caption(doc: _Document, text: str) -> None:
    """Legenda ACIMA da tabela, navy, numerada no documento (print-table.md)."""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(5)
    p.paragraph_format.keep_with_next = True
    theme.styled_run(p, f"Tabela {_next(doc, 'tabela')}: ", size=9.5, color=theme.NAVY, bold=True)
    theme.styled_run(p, text.strip().rstrip("."), size=9.5, color=theme.NAVY)


def add_dictionary_table(doc: _Document, table_name: str, columns: List[dict]) -> None:
    """Tabela do dicionário de dados a partir de colunas ESTRUTURADAS
    (`{name, type, is_pk, is_join_key}`) — sem parsing de Markdown."""
    pad = dict(top=70, bottom=70, left=140, right=140)
    table = doc.add_table(rows=1, cols=2)
    theme.clear_table_borders(table)
    theme.set_table_width(table, theme.BODY_WIDTH, [Mm(100), Mm(70)])
    theme.repeat_header(table.rows[0])

    h0, h1 = table.rows[0].cells
    for c in (h0, h1):
        theme.shade_cell(c, theme.FILL_HEADER)
        theme.set_cell_margins(c, **pad)
        c.paragraphs[0].paragraph_format.space_after = Pt(0)
    r0 = h0.paragraphs[0].add_run("📋 ")
    r0.bold = True
    r0.font.color.rgb = theme.WHITE
    theme.set_run_font(r0, theme.FONT_BASE)
    add_mono_chip(h0.paragraphs[0], table_name, color=theme.WHITE, shaded=False, bold=True)
    r1 = h1.paragraphs[0].add_run("Tipo e chave")
    r1.bold = True
    r1.font.color.rgb = theme.WHITE
    theme.set_run_font(r1, theme.FONT_BASE)

    for i, col in enumerate(columns):
        row = table.add_row()
        c0, c1 = row.cells
        for c in (c0, c1):
            theme.set_cell_margins(c, **pad)
            c.paragraphs[0].paragraph_format.space_after = Pt(0)
            if i % 2 == 1:
                theme.shade_cell(c, theme.FILL_ZEBRA)
        add_mono_chip(c0.paragraphs[0], col.get("name", ""))
        p1 = c1.paragraphs[0]
        add_type_badge(p1, col.get("type", "") or "—")
        keys = []
        if col.get("is_pk"):
            keys.append("🔑 PK")
        if col.get("is_join_key"):
            keys.append("🔗 join")
        if keys:
            rk = p1.add_run("  " + " · ".join(keys))
            rk.font.size = Pt(9)
            rk.font.color.rgb = theme.TEXT_MUTED
            theme.set_run_font(rk, theme.FONT_BASE)
        theme.set_row_bottom_border(row, theme.BORDER_SOFT, size=4)
    _thin_gap(doc)


# --------------------------------------------------------------------------- #
# Conversor de Markdown legado (fallback / testes)
# --------------------------------------------------------------------------- #
_IMG_RE = re.compile(r"!\[([^\]]*)\]\((data:image/[^;]+;base64,[^)]+)\)")
_IMG_ANY_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def add_table_cell(paragraph, text: str) -> None:
    """Célula de corpo (caminho Markdown): tipo conhecido → badge; resto inline."""
    text = text.strip()
    head = re.split(r"\s*·\s*", text, maxsplit=1)
    if head and type_category(head[0]):
        add_type_badge(paragraph, head[0])
        if len(head) > 1 and head[1].strip():
            paragraph.add_run("  ")
            add_inline(paragraph, head[1].strip())
    else:
        add_inline(paragraph, text)


def render_markdown(doc: _Document, markdown: str, eyebrows: Optional[dict] = None,
                    chapters: bool = False, first_chapter: int = 1,
                    first_on_new_page: bool = False) -> None:
    """Markdown → corpo do `.docx`.

    Com `chapters=True` (o relatório da jornada), cada `## Título` abre um
    capítulo numerado a partir de `first_chapter` — página nova, eyebrow de
    `eyebrows[título]` (ou "Seção") — e tabelas e figuras ganham legenda
    numerada: a tabela leva o último subtítulo (`###`) como descrição; a
    figura, o texto alternativo da imagem. Sem `chapters`, o comportamento
    antigo (`##` é só um subtítulo, sem legendas), que é o dos testes e do
    fallback da Persona 2."""
    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    i, n = 0, len(lines)
    buf: List[str] = []
    numero = first_chapter
    contexto = {"capitulo": "", "sub": ""}
    novo_capitulo_em_pagina_nova = first_on_new_page

    def flush():
        if buf:
            text = " ".join(s.strip() for s in buf).strip()
            buf.clear()
            if text:
                _body_paragraph(doc, text, justify=chapters)
                # Linha inteira em negrito ("**Os valores fora da curva**") é
                # rótulo do que vem embaixo: descreve a próxima tabela.
                rotulo = re.fullmatch(r"\*\*([^*]+)\*\*:?", text)
                if rotulo:
                    contexto["sub"] = rotulo.group(1).strip().rstrip(":")

    def proxima_linha(j: int) -> str:
        while j < n and not lines[j].strip():
            j += 1
        return lines[j].strip() if j < n else ""

    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            flush(); i += 1; continue
        if s.startswith("```"):
            flush(); i = _consume_code_block(doc, lines, i); continue
        if s.startswith("|") and i + 1 < n and _is_table_separator(lines[i + 1]):
            flush()
            legenda = (contexto["sub"] or contexto["capitulo"]) if chapters else ""
            i = _consume_table(doc, lines, i, caption=legenda); continue
        if s.startswith("#"):
            flush()
            level = len(s) - len(s.lstrip("#"))
            titulo = s[level:].strip()
            if chapters and level <= 2:
                theme.add_chapter(doc, numero, (eyebrows or {}).get(titulo, "Seção"),
                                  titulo, new_page=novo_capitulo_em_pagina_nova)
                novo_capitulo_em_pagina_nova = True
                numero += 1
                contexto.update(capitulo=titulo, sub="")
            else:
                add_heading(doc, titulo, level)
                contexto["sub"] = titulo
            i += 1; continue
        if s in ("---", "***", "___"):
            flush()
            # entre capítulos a página nova já separa: o fio seria redundante
            if not (chapters and proxima_linha(i + 1).startswith("## ")):
                _add_rule(doc)
            i += 1; continue
        if s.startswith("!["):
            flush(); _add_image_line(doc, s, captions=chapters); i += 1; continue
        if s.startswith(">"):
            flush(); add_callout(doc, text=s.lstrip(">").strip()); i += 1; continue
        if s.startswith("- ") or s.startswith("* "):
            flush(); _add_bullet(doc, s[2:].strip()); i += 1; continue
        buf.append(line); i += 1
    flush()


def _add_rule(doc: _Document) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    p = doc.add_paragraph()
    pPr = p._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), theme.BORDER_SOFT)
    borders.append(bottom)
    pPr.append(borders)


def _add_bullet(doc: _Document, text: str) -> None:
    try:
        p = doc.add_paragraph(style="List Bullet")
    except KeyError:
        p = doc.add_paragraph()
        p.add_run("• ")
    add_inline(p, text)


def _add_image_line(doc: _Document, line: str, captions: bool = False) -> None:
    m = _IMG_RE.search(line)
    if m and add_chart(doc, m.group(2), caption=m.group(1) if captions else ""):
        return
    any_m = _IMG_ANY_RE.search(line)
    if any_m and not any_m.group(1).startswith("data:"):
        add_muted(doc, "[imagem externa omitida no relatório]")


def _is_table_separator(line: str) -> bool:
    s = line.strip()
    if not s.startswith("|"):
        return False
    cells = _split_row(s)
    return bool(cells) and all(re.fullmatch(r":?-{1,}:?", c.strip()) for c in cells)


def _split_row(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return s.split("|")


_NUMERO = re.compile(r"^[-+−]?\s*(R\$\s*)?[\d.,]+\s*(%|mi|bi|mil|p\.p\.)?$")


def _is_number(text: str) -> bool:
    t = re.sub(r"[*`]", "", text or "").strip()
    return bool(t) and bool(_NUMERO.match(t))


def _consume_table(doc: _Document, lines: List[str], start: int, caption: str = "") -> int:
    header = _split_row(lines[start])
    ncols = len(header)
    i = start + 2
    body: List[List[str]] = []
    while i < len(lines) and lines[i].strip().startswith("|"):
        row = (_split_row(lines[i]) + [""] * ncols)[:ncols]
        body.append(row); i += 1

    # Coluna de número alinha à direita (dígito sob dígito): decide pela
    # maioria das células do corpo, não pelo cabeçalho.
    numeric = [bool(body) and sum(_is_number(r[c]) for r in body) > len(body) / 2
               for c in range(ncols)]

    if caption:
        add_table_caption(doc, caption)
    pad = dict(top=70, bottom=70, left=140, right=140)
    table = doc.add_table(rows=1, cols=ncols)
    theme.clear_table_borders(table)
    theme.set_table_width(table, theme.BODY_WIDTH)
    theme.repeat_header(table.rows[0])
    for c, (cell, txt) in enumerate(zip(table.rows[0].cells, header)):
        theme.shade_cell(cell, theme.FILL_HEADER)
        theme.set_cell_margins(cell, **pad)
        para = cell.paragraphs[0]; para.text = ""; para.paragraph_format.space_after = Pt(0)
        para.paragraph_format.line_spacing = 1.15
        if numeric[c]:
            para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        add_inline(para, txt.strip(), color=theme.WHITE, mono_color=theme.WHITE)
        for run in para.runs:
            run.bold = True
            run.font.size = Pt(9)
    for ridx, row in enumerate(body):
        docx_row = table.add_row()
        theme.keep_row_together(docx_row)
        for c, (cell, txt) in enumerate(zip(docx_row.cells, row)):
            if ridx % 2 == 1:
                theme.shade_cell(cell, theme.FILL_ZEBRA)
            theme.set_cell_margins(cell, **pad)
            para = cell.paragraphs[0]; para.text = ""; para.paragraph_format.space_after = Pt(0)
            para.paragraph_format.line_spacing = 1.15
            if numeric[c]:
                para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            add_table_cell(para, txt.strip())
            for run in para.runs:
                if run.font.size is None:
                    run.font.size = Pt(9.5)
        theme.set_row_bottom_border(docx_row, theme.BORDER_SOFT, size=4)
    _thin_gap(doc)
    return i


def _consume_code_block(doc: _Document, lines: List[str], start: int) -> int:
    i = start + 1
    code_lines: List[str] = []
    while i < len(lines) and not lines[i].strip().startswith("```"):
        code_lines.append(lines[i]); i += 1
    i += 1
    add_code_box(doc, code_lines)
    return i


# --------------------------------------------------------------------------- #
# Página de identificação (antes do primeiro capítulo)
# --------------------------------------------------------------------------- #
def add_about(doc: _Document, rows: List[tuple], contents: List[tuple], note: str = "") -> None:
    """"Sobre este relatório": ficha de identificação (rótulo navy sobre o
    claro, valor ao lado, fio fino entre linhas), o sumário dos capítulos com
    o numeral de cada um e uma nota. Sem número de página no sumário: o
    documento é editável e a página mudaria a cada edição."""
    theme.add_chapter(doc, None, "Sobre este documento", "Identificação", new_page=False)

    table = doc.add_table(rows=0, cols=2)
    theme.clear_table_borders(table)
    for label, value in rows:
        row = table.add_row()
        theme.keep_row_together(row)
        c0, c1 = row.cells
        theme.shade_cell(c0, theme.FILL_SOFT)
        for c in (c0, c1):
            theme.set_cell_margins(c, top=110, bottom=110, left=160, right=160)
        p0 = theme.tight(c0.paragraphs[0], line=1.2)
        theme.styled_run(p0, label, size=9.5, color=theme.NAVY, bold=True)
        p1 = theme.tight(c1.paragraphs[0], line=1.3)
        add_inline(p1, value)
        theme.set_row_bottom_border(row, "FFFFFF", size=12)
    theme.set_table_width(table, theme.BODY_WIDTH, [Mm(42), Mm(128)])
    _thin_gap(doc)

    if contents:
        h = doc.add_paragraph(style="Heading 2")
        theme.styled_run(h, "Neste relatório", size=14, color=theme.NAVY, bold=True)
        lista = doc.add_table(rows=0, cols=2)
        theme.clear_table_borders(lista)
        for numero, titulo, eyebrow in contents:
            row = lista.add_row()
            c0, c1 = row.cells
            for c in (c0, c1):
                theme.set_cell_margins(c, top=90, bottom=90, left=0, right=120)
            p0 = theme.tight(c0.paragraphs[0], line=1.0)
            theme.styled_run(p0, f"{numero:02d}", font=theme.FONT_DISPLAY, size=16,
                             color=theme.NAVY_45, bold=True)
            p1 = theme.tight(c1.paragraphs[0], line=1.15)
            theme.styled_run(p1, titulo, size=11.5, color=theme.NAVY, bold=True)
            if eyebrow:
                p2 = theme.tight(c1.add_paragraph(), line=1.1)
                theme.styled_run(p2, eyebrow, size=8, color=theme.NAVY_70, bold=True,
                                 spacing=1.2, caps=True)
            theme.set_row_bottom_border(row, theme.BORDER_SOFT, size=4)
        theme.set_table_width(lista, theme.BODY_WIDTH, [Mm(18), Mm(152)])
        _thin_gap(doc)

    if note:
        add_callout(doc, text=note)

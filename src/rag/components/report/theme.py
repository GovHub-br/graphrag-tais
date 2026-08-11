"""Tema visual GovHub para o relatório `.docx`, aplicado por código.

Recria em `python-docx` o **estilo editorial** do GovHub (o do livro/guia prático e
dos PDFs de dicionário de dados): capa roxa full-bleed com logo branca, faixa/þítulos
roxos, tabelas elegantes sem bordas verticais, chips de código, callouts e rodapé com
o símbolo da marca. A identidade só existe como CSS/HTML (`govhub-visual-identity`),
não há asset `.docx` — por isso é reconstruída aqui.

Alvo é um documento **editável** no Word, então algumas coisas do PDF (cantos
arredondados, gradiente) não têm equivalente e ficam de fora de propósito; o que
sobrevive bem (cor sólida, tipografia, tabelas, shading) é reproduzido fielmente.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from docx.document import Document as _Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor
from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------- #
# Paleta (govhub-visual-identity/references/palette.md)
# --------------------------------------------------------------------------- #
PURPLE = RGBColor(0x7A, 0x34, 0xF3)        # roxo do site — títulos, tabelas, badges
LOGO_PURPLE = RGBColor(0x75, 0x21, 0xF9)   # roxo do arquivo da logo — fundo da capa
PURPLE_DARK = RGBColor(0x5B, 0x21, 0xB6)   # texto roxo pequeno (contraste AA)
ORANGE = RGBColor(0xF9, 0x73, 0x16)
WARM = RGBColor(0xF1, 0x9F, 0x42)
SUCCESS = RGBColor(0x10, 0xB9, 0x81)
TEXT_STRONG = RGBColor(0x20, 0x20, 0x20)
TEXT_BODY = RGBColor(0x2D, 0x37, 0x48)
TEXT_MUTED = RGBColor(0x66, 0x66, 0x66)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

# fills (hex sem #, para o w:shd)
FILL_HEADER = "7A34F3"    # header de tabela (roxo)
FILL_ZEBRA = "F5F2FE"     # linha alternada (lavanda bem clara)
FILL_CODE = "F1ECFB"      # chip de código inline / bloco
BORDER_SOFT = "E2E0EA"    # divisórias finas
CATEGORY_FILLS = {        # badges de tipo (mesma lógica do PDF de referência)
    "texto": ("EDE7FB", PURPLE_DARK),
    "numero": ("E5DBFA", PURPLE_DARK),
    "data": ("FCE9D6", RGBColor(0xB5, 0x5A, 0x12)),
    "booleano": ("D6F3E8", RGBColor(0x0A, 0x7A, 0x55)),
}

FONT_BASE = "Inter"
FONT_MONO = "Consolas"

_ASSETS = Path(__file__).parent / "assets"
_LOGO_LIGHT = _ASSETS / "logo_govhub_light.png"   # branca, p/ a capa roxa
_SYMBOL = _ASSETS / "symbol_govhub.png"           # símbolo roxo, p/ o rodapé
_FONTS = _ASSETS / "fonts"                        # Inter (OFL) p/ desenhar a capa


# --------------------------------------------------------------------------- #
# Helpers XML de baixo nível
# --------------------------------------------------------------------------- #
def set_run_font(run, name: str = FONT_BASE) -> None:
    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs"):
        rfonts.set(qn(attr), name)


def _shd(hex_fill: str):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    return shd


def shade_cell(cell, hex_fill: str) -> None:
    cell._tc.get_or_add_tcPr().append(_shd(hex_fill))


def shade_paragraph(paragraph, hex_fill: str) -> None:
    paragraph._p.get_or_add_pPr().append(_shd(hex_fill))


def set_run_shading(run, hex_fill: str) -> None:
    """Fundo atrás de um run (aproxima o 'chip' de código; sem cantos arredondados)."""
    run._element.get_or_add_rPr().append(_shd(hex_fill))


def set_cell_margins(cell, top=0, left=0, bottom=0, right=0) -> None:
    """Margens internas da célula em twips (padding)."""
    tcPr = cell._tc.get_or_add_tcPr()
    mar = OxmlElement("w:tcMar")
    for name, val in (("top", top), ("start", left), ("bottom", bottom), ("end", right)):
        node = OxmlElement(f"w:{name}")
        node.set(qn("w:w"), str(int(val)))
        node.set(qn("w:type"), "dxa")
        mar.append(node)
    tcPr.append(mar)


def clear_table_borders(table) -> None:
    tblPr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:val"), "none")
        borders.append(e)
    tblPr.append(borders)


def set_cell_borders(cell, hex_color: str, size: int = 8, left_accent_size: int = 0) -> None:
    """Borda ao redor da célula (usada no callout box). `left_accent_size` engrossa
    só a borda esquerda (barra de acento), estilo callout do GovHub."""
    tcPr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:val"), "single")
        sz = left_accent_size if (edge == "left" and left_accent_size) else size
        e.set(qn("w:sz"), str(sz))
        e.set(qn("w:color"), hex_color)
        borders.append(e)
    tcPr.append(borders)


def set_row_bottom_border(row, hex_color: str = BORDER_SOFT, size: int = 4) -> None:
    """Linha divisória fina só embaixo de cada célula da linha (zebra elegante)."""
    for cell in row.cells:
        tcPr = cell._tc.get_or_add_tcPr()
        borders = OxmlElement("w:tcBorders")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), str(size))
        bottom.set(qn("w:color"), hex_color)
        borders.append(bottom)
        tcPr.append(borders)


# --------------------------------------------------------------------------- #
# Estilos base
# --------------------------------------------------------------------------- #
def apply_base_styles(doc: _Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = FONT_BASE
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = TEXT_BODY
    normal.paragraph_format.space_after = Pt(8)
    normal.paragraph_format.line_spacing = 1.35
    rpr = normal.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs"):
        rfonts.set(qn(attr), FONT_BASE)

    for name, size, color, before in (
        ("Heading 1", 20, PURPLE, 18),
        ("Heading 2", 15, PURPLE, 14),
        ("Heading 3", 12.5, PURPLE_DARK, 12),
    ):
        try:
            st = doc.styles[name]
        except KeyError:
            continue
        st.font.name = FONT_BASE
        st.font.bold = True
        st.font.size = Pt(size)
        st.font.color.rgb = color
        st.paragraph_format.space_before = Pt(before)
        st.paragraph_format.space_after = Pt(6)
        st.paragraph_format.keep_with_next = True


# --------------------------------------------------------------------------- #
# Capa como IMAGEM de página inteira
# --------------------------------------------------------------------------- #
# Uma imagem é atômica: nunca quebra em duas páginas (o problema da capa em
# tabela, cujas linhas transbordavam para uma 2ª página fantasma). Também rende
# a borda arredondada e a fonte Inter exatas da referência, coisas que o docx
# nativo não faz. O texto da capa deixa de ser editável — aceitável, o conteúdo
# editável é o CORPO do relatório.
_COVER_DPI = 200


def build_cover(
    doc: _Document,
    title: str,
    kicker: str = "Metodologia GovHub · Governança de Dados",
    subtitle: str = "",
    brand_line: str = "GovHub · Lab Livre · Metodologia de projetos",
) -> None:
    sec = doc.sections[0]
    sec.page_width = Mm(210)
    sec.page_height = Mm(297)
    for attr in ("top_margin", "bottom_margin", "left_margin", "right_margin",
                 "header_distance", "footer_distance"):
        setattr(sec, attr, Mm(0))

    png = _render_cover_png(title, kicker, subtitle, brand_line)
    img_p = doc.paragraphs[0] if doc.paragraphs else doc.add_paragraph()
    img_p.paragraph_format.space_before = Pt(0)
    img_p.paragraph_format.space_after = Pt(0)
    img_p.add_run().add_picture(png, width=Mm(210), height=Mm(297))

    # corpo numa nova seção, com margens normais
    body = doc.add_section(WD_SECTION.NEW_PAGE)
    body.page_width = Mm(210)
    body.page_height = Mm(297)
    body.top_margin = Mm(22)
    body.bottom_margin = Mm(24)
    body.left_margin = Mm(22)
    body.right_margin = Mm(22)
    _build_footer(body)

    # O `add_section` cria um parágrafo vazio (guarda o sectPr da capa) que somaria
    # altura à imagem de 297mm e empurraria a capa para uma 2ª página. Movemos o
    # sectPr para o próprio parágrafo da imagem e removemos o vazio → capa = 1 página.
    _merge_cover_sectpr(doc, img_p)


def _merge_cover_sectpr(doc: _Document, img_p) -> None:
    for p in list(doc.paragraphs):
        if p._p is img_p._p:
            continue
        pPr = p._p.find(qn("w:pPr"))
        sectPr = pPr.find(qn("w:sectPr")) if pPr is not None else None
        if sectPr is not None:
            img_pPr = img_p._p.get_or_add_pPr()
            img_pPr.append(sectPr)                 # sectPr no fim do pPr (posição válida)
            p._p.getparent().remove(p._p)          # remove o parágrafo vazio
            return


def _render_cover_png(title: str, kicker: str, subtitle: str, brand_line: str) -> BytesIO:
    dpi = _COVER_DPI

    def mm(v: float) -> int:
        return int(round(v / 25.4 * dpi))

    W, H = mm(210), mm(297)
    purple = (0x75, 0x21, 0xF9)
    dark = (0x20, 0x20, 0x20)
    white = (255, 255, 255)

    img = Image.new("RGB", (W, H), purple)
    # moldura arredondada fina, branca a ~55% (overlay RGBA)
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    inset = mm(10)
    od.rounded_rectangle(
        [inset, inset, W - 1 - inset, H - 1 - inset],
        radius=mm(6), outline=(255, 255, 255, 140), width=max(2, mm(0.6)),
    )
    img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    d = ImageDraw.Draw(img)

    left = mm(20)
    content_w = W - 2 * left
    f_kicker = _font("Inter-SemiBold.ttf", 10)
    f_title = _font("Inter-ExtraBold.ttf", 30)
    f_sub = _font("Inter-Regular.ttf", 12.5)
    f_brand = _font("Inter-SemiBold.ttf", 9)

    y = int(0.55 * H)
    if _LOGO_LIGHT.exists():
        logo = Image.open(_LOGO_LIGHT).convert("RGBA")
        lh = mm(11)
        lw = int(logo.width * lh / logo.height)
        logo = logo.resize((lw, lh))
        img.paste(logo, (left, y), logo)
        d = ImageDraw.Draw(img)
        y += lh + mm(15)

    _tracked_text(d, (left, y), kicker.upper(), f_kicker, white, mm(0.7))
    y += int(f_kicker.size * 1.1) + mm(7)

    for line in _wrap(d, title, f_title, content_w):
        d.text((left, y), line, font=f_title, fill=dark)
        y += int(f_title.size * 1.14)
    y += mm(4)

    for line in _wrap(d, subtitle, f_sub, content_w):
        d.text((left, y), line, font=f_sub, fill=white)
        y += int(f_sub.size * 1.45)

    by = H - mm(16) - int(f_brand.size)
    _tracked_text(d, (left, by), brand_line.upper(), f_brand, white, mm(0.6))

    bio = BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio


def _font(fname: str, pt: float) -> ImageFont.FreeTypeFont:
    px = max(1, int(round(pt / 72 * _COVER_DPI)))
    return ImageFont.truetype(str(_FONTS / fname), px)


def _tracked_text(draw, xy, text: str, font, fill, tracking: int) -> None:
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking


def _wrap(draw, text: str, font, max_w: int) -> list:
    lines, cur = [], ""
    for word in (text or "").split():
        test = (cur + " " + word).strip()
        if not cur or draw.textlength(test, font=font) <= max_w:
            cur = test
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


# --------------------------------------------------------------------------- #
# Rodapé (barra fina + nome do doc à esquerda + símbolo à direita)
# --------------------------------------------------------------------------- #
def _build_footer(section, doc_name: str = "Relatório GovHub · Metodologia GovHub") -> None:
    section.footer.is_linked_to_previous = False
    footer = section.footer
    # limpa parágrafo default
    p0 = footer.paragraphs[0]
    p0.text = ""
    p0.paragraph_format.space_after = Pt(4)
    _bottom_border_paragraph(p0)

    # tabela 1x2 sem bordas: texto à esquerda, símbolo à direita
    table = footer.add_table(rows=1, cols=2, width=Mm(166))
    clear_table_borders(table)
    cols = table._tbl.tblGrid.findall(qn("w:gridCol"))
    if len(cols) == 2:
        cols[0].set(qn("w:w"), str(int(Mm(120).twips)))
        cols[1].set(qn("w:w"), str(int(Mm(46).twips)))

    left = table.cell(0, 0)
    lp = left.paragraphs[0]
    r = lp.add_run(doc_name)
    r.font.size = Pt(8)
    r.font.color.rgb = TEXT_MUTED
    set_run_font(r, FONT_BASE)

    right = table.cell(0, 1)
    rp = right.paragraphs[0]
    rp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    if _SYMBOL.exists():
        rp.add_run().add_picture(str(_SYMBOL), height=Mm(7))


def _bottom_border_paragraph(paragraph, hex_color: str = BORDER_SOFT) -> None:
    pPr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "2")
    bottom.set(qn("w:color"), hex_color)
    borders.append(bottom)
    pPr.append(borders)

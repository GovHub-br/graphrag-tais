"""Tema visual GovHub para o relatório `.docx`, aplicado por código.

Recria em `python-docx` o relatório SÓBRIO da IDV do GovHub (modo sóbrio de
30/09/2026; receitas `print-cover`, `print-header`, `print-table` e
`print-footer` da skill `govhub-visual-identity`, em GovHub-br/gov-hub-skills):

- capa BRANCA: logomarca navy no alto à esquerda, três elementos gráficos
  oficiais sangrando pelas bordas (quarto de círculo roxo, pílula #F2F1F6,
  anel navy), título em Reddit Sans 800 navy e os parceiros (Lab Livre + UnB)
  no pé — sem moldura, sem gradiente, sem kicker;
- cabeçalho de capítulo sobre o branco: numeral navy translúcido, eyebrow em
  maiúsculas, título em roxo (o único roxo da página de texto) e uma barra
  fina `#E9DFFF` fechando o bloco;
- tabela sem cartão: legenda numerada acima, cabeçalho navy sólido, zebra
  `#F2F1F6`, fio fino entre linhas;
- rodapé em toda página de conteúdo: barra, número da página, nome curto do
  documento e o símbolo navy.

A versão anterior (capa roxa full-bleed com moldura, roxo #7A34F3, laranja,
Inter) seguia a paleta descontinuada.

Alvo é um documento **editável** no Word. Por isso a capa não é mais uma imagem
de página inteira com o texto queimado: as formas e as logos são UMA imagem
ancorada atrás do texto, e título e subtítulo são parágrafos de verdade. E a
Reddit Sans vai EMBUTIDA no arquivo (`embed_fonts`): quase ninguém a tem
instalada, e sem ela o Word trocaria a letra do documento inteiro.
"""
from __future__ import annotations

import copy
import uuid
from io import BytesIO
from pathlib import Path

from docx.document import Document as _Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Emu, Mm, Pt, RGBColor
from PIL import Image, ImageDraw

# --------------------------------------------------------------------------- #
# Paleta (govhub-visual-identity/references/palette.md, modo sóbrio)
# --------------------------------------------------------------------------- #
PURPLE = RGBColor(0x61, 0x3E, 0xFF)        # assinatura: título de capítulo
NAVY = RGBColor(0x0A, 0x00, 0x5A)          # títulos, cabeçalho de tabela, texto forte
PURPLE_DARK = RGBColor(0x3F, 0x28, 0xA6)   # roxo de TEXTO pequeno (contraste AA)
ROSE = RGBColor(0xBE, 0x00, 0x6E)          # acento: só detalhe
SUCCESS = RGBColor(0x10, 0xB9, 0x81)
TEXT_STRONG = RGBColor(0x20, 0x20, 0x20)
TEXT_BODY = RGBColor(0x2D, 0x37, 0x48)
TEXT_MUTED = RGBColor(0x66, 0x66, 0x66)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
# O numeral e o eyebrow do cabeçalho são navy a 45% e 70% (print-header.md).
# Word não tem opacidade de texto: são as cores já misturadas com o branco.
NAVY_45 = RGBColor(0x91, 0x8C, 0xBE)
NAVY_70 = RGBColor(0x54, 0x4D, 0x8C)

# fills (hex sem #, para o w:shd)
FILL_HEADER = "0A005A"    # cabeçalho de tabela: sempre navy (print-table.md)
FILL_ZEBRA = "F2F1F6"     # linha alternada: o claro da marca
FILL_CODE = "F2F1F6"      # chip de código inline / bloco
FILL_SOFT = "F2F1F6"
BORDER_SOFT = "E9DFFF"    # barra do cabeçalho, do rodapé e fio entre linhas
CATEGORY_FILLS = {        # badges de tipo: tons do roxo, sem laranja
    "texto": ("ECE8FF", PURPLE_DARK),
    "numero": ("DDD5FF", PURPLE_DARK),
    "data": ("F2F1F6", NAVY),
    "booleano": ("D6F3E8", RGBColor(0x0A, 0x6B, 0x4B)),
}

# Só Regular e Bold. Os pesos 600 e 800 da Reddit Sans são FAMÍLIAS separadas
# no nome do arquivo ("Reddit Sans ExtraBold"): o Word as acha, mas o
# LibreOffice (e o Google Docs, ao importar) não, e trocava a capa e os títulos
# por uma substituta. Bold vale igual nos três — por isso a chamada da IDV
# (peso 800) sai em 700 aqui.
FONT_BASE = "Reddit Sans"
FONT_DISPLAY = FONT_BASE   # sempre com bold=True
FONT_MONO = "Consolas"

_ASSETS = Path(__file__).parent / "assets"
_FONTS = _ASSETS / "fonts"
_LOGO = _ASSETS / "govhub-logomarca-navy.png"     # capa
_SYMBOL = _ASSETS / "govhub-icone-navy.png"       # rodapé
_PARTNERS = (_ASSETS / "parceiro-lab-livre.png",  # ordem fixa: Lab Livre, depois UnB
             _ASSETS / "parceiro-unb.png")

# Margem do corpo (print-pages.md): 20 mm dos quatro lados; o rodapé fica
# dentro da margem de baixo, que por isso é maior.
MARGIN = Mm(20)
BODY_WIDTH = Mm(170)


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


def styled_run(paragraph, text: str, *, font: str = FONT_BASE, size: float | None = None,
               color: RGBColor | None = None, bold: bool = False, italic: bool = False,
               spacing: float | None = None, caps: bool = False):
    """Run com fonte, corpo, cor e (opcional) espaçamento entre letras em pt."""
    run = paragraph.add_run(text)
    set_run_font(run, font)
    if size is not None:
        run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = color
    run.bold = bold
    run.italic = italic
    run.font.all_caps = caps
    if spacing:
        sp = OxmlElement("w:spacing")
        sp.set(qn("w:val"), str(int(spacing * 20)))
        run._element.get_or_add_rPr().append(sp)
    return run


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


def set_table_width(table, width, columns=None) -> None:
    """Largura fixa da tabela (e das colunas, em Length): sem isto o Word e o
    LibreOffice dividem por igual e a coluna de rótulo come metade da página.
    Aceita int (Length - Length devolve int no python-docx)."""
    width = Emu(int(width))
    columns = [Emu(int(c)) for c in columns] if columns else None
    tblPr = table._tbl.tblPr
    tblW = tblPr.find(qn("w:tblW"))
    if tblW is None:
        tblW = OxmlElement("w:tblW")
        tblPr.append(tblW)
    tblW.set(qn("w:w"), str(int(width.twips)))
    tblW.set(qn("w:type"), "dxa")
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    if columns:
        for col, w in zip(table._tbl.tblGrid.findall(qn("w:gridCol")), columns):
            col.set(qn("w:w"), str(int(w.twips)))
        for row in table.rows:
            for cell, w in zip(row.cells, columns):
                cell.width = w


def set_cell_borders(cell, hex_color: str, size: int = 8, left_accent_size: int = 0,
                     edges=("top", "left", "bottom", "right"), left_color: str | None = None) -> None:
    """Borda ao redor da célula (usada no callout box). `left_accent_size` engrossa
    só a borda esquerda (barra de acento)."""
    tcPr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        e = OxmlElement(f"w:{edge}")
        if edge not in edges and not (edge == "left" and left_accent_size):
            e.set(qn("w:val"), "nil")
            borders.append(e)
            continue
        e.set(qn("w:val"), "single")
        sz = left_accent_size if (edge == "left" and left_accent_size) else size
        e.set(qn("w:sz"), str(sz))
        e.set(qn("w:color"), (left_color or hex_color) if edge == "left" else hex_color)
        borders.append(e)
    tcPr.append(borders)


def set_row_bottom_border(row, hex_color: str = BORDER_SOFT, size: int = 4) -> None:
    """Linha divisória fina só embaixo de cada célula da linha."""
    for cell in row.cells:
        tcPr = cell._tc.get_or_add_tcPr()
        borders = OxmlElement("w:tcBorders")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), str(size))
        bottom.set(qn("w:color"), hex_color)
        borders.append(bottom)
        tcPr.append(borders)


def repeat_header(row) -> None:
    """Cabeçalho da tabela repetido no topo de cada página por onde ela passa."""
    trPr = row._tr.get_or_add_trPr()
    th = OxmlElement("w:tblHeader")
    th.set(qn("w:val"), "true")
    trPr.append(th)


def keep_row_together(row) -> None:
    trPr = row._tr.get_or_add_trPr()
    cs = OxmlElement("w:cantSplit")
    cs.set(qn("w:val"), "true")
    trPr.append(cs)


def paragraph_border(paragraph, edge: str = "bottom", hex_color: str = BORDER_SOFT,
                     size: int = 12, space: int = 4) -> None:
    """Fio sob (ou sobre) o parágrafo. `size` em oitavos de ponto: 12 = 1,5 pt,
    a barra de 2 px do cabeçalho e do rodapé da IDV."""
    pPr = paragraph._p.get_or_add_pPr()
    borders = pPr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        pPr.append(borders)
    e = OxmlElement(f"w:{edge}")
    e.set(qn("w:val"), "single")
    e.set(qn("w:sz"), str(size))
    e.set(qn("w:space"), str(space))
    e.set(qn("w:color"), hex_color)
    borders.append(e)


def tight(paragraph, before: float = 0, after: float = 0, line: float | None = None):
    pf = paragraph.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    if line is not None:
        pf.line_spacing = line
    return paragraph


# --------------------------------------------------------------------------- #
# Estilos base
# --------------------------------------------------------------------------- #
def _style_font(style, name: str) -> None:
    style.font.name = name
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), name)
    # os temas do Word ligam fonte de título ao tema (asciiTheme), que vence o
    # nome explícito — tira o vínculo para a Reddit Sans valer
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme", "w:eastAsiaTheme"):
        if rfonts.get(qn(attr)) is not None:
            del rfonts.attrib[qn(attr)]


def apply_base_styles(doc: _Document) -> None:
    """Escala tipográfica da IDV (print-header.md), adaptada à tela do Word:
    corpo 10,5 pt, subtítulo navy 14 pt, título de capítulo roxo 22 pt."""
    normal = doc.styles["Normal"]
    _style_font(normal, FONT_BASE)
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = TEXT_BODY
    normal.paragraph_format.space_after = Pt(7)
    normal.paragraph_format.line_spacing = 1.4
    lang = OxmlElement("w:lang")
    lang.set(qn("w:val"), "pt-BR")
    normal.element.get_or_add_rPr().append(lang)

    # Heading 1 = título de capítulo (no cabeçalho, ver `add_chapter`);
    # Heading 2/3 = subtítulos do corpo, em navy — o roxo roubava atenção do
    # título do capítulo (decisão de 18/09 na IDV).
    for name, font, size, color, before, after in (
        ("Heading 1", FONT_DISPLAY, 22, PURPLE, 0, 0),
        ("Heading 2", FONT_BASE, 14, NAVY, 16, 6),
        ("Heading 3", FONT_BASE, 11.5, NAVY, 12, 4),
    ):
        try:
            st = doc.styles[name]
        except KeyError:
            continue
        _style_font(st, font)
        st.font.bold = True
        st.font.italic = False
        st.font.size = Pt(size)
        st.font.color.rgb = color
        st.paragraph_format.space_before = Pt(before)
        st.paragraph_format.space_after = Pt(after)
        st.paragraph_format.keep_with_next = True
        st.paragraph_format.line_spacing = 1.12

    for name in ("List Bullet", "List Number", "Caption"):
        try:
            st = doc.styles[name]
        except KeyError:
            continue
        _style_font(st, FONT_BASE)
        st.font.color.rgb = TEXT_BODY
        st.font.size = Pt(10.5)
        st.paragraph_format.space_after = Pt(3)


def set_properties(doc: _Document, title: str, subject: str = "") -> None:
    cp = doc.core_properties
    cp.title = title
    cp.subject = subject
    cp.author = "Assistente de Dados TAIS · Gov Hub"
    cp.language = "pt-BR"
    cp.keywords = "Gov Hub; relatório; dados públicos"


# --------------------------------------------------------------------------- #
# Fontes embutidas
# --------------------------------------------------------------------------- #
# (família como o Word a vê, {estilo do embed: arquivo})
_EMBED = (
    (FONT_BASE, {"embedRegular": "RedditSans-Regular.ttf", "embedBold": "RedditSans-Bold.ttf"}),
)
_OBFUSCATED = "application/vnd.openxmlformats-officedocument.obfuscatedFont"


def _obfuscate(data: bytes, guid: uuid.UUID) -> bytes:
    """Ofuscação de fonte embutida do OOXML (ECMA-376, 17.8.1): os 32 primeiros
    bytes passam por XOR com a chave, que é o GUID lido de trás para frente."""
    key = bytes.fromhex(guid.hex)[::-1]
    out = bytearray(data)
    for i in range(32):
        out[i] ^= key[i % 16]
    return bytes(out)


def embed_fonts(doc: _Document) -> bool:
    """Embute a Reddit Sans no `.docx` (licença OFL permite).

    Sem isto, quem abrir o relatório num computador sem a fonte — quase todo
    mundo — veria o Word trocar a letra do documento inteiro por Calibri, e a
    capa, o cabeçalho e as tabelas perderiam o desenho. O Word (Windows e Mac)
    e o LibreOffice leem fonte embutida. Devolve False se não conseguir: o
    documento continua válido, só sem a fonte."""
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part
    from lxml import etree

    try:
        table_part = doc.part.part_related_by(RT.FONT_TABLE)
        fonts = parse_xml(table_part.blob)
        n = 0
        for family, files in _EMBED:
            font = OxmlElement("w:font")
            font.set(qn("w:name"), family)
            for tag, val in (("w:charset", "00"), ("w:family", "swiss"), ("w:pitch", "variable")):
                el = OxmlElement(tag)
                el.set(qn("w:val"), val)
                font.append(el)
            for tag, filename in files.items():
                path = _FONTS / filename
                if not path.exists():
                    continue
                n += 1
                guid = uuid.uuid4()
                part = Part(PackURI(f"/word/fonts/font{n}.odttf"), _OBFUSCATED,
                            _obfuscate(path.read_bytes(), guid), doc.part.package)
                rid = table_part.relate_to(part, RT.FONT)
                emb = OxmlElement(f"w:{tag}")
                emb.set(qn("r:id"), rid)
                emb.set(qn("w:fontKey"), "{" + str(guid).upper() + "}")
                font.append(emb)
            fonts.append(font)
        table_part._blob = etree.tostring(fonts, xml_declaration=True, encoding="UTF-8",
                                          standalone=True)

        # <w:embedTrueTypeFonts/> tem posição fixa no esquema do settings: logo
        # depois de writeProtection/view/zoom/remove*/print* — o Word recusa o
        # arquivo como corrompido se ele vier depois de, por exemplo, proofState.
        settings = doc.settings.element
        flag = OxmlElement("w:embedTrueTypeFonts")
        before = ("w:writeProtection", "w:view", "w:zoom", "w:removePersonalInformation",
                  "w:removeDateAndTime", "w:doNotDisplayPageBoundaries",
                  "w:displayBackgroundShape", "w:printPostScriptOverText",
                  "w:printFractionalCharacterWidth", "w:printFormsData")
        anchor = None
        for child in settings:
            if child.tag in {qn(t) for t in before}:
                anchor = child
        if anchor is None:
            settings.insert(0, flag)
        else:
            anchor.addnext(flag)
        return n > 0
    except Exception as exc:  # noqa: BLE001 — fonte é cosmético, nunca derruba o relatório
        from loguru import logger

        logger.warning("[report] falha ao embutir a fonte: {}", exc)
        return False


# --------------------------------------------------------------------------- #
# Imagem ancorada atrás do texto
# --------------------------------------------------------------------------- #
_ANCHOR = (
    '<wp:anchor %s distT="0" distB="0" distL="0" distR="0" simplePos="0" '
    'relativeHeight="0" behindDoc="1" locked="1" layoutInCell="1" allowOverlap="1">'
    '<wp:simplePos x="0" y="0"/>'
    '<wp:positionH relativeFrom="page"><wp:posOffset>%d</wp:posOffset></wp:positionH>'
    '<wp:positionV relativeFrom="page"><wp:posOffset>%d</wp:posOffset></wp:positionV>'
    '<wp:extent cx="%d" cy="%d"/><wp:effectExtent l="0" t="0" r="0" b="0"/><wp:wrapNone/>'
    '</wp:anchor>'
)


def float_behind(run, picture_stream, width, height, x=Emu(0), y=Emu(0)) -> None:
    """Insere uma imagem FLUTUANTE atrás do texto, posicionada na página.

    O python-docx só sabe inserir imagem em linha; aqui ela é inserida assim e
    o `<wp:inline>` é trocado por um `<wp:anchor behindDoc>` com o mesmo
    conteúdo. É o que deixa a capa ter fundo desenhado E título editável."""
    run.add_picture(picture_stream, width=width, height=height)
    inline = run._element.find(".//" + qn("wp:inline"))
    anchor = parse_xml(_ANCHOR % (nsdecls("wp"), int(x), int(y), int(width), int(height)))
    for tag in ("wp:docPr", "wp:cNvGraphicFramePr", "a:graphic"):
        el = inline.find(qn(tag))
        if el is not None:
            anchor.append(copy.deepcopy(el))
    inline.getparent().replace(inline, anchor)


# --------------------------------------------------------------------------- #
# Capa
# --------------------------------------------------------------------------- #
_COVER_DPI = 300


def _render_cover_png() -> BytesIO:
    """Fundo da capa (print-cover.md): as três formas e as logos, sem texto.

    Medidas da receita validada, em mm sobre A4. O texto (título, subtítulo)
    NÃO entra aqui: fica editável no documento, por cima desta imagem."""
    dpi = _COVER_DPI

    def mm(v: float) -> int:
        return int(round(v / 25.4 * dpi))

    W, H = mm(210), mm(297)
    navy = (0x0A, 0x00, 0x5A)
    purple = (0x61, 0x3E, 0xFF)
    soft = (0xF2, 0xF1, 0xF6)

    # Desenha em 2× e reduz: o PIL não suaviza borda de elipse.
    s = 2
    img = Image.new("RGB", (W * s, H * s), (255, 255, 255))
    d = ImageDraw.Draw(img)
    # quarto de círculo roxo: círculo de raio 104 mm com centro no canto inferior direito
    r = mm(104) * s
    d.ellipse([W * s - r, H * s - r, W * s + r, H * s + r], fill=purple)
    # pílula #F2F1F6 entrando pela esquerda: left -26, top 64, 74 × 27,8 mm
    x0, y0 = mm(-26) * s, mm(64) * s
    d.rounded_rectangle([x0, y0, x0 + mm(74) * s, y0 + mm(27.8) * s],
                        radius=mm(13.9) * s, fill=soft)
    # anel navy no alto à direita: right -12, top 22, 40 mm; furo de 24,4%
    cx, cy, ro = W * s - mm(-12) * s - mm(20) * s, mm(22) * s + mm(20) * s, mm(20) * s
    ri = int(ro * 24.4 / 50)
    d.ellipse([cx - ro, cy - ro, cx + ro, cy + ro], fill=navy)
    d.ellipse([cx - ri, cy - ri, cx + ri, cy + ri], fill=(255, 255, 255))
    img = img.resize((W, H), Image.LANCZOS)

    def paste(path: Path, left_mm: float, top_mm: float, height_mm: float) -> float:
        if not path.exists():
            return 0
        logo = Image.open(path).convert("RGBA")
        h = mm(height_mm)
        w = int(logo.width * h / logo.height)
        img.paste(logo.resize((w, h), Image.LANCZOS), (mm(left_mm), mm(top_mm)),
                  logo.resize((w, h), Image.LANCZOS))
        return w / dpi * 25.4

    paste(_LOGO, 20, 20, 12)
    # parceiros: Lab Livre e depois UnB, pretos, 9 mm, alinhados à esquerda no pé
    left = 20.0
    for path in _PARTNERS:
        left += paste(path, left, 297 - 16 - 9, 9) + 9.5

    bio = BytesIO()
    img.save(bio, format="PNG", optimize=True)
    bio.seek(0)
    return bio


def build_cover(doc: _Document, title: str, subtitle: str = "", meta: str = "",
                doc_name: str = "") -> None:
    """Capa branca da IDV + seção do corpo com o rodapé.

    `title` é o nome do documento (16 caracteres por linha, como na receita),
    `subtitle` uma frase, `meta` a linha pequena de data/autoria."""
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.top_margin = sec.bottom_margin = MARGIN
    sec.left_margin = sec.right_margin = MARGIN
    sec.header_distance = sec.footer_distance = Mm(8)

    anchor_p = doc.paragraphs[0] if doc.paragraphs else doc.add_paragraph()
    tight(anchor_p, line=1.0)
    float_behind(anchor_p.add_run(), _render_cover_png(), Mm(210), Mm(297))

    # Título: começa a ~123 mm do topo da folha (receita: 46% da altura,
    # deslocado para cima), limitado a ~16 caracteres por linha pelo recuo
    # direito, o que dá as duas linhas da receita ("Relatório de / consulta").
    t = doc.add_paragraph()
    tight(t, before=Mm(93).pt, after=14, line=1.04)
    t.paragraph_format.right_indent = Mm(62)
    styled_run(t, title, font=FONT_DISPLAY, size=34, color=NAVY, bold=True)

    if subtitle:
        p = doc.add_paragraph()
        tight(p, after=10, line=1.4)
        p.paragraph_format.right_indent = Mm(48)
        styled_run(p, subtitle, size=12, color=TEXT_BODY)
    if meta:
        p = doc.add_paragraph()
        tight(p, line=1.3)
        styled_run(p, meta, size=9.5, color=TEXT_MUTED)

    body = doc.add_section(WD_SECTION.NEW_PAGE)
    body.page_width, body.page_height = Mm(210), Mm(297)
    body.top_margin = MARGIN
    body.bottom_margin = Mm(30)
    body.left_margin = body.right_margin = MARGIN
    body.footer_distance = Mm(10)
    _build_footer(body, doc_name or title)


# --------------------------------------------------------------------------- #
# Cabeçalho de capítulo
# --------------------------------------------------------------------------- #
def add_chapter(doc: _Document, number: int | None, eyebrow: str, title: str,
                new_page: bool = True) -> None:
    """Cabeçalho sóbrio (print-header.md): numeral navy translúcido, eyebrow em
    maiúsculas, título roxo (estilo Heading 1, entra no painel de navegação) e a
    barra `#E9DFFF` embaixo. Numeração começa em 1: "01", "02"…"""
    if new_page:
        brk = doc.add_paragraph()
        tight(brk, line=1.0)
        brk.paragraph_format.page_break_before = True
        styled_run(brk, "", size=2)

    table = doc.add_table(rows=1, cols=2 if number is not None else 1)
    clear_table_borders(table)
    cols = [Mm(30), BODY_WIDTH - Mm(30)] if number is not None else [BODY_WIDTH]
    set_table_width(table, BODY_WIDTH, cols)
    cells = table.rows[0].cells
    if number is not None:
        num = cells[0]
        num.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        set_cell_margins(num, top=0, bottom=0, left=0, right=0)
        p = tight(num.paragraphs[0], line=1.0)
        styled_run(p, f"{number:02d}", font=FONT_DISPLAY, size=40, color=NAVY_45, bold=True)
    box = cells[-1]
    box.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    set_cell_margins(box, top=0, bottom=0, left=0, right=0)
    ep = tight(box.paragraphs[0], after=4, line=1.0)
    styled_run(ep, eyebrow, font=FONT_BASE, size=8, color=NAVY_70, bold=True,
               spacing=1.6, caps=True)
    tp = box.add_paragraph(style="Heading 1") if "Heading 1" in [s.name for s in doc.styles] \
        else box.add_paragraph()
    tight(tp, line=1.08)
    styled_run(tp, title, font=FONT_DISPLAY, size=22, color=PURPLE, bold=True)

    bar = doc.add_paragraph()
    tight(bar, before=0, after=Mm(9).pt, line=1.0)
    paragraph_border(bar, "bottom", BORDER_SOFT, size=12, space=10)
    styled_run(bar, "", size=2)


# --------------------------------------------------------------------------- #
# Rodapé: barra, número da página, nome do documento, símbolo
# --------------------------------------------------------------------------- #
def _field(paragraph, instr: str, **run_kw) -> None:
    """Campo do Word (PAGE): o número é calculado por quem abre o documento."""
    def part(kind: str):
        r = styled_run(paragraph, "", **run_kw)
        fc = OxmlElement("w:fldChar")
        fc.set(qn("w:fldCharType"), kind)
        r._element.append(fc)

    part("begin")
    r = styled_run(paragraph, "", **run_kw)
    it = OxmlElement("w:instrText")
    it.set(qn("xml:space"), "preserve")
    it.text = f" {instr} "
    r._element.append(it)
    part("separate")
    styled_run(paragraph, "2", **run_kw)
    part("end")


def _build_footer(section, doc_name: str) -> None:
    """print-footer.md: barra fina na largura do conteúdo; embaixo, à esquerda
    o número da página em navy forte e o nome curto do documento, à direita o
    símbolo navy. Sem rodapé na capa (a seção dela não tem)."""
    section.footer.is_linked_to_previous = False
    footer = section.footer
    bar = footer.paragraphs[0]
    bar.text = ""
    tight(bar, after=5, line=1.0)
    paragraph_border(bar, "bottom", BORDER_SOFT, size=12, space=1)
    styled_run(bar, "", size=2)

    table = footer.add_table(rows=1, cols=2, width=BODY_WIDTH)
    clear_table_borders(table)
    set_table_width(table, BODY_WIDTH, [BODY_WIDTH - Mm(20), Mm(20)])
    left, right = table.rows[0].cells
    for c in (left, right):
        c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        set_cell_margins(c)
    lp = tight(left.paragraphs[0], line=1.0)
    _field(lp, "PAGE", font=FONT_DISPLAY, size=9, color=NAVY, bold=True)
    styled_run(lp, "    " + doc_name, size=8.5, color=TEXT_MUTED)

    rp = tight(right.paragraphs[0], line=1.0)
    rp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    if _SYMBOL.exists():
        rp.add_run().add_picture(str(_SYMBOL), height=Mm(8))

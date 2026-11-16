"""Testes offline do conversor Markdown → .docx do relatório (ADR-0010).

Cobre o núcleo (`docx_writer.render_markdown`) sem rede nem MinIO: garante que os
elementos que a resposta do chat realmente produz — títulos, bloco SQL, tabela do
dicionário, badges de tipo, callout, imagem base64, ênfases inline — viram os
elementos docx certos.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docx import Document  # noqa: E402

from rag.components.report import docx_writer, theme  # noqa: E402


def _sample_png_data_uri() -> str:
    """PNG válido (via Pillow, que acompanha o matplotlib) como data-uri base64."""
    import base64
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (64, 40), (122, 52, 243)).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


try:
    _DATA_URI = _sample_png_data_uri()
    _HAS_PIL = True
except Exception:  # noqa: BLE001
    _DATA_URI = "data:image/png;base64,"
    _HAS_PIL = False

_MARKDOWN = f"""Foram **74 empreendimentos** no schema `empreendimento_far`.

### SQL gerado
```sql
SELECT categoria, COUNT(*) AS total
FROM empreendimento_far.empreendimento
GROUP BY categoria;
```

### Dados de referência

**Base `empreendimento_far`**

| 📋 `empreendimento` | Tipo e chave |
| :-- | :-- |
| `apf` | text · 🔑 PK |
| `valor` | numeric |

### Relações de JOIN no contexto
- `empreendimento.municipio_id` = `municipio.id`

> ⚠️ a chave não casa direto.

### Gráfico

![gráfico]({_DATA_URI})
"""


def _all_runs(doc):
    """Runs de todos os parágrafos, inclusive dentro de células de tabela."""
    runs = [r for p in doc.paragraphs for r in p.runs]
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    runs.extend(p.runs)
    return runs


def _find_dict_table(doc):
    """A tabela do dicionário: header com o marcador '📋' (único do dicionário)."""
    for t in doc.tables:
        if t.rows and "📋" in t.rows[0].cells[0].text:
            return t
    return None


class ReportDocxTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = Document()
        docx_writer.render_markdown(cls.doc, _MARKDOWN)

    def test_headings_use_heading_styles(self) -> None:
        headings = {
            p.text for p in self.doc.paragraphs if p.style.name.startswith("Heading")
        }
        for expected in ("SQL gerado", "Dados de referência", "Gráfico"):
            self.assertIn(expected, headings)

    def test_data_dictionary_becomes_a_table(self) -> None:
        table = _find_dict_table(self.doc)
        self.assertIsNotNone(table)
        # normaliza espaços (os chips de código inserem thin-spaces de respiro)
        header = [" ".join(c.text.split()) for c in table.rows[0].cells]
        self.assertEqual(header, ["📋 empreendimento", "Tipo e chave"])
        self.assertEqual(len(table.rows), 3)  # header + 2 colunas

    def test_table_header_is_purple_filled(self) -> None:
        table = _find_dict_table(self.doc)
        self.assertIn(theme.FILL_HEADER, table.rows[0].cells[0]._tc.xml)

    def test_type_badge_is_shaded(self) -> None:
        table = _find_dict_table(self.doc)
        # 'text' e 'numeric' viram badge → shading da categoria no run
        type_cells_xml = table.rows[1].cells[1]._tc.xml + table.rows[2].cells[1]._tc.xml
        fill_texto = theme.CATEGORY_FILLS["texto"][0]
        fill_numero = theme.CATEGORY_FILLS["numero"][0]
        self.assertTrue(fill_texto in type_cells_xml or fill_numero in type_cells_xml)

    def test_callout_box_for_blockquote(self) -> None:
        # o blockquote vira uma tabela-callout com fundo lavanda
        callouts = [t for t in self.doc.tables if "casa direto" in t.rows[0].cells[0].text]
        self.assertEqual(len(callouts), 1)

    @unittest.skipUnless(_HAS_PIL, "Pillow indisponível para gerar o PNG de teste")
    def test_base64_image_is_embedded(self) -> None:
        images = [r for r in self.doc.part.rels.values() if "image" in r.reltype]
        self.assertEqual(len(images), 1)

    def test_sql_block_is_monospace(self) -> None:
        mono = [
            r for r in _all_runs(self.doc)
            if "SELECT categoria" in r.text and r.font.name == theme.FONT_MONO
        ]
        self.assertTrue(mono)

    def test_bold_inline_is_applied(self) -> None:
        para = next(p for p in self.doc.paragraphs if p.text.startswith("Foram"))
        self.assertTrue(any(r.bold for r in para.runs))


_JORNADA = f"""## Resposta

O total é **R$ 10**.

![Mapa do Brasil: valor por UF]({_DATA_URI})

---

## Números para decisão

### O resultado

| UF | Valor |
| :-- | --: |
| SP | R$ 7 |
| MG | R$ 3 |

**Os valores fora da curva**

| UF | Valor |
| :-- | --: |
| SP | R$ 7 |
"""


@unittest.skipUnless(_HAS_PIL, "Pillow indisponível para gerar o PNG de teste")
class RelatorioSobrioTest(unittest.TestCase):
    """O relatório da jornada no modo sóbrio da IDV (15/11): capa com fundo
    ancorado e título editável, Reddit Sans embutida, capítulo por `##`,
    tabela e figura com legenda numerada."""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile
        import zipfile

        from rag.components.report import ReportBuilder

        cls.tmp = tempfile.TemporaryDirectory()
        path = ReportBuilder(output_dir=cls.tmp.name).build(
            _JORNADA, question="Quanto foi contratado por UF?",
            eyebrows={"Resposta": "Visão geral"})
        cls.doc = Document(path)
        with zipfile.ZipFile(path) as z:
            cls.names = z.namelist()
            cls.font_table = z.read("word/fontTable.xml").decode("utf-8")
            cls.settings = z.read("word/settings.xml").decode("utf-8")
            cls.body = z.read("word/document.xml").decode("utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _textos(self):
        textos = [p.text for p in self.doc.paragraphs]
        for t in self.doc.tables:
            for row in t.rows:
                for cell in row.cells:
                    textos.extend(p.text for p in cell.paragraphs)
        return textos

    def test_reddit_sans_embutida(self) -> None:
        self.assertEqual(sum(n.startswith("word/fonts/") for n in self.names), 2)
        self.assertIn('w:name="Reddit Sans"', self.font_table)
        self.assertIn("w:embedRegular", self.font_table)
        self.assertIn("w:embedBold", self.font_table)
        self.assertIn("w:embedTrueTypeFonts", self.settings)

    def test_ofuscacao_e_reversivel(self) -> None:
        import uuid

        guid = uuid.uuid4()
        dado = bytes(range(64))
        self.assertEqual(theme._obfuscate(theme._obfuscate(dado, guid), guid), dado)
        self.assertNotEqual(theme._obfuscate(dado, guid)[:32], dado[:32])
        self.assertEqual(theme._obfuscate(dado, guid)[32:], dado[32:])

    def test_capa_com_fundo_ancorado_e_titulo_editavel(self) -> None:
        self.assertIn('behindDoc="1"', self.body)
        self.assertIn("Relatório de consulta", self._textos())
        self.assertIn("“Quanto foi contratado por UF?”", self._textos())

    def test_capitulos_numerados_com_eyebrow(self) -> None:
        textos = self._textos()
        for esperado in ("01", "02", "Visão geral", "Resposta", "Números para decisão", "Identificação"):
            self.assertIn(esperado, textos)
        titulos = [p.text for t in self.doc.tables for row in t.rows for c in row.cells
                   for p in c.paragraphs if p.style.name == "Heading 1"]
        self.assertEqual(titulos, ["Identificação", "Resposta", "Números para decisão"])

    def test_legendas_numeradas(self) -> None:
        textos = self._textos()
        self.assertIn("Figura 1. Mapa do Brasil: valor por UF", textos)
        self.assertIn("Tabela 1: O resultado", textos)
        # a linha em negrito descreve a tabela de baixo, não o último ###
        self.assertIn("Tabela 2: Os valores fora da curva", textos)

    def test_cabecalho_navy_e_numero_a_direita(self) -> None:
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        tabela = next(t for t in self.doc.tables if t.rows[0].cells[0].text == "UF")
        self.assertIn(theme.FILL_HEADER, tabela.rows[0].cells[0]._tc.xml)
        self.assertEqual(tabela.rows[1].cells[1].paragraphs[0].alignment, WD_ALIGN_PARAGRAPH.RIGHT)

    def test_rodape_com_numero_de_pagina(self) -> None:
        rodape = self.doc.sections[-1].footer._element.xml
        self.assertIn("PAGE", rodape)

    def test_fio_entre_capitulos_some(self) -> None:
        # a página nova já separa os capítulos; o `---` não vira fio solto
        self.assertNotIn('w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1"', self.body)


if __name__ == "__main__":
    unittest.main()

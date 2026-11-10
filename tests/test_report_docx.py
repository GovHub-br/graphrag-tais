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


if __name__ == "__main__":
    unittest.main()

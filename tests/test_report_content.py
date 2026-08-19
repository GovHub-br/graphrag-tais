"""Testes do caminho ESTRUTURADO do relatório (ADR-0010 opção D).

Garante que montar o `.docx` a partir do `answer` do motor:
- NÃO deixa marcação Markdown crua (crases, underscore-wrappers) — o bug que o
  conversor de Markdown produzia;
- renderiza descrição em itálico, badges de tipo, callout de JOIN e o gráfico.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docx import Document  # noqa: E402

from rag.components.report import report_content, theme  # noqa: E402


def _png_data_uri() -> str:
    import base64
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (48, 32), (122, 52, 243)).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


try:
    _URI = _png_data_uri()
    _HAS_PIL = True
except Exception:  # noqa: BLE001
    _URI, _HAS_PIL = "", False

# answer com underscores em toda parte (o caso que quebrava o parser de Markdown)
_ANSWER = {
    "status": "sucesso",
    "explanation": "Contagem por UF a partir de `empreendimento_far.mapa_nacional`, "
                   "usando a coluna `total_empreendimentos`.",
    "sql": "SELECT uf, total_empreendimentos\nFROM empreendimento_far.mapa_nacional\n"
           "WHERE nivel = 'uf';",
    "ir": {"intent": "count", "metric": "empreendimentos",
           "filters": [{"concept": "nivel", "value": "uf"}]},
    "references": [{
        "base": "empreendimento_far",
        "table": "empreendimento_far.mapa_nacional",
        "description": "Visão Gold. Usa a tabela IBGE api_ibge_uf como referência.",
        "columns": [
            {"name": "nivel", "type": "text", "is_pk": True, "is_join_key": False},
            {"name": "uf", "type": "varchar(2)", "is_pk": False, "is_join_key": True},
            {"name": "total_empreendimentos", "type": "integer", "is_pk": False, "is_join_key": False},
        ],
        "used": True,
    }],
    "joins": [{"left_table": "mapa_nacional", "left_col": "uf",
               "right_table": "api_ibge_uf", "right_col": "sigla_uf",
               "sql_join_eligible": False}],
}


def _body_text(doc) -> str:
    parts = [p.text for p in doc.paragraphs]
    for t in doc.tables:
        for row in t.rows:
            for c in row.cells:
                parts.append(c.text)
    return "\n".join(parts)


class ReportContentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = Document()
        theme.apply_base_styles(cls.doc)
        report_content.render_answer(
            cls.doc, _ANSWER, chart_data_uri=_URI,
            exec_info={"exec_ok": True, "sql_final": "", "exec_error": ""},
        )
        cls.body = _body_text(cls.doc)

    def test_no_literal_backticks(self) -> None:
        self.assertNotIn("`", self.body)

    def test_no_underscore_wrappers(self) -> None:
        for line in self.body.splitlines():
            s = line.strip()
            self.assertFalse(s.startswith("_") or s.endswith("_"),
                             f"marcação de itálico crua: {s!r}")

    def test_description_is_italic(self) -> None:
        target = next(
            (p for p in self.doc.paragraphs if p.text.startswith("Visão Gold")), None
        )
        self.assertIsNotNone(target)
        self.assertTrue(all(r.italic for r in target.runs if r.text.strip()))

    def test_type_badges_present(self) -> None:
        dict_table = next(t for t in self.doc.tables if "📋" in t.rows[0].cells[0].text)
        xml = "".join(row.cells[1]._tc.xml for row in dict_table.rows[1:])
        fills = [f for f, _ in theme.CATEGORY_FILLS.values()]
        self.assertTrue(any(f in xml for f in fills))

    def test_join_callout_present(self) -> None:
        callouts = [t for t in self.doc.tables if "casa direto" in t.rows[0].cells[0].text]
        self.assertEqual(len(callouts), 1)

    @unittest.skipUnless(_HAS_PIL, "Pillow indisponível")
    def test_chart_embedded(self) -> None:
        imgs = [r for r in self.doc.part.rels.values() if "image" in r.reltype]
        self.assertEqual(len(imgs), 1)


class ReportSqlInvariantTest(unittest.TestCase):
    """O .docx segue a mesma invariante do chat: em destaque, o SQL que RODOU."""

    def _render(self, exec_info: dict, answer: dict | None = None):
        doc = Document()
        theme.apply_base_styles(doc)
        report_content.render_answer(doc, answer or _ANSWER, exec_info=exec_info)
        return _body_text(doc)

    def test_corrected_sql_replaces_the_generated_one(self) -> None:
        corrigido = "SELECT uf, total FROM empreendimento_far.mapa_nacional;"
        body = self._render({"exec_ok": True, "sql_final": corrigido})
        self.assertIn("SQL executado", body)
        self.assertIn("SELECT uf, total", body)
        # a caixa de código do SQL gerado (que não rodou) some do relatório
        self.assertNotIn("WHERE nivel", body)
        self.assertIn("ajustado na execução", body)

    def test_failed_execution_shows_the_generated_sql_without_noise(self) -> None:
        body = self._render({
            "exec_ok": False,
            "sql_final": _ANSWER["sql"],
            "exec_error": 'Error: column "x" does not exist',
        })
        self.assertIn("SQL gerado", body)
        self.assertIn("WHERE nivel", body)      # o SQL gerado, inteiro
        self.assertNotIn("does not exist", body)

    def test_without_exec_info_nothing_is_claimed(self) -> None:
        body = self._render({})
        self.assertIn("SQL gerado", body)
        self.assertNotIn("SQL executado", body)


if __name__ == "__main__":
    unittest.main()

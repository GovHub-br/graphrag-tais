"""ReportBuilder — monta o `.docx` do relatório a partir do Markdown do chat.

Entrega o item (d) do ADR-0010. Recebe a MESMA string Markdown que já vai para o
OpenWebUI (explicação + SQL + dicionário de dados + JOINs + gráfico base64),
aplica o tema GovHub (`theme.py`) e converte o conteúdo (`docx_writer.py`) num
documento Word **editável**. Devolve o caminho local do arquivo gerado; o upload
e o link de download ficam por conta do `MinIOUploader` na camada de entrega.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime

from docx import Document
from loguru import logger

from rag.components.report import docx_writer, report_content, theme
from rag.settings.configs import settings


class ReportBuilder:
    """Monta o `.docx` do relatório com a identidade GovHub."""

    def __init__(self, output_dir: str | None = None):
        self.output_dir = output_dir or settings.REPORT_OUTPUT_DIR
        os.makedirs(self.output_dir, exist_ok=True)

    def build_from_answer(
        self,
        answer: dict,
        chart_data_uri: str = "",
        exec_info: dict | None = None,
        title: str | None = None,
        subtitle: str = "",
    ) -> str:
        """Caminho principal (ADR-0010 opção D): monta o relatório a partir do
        `answer` ESTRUTURADO do motor, sem parsear Markdown."""
        doc = self._new_doc(title, subtitle)
        report_content.render_answer(doc, answer, chart_data_uri, exec_info or {})
        return self._save(doc)

    def build(self, markdown: str, title: str | None = None, subtitle: str = "") -> str:
        """Fallback: monta a partir da string Markdown do chat (parser legado)."""
        doc = self._new_doc(title, subtitle)
        docx_writer.render_markdown(doc, markdown)
        return self._save(doc)

    # ------------------------------------------------------------------ #
    def _new_doc(self, title: str | None, subtitle: str):
        doc = Document()
        theme.apply_base_styles(doc)
        theme.build_cover(
            doc,
            title=title or settings.REPORT_TITLE,
            kicker=settings.REPORT_KICKER,
            subtitle=subtitle,
            brand_line=settings.REPORT_FOOTER,
        )
        return doc

    def _save(self, doc) -> str:
        filename = self._unique_filename()
        path = os.path.join(self.output_dir, filename)
        doc.save(path)
        logger.info("[report] .docx gerado: {}", path)
        return path

    @staticmethod
    def _unique_filename() -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"relatorio-govhub-{stamp}-{uuid.uuid4().hex[:6]}.docx"

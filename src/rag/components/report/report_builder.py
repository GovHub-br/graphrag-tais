"""ReportBuilder — monta o `.docx` do relatório a partir do Markdown do chat.

Entrega o item (d) do ADR-0010. Recebe a MESMA string Markdown que já vai para o
OpenWebUI (explicação + SQL + dicionário de dados + JOINs + gráfico base64),
aplica o tema GovHub (`theme.py`) e converte o conteúdo (`docx_writer.py`) num
documento Word **editável**. Devolve o caminho local do arquivo gerado; o upload
e o link de download ficam por conta do `MinIOUploader` na camada de entrega.

Organização do documento (relatório sóbrio da IDV, 15/11):

1. capa branca — título, a pergunta como subtítulo, data;
2. "Identificação" — ficha (pergunta, data, origem dos dados, autoria) e o
   sumário dos capítulos; só quando a pergunta é conhecida (`question`);
3. um capítulo por `## Título` do Markdown, numerado, cada um em página nova;
4. rodapé com número de página, nome do documento e símbolo em toda página
   de conteúdo; Reddit Sans embutida no arquivo.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime

from docx import Document
from loguru import logger

from rag.components.report import docx_writer, report_content, theme
from rag.settings.configs import settings

_MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
          "agosto", "setembro", "outubro", "novembro", "dezembro")

_NOTA = ("Os valores foram calculados no banco analítico no momento da consulta, "
         "com SQL somente leitura. Textos, tabelas e gráficos são os mesmos "
         "apresentados na conversa, na ordem em que foram abertos. O documento é "
         "editável: revise e ajuste o que precisar antes de compartilhar.")


def _data_extenso(quando: datetime) -> str:
    return f"{quando.day} de {_MESES[quando.month - 1]} de {quando.year}"


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
        theme.add_chapter(doc, 1, "Resposta do assistente", "Consulta", new_page=False)
        report_content.render_answer(doc, answer, chart_data_uri, exec_info or {})
        return self._save(doc)

    def build(self, markdown: str, title: str | None = None, subtitle: str = "", *,
              question: str = "", eyebrows: dict | None = None,
              when: datetime | None = None) -> str:
        """Relatório a partir do Markdown (a jornada, e o fallback do parser).

        Com `question`, a capa usa a pergunta como subtítulo, entra a página de
        identificação e cada `## Título` vira capítulo numerado, com o eyebrow
        de `eyebrows`. Sem ela, o comportamento antigo: `subtitle` como veio e
        o Markdown sem capítulos."""
        when = when or datetime.now()
        if not question:
            doc = self._new_doc(title, subtitle)
            docx_writer.render_markdown(doc, markdown)
            return self._save(doc)

        pergunta = question.strip()
        doc = self._new_doc(title, f"“{pergunta}”",
                            meta=f"Gerado em {_data_extenso(when)} · Assistente de Dados TAIS")
        capitulos = [m.group(1).strip() for m in re.finditer(r"(?m)^##\s+(.+)$", markdown or "")]
        docx_writer.add_about(
            doc,
            rows=[("Pergunta", pergunta),
                  ("Gerado em", f"{_data_extenso(when)}, às {when:%H:%M}"),
                  ("Origem dos dados", "Catálogo de metadados OpenMetadata e banco analítico, "
                                       "consultado no momento da pergunta"),
                  ("Gerado por", "Assistente de Dados TAIS · Gov Hub")],
            contents=[(i + 1, t, (eyebrows or {}).get(t, "")) for i, t in enumerate(capitulos)],
            note=_NOTA,
        )
        docx_writer.render_markdown(doc, markdown, eyebrows=eyebrows, chapters=True,
                                    first_on_new_page=True)
        return self._save(doc)

    # ------------------------------------------------------------------ #
    def _new_doc(self, title: str | None, subtitle: str, meta: str = ""):
        doc = Document()
        titulo = title or settings.REPORT_TITLE
        theme.apply_base_styles(doc)
        theme.set_properties(doc, titulo, subtitle)
        theme.build_cover(doc, title=titulo, subtitle=subtitle, meta=meta, doc_name=settings.REPORT_FOOTER)
        if not theme.embed_fonts(doc):
            logger.warning("[report] Reddit Sans não embutida; o Word usará a fonte substituta")
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

"""Arquivos para baixar: CSV do resultado e relatório `.docx` (ADR-0020).

Reusa o que a Persona 2 já tem — `ReportBuilder` (estilo editorial GovHub),
`MinIOUploader` e o link assinado servido pelo próprio RAG (`/files`). Tudo vai
para o bucket dos relatórios, com a validade dele (`REPORT_PRESIGN_EXPIRY`): o
link aparece numa conversa que a pessoa pode reabrir dias depois, e a validade
curta da amostra do Explorar (15 min) quebraria o botão "Baixar".

Aditivo e defensivo: sem MinIO, devolve string vazia e a resposta sai sem link.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Sequence

from loguru import logger

from rag.settings.configs import settings


def _slug(text: str, size: int = 40) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return base[:size].strip("-") or "resultado"


class Publisher:
    def __init__(self) -> None:
        self._uploader = None
        self._report_builder = None

    def _upload(self, path: str, content_type: str | None = None) -> str:
        from rag.components.storage import MinIOUploader

        if self._uploader is None:
            self._uploader = MinIOUploader(bucket=settings.MINIO_BUCKET,
                                           presign_expiry=settings.REPORT_PRESIGN_EXPIRY)
        return self._uploader.upload(path, content_type=content_type)

    def csv(self, columns: Sequence[str], rows: Sequence[Dict[str, Any]], question: str) -> str:
        try:
            from rag.components.samples.sample_sql import write_csv

            os.makedirs(settings.REPORT_OUTPUT_DIR, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = os.path.join(settings.REPORT_OUTPUT_DIR,
                                f"resultado-{_slug(question)}-{stamp}.csv")
            write_csv(path, list(columns), list(rows))
            return self._upload(path, content_type="text/csv; charset=utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[jornada/baixar] CSV indisponível: {}", exc)
            return ""

    def report(self, markdown: str, question: str) -> str:
        """Relatório da jornada: o Markdown das seções que a pessoa abriu
        (`render.report_markdown`), no tema editorial GovHub."""
        if not settings.REPORT_ENABLED or not (markdown or "").strip():
            return ""
        try:
            from rag.components.report import ReportBuilder

            if self._report_builder is None:
                self._report_builder = ReportBuilder()
            data = datetime.now().strftime("%d/%m/%Y")
            path = self._report_builder.build(
                markdown, subtitle=f'Pergunta: "{question.strip()[:140]}"   ·   {data}')
            return self._upload(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[jornada/baixar] relatório indisponível: {}", exc)
            return ""

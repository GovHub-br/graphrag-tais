"""Rota de download dos artefatos (relatório `.docx`, CSV de amostra).

O RAG serve o arquivo; o navegador **nunca fala com o MinIO**. Motivo em
`components/storage/download_links.py`: só o RAG tem URL pública — o MinIO vive
na rede privada do compose, então a URL assinada do S3 (que embute o host)
apontava para um endereço inalcançável fora do Docker.

Contrato: `GET /files/{bucket}/{objeto}?exp=<unix>&sig=<hmac>`. Sem assinatura
válida não há resposta — a rota não lista, não busca e não aceita bucket fora do
par conhecido (`MINIO_BUCKET`, `SAMPLE_BUCKET`).
"""
from __future__ import annotations

import os
import unicodedata
from urllib.parse import quote

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from loguru import logger

from rag.components.storage import download_links, minio_uploader
from rag.settings.configs import settings

router = APIRouter(tags=["downloads"])

_CHUNK = 64 * 1024


def _allowed_buckets() -> set[str]:
    """Defesa em profundidade: mesmo com a chave de assinatura, o RAG só serve
    os buckets que ele próprio publica — nunca vira leitor genérico de S3."""
    return {settings.MINIO_BUCKET, settings.SAMPLE_BUCKET}


def _content_disposition(object_name: str) -> str:
    """`attachment` com o nome do arquivo (ASCII + RFC 5987 para acentos)."""
    filename = os.path.basename(object_name) or "arquivo"
    ascii_name = (
        unicodedata.normalize("NFKD", filename)
        .encode("ascii", "ignore")
        .decode("ascii")
        .replace('"', "")
    ) or "arquivo"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@router.get(
    f"{download_links.DOWNLOAD_PREFIX}/{{bucket}}/{{object_name:path}}",
    name="download_file",
    summary="Baixa um artefato publicado pelo RAG (link assinado)",
)
def download_file(
    bucket: str,
    object_name: str,
    exp: int = Query(..., description="Instante de expiração (epoch), assinado"),
    sig: str = Query(..., description="HMAC-SHA256 de (bucket, objeto, exp)"),
) -> StreamingResponse:
    if bucket not in _allowed_buckets():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")

    try:
        download_links.verify(bucket, object_name, exp, sig)
    except download_links.LinkExpired:
        raise HTTPException(
            status_code=410,
            detail="Este link de download expirou. Refaça a pergunta para gerar outro.",
        ) from None
    except download_links.DownloadLinkError:
        # Não diz se o objeto existe: link inválido e objeto inexistente têm a
        # mesma cara para quem está tentando adivinhar.
        raise HTTPException(status_code=403, detail="Link de download inválido.") from None

    try:
        obj = minio_uploader.get_object(bucket, object_name)
    except ClientError as e:  # noqa: BLE001 — some do log do MinIO, vira 404/502 aqui
        code = e.response.get("Error", {}).get("Code", "")
        if code in {"NoSuchKey", "NoSuchBucket", "404"}:
            raise HTTPException(status_code=404, detail="Arquivo não encontrado.") from None
        logger.error("[download] MinIO indisponível para {}/{}: {}", bucket, object_name, e)
        raise HTTPException(status_code=502, detail="Armazenamento indisponível.") from None

    body = obj["Body"]
    headers = {
        "Content-Disposition": _content_disposition(object_name),
        # O link é pessoal e expira: nada de cache compartilhado.
        "Cache-Control": "private, no-store",
    }
    if obj.get("ContentLength") is not None:
        headers["Content-Length"] = str(obj["ContentLength"])

    logger.info("[download] servindo {}/{}", bucket, object_name)
    return StreamingResponse(
        body.iter_chunks(_CHUNK),
        media_type=obj.get("ContentType") or "application/octet-stream",
        headers=headers,
    )

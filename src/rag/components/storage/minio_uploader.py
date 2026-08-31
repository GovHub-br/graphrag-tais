"""MinIOUploader — sobe o `.docx` no MinIO e devolve o link de download do RAG.

MinIO é S3-compatível, então usamos `boto3`. Um único client, **interno**
(`MINIO_ENDPOINT`, ex. `http://minio:9000`): tanto o upload quanto a leitura do
download acontecem dentro da rede do compose.

⚠️ O link devolvido **não aponta para o MinIO** — aponta para o próprio RAG
(`/files/...`, ver `download_links.py`). O navegador do usuário não alcança o
MinIO: em produção ele não tem URL pública, e a URL assinada do S3 (que embute o
host) levava o navegador a um endereço que só o compose enxerga.
"""
from __future__ import annotations

import os
from functools import lru_cache

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from loguru import logger

from rag.components.storage.download_links import build_download_url
from rag.settings.configs import settings

_DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


@lru_cache(maxsize=1)
def internal_client():
    """Client S3 do endpoint interno, compartilhado (upload e download)."""
    return boto3.client(
        "s3",
        endpoint_url=settings.MINIO_ENDPOINT,
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY,
        region_name=settings.MINIO_REGION,
        config=Config(signature_version="s3v4"),
    )


def get_object(bucket: str, object_name: str) -> dict:
    """Resposta crua do `get_object` (Body streamado, ContentType, ContentLength).

    É o que a rota `/files/...` do RAG repassa ao navegador."""
    return internal_client().get_object(Bucket=bucket, Key=object_name)


class MinIOUploader:
    def __init__(self, bucket: str | None = None, presign_expiry: int | None = None):
        # Parametrizável para a amostra de dados da Persona 1: LINHAS do banco
        # não moram no mesmo bucket que relatórios, e a URL delas expira antes.
        self.bucket = bucket or settings.MINIO_BUCKET
        # `presign_expiry`: nome herdado da URL assinada do S3. Hoje é a
        # validade do link do RAG — mantido para não mexer no contrato do `.env`.
        self.presign_expiry = presign_expiry or settings.REPORT_PRESIGN_EXPIRY
        self._internal = internal_client()
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            self._internal.head_bucket(Bucket=self.bucket)
        except ClientError:
            logger.info("[report] criando bucket MinIO '{}'", self.bucket)
            try:
                self._internal.create_bucket(Bucket=self.bucket)
            except ClientError as e:  # noqa: BLE001 — corrida/permite existir
                logger.warning("[report] create_bucket: {}", e)

    def upload(self, local_path: str, object_name: str | None = None,
               content_type: str | None = None) -> str:
        """Sobe o arquivo e devolve o link de download (servido pelo RAG)."""
        object_name = object_name or os.path.basename(local_path)
        content_type = content_type or _DOCX_CONTENT_TYPE
        self._internal.upload_file(
            local_path,
            self.bucket,
            object_name,
            ExtraArgs={"ContentType": content_type},
        )
        logger.info("[storage] enviado ao MinIO: {}/{}", self.bucket, object_name)
        return build_download_url(self.bucket, object_name, self.presign_expiry)

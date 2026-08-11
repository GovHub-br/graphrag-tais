"""MinIOUploader — sobe o `.docx` no MinIO e devolve uma URL assinada.

MinIO é S3-compatível, então usamos `boto3`. Dois clients de propósito:

- **interno** (`MINIO_ENDPOINT`, ex. `http://minio:9000`): usado dentro da rede do
  compose para *subir* o arquivo e garantir o bucket.
- **público** (`MINIO_PUBLIC_ENDPOINT`, ex. `http://localhost:9000`): usado só para
  *assinar* a URL de download. A assinatura SigV4 inclui o host; se assinássemos com
  `minio:9000` (nome de serviço interno), o link **quebraria no navegador** do
  usuário. Por isso a URL é assinada contra o endpoint público, acessível de fora.
"""
from __future__ import annotations

import os

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from loguru import logger

from rag.settings.configs import settings

_DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


class MinIOUploader:
    def __init__(self):
        self.bucket = settings.MINIO_BUCKET
        common = dict(
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            region_name=settings.MINIO_REGION,
            config=Config(signature_version="s3v4"),
        )
        self._internal = boto3.client(
            "s3", endpoint_url=settings.MINIO_ENDPOINT, **common
        )
        # cliente só para assinar (host acessível pelo navegador do usuário)
        public_endpoint = settings.MINIO_PUBLIC_ENDPOINT or settings.MINIO_ENDPOINT
        self._public = boto3.client(
            "s3", endpoint_url=public_endpoint, **common
        )
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

    def upload(self, local_path: str, object_name: str | None = None) -> str:
        """Sobe o arquivo e devolve uma URL assinada de download (attachment)."""
        object_name = object_name or os.path.basename(local_path)
        self._internal.upload_file(
            local_path,
            self.bucket,
            object_name,
            ExtraArgs={"ContentType": _DOCX_CONTENT_TYPE},
        )
        logger.info("[report] .docx enviado ao MinIO: {}/{}", self.bucket, object_name)
        return self._presigned_url(object_name)

    def _presigned_url(self, object_name: str) -> str:
        disposition = f'attachment; filename="{object_name}"'
        return self._public.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": object_name,
                "ResponseContentDisposition": disposition,
                "ResponseContentType": _DOCX_CONTENT_TYPE,
            },
            ExpiresIn=settings.REPORT_PRESIGN_EXPIRY,
        )

"""Armazenamento de objetos (MinIO/S3) para artefatos do relatório."""
from rag.components.storage.download_links import build_download_url
from rag.components.storage.minio_uploader import MinIOUploader, get_object

__all__ = ["MinIOUploader", "build_download_url", "get_object"]

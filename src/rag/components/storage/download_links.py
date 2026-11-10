"""Links de download servidos pelo PRÓPRIO RAG (não pelo MinIO).

Por que existe: o navegador do usuário **não alcança o MinIO**. Em produção o
`minio` só existe dentro da rede do compose — quem tem URL pública é o RAG
(hayhooks). A URL assinada (SigV4) que o `MinIOUploader` gerava era, portanto,
um link para um host que só o próprio compose enxerga: o download morria no
navegador. Agora o RAG é o **proxy** — ele serve o objeto em `/files/...` e a
requisição do navegador nunca toca o MinIO.

Perdendo a assinatura do S3, o link precisa de uma própria — senão `/files/...`
viraria leitura anônima de bucket. É o que este módulo faz: um HMAC-SHA256 do
`(bucket, objeto, expiração)` que só o RAG sabe calcular. Mesma semântica de
antes (link **opaco e expirável**), com a validade continuando nas configs de
sempre (`REPORT_PRESIGN_EXPIRY`, `SAMPLE_PRESIGN_EXPIRY`).
"""
from __future__ import annotations

import hmac
import time
from hashlib import sha256
from urllib.parse import quote, urlparse

from rag.settings.configs import settings

#: Prefixo da rota de download no app do RAG (ver `rag/server/downloads.py`).
DOWNLOAD_PREFIX = "/files"


class DownloadLinkError(Exception):
    """Base dos erros de validação do link."""


class LinkInvalid(DownloadLinkError):
    """Assinatura ausente, malformada ou que não confere."""


class LinkExpired(DownloadLinkError):
    """Assinatura válida, mas o prazo do link passou."""


def _signing_key() -> bytes:
    """Chave do HMAC. Sem `DOWNLOAD_SIGNING_KEY`, usa a secret do MinIO: já é
    segredo do mesmo domínio (quem a tem já lê o bucket inteiro), então não
    obriga ninguém a inventar mais uma variável no `.env`."""
    return (settings.DOWNLOAD_SIGNING_KEY or settings.MINIO_SECRET_KEY).encode("utf-8")


def _signature(bucket: str, object_name: str, expires_at: int) -> str:
    # `\n` como separador: nenhum dos três campos contém quebra de linha, então
    # não há como um objeto se passar por outro deslocando a fronteira.
    payload = f"{bucket}\n{object_name}\n{expires_at}".encode("utf-8")
    return hmac.new(_signing_key(), payload, sha256).hexdigest()


def build_download_url(bucket: str, object_name: str, expires_in: int) -> str:
    """URL absoluta e assinada, na base pública do RAG (`PUBLIC_BASE_URL`)."""
    expires_at = int(time.time()) + int(expires_in)
    path = f"{DOWNLOAD_PREFIX}/{quote(bucket, safe='')}/{quote(object_name)}"
    signature = _signature(bucket, object_name, expires_at)
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    return f"{base}{path}?exp={expires_at}&sig={signature}"


# Gráfico interativo da jornada (`rag/server/charts.py`). Mesma chave e mesmo
# HMAC dos downloads, com "charts" no lugar do bucket: um link de gráfico não
# serve para baixar arquivo e vice-versa.
CHART_PREFIX = "/charts"
_CHART_SCOPE = "charts"


def build_chart_path(ref: str, kind: str, expires_in: int) -> str:
    """Caminho RELATIVO e assinado do gráfico interativo de um resultado.

    Relativo de propósito: quem busca é a Function do Open WebUI, pela rede
    interna dela até o RAG (`RAG_URL` da Function), não o navegador."""
    expires_at = int(time.time()) + int(expires_in)
    nome = f"{ref}/{kind}"
    return (f"{CHART_PREFIX}/{quote(ref, safe='')}/{quote(kind, safe='')}"
            f"?exp={expires_at}&sig={_signature(_CHART_SCOPE, nome, expires_at)}")


def verify_chart(ref: str, kind: str, expires_at: int, signature: str) -> None:
    verify(_CHART_SCOPE, f"{ref}/{kind}", expires_at, signature)


def base_url_looks_local(base_url: str | None = None) -> bool:
    """`PUBLIC_BASE_URL` ainda aponta para a própria máquina?

    Existe porque esta é a falha mais silenciosa do download: o link sai
    **bonito e assinado**, a rota funciona, o arquivo está no MinIO — e mesmo
    assim ninguém baixa, porque `http://localhost:8000` no navegador de quem
    clica é a máquina DELE, não o servidor. Sem checagem, o sintoma ("o botão
    não faz nada") não aponta para a causa (uma variável faltando no `.env`).
    """
    host = urlparse(base_url if base_url is not None else settings.PUBLIC_BASE_URL).hostname or ""
    return host.lower() in {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}


def verify(bucket: str, object_name: str, expires_at: int, signature: str) -> None:
    """Valida o link; não devolve nada, levanta `DownloadLinkError` no erro.

    A expiração entra no HMAC — esticar o `exp` na mão quebra a assinatura. Por
    isso a ordem: confere a assinatura ANTES do prazo (um `exp` não assinado não
    merece nem a resposta 'expirado')."""
    expected = _signature(bucket, object_name, expires_at)
    if not hmac.compare_digest(expected, signature or ""):
        raise LinkInvalid("assinatura inválida para este arquivo")
    if expires_at < int(time.time()):
        raise LinkExpired("link de download expirado")

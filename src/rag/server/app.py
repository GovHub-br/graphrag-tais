"""App FastAPI do RAG = app do Hayhooks + as rotas próprias do projeto.

O `hayhooks run` monta o app internamente e **não expõe gancho** para rotas
custom (`create_app()` devolve um app novo a cada chamada, sem hook de
extensão). Por isso o compose sobe ESTE factory pelo uvicorn — que é
exatamente o que o `hayhooks run --reload` faz por baixo
(`uvicorn hayhooks.server.app:create_app --factory`).

Config do Hayhooks continua vindo do ambiente (`HAYHOOKS_PIPELINES_DIR`,
`HAYHOOKS_ADDITIONAL_PYTHON_PATH`), que é como o CLI a propaga para os workers.
"""
from __future__ import annotations

from fastapi import FastAPI
from hayhooks import create_app as create_hayhooks_app
from loguru import logger

from rag.components.storage import download_links
from rag.server.downloads import router as downloads_router
from rag.settings.configs import settings


def _check_public_base_url() -> None:
    """Avisa no boot se os links de download vão sair inalcançáveis.

    `PUBLIC_BASE_URL` é a única peça do download que o RAG não consegue
    descobrir sozinho: o link é gerado dentro da pipeline, longe da requisição,
    então não dá para deduzir o host do `Host:` recebido. Quando a variável
    falta no `.env`, o default (`localhost:8000`) produz link que **abre na
    máquina de quem clicou** — o arquivo existe, a rota responde, e ainda assim
    o download falha para todo mundo que não estiver no servidor. Falha assim
    não se debuga pelo sintoma; por isso ela grita aqui, uma vez, no boot."""
    if download_links.base_url_looks_local():
        logger.warning(
            "PUBLIC_BASE_URL={} aponta para a máquina local: os links de download "
            "(.docx e CSV de amostra) só vão funcionar para quem abrir o navegador "
            "NO PRÓPRIO servidor. Em produção, defina PUBLIC_BASE_URL com a URL "
            "pública do RAG (ex.: https://rag.exemplo.gov.br).",
            settings.PUBLIC_BASE_URL,
        )


def create_app() -> FastAPI:
    app = create_hayhooks_app()
    app.include_router(downloads_router)
    _check_public_base_url()
    return app

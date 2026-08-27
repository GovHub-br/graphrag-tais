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

from rag.server.downloads import router as downloads_router


def create_app() -> FastAPI:
    app = create_hayhooks_app()
    app.include_router(downloads_router)
    return app

"""Rota do gráfico interativo da jornada: `GET /charts/{ref}/{tipo}`.

Quem chama é a Function do Open WebUI (`webui/functions/grafico_interativo.py`)
logo depois de cada resposta: ela lê o caminho assinado na linha escondida
`tais:grafico` e põe o HTML devolvido aqui como "embed" embaixo da mensagem.

O HTML é montado na hora, do MESMO resultado guardado que as trilhas usam
(`journey.state`, chave `ref`; para a série histórica da política, a
consulta de série que a trilha guardou em `state["series"]`): nenhum SQL novo, e o gráfico sempre bate com o
número da resposta. Depois que o Open WebUI grava o embed na conversa, o
gráfico não depende mais desta rota (nem do prazo do resultado no Redis).

Assinatura e prazo seguem os do download (`download_links.verify_chart`):
inválida → 403, vencida → 410, resultado que já saiu do Redis → 404.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from loguru import logger

from rag.components.storage import download_links

router = APIRouter(tags=["charts"])


@router.get(
    f"{download_links.CHART_PREFIX}/{{ref}}/{{kind}}",
    name="journey_chart",
    summary="Gráfico interativo de um resultado da jornada (link assinado)",
    response_class=HTMLResponse,
)
def journey_chart(
    ref: str,
    kind: str,
    exp: int = Query(..., description="Instante de expiração (epoch), assinado"),
    sig: str = Query(..., description="HMAC-SHA256 de (ref, tipo, exp)"),
) -> HTMLResponse:
    from rag.journey import interactive
    from rag.journey.service import facts_of
    from rag.journey.state import JourneyStore

    if kind not in interactive.KINDS:
        raise HTTPException(status_code=404, detail="Gráfico não encontrado.")
    try:
        download_links.verify_chart(ref, kind, exp, sig)
    except download_links.LinkExpired:
        raise HTTPException(status_code=410, detail="Este gráfico expirou.") from None
    except download_links.DownloadLinkError:
        raise HTTPException(status_code=403, detail="Link de gráfico inválido.") from None

    state = JourneyStore().load(ref)
    if not state or state.get("status") != "ok":
        raise HTTPException(status_code=404, detail="Resultado não encontrado.")
    if kind == interactive.SERIE:
        # A série histórica da política: o resultado da consulta de série que
        # a trilha guardou, ou o próprio resultado quando ele já é no tempo.
        fs = facts_of(state.get("series") or state, prefer_time=True)
        fs.label = state.get("label", "")
        pagina = interactive.page(fs, interactive.ONDE, title="Série histórica")
    else:
        pagina = interactive.page_for(facts_of(state), kind, question=state.get("question", ""))
    if not pagina:
        raise HTTPException(status_code=404, detail="Este resultado não tem gráfico.")
    logger.info("[grafico] servindo ref={} tipo={} ({} KB)", ref, kind, len(pagina) // 1024)
    return HTMLResponse(pagina, headers={"Cache-Control": "private, no-store"})

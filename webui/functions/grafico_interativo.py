"""
title: Gráfico interativo (GovHub)
author: GovHub
version: 0.2.0
description: Põe embaixo da resposta do Assistente os gráficos em que se lê o valor exato ao passar o cursor ou tocar.
"""
# Function de FILTRO do Open WebUI (instalada por scripts/setup_webui_models.sh;
# NÃO é importada pelo RAG). Roda no `outlet`, depois que a resposta termina:
#
#   1. procura na resposta as linhas escondidas que o RAG deixa, uma por
#      gráfico: `[//]: # (tais:grafico /charts/<ref>/<tipo>?exp=…&sig=…)`
#      (a trilha do gestor tem duas: ranking e pontos fora da curva);
#   2. busca o HTML de cada uma no RAG (`RAG_URL` + caminho assinado), pela
#      rede do Open WebUI até o RAG — o navegador não precisa alcançar o RAG;
#   3. manda UM evento `embeds` com todos, na ordem da resposta: o Open WebUI
#      grava a lista na mensagem e mostra cada HTML num iframe
#      (`allow-scripts`) embaixo dela.
#
# Gravado na mensagem, o gráfico continua lá ao reabrir a conversa, mesmo
# depois que o resultado vence no Redis. Sem a linha escondida (Indicador,
# Explorar, resposta sem gráfico), não faz nada. Falha ao buscar também não:
# a resposta já tem o PNG e a tabela de valores.
import re
from typing import List, Optional

from pydantic import BaseModel, Field

_LINHA = re.compile(r"\[//\]: # \(tais:grafico (/charts/[^)\s]+)\)")


class Filter:
    class Valves(BaseModel):
        RAG_URL: str = Field(
            default="http://hayhooks:8000",
            description="Endereço do RAG visto de DENTRO do Open WebUI (rede do compose ou URL pública).",
        )
        TIMEOUT: int = Field(default=20, description="Segundos para buscar o gráfico.")

    def __init__(self):
        self.valves = self.Valves()

    async def outlet(self, body: dict, __event_emitter__=None) -> dict:
        if __event_emitter__ is None:
            return body
        embeds = []
        for caminho in self._chart_paths(body):
            html = await self._fetch(caminho)
            if html:
                embeds.append(html)
        if embeds:
            await __event_emitter__({"type": "embeds", "data": {"embeds": embeds}})
        return body

    @staticmethod
    def _chart_paths(body: dict) -> List[str]:
        """Os caminhos da ÚLTIMA resposta, na ordem, sem repetir."""
        for message in reversed(body.get("messages") or []):
            if message.get("role") != "assistant":
                continue
            conteudo = message.get("content") or ""
            if isinstance(conteudo, list):
                conteudo = " ".join(p.get("text", "") for p in conteudo if isinstance(p, dict))
            return list(dict.fromkeys(_LINHA.findall(conteudo)))
        return []

    @classmethod
    def _chart_path(cls, body: dict) -> Optional[str]:
        """O último gráfico da resposta (compatibilidade da v0.1.0)."""
        caminhos = cls._chart_paths(body)
        return caminhos[-1] if caminhos else None

    async def _fetch(self, caminho: str) -> Optional[str]:
        import aiohttp

        url = self.valves.RAG_URL.rstrip("/") + caminho
        try:
            timeout = aiohttp.ClientTimeout(total=self.valves.TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as sessao:
                async with sessao.get(url) as resposta:
                    if resposta.status != 200:
                        print(f"[grafico_interativo] {resposta.status} em {caminho.split('?')[0]}")
                        return None
                    return await resposta.text()
        except Exception as exc:  # noqa: BLE001 — gráfico é extra; a resposta já está na tela
            print(f"[grafico_interativo] falhou: {exc}")
            return None

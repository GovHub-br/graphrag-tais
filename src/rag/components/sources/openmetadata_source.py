"""
OpenMetadataSource — componente Haystack custom que puxa metadados da REST API
do OpenMetadata (tabelas+colunas+constraints, métricas, glossário e, opcional,
lineage) e devolve as entidades cruas para o GraphCardBuilder.

⚠ Os campos/rotas seguem a API v1 do OpenMetadata, mas variam por versão do OM.
   Ajuste `fields=` e o parsing conforme a sua instância (Swagger em {OM_HOST}/docs).
"""
from typing import Any, Dict, List

import httpx
from loguru import logger
from haystack import component

from rag.settings.configs import settings


@component
class OpenMetadataSource:
    def __init__(
        self,
        host: str | None = None,
        api_path: str | None = None,
        jwt_token: str | None = None,
        page_size: int | None = None,
        timeout: int | None = None,
        with_lineage: bool = True,
    ):
        self.base = (host or settings.OM_HOST).rstrip("/") + (api_path or settings.OM_API_PATH)
        self.jwt_token = jwt_token if jwt_token is not None else settings.OM_JWT_TOKEN
        self.page_size = page_size or settings.OM_PAGE_SIZE
        self.timeout = timeout or settings.OM_TIMEOUT
        self.with_lineage = with_lineage

    def _headers(self) -> dict:
        h = {"Accept": "application/json"}
        if self.jwt_token:
            h["Authorization"] = f"Bearer {self.jwt_token}"
        return h

    def _get_paginated(self, client: httpx.Client, endpoint: str, fields: str = "") -> List[dict]:
        """Percorre a paginação por cursor (`paging.after`) do OpenMetadata."""
        items: List[dict] = []
        after = None
        while True:
            params: Dict[str, Any] = {"limit": self.page_size}
            if fields:
                params["fields"] = fields
            if after:
                params["after"] = after
            resp = client.get(f"{self.base}{endpoint}", params=params, headers=self._headers())
            resp.raise_for_status()
            payload = resp.json()
            items.extend(payload.get("data", []))
            after = (payload.get("paging") or {}).get("after")
            if not after:
                break
        return items

    def _get_lineage(self, client: httpx.Client, table_fqn: str) -> dict:
        try:
            resp = client.get(
                f"{self.base}/lineage/table/name/{table_fqn}",
                params={"upstreamDepth": 1, "downstreamDepth": 1},
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # lineage é best-effort
            logger.warning("Lineage indisponível para %s: %s", table_fqn, e)
            return {}

    @component.output_types(entities=Dict[str, Any])
    def run(self) -> dict:
        with httpx.Client(timeout=self.timeout) as client:
            logger.info("OpenMetadata: buscando tables...")
            tables = self._get_paginated(
                client, "/tables",
                fields="columns,tableConstraints,database,databaseSchema",
            )
            logger.info("OpenMetadata: buscando metrics...")
            metrics = self._safe_list(client, "/metrics", fields="")
            logger.info("OpenMetadata: buscando glossaryTerms...")
            glossary = self._safe_list(client, "/glossaryTerms", fields="")

            lineage: List[dict] = []
            if self.with_lineage:
                logger.info("OpenMetadata: buscando lineage de %d tabelas...", len(tables))
                for t in tables:
                    fqn = t.get("fullyQualifiedName")
                    if fqn:
                        lg = self._get_lineage(client, fqn)
                        if lg:
                            lineage.append(lg)

        logger.info(
            "OpenMetadata: %d tables, %d metrics, %d termos, %d lineage docs",
            len(tables), len(metrics), len(glossary), len(lineage),
        )
        return {
            "entities": {
                "tables": tables,
                "metrics": metrics,
                "glossary": glossary,
                "lineage": lineage,
            }
        }

    def _safe_list(self, client: httpx.Client, endpoint: str, fields: str) -> List[dict]:
        """Alguns endpoints podem não existir/estar vazios — não deixa quebrar a ingestão."""
        try:
            return self._get_paginated(client, endpoint, fields=fields)
        except Exception as e:
            logger.warning("Endpoint %s indisponível: %s", endpoint, e)
            return []

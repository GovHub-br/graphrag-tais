"""Leitura autoritativa e limitada do OpenMetadata para a ingestão do MCID.

O componente lê apenas ``Table`` e suas ``Column`` aninhadas. Ele não consulta
amostras, perfis, métricas, glossário ou lineage. Todas as páginas e todos os
schemas são lidos antes de o snapshot ser liberado para os writers.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Mapping
from typing import Any, Dict
from uuid import uuid4

import httpx
from haystack import component
from loguru import logger

from rag.settings.configs import settings


class OpenMetadataContractError(RuntimeError):
    """A resposta do OpenMetadata não satisfaz o contrato da ingestão."""


@component
class OpenMetadataSource:
    """Produz um snapshot completo do escopo configurado no OpenMetadata."""

    _TABLE_FIELDS = "columns,tableConstraints,tags,extension,dataProducts"

    def __init__(
        self,
        host: str | None = None,
        api_path: str | None = None,
        jwt_token: str | None = None,
        scope_id: str | None = None,
        service: str | None = None,
        database: str | None = None,
        schemas: str | list[str] | tuple[str, ...] | None = None,
        expected_version: str | None = None,
        page_size: int | None = None,
        timeout: int | None = None,
        max_retries: int | None = None,
        require_documented_tables: bool | None = None,
        documentation_property: str | None = None,
        extension_overrides_file: str | None = None,
    ) -> None:
        self.base = (host or settings.OM_HOST).rstrip("/") + (
            api_path or settings.OM_API_PATH
        )
        self.jwt_token = jwt_token if jwt_token is not None else settings.OM_JWT_TOKEN
        self.scope_id = (scope_id or settings.OM_SCOPE_ID or "").strip()
        self.service = (service or settings.OM_SERVICE or "").strip()
        self.database = (database or settings.OM_DATABASE or "").strip()
        self.schemas = self._parse_schemas(
            schemas if schemas is not None else settings.OM_SCHEMAS
        )
        self.expected_version = (
            expected_version or settings.OM_EXPECTED_VERSION or ""
        ).strip()
        self.page_size = page_size or settings.OM_PAGE_SIZE
        self.timeout = timeout or settings.OM_TIMEOUT
        self.max_retries = (
            max_retries if max_retries is not None else settings.OM_MAX_RETRIES
        )
        self.require_documented_tables = (
            require_documented_tables
            if require_documented_tables is not None
            else settings.OM_REQUIRE_DOCUMENTED_TABLES
        )
        self.documentation_property = (
            documentation_property or settings.OM_SEMANTIC_RELATIONSHIPS_PROPERTY
        ).strip()
        self.extension_overrides = self._load_extension_overrides(
            extension_overrides_file
            if extension_overrides_file is not None
            else settings.OM_EXTENSION_OVERRIDES_FILE
        )

        self._validate_config()

    @staticmethod
    def _load_extension_overrides(path: str) -> dict[str, dict[str, Any]]:
        if not (path or "").strip():
            return {}
        with open(path, encoding="utf-8") as handle:
            overrides = json.load(handle)
        if not isinstance(overrides, Mapping) or not all(
            isinstance(value, Mapping) for value in overrides.values()
        ):
            raise OpenMetadataContractError(
                f"OM_EXTENSION_OVERRIDES_FILE inválido: {path}"
            )
        # Alto de propósito: o snapshot deixa de ser o que o OpenMetadata
        # publica, e isso não pode passar despercebido num log de produção.
        logger.warning(
            "OM_EXTENSION_OVERRIDES_FILE ativo ({} tabelas): a extension lida do "
            "OpenMetadata será SOBREPOSTA. Uso só para desenvolvimento.",
            len(overrides),
        )
        return {str(fqn): dict(value) for fqn, value in overrides.items()}

    @staticmethod
    def _parse_schemas(
        schemas: str | list[str] | tuple[str, ...] | None,
    ) -> tuple[str, ...]:
        if isinstance(schemas, str):
            values = schemas.split(",")
        else:
            values = list(schemas or [])
        return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))

    def _validate_config(self) -> None:
        missing = [
            name
            for name, value in (
                ("OM_SCOPE_ID", self.scope_id),
                ("OM_SERVICE", self.service),
                ("OM_DATABASE", self.database),
                ("OM_SCHEMAS", self.schemas),
                ("OM_EXPECTED_VERSION", self.expected_version),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Configuração OpenMetadata incompleta: " + ", ".join(missing)
            )
        if self.page_size <= 0:
            raise ValueError("OM_PAGE_SIZE deve ser maior que zero")
        if self.max_retries < 0:
            raise ValueError("OM_MAX_RETRIES não pode ser negativo")
        if self.require_documented_tables and not self.documentation_property:
            raise ValueError(
                "OM_SEMANTIC_RELATIONSHIPS_PROPERTY é obrigatório quando "
                "OM_REQUIRE_DOCUMENTED_TABLES=true"
            )

        expected_prefix = f"{self.service}.{self.database}."
        invalid_schemas = [
            schema
            for schema in self.schemas
            if not schema.startswith(expected_prefix)
            or len(schema.split(".")) != 3
        ]
        if invalid_schemas:
            raise ValueError(
                "OM_SCHEMAS deve conter FQNs no escopo "
                f"{expected_prefix}<schema>: {invalid_schemas}"
            )

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.jwt_token:
            headers["Authorization"] = f"Bearer {self.jwt_token.strip()}"
        return headers

    def _request(
        self,
        client: httpx.Client,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Executa GET com retry apenas para timeout, 429 e 5xx."""
        url = f"{self.base}{endpoint}"
        attempts = self.max_retries + 1

        for attempt in range(attempts):
            try:
                response = client.get(url, params=params, headers=self._headers())
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt + 1 >= attempts:
                    raise
                self._sleep_before_retry(attempt, None)
                continue

            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt + 1 < attempts:
                self._sleep_before_retry(attempt, response.headers.get("Retry-After"))
                continue

            response.raise_for_status()
            return response

        raise AssertionError("loop de retry terminou sem resposta")

    @staticmethod
    def _sleep_before_retry(attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                delay = max(0.0, float(retry_after))
            except ValueError:
                delay = 0.0
        else:
            delay = min(30.0, (2**attempt) + random.uniform(0.0, 0.25))
        time.sleep(delay)

    def _read_version(self, client: httpx.Client) -> str:
        response = self._request(client, "/system/version")
        payload = response.json()
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("version"), str
        ):
            raise OpenMetadataContractError(
                "GET /system/version retornou um payload incompatível"
            )
        version = payload["version"].strip()
        if version != self.expected_version:
            raise OpenMetadataContractError(
                "Versão do OpenMetadata incompatível: "
                f"esperada={self.expected_version!r}, recebida={version!r}"
            )
        return version

    def _read_schema(
        self,
        client: httpx.Client,
        schema_fqn: str,
    ) -> tuple[list[dict[str, Any]], int]:
        tables: list[dict[str, Any]] = []
        after: str | None = None
        pages = 0

        while True:
            params: dict[str, Any] = {
                "databaseSchema": schema_fqn,
                "fields": self._TABLE_FIELDS,
                "include": "non-deleted",
                "limit": self.page_size,
            }
            if after:
                params["after"] = after

            payload = self._request(client, "/tables", params=params).json()
            if not isinstance(payload, Mapping) or not isinstance(
                payload.get("data"), list
            ):
                raise OpenMetadataContractError(
                    f"Listagem de tabelas incompatível para {schema_fqn}"
                )
            paging = payload.get("paging") or {}
            if not isinstance(paging, Mapping):
                raise OpenMetadataContractError(
                    f"paging incompatível para {schema_fqn}"
                )

            pages += 1
            tables.extend(payload["data"])
            next_after = paging.get("after")
            if next_after is None:
                break
            if not isinstance(next_after, str) or not next_after.strip():
                raise OpenMetadataContractError(
                    f"paging.after inválido para {schema_fqn}"
                )
            if next_after == after:
                raise OpenMetadataContractError(
                    f"paging.after repetido para {schema_fqn}"
                )
            after = next_after

        return tables, pages

    @staticmethod
    def _reference_matches(reference: Any, allowed: str) -> bool:
        if not isinstance(reference, Mapping):
            return False
        return allowed in {
            str(reference.get("name") or "").strip(),
            str(reference.get("fullyQualifiedName") or "").strip(),
        }

    def _accept_table(
        self,
        table: Any,
        schema_fqn: str,
    ) -> tuple[bool, str | None]:
        if not isinstance(table, Mapping):
            return False, "table_not_object"

        fqn = table.get("fullyQualifiedName")
        if not isinstance(fqn, str) or not fqn.strip():
            return False, "missing_table_fqn"
        if not self._reference_matches(table.get("service"), self.service):
            return False, "service_out_of_scope"
        if not self._reference_matches(table.get("database"), self.database):
            return False, "database_out_of_scope"
        if not self._reference_matches(table.get("databaseSchema"), schema_fqn):
            return False, "schema_out_of_scope"
        if not fqn.startswith(f"{schema_fqn}."):
            return False, "table_fqn_out_of_scope"

        if self.require_documented_tables:
            extension = table.get("extension")
            documentation = (
                extension.get(self.documentation_property)
                if isinstance(extension, Mapping)
                else None
            )
            if not isinstance(documentation, str) or not documentation.strip():
                return False, "missing_documentation_marker"

        return True, None

    @component.output_types(snapshot=Dict[str, Any])
    def run(self) -> Dict[str, Any]:
        ingestion_run_id = str(uuid4())
        accepted: dict[str, dict[str, Any]] = {}
        rejected_by_reason: dict[str, int] = {}
        pages_by_schema: dict[str, int] = {}
        read_by_schema: dict[str, int] = {}
        overrides_applied = 0

        with httpx.Client(timeout=self.timeout) as client:
            source_version = self._read_version(client)
            for schema_fqn in self.schemas:
                raw_tables, pages = self._read_schema(client, schema_fqn)
                pages_by_schema[schema_fqn] = pages
                read_by_schema[schema_fqn] = len(raw_tables)
                for table in raw_tables:
                    override = self.extension_overrides.get(
                        str(table.get("fullyQualifiedName") or "").strip()
                    )
                    if override:
                        table = dict(table)
                        table["extension"] = {
                            **dict(table.get("extension") or {}),
                            **override,
                        }
                        overrides_applied += 1
                    is_accepted, reason = self._accept_table(table, schema_fqn)
                    if not is_accepted:
                        rejected_by_reason[reason or "unknown"] = (
                            rejected_by_reason.get(reason or "unknown", 0) + 1
                        )
                        continue
                    fqn = str(table["fullyQualifiedName"]).strip()
                    if fqn in accepted:
                        raise OpenMetadataContractError(
                            f"Tabela duplicada no snapshot: {fqn}"
                        )
                    accepted[fqn] = dict(table)

        if not accepted:
            raise OpenMetadataContractError(
                "Nenhuma tabela documentada foi aceita no escopo configurado"
            )

        snapshot = {
            "scope_id": self.scope_id,
            "service": self.service,
            "database": self.database,
            "schemas": list(self.schemas),
            "source_version": source_version,
            "ingestion_run_id": ingestion_run_id,
            "tables": [accepted[fqn] for fqn in sorted(accepted)],
            "stats": {
                "pages_by_schema": pages_by_schema,
                "tables_read_by_schema": read_by_schema,
                "tables_read": sum(read_by_schema.values()),
                "tables_accepted": len(accepted),
                "tables_rejected": sum(rejected_by_reason.values()),
                "tables_rejected_by_reason": rejected_by_reason,
                "extension_overrides_applied": overrides_applied,
            },
        }
        logger.info(
            "OpenMetadata snapshot {}: {} tabelas aceitas, {} rejeitadas",
            ingestion_run_id,
            len(accepted),
            sum(rejected_by_reason.values()),
        )
        return {"snapshot": snapshot}

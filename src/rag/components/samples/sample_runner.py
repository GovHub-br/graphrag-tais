"""SampleRunner — a amostra de dados reais da Persona 1.

Quem explora não conhece as bases: ver **dez linhas de verdade** responde uma
pergunta que nenhuma descrição de catálogo responde ("o que tem dentro dessa
coluna?"). O modo Explorar já sugeria um `sample_sql`; o que faltava era rodá-lo.

Escada de execução, do mais barato ao mais caro — a ordem é a decisão de projeto:

1. **guarda determinística** (`sample_sql.guard`): leitura só, com teto de linhas;
2. **execução direta** pelo MCP de SQL, **sem LLM** (`MCP_DIRECT_FIRST`);
3. **autocorreção** pelo `Agent`, com contexto ENXUTO (as colunas da tabela
   alvo, não o catálogo inteiro) e poucos passos — preserva a intenção da
   consulta quando o banco recusa. Atrás de `EXPLORE_SAMPLE_AUTOCORRECT`;
4. **reconstrução pelo catálogo** (`sample_sql.catalog_sql`): projeção simples da
   tabela alvo, sem LLM. É o degrau que garante amostra — perde a intenção, mas
   roda.

Nunca levanta: sem amostra, a resposta da Persona 1 sai como sempre saiu.
"""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List

from loguru import logger

from rag.components.samples import sample_sql
from rag.settings.configs import settings


def _slug(text: str, size: int = 40) -> str:
    normalized = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return (re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-") or "amostra")[:size]


class SampleRunner:
    """Orquestra a escada. Componentes pesados (MCP, MinIO) são preguiçosos:
    o modo Explorar tem de subir mesmo com um deles fora do ar."""

    def __init__(self, rows: int | None = None, preview_rows: int | None = None,
                 autocorrect: bool | None = None) -> None:
        self.rows = rows or settings.EXPLORE_SAMPLE_ROWS
        self.preview_rows = preview_rows or settings.EXPLORE_SAMPLE_PREVIEW_ROWS
        self.autocorrect = (settings.EXPLORE_SAMPLE_AUTOCORRECT
                            if autocorrect is None else autocorrect)
        self._executor = None
        self._executor_direct = None
        self._uploader = None

    # ------------------------------------------------------------------ #
    def _build_executor(self, direct_only: bool):
        from rag.components.executors.mcp_sql_executor import McpSqlExecutor

        return McpSqlExecutor(
            max_rows=self.rows,
            max_steps=settings.EXPLORE_SAMPLE_AGENT_STEPS,
            direct_only=direct_only,
        )

    def _exec(self, sql: str, query: str = "", schema: str = "", direct: bool = False) -> dict:
        """Executa uma tentativa. `direct=True` nunca aciona LLM."""
        if direct or not self.autocorrect:
            if self._executor_direct is None:
                self._executor_direct = self._build_executor(direct_only=True)
            return self._executor_direct.run(sql=sql)
        if self._executor is None:
            self._executor = self._build_executor(direct_only=False)
        return self._executor.run(sql=sql, query=query, schema_context=schema)

    # ------------------------------------------------------------------ #
    def run(self, answer: Dict[str, Any]) -> Dict[str, Any]:
        """Devolve o dict `sample` para a resposta. `ok=False` = sem amostra."""
        empty = {"ok": False, "sql": "", "origin": "", "columns": [], "rows": [],
                 "row_count": 0, "url": "", "file_name": "", "reason": ""}
        tables: List[dict] = answer.get("tables") or []
        if not tables:
            empty["reason"] = "nenhuma tabela recuperada"
            return empty

        known = [t["table"] for t in tables if t.get("table")]
        proposed = (answer.get("sample_sql") or "").strip()
        alvo = sample_sql.target_table(proposed, known) if proposed else ""
        entry = next((t for t in tables if t["table"] == alvo), tables[0])
        alvo = entry["table"]

        # 1+2+3) a consulta proposta pelo LLM, guardada e executada
        origin, executed, out = "llm", "", {}
        safe, recusa = sample_sql.guard(proposed, self.rows) if proposed else ("", "sem SQL de amostra")
        if safe:
            out = self._exec(safe, answer.get("query", ""), self._schema_hint(entry))
            if out.get("exec_ok"):
                executed = out.get("sql_final") or safe
                # O SQL exibido é o do LLM (legível); o envelope é mecânica interna.
                if executed == safe:
                    executed = proposed
                elif executed != safe:
                    origin = "corrigido"
        else:
            logger.info("[explore/amostra] SQL do LLM recusado na guarda: {}", recusa)

        # 4) reconstrução pelo catálogo — o degrau que garante amostra
        if not out.get("exec_ok"):
            columns = [c.get("name", "") for c in (entry.get("columns") or []) if c.get("name")]
            usadas = [c for c in sample_sql.referenced_columns(proposed) if c in columns]
            fallback = sample_sql.catalog_sql(alvo, usadas or columns, self.rows)
            if not fallback:
                empty["reason"] = "sem colunas conhecidas para a tabela alvo"
                return empty
            out = self._exec(fallback, direct=True)
            if not out.get("exec_ok"):
                logger.warning(
                    "[explore/amostra] nem a consulta do catálogo rodou em {} ({}) "
                    "— catálogo pode estar desatualizado", alvo, out.get("error", ""),
                )
                empty["reason"] = out.get("error", "") or "execução indisponível"
                return empty
            origin, executed = "catalogo", fallback

        rows = out.get("rows") or []
        if not rows:
            empty["sql"] = executed
            empty["reason"] = "a consulta rodou e não devolveu linhas"
            return empty

        columns = out.get("columns") or list(rows[0].keys())
        sample = {
            "ok": True,
            "sql": executed,
            "origin": origin,
            "table": alvo,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "preview_rows": self.preview_rows,
            "url": "",
            "file_name": "",
            "reason": "",
        }
        sample.update(self._publish(sample, answer.get("query", "")))
        logger.info(
            "[explore/amostra] {} linhas de {} (origem={}, arquivo={})",
            len(rows), alvo, origin, bool(sample["url"]),
        )
        return sample

    # ------------------------------------------------------------------ #
    @staticmethod
    def _schema_hint(entry: Dict[str, Any]) -> str:
        """Contexto da autocorreção: SÓ a tabela alvo.

        O `schema_context` da Persona 2 é o subgrafo inteiro; na Persona 1 isso
        seriam 25 cards de catálogo em cada passo do agente. Para consertar um
        `SELECT` de uma tabela, as colunas dela bastam — e o prompt cai de
        milhares de tokens para dezenas."""
        columns = ", ".join(
            f"{c.get('name')} ({c.get('type') or 'sem tipo'})"
            for c in (entry.get("columns") or []) if c.get("name")
        )
        return f"Tabela {entry.get('table', '')}\nColunas: {columns}"

    def _publish(self, sample: Dict[str, Any], query: str) -> Dict[str, str]:
        """Grava o CSV e sobe no MinIO. Aditivo: sem MinIO, a amostra ainda
        aparece no chat — só não tem link de download."""
        try:
            os.makedirs(settings.REPORT_OUTPUT_DIR, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            name = f"amostra-{_slug(sample['table'] or query)}-{stamp}.csv"
            path = os.path.join(settings.REPORT_OUTPUT_DIR, name)
            sample_sql.write_csv(path, sample["columns"], sample["rows"])

            from rag.components.storage import MinIOUploader

            if self._uploader is None:
                self._uploader = MinIOUploader(
                    bucket=settings.SAMPLE_BUCKET,
                    presign_expiry=settings.SAMPLE_PRESIGN_EXPIRY,
                )
            url = self._uploader.upload(path, content_type="text/csv; charset=utf-8")
            return {"url": url, "file_name": name}
        except Exception as e:  # noqa: BLE001 — download é extra, não requisito
            logger.warning("[explore/amostra] CSV/MinIO indisponível: {}", e)
            return {"url": "", "file_name": ""}

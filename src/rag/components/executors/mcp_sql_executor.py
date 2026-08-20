"""McpSqlExecutor — executa o SQL com auto-correção via postgres-mcp (ADR-0013).

Recebe o SQL candidato (do motor) e o executa através de um `Agent` do Haystack
equipado com o `postgres-mcp` (tool `execute_sql`, modo read-only). Se o SQL der
erro, o próprio agente lê a mensagem do banco, corrige e tenta de novo, até
`AGENT_MAX_STEPS`. Devolve o SQL que rodou + as linhas do resultado.

Princípios (ADR-0013):
- **read-only garantido pelo MCP** (RESTRICTED: só SELECT, timeout, sem COMMIT);
- **tolerante a falha**: se o MCP estiver fora ou o SQL não rodar, devolve
  `exec_ok=False` e a entrega degrada para só-texto — nunca derruba a resposta;
- **conexão preguiçosa**: o toolset MCP só conecta na primeira execução, para
  não travar o setup da pipeline se o postgres-mcp estiver indisponível.
"""
from __future__ import annotations

import datetime as _dt
import json
from decimal import Decimal
from typing import Any, Dict, List, NamedTuple, Optional

from loguru import logger
from haystack import component
from haystack.dataclasses import ChatMessage

from rag.settings.configs import settings
from rag.components.generators.openai_generator import OpenAIChatGeneratorFactory

_SYSTEM_PROMPT = """\
Você executa SQL de leitura no PostgreSQL usando a ferramenta `execute_sql`.

Faça:
1. Chame `execute_sql` com o SQL fornecido.
2. Se retornar erro, LEIA a mensagem, corrija o SQL usando apenas o schema
   fornecido (nomes de tabela/coluna existentes) e chame `execute_sql` de novo.
3. Repita até rodar sem erro ou até esgotar as tentativas.
4. Quando rodar, responda apenas "OK" (não repita os dados).

Regras: só SELECT; não invente colunas; mantenha o LIMIT quando houver.\
"""


class _Attempt(NamedTuple):
    """Uma chamada de `execute_sql` no histórico do agente, já interpretada.
    `error` vazio = a chamada rodou e o resultado é utilizável."""
    sql: Optional[str]
    columns: List[str]
    rows: List[Dict[str, Any]]
    error: str


@component
class McpSqlExecutor:
    def __init__(
        self,
        mcp_url: str | None = None,
        max_steps: int | None = None,
        max_rows: int | None = None,
        direct_only: bool = False,
    ) -> None:
        self.mcp_url = mcp_url or settings.POSTGRES_MCP_URL
        self.max_steps = max_steps or settings.AGENT_MAX_STEPS
        self.max_rows = max_rows or settings.MAX_EXEC_ROWS
        self.direct_timeout = settings.MCP_DIRECT_TIMEOUT
        # `direct_only=True`: executa só pelo caminho direto, sem Agent e sem
        # LLM. Aditivo — o default preserva a Persona 2. Existe para a amostra
        # da Persona 1, que tem escada própria de recuperação (reconstrói a
        # consulta pelo catálogo em vez de pedir correção ao modelo).
        self.direct_only = direct_only
        self._agent = None  # construído preguiçosamente

    # ------------------------------------------------------------------ #
    def _build_agent(self):
        """Constrói o Agent + MCPToolset(postgres-mcp). Import tardio para não
        acoplar o resto da pipeline à dependência de MCP."""
        from haystack.components.agents import Agent
        from haystack_integrations.tools.mcp import MCPToolset, SSEServerInfo

        toolset = MCPToolset(
            server_info=SSEServerInfo(url=self.mcp_url),
            tool_names=["execute_sql"],
        )
        # Gerador em modo tool-calling (json_mode=False: o agente precisa
        # chamar ferramentas e depois responder texto).
        chat = OpenAIChatGeneratorFactory(json_mode=False).build()
        agent = Agent(
            chat_generator=chat,
            tools=toolset,
            system_prompt=_SYSTEM_PROMPT,
            exit_conditions=["text"],
            max_agent_steps=self.max_steps,
        )
        agent.warm_up()
        return agent

    def _get_agent(self):
        if self._agent is None:
            self._agent = self._build_agent()
        return self._agent

    # ------------------------------------------------------------------ #
    @component.output_types(
        rows=List[Dict[str, Any]],
        columns=List[str],
        sql_final=str,
        exec_ok=bool,
        error=str,
    )
    def run(self, sql: str, query: str = "", schema_context: str = "") -> dict:
        empty = {"rows": [], "columns": [], "sql_final": sql, "exec_ok": False, "error": ""}
        if not (sql or "").strip():
            empty["error"] = "SQL vazio."
            return empty

        # Caminho rápido: executa direto pelo MCP, sem LLM. O Agent existe para
        # AUTOCORRIGIR SQL com erro, mas cobrava duas chamadas de LLM (~5s)
        # mesmo quando o SQL rodava de primeira — que é o caso quase sempre
        # (fidelidade ao schema medida em 1,0). Só caímos no Agent se o banco
        # recusar, preservando a autocorreção onde ela de fato serve.
        if settings.MCP_DIRECT_FIRST or self.direct_only:
            direct = self._execute_direct(sql)
            if direct is not None:
                columns, rows, parse_error = self._parse_rows(direct)
                if parse_error:
                    logger.warning("McpSqlExecutor[direto]: {}", parse_error)
                else:
                    logger.info(
                        "McpSqlExecutor[direto]: {} linhas, {} colunas",
                        len(rows), len(columns),
                    )
                    return {
                        "rows": rows[: self.max_rows],
                        "columns": columns,
                        "sql_final": sql,
                        "exec_ok": True,
                        "error": "",
                    }
            if self.direct_only:
                empty["error"] = "o banco recusou o SQL (execução direta, sem autocorreção)"
                return empty
            logger.info("McpSqlExecutor: execução direta falhou; acionando autocorreção")

        user_msg = (
            f"Pergunta do usuário: {query}\n\n"
            f"Schema disponível:\n{schema_context}\n\n"
            f"Execute este SQL (corrija se der erro):\n```sql\n{sql}\n```"
        )
        try:
            agent = self._get_agent()
            result = agent.run(messages=[ChatMessage.from_user(user_msg)])
        except Exception as exc:  # noqa: BLE001
            logger.warning("McpSqlExecutor: agente/MCP indisponível ({})", exc)
            empty["error"] = f"Execução indisponível: {exc}"
            return empty

        ok, failed = self._scan_executions(result.get("messages", []))
        if ok is None:
            if failed is None:
                empty["error"] = "Nenhuma execução de SQL bem-sucedida."
                return empty
            logger.warning("McpSqlExecutor: execução não utilizável ({})", failed.error)
            empty["sql_final"] = failed.sql or sql
            empty["error"] = failed.error
            return empty

        logger.info(
            "McpSqlExecutor: {} linhas, {} colunas", len(ok.rows), len(ok.columns)
        )
        return {
            "rows": ok.rows[: self.max_rows],
            "columns": ok.columns,
            "sql_final": ok.sql or sql,
            "exec_ok": True,
            "error": "",
        }

    # ------------------------------------------------------------------ #
    def _execute_direct(self, sql: str) -> Optional[str]:
        """Chama `execute_sql` pelo protocolo MCP, sem Agent e sem LLM.

        Devolve o texto cru do resultado, ou None se o banco recusou — nesse
        caso o chamador aciona o Agent para autocorrigir.

        Usa o transporte SSE clássico (`GET /sse` abre o stream e informa o
        endpoint de POST); o MCPToolset do Haystack tenta POST no próprio /sse
        (Streamable HTTP) e recebe 405 desta versão do postgres-mcp.
        """
        import asyncio

        async def _call() -> Optional[str]:
            from mcp import ClientSession
            from mcp.client.sse import sse_client

            async with sse_client(self.mcp_url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    res = await session.call_tool("execute_sql", {"sql": sql})
                    if getattr(res, "isError", False):
                        return None
                    text = "\n".join(
                        c.text for c in res.content
                        if getattr(c, "type", "") == "text"
                    )
                    # O envelope pode trazer erro do banco em vez de linhas.
                    if not text or '"isError": true' in text.lower():
                        return None
                    db_error = self._db_error(text)
                    if db_error:
                        logger.info(
                            "McpSqlExecutor: banco recusou o SQL ({})", db_error[:160]
                        )
                        return None
                    return text

        try:
            return asyncio.run(asyncio.wait_for(_call(), timeout=self.direct_timeout))
        except Exception as exc:  # noqa: BLE001 — degrada para o Agent
            logger.warning("McpSqlExecutor: execução direta indisponível ({})", exc)
            return None

    @classmethod
    def _scan_executions(cls, messages: list) -> tuple[Optional["_Attempt"], Optional["_Attempt"]]:
        """Varre o histórico do agente e separa a última execução que de fato
        RODOU da última que o banco RECUSOU.

        Antes isto se chamava `_last_successful_execution` e filtrava só por
        `res.error` — mas o postgres-mcp não marca erro de banco no envelope
        (ver `_db_error`: `isError=False` e o erro vem como TEXTO). Na prática
        a função devolvia a última TENTATIVA, não a última bem-sucedida. Dois
        efeitos: um resultado bom seguido de uma tentativa recusada era
        descartado (resposta sem gráfico à toa), e o `sql_final` exibido podia
        ser um SQL que nunca rodou."""
        ok: Optional[_Attempt] = None
        failed: Optional[_Attempt] = None
        last_sql: Optional[str] = None
        for msg in messages or []:
            for res in (getattr(msg, "tool_call_results", None) or []):
                origin = getattr(res, "origin", None)
                if getattr(origin, "tool_name", None) != "execute_sql":
                    continue
                args = getattr(origin, "arguments", None) or {}
                last_sql = args.get("sql") or args.get("query") or last_sql
                raw = getattr(res, "result", None)
                if getattr(res, "error", False):
                    failed = _Attempt(last_sql, [], [], "a ferramenta execute_sql falhou")
                    continue
                # O que conta como resultado utilizável é decidido num lugar só.
                columns, rows, error = cls._parse_rows(raw)
                if error:
                    failed = _Attempt(last_sql, [], [], error)
                else:
                    ok = _Attempt(last_sql, columns, rows, "")
        return ok, failed

    # O postgres-mcp NÃO usa o campo `isError` do MCP para erro de banco: ele
    # devolve isError=False e o erro como TEXTO ("Error: column x does not
    # exist"). Sem reconhecer isso, o erro passa por resultado válido, a
    # autocorreção do Agent (ADR-0013) nunca é acionada e o `eval` do repr
    # quebra com "invalid syntax" — sobrando 0 linhas com exec_ok=True, ou
    # seja, resposta sem gráfico e sem aviso nenhum para o usuário.
    _DB_ERROR_MARKERS = ("error:", "erro:", "exception:", "traceback")

    @classmethod
    def _db_error(cls, text: str) -> str:
        """Mensagem de erro do banco, ou string vazia se o texto for resultado."""
        head = (text or "").strip()[:200].lower()
        if any(head.startswith(m) for m in cls._DB_ERROR_MARKERS):
            return " ".join((text or "").strip().splitlines())[:300]
        return ""

    @classmethod
    def _parse_rows(cls, raw: Any) -> tuple[List[str], List[Dict[str, Any]], str]:
        """Parse do resultado do execute_sql em (colunas, linhas, erro).

        O postgres-mcp devolve um envelope MCP JSON cujo `content[].text` é o
        **repr Python** das linhas (aspas simples, `Decimal('...')`, datas como
        `datetime.date(...)`). Desembrulhamos o envelope e avaliamos o repr num
        namespace restrito (só Decimal/datetime), normalizando os valores.

        O terceiro elemento é o que separa "consulta sem resultado" (válido, 0
        linhas, erro vazio) de "não entendi a resposta" (falha). Sem essa
        distinção, um erro do banco virava sucesso com 0 linhas — sem gráfico e
        sem aviso."""
        text = cls._unwrap_mcp_text(raw)
        if not text:
            return [], [], "o executor não devolveu resultado"
        db_error = cls._db_error(text)
        if db_error:
            return [], [], db_error
        data = cls._eval_repr(text)
        if isinstance(data, list) and data and isinstance(data[0], dict):
            rows = [cls._normalize_row(r) for r in data if isinstance(r, dict)]
            columns = list(rows[0].keys()) if rows else []
            return columns, rows, ""
        if text.strip() in {"[]", "()"}:
            return [], [], ""  # consulta válida, sem linhas
        return [], [], "não consegui interpretar o resultado da execução"

    @staticmethod
    def _unwrap_mcp_text(raw: Any) -> str:
        """Extrai o texto das linhas do envelope MCP (ou aceita texto/JSON cru)."""
        if not isinstance(raw, str):
            return ""
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return raw  # já é o texto das linhas
        if isinstance(obj, dict):
            if obj.get("isError"):
                return ""
            parts = obj.get("content") or []
            texts = [
                p.get("text", "")
                for p in parts
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            return "\n".join(t for t in texts if t)
        return raw

    @staticmethod
    def _eval_repr(text: str) -> Any:
        """Avalia o repr Python das linhas de forma contida (sem builtins;
        só Decimal/datetime disponíveis). Valores string ficam como literais —
        não são executados."""
        text = text.strip()
        if not text or text in {"[]", "()"}:
            return []
        # Caminho feliz: pode já ser JSON.
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        try:
            return eval(  # noqa: S307 — namespace restrito; fonte é o próprio banco
                text,
                {"__builtins__": {}},
                {"Decimal": Decimal, "datetime": _dt},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("McpSqlExecutor: não consegui parsear as linhas ({})", exc)
            return []

    @staticmethod
    def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
        """Converte tipos não-JSON (Decimal→float, date/datetime→ISO) para o
        gráfico e para a serialização da resposta."""
        out: Dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, Decimal):
                out[key] = float(value)
            elif isinstance(value, (_dt.datetime, _dt.date)):
                out[key] = value.isoformat()
            else:
                out[key] = value
        return out

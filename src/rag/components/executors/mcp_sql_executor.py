"""McpSqlExecutor — executa o SQL com auto-correção via MCP (ADR-0013/0017).

Recebe o SQL candidato (do motor) e o executa através de um `Agent` do Haystack
equipado com o **MCP de SQL** (hoje o `trino-mcp`; o `postgres-mcp` continua
suportado). Se o SQL der erro, o próprio agente lê a mensagem do banco, corrige
e tenta de novo, até `AGENT_MAX_STEPS`. Devolve o SQL que rodou + as linhas.

Princípios (ADR-0013):
- **read-only garantido pelo MCP** (o trino-mcp só aceita SELECT/SHOW/DESCRIBE/
  EXPLAIN enquanto `TRINO_ALLOW_WRITE_QUERIES=false`; o postgres-mcp fazia o
  mesmo pelo modo RESTRICTED);
- **tolerante a falha**: se o MCP estiver fora ou o SQL não rodar, devolve
  `exec_ok=False` e a entrega degrada para só-texto — nunca derruba a resposta;
- **conexão preguiçosa**: o toolset MCP só conecta na primeira execução, para
  não travar o setup da pipeline se o MCP estiver indisponível.

⚠️ O componente é **agnóstico de backend**: nome da tool, nome do argumento,
URL e transporte vêm de `SQL_MCP_*` (ADR-0017). Não escreva "execute_sql" nem
"execute_query" no código — leia da config.
"""
from __future__ import annotations

import datetime as _dt
import json
from decimal import Decimal
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from loguru import logger
from haystack import component
from haystack.dataclasses import ChatMessage

from rag import prompts
from rag.settings.configs import settings
from rag.components.generators.openai_generator import OpenAIChatGeneratorFactory
from rag.sensitivity import mask_rows

# O nome da tool e o dialeto entram no prompt: o agente tem de chamar a tool que
# EXISTE no servidor configurado, e corrigir SQL no dialeto que o banco fala.
_SYSTEM_PROMPT = prompts.render(
    "sql_executor_system",
    dialect=settings.SQL_DIALECT,
    tool=settings.SQL_MCP_TOOL,
)

#: Tools de execução conhecidas. O histórico do Agent é varrido por nome, e
#: aceitar os dois backends evita que trocar `SQL_MCP_TOOL` invalide um trace
#: gravado com o outro (o campo configurado entra junto em `_sql_tool_names`).
_KNOWN_SQL_TOOLS = frozenset({"execute_query", "execute_sql"})
#: Nomes de argumento conhecidos, na mesma lógica.
_KNOWN_SQL_ARGS = ("query", "sql")


class _Attempt(NamedTuple):
    """Uma chamada da tool de execução no histórico do agente, já interpretada.
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
        self.mcp_url = mcp_url or settings.SQL_MCP_URL
        self.transport = (settings.SQL_MCP_TRANSPORT or "streamable_http").lower()
        self.tool_name = settings.SQL_MCP_TOOL
        self.tool_arg = settings.SQL_MCP_TOOL_ARG
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
    def _server_info(self):
        """`MCPServerInfo` do transporte configurado.

        `streamable_http` é o default porque é o que o trino-mcp fala nos DOIS
        endpoints que publica (`/mcp` e `/sse`); `sse` é o SSE clássico, que é
        o que o postgres-mcp fala.

        O `timeout` é passado de propósito: o default do transporte é 30s, e
        consulta analítica passa disso com facilidade — quem manda no tempo tem
        de ser a nossa config (`MCP_DIRECT_TIMEOUT`), não o default da lib."""
        from haystack_integrations.tools.mcp import SSEServerInfo, StreamableHttpServerInfo

        if self.transport == "sse":
            return SSEServerInfo(url=self.mcp_url, timeout=self.direct_timeout)
        if self.transport != "streamable_http":
            logger.warning(
                "McpSqlExecutor: SQL_MCP_TRANSPORT='{}' desconhecido; usando streamable_http",
                self.transport,
            )
        return StreamableHttpServerInfo(url=self.mcp_url, timeout=self.direct_timeout)

    def _build_agent(self):
        """Constrói o Agent + MCPToolset. Import tardio para não acoplar o resto
        da pipeline à dependência de MCP."""
        from haystack.components.agents import Agent
        from haystack_integrations.tools.mcp import MCPToolset

        toolset = MCPToolset(
            server_info=self._server_info(),
            tool_names=[self.tool_name],
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
        # ⚠️ DEPOIS do warm_up, nunca antes. O `MCPToolset` nasce com uma tool
        # placeholder (`mcp_not_connected_placeholder_…`) e, ao conectar, faz
        # `self.tools = self._connect_and_load_tools()` — ele SUBSTITUI a lista.
        # Envolver antes é envolver o descartado: o agente receberia as linhas
        # cruas e o log diria que estava tudo mascarado. É o pior tipo de bug de
        # segurança, o que parece resolvido.
        self._mask_toolset(toolset)
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
        masked_columns=List[str],
        truncated=bool,
    )
    def run(self, sql: str, query: str = "", schema_context: str = "") -> dict:
        empty = {"rows": [], "columns": [], "sql_final": sql, "exec_ok": False,
                 "error": "", "masked_columns": [], "truncated": False}
        if not (sql or "").strip():
            empty["error"] = "SQL vazio."
            return empty

        # Definido aqui porque o gate de sensibilidade (abaixo) o lê mesmo
        # quando o caminho direto não roda (`MCP_DIRECT_FIRST=false`).
        direct_error = ""

        # Caminho rápido: executa direto pelo MCP, sem LLM. O Agent existe para
        # AUTOCORRIGIR SQL com erro, mas cobrava duas chamadas de LLM (~5s)
        # mesmo quando o SQL rodava de primeira — que é o caso quase sempre
        # (fidelidade ao schema medida em 1,0). Só caímos no Agent se o banco
        # recusar, preservando a autocorreção onde ela de fato serve.
        if settings.MCP_DIRECT_FIRST or self.direct_only:
            direct, direct_error = self._execute_direct(sql)
            if direct is not None:
                columns, rows, parse_error = self._parse_rows(direct)
                if parse_error:
                    logger.warning("McpSqlExecutor[direto]: {}", parse_error)
                    direct_error = direct_error or parse_error
                else:
                    columns = self._order_columns(sql, columns)
                    logger.info(
                        "McpSqlExecutor[direto]: {} linhas, {} colunas",
                        len(rows), len(columns),
                    )
                    return self._protect(
                        rows[: self.max_rows], columns, sql,
                        truncated=len(rows) > self.max_rows,
                    )
            if self.direct_only:
                empty["error"] = (
                    direct_error
                    or "o banco recusou o SQL (execução direta, sem autocorreção)"
                )
                return empty
            logger.info("McpSqlExecutor: execução direta falhou; acionando autocorreção")

        # Daqui para baixo é o caminho do Agent, e é o único ponto do sistema
        # em que uma linha do banco entraria num prompt: a autocorreção devolve
        # o RETORNO DA TOOL para o contexto do modelo, antes de qualquer
        # mascaramento (pendência (3) do ADR-0016). Quando a consulta toca
        # tabela com coluna sensível ele não roda — perde-se a chance de
        # consertar um SQL recusado, e ganha-se a garantia de que dado pessoal
        # não entra em prompt. Fica FORA do bloco acima de propósito: com
        # `MCP_DIRECT_FIRST=false` o agente é o primeiro caminho, não o segundo.
        if self._sensitive_sql(sql):
            logger.warning(
                "McpSqlExecutor: SQL toca tabela com coluna sensível — "
                "autocorreção desativada (ADR-0018)"
            )
            empty["error"] = (
                direct_error
                or "o banco recusou o SQL (autocorreção inibida por sensibilidade)"
            )
            return empty

        user_msg = prompts.render(
            "sql_executor_user", query=query, schema_context=schema_context, sql=sql,
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

        sql_final = ok.sql or sql
        columns = self._order_columns(sql_final, ok.columns)
        logger.info(
            "McpSqlExecutor: {} linhas, {} colunas", len(ok.rows), len(columns)
        )
        return self._protect(ok.rows[: self.max_rows], columns, sql_final,
                             ja_mascarado=True,
                             truncated=len(ok.rows) > self.max_rows)

    # ------------------------------------------------------------------ #
    def _client(self):
        """Context manager do transporte configurado, já normalizado para
        `(read, write)` — o `streamablehttp_client` devolve uma tripla (o
        terceiro item é o getter do session id, que não usamos)."""
        import contextlib

        if self.transport == "sse":
            from mcp.client.sse import sse_client

            return sse_client(self.mcp_url, timeout=self.direct_timeout)

        from mcp.client.streamable_http import streamablehttp_client

        @contextlib.asynccontextmanager
        async def _streams():
            # `timeout` explícito: o default da lib é 30s e uma consulta
            # analítica passa disso. Quem manda é MCP_DIRECT_TIMEOUT.
            async with streamablehttp_client(
                self.mcp_url, timeout=self.direct_timeout
            ) as (read, write, _sid):
                yield read, write

        return _streams()

    def _execute_direct(self, sql: str) -> Tuple[Optional[str], str]:
        """Chama a tool de execução pelo protocolo MCP, sem Agent e sem LLM.

        Devolve `(texto cru do resultado, erro)`. Texto `None` = o banco (ou o
        transporte) recusou — nesse caso o chamador aciona o Agent para
        autocorrigir, e o `erro` serve para quem não tem Agent (a amostra da
        Persona 1, `direct_only`) reportar o motivo em vez de "recusou".

        Os dois backends sinalizam erro de forma DIFERENTE e as duas são
        tratadas: o trino-mcp marca `isError=True` no envelope; o postgres-mcp
        devolve `isError=False` com o erro do banco como TEXTO (ver `_db_error`).
        """
        import asyncio

        async def _call() -> Tuple[Optional[str], str]:
            from mcp import ClientSession

            async with self._client() as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    res = await session.call_tool(
                        self.tool_name, {self.tool_arg: sql}
                    )
                    text = "\n".join(
                        c.text for c in res.content
                        if getattr(c, "type", "") == "text"
                    )
                    if getattr(res, "isError", False):
                        message = " ".join((text or "").split())[:300]
                        logger.info(
                            "McpSqlExecutor: o MCP recusou o SQL ({})",
                            message or "sem mensagem",
                        )
                        return None, message
                    if not text:
                        return None, "o executor não devolveu resultado"
                    db_error = self._db_error(text)
                    if db_error:
                        logger.info(
                            "McpSqlExecutor: banco recusou o SQL ({})", db_error[:160]
                        )
                        return None, db_error
                    return text, ""

        try:
            # Margem sobre o timeout do transporte de propósito: assim quem
            # estoura primeiro é a camada de baixo, que sabe dizer O QUE falhou;
            # este `wait_for` é a rede de segurança contra travar para sempre.
            return asyncio.run(
                asyncio.wait_for(_call(), timeout=self.direct_timeout + 5)
            )
        except Exception as exc:  # noqa: BLE001 — degrada para o Agent
            logger.warning("McpSqlExecutor: execução direta indisponível ({})", exc)
            return None, f"Execução indisponível: {exc}"

    # ------------------------------------------------------------------ #
    @classmethod
    def _sql_tool_names(cls) -> frozenset:
        """Nomes de tool que contam como "execução de SQL" na varredura."""
        return _KNOWN_SQL_TOOLS | {settings.SQL_MCP_TOOL}

    @classmethod
    def _tool_sql(cls, args: Dict[str, Any]) -> Optional[str]:
        """O SQL de uma chamada, seja qual for o nome do argumento no backend."""
        for key in (settings.SQL_MCP_TOOL_ARG, *_KNOWN_SQL_ARGS):
            value = (args or {}).get(key)
            if value:
                return value
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
        ser um SQL que nunca rodou.

        Com o trino-mcp o erro VEM marcado (`isError=True`), e o Haystack o
        converte num `ToolCallResult` com `error=True` cujo `result` é a
        mensagem — por isso a tentativa falha carrega o texto, não um genérico."""
        tools = cls._sql_tool_names()
        ok: Optional[_Attempt] = None
        failed: Optional[_Attempt] = None
        last_sql: Optional[str] = None
        for msg in messages or []:
            for res in (getattr(msg, "tool_call_results", None) or []):
                origin = getattr(res, "origin", None)
                if getattr(origin, "tool_name", None) not in tools:
                    continue
                last_sql = cls._tool_sql(getattr(origin, "arguments", None) or {}) or last_sql
                raw = getattr(res, "result", None)
                if getattr(res, "error", False):
                    detail = " ".join(str(raw or "").split())[:300]
                    failed = _Attempt(
                        last_sql, [], [],
                        detail or f"a ferramenta {settings.SQL_MCP_TOOL} falhou",
                    )
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
    # O trino-mcp marca `isError=True`, então para ele isto é redundante — e
    # continua valendo, porque a heurística é barata e o backend é configurável.
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
        """Parse do resultado da tool de execução em (colunas, linhas, erro).

        Os dois backends entregam a MESMA forma lógica (lista de dicts), em
        serializações diferentes, e as duas são aceitas:

        - **trino-mcp**: `content[].text` é **JSON** (`json.MarshalIndent` das
          linhas) — cai no caminho feliz do `json.loads`;
        - **postgres-mcp**: `content[].text` é o **repr Python** das linhas
          (aspas simples, `Decimal('...')`, `datetime.date(...)`) — avaliado num
          namespace restrito (só Decimal/datetime), com os valores normalizados.

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
            # As colunas saem da UNIÃO das chaves, na ordem de aparição: o
            # trino-mcp serializa cada linha a partir de um `map[string]any`,
            # e um valor nulo pode não aparecer na primeira linha.
            columns: List[str] = []
            for row in rows:
                for key in row:
                    if key not in columns:
                        columns.append(key)
            return columns, rows, ""
        if text.strip() in {"[]", "()", "null"}:
            return [], [], ""  # consulta válida, sem linhas
        return [], [], "não consegui interpretar o resultado da execução"

    @staticmethod
    def _unwrap_mcp_text(raw: Any) -> str:
        """Extrai o texto das linhas do envelope MCP (ou aceita texto/JSON cru).

        Quando o envelope vem marcado com `isError`, o texto É a mensagem do
        banco: devolvê-la (em vez de string vazia) faz o `_parse_rows` reportar
        o erro de verdade em vez de "o executor não devolveu resultado"."""
        if not isinstance(raw, str):
            return ""
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return raw  # já é o texto das linhas
        if isinstance(obj, dict):
            parts = obj.get("content") or []
            texts = [
                p.get("text", "")
                for p in parts
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            text = "\n".join(t for t in texts if t)
            if obj.get("isError"):
                message = " ".join(text.split())[:300]
                return f"Error: {message}" if message else "Error: execução recusada"
            return text
        return raw

    @staticmethod
    def _eval_repr(text: str) -> Any:
        """Avalia as linhas de forma contida. JSON (trino-mcp) é o caminho
        feliz; o repr Python do postgres-mcp cai no `eval` sem builtins, com
        só Decimal/datetime disponíveis — valores string ficam como literais,
        não são executados."""
        text = text.strip()
        if not text or text in {"[]", "()"}:
            return []
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

    # ------------------------------------------------------------------ #
    def _mask_toolset(self, toolset) -> None:
        """Faz a tool de execução devolver linhas JÁ MASCARADAS ao agente.

        É aqui que a autocorreção deixa de ser um vazamento. O agente corrige
        SQL lendo a MENSAGEM DE ERRO do banco e os nomes de coluna — ele nunca
        precisou dos valores. Então mascarar o retorno da ferramenta preserva a
        autocorreção inteira e fecha a pendência (3) do ADR-0016 de vez, em vez
        de escolher entre uma coisa e outra.

        Envolve `function` (e `async_function`, se houver) de cada tool. O
        `Tool` do Haystack guarda a função como atributo comum; `object.__setattr__`
        cobre o caso de a dataclass ser congelada."""
        envolvidas = 0
        for tool in list(toolset or []):
            if getattr(tool, "_mascarada_adr0018", False):
                envolvidas += 1
                continue
            if "placeholder" in (getattr(tool, "name", "") or ""):
                # Chamado cedo demais: o toolset ainda não conectou e esta tool
                # vai ser descartada. Envolver aqui daria falsa sensação de
                # proteção — ver o comentário em `_build_agent`.
                logger.error(
                    "McpSqlExecutor: _mask_toolset chamado ANTES do warm_up — "
                    "o retorno da tool NÃO será mascarado"
                )
                return
            for campo in ("function", "async_function"):
                original = getattr(tool, campo, None)
                if original is None:
                    continue
                envolvida = (self._envolver_async(original) if campo == "async_function"
                             else self._envolver(original))
                try:
                    object.__setattr__(tool, campo, envolvida)
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "McpSqlExecutor: NÃO consegui mascarar o retorno da tool "
                        "({}) — a autocorreção fica inibida por segurança", exc,
                    )
                    return
            object.__setattr__(tool, "_mascarada_adr0018", True)
            envolvidas += 1
            logger.info("McpSqlExecutor: retorno da tool '{}' será mascarado", tool.name)
        if not envolvidas:
            logger.error(
                "McpSqlExecutor: NENHUMA tool foi envolvida — o retorno da "
                "execução chegará CRU ao modelo na autocorreção"
            )

    def _envolver(self, original):
        def chamada(**kwargs):
            return self._mask_tool_result(original(**kwargs), self._tool_sql(kwargs) or "")
        return chamada

    def _envolver_async(self, original):
        async def chamada(**kwargs):
            return self._mask_tool_result(await original(**kwargs), self._tool_sql(kwargs) or "")
        return chamada

    def _mask_tool_result(self, raw: Any, sql: str) -> Any:
        """Mascara o resultado da tool antes de ele virar contexto do modelo.

        Erro e resultado vazio passam intactos: não há valor para vazar, e a
        mensagem do banco é exatamente o que o agente precisa ler para
        corrigir. Falha aqui **fecha** (devolve erro em vez do texto original) —
        deixar passar seria transformar um bug em vazamento."""
        try:
            columns, rows, error = self._parse_rows(raw)
            if error or not rows:
                return raw
            masked, cols = mask_rows(rows, columns, sql)
            if not cols:
                return raw
            logger.info(
                "McpSqlExecutor: retorno da tool mascarado antes do contexto ({})",
                ", ".join(cols),
            )
            return json.dumps(masked, ensure_ascii=False, default=str)
        except Exception as exc:  # noqa: BLE001 — nunca devolve o cru na dúvida
            logger.error(
                "McpSqlExecutor: falha ao mascarar retorno da tool ({}) — "
                "resultado descartado", exc,
            )
            return "Error: resultado indisponível (falha no mascaramento)"

    @staticmethod
    def _sensitive_sql(sql: str) -> bool:
        """A consulta toca alguma tabela do catálogo de sensibilidade?

        Grosso de propósito: pergunta pela TABELA, não pela coluna projetada.
        Um `SELECT` que o banco recusou pode ser reescrito pelo agente para
        projetar qualquer coluna daquela tabela, então saber que a projeção
        atual é inofensiva não garante nada sobre a próxima tentativa."""
        from rag.sensitivity import catalog as catalog_mod
        from rag.sensitivity import source_tables

        if not (settings.SENSITIVE_MASKING_ENABLED
                and settings.SENSITIVE_INHIBIT_AUTOCORRECT):
            return False
        try:
            catalog = catalog_mod.load(settings.SENSITIVE_MAP_PATH)
            if not catalog:
                return False
            if catalog.by_column:
                # Há regra por NOME de coluna, que vale em qualquer tabela —
                # não dá para afirmar que a reescrita será inofensiva.
                return True
            return any(catalog.knows_table(t) for t in source_tables(sql, settings.SQL_DIALECT))
        except Exception as exc:  # noqa: BLE001 — na dúvida, inibe
            logger.warning("McpSqlExecutor: checagem de sensibilidade falhou ({})", exc)
            return True

    def _protect(self, rows, columns, sql_final: str,
                 ja_mascarado: bool = False, truncated: bool = False) -> Dict[str, Any]:
        """Monta a saída de sucesso com o dado sensível já mascarado (ADR-0018).

        ⚠️ **É este o ponto único de saída de linha real do sistema.** Todo
        consumidor — tabela do chat, gráfico, digest do `ResultExplainer`, CSV
        da amostra, `.docx` — recebe as linhas daqui, e o LLM também (o
        `ChartSpecBuilder` manda 3 linhas de exemplo no prompt, e a
        autocorreção devolve o retorno da tool para o contexto do agente).
        Mascarar aqui é o que faz "não vaza para o modelo" e "não vaza para o
        usuário" serem a mesma linha de código; mascarar no renderer
        protegeria só a segunda.

        Custo: nulo quando o catálogo está vazio (`mask_rows` nem chega a
        parsear o SQL), e alguns milissegundos quando não está.
        """
        # `ja_mascarado`: caminho do agente, onde as linhas foram mascaradas no
        # retorno da tool. Reaplicar geraria hash de hash — o mesmo CPF sairia
        # com códigos diferentes conforme o caminho, quebrando a estabilidade
        # que é a razão de ser do `hash`. Aqui só se recalcula o PLANO, para a
        # nota de transparência saber quais colunas citar.
        rows, masked = mask_rows(rows, columns, sql_final,
                                 apply_values=not ja_mascarado)
        # `truncated` existe porque o corte era MUDO: o gráfico, o parágrafo do
        # resultado e o .docx eram montados sobre as `max_rows` primeiras
        # linhas com a mesma confiança de sempre. Errar em silêncio é pior que
        # errar — o leitor não tem como desconfiar.
        if truncated:
            logger.warning(
                "McpSqlExecutor: resultado truncado em {} linhas — a leitura e o "
                "gráfico cobrem só essa fatia", self.max_rows,
            )
        return {
            "rows": rows,
            "columns": columns,
            "sql_final": sql_final,
            "exec_ok": True,
            "error": "",
            "masked_columns": masked,
            "truncated": truncated,
        }

    @staticmethod
    def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
        """Converte tipos não-JSON (Decimal→float, date/datetime→ISO) para o
        gráfico e para a serialização da resposta.

        ⚠️ O trino-mcp entrega `DECIMAL` como **string** (limitação do driver
        Go do Trino, que não tem tipo decimal). Não convertemos aqui de
        propósito: "12345" tanto pode ser um `SUM(decimal)` quanto um código de
        IBGE/CNPJ, e transformar código em número estragaria rótulo de eixo e
        CSV. Quem precisa de número já coage sozinho — `_infer_kinds`
        (chart_spec), `pd.to_numeric` (renderers) e `_as_float`
        (result_explainer) aceitam string numérica."""
        out: Dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, Decimal):
                out[key] = float(value)
            elif isinstance(value, (_dt.datetime, _dt.date)):
                out[key] = value.isoformat()
            else:
                out[key] = value
        return out

    @staticmethod
    def _order_columns(sql: str, columns: List[str]) -> List[str]:
        """Reordena as colunas na ordem do `SELECT`.

        Existe por causa de um detalhe do trino-mcp: cada linha é um
        `map[string]interface{}` em Go, e o `encoding/json` serializa mapa com
        as chaves **em ordem alfabética**. O resultado chega correto, mas com as
        colunas embaralhadas — `SELECT uf, total` volta como `total, uf`, e é
        essa ordem que vai para a tabela do chat, para o CSV da amostra e para o
        `.docx`. O envelope não traz metadado de coluna, então a ordem tem de
        ser reconstruída da única fonte que a conhece: o próprio SQL.

        Conservadora por construção: só reordena quando o parse dá certo E o
        conjunto de nomes projetados é EXATAMENTE o que voltou (nada de `*`,
        nada de projeção sem nome). Qualquer dúvida devolve a ordem recebida —
        o pior caso é continuar alfabético, nunca perder coluna."""
        if len(columns) < 2 or not (sql or "").strip():
            return columns
        try:
            import sqlglot

            from sqlglot import exp

            tree = sqlglot.parse_one(sql, read=settings.SQL_DIALECT)
            # A amostra da Persona 1 chega envelopada (`SELECT * FROM (…) AS
            # amostra LIMIT n`, ver `sample_sql.guard`): a projeção de fora é
            # `*` e quem conhece a ordem é a consulta de dentro.
            for _ in range(3):
                projected = [expression.alias_or_name for expression in tree.selects]
                if projected != ["*"]:
                    break
                # `from_` no sqlglot 30+, `from` nas gerações anteriores.
                source = tree.args.get("from_") or tree.args.get("from")
                inner = source.this if source is not None else None
                if not isinstance(inner, exp.Subquery):
                    break
                tree = inner.this
            projected = [expression.alias_or_name for expression in tree.selects]
        except Exception:  # noqa: BLE001 — SQL de LLM; ordem é cosmética
            return columns
        if not projected or any(not name or name == "*" for name in projected):
            return columns
        by_lower = {name.lower(): name for name in columns}
        ordered = [by_lower[name.lower()] for name in projected if name.lower() in by_lower]
        return ordered if set(ordered) == set(columns) else columns

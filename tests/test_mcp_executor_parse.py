"""Parse do resultado do MCP de SQL (camada de entrega da Persona 2).

O executor fala com DOIS backends (ADR-0017) e cada um entrega o resultado de
um jeito. Estes testes fixam os dois contratos, porque a diferença entre eles já
custou um bug caro:

- **trino-mcp** (default): linhas em **JSON**, erro marcado com `isError=True`.
  As chaves vêm em ordem ALFABÉTICA (o `encoding/json` do Go ordena mapa), então
  a ordem do `SELECT` é reconstruída do próprio SQL (`_order_columns`).
- **postgres-mcp** (legado): linhas como **repr Python** e — o bug — erro de
  banco como TEXTO com `isError=False`. Isso passava por resultado válido: a
  autocorreção do Agent (ADR-0013) não era acionada, o `eval` do repr quebrava e
  a resposta saía com 0 linhas e `exec_ok=True` — sem gráfico e sem aviso.

Só stdlib + o componente; não sobe MCP nem banco.
"""
from __future__ import annotations

import json
import unittest

try:  # pragma: no cover - depende do ambiente
    from rag.components.executors.mcp_sql_executor import McpSqlExecutor
    HAYSTACK = True
except ImportError:  # pragma: no cover
    HAYSTACK = False


def _envelope(text: str, is_error: bool = False) -> str:
    payload = {"content": [{"type": "text", "text": text}]}
    if is_error:
        payload["isError"] = True
    return json.dumps(payload)


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestParseRowsTrino(unittest.TestCase):
    """trino-mcp: `content[].text` é o JSON das linhas."""

    def test_json_rows_are_parsed(self):
        payload = json.dumps([{"uf": "SP", "total": 412}, {"uf": "MG", "total": 300}])
        columns, rows, error = McpSqlExecutor._parse_rows(_envelope(payload))
        self.assertEqual(error, "")
        self.assertEqual(columns, ["uf", "total"])
        self.assertEqual(rows[0], {"uf": "SP", "total": 412})

    def test_decimal_arrives_as_string_and_is_preserved(self):
        # Limitação conhecida do driver Go do Trino: DECIMAL vira string. Não
        # convertemos (código de IBGE viraria número); quem precisa, coage.
        payload = json.dumps([{"uf": "SP", "valor": "1234.56"}])
        _, rows, error = McpSqlExecutor._parse_rows(_envelope(payload))
        self.assertEqual(error, "")
        self.assertEqual(rows[0]["valor"], "1234.56")

    def test_columns_cover_keys_missing_from_the_first_row(self):
        # Cada linha é um mapa próprio: um nulo pode simplesmente não aparecer.
        payload = json.dumps([{"uf": "SP"}, {"uf": "MG", "total": 3}])
        columns, _, error = McpSqlExecutor._parse_rows(_envelope(payload))
        self.assertEqual(error, "")
        self.assertEqual(columns, ["uf", "total"])

    def test_is_error_envelope_reports_the_message(self):
        # O trino-mcp marca erro no envelope; a mensagem não pode virar
        # "o executor não devolveu resultado".
        text = "query execution failed: line 1:8: Column 'regiao' cannot be resolved"
        columns, rows, error = McpSqlExecutor._parse_rows(_envelope(text, is_error=True))
        self.assertEqual((columns, rows), ([], []))
        self.assertIn("cannot be resolved", error)

    def test_empty_result_is_success_not_failure(self):
        columns, rows, error = McpSqlExecutor._parse_rows(_envelope("[]"))
        self.assertEqual((columns, rows, error), ([], [], ""))


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestParseRowsPostgres(unittest.TestCase):
    """postgres-mcp (legado): repr Python e erro como texto."""

    def test_rows_with_decimal_are_parsed(self):
        repr_rows = ("[{'regiao_nome': 'Norte', 'total': Decimal('500')}, "
                     "{'regiao_nome': 'Sul', 'total': Decimal('344')}]")
        columns, rows, error = McpSqlExecutor._parse_rows(_envelope(repr_rows))
        self.assertEqual(error, "")
        self.assertEqual(columns, ["regiao_nome", "total"])
        self.assertEqual(rows[0]["total"], 500.0)  # Decimal normalizado

    def test_database_error_is_reported_not_swallowed(self):
        text = ('Error: column "regiao" does not exist\n'
                'LINE 1: /* crystaldba */ SELECT regiao, COUNT(*) ...')
        columns, rows, error = McpSqlExecutor._parse_rows(_envelope(text))
        self.assertEqual(rows, [])
        self.assertIn("does not exist", error)

    def test_unparseable_text_is_a_failure(self):
        columns, rows, error = McpSqlExecutor._parse_rows(_envelope("isso não é repr"))
        self.assertEqual(rows, [])
        self.assertTrue(error)


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestDbErrorDetection(unittest.TestCase):
    def test_detects_postgres_error_prefix(self):
        self.assertTrue(McpSqlExecutor._db_error('Error: relation "x" does not exist'))

    def test_does_not_flag_rows_that_mention_error(self):
        # Uma linha de dados pode conter a palavra "erro" no meio do texto.
        rows = "[{'situacao': 'contrato com erro de cadastro'}]"
        self.assertEqual(McpSqlExecutor._db_error(rows), "")


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestOrderColumns(unittest.TestCase):
    """A ordem do `SELECT` é reconstruída do SQL — o trino-mcp devolve as
    chaves em ordem alfabética, e é essa ordem que iria para a tabela do chat,
    o CSV da amostra e o `.docx`."""

    def test_select_order_wins_over_alphabetical(self):
        ordered = McpSqlExecutor._order_columns(
            "SELECT uf, total FROM far.mapa", ["total", "uf"])
        self.assertEqual(ordered, ["uf", "total"])

    def test_alias_is_the_column_name(self):
        ordered = McpSqlExecutor._order_columns(
            "SELECT uf, SUM(valor) AS total FROM far.mapa GROUP BY uf",
            ["total", "uf"])
        self.assertEqual(ordered, ["uf", "total"])

    def test_sample_envelope_is_unwrapped(self):
        # `sample_sql.guard` envelopa a consulta; a projeção de fora é `*` e
        # quem conhece a ordem é a consulta de dentro.
        envelope = "SELECT * FROM (SELECT uf, valor FROM far.mapa) AS amostra LIMIT 100"
        self.assertEqual(
            McpSqlExecutor._order_columns(envelope, ["valor", "uf"]), ["uf", "valor"])

    def test_star_projection_keeps_what_came(self):
        received = ["valor", "uf"]
        self.assertEqual(
            McpSqlExecutor._order_columns("SELECT * FROM far.mapa", received), received)

    def test_mismatch_between_sql_and_result_keeps_what_came(self):
        # Autocorreção pode ter mudado o SQL: na dúvida, não reordena.
        received = ["valor", "uf"]
        self.assertEqual(
            McpSqlExecutor._order_columns("SELECT uf, outro FROM far.mapa", received),
            received)

    def test_unparseable_sql_keeps_what_came(self):
        received = ["b", "a"]
        self.assertEqual(McpSqlExecutor._order_columns("isso não é sql", received),
                         received)


# --------------------------------------------------------------------------- #
# Seleção da execução no histórico do Agent (autocorreção)
# --------------------------------------------------------------------------- #
class _Origin:
    def __init__(self, sql: str, tool: str = "execute_query", arg: str = "query") -> None:
        self.tool_name = tool
        self.arguments = {arg: sql}


class _Res:
    def __init__(self, sql: str, result: str, error: bool = False,
                 tool: str = "execute_query", arg: str = "query") -> None:
        self.origin = _Origin(sql, tool, arg)
        self.result, self.error = result, error


class _Msg:
    def __init__(self, *results) -> None:
        self.tool_call_results = list(results)


_OK_ROWS = _envelope(json.dumps([{"uf": "SP", "total": 412}]))
_DB_ERROR = _envelope("line 1:8: Column 'valor_total' cannot be resolved", is_error=True)


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestScanExecutions(unittest.TestCase):
    """Regressão: `_last_successful_execution` filtrava só por `res.error` e,
    como o postgres-mcp devolve erro de banco como TEXTO com `isError=False`,
    devolvia a última TENTATIVA — não a última bem-sucedida."""

    def test_correction_after_error_returns_the_sql_that_ran(self):
        ok, failed = McpSqlExecutor._scan_executions([
            _Msg(_Res("SELECT valor_total FROM t", _DB_ERROR)),
            _Msg(_Res("SELECT valor FROM t", _OK_ROWS)),
        ])
        self.assertEqual(ok.sql, "SELECT valor FROM t")
        self.assertEqual(ok.rows, [{"uf": "SP", "total": 412}])
        self.assertIn("cannot be resolved", failed.error)

    def test_good_result_is_not_discarded_by_a_later_failed_attempt(self):
        # O agente acerta e depois tenta de novo (e erra) até esgotar os passos:
        # o resultado bom tem de sobreviver.
        ok, failed = McpSqlExecutor._scan_executions([
            _Msg(_Res("SELECT valor FROM t", _OK_ROWS)),
            _Msg(_Res("SELECT valor, x FROM t", _DB_ERROR)),
        ])
        self.assertIsNotNone(ok)
        self.assertEqual(ok.sql, "SELECT valor FROM t")
        self.assertEqual(ok.rows, [{"uf": "SP", "total": 412}])

    def test_all_attempts_failed_reports_the_database_error(self):
        ok, failed = McpSqlExecutor._scan_executions([
            _Msg(_Res("SELECT a FROM t", _DB_ERROR)),
            _Msg(_Res("SELECT b FROM t", _DB_ERROR)),
        ])
        self.assertIsNone(ok)
        self.assertEqual(failed.sql, "SELECT b FROM t")
        self.assertIn("cannot be resolved", failed.error)

    def test_tool_level_failure_carries_the_message(self):
        # Erro marcado (isError) vira MCPInvocationError no Haystack, que o
        # ToolInvoker converte em `error=True` com a mensagem no `result`.
        ok, failed = McpSqlExecutor._scan_executions([
            _Msg(_Res("SELECT a FROM t",
                      "Tool 'execute_query' returned an error: Column 'a' cannot be resolved",
                      error=True)),
        ])
        self.assertIsNone(ok)
        self.assertIn("cannot be resolved", failed.error)

    def test_legacy_postgres_tool_name_and_arg_still_scan(self):
        # Trace gravado com o backend antigo continua legível.
        ok, _ = McpSqlExecutor._scan_executions([
            _Msg(_Res("SELECT valor FROM t",
                      _envelope("[{'uf': 'SP', 'total': 412}]"),
                      tool="execute_sql", arg="sql")),
        ])
        self.assertEqual(ok.sql, "SELECT valor FROM t")
        self.assertEqual(ok.rows, [{"uf": "SP", "total": 412}])

    def test_other_tools_are_ignored(self):
        ok, failed = McpSqlExecutor._scan_executions([
            _Msg(_Res("qualquer", _OK_ROWS, tool="list_catalogs")),
        ])
        self.assertEqual((ok, failed), (None, None))

    def test_no_execution_at_all(self):
        self.assertEqual(McpSqlExecutor._scan_executions([]), (None, None))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class PhysicalSchemaContextTest(unittest.TestCase):
    """A autocorreção copiava o FQN do card (`Cidades.cidades.prata.x`), que o
    banco recusa: o contexto do agente sai só com o nome físico."""

    def test_fqn_note_is_removed_and_the_rest_kept(self):
        from rag.components.executors.mcp_sql_executor import physical_schema_context

        card = ("Tabela: prata.prata_far   (FQN: Cidades.cidades.prata.prata_far)\n"
                "  Descrição: base do FAR (dado conformado)\n"
                "  Colunas:\n    - uf text")
        out = physical_schema_context(card)
        self.assertNotIn("Cidades.cidades", out)
        self.assertIn("Tabela: prata.prata_far\n", out)
        self.assertIn("(dado conformado)", out)  # parêntese comum fica
        self.assertEqual(physical_schema_context(""), "")

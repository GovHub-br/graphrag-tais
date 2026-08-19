"""Parse do resultado do postgres-mcp (camada de entrega da Persona 2).

Regressão de um bug real: o postgres-mcp devolve erro de banco como TEXTO com
`isError: False`. Isso passava por resultado válido — a autocorreção do Agent
(ADR-0013) não era acionada, o `eval` do repr quebrava e a resposta saía com 0
linhas e `exec_ok=True`: sem gráfico e sem aviso nenhum para o usuário.

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


def _envelope(text: str) -> str:
    return json.dumps({"content": [{"type": "text", "text": text}]})


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestParseRows(unittest.TestCase):
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

    def test_empty_result_is_success_not_failure(self):
        # Consulta válida que não retorna linhas não pode virar erro.
        columns, rows, error = McpSqlExecutor._parse_rows(_envelope("[]"))
        self.assertEqual((columns, rows, error), ([], [], ""))

    def test_unparseable_text_is_a_failure(self):
        columns, rows, error = McpSqlExecutor._parse_rows(_envelope("isso não é repr"))
        self.assertEqual(rows, [])
        self.assertTrue(error)

    def test_json_rows_also_work(self):
        payload = json.dumps([{"uf": "SP", "total": 3}])
        columns, rows, error = McpSqlExecutor._parse_rows(_envelope(payload))
        self.assertEqual(error, "")
        self.assertEqual(rows, [{"uf": "SP", "total": 3}])


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestDbErrorDetection(unittest.TestCase):
    def test_detects_postgres_error_prefix(self):
        self.assertTrue(McpSqlExecutor._db_error('Error: relation "x" does not exist'))

    def test_does_not_flag_rows_that_mention_error(self):
        # Uma linha de dados pode conter a palavra "erro" no meio do texto.
        rows = "[{'situacao': 'contrato com erro de cadastro'}]"
        self.assertEqual(McpSqlExecutor._db_error(rows), "")


if __name__ == "__main__":
    unittest.main()

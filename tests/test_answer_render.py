"""Invariantes de exibição da resposta da Persona 2: SQL, IR e Resultado.

O que a pessoa vê como SQL tem de ser o SQL que **rodou no banco**. Antes, o
bloco "SQL gerado" vinha sempre da primeira tentativa do LLM — mesmo quando a
autocorreção do `McpSqlExecutor` tinha reescrito a consulta, e mesmo quando o
banco havia recusado tudo. O dicionário de dados abaixo dele era derivado desse
mesmo texto, então documentava colunas que a execução não usou.

Fixa também o que saiu e o que entrou na resposta: a **IR** deixou de ser
renderizada (segue no `answer` da API) e o parágrafo **"Resultado"** passou a
fechar o texto, logo acima do gráfico que o wrapper anexa (ADR-0010 item c).

Só stdlib + o componente; não sobe MCP nem banco.
"""
from __future__ import annotations

import unittest

try:  # pragma: no cover - depende do ambiente
    from rag.components.answer.sql_answer_builder import render_markdown
    HAYSTACK = True
except ImportError:  # pragma: no cover
    HAYSTACK = False

_GERADO = "SELECT uf, valor_total FROM far.mapa;"
_EXECUTADO = "SELECT uf, valor_contratado FROM far.mapa;"

_ANSWER = {
    "status": "sucesso",
    "explanation": "Valor por UF.",
    "sql": _GERADO,
    "ir": {"intent": "sum", "metric": "valor", "filters": []},
    "references": [{
        "base": "far",
        "table": "far.mapa",
        "description": "",
        "columns": [
            {"name": "uf", "type": "varchar(2)", "is_pk": False, "is_join_key": False},
            {"name": "valor_total", "type": "numeric", "is_pk": False, "is_join_key": False},
            {"name": "valor_contratado", "type": "numeric", "is_pk": False, "is_join_key": False},
        ],
        "used": True,
    }],
    "joins": [],
}


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestSqlSection(unittest.TestCase):
    def test_without_execution_nothing_is_claimed(self):
        md = render_markdown(_ANSWER)
        self.assertIn("### SQL gerado", md)
        self.assertIn(_GERADO, md)
        self.assertNotIn("não executado", md)
        self.assertNotIn("### SQL executado", md)

    def test_corrected_sql_is_the_one_displayed(self):
        md = render_markdown(_ANSWER, {"exec_ok": True, "sql_final": _EXECUTADO})
        self.assertIn("### SQL executado", md)
        self.assertIn(_EXECUTADO, md)
        self.assertNotIn(_GERADO, md)          # a tentativa que não rodou some
        self.assertIn("ajustado na execução", md)   # mas o ajuste é declarado
        self.assertEqual(md.count("```sql"), 1)     # UM bloco de SQL, nunca dois

    def test_data_dictionary_follows_the_executed_sql(self):
        md = render_markdown(_ANSWER, {"exec_ok": True, "sql_final": _EXECUTADO})
        dicionario = md.split("### Dados de referência")[1]
        self.assertIn("`valor_contratado`", dicionario)
        self.assertNotIn("`valor_total`", dicionario)

    def test_unchanged_sql_is_shown_as_executed(self):
        md = render_markdown(_ANSWER, {"exec_ok": True, "sql_final": _GERADO})
        self.assertIn("### SQL executado", md)
        self.assertIn(_GERADO, md)
        self.assertNotIn("ajustado na execução", md)

    def test_exec_ok_without_sql_final_falls_back_to_the_generated_sql(self):
        md = render_markdown(_ANSWER, {"exec_ok": True, "sql_final": ""})
        self.assertIn("### SQL executado", md)
        self.assertIn(_GERADO, md)

    def test_failed_execution_shows_the_generated_sql_without_noise(self):
        # Decisão de produto: quem lê quer a consulta, não o diagnóstico da
        # falha. O erro do banco fica no log e no `delivery`, fora da resposta.
        md = render_markdown(
            _ANSWER,
            {"exec_ok": False, "sql_final": _GERADO,
             "exec_error": 'Error: column "valor_total" does not exist'},
        )
        self.assertIn("### SQL gerado", md)
        self.assertIn(_GERADO, md)
        self.assertNotIn("does not exist", md)
        self.assertNotIn("não executado", md)
        self.assertNotIn("⚠️", md)

    def test_no_sql_at_all_keeps_the_refine_message(self):
        md = render_markdown({**_ANSWER, "sql": ""}, {"exec_ok": False})
        self.assertIn("Não foi possível gerar SQL", md)
        self.assertNotIn("```sql", md)


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestIrNaoAparece(unittest.TestCase):
    """A IR é instrumentação (ADR-0006/0007), não conteúdo: fica no `answer`."""

    def test_ir_fora_do_markdown(self):
        md = render_markdown(_ANSWER, {"exec_ok": True, "sql_final": _EXECUTADO})
        self.assertNotIn("Interpretação (IR)", md)
        self.assertNotIn("intent", md)
        # …mas continua no dado estruturado que a API devolve.
        self.assertEqual(_ANSWER["ir"]["intent"], "sum")


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestSecaoResultado(unittest.TestCase):
    """O gráfico não chega mais sozinho: o parágrafo do resultado vem antes."""

    _RESUMO = "SP lidera com 2.500.000,00, seguida de MG."

    def test_resultado_aparece_quando_a_entrega_explicou(self):
        md = render_markdown(_ANSWER, {"exec_ok": True, "sql_final": _EXECUTADO,
                                       "result_summary": self._RESUMO})
        self.assertIn("### Resultado", md)
        self.assertIn(self._RESUMO, md)

    def test_resultado_e_a_ultima_secao(self):
        # O wrapper anexa "### Gráfico" logo depois — a adjacência é o ponto.
        md = render_markdown(_ANSWER, {"exec_ok": True, "sql_final": _EXECUTADO,
                                       "result_summary": self._RESUMO})
        self.assertTrue(md.rstrip().endswith(self._RESUMO))

    def test_sem_execucao_nao_ha_secao_de_resultado(self):
        self.assertNotIn("### Resultado", render_markdown(_ANSWER))
        self.assertNotIn("### Resultado",
                         render_markdown(_ANSWER, {"exec_ok": False, "result_summary": ""}))


if __name__ == "__main__":
    unittest.main()

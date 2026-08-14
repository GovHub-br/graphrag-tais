"""Unitários do motor de métricas da resposta — offline, sem serviços."""
import unittest

from eval.answer.metrics import score_answer
from eval.answer.sql_introspection import introspect


TABLES = ["empreendimento_far.empreendimento", "entidades_fds.fds_ficha_empreendimento"]
COLUMNS = ["apf", "uf", "quantidade_uh", "valor_contratado", "nome_empreendimento"]
DUPLICADAS = {"empreendimento_far.empreendimento": "um registro por apf (2.00x)"}


class SqlIntrospectionTest(unittest.TestCase):
    def test_resolve_alias_em_subquery(self):
        shape = introspect(
            "SELECT uf, SUM(quantidade_uh) FROM (SELECT DISTINCT apf, uf, quantidade_uh "
            "FROM empreendimento_far.empreendimento) d GROUP BY uf"
        )
        self.assertIn("empreendimento_far.empreendimento", shape.tables)
        self.assertTrue(shape.has_distinct)
        self.assertTrue(shape.has_aggregate)

    def test_join_implicito_por_virgula_conta_como_par(self):
        shape = introspect(
            "SELECT * FROM empreendimento_far.empreendimento a, "
            "entidades_fds.fds_ficha_empreendimento b WHERE a.apf = b.apf"
        )
        self.assertEqual(len(shape.join_pairs), 1)

    def test_sql_invalido_vira_campo_nao_excecao(self):
        self.assertTrue(introspect("SELECT FROM WHERE ***").parse_error
                        or introspect("SELECT FROM WHERE ***").tables == set())


class AnswerMetricsTest(unittest.TestCase):
    def _score(self, sql, **kw):
        return score_answer(sql, available_tables=TABLES, available_columns=COLUMNS,
                            duplicated_tables=DUPLICADAS, **kw)

    def test_tabela_inventada_derruba_fidelidade(self):
        s = self._score("SELECT apf FROM empreendimento_far.tabela_que_nao_existe")
        self.assertLess(s["schema_faithfulness"], 1.0)
        self.assertEqual(s["table_hallucination_rate"], 1.0)

    def test_sql_fiel_ao_subgrafo_pontua_um(self):
        s = self._score("SELECT apf, uf FROM empreendimento_far.empreendimento")
        self.assertEqual(s["schema_faithfulness"], 1.0)

    def test_agregacao_sem_distinct_em_tabela_duplicada_reprova(self):
        s = self._score("SELECT uf, SUM(quantidade_uh) FROM "
                        "empreendimento_far.empreendimento GROUP BY uf")
        self.assertEqual(s["grain_compliance"], 0.0)

    def test_agregacao_com_dedup_passa(self):
        s = self._score(
            "SELECT uf, SUM(quantidade_uh) FROM (SELECT DISTINCT apf, uf, "
            "quantidade_uh FROM empreendimento_far.empreendimento) d GROUP BY uf")
        self.assertEqual(s["grain_compliance"], 1.0)

    def test_join_nao_autorizado_e_acusado(self):
        s = self._score(
            "SELECT * FROM empreendimento_far.empreendimento a JOIN "
            "entidades_fds.fds_ficha_empreendimento b ON a.apf = b.apf",
            authorized_pairs=[])
        self.assertEqual(s["unauthorized_join_rate"], 1.0)

    def test_join_declarado_nao_e_acusado(self):
        s = self._score(
            "SELECT * FROM empreendimento_far.empreendimento a JOIN "
            "entidades_fds.fds_ficha_empreendimento b ON a.apf = b.apf",
            authorized_pairs=[(TABLES[0], TABLES[1])])
        self.assertEqual(s["unauthorized_join_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()


class AliasNotHallucinationTest(unittest.TestCase):
    """Alias de saída não é coluna do schema — regressão vista em 2026-08-12."""

    def test_alias_em_order_by_nao_conta_como_alucinacao(self):
        sql = ("SELECT uf, SUM(quantidade_uh) AS total_uh FROM (SELECT DISTINCT apf, uf, "
               "quantidade_uh FROM empreendimento_far.empreendimento) d "
               "GROUP BY uf ORDER BY total_uh DESC LIMIT 10")
        s = score_answer(sql, available_tables=TABLES, available_columns=COLUMNS,
                         duplicated_tables=DUPLICADAS)
        self.assertEqual(s["column_hallucination_rate"], 0.0, s.get("_hallucinated_columns"))
        self.assertEqual(s["schema_faithfulness"], 1.0)

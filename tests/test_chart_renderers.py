"""Renderers de gráfico — o eixo do tempo (ADR-0013).

Este arquivo existe por um defeito que NÃO dava erro: `render_line` fazia
`pd.to_datetime` no eixo x sem exceção, e `pd.to_datetime(1993)` lê o inteiro
como NANOSSEGUNDOS desde a época — devolve 1970-01-01, não o ano de 1993.
"Projetos por ano" saía com 30 anos empilhados num instante, ligados por
interpolação, e o eixo rotulado `1970-Jan-01 00:00`. O gráfico mentia em
silêncio.
"""
import unittest

import pandas as pd

from rag.components.charts.renderers import _eh_ano, render_line


class EhAnoTest(unittest.TestCase):
    def test_ano_inteiro(self):
        self.assertTrue(_eh_ano(pd.Series([1993, 2000, 2010, 2024])))

    def test_ano_como_texto(self):
        self.assertTrue(_eh_ano(pd.Series(["1993", "2024"])))

    def test_data_de_verdade_nao_e_ano(self):
        self.assertFalse(_eh_ano(pd.to_datetime(pd.Series(["2024-01-01", "2024-02-01"]))))

    def test_periodo_aaaamm_nao_e_ano(self):
        """`202401` é período, não ano: segue no caminho de data."""
        self.assertFalse(_eh_ano(pd.Series(["202401", "202402"])))

    def test_categoria_textual_nao_e_ano(self):
        self.assertFalse(_eh_ano(pd.Series(["ROUANET", "PNAB"])))

    def test_ano_fracionario_nao_e_ano(self):
        self.assertFalse(_eh_ano(pd.Series([1993.5, 2000.2])))

    def test_serie_vazia(self):
        self.assertFalse(_eh_ano(pd.Series([], dtype=object)))


class RenderLineTest(unittest.TestCase):
    def test_ano_nao_colapsa_na_epoca(self):
        """A regressão original: sem o desvio, todos os pontos viram 1970."""
        df = pd.DataFrame({"ano": [1993, 2005, 2024], "n": [10, 200, 800]})
        self.assertIsNotNone(render_line(df, "ano", "n", title="Por ano"))

    def test_data_continua_funcionando(self):
        df = pd.DataFrame({"data": ["2024-01-01", "2024-02-01"], "v": [1, 5]})
        self.assertIsNotNone(render_line(df, "data", "v"))

    def test_sem_dado_utilizavel_devolve_none(self):
        df = pd.DataFrame({"ano": ["x", "y"], "n": ["a", "b"]})
        self.assertIsNone(render_line(df, "ano", "n"))


if __name__ == "__main__":
    unittest.main()

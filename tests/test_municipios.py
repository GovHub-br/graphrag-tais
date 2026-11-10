"""Recorte por município: nome ↔ código IBGE, a dimensão nos fatos, o mapa
municipal e o botão do mapa por UF que pergunta pelos municípios.

Roda no host (módulos puros + dados do IBGE em `journey/data/`).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag.journey import ibge, interactive, municipios, render  # noqa: E402
from rag.journey.facts import build  # noqa: E402

SP, CAMPINAS, SANTOS = "3550308", "3509502", "3548500"


class TestResolve(unittest.TestCase):
    def test_nome_com_e_sem_acento_e_caixa(self):
        for rotulo in ("São Paulo", "SAO PAULO", "sao paulo"):
            self.assertEqual(municipios.resolve(rotulo, "SP"), SP)

    def test_codigo_de_7_e_de_6_digitos(self):
        self.assertEqual(municipios.resolve("3550308"), SP)
        self.assertEqual(municipios.resolve(3550308), SP)
        self.assertEqual(municipios.resolve("355030"), SP)     # sem o dígito verificador
        self.assertEqual(municipios.resolve("3550308.0"), SP)  # código lido como float

    def test_uf_colada_no_nome(self):
        for rotulo in ("Mogi das Cruzes - SP", "Mogi das Cruzes/SP", "Mogi das Cruzes (SP)"):
            self.assertEqual(municipios.name(municipios.resolve(rotulo)), "Mogi das Cruzes")

    def test_nome_com_hifen_nao_vira_uf(self):
        self.assertEqual(municipios.name(municipios.resolve("Embu-Guaçu")), "Embu-Guaçu")

    def test_apostrofo(self):
        self.assertEqual(municipios.name(municipios.resolve("PEROLA D OESTE", "PR")), "Pérola d'Oeste")

    def test_nome_ambiguo_sem_uf_nao_resolve(self):
        self.assertIsNone(municipios.resolve("Bom Jesus"))
        self.assertEqual(municipios.uf_of(municipios.resolve("Bom Jesus", "PI")), "PI")

    def test_malha_de_cada_uf(self):
        for uf in ibge.ufs():
            self.assertTrue(municipios.geometry(uf["sigla"]), uf["sigla"])
        self.assertEqual(len(municipios.geometry("SP")), 645)


def _sp_facts():
    rows = [{"municipio": "São Paulo", "v": 10.0}, {"municipio": "CAMPINAS", "v": 5.0},
            {"municipio": "Santos", "v": 3.0}, {"municipio": "Xpto", "v": 1.0}]
    return build(rows, sql="SELECT municipio, SUM(v) AS v FROM t WHERE uf = 'SP' GROUP BY municipio",
                 kinds={"municipio": "text", "v": "numeric"})


class TestFatos(unittest.TestCase):
    def test_municipio_com_uf_do_filtro(self):
        f = _sp_facts()
        self.assertEqual(f.dimension_kind, "municipio")
        self.assertEqual(f.mun_values, {SP: 10.0, CAMPINAS: 5.0, SANTOS: 3.0})
        self.assertEqual(f.mun_ufs, ["SP"])
        self.assertEqual(f.headline, 19.0)                       # o não resolvido conta no total
        self.assertIn(("Campinas", 5.0), f.values)              # nome do IBGE, não o da base

    def test_municipio_vence_a_uf_e_desambigua(self):
        rows = [{"uf": "PI", "municipio": "Bom Jesus", "v": 2.0},
                {"uf": "RS", "municipio": "Bom Jesus", "v": 3.0},
                {"uf": "SP", "municipio": "Campinas", "v": 4.0}]
        f = build(rows, sql="SELECT uf, municipio, SUM(v) AS v FROM t GROUP BY uf, municipio",
                  kinds={"uf": "text", "municipio": "text", "v": "numeric"})
        self.assertEqual(f.dimension_kind, "municipio")
        self.assertEqual(sorted(r for r, _ in f.values),
                         ["Bom Jesus (PI)", "Bom Jesus (RS)", "Campinas (SP)"])

    def test_codigo_ibge_como_dimensao(self):
        rows = [{"cod_ibge": 3550308, "v": 1.0}, {"cod_ibge": 3509502, "v": 2.0}]
        f = build(rows, sql="SELECT cod_ibge, SUM(v) AS v FROM t GROUP BY cod_ibge",
                  kinds={"cod_ibge": "numeric", "v": "numeric"})
        self.assertEqual((f.dimension_kind, f.metric), ("municipio", "v"))

    def test_contagem_de_municipios_nao_e_recorte(self):
        rows = [{"uf": "SP", "total_municipios": 645, "v": 1.0}, {"uf": "RJ", "total_municipios": 92, "v": 2.0}]
        f = build(rows, sql="SELECT uf, total_municipios, SUM(v) AS v FROM t GROUP BY 1, 2",
                  kinds={"uf": "text", "total_municipios": "numeric", "v": "numeric"})
        self.assertEqual(f.dimension_kind, "uf")

    def test_valores_na_tabela_do_relatorio(self):
        tabela = render.values_table(_sp_facts())
        self.assertIn("| Município |", tabela)
        self.assertIn("| São Paulo |", tabela)


class TestMapaMunicipal(unittest.TestCase):
    def test_desenha_os_645_municipios_e_colore_os_do_resultado(self):
        pagina = interactive.page(_sp_facts())
        self.assertEqual(pagina.count("<path "), 645)
        self.assertIn('data-k="3550308"', pagina)
        self.assertIn("Mapa dos municípios de SP", pagina)
        self.assertIn('"nome": "Campinas"', pagina)
        self.assertIn('"3500105": "Adamantina"', pagina)       # sem dado, mas com nome

    def test_muitas_ufs_vira_so_ranking(self):
        rows = [{"cod_ibge": cod, "v": 1.0} for cod in
                ("1100015", "1200013", "1302603", "1400100", "1500107", "1600105", "1702109")]
        f = build(rows, sql="SELECT cod_ibge, SUM(v) AS v FROM t GROUP BY cod_ibge",
                  kinds={"cod_ibge": "numeric", "v": "numeric"})
        self.assertGreater(len(f.mun_ufs), municipios.MAX_UFS_MAPA)
        pagina = interactive.page(f)
        self.assertNotIn("<path ", pagina)
        self.assertIn('class="linha"', pagina)


class TestCores(unittest.TestCase):
    def test_linear_quando_parecidos(self):
        self.assertEqual(interactive.color_positions({"a": 1, "b": 2, "c": 3}),
                         {"a": 0.0, "b": 0.5, "c": 1.0})

    def test_ranking_quando_um_domina(self):
        """Capital com R$ 361 mi e o resto com milhares: na escala linear, todos
        os outros sairiam no mesmo lilás quase branco."""
        tons = interactive.color_positions({"sp": 361e6, "a": 5e6, "b": 3e6, "c": 1e6, "d": 566},
                                           floor=0.18)
        self.assertEqual(tons["sp"], 1.0)
        self.assertAlmostEqual(tons["d"], 0.18)
        self.assertGreater(tons["c"], 0.3)               # linear daria ~0,18
        self.assertTrue(tons["sp"] > tons["a"] > tons["b"] > tons["c"] > tons["d"])

    def test_municipio_sem_dado_quase_branco(self):
        pagina = interactive.page(_sp_facts())
        self.assertIn('fill="#f3f2f7"', pagina)
        self.assertIn('class="mapa municipal"', pagina)


class TestBotaoDosMunicipios(unittest.TestCase):
    def _uf_facts(self):
        rows = [{"uf": u["sigla"], "v": float(i + 1)} for i, u in enumerate(ibge.ufs())]
        return build(rows, sql="SELECT uf, SUM(v) AS v FROM t GROUP BY uf",
                     kinds={"uf": "text", "v": "numeric"})

    def test_pergunta_do_botao(self):
        self.assertEqual(interactive.drill_question("Quanto foi contratado no FAR por estado?", "SP"),
                         "Quanto foi contratado no FAR por município em São Paulo?")
        self.assertEqual(interactive.drill_question("Quantas UH por UF?", "BA"),
                         "Quantas UH por município na Bahia?")
        self.assertEqual(interactive.drill_question("Total do FAR por unidade da federação", "PA"),
                         "Total do FAR por município no Pará?")
        self.assertEqual(interactive.drill_question("Qual o valor contratado do FAR?", "AC"),
                         "Qual o valor contratado do FAR, por município no Acre?")

    def test_mapa_por_uf_com_pergunta_tem_o_botao(self):
        pagina = interactive.page(self._uf_facts(), question="Quanto foi contratado no FAR por estado?")
        self.assertIn('id="painel"', pagina)
        self.assertIn("input:prompt:submit", pagina)
        self.assertIn("por município em São Paulo?", pagina)
        self.assertIn("Ver os municípios da Bahia", pagina)
        self.assertIn("Ver os municípios do Pará", pagina)

    def test_sem_pergunta_nao_ha_botao(self):
        pagina = interactive.page(self._uf_facts())
        self.assertIn('"drill": {}', pagina)

    def test_mapa_municipal_nao_detalha_de_novo(self):
        pagina = interactive.page(_sp_facts(), question="Quanto foi contratado por município em São Paulo?")
        self.assertIn('"drill": {}', pagina)


class TestDetalharNoMesmoSql(unittest.TestCase):
    """O clique no estado detalha o MESMO cálculo: mesma tabela, mesma métrica."""

    def setUp(self):
        try:
            import sqlglot  # noqa: F401
        except ImportError:
            self.skipTest("sqlglot (container)")
        from rag.journey import drill

        self.drill = drill

    def test_uf_vira_municipio_com_filtro(self):
        sql = ("SELECT uf, SUM(valor_contratado) AS total FROM prata.prata_far_empreendimento "
               "GROUP BY uf ORDER BY total DESC")
        novo = self.drill.rewrite(sql, "uf", "BA", "cod_ibge")
        self.assertEqual(novo, "SELECT cod_ibge, SUM(valor_contratado) AS total FROM "
                               "prata.prata_far_empreendimento WHERE uf = 'BA' GROUP BY cod_ibge "
                               "ORDER BY total DESC")

    def test_apelido_e_filtro_existente(self):
        sql = "SELECT sigla_uf AS estado, COUNT(*) AS n FROM ouro.x WHERE ativo = 1 GROUP BY sigla_uf"
        self.assertEqual(self.drill.uf_source_column(sql, "estado"), "sigla_uf")
        self.assertEqual(self.drill.rewrite(sql, "sigla_uf", "Bahia", "municipio"),
                         "SELECT municipio AS municipio, COUNT(*) AS n FROM ouro.x "
                         "WHERE ativo = 1 AND sigla_uf = 'Bahia' GROUP BY municipio")

    def test_juncao_nao_se_reescreve(self):
        self.assertIsNone(self.drill.table_of("SELECT a.uf FROM t a JOIN u b ON a.x = b.x"))

    def test_pedido_e_coluna(self):
        self.assertEqual(self.drill.requested_uf("Quanto no FAR por município na Bahia?"), "BA")
        self.assertEqual(self.drill.requested_uf("e por cidades de São Paulo?"), "SP")
        self.assertIsNone(self.drill.requested_uf("Quanto por estado?"))
        self.assertEqual(self.drill.municipio_column(["uf", "municipio", "cod_ibge"]), "cod_ibge")
        self.assertEqual(self.drill.municipio_column(["uf", "municipio"]), "municipio")
        self.assertIsNone(self.drill.municipio_column(["uf", "total_municipios"]))


if __name__ == "__main__":
    unittest.main()

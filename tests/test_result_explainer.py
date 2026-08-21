"""O que a explicação do resultado (ADR-0010 item c) pode afirmar.

O parágrafo é escrito por LLM, mas o **digest** que ele recebe é determinístico —
e é o teto do que ele pode dizer: número que não está aqui é número inventado.
Estes testes fixam esse contrato (e os caminhos que nem chamam o modelo).

Só stdlib + o componente; não sobe MCP, banco nem OpenAI.
"""
from __future__ import annotations

import unittest

try:  # pragma: no cover — depende do ambiente (Haystack só existe no container)
    from rag.components.answer.result_explainer import (
        ResultExplainer, _SEM_LINHAS, _fmt, _one_paragraph, build_digest,
    )
    HAYSTACK = True
except ImportError:  # pragma: no cover
    HAYSTACK = False

_CATEGORICO = [
    {"uf": "SP", "total_valor_contratado": 2500000.0},
    {"uf": "MG", "total_valor_contratado": 1800000.5},
    {"uf": "BA", "total_valor_contratado": 900000.0},
    {"uf": "RR", "total_valor_contratado": 1000.0},
]
_SERIE = [
    {"mes": "2024-01", "empreendimentos": 10},
    {"mes": "2024-02", "empreendimentos": 25},
    {"mes": "2024-03", "empreendimentos": 40},
]


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestDigest(unittest.TestCase):
    def test_categorico_traz_forma_estatistica_e_lideres(self):
        d = build_digest(_CATEGORICO, ["uf", "total_valor_contratado"], top_n=2)
        self.assertIn("Linhas retornadas: 4", d)
        self.assertIn("uf (texto)", d)
        self.assertIn("total_valor_contratado (número)", d)
        self.assertIn("soma: 5.201.000,50", d)      # formato brasileiro
        self.assertIn("máximo: 2.500.000", d)   # inteiro não ganha ",00"
        self.assertIn("Maiores total_valor_contratado por uf: SP", d)
        self.assertIn("Menores:", d)

    def test_serie_temporal_declara_comeco_e_fim(self):
        d = build_digest(_SERIE, ["mes", "empreendimentos"])
        self.assertIn("Série por mes: começa em 2024-01 com 10", d)
        self.assertIn("termina em 2024-03 com 40", d)

    def test_escalar_e_o_proprio_valor(self):
        d = build_digest([{"total": 1234567}], ["total"])
        self.assertIn("Valores: total: 1.234.567", d)
        self.assertNotIn("Maiores", d)

    def test_resultado_no_teto_de_linhas_avisa_leitura_parcial(self):
        from rag.settings.configs import settings
        linhas = [{"uf": f"U{i}", "v": i} for i in range(settings.MAX_EXEC_ROWS)]
        self.assertIn("leitura é parcial", build_digest(linhas, ["uf", "v"]))
        self.assertNotIn("leitura é parcial", build_digest(linhas[:-1], ["uf", "v"]))

    def test_digest_nao_carrega_as_linhas_cruas(self):
        """LGPD (ADR-0010): o LLM lê estatística, não a tabela. Só as `top_n`
        categorias líderes e as 2 últimas aparecem nominalmente."""
        d = build_digest(_CATEGORICO, ["uf", "total_valor_contratado"], top_n=1)
        self.assertIn("SP", d)
        self.assertNotIn("MG", d)   # ficou fora do topo e das duas últimas

    def test_numero_sai_no_formato_brasileiro(self):
        self.assertEqual(_fmt(1234567.891), "1.234.567,89")
        self.assertEqual(_fmt(27), "27")
        self.assertEqual(_fmt(None), "—")
        self.assertEqual(_fmt("uf"), "uf")


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestCaminhosSemLLM(unittest.TestCase):
    """Os casos que não chamam o modelo — e por isso não podem falhar."""

    def test_execucao_falhada_nao_vira_texto(self):
        # Mesma decisão de produto do bloco de SQL: o diagnóstico fica no log.
        out = ResultExplainer().run(rows=[], columns=[], exec_ok=False, query="q")
        self.assertEqual(out["summary"], "")

    def test_consulta_valida_sem_linhas_ganha_frase_propria(self):
        out = ResultExplainer().run(rows=[], columns=["uf"], exec_ok=True, query="q")
        self.assertEqual(out["summary"], _SEM_LINHAS)

    def test_desligado_por_config_nao_explica(self):
        from rag.settings.configs import settings
        antes = settings.RESULT_SUMMARY_ENABLED
        settings.RESULT_SUMMARY_ENABLED = False
        try:
            out = ResultExplainer().run(rows=_CATEGORICO, columns=["uf"], exec_ok=True)
            self.assertEqual(out["summary"], "")
        finally:
            settings.RESULT_SUMMARY_ENABLED = antes

    def test_falha_do_llm_devolve_vazio_em_vez_de_levantar(self):
        explainer = ResultExplainer()
        explainer._ask_llm = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("429"))
        out = explainer.run(rows=_CATEGORICO, columns=["uf", "total_valor_contratado"],
                            exec_ok=True, query="q")
        self.assertEqual(out["summary"], "")

    def test_so_o_primeiro_paragrafo_sobrevive(self):
        self.assertEqual(_one_paragraph("Um parágrafo.\n\nOutro."), "Um parágrafo.")
        self.assertEqual(_one_paragraph("  "), "")


if __name__ == "__main__":
    unittest.main()

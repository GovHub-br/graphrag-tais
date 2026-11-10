"""Métricas do eval da jornada (`eval/journey/metrics.py`) — roda no host."""
import unittest

from eval.journey import truth
from eval.journey.metrics import aggregate, phrase_numbers_outside, score_case


def _turn(kind="pergunta", **extra):
    base = {"n": 0, "kind": kind, "input": "q", "latency_s": 1.0, "bytes": 2048,
            "images": [], "exception": "", "chat_error": False, "emoji_leak": False,
            "marker": "[//]: # (tais:v1 ref=a rota=numero)", "warnings": [], "markdown": "",
            "follow_ups": []}
    base.update(extra)
    return base


class TestPhraseNumbers(unittest.TestCase):
    def test_numero_dos_fatos_passa(self):
        self.assertEqual(phrase_numbers_outside("Foram 878 obras; a BA tem 101.",
                                                "878 Maiores: BA: 101"), [])

    def test_formato_compacto_passa(self):
        self.assertEqual(phrase_numbers_outside("R$ 2,1 bi no total.", "R$ 2,1 bi"), [])

    def test_numero_inventado_aparece(self):
        self.assertEqual(phrase_numbers_outside("Cresceu 12% no ano.", "878"), ["12"])


class TestScoreCase(unittest.TestCase):
    def test_numero_com_fallback_da_frase(self):
        case = {"id": "x", "expected_route": "numero", "expected_outcome": "numero"}
        t0 = _turn(rota="numero", status="ok", exec_ok=True,
                   warnings=["WARNING: [jornada] frase sem o número '878'; usando a determinística"])
        s = score_case(case, [t0])
        self.assertTrue(s["route_ok"] and s["reached_number"] and s["outcome_ok"])
        self.assertEqual(s["phrase_source"], "sem_numero")

    def test_recusa_sem_marcador(self):
        case = {"id": "x", "expected_route": "recusa", "expected_outcome": "recusa"}
        s = score_case(case, [_turn(marker="", markdown="Não respondo dado pessoal.")])
        self.assertEqual(s["route_observed"], "recusa")
        self.assertTrue(s["outcome_ok"])

    def test_botao_sem_estado_e_laco(self):
        case = {"id": "x", "expected_route": "sem_pergunta", "expected_outcome": "sem_pergunta"}
        t0 = _turn(marker="[//]: # (tais:v1 ref= rota=numero)",
                   markdown="Faça uma pergunta primeiro", follow_ups=["Contar a história"])
        s = score_case(case, [t0])
        self.assertTrue(s["outcome_ok"])
        self.assertTrue(s["buttons_without_state"])

    def test_botao_clicado_que_volta_e_contado(self):
        case = {"id": "x", "expected_route": "numero", "expected_outcome": "numero"}
        t0 = _turn(rota="numero", status="ok", exec_ok=True)
        t1 = _turn(kind="clique", input="Contar a história", follow_ups=["Contar a história"])
        s = score_case(case, [t0, t1])
        self.assertTrue(s["tracks"][0]["botao_repetido"])
        resumo = aggregate([{**case, "score": s}])
        self.assertEqual(resumo["tracks"]["Contar a história"]["botao_repetido"], 1)


def _uf(texto):
    return {"sigla": {"bahia": "BA", "ba": "BA", "sp": "SP"}.get(texto.lower())} \
        if texto.lower() in ("bahia", "ba", "sp") else None


class TestGabarito(unittest.TestCase):
    """Comparação com o banco real (`eval/journey/truth.py`)."""

    LINHAS = [{"chave": "BA", "valor": 101}, {"chave": "SP", "valor": 63}]

    def test_total_que_bate_e_ok(self):
        out = truth.compare({"headline": "total", "key": "uf"}, self.LINHAS,
                            {"reached": True, "headline": 164.0, "values": [["Bahia", 101], ["SP", 63]]},
                            uf_resolver=_uf)
        self.assertEqual(out["verdict"], "ok")
        self.assertEqual(out["keys"]["matched"], 2)

    def test_total_dobrado_aponta_linha_de_total(self):
        out = truth.compare({"headline": "total"}, [{"chave": "a", "valor": 19.5e9}],
                            {"reached": True, "headline": 39.0e9})
        self.assertEqual(out["verdict"], "diferente")
        self.assertIn("dobrado", out["hint"])

    def test_fracao_no_lugar_de_percentual(self):
        self.assertIn("1/100", truth.hint(0.061, 6.1))

    def test_leitura_alternativa_e_separada_de_erro(self):
        out = truth.compare({"headline": "maior"}, [{"chave": "x", "valor": 3173}],
                            {"reached": True, "headline": 10940.0}, alternatives=[("soma do top 10", 10940.0)])
        self.assertEqual(out["verdict"], "leitura_alternativa")
        self.assertEqual(truth.owner(out), "interpretação (outra leitura válida)")

    def test_ultimo_usa_a_maior_chave(self):
        linhas = [{"chave": "2025-12-01", "valor": 6.1}, {"chave": "2025-01-01", "valor": 6.85}]
        self.assertEqual(truth.headline_from_rows(linhas, "ultimo"), 6.1)

    def test_periodo_normaliza(self):
        self.assertEqual(truth.norm_key("2025-03-01", "periodo"), "2025-03")

    def test_sem_numero_e_dono_catalogo_quando_falta_tabela(self):
        out = truth.compare({"headline": "total"}, self.LINHAS, {"reached": False})
        out.update(data_checks=[], catalog_missing=["ouro_ts_resumo_uf"])
        self.assertEqual(out["verdict"], "sem_numero")
        self.assertTrue(truth.owner(out).startswith("catálogo"))

    def test_checagem_de_dado(self):
        self.assertFalse(truth.check_data({"expect": "zero", "means": "linha SOMA"}, [{"v": 1}])["ok"])
        self.assertTrue(truth.check_data({"expect": ">1"}, [{"v": 7.5}])["ok"])


if __name__ == "__main__":
    unittest.main()

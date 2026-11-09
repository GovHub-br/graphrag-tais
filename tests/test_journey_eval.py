"""Métricas do eval da jornada (`eval/journey/metrics.py`) — roda no host."""
import unittest

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


if __name__ == "__main__":
    unittest.main()

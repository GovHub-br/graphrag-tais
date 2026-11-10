"""Contexto da conversa (`rag.conversation`) e acabamento do texto da resposta.

Pedidos de produto de 10/11/2026: (1) número por extenso, não "1,3 mi"; (2) a
pergunta de seguimento ("e em SP?") usa o histórico do chat; (3) sem " · ",
" — " e "- item" na resposta; (4) os valores do gráfico também em texto.

Roda no host, sem Haystack e sem rede: a reescrita do LLM é injetada.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag import conversation  # noqa: E402
from rag.journey import fmt, render, tracks  # noqa: E402
from rag.journey.facts import build  # noqa: E402
from rag.journey.text import plain_typography  # noqa: E402

RESPOSTA_FAR = (
    "Existem 878 empreendimentos do FAR somando 27 UFs. A Bahia lidera com 101.\n\n"
    "![878: empreendimentos](data:image/png;base64,AAAA)\n\n"
    "| UF | Empreendimentos |\n| :-- | --: |\n| Bahia | 101 |\n\n"
    "*Fonte: `ouro.ouro_far_ficha_empreendimento`.*\n\n"
    "[//]: # (tais:v1 ref=abc123 rota=numero trilha=populacao)"
)


def _historico(*pares):
    msgs = []
    for papel, texto in pares:
        msgs.append({"role": papel, "content": texto})
    return msgs


class TestHistorico(unittest.TestCase):
    def test_sem_historico_nao_chama_o_llm(self):
        chamadas = []
        msgs = _historico(("user", "Quantos empreendimentos do FAR existem por estado?"))
        q = conversation.standalone_question(
            msgs[-1]["content"], msgs, lambda h, q: chamadas.append(q) or "{}")
        self.assertEqual(q, "Quantos empreendimentos do FAR existem por estado?")
        self.assertEqual(chamadas, [])

    def test_seguimento_vai_com_a_pergunta_e_o_comeco_da_resposta(self):
        msgs = _historico(("user", "Quantos empreendimentos do FAR existem por estado?"),
                          ("assistant", RESPOSTA_FAR), ("user", "e em São Paulo?"))
        vistos = {}

        def fake(historico, pergunta):
            vistos["h"], vistos["q"] = historico, pergunta
            return json.dumps({"pergunta": "Quantos empreendimentos do FAR existem em São Paulo?"})

        q = conversation.standalone_question("e em São Paulo?", msgs, fake)
        self.assertEqual(q, "Quantos empreendimentos do FAR existem em São Paulo?")
        self.assertEqual(vistos["q"], "e em São Paulo?")
        self.assertIn("Pessoa: Quantos empreendimentos do FAR existem por estado?", vistos["h"])
        self.assertIn("878 empreendimentos", vistos["h"])
        # O que só faz sentido na tela não vai para o LLM.
        for lixo in ("base64", "| UF |", "tais:v1", "ouro_far"):
            self.assertNotIn(lixo, vistos["h"])

    def test_clique_em_botao_nao_vira_pergunta_do_historico(self):
        msgs = _historico(("user", "Quanto foi contratado no FAR por UF?"),
                          ("assistant", "R$ 24.956.847.898 no total."),
                          ("user", tracks.GESTOR.button),
                          ("assistant", "## Números para decisão"),
                          ("user", "e no Rural?"))
        turnos = conversation.previous_turns(msgs, skip=lambda t: bool(tracks.match_track(t)))
        self.assertEqual([p for p, _ in turnos], ["Quanto foi contratado no FAR por UF?"])

    def test_limite_de_turnos(self):
        pares = []
        for i in range(6):
            pares += [("user", f"pergunta {i}"), ("assistant", f"resposta {i}.")]
        msgs = _historico(*pares, ("user", "e agora?"))
        turnos = conversation.previous_turns(msgs, limit=3)
        self.assertEqual([p for p, _ in turnos], ["pergunta 3", "pergunta 4", "pergunta 5"])

    def test_resposta_estranha_do_llm_mantem_a_pergunta(self):
        msgs = _historico(("user", "a"), ("assistant", "b."), ("user", "e em SP?"))
        for bruto in ("não é json", '{"pergunta": ""}', '{"outra": 1}', "{" + "x" * 900 + "}"):
            self.assertEqual(conversation.standalone_question("e em SP?", msgs, lambda h, q: bruto),
                             "e em SP?", bruto[:20])

    def test_falha_do_llm_mantem_a_pergunta(self):
        msgs = _historico(("user", "a"), ("assistant", "b."), ("user", "e em SP?"))

        def quebra(h, q):
            raise TimeoutError("openai fora")

        self.assertEqual(conversation.standalone_question("e em SP?", msgs, quebra), "e em SP?")

    def test_nota_so_quando_a_pergunta_mudou(self):
        self.assertEqual(conversation.interpreted_note("Quanto no FAR?", "quanto no FAR"), "")
        nota = conversation.interpreted_note("e em SP?", "Quanto foi contratado no FAR em SP?")
        self.assertIn("Quanto foi contratado no FAR em SP?", nota)

    def test_conteudo_em_partes(self):
        msg = {"role": "user", "content": [{"type": "text", "text": "oi"}, {"type": "image_url"}]}
        self.assertEqual(conversation.content_of(msg), "oi")


class TestNumeroPorExtenso(unittest.TestCase):
    def test_contagem_e_dinheiro(self):
        self.assertEqual(fmt.exact(1_300_452), "1.300.452")
        self.assertEqual(fmt.exact(24_956_847_897.92, money=True), "R$ 24.956.847.898")
        self.assertEqual(fmt.exact(491.58, money=True), "R$ 491,58")
        self.assertEqual(fmt.exact(820, money=True), "R$ 820")

    def test_taxa_pequena_nao_vira_zero_nem_arredonda_para_cima(self):
        self.assertEqual(fmt.exact(0.06096), "0,061")
        self.assertEqual(fmt.exact(6.096), "6,1")
        self.assertEqual(fmt.exact(0.0004), "0,0004")

    def test_frase_e_cartao_usam_o_numero_inteiro(self):
        rows = [{"uf": "SP", "v": 1_000_000.0}, {"uf": "BA", "v": 300_452.0}]
        f = build(rows, sql="SELECT uf, SUM(v) AS v FROM t GROUP BY uf",
                  kinds={"uf": "text", "v": "numeric"})
        self.assertEqual(render.headline_text(f), "1.300.452")
        self.assertIn("1.300.452", render.fallback_phrase(f))
        # Dentro do desenho, o compacto continua (não cabe por extenso).
        self.assertEqual(render.chart_fmt(f)(f.headline), "1,3 mi")


class TestValoresDoGrafico(unittest.TestCase):
    def _uf(self, n):
        from rag.journey import ibge

        rows = [{"uf": u["sigla"], "v": float(1000 + i)} for i, u in enumerate(ibge.ufs()[:n])]
        return build(rows, sql="SELECT uf, SUM(v) AS v FROM t GROUP BY uf",
                     kinds={"uf": "text", "v": "numeric"})

    def test_poucos_itens_a_mostra(self):
        tabela = render.values_table(self._uf(5))
        self.assertNotIn("<details>", tabela)
        self.assertEqual(tabela.count("\n| "), 1 + 1 + 5)   # cabeçalho, separador, 5 UFs
        self.assertIn("% do total", tabela)

    def test_muitos_itens_recolhidos(self):
        tabela = render.values_table(self._uf(27))
        self.assertTrue(tabela.startswith("<details>"))
        self.assertIn("(27)", tabela)

    def test_resposta_traz_o_mapa_e_os_valores(self):
        class Charts:
            def __getattr__(self, nome):
                return lambda *a, **k: b"png"

        md = render.populacao(self._uf(5), "Frase.", None, charts=Charts(), tables=["ouro.t"],
                              service="Cidades")
        self.assertIn("Os valores do gráfico", md)
        self.assertLess(md.index("data:image/png"), md.index("Os valores do gráfico"))


class TestTipografia(unittest.TestCase):
    def test_prosa(self):
        self.assertEqual(plain_typography("Texto — com travessão · e ponto."),
                         "Texto, com travessão, e ponto.")
        self.assertEqual(plain_typography("**Lei** — existe no catálogo"),
                         "**Lei**: existe no catálogo")

    def test_lista_vira_paragrafos(self):
        self.assertEqual(plain_typography("Itens:\n- um\n- dois\nFim."),
                         "Itens:\n\num\n\ndois\n\nFim.")

    def test_nao_toca_no_que_nao_e_prosa(self):
        intacto = ("```sql\nSELECT a - b FROM t -- x\n```\n| a | — |\n| --- | --- |\n"
                   "![a · b](data:image/png;base64,AA)\n---\n[//]: # (tais:v1 ref=1)\n"
                   "<summary>a — b</summary>")
        self.assertEqual(plain_typography(intacto), intacto)
        self.assertEqual(plain_typography("Em 2026-06, no `x · y`, o Pró-Moradia."),
                         "Em 2026-06, no `x · y`, o Pró-Moradia.")

    def test_celula_de_tabela(self):
        self.assertEqual(plain_typography("| `t` | Prata — série mensal |"),
                         "| `t` | Prata, série mensal |")
        self.assertEqual(plain_typography("| a | — |\n| :-- | --: |"), "| a | — |\n| :-- | --: |")
        self.assertEqual(plain_typography("| `apf` | text · PK |"), "| `apf` | text, PK |")

    def test_negrito_no_inicio_nao_e_lista(self):
        self.assertEqual(plain_typography("**Fora da curva:** BA."), "**Fora da curva:** BA.")
        self.assertEqual(plain_typography("*Fonte: x.*"), "*Fonte: x.*")


if __name__ == "__main__":
    unittest.main()

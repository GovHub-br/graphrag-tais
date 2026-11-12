"""Gráfico interativo da jornada: página, link assinado, rota e Function.

O PNG não deixa ler o valor de cada UF (pedido de produto, 10/11/2026). O RAG
deixa na resposta uma linha escondida com o caminho assinado do gráfico; a
Function do Open WebUI busca o HTML e o grava como "embed" embaixo da mensagem.

Página, assinatura e Function rodam no host. A rota (`TestRota`) precisa do
FastAPI e é pulada sem ele — roda no container.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag.components.storage import download_links  # noqa: E402
from rag.journey import ibge, interactive, render  # noqa: E402
from rag.journey.facts import build  # noqa: E402
from rag.settings.configs import settings  # noqa: E402


def _uf_facts(n=27):
    rows = [{"uf": u["sigla"], "valor": float(1_000_000 + i * 12_345)}
            for i, u in enumerate(ibge.ufs()[:n])]
    return build(rows, sql="SELECT uf, SUM(valor) AS valor FROM t GROUP BY uf",
                 kinds={"uf": "text", "valor": "numeric"})


def _dados(pagina: str) -> str:
    return pagina.split("const DATA=", 1)[1].split(";", 1)[0]


class TestPagina(unittest.TestCase):
    def test_mapa_com_as_27_ufs_e_valor_exato(self):
        pagina = interactive.page(_uf_facts(), interactive.ONDE)
        self.assertEqual(pagina.count("<path "), 27)
        self.assertIn("1.000.000", _dados(pagina))               # valor por extenso no tooltip
        self.assertIn("% do total", _dados(pagina))
        self.assertIn("iframe:height", pagina)                   # altura para o Open WebUI
        self.assertNotIn("<script src", pagina)                  # nada de CDN

    def test_aba_inicial_depende_do_tipo(self):
        onde = interactive.page(_uf_facts(), interactive.ONDE)
        ranking = interactive.page(_uf_facts(), interactive.RANKING)
        self.assertIn('data-v="mapa" class="on"', onde)
        self.assertIn('data-v="rank" class="on"', ranking)

    def test_serie_vira_linha(self):
        rows = [{"mes": f"2025-{m:02d}-01", "v": float(m * 10)} for m in range(1, 13)]
        f = build(rows, sql="SELECT mes, SUM(v) AS v FROM t GROUP BY mes",
                  kinds={"mes": "date", "v": "numeric"})
        pagina = interactive.page(f)
        self.assertIn("<polyline", pagina)
        self.assertEqual(pagina.count('class="alvo"'), 12)
        self.assertIn("jan/2025", _dados(pagina))

    def test_categoria_vira_barras(self):
        rows = [{"eo": f"Entidade {i}", "v": float(i)} for i in range(1, 6)]
        f = build(rows, sql="SELECT eo, SUM(v) AS v FROM t GROUP BY eo",
                  kinds={"eo": "text", "v": "numeric"})
        pagina = interactive.page(f)
        self.assertEqual(pagina.count('class="linha"'), 5)
        self.assertNotIn("<svg class=\"mapa\"", pagina)

    def test_rotulo_do_dado_nao_vira_html_nem_fecha_o_script(self):
        rows = [{"eo": "<img src=x onerror=alert(1)>", "v": 1.0},
                {"eo": "</script><b>x", "v": 2.0}]
        f = build(rows, sql="SELECT eo, SUM(v) AS v FROM t GROUP BY eo",
                  kinds={"eo": "text", "v": "numeric"})
        pagina = interactive.page(f)
        corpo, script = pagina.split("<script>", 1)
        self.assertNotIn("<img src=x", corpo)
        self.assertEqual(script.count("</script>"), 1)          # só o fechamento de verdade

    def test_sem_recorte_nao_ha_grafico(self):
        f = build([{"total": 10.0}], sql="SELECT SUM(x) AS total FROM t", kinds={"total": "numeric"})
        self.assertIsNone(interactive.page(f))


def _mun_facts():
    """43 municípios, os dois maiores fora da curva (o caso da tela de 11/11)."""
    valores = ([("Santo André", 141e6), ("Sorocaba", 138e6), ("Campinas", 116e6), ("Santos", 102e6),
                ("Osasco", 93e6), ("Bauru", 91e6)]
               + [(f"Município {i}", 80e6 - i * 1.9e6) for i in range(36)] + [("Ilhabela", 6e6)])
    rows = [{"municipio_nome": n, "valor_contratado": v} for n, v in valores]
    return build(rows, sql="SELECT municipio_nome, SUM(valor_contratado) AS valor_contratado FROM t GROUP BY 1",
                 kinds={"municipio_nome": "text", "valor_contratado": "numeric"})


class TestForaDaCurva(unittest.TestCase):
    def test_um_ponto_por_valor_e_os_de_fora_em_laranja_com_botao(self):
        f = _mun_facts()
        self.assertEqual([r for r, _ in f.outliers()], ["Santo André", "Sorocaba"])
        pagina = interactive.page_for(f, interactive.FORA)
        self.assertEqual(pagina.count('class="pt'), 43)
        self.assertEqual(pagina.count('class="pt fora"'), 2)
        # O nome vai num botão embaixo, não num rótulo ao lado do ponto.
        self.assertEqual(pagina.count('class="chip"'), 2)
        self.assertIn("Santo André <b>R$ 141.000.000</b>", pagina)
        self.assertIn("acima do limite de", _dados(pagina))
        self.assertIn("Dentro da faixa esperada", _dados(pagina))

    def test_sem_ponto_fora_nao_ha_grafico(self):
        rows = [{"eo": f"E{i}", "v": float(100 + i)} for i in range(10)]
        f = build(rows, sql="SELECT eo, SUM(v) AS v FROM t GROUP BY eo", kinds={"eo": "text", "v": "numeric"})
        self.assertFalse(interactive.available(f, interactive.FORA))
        self.assertIsNone(interactive.page_for(f, interactive.FORA))

    def test_serie_do_gestor_marca_o_ponto_fora(self):
        rows = [{"mes": f"2025-{m:02d}-01", "v": float(100 + m * (9 if m == 7 else 1))} for m in range(1, 13)]
        f = build(rows, sql="SELECT mes, SUM(v) AS v FROM t GROUP BY mes", kinds={"mes": "date", "v": "numeric"})
        self.assertEqual(interactive.page(f, interactive.RANKING).count('class="alerta"'), 1)
        self.assertEqual(interactive.page(f, interactive.ONDE).count('class="alerta"'), 0)
        self.assertFalse(interactive.available(f, interactive.FORA))   # a série já mostra


class TestDistribuicao(unittest.TestCase):
    def test_uma_barra_por_faixa_e_a_lista_de_quem_esta_nela(self):
        f = _mun_facts()
        faixas = f.bins()
        self.assertEqual(sum(len(d) for _, _, d in faixas), 43)       # ninguém fica de fora
        pagina = interactive.page_for(f, interactive.DISTRIBUICAO)
        self.assertEqual(pagina.count('class="bin"'), len(faixas))
        self.assertIn("mediana", pagina)
        self.assertIn("Santo André", _dados(pagina))

    def test_poucos_valores_nao_tem_histograma(self):
        rows = [{"eo": f"E{i}", "v": float(i)} for i in range(5)]
        f = build(rows, sql="SELECT eo, SUM(v) AS v FROM t GROUP BY eo", kinds={"eo": "text", "v": "numeric"})
        self.assertIsNone(interactive.page_for(f, interactive.DISTRIBUICAO))
        self.assertEqual(render.distribution_table(f), "")


class TestRegioes(unittest.TestCase):
    def test_um_segmento_por_uf_e_uma_linha_por_regiao(self):
        pagina = interactive.page_for(_uf_facts(), interactive.REGIOES)
        self.assertEqual(pagina.count('class="seg'), 27)
        self.assertEqual(pagina.count('class="reg"'), 5)
        self.assertIn("da região", _dados(pagina))

    def test_categoria_nao_tem_regioes(self):
        self.assertIsNone(interactive.page_for(_mun_facts(), interactive.REGIOES))


class TestValoresDeReferencia(unittest.TestCase):
    """No .docx não há cursor: embaixo de cada gráfico, os valores dele."""

    def test_tabela_e_frase_do_fora_da_curva(self):
        f = _mun_facts()
        tabela = render.outliers_table(f)
        self.assertIn("**Os valores fora da curva**", tabela)
        self.assertIn("| Santo André | R$ 141.000.000 | R$ 9.700.000 acima do limite de R$ 131.300.000 |", tabela)
        frase = render.outliers_text(f)
        self.assertIn("Santo André (R$ 141.000.000) e Sorocaba (R$ 138.000.000)", frase)
        self.assertIn("até R$ 131.300.000", frase)          # limite de baixo negativo não aparece

    def test_tabela_da_distribuicao_bate_com_as_faixas(self):
        f = _mun_facts()
        tabela = render.distribution_table(f)
        self.assertEqual(tabela.count("\n| R$ "), len(f.bins()))
        self.assertIn("e mais", tabela)

    def test_tabela_das_regioes(self):
        tabela = render.regions_table(_uf_facts())
        self.assertIn("| Nordeste |", tabela)
        self.assertIn("% do total", tabela)


class TestChatSemPngDuplicado(unittest.TestCase):
    """No chat o embed substitui o PNG e a tabela; o relatório guarda os dois."""

    def _md(self, n):
        class Charts:
            def __getattr__(self, nome):
                return lambda *a, **k: b"png"

        return render.populacao(_uf_facts(n), "Frase com 1.000.000.", None, charts=Charts(),
                                tables=["ouro.t"], service="Cidades")

    def test_tira_o_mapa_e_a_tabela_e_mantem_o_resto(self):
        completo = self._md(27)
        self.assertIn("Mapa do Brasil", completo)
        self.assertIn("Os valores do gráfico", completo)
        chat = render.for_chat(completo)
        self.assertNotIn("Mapa do Brasil", chat)
        self.assertNotIn("Os valores do gráfico", chat)
        self.assertNotIn("| UF |", chat)
        self.assertIn("Frase com 1.000.000.", chat)
        self.assertIn("*Fonte:", chat)
        self.assertEqual(chat.count("data:image/png"), completo.count("data:image/png") - 1)

    def test_tabela_aberta_tambem_sai(self):
        chat = render.for_chat(self._md(5))
        self.assertNotIn("Os valores do gráfico", chat)
        self.assertNotIn("| UF |", chat)

    def test_marcador_no_lugar_do_grafico(self):
        md = "### 3. Onde?\n\n![Mapa do Brasil: x](data:image/png;base64,AA)\n\n### 4. Para quem?"
        self.assertEqual(render.for_chat(md, "_abaixo_"), "### 3. Onde?\n\n_abaixo_\n\n### 4. Para quem?")


    def test_so_sai_o_que_tem_embed(self):
        md = ("![Ranking de x](data:image/png;base64,AA)\n\n**Os valores do ranking**\n\n| a | b |\n| -- | -- |\n\n"
              "![Pontos fora da curva](data:image/png;base64,BB)\n\n**Os valores fora da curva**\n\n| c | d |\n\n"
              "**Fora da curva:** X.")
        so_ranking = render.for_chat(md, kinds=("ranking",))
        self.assertNotIn("Ranking de", so_ranking)
        self.assertIn("Pontos fora da curva", so_ranking)
        self.assertIn("Os valores fora da curva", so_ranking)
        os_dois = render.for_chat(md, kinds=("ranking", "fora"))
        self.assertNotIn("data:image", os_dois)
        self.assertNotIn("| c | d |", os_dois)
        self.assertIn("**Fora da curva:** X.", os_dois)          # a frase fica no chat


class TestLinkAssinado(unittest.TestCase):
    def setUp(self):
        self._key = settings.DOWNLOAD_SIGNING_KEY
        settings.DOWNLOAD_SIGNING_KEY = "chave-de-teste"

    def tearDown(self):
        settings.DOWNLOAD_SIGNING_KEY = self._key

    def _parse(self, caminho):
        partes = urlparse(caminho)
        qs = parse_qs(partes.query)
        return partes.path, int(qs["exp"][0]), qs["sig"][0]

    def test_caminho_relativo_e_valido(self):
        caminho = download_links.build_chart_path("abc123", "onde", 3600)
        path, exp, sig = self._parse(caminho)
        self.assertEqual(path, "/charts/abc123/onde")
        download_links.verify_chart("abc123", "onde", exp, sig)

    def test_trocar_ref_ou_tipo_invalida(self):
        _, exp, sig = self._parse(download_links.build_chart_path("abc123", "onde", 3600))
        for ref, tipo in (("outro", "onde"), ("abc123", "ranking")):
            with self.assertRaises(download_links.LinkInvalid):
                download_links.verify_chart(ref, tipo, exp, sig)

    def test_assinatura_de_grafico_nao_baixa_arquivo(self):
        _, exp, sig = self._parse(download_links.build_chart_path("abc123", "onde", 3600))
        with self.assertRaises(download_links.LinkInvalid):
            download_links.verify("relatorios", "abc123/onde", exp, sig)

    def test_marcador_invisivel_e_lido_pela_function(self):
        linha = render.chart_marker(download_links.build_chart_path("abc123", "onde", 3600))
        self.assertTrue(linha.startswith("[//]: # (tais:grafico /charts/abc123/onde?exp="))
        self.assertEqual(_function().Filter._chart_path(
            {"messages": [{"role": "assistant", "content": "Texto.\n\n" + linha}]}),
            linha.split("tais:grafico ", 1)[1].rstrip(")"))


def _function():
    spec = importlib.util.spec_from_file_location(
        "grafico_interativo", ROOT / "webui" / "functions" / "grafico_interativo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestFunction(unittest.TestCase):
    def test_sem_linha_nao_faz_nada(self):
        filtro = _function().Filter()
        eventos = []

        async def emitir(e):
            eventos.append(e)

        corpo = {"messages": [{"role": "user", "content": "oi"},
                              {"role": "assistant", "content": "Resposta do Indicador."}]}
        self.assertIs(asyncio.run(filtro.outlet(corpo, __event_emitter__=emitir)), corpo)
        self.assertEqual(eventos, [])

    def test_emite_o_html_como_embed(self):
        mod = _function()
        filtro = mod.Filter()
        eventos = []

        async def emitir(e):
            eventos.append(e)

        async def busca(caminho):
            return f"<html>{caminho}</html>"

        filtro._fetch = busca
        corpo = {"messages": [{"role": "assistant",
                               "content": "x\n\n[//]: # (tais:grafico /charts/r1/onde?exp=1&sig=ab)"}]}
        asyncio.run(filtro.outlet(corpo, __event_emitter__=emitir))
        self.assertEqual(eventos, [{"type": "embeds",
                                    "data": {"embeds": ["<html>/charts/r1/onde?exp=1&sig=ab</html>"]}}])

    def test_emite_todos_os_graficos_da_resposta_na_ordem(self):
        mod = _function()
        filtro = mod.Filter()
        eventos = []

        async def emitir(e):
            eventos.append(e)

        async def busca(caminho):
            return None if "falha" in caminho else f"<html>{caminho.split('?')[0]}</html>"

        filtro._fetch = busca
        corpo = {"messages": [{"role": "assistant", "content": (
            "x\n\n[//]: # (tais:grafico /charts/r1/ranking?exp=1&sig=a)\n"
            "[//]: # (tais:grafico /charts/r1/falha?exp=1&sig=b)\n"
            "[//]: # (tais:grafico /charts/r1/fora?exp=1&sig=c)")}]}
        asyncio.run(filtro.outlet(corpo, __event_emitter__=emitir))
        # O que falhou não derruba os outros.
        self.assertEqual(eventos, [{"type": "embeds", "data": {"embeds": [
            "<html>/charts/r1/ranking</html>", "<html>/charts/r1/fora</html>"]}}])


    def test_so_olha_a_ultima_resposta(self):
        corpo = {"messages": [
            {"role": "assistant", "content": "[//]: # (tais:grafico /charts/velho/onde?exp=1&sig=a)"},
            {"role": "user", "content": "Números para decisão"},
            {"role": "assistant", "content": "Sem gráfico aqui."}]}
        self.assertIsNone(_function().Filter._chart_path(corpo))


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "rota precisa do FastAPI (container)")
class TestRota(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from rag.journey import state as state_mod
        from rag.server.charts import router

        self._key = settings.DOWNLOAD_SIGNING_KEY
        settings.DOWNLOAD_SIGNING_KEY = "chave-de-teste"
        self._load = state_mod.JourneyStore.load
        rows = [{"uf": u["sigla"], "valor": float(100 + i)} for i, u in enumerate(ibge.ufs())]
        serie = [{"ano": a, "valor": float(v)} for a, v in ((2021, 10), (2022, 30), (2023, 45))]
        guardado = {"ref1": {"status": "ok", "exec": {
            "rows": rows, "columns": ["uf", "valor"],
            "sql_final": "SELECT uf, SUM(valor) AS valor FROM t GROUP BY uf"},
            "series": {"exec": {"rows": serie, "columns": ["ano", "valor"],
                                "sql_final": "SELECT ano, SUM(valor) AS valor FROM t GROUP BY ano"}}}}
        state_mod.JourneyStore.load = lambda self, ref: guardado.get(ref)
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        from rag.journey import state as state_mod

        state_mod.JourneyStore.load = self._load
        settings.DOWNLOAD_SIGNING_KEY = self._key

    def test_link_valido_devolve_o_html(self):
        r = self.client.get(download_links.build_chart_path("ref1", "onde", 60))
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertEqual(r.text.count("<path "), 27)

    def test_assinatura_errada_403_vencido_410_sumiu_404(self):
        caminho = download_links.build_chart_path("ref1", "onde", 60)
        self.assertEqual(self.client.get(re.sub(r"sig=\w+", "sig=00", caminho)).status_code, 403)
        self.assertEqual(self.client.get(download_links.build_chart_path("ref1", "onde", -10)).status_code, 410)
        self.assertEqual(self.client.get(download_links.build_chart_path("zzz", "onde", 60)).status_code, 404)
        self.assertEqual(self.client.get(download_links.build_chart_path("ref1", "pizza", 60)).status_code, 404)


    def test_tipos_novos(self):
        r = self.client.get(download_links.build_chart_path("ref1", "regioes", 60))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text.count('class="reg"'), 5)
        # 27 valores em fila, sem ponto fora da curva: o gráfico não existe.
        self.assertEqual(self.client.get(download_links.build_chart_path("ref1", "fora", 60)).status_code, 404)
        self.assertEqual(self.client.get(download_links.build_chart_path("ref1", "distribuicao", 60)).status_code, 200)

    def test_serie_historica_guardada_pela_politica(self):
        r = self.client.get(download_links.build_chart_path("ref1", "serie", 60))
        self.assertEqual(r.status_code, 200)
        self.assertIn("Série histórica", r.text)
        self.assertEqual(r.text.count('class="alvo"'), 3)          # os 3 anos, não as 27 UFs


if __name__ == "__main__":
    unittest.main()

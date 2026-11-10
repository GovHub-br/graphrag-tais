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
        guardado = {"ref1": {"status": "ok", "exec": {
            "rows": rows, "columns": ["uf", "valor"],
            "sql_final": "SELECT uf, SUM(valor) AS valor FROM t GROUP BY uf"}}}
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


if __name__ == "__main__":
    unittest.main()

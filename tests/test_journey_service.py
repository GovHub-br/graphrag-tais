"""Jornada do usuário (ADR-0020) ponta a ponta, com dublês no lugar do mundo.

O motor, o executor, o explorador, o LLM da frase, o catálogo e o MinIO são
substituídos por dublês. O que se verifica é a ORQUESTRAÇÃO: o que cada trilha
mostra, o que ela nunca mostra, quando ela recalcula e quais botões vêm depois —
e que os gráficos reais saem (PNG de verdade, desenhado pelo matplotlib).

Precisa do Haystack e do matplotlib, que só existem dentro do container; no
host o módulo é pulado em vez de quebrar a suíte:

    docker exec -w /app -e PYTHONPATH=/app:/app/src hayhooks \\
        python -m unittest tests.test_journey_service
"""
from __future__ import annotations

import base64
import json
import re
import unittest

try:  # pragma: no cover - depende do ambiente
    from rag.components.charts import infographics
    from rag.journey import facts as facts_mod
    from rag.journey import render, tracks
    from rag.journey.catalog_probe import CatalogIndex, complementary, policy_stages
    from rag.journey.human_scale import references, scale_for
    from rag.journey.marker import Marker, parse
    from rag.journey.service import (JourneyService, calculation_lines, kpis, parse_phrase,
                                     planned_text, region_table, series_questions)
    from rag.journey.state import JourneyStore

    OK = True
except ImportError:  # pragma: no cover
    OK = False

_PNG = re.compile(r"!\[[^\]]*\]\(data:image/png;base64,([A-Za-z0-9+/=]+)\)")

UF_ROWS = [{"uf": s, "total": v} for s, v in
           [("SP", 300.0), ("BA", 120.0), ("RJ", 100.0), ("MG", 90.0), ("PE", 60.0),
            ("RS", 50.0), ("CE", 40.0), ("PA", 30.0), ("AM", 20.0), ("DF", 10.0)]]
UF_SQL = "SELECT uf, SUM(valor_contratado) AS total FROM ouro.far GROUP BY uf"
ANSWER = {
    "status": "sucesso", "explanation": "Somo o valor contratado por UF.", "sql": UF_SQL,
    "refusal": "", "ir": {}, "joins": [],
    "references": [{"base": "ouro", "table": "ouro.far", "description": "FAR por UF",
                    "used": True, "columns": [{"name": "uf", "type": "text"},
                                              {"name": "valor_contratado", "type": "numeric"}]}],
}


class FakeMemory:
    """Redis de mentira: mesma interface que o `JourneyStore` usa."""

    def __init__(self):
        self.data = {}

    def ping(self):
        return True

    def set(self, k, v, ex=None):
        self.data[k] = v

    def get(self, k):
        return self.data.get(k)


class FakePublisher:
    def __init__(self):
        self.csv_calls = 0
        self.report_calls = 0
        self.last_report = ""

    def csv(self, columns, rows, question):
        self.csv_calls += 1
        return "http://rag/files/relatorios/r.csv?sig=x"

    def report(self, markdown, question):
        self.report_calls += 1
        self.last_report = markdown
        return f"http://rag/files/relatorios/r{self.report_calls}.docx?sig=x"


def _catalog():
    return CatalogIndex.from_rows(
        tables=[{"schema": "Cidades.cidades.ouro", "name": "far", "description": "execução financeira"},
                {"schema": "Cidades.cidades.ouro", "name": "ogu", "description": "dotação orçamentária"},
                {"schema": "Cidades.cidades.ouro", "name": "marco_portaria", "description": ""}],
        columns=[{"schema": "Cidades.cidades.ouro", "table": "perfil", "name": "beneficiario_sexo"},
                 {"schema": "Cidades.cidades.ouro", "table": "obra", "name": "situacao_trabalho_social"}],
    )


def _service(rows=UF_ROWS, sql=UF_SQL, answer=ANSWER, phrase=None, series_rows=None,
             explore=None, exec_ok=True):
    calls = {"motor": [], "exec": 0, "explore": 0}

    def motor(q):
        calls["motor"].append(q)
        if series_rows is not None and "evolu" in q:
            return ({**answer, "sql": "SELECT ano, SUM(valor_contratado) AS total FROM t GROUP BY ano"}, [])
        return answer, []

    def executor(s, q, ctx):
        calls["exec"] += 1
        if series_rows is not None and "ano" in s:
            return {"rows": series_rows, "columns": ["ano", "total"], "sql_final": s, "exec_ok": True}
        return {"rows": rows, "columns": list(rows[0].keys()) if rows else [], "sql_final": s,
                "exec_ok": exec_ok, "error": "" if exec_ok else "relation does not exist"}

    def explorer(q):
        calls["explore"] += 1
        return explore or ("panorama do catálogo", {"tables": [
            {"table": "ouro.fnhis", "questions": ["Qual o repasse por UF?", "Quantos termos?"]}]})

    def phrase_fn(q, numero, legenda, digest):
        if phrase is not None:
            return phrase
        return json.dumps({"frase": f"Foram contratados {numero} no FAR.",
                           "legenda": "valor contratado no FAR"})

    store = JourneyStore(ttl=60, client=FakeMemory())
    pub = FakePublisher()
    svc = JourneyService(motor=motor, executor=executor, explorer=explorer, store=store,
                         phrase=phrase_fn, catalog=_catalog, publisher=pub)
    return svc, calls, pub


def _pngs(markdown):
    return [base64.b64decode(m) for m in _PNG.findall(markdown)]


@unittest.skipUnless(OK, "precisa do container (Haystack + matplotlib)")
class TestPopulacaoGeral(unittest.TestCase):
    def test_resposta_padrao(self):
        svc, calls, _ = _service()
        reply = svc.answer("Qual o valor contratado por UF no FAR?")
        md = reply.markdown
        self.assertTrue(md.startswith("Foram contratados R$ 820 no FAR."))
        imagens = _pngs(md)
        self.assertEqual(len(imagens), 3)  # destaque, pictograma, mapa
        self.assertTrue(all(i.startswith(b"\x89PNG") for i in imagens))
        self.assertIn("De onde veio", md)
        self.assertNotIn("SELECT", md)        # População geral não vê SQL
        self.assertNotIn("dados abertos", md)  # portal não configurado → oculto
        self.assertEqual(reply.marker.trilha, "populacao")
        self.assertEqual(reply.follow_ups, tracks.buttons(tracks.suggest_order("x")))
        self.assertEqual(parse(reply.text()).ref, reply.marker.ref)

    def test_frase_sem_o_numero_cai_na_deterministica(self):
        """Número na frase que não bate com o cartão é pior que estilo."""
        svc, _, _ = _service(phrase=json.dumps({"frase": "Foram contratados cerca de 800 reais.",
                                                "legenda": "valor contratado"}))
        md = svc.answer("quanto foi contratado por UF?").markdown
        self.assertTrue(md.startswith("Ao todo, valor contratado soma R$ 820."))

    def test_legenda_com_numero_e_descartada(self):
        svc, _, _ = _service(phrase=json.dumps({"frase": "São R$ 820.",
                                                "legenda": "valor de 2024 contratado"}))
        reply = svc.answer("quanto foi contratado por UF?")
        state = svc.store.load(reply.marker.ref)
        self.assertEqual(state["label"], "")

    def test_frase_que_atribui_o_total_a_quem_lidera_cai_na_deterministica(self):
        """O número bate com o cartão, mas a frase diz que é de um estado só."""
        svc, _, _ = _service(phrase=json.dumps({
            "frase": "O estado com mais valor contratado tem R$ 820. A Bahia lidera.",
            "legenda": "valor contratado no FAR"}))
        md = svc.answer("Quais estados têm mais valor contratado? Quero o ranking.").markdown
        self.assertTrue(md.startswith("Ao todo, valor contratado no FAR soma R$ 820. "
                                      "Quem lidera é São Paulo, com R$ 300."), md[:160])

    def test_frase_com_total_e_lider_separados_passa(self):
        svc, _, _ = _service(phrase=json.dumps({
            "frase": "Ao todo, foram contratados R$ 820 no FAR. São Paulo lidera, com R$ 300.",
            "legenda": "valor contratado no FAR"}))
        md = svc.answer("Quais estados lideram?").markdown
        self.assertTrue(md.startswith("Ao todo, foram contratados R$ 820 no FAR."))

    def test_link_de_dados_abertos_quando_configurado(self):
        svc, _, _ = _service()
        svc.settings = type("S", (), {**{k: getattr(svc.settings, k) for k in
                                         ("OM_SERVICE", "OM_DATABASE", "MAX_EXEC_ROWS",
                                          "JOURNEY_POLICY_SERIES")},
                                      "JOURNEY_OPEN_DATA_URL": "https://dados.exemplo.gov.br/far"})()
        md = svc.answer("quanto foi contratado por UF?").markdown
        self.assertIn("https://dados.exemplo.gov.br/far", md)

    def test_sem_resultado_nao_tem_botoes(self):
        svc, _, _ = _service(exec_ok=False, rows=[])
        reply = svc.answer("quanto foi contratado?")
        self.assertIn("Não consegui chegar a um número", reply.markdown)
        self.assertEqual(reply.follow_ups, [])
        self.assertEqual(svc.follow_ups(reply.marker), [])

    def test_recusa_do_motor(self):
        svc, calls, _ = _service(answer={**ANSWER, "refusal": "pii", "sql": "",
                                         "explanation": "Não posso identificar pessoas."})
        reply = svc.answer("quanto recebeu o fulano?")
        self.assertEqual(reply.markdown, "Não posso identificar pessoas.")
        self.assertEqual(calls["exec"], 0)

    def test_assunto_vai_para_o_explorar_e_sugere_perguntas(self):
        svc, calls, _ = _service()
        reply = svc.answer("O que vocês têm sobre o FNHIS?")
        self.assertEqual(calls["explore"], 1)
        self.assertEqual(calls["motor"], [])
        self.assertEqual(reply.marker.rota, "assunto")
        self.assertEqual(reply.follow_ups, ["Qual o repasse por UF?", "Quantos termos?"])
        self.assertEqual(svc.follow_ups(reply.marker), reply.follow_ups)


@unittest.skipUnless(OK, "precisa do container (Haystack + matplotlib)")
class TestTrilhas(unittest.TestCase):
    def _primeira(self, **kw):
        svc, calls, pub = _service(**kw)
        reply = svc.answer("Qual o valor contratado por UF no FAR?")
        return svc, calls, pub, reply

    def test_tecnica_reusa_o_resultado_sem_novo_sql(self):
        svc, calls, pub, r = self._primeira()
        t = svc.track(tracks.TECNICA, r.marker, "")
        self.assertEqual(len(calls["motor"]), 1)   # nada de recalcular
        self.assertEqual(calls["exec"], 1)
        self.assertIn("SQL executado", t.markdown)
        self.assertIn(UF_SQL, t.markdown)
        self.assertIn("Baixar o resultado completo (CSV)", t.markdown)
        self.assertIn("Mostrando 10 de 10 linhas", t.markdown)
        self.assertGreaterEqual(len(_pngs(t.markdown)), 3)  # linhagem, histograma, cobertura
        self.assertEqual(t.marker.vistas, ["tecnica"])
        self.assertNotIn(tracks.TECNICA.button, t.follow_ups)

    def test_csv_e_relatorio_gerados_uma_vez(self):
        svc, _, pub, r = self._primeira()
        t = svc.track(tracks.TECNICA, r.marker, "")
        svc.track(tracks.BAIXAR, t.marker, "")
        self.assertEqual(pub.csv_calls, 1)
        b = svc.track(tracks.BAIXAR, t.marker, "")
        self.assertIn("r1.docx", b.markdown)
        self.assertEqual((pub.csv_calls, pub.report_calls), (1, 1))

    def test_relatorio_e_o_que_a_pessoa_viu(self):
        """Relatório do "Números para decisão" traz a resposta e o gestor — não
        o SQL da trilha técnica que ela nunca abriu."""
        svc, _, pub, r = self._primeira()
        g = svc.track(tracks.GESTOR, r.marker, "")
        md = pub.last_report
        self.assertTrue(md.startswith("## Resposta\n\nForam contratados R$ 820 no FAR."))
        self.assertIn("## Números para decisão", md)
        self.assertIn("Comparação com regiões parecidas", md)
        self.assertNotIn("SELECT", md)
        self.assertNotIn("/files/", md)       # link de download não vai para dentro do .docx
        self.assertNotIn("[//]: #", md)
        self.assertGreaterEqual(len(_pngs(md)), 4)  # destaque, pictograma, mapa, cartões
        # Abriu outra trilha → o "Baixar" traz um relatório novo, com ela no fim.
        p = svc.track(tracks.TECNICA, g.marker, "")
        b = svc.track(tracks.BAIXAR, p.marker, "")
        self.assertEqual(pub.report_calls, 2)
        self.assertIn("r2.docx", b.markdown)
        self.assertIn("Traz o que você abriu nesta conversa: a resposta, Números para decisão, "
                      "Como esse dado foi calculado.", b.markdown)
        # Cada arquivo num parágrafo só dele: é o que o CSS transforma em botão.
        self.assertRegex(b.markdown, r"\n\n\[Relatório \(\.docx\)\]\([^)]+\)\n\n")
        md = pub.last_report
        self.assertLess(md.index("## Números para decisão"), md.index("## Como esse dado foi calculado"))
        self.assertIn(UF_SQL, md)
        # Baixar de novo sem trilha nova reusa o mesmo arquivo.
        svc.track(tracks.BAIXAR, b.marker, "")
        self.assertEqual(pub.report_calls, 2)

    def test_relatorio_sem_html_do_details(self):
        md = render.report_markdown([("politica", "## Contexto\n\n<details>\n<summary>SQL tentado</summary>"
                                                  "\n\n```sql\nSELECT 1\n```\n</details>")])
        self.assertIn("### SQL tentado", md)
        self.assertNotIn("<details>", md)
        self.assertNotIn("</details>", md)

    def test_gestor(self):
        svc, _, _, r = self._primeira()
        g = svc.track(tracks.GESTOR, r.marker, "").markdown
        self.assertIn("Números para decisão", g)
        self.assertIn("Comparação com regiões parecidas", g)
        self.assertIn("previsto", g)   # lacuna de executado × previsto dita, não inventada
        self.assertIn("ogu", g)        # e o catálogo indica onde procurar
        self.assertIn("r1.docx", g)

    def test_politica_com_serie_derivada(self):
        serie = [{"ano": a, "total": v} for a, v in [(2021, 10.0), (2022, 30.0), (2023, 45.0)]]
        svc, calls, _, r = self._primeira(series_rows=serie)
        p = svc.track(tracks.POLITICA, r.marker, "").markdown
        self.assertEqual(len(calls["motor"]), 2)
        self.assertTrue(calls["motor"][1].startswith("Qual a evolução por ano de valor contratado no FAR"))
        self.assertIn("De R$ 10 em 2021 para R$ 45 em 2023 (+350% no período).", p)
        self.assertIn("SQL da série histórica", p)
        self.assertIn("Raça/cor", p)
        self.assertIn("O que o sistema não tem", p)

    def test_politica_quando_a_serie_nao_roda(self):
        svc, calls, _, r = self._primeira()
        # Sem `series_rows`, o dublê devolve o mesmo resultado por UF: não é série.
        p = svc.track(tracks.POLITICA, r.marker, "").markdown
        self.assertIn("Não encontrei", p)

    def test_historia_em_quatro_passos(self):
        svc, _, _, r = self._primeira()
        h = svc.track(tracks.HISTORIA, r.marker, "").markdown
        for passo in ("1 · Quanto?", "2 · O que significa?", "3 · Onde?", "4 · Para quem?"):
            self.assertIn(passo, h)
        self.assertIn("Gênero", h)  # catálogo do dublê tem beneficiario_sexo

    def test_estado_expirado_recalcula_e_avisa(self):
        svc, calls, _ = _service()
        t = svc.track(tracks.TECNICA, Marker(ref="sumiu", rota="numero"), "quanto por UF?")
        self.assertIn("expirou", t.markdown)
        self.assertEqual(calls["motor"], ["quanto por UF?"])

    def test_clique_sem_pergunta_nenhuma(self):
        svc, calls, _ = _service()
        t = svc.track(tracks.GESTOR, None, "")
        self.assertIn("Faça uma pergunta primeiro", t.markdown)
        self.assertEqual(calls["motor"], [])

    def test_botoes_depois_de_cada_trilha(self):
        svc, _, _, r = self._primeira()
        t1 = svc.track(tracks.GESTOR, r.marker, "")
        t2 = svc.track(tracks.HISTORIA, t1.marker, "")
        self.assertEqual(t2.marker.vistas, ["gestor", "historia"])
        self.assertEqual(svc.follow_ups(t2.marker),
                         [tracks.TECNICA.button, tracks.POLITICA.button, tracks.BAIXAR.button])


@unittest.skipUnless(OK, "precisa do container (Haystack + matplotlib)")
class TestPecas(unittest.TestCase):
    def _facts(self, rows=UF_ROWS, sql=UF_SQL):
        return facts_mod.build(rows, sql=sql)

    def test_escala_humana_dinheiro_por_habitante(self):
        f = self._facts([{"uf": "BR", "total": 203_080_756.0 * 2}],
                        "SELECT SUM(valor_pago) AS total")
        self.assertEqual(scale_for(f).text, "Dá R$ 2 por habitante do Brasil.")

    def test_escala_humana_contagem_usa_a_tabela_fixa(self):
        refs = {r.id: r for r in references("/app/config/escala_humana.yml")}
        self.assertIn("maracana", refs)
        f = self._facts([{"n": 78_838 * 3}], "SELECT COUNT(*) AS n")
        self.assertEqual(scale_for(f, "/app/config/escala_humana.yml").text,
                         "É como 3 Maracanãs lotados.")

    def test_taxa_nao_tem_escala_humana(self):
        f = self._facts([{"uf": "SP", "taxa": 0.2}, {"uf": "RJ", "taxa": 0.4}])
        self.assertIsNone(scale_for(f))

    def test_calculo_lido_do_sql(self):
        linhas = calculation_lines("SELECT uf, COUNT(DISTINCT apf) AS n FROM t "
                                   "WHERE situacao = 'ok' GROUP BY uf ORDER BY n")
        self.assertEqual(linhas, ["COUNT(DISTINCT apf)", "agrupado por uf", "filtro: situacao = 'ok'"])

    def test_kpis_e_regioes(self):
        f = self._facts()
        cartoes = kpis(f)
        self.assertEqual(cartoes[0][0], "R$ 820")
        self.assertEqual(cartoes[1][0], "SP")
        self.assertIn("Sudeste", region_table(f))

    def test_executado_vs_previsto_quando_existe(self):
        rows = [{"ano": 2023, "valor_previsto": 100.0, "valor_executado": 80.0},
                {"ano": 2024, "valor_previsto": 100.0, "valor_executado": 90.0}]
        f = facts_mod.build(rows)
        self.assertIn("**85%** do previsto foi executado", planned_text(f, None, []))

    def test_catalogo_por_token_nao_por_substring(self):
        etapas = {s.name: s for s in policy_stages(_catalog(), ["ouro.far"])}
        self.assertEqual(etapas["Execução"].state, "usado")
        self.assertEqual(etapas["Orçamento"].state, "catalogo")
        self.assertEqual(etapas["Lei"].state, "catalogo")
        self.assertEqual(etapas["Resultado"].state, "lacuna")
        comp = {i.name: i for i in complementary(_catalog())}
        self.assertTrue(comp["Gênero"].found)
        self.assertFalse(comp["Emprego"].found)   # "trabalho social" não é emprego
        self.assertFalse(comp["Raça/cor"].found)  # "opeRACAo" não é raça

    def test_frase_do_llm(self):
        self.assertEqual(parse_phrase('{"frase": "São R$ 1 mi.", "legenda": "valor pago."}'),
                         ("São R$ 1 mi.", "valor pago"))
        self.assertEqual(parse_phrase("texto solto"), ("texto solto", ""))

    def test_perguntas_da_serie(self):
        f = self._facts()
        f.label = "valor contratado no FAR"
        self.assertEqual(series_questions("Quanto por UF?", f),
                         ["Qual a evolução por ano de valor contratado no FAR?",
                          "Quanto por UF. Mostre a evolução ano a ano."])


@unittest.skipUnless(OK, "precisa do container (Haystack + matplotlib)")
class TestGraficos(unittest.TestCase):
    """Cada gráfico desenha com dado bom e devolve None sem dado."""

    def setUp(self):
        self.fmt = lambda v: f"{v:.0f}"
        self.items = [(r["uf"], r["total"]) for r in UF_ROWS]

    def assertPng(self, png):
        self.assertIsNotNone(png)
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_todos_desenham(self):
        g = infographics
        self.assertPng(g.highlight("R$ 820", "valor contratado", "Dá R$ 4 por habitante."))
        self.assertPng(g.pictogram(4, "SP concentra 4 em cada 10."))
        self.assertPng(g.uf_map(dict(self.items), self.fmt, title="Onde?"))
        self.assertPng(g.comparison(self.items, 82.0, "Média", self.fmt))
        self.assertPng(g.kpi_cards([("R$ 820", "Total", ""), ("SP", "Maior", "37%")]))
        self.assertPng(g.ranking(self.items, self.fmt))
        self.assertPng(g.alerts(self.items, ["SP"], self.fmt))
        self.assertPng(g.alerts([("2021", 1), ("2022", 2), ("2023", 9)], ["2023"], self.fmt,
                                temporal=True))
        self.assertPng(g.histogram([v for _, v in self.items], self.fmt))
        self.assertPng(g.coverage({"uf": 1.0, "total": 0.8}))
        self.assertPng(g.lineage([("Fonte", ["OpenMetadata"]), ("Cálculo", ["SUM(x)"])]))
        self.assertPng(g.policy_journey([("Lei", "lacuna", "-"), ("Execução", "usado", "far")]))
        self.assertPng(g.regions_stacked({"Sudeste": {"SP": 3.0, "RJ": 1.0}, "Sul": {"RS": 1.0}},
                                         self.fmt))

    def test_sem_dado_sem_grafico(self):
        g = infographics
        self.assertIsNone(g.highlight("", "x"))
        self.assertIsNone(g.uf_map({}, self.fmt))
        self.assertIsNone(g.comparison([("SP", 1.0)], 1.0, "Média", self.fmt))
        self.assertIsNone(g.kpi_cards([]))
        self.assertIsNone(g.ranking([("SP", 1.0)], self.fmt))
        self.assertIsNone(g.histogram([1.0, 2.0], self.fmt))
        self.assertIsNone(g.coverage({}))
        self.assertIsNone(g.regions_stacked({}, self.fmt))


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- #
# Conferência de 10/11: fonte do SQL que rodou, recorte do programa e "Contexto
# da política" restrito ao produto.
# --------------------------------------------------------------------------- #
def _product_catalog():
    return CatalogIndex.from_rows(
        tables=[
            {"schema": "Cidades.cidades.ouro", "name": "ouro_pro_moradia_panorama",
             # Como no MCid: o Data Product junta programas ("Reloginho MCMV");
             # quem diz o programa é a descrição da tabela.
             "description": "Grandes números do Pró-Moradia por UF: contratos, UH e valores. "
                            "Uma linha por UF.", "product": "Reloginho MCMV"},
            {"schema": "Cidades.cidades.ouro", "name": "ouro_pro_moradia_portaria",
             "description": "portaria de seleção", "product": "Reloginho MCMV"},
            {"schema": "Cidades.cidades.ouro", "name": "ouro_far_ficha",
             "description": "Uma linha por empreendimento FAR, com localização e execução.",
             "product": "Empreendimento FAR"},
            {"schema": "Cidades.cidades.ouro", "name": "lei", "description": "lei geral",
             "product": "Empreendimento FAR"},
            {"schema": "Cidades.cidades.ouro", "name": "ogu", "description": "dotação orçamentária",
             "product": "Empreendimento FAR"},
        ],
        columns=[],
    )


PM_SQL = ("SELECT uf, SUM(valor_contratado) AS total FROM ouro.ouro_pro_moradia_panorama "
          "GROUP BY uf")
PM_ANSWER = {**ANSWER, "sql": PM_SQL, "references": [
    # O LLM marcou uma tabela prata; o SQL que rodou lê a ouro.
    {"base": "prata", "table": "prata.prata_pro_moradia", "used": True, "columns": []},
    {"base": "ouro", "table": "ouro.ouro_pro_moradia_panorama", "used": False, "columns": []},
]}


@unittest.skipUnless(OK, "precisa do Haystack (rode no container)")
class FonteEProgramaTest(unittest.TestCase):

    def test_used_tables_come_from_the_executed_sql(self):
        from rag.journey.service import _used_tables

        state = {"answer": PM_ANSWER, "exec": {"exec_ok": True, "sql_final": PM_SQL}}
        self.assertEqual(_used_tables(state), ["ouro.ouro_pro_moradia_panorama"])

    def test_used_tables_fall_back_to_the_llm_mark_without_execution(self):
        from rag.journey.service import _used_tables

        state = {"answer": PM_ANSWER, "exec": {"exec_ok": False, "sql_final": PM_SQL}}
        self.assertEqual(_used_tables(state), ["prata.prata_pro_moradia"])
        # SQL que não parseia também cai na marcação.
        state = {"answer": PM_ANSWER, "exec": {"exec_ok": True, "sql_final": "SELEC lixo"}}
        self.assertEqual(_used_tables(state), ["prata.prata_pro_moradia"])

    def test_source_line_matches_the_sql_shown(self):
        svc, _, _ = _service(answer=PM_ANSWER, sql=PM_SQL)
        md = svc.answer("Qual o total contratado por UF?").markdown
        self.assertIn("`ouro.ouro_pro_moradia_panorama`", md)
        self.assertNotIn("prata.prata_pro_moradia", md)

    def test_phrase_says_the_program_when_the_question_does_not(self):
        svc, _, _ = _service(answer=PM_ANSWER, sql=PM_SQL)
        svc.catalog = _product_catalog
        md = svc.answer("Qual o total contratado por UF?").markdown
        self.assertIn("*Recorte: Grandes números do Pró-Moradia por UF "
                      "(`ouro.ouro_pro_moradia_panorama`).", md)
        self.assertNotIn("Reloginho", md)

    def test_phrase_untouched_when_the_question_names_the_program(self):
        from rag.journey.catalog_probe import scope_of
        from rag.journey.service import with_scope

        idx = _product_catalog()
        pm = scope_of(idx, ["ouro.ouro_pro_moradia_panorama"])
        far = scope_of(idx, ["ouro.ouro_far_ficha"])
        frase = "Foram contratados R$ 5,8 bi."
        # Pelo nome da tabela ("pro moradia"), com ou sem acento.
        for pergunta in ("Quanto o Pró-Moradia contratou por UF?",
                         "quanto o pro moradia contratou por uf?"):
            self.assertEqual(with_scope(frase, pergunta, pm), frase)
        # Pela sigla da descrição, e pelo rótulo do produto sem a palavra comum.
        self.assertEqual(with_scope(frase, "Quanto o FAR contratou?", far), frase)
        self.assertEqual(with_scope(frase, "e o far?", far, ["Empreendimento FAR"],
                                    stopwords="empreendimento"), frase)
        # A frase do LLM já nomeia o programa.
        self.assertEqual(with_scope("O Pró-Moradia contratou R$ 5,8 bi.", "total por UF?", pm),
                         "O Pró-Moradia contratou R$ 5,8 bi.")
        # Sem catálogo (ou tabela sem descrição nem produto): nada muda.
        self.assertEqual(with_scope(frase, "total por UF?", None), frase)
        self.assertIsNone(scope_of(None, ["ouro.x"]))

    def test_scope_text_is_the_first_clause_of_the_description(self):
        from rag.journey.catalog_probe import scope_of

        idx = _product_catalog()
        self.assertEqual(scope_of(idx, ["ouro.ouro_far_ficha"]).text,
                         "Uma linha por empreendimento FAR")
        # Sem descrição, fica o rótulo do produto.
        idx.descriptions.pop("ouro.ogu")
        self.assertEqual(scope_of(idx, ["ouro.ogu"]).text, "Empreendimento FAR")

    def test_policy_stages_stay_in_the_product_of_the_answer(self):
        etapas = {s.name: s for s in policy_stages(_product_catalog(),
                                                  ["ouro.ouro_pro_moradia_panorama"])}
        # "Lei" vinha da tabela `lei` do FAR (nome mais curto do catálogo); agora
        # só o que é do Pró-Moradia entra.
        self.assertEqual(etapas["Lei"].tables, ["ouro.ouro_pro_moradia_portaria"])
        self.assertEqual(etapas["Orçamento"].state, "lacuna")

    def test_policy_stages_without_products_scan_the_whole_catalog(self):
        etapas = {s.name: s for s in policy_stages(_catalog(), ["ouro.far"])}
        self.assertEqual(etapas["Orçamento"].tables, ["ouro.ogu"])

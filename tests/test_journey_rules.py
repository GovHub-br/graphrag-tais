"""Regras da jornada do usuário (ADR-0020) — o que é contrato, caso a caso.

O botão é texto: o OpenWebUI mostra o rótulo e devolve O MESMO texto como
mensagem do usuário quando a pessoa clica. Se o casamento falhar, o clique vira
uma "pergunta nova" e o motor gera SQL para "Números para decisão". Se casar
demais, uma pergunta de verdade ("como esse dado foi calculado em 2023?") vira
clique. Os dois lados estão aqui.

Roda no host, sem Haystack e sem serviço: os módulos testados são puros.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag.journey import fmt, ibge, openwebui_tasks, routing, tracks, units  # noqa: E402
from rag.journey.facts import build  # noqa: E402
from rag.journey.marker import Marker, last_in_messages, parse  # noqa: E402


class TestClique(unittest.TestCase):
    def test_rotulo_exato_com_emoji(self):
        for track in tracks.ALL_TRACKS:
            self.assertIs(tracks.match_track(track.button), track, track.button)

    def test_rotulo_antigo_com_emoji_ainda_abre_a_trilha(self):
        """Conversas de antes de 01/11 têm os rótulos com emoji no histórico."""
        self.assertIs(tracks.match_track("🔧 Como esse dado foi calculado?"), tracks.TECNICA)
        self.assertIs(tracks.match_track("📥 Baixar dados e relatório"), tracks.BAIXAR)

    def test_rotulos_sem_emoji(self):
        from rag.journey.text import strip_emoji

        for track in tracks.ALL_TRACKS:
            self.assertEqual(strip_emoji(track.button), track.button, track.button)

    def test_limpeza_de_emoji_preserva_texto_e_codigo(self):
        from rag.journey.text import strip_emoji

        self.assertEqual(strip_emoji("| `apf` | text · 🔑 PK |"), "| `apf` | text · PK |")
        self.assertEqual(strip_emoji("📥 **[Baixar](u)**"), "**[Baixar](u)**")
        self.assertEqual(strip_emoji("> ⚠️ O resultado"), "> O resultado")
        self.assertEqual(strip_emoji("de jan → jun"), "de jan → jun")
        self.assertEqual(strip_emoji("```sql\n    SELECT a,  b\n```"), "```sql\n    SELECT a,  b\n```")

    def test_digitado_sem_emoji_e_sem_acento(self):
        self.assertIs(tracks.match_track("como esse dado foi calculado"), tracks.TECNICA)
        self.assertIs(tracks.match_track("Numeros para decisao"), tracks.GESTOR)
        self.assertIs(tracks.match_track("  contexto da política!  "), tracks.POLITICA)

    def test_alias(self):
        self.assertIs(tracks.match_track("mostre o SQL"), tracks.TECNICA)
        self.assertIs(tracks.match_track("exportar"), tracks.BAIXAR)

    def test_pergunta_que_contem_o_rotulo_nao_e_clique(self):
        """Casamento por igualdade, nunca por 'contém'."""
        for pergunta in (
            "Como esse dado foi calculado para SP em 2023?",
            "Quero números para decisão sobre o FAR no Nordeste",
            "Qual o contexto da política de habitação no Pará?",
        ):
            self.assertIsNone(tracks.match_track(pergunta), pergunta)

    def test_vazio(self):
        self.assertIsNone(tracks.match_track(""))
        self.assertIsNone(tracks.match_track("🔧"))


class TestPalpite(unittest.TestCase):
    def test_todas_as_trilhas_aparecem_sem_pergunta_especifica(self):
        ordem = [t.id for t in tracks.suggest_order("quanto foi contratado por UF?")]
        self.assertEqual(ordem, ["tecnica", "politica", "gestor", "historia", "baixar"])

    def test_palpite_reordena_as_personas(self):
        self.assertEqual(tracks.suggest_order("qual o SQL e a tabela usada?")[0], tracks.TECNICA)
        self.assertEqual(tracks.suggest_order("ranking das UFs que bateram a meta")[0], tracks.GESTOR)
        self.assertEqual(tracks.suggest_order("evolução histórica do programa")[0], tracks.POLITICA)

    def test_historia_e_baixar_sempre_depois_das_personas(self):
        ordem = tracks.suggest_order("ranking por meta")
        self.assertEqual([t.id for t in ordem[-2:]], ["historia", "baixar"])

    def test_trilha_vista_sai_da_lista(self):
        ids = [t.id for t in tracks.suggest_order("x", done=["tecnica", "historia"])]
        self.assertNotIn("tecnica", ids)
        self.assertNotIn("historia", ids)
        self.assertEqual(len(ids), 3)


class TestRoteamento(unittest.TestCase):
    NUMERO = [
        "Qual o valor total contratado por UF no FAR?",
        "Quantos empreendimentos existem em cada estado?",
        "Como evoluiu o estoque de empregos na construção civil?",
        "ranking dos municípios com mais unidades entregues",
        "valor desembolsado em 2024",
        # Cita "dados"/"base", mas é pergunta de número.
        "Quantos dados de empreendimentos a base tem por UF?",
    ]
    ASSUNTO = [
        "O que vocês têm sobre o FNHIS?",
        "Quais bases de dados vocês têm?",
        "Me fale sobre o programa Pró-Moradia",
        "Por onde começo para estudar habitação rural?",
        "o que existe sobre regularidade dos municípios",
    ]

    def test_numero(self):
        for q in self.NUMERO:
            self.assertEqual(routing.classify(q), routing.NUMERO, q)

    def test_assunto(self):
        for q in self.ASSUNTO:
            self.assertEqual(routing.classify(q), routing.ASSUNTO, q)

    def test_default_e_numero(self):
        self.assertEqual(routing.classify("FAR no Nordeste"), routing.NUMERO)
        self.assertEqual(routing.classify(""), routing.NUMERO)


class TestMarcador(unittest.TestCase):
    def test_ida_e_volta(self):
        m = Marker(ref="6f1c2a9b0d1e", rota="numero", trilha="gestor", vistas=["tecnica", "gestor"])
        texto = "resposta qualquer\n\n" + m.render()
        self.assertEqual(parse(texto), m)

    def test_e_comentario_markdown(self):
        """`[//]: # (...)` é definição de link sem uso: o Markdown não renderiza."""
        self.assertTrue(Marker(ref="a").render().startswith("[//]: # (tais:v1 "))

    def test_vale_o_ultimo(self):
        texto = Marker(ref="velho").render() + "\n...\n" + Marker(ref="novo").render()
        self.assertEqual(parse(texto).ref, "novo")

    def test_historico_do_openwebui(self):
        msgs = [
            {"role": "user", "content": "quanto?"},
            {"role": "assistant", "content": "x\n\n" + Marker(ref="r1", rota="numero").render()},
            {"role": "user", "content": "Números para decisão"},
            {"role": "assistant", "content": [{"type": "text", "text": Marker(ref="r1", trilha="gestor").render()}]},
            {"role": "user", "content": "Contar a história"},
        ]
        self.assertEqual(last_in_messages(msgs).trilha, "gestor")

    def test_sem_marcador(self):
        self.assertIsNone(parse("texto sem estado"))
        self.assertIsNone(last_in_messages([{"role": "assistant", "content": "oi"}]))


_FOLLOW_UP = """### Task:
Suggest 3-5 relevant follow-up questions ...
JSON format: { "follow_ups": ["Question 1?", "Question 2?", "Question 3?"] }
### Chat History:
<chat_history>
USER: Qual o valor contratado por UF no FAR?
ASSISTANT: O valor é R$ 61 bi.

%s
</chat_history>"""

_TITLE = """### Task:
Generate a concise title summarizing the chat history.
JSON format: { "title": "your concise title here" }
### Chat History:
<chat_history>
USER: Qual o valor total contratado por unidade da federação no programa FAR em 2024?
ASSISTANT: ...
</chat_history>"""


class TestTarefasDoOpenWebUI(unittest.TestCase):
    def test_detecta_cada_tarefa(self):
        self.assertEqual(openwebui_tasks.detect(_FOLLOW_UP % ""), openwebui_tasks.FOLLOW_UPS)
        self.assertEqual(openwebui_tasks.detect(_TITLE), openwebui_tasks.TITLE)
        self.assertEqual(openwebui_tasks.detect('### Task:\nx\n{ "tags": [] }'), openwebui_tasks.TAGS)
        self.assertEqual(openwebui_tasks.detect("### Task:\nqualquer"), openwebui_tasks.OTHER)

    def test_mensagem_de_verdade_nao_e_tarefa(self):
        self.assertIsNone(openwebui_tasks.detect("Qual o total contratado?"))
        self.assertIsNone(openwebui_tasks.detect("Me explique a ### Task: do sistema"))

    def test_marcador_chega_no_prompt_de_follow_up(self):
        marcador = Marker(ref="abc123", rota="numero", trilha="populacao").render()
        historico = openwebui_tasks.chat_history(_FOLLOW_UP % marcador)
        self.assertEqual(parse(historico).ref, "abc123")

    def test_titulo_sem_llm(self):
        titulo = json.loads(openwebui_tasks.json_title(_TITLE))["title"]
        self.assertTrue(titulo.startswith("Qual o valor total contratado"))
        self.assertLessEqual(len(titulo), 61)
        self.assertTrue(titulo.endswith("…"))

    def test_json_dos_botoes(self):
        botoes = tracks.buttons(tracks.suggest_order("x"))
        self.assertEqual(json.loads(openwebui_tasks.json_follow_ups(botoes))["follow_ups"], botoes)


class TestFormato(unittest.TestCase):
    def test_compacto(self):
        self.assertEqual(fmt.compact(99_827_391_591.68, money=True), "R$ 99,8 bi")
        self.assertEqual(fmt.compact(150e9, money=True), "R$ 150 bi")
        self.assertEqual(fmt.compact(3_100_000), "3,1 mi")
        self.assertEqual(fmt.compact(1000), "1 mil")
        self.assertEqual(fmt.compact(878), "878")
        self.assertEqual(fmt.compact(491.58, money=True), "R$ 491,58")

    def test_completo(self):
        self.assertEqual(fmt.full(1234567.891, money=True), "R$ 1.234.567,89")
        self.assertEqual(fmt.full(27), "27")
        self.assertEqual(fmt.full(None), "—")

    def test_periodo(self):
        self.assertEqual(fmt.period("2026-06"), "jun/2026")
        self.assertEqual(fmt.period("2024-01-01"), "jan/2024")
        self.assertEqual(fmt.period("2023"), "2023")
        self.assertEqual(fmt.period("2024-T1"), "2024-T1")

    def test_percentual_e_nome(self):
        self.assertEqual(fmt.percent(0.2049), "20%")
        self.assertEqual(fmt.humanize("total_valor_contratado"), "valor contratado")


class TestIBGE(unittest.TestCase):
    def test_populacao_censo_2022(self):
        self.assertEqual(ibge.population_brazil(), 203_080_756)
        self.assertEqual(sum(u["populacao_2022"] for u in ibge.ufs()), 203_080_756)
        self.assertEqual(len(ibge.ufs()), 27)

    def test_resolve_sigla_nome_e_codigo(self):
        self.assertEqual(ibge.resolve("SP")["nome"], "São Paulo")
        self.assertEqual(ibge.resolve("sao paulo")["sigla"], "SP")
        self.assertEqual(ibge.resolve(35)["sigla"], "SP")
        self.assertEqual(ibge.resolve("35.0")["sigla"], "SP")
        self.assertIsNone(ibge.resolve("NE"))
        self.assertIsNone(ibge.resolve(None))

    def test_malha_tem_as_27(self):
        malha = json.loads(ibge.geojson_path().read_text(encoding="utf-8"))
        codigos = {f["properties"]["codarea"] for f in malha["features"]}
        self.assertEqual(codigos, {str(u["codigo"]) for u in ibge.ufs()})


def _k(**kinds):
    return kinds


class TestFatos(unittest.TestCase):
    """`facts.build` com `kinds` explícito: roda no host, sem `_infer_kinds`."""

    def test_total_por_uf_em_reais(self):
        rows = [{"uf": "SP", "total": 30.0}, {"uf": "BA", "total": 10.0}, {"uf": "RJ", "total": 20.0}]
        f = build(rows, ["uf", "total"], sql="SELECT uf, SUM(valor_contratado) AS total FROM t GROUP BY uf",
                  kinds=_k(uf="text", total="numeric"))
        self.assertEqual((f.metric, f.dimension, f.dimension_kind), ("total", "uf", "uf"))
        self.assertTrue(f.money)          # dinheiro pela coluna somada, não pelo alias
        self.assertEqual(f.headline_kind, "total")
        self.assertEqual(f.headline, 60.0)
        self.assertEqual(f.uf_values, {"SP": 30.0, "BA": 10.0, "RJ": 20.0})
        self.assertEqual(f.share_of_top(), ("SP", 0.5))

    def test_contagem_nao_e_dinheiro(self):
        rows = [{"uf": "SP", "n": 3}, {"uf": "BA", "n": 1}]
        f = build(rows, sql="SELECT uf, COUNT(DISTINCT valor_id) AS n FROM t",
                  kinds=_k(uf="text", n="numeric"))
        self.assertFalse(f.money)

    def test_media_nao_soma(self):
        rows = [{"uf": "SP", "taxa_media": 0.3}, {"uf": "BA", "taxa_media": 0.5}]
        f = build(rows, kinds=_k(uf="text", taxa_media="numeric"))
        self.assertFalse(f.additive)
        self.assertEqual((f.headline_kind, f.headline_label, f.headline), ("maior", "BA", 0.5))
        self.assertIsNone(f.total())

    def test_ano_e_mes_viram_um_periodo_e_mes_nao_e_metrica(self):
        rows = [{"ano": 2024, "mes": m, "estoque": 100 + m} for m in (1, 2, 3)]
        rows += [{"ano": 2025, "mes": 1, "estoque": 200}]
        f = build(rows, kinds=_k(ano="numeric", mes="numeric", estoque="numeric"))
        self.assertEqual(f.metric, "estoque")
        self.assertEqual(f.dimension_kind, "tempo")
        self.assertEqual([r for r, _ in f.series()], ["2024-01", "2024-02", "2024-03", "2025-01"])
        self.assertEqual((f.headline_kind, f.headline_label, f.headline), ("ultimo", "2025-01", 200))

    def test_regiao_misturada_com_uf_fica_fora_da_soma(self):
        """Tabela "mapa" com linhas de UF e de região: somar tudo dobra o valor."""
        rows = [{"uf": u, "v": 10.0} for u in ("SP", "RJ", "BA", "PE", "AM", "SE")]
        rows += [{"uf": "NE", "v": 50.0}, {"uf": "N", "v": 30.0}, {"uf": "S", "v": 5.0}]
        f = build(rows, kinds=_k(uf="text", v="numeric"))
        self.assertEqual(f.dimension_kind, "uf")
        self.assertIn("NE", f.mixed_levels)
        self.assertIn("SE", f.mixed_levels)   # Sergipe ou Sudeste? ambíguo → fora
        self.assertEqual(f.headline, 50.0)    # só SP, RJ, BA, PE, AM
        self.assertNotIn("SE", f.uf_values)

    def test_ano_x_uf_com_preferencia_por_tempo(self):
        rows = [{"ano": 2023, "uf": "SP", "n": 2}, {"ano": 2023, "uf": "BA", "n": 1},
                {"ano": 2024, "uf": "SP", "n": 5}]
        kinds = _k(ano="numeric", uf="text", n="numeric")
        self.assertEqual(build(rows, kinds=kinds).dimension, "uf")
        serie = build(rows, kinds=kinds, prefer_time=True)
        self.assertEqual(serie.dimension_kind, "tempo")
        self.assertEqual(serie.series(), [("2023", 3.0), ("2024", 5.0)])

    def test_escalar(self):
        f = build([{"total": 42}], kinds=_k(total="numeric"))
        self.assertEqual((f.headline_kind, f.headline), ("valor", 42.0))

    def test_sem_metrica_nao_e_ok(self):
        f = build([{"uf": "SP"}, {"uf": "RJ"}], kinds=_k(uf="text"))
        self.assertFalse(f.ok)

    def test_fora_da_curva(self):
        rows = [{"c": str(i), "v": 10.0} for i in range(8)] + [{"c": "x", "v": 500.0}]
        f = build(rows, kinds=_k(c="text", v="numeric"))
        self.assertEqual(f.outliers(), [("x", 500.0)])

    def test_cobertura(self):
        rows = [{"a": 1, "b": None}, {"a": 2, "b": "x"}, {"a": 3, "b": ""}, {"a": 4, "b": "y"}]
        f = build(rows, kinds=_k(a="numeric", b="text"))
        self.assertEqual(f.coverage, {"a": 1.0, "b": 0.5})


class TestUnidade(unittest.TestCase):
    """A unidade vem da descrição da coluna no catálogo — regra de língua, não
    de base: tem de valer para qualquer órgão que plugar a camada semântica."""

    POUPANCA_REFS = [{"table": "ouro.saldo_poupanca", "used": True, "columns": [
        {"name": "data_referencia", "description": "Primeiro dia do período."},
        {"name": "saldo", "description": "Saldo total das cadernetas ao fim do mês, "
                                          "em milhões de reais.\n"}]}]
    INDICE_REFS = [{"table": "ouro.incc_m", "used": True, "columns": [
        {"name": "mes", "description": "Mês de referência."},
        {"name": "indice", "description": "Número índice do INCC-M apurado pela FGV."},
        {"name": "var_mes", "description": "Variação percentual do índice no mês."}]}]

    def test_descricoes(self):
        casos = {
            "Saldo ao fim do mês, em milhões de reais.": (units.DINHEIRO, 1e6),
            "Valor pago, em R$ mil": (units.DINHEIRO, 1e3),
            "Valores em bilhões de reais": (units.DINHEIRO, 1e9),
            "Amount paid in BRL, in millions": (units.DINHEIRO, 1e6),
            "Valor contratado em reais": (units.DINHEIRO, 1.0),
            "Produção em milhares de unidades": ("", 1e3),
            "Número índice do INCC-M (base 100 em ago/1994)": (units.INDICE, 1.0),
            # Variação do índice é taxa, não o nível do índice.
            "Variação percentual do índice no mês": ("", 1.0),
            # "mil" sem "em" na frente é contagem, não escala.
            "Quantidade de empreendimentos, mais de mil por ano": ("", 1.0),
            "": ("", 1.0),
        }
        for texto, (kind, fator) in casos.items():
            u = units.from_description(texto)
            self.assertEqual((u.kind, u.factor), (kind, fator), texto)

    def test_count_nao_herda_unidade(self):
        refs = [{"used": True, "columns": [{"name": "valor", "description": "em milhões de reais"}]}]
        self.assertEqual(units.resolve("total", "count(valor)", refs), units.Unit())
        self.assertEqual(units.resolve("total", "sum(valor)", refs).factor, 1e6)

    def test_poupanca_em_milhoes_vira_bilhoes(self):
        rows = [{"data_referencia": "2026-06-01", "saldo": 764080.812},
                {"data_referencia": "2026-07-01", "saldo": 761951.141}]
        f = build(rows, sql="SELECT data_referencia, saldo FROM ouro.saldo_poupanca",
                  references=self.POUPANCA_REFS, kinds=_k(data_referencia="date", saldo="numeric"))
        self.assertTrue(f.money)
        self.assertAlmostEqual(f.headline, 761951.141e6)
        self.assertEqual(fmt.compact(f.headline, money=f.money), "R$ 762 bi")
        self.assertIn("milhões de reais", f.unit_note)
        # A linha crua da base não muda: é ela que a trilha técnica e o CSV mostram.
        self.assertEqual(f.rows[-1]["saldo"], 761951.141)

    def test_indice_nao_e_abreviado_nem_somado(self):
        rows = [{"mes": "2026-06-01", "indice": 1275.085}, {"mes": "2026-07-01", "indice": 1283.035}]
        f = build(rows, sql="SELECT mes, indice FROM ouro.incc_m", references=self.INDICE_REFS,
                  kinds=_k(mes="date", indice="numeric"))
        self.assertEqual(f.unit, units.INDICE)
        self.assertFalse(f.additive)
        self.assertEqual(fmt.plain(f.headline), "1.283")
        self.assertEqual(fmt.plain(100.3), "100,3")

    def test_indice_pelo_nome_sem_descricao(self):
        rows = [{"ano": 2024, "indice_custo": 1500.0}, {"ano": 2025, "indice_custo": 1600.0}]
        f = build(rows, sql="SELECT ano, indice_custo FROM t", kinds=_k(ano="numeric", indice_custo="numeric"))
        self.assertEqual(f.unit, units.INDICE)

    def test_sem_catalogo_nada_muda(self):
        rows = [{"uf": "SP", "total": 10.0}, {"uf": "RJ", "total": 5.0}]
        f = build(rows, sql="SELECT uf, SUM(valor_contratado) AS total FROM t GROUP BY uf",
                  kinds=_k(uf="text", total="numeric"))
        self.assertTrue(f.money)
        self.assertEqual((f.unit, f.unit_note, f.headline), ("", "", 15.0))


if __name__ == "__main__":
    unittest.main()

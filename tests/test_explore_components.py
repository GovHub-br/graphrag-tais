"""Testes dos componentes da Persona 1 sem tocar em serviço nenhum.

O grafo é substituído por um store de mentira que devolve linhas canned, então o
que se verifica aqui é a LÓGICA: a ordem do ranking (que é o contrato consumido
pelo eval), a poda de colunas e as invariantes do answer builder — ancoragem no
catálogo e cobertura não limitada pelo LLM.

Precisa do Haystack (`Document`, `@component`), que só existe dentro do
container; no host o módulo é pulado em vez de quebrar a suíte.
"""
from __future__ import annotations

import json
import unittest

try:  # pragma: no cover - depende do ambiente
    from haystack import Document

    from rag.components.answer.explore_answer_builder import ExploreAnswerBuilder
    from rag.components.retrievers.neo4j_catalog_retriever import Neo4jCatalogRetriever

    HAYSTACK = True
except ImportError:  # pragma: no cover
    HAYSTACK = False


class FakeGraphStore:
    """Store de grafo canned: casa a Cypher pela primeira palavra distintiva."""

    def __init__(self, seeds=None, neighbors=None, tables=None, joins=None, domains=None):
        self.seeds = seeds or []
        self.neighbors = neighbors or []
        self.tables = tables or []
        self.joins = joins or []
        self.domains = domains or []
        self.calls: list[str] = []

    def execute_read(self, cypher: str, params: dict | None = None):
        self.calls.append(cypher)
        if "collect(t.name)" in cypher:
            return self.domains
        if "coalesce(CASE WHEN seed:Table" in cypher:
            return self.seeds
        if "neighbor_id" in cypher:
            return self.neighbors
        if "collect({" in cypher:
            return self.tables
        if "SEMANTIC_JOIN" in cypher:
            return self.joins
        raise AssertionError(f"Cypher inesperada: {cypher[:60]}")


def _table_row(fqn: str, name: str, schema: str, columns: list[dict], desc: str = ""):
    return {
        "table_fqn": fqn,
        "table_name": name,
        "schema_fqn": f"Cidades.cidades.{schema}",
        "table_desc": desc,
        "columns": columns,
    }


def _cols(*names: str) -> list[dict]:
    return [
        {"name": n, "type": "text", "is_pk": False, "description": ""} for n in names
    ]


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestCatalogRetrieverOrdering(unittest.TestCase):
    """A ORDEM dos documentos é o contrato que o eval pontua (MRR/nDCG)."""

    def _store(self):
        return FakeGraphStore(
            domains=[{"schema_fqn": "Cidades.cidades.far", "table_count": 2,
                      "names": ["alfa", "beta"]},
                     {"schema_fqn": "Cidades.cidades.fds", "table_count": 1,
                      "names": ["gama"]}],
            seeds=[{"tid": "C.c.far.alfa", "rank": 0}, {"tid": "C.c.far.beta", "rank": 2}],
            neighbors=[{"neighbor_id": "C.c.fds.gama", "rank": 1, "links": 1}],
            tables=[
                _table_row("C.c.far.alfa", "alfa", "far", _cols("id", "uf")),
                _table_row("C.c.far.beta", "beta", "far", _cols("id", "valor")),
                _table_row("C.c.fds.gama", "gama", "fds", _cols("id")),
            ],
            joins=[],
        )

    def _seed_docs(self):
        return [
            Document(content="", meta={"node_id": "C.c.far.alfa", "card_type": "table"}),
            Document(content="", meta={"node_id": "C.c.far.beta", "card_type": "table"}),
        ]

    def test_seeds_come_before_neighbors_even_with_better_rank(self):
        # O vizinho tem rank 1 (melhor que a semente beta, rank 2), mas semente
        # tem evidência DIRETA da pergunta e vizinho só evidência de vizinhança.
        retriever = Neo4jCatalogRetriever(graph_store=self._store())
        out = retriever.run(documents=self._seed_docs(), query="alfa")
        names = [d.meta["sql_name"] for d in out["documents"]]
        self.assertEqual(names, ["far.alfa", "far.beta", "fds.gama"])

    def test_seed_flag_and_domain_are_exposed(self):
        retriever = Neo4jCatalogRetriever(graph_store=self._store())
        out = retriever.run(documents=self._seed_docs(), query="alfa")
        by_name = {d.meta["sql_name"]: d.meta for d in out["documents"]}
        self.assertTrue(by_name["far.alfa"]["is_seed"])
        self.assertFalse(by_name["fds.gama"]["is_seed"])
        self.assertEqual(by_name["fds.gama"]["schema"], "fds")

    def test_catalog_lists_every_table_in_scope(self):
        retriever = Neo4jCatalogRetriever(graph_store=self._store())
        out = retriever.run(documents=self._seed_docs(), query="alfa")
        self.assertEqual(out["catalog"], ["far.alfa", "far.beta", "fds.gama"])

    def test_no_seeds_still_returns_the_panorama(self):
        # "Quais bases existem?" não tem semente semântica — e tem resposta.
        retriever = Neo4jCatalogRetriever(graph_store=self._store())
        out = retriever.run(documents=[], query="quais bases existem")
        self.assertEqual(out["documents"], [])
        self.assertEqual(len(out["domains"]), 2)
        self.assertEqual(len(out["catalog"]), 3)

    def test_expansion_can_be_switched_off(self):
        retriever = Neo4jCatalogRetriever(graph_store=self._store(), expand_neighbors=False)
        out = retriever.run(documents=self._seed_docs(), query="alfa")
        self.assertEqual([d.meta["sql_name"] for d in out["documents"]],
                         ["far.alfa", "far.beta"])

    def test_max_tables_truncates_the_tail(self):
        retriever = Neo4jCatalogRetriever(graph_store=self._store(), max_tables=2)
        out = retriever.run(documents=self._seed_docs(), query="alfa")
        self.assertEqual(len(out["documents"]), 2)

    def test_column_pruning_respects_the_cap(self):
        store = self._store()
        store.tables = [_table_row("C.c.far.alfa", "alfa", "far",
                                   _cols(*[f"c{i}" for i in range(20)]))]
        store.seeds = [{"tid": "C.c.far.alfa", "rank": 0}]
        store.neighbors = []
        retriever = Neo4jCatalogRetriever(graph_store=store, max_columns=4)
        out = retriever.run(documents=self._seed_docs()[:1], query="")
        self.assertEqual(len(out["documents"][0].meta["columns"]), 4)

    def test_island_is_marked(self):
        retriever = Neo4jCatalogRetriever(graph_store=self._store())
        out = retriever.run(documents=self._seed_docs(), query="alfa")
        self.assertTrue(all(d.meta["is_island"] for d in out["documents"]))


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestColumnRelevance(unittest.TestCase):
    """Relevância de coluna: o que decide QUEM ocupa as vagas do card.

    O caso que motivou tudo: numa pergunta sobre *localização*, `municipio`
    ficava de fora (a descrição dela diz "localizado", não "localização") e
    `situacao_empreendimento` entrava por ter a palavra "dados" na descrição —
    palavra que estava na pergunta só como enchimento."""

    COLUNAS = [
        {"name": "apf", "description": "Chave principal do empreendimento."},
        {"name": "municipio", "description": "Municipio onde o empreendimento esta localizado."},
        {"name": "uf", "description": "Unidade federativa onde o empreendimento esta localizado."},
        {"name": "situacao_empreendimento",
         "description": "Situacao gerencial do empreendimento, a partir dos dados da CAIXA."},
        {"name": "valor_contratado", "description": "Valor total contratado no empreendimento."},
    ]
    PERGUNTA = ("Quais dados granulares de localização sobre os empreendimentos posso "
                "cruzar com setores censitários do ibge?")

    def _pontos(self, pergunta: str) -> dict:
        from rag.components.retrievers.neo4j_catalog_retriever import (
            _pontua_colunas, _radicais_da_pergunta,
        )
        return _pontua_colunas(self.COLUNAS, _radicais_da_pergunta(pergunta))

    def test_accent_and_inflection_do_not_hide_a_column(self):
        # "localização" (pergunta) precisa encontrar "localizado" (descrição).
        pontos = self._pontos(self.PERGUNTA)
        self.assertGreater(pontos.get("municipio", 0), 0)
        self.assertGreater(pontos.get("uf", 0), 0)

    def test_location_columns_outrank_the_rest(self):
        pontos = self._pontos(self.PERGUNTA)
        melhor = max(pontos, key=pontos.get)
        self.assertIn(melhor, {"municipio", "uf"})
        self.assertGreater(pontos["municipio"], pontos.get("valor_contratado", 0))

    def test_generic_catalog_words_decide_nothing(self):
        # "dados"/"tabela"/"base" aparecem em quase toda descrição: são ruído.
        self.assertEqual(self._pontos("Quais dados essa tabela tem na base?"), {})

    def test_rare_term_beats_ubiquitous_term(self):
        # "empreendimento" está em quase toda descrição desta tabela e não
        # distingue nada; "localização" está em duas e distingue tudo.
        pontos = self._pontos(self.PERGUNTA)
        self.assertGreater(pontos["municipio"], pontos.get("situacao_empreendimento", 0))

    def test_stem_matches_related_words(self):
        from rag.components.retrievers.neo4j_catalog_retriever import _radical
        self.assertEqual(_radical("localizacao"), _radical("localizado"))
        self.assertEqual(_radical("empreendimentos"), _radical("empreendimento"))


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestColumnBudget(unittest.TestCase):
    """Orçamento de colunas: melhorar a relevância NÃO pode engordar o prompt.

    O teto vale para o total do card — inclusive para as colunas estruturais e
    para as relevantes. Antes, "essencial" entrava sem teto: quem melhorasse o
    casamento pagava em contexto. Aqui as vagas são fixas; muda quem as ocupa."""

    def _retriever(self, **kwargs):
        store = FakeGraphStore(
            seeds=[{"tid": "C.c.far.alfa", "rank": 0}], neighbors=[],
            tables=[_table_row(
                "C.c.far.alfa", "alfa", "far",
                [{"name": "municipio", "type": "text", "is_pk": False,
                  "description": "Municipio onde o empreendimento esta localizado."}]
                + [{"name": f"c{i}", "type": "text", "is_pk": False,
                    "description": "Localizacao do empreendimento."} for i in range(20)],
            )],
            joins=[], domains=[],
        )
        return Neo4jCatalogRetriever(graph_store=store, **kwargs)

    def _docs(self):
        return [Document(content="", meta={"node_id": "C.c.far.alfa", "label": "Table"})]

    def test_budget_is_never_exceeded_even_with_many_relevant_columns(self):
        # 21 colunas casam com a pergunta; o card continua com 5.
        out = self._retriever(max_columns=5).run(
            documents=self._docs(), query="onde fica a localização do empreendimento?")
        self.assertEqual(len(out["documents"][0].meta["columns"]), 5)

    def test_relevant_column_takes_the_slot_from_ordinal_filler(self):
        out = self._retriever(max_columns=2).run(
            documents=self._docs(), query="qual o município do empreendimento?")
        nomes = [c["name"] for c in out["documents"][0].meta["columns"]]
        self.assertIn("municipio", nomes)

    def test_expansion_table_gets_the_smaller_budget(self):
        retriever = self._retriever(max_columns=10, max_columns_expansion=3)
        colunas = retriever._select_columns(  # noqa: SLF001
            [{"name": f"c{i}", "type": "text", "is_pk": False, "description": ""}
             for i in range(20)],
            table_fqn="C.c.far.beta", seed_column_fqns=set(), join_key_names=set(),
            query_tokens=set(), is_seed_table=False,
        )
        self.assertEqual(len(colunas), 3)

    def test_structural_columns_come_first(self):
        retriever = self._retriever(max_columns=2)
        colunas = retriever._select_columns(  # noqa: SLF001
            [{"name": "id", "type": "int", "is_pk": True, "description": ""},
             {"name": "chave_join", "type": "text", "is_pk": False, "description": ""},
             {"name": "outra", "type": "text", "is_pk": False, "description": "localizacao"}],
            table_fqn="C.c.far.beta", seed_column_fqns=set(),
            join_key_names={"chave_join"}, query_tokens={"localiz"}, is_seed_table=True,
        )
        self.assertEqual([c["name"] for c in colunas], ["id", "chave_join"])


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestExploreAnswerBuilder(unittest.TestCase):
    def _docs(self):
        return [
            Document(content="", meta={
                "node_id": "C.c.far.alfa", "sql_name": "far.alfa", "schema": "far",
                "domain": "FAR", "table": "alfa", "description": "tabela alfa",
                "grain": "1 linha por empreendimento", "columns": [{"name": "uf"}],
                "is_seed": True, "is_island": False, "neighbors": ["beta"],
            }),
            Document(content="", meta={
                "node_id": "C.c.far.beta", "sql_name": "far.beta", "schema": "far",
                "domain": "FAR", "table": "beta", "description": "", "grain": "",
                "columns": [{"name": "valor"}], "is_seed": False, "is_island": True,
                "neighbors": [],
            }),
        ]

    def _reply(self, payload: dict) -> list:
        from haystack.dataclasses import ChatMessage

        return [ChatMessage.from_assistant(json.dumps(payload, ensure_ascii=False))]

    def test_invented_table_is_dropped_and_counted(self):
        out = ExploreAnswerBuilder().run(
            replies=self._reply({
                "summary": "s",
                "tables": [{"table": "far.alfa", "why": "serve"},
                           {"table": "far.inexistente", "why": "inventada"}],
            }),
            documents=self._docs(), catalog=["far.alfa", "far.beta"], query="q",
        )
        answer = out["answer"]
        self.assertEqual(answer["off_catalog_tables"], ["far.inexistente"])
        self.assertNotIn("far.inexistente", answer["ranked_tables"])

    def test_coverage_is_not_limited_by_what_the_llm_cited(self):
        # O LLM citou só uma; a outra recuperada continua na resposta. Recall é
        # a métrica da Persona 1 — o LLM prioriza, não filtra.
        out = ExploreAnswerBuilder().run(
            replies=self._reply({"summary": "s", "tables": [{"table": "far.alfa"}]}),
            documents=self._docs(), catalog=["far.alfa", "far.beta"], query="q",
        )
        self.assertEqual(out["answer"]["ranked_tables"], ["far.alfa", "far.beta"])

    def test_llm_reorder_promotes_cited_tables(self):
        out = ExploreAnswerBuilder().run(
            replies=self._reply({"summary": "s", "tables": [{"table": "far.beta"}]}),
            documents=self._docs(), catalog=["far.alfa", "far.beta"], query="q",
        )
        self.assertEqual(out["answer"]["ranked_tables"], ["far.beta", "far.alfa"])

    def test_reorder_can_be_switched_off_for_the_ab(self):
        out = ExploreAnswerBuilder(llm_reorder=False).run(
            replies=self._reply({"summary": "s", "tables": [{"table": "far.beta"}]}),
            documents=self._docs(), catalog=["far.alfa", "far.beta"], query="q",
        )
        self.assertEqual(out["answer"]["ranked_tables"], ["far.alfa", "far.beta"])

    def test_short_name_resolves_when_unambiguous(self):
        out = ExploreAnswerBuilder().run(
            replies=self._reply({"summary": "s", "tables": [{"table": "alfa"}]}),
            documents=self._docs(), catalog=["far.alfa", "far.beta"], query="q",
        )
        self.assertEqual(out["answer"]["off_catalog_tables"], [])
        self.assertEqual(out["answer"]["ranked_tables"][0], "far.alfa")

    def test_non_json_reply_still_answers_with_the_panorama(self):
        out = ExploreAnswerBuilder().run(
            replies=["desculpe, não consegui"],
            documents=self._docs(),
            domains=[{"schema": "far", "domain": "FAR", "table_count": 9}],
            catalog=["far.alfa", "far.beta"], query="q",
        )
        # Sem JSON não há priorização, mas as tabelas recuperadas seguem lá.
        self.assertEqual(out["answer"]["ranked_tables"], ["far.alfa", "far.beta"])
        self.assertIn("far.alfa", out["markdown"])

    def test_markdown_has_the_persona_blocks(self):
        out = ExploreAnswerBuilder().run(
            replies=self._reply({
                "summary": "resumo",
                "tables": [{"table": "far.alfa", "why": "porque sim",
                            "questions": ["quanto por UF?"]}],
                "sample_sql": "SELECT uf FROM far.alfa LIMIT 20",
                "sample_sql_rationale": "amostra",
            }),
            documents=self._docs(),
            domains=[{"schema": "far", "domain": "FAR", "table_count": 9}],
            catalog=["far.alfa", "far.beta"], query="q",
        )
        markdown = out["markdown"]
        for block in ("Tabelas que podem servir", "Detalhe das principais",
                      "Como elas se conectam", "O que dá para perguntar",
                      "SQL de amostra"):
            self.assertIn(block, markdown)
        self.assertIn("1 linha por empreendimento", markdown)  # grão exposto
        self.assertIn("Indicador", markdown)  # handoff (nome exibido) para a Persona 2

    # ------------------------------------------------------------------ #
    # Amostra de dados: a Persona 1 vê LINHAS reais, não só o SQL que as traria
    # ------------------------------------------------------------------ #
    _SAMPLE = {
        "ok": True,
        "sql": "SELECT uf, total FROM far.alfa LIMIT 100",
        "origin": "llm",
        "table": "far.alfa",
        "columns": ["uf", "total", "obs"],
        "rows": [{"uf": "SP", "total": 412, "obs": None},
                 {"uf": "MG", "total": 118, "obs": None}],
        "row_count": 2,
        "preview_rows": 10,
        "url": "http://minio.local/amostra.csv",
        "file_name": "amostra.csv",
    }

    def _answer_with_sample(self, sample: dict | None = None) -> tuple[dict, str]:
        builder = ExploreAnswerBuilder()
        out = builder.run(
            replies=self._reply({
                "summary": "resumo",
                "tables": [{"table": "far.alfa", "why": "porque sim"}],
                "sample_sql": "SELECT uf FROM far.alfa LIMIT 20",
                "sample_sql_rationale": "amostra",
            }),
            documents=self._docs(), catalog=["far.alfa", "far.beta"], query="q",
        )
        answer = out["answer"]
        answer["sample"] = self._SAMPLE if sample is None else sample
        return answer, builder.render(answer, [])

    def test_sample_rows_replace_the_sql_only_block(self):
        _, markdown = self._answer_with_sample()
        self.assertIn("Uma amostra do dado", markdown)
        self.assertNotIn("SQL de amostra (para ver o dado)", markdown)
        self.assertIn("| `uf` |", markdown)
        self.assertIn("| SP |", markdown.replace(" | 412 |", " |"))
        # o SQL que produziu a amostra continua na resposta, depois das linhas
        self.assertLess(markdown.index("Uma amostra"), markdown.index("```sql"))
        self.assertIn("SELECT uf, total FROM far.alfa", markdown)

    def test_sample_offers_the_csv_download(self):
        _, markdown = self._answer_with_sample()
        self.assertIn("Baixar a amostra", markdown)
        self.assertIn("http://minio.local/amostra.csv", markdown)

    def test_numeric_column_is_right_aligned_and_null_is_visible(self):
        _, markdown = self._answer_with_sample()
        linha_alinhamento = [l for l in markdown.splitlines() if l.startswith("| :--")][-1]
        self.assertIn("--:", linha_alinhamento)   # a coluna numérica alinha à direita
        self.assertIn("—", markdown)              # nulo aparece como nulo

    def test_without_sample_the_answer_is_the_one_from_before(self):
        _, markdown = self._answer_with_sample(sample={"ok": False})
        self.assertIn("SQL de amostra (para ver o dado)", markdown)
        self.assertNotIn("Uma amostra do dado", markdown)

    def test_tables_are_rendered_as_a_markdown_table(self):
        out = ExploreAnswerBuilder().run(
            replies=self._reply({
                "summary": "resumo",
                "tables": [{"table": "far.alfa", "why": "visão individual",
                            "questions": ["quanto por UF?"]}],
            }),
            documents=self._docs(),
            domains=[{"schema": "far", "domain": "FAR", "table_count": 13}],
            catalog=["far.alfa", "far.beta"], query="q",
        )
        markdown = out["markdown"]
        # Cabeçalho de grupo com o contraste "quantas de quantas".
        self.assertIn("**FAR** · 2 de 13 tabelas", markdown)
        # Linha de tabela Markdown, não bullet.
        self.assertIn("| `far.alfa` |", markdown)
        self.assertIn("| :-- |", markdown)
        # A tabela não citada pelo LLM entra na visão geral com a descrição.
        self.assertIn("| `far.beta` |", markdown)

    def test_grain_column_appears_only_when_there_is_grain(self):
        docs = self._docs()
        for doc in docs:  # nenhum grão declarado (caso comum no MCid)
            doc.meta["grain"] = ""
        out = ExploreAnswerBuilder().run(
            replies=self._reply({"summary": "s", "tables": [{"table": "far.alfa"}]}),
            documents=docs, catalog=["far.alfa", "far.beta"], query="q",
        )
        self.assertIn("| Tabela | Para que serve |", out["markdown"])
        self.assertNotIn("| Grão |", out["markdown"])

    def test_grain_column_appears_when_declared(self):
        out = ExploreAnswerBuilder().run(
            replies=self._reply({"summary": "s", "tables": [{"table": "far.alfa"}]}),
            documents=self._docs(), catalog=["far.alfa", "far.beta"], query="q",
        )
        self.assertIn("| Tabela | Grão | Para que serve |", out["markdown"])

    def test_pipe_in_description_does_not_break_the_table(self):
        docs = self._docs()
        docs[0].meta["description"] = "coluna a | coluna b"
        out = ExploreAnswerBuilder().run(
            replies=self._reply({"summary": "s", "tables": []}),
            documents=docs, catalog=["far.alfa", "far.beta"], query="q",
        )
        row = [ln for ln in out["markdown"].splitlines() if "far.alfa" in ln][0]
        self.assertIn("\\|", row)  # o pipe do texto foi escapado
        # Sem os escapados, sobram só as barras estruturais: 3 colunas = 4 barras.
        self.assertEqual(row.replace("\\|", "").count("|"), 4)

    def test_long_description_is_truncated_in_the_cell(self):
        docs = self._docs()
        docs[0].meta["description"] = "palavra " * 80
        out = ExploreAnswerBuilder().run(
            replies=self._reply({"summary": "s", "tables": []}),
            documents=docs, catalog=["far.alfa", "far.beta"], query="q",
        )
        row = [ln for ln in out["markdown"].splitlines() if "far.alfa" in ln][0]
        self.assertLess(len(row), 220)
        self.assertIn("…", row)

    def test_truncation_falls_on_a_word_boundary(self):
        docs = self._docs()
        docs[0].meta["description"] = (
            "Cobre o ciclo de vida da proposta com identificadores, situacao, "
            "localizacao, tipologia, valores e infraestrutura do entorno completa"
        )
        out = ExploreAnswerBuilder().run(
            replies=self._reply({"summary": "s", "tables": []}),
            documents=docs, catalog=["far.alfa", "far.beta"], query="q",
        )
        row = [ln for ln in out["markdown"].splitlines() if "far.alfa" in ln][0]
        texto = row.split("|")[-2].strip().rstrip("…")
        # A última palavra não pode estar cortada no meio (a pontuação final é
        # removida junto com o corte, então comparo sem ela).
        palavras = {w.strip(",.;:") for w in docs[0].meta["description"].split()}
        self.assertIn(texto.split()[-1], palavras)

    def test_detail_only_covers_highlighted_tables(self):
        out = ExploreAnswerBuilder().run(
            replies=self._reply({"summary": "s", "tables": [{"table": "far.alfa"}]}),
            documents=self._docs(), catalog=["far.alfa", "far.beta"], query="q",
        )
        detail = out["markdown"].split("### Detalhe das principais")[1]
        self.assertIn("far.alfa", detail)
        # far.beta aparece na visão geral, mas não vira bloco longo de detalhe.
        self.assertNotIn("far.beta", detail.split("###")[0])

    def test_catalog_question_puts_the_panorama_first(self):
        """O retriever devolve top-k para QUALQUER texto — inclusive para
        'quais bases existem'. Sem esta detecção, uma amostra de tabelas seria
        apresentada como se fosse o catálogo."""
        out = ExploreAnswerBuilder().run(
            replies=self._reply({"summary": "s", "tables": [{"table": "far.alfa"}]}),
            documents=self._docs(),
            domains=[{"schema": "far", "domain": "FAR", "table_count": 13,
                      "tables": ["far.alfa", "far.beta"]},
                     {"schema": "fds", "domain": "FDS", "table_count": 9,
                      "tables": ["fds.gama"]}],
            catalog=["far.alfa", "far.beta"],
            query="Quais bases de dados vocês têm disponíveis?",
        )
        markdown = out["markdown"]
        self.assertTrue(out["answer"]["catalog_scope"])
        # Panorama do escopo INTEIRO (os 2 domínios), antes das tabelas.
        self.assertLess(markdown.index("Bases disponíveis"),
                        markdown.index("amostra, pelo que você escreveu"))
        self.assertIn("| **FDS** | 9 |", markdown)

    def test_thematic_question_does_not_trigger_the_panorama(self):
        for query in ("Nunca mexi com MCMV Entidades. O que tem disponível sobre isso?",
                      "O que existe de dado sobre o programa MCMV FAR?",
                      "Tem dado de emprego na construção civil?"):
            out = ExploreAnswerBuilder().run(
                replies=self._reply({"summary": "s", "tables": [{"table": "far.alfa"}]}),
                documents=self._docs(),
                domains=[{"schema": "far", "domain": "FAR", "table_count": 13}],
                catalog=["far.alfa", "far.beta"], query=query,
            )
            self.assertFalse(out["answer"]["catalog_scope"], query)
            self.assertNotIn("Bases disponíveis", out["markdown"])
            self.assertIn("Tabelas que podem servir", out["markdown"])

    def test_catalog_only_answer_renders_the_domain_list(self):
        out = ExploreAnswerBuilder().run(
            replies=self._reply({"summary": "temos 3 bases"}),
            documents=[],
            domains=[{"schema": "far", "domain": "FAR", "table_count": 9,
                      "tables": ["far.alfa", "far.beta", "far.gama", "far.delta"]}],
            catalog=["far.alfa"], query="quais bases existem",
        )
        markdown = out["markdown"]
        self.assertIn("Bases disponíveis", markdown)
        self.assertIn("| **FAR** | 9 |", markdown)
        self.assertIn("`alfa`", markdown)  # exemplos, sem repetir o schema
        self.assertNotIn("`delta`", markdown)  # só 3 exemplos
        self.assertEqual(out["answer"]["ranked_tables"], [])

    def test_llm_gap_naming_an_existing_table_is_discarded(self):
        out = ExploreAnswerBuilder().run(
            replies=self._reply({
                "summary": "s",
                "gaps": ["não tenho far.alfa", "não tenho execução orçamentária"],
            }),
            documents=self._docs(), catalog=["far.alfa", "far.beta"], query="q",
        )
        self.assertEqual(out["answer"]["gaps"], ["não tenho execução orçamentária"])

    def test_island_is_disclosed_in_the_markdown(self):
        out = ExploreAnswerBuilder().run(
            replies=self._reply({"summary": "s", "tables": [{"table": "far.beta"}]}),
            documents=self._docs(), catalog=["far.alfa", "far.beta"], query="q",
        )
        self.assertIn("uso isolado", out["markdown"])


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestPromptTemplate(unittest.TestCase):
    """O template Jinja só falha em runtime — aqui ele é renderizado de verdade."""

    def _builder(self):
        from haystack.components.builders import ChatPromptBuilder
        from haystack.dataclasses import ChatMessage

        from rag.pipelines.explore.explore_pipeline import SYSTEM_PROMPT, USER_PROMPT

        return ChatPromptBuilder(
            template=[ChatMessage.from_system(SYSTEM_PROMPT),
                      ChatMessage.from_user(USER_PROMPT)],
            required_variables=["query"],
            variables=["documents", "joins", "domains", "query"],
        )

    def test_renders_with_tables_joins_and_domains(self):
        prompt = self._builder().run(
            query="o que existe sobre habitação?",
            documents=[Document(content="Tabela: far.alfa\n  Grão: 1 por obra")],
            joins=[{"left_table": "alfa", "left_col": "id", "right_table": "beta",
                    "right_col": "id", "sql_join_eligible": True}],
            domains=[{"domain": "FAR", "schema": "far", "table_count": 9}],
        )["prompt"]
        rendered = prompt[-1].text
        self.assertIn("o que existe sobre habitação?", rendered)
        self.assertIn("far.alfa", rendered)
        self.assertIn("JOIN direto", rendered)
        self.assertIn("FAR", rendered)

    def test_renders_the_catalog_only_branch(self):
        # Sem documentos e sem joins: o prompt tem de instruir a responder com o
        # panorama, não quebrar nem pedir tabela inexistente.
        rendered = self._builder().run(
            query="quais bases existem?",
            documents=[], joins=[],
            domains=[{"domain": "FAR", "schema": "far", "table_count": 9}],
        )["prompt"][-1].text
        self.assertIn("nenhuma tabela específica recuperada", rendered)
        self.assertIn("nenhuma — as tabelas recuperadas", rendered)

    def test_system_prompt_forbids_inventing_tables(self):
        from rag.pipelines.explore.explore_pipeline import SYSTEM_PROMPT

        self.assertIn("NUNCA invente uma tabela", SYSTEM_PROMPT)


@unittest.skipUnless(HAYSTACK, "Haystack indisponível no host — rode no container")
class TestPersonaIsolation(unittest.TestCase):
    """A promessa central do ADR-0014: a Persona 2 não muda."""

    def test_explore_settings_are_not_read_by_persona_2(self):
        import inspect

        from rag.components.retrievers import neo4j_subgraph_retriever
        from rag.pipelines.query import query_pipeline

        # A asserção é sobre LER o setting (`settings.X`), não sobre citá-lo em
        # comentário — um grep por nome puro proibiria explicar a diferença.
        for module in (neo4j_subgraph_retriever, query_pipeline):
            source = inspect.getsource(module)
            self.assertNotIn("settings.EXPLORE_", source, module.__name__)
            self.assertNotIn("settings.SCOPE_GAPS", source, module.__name__)

    def test_catalog_retriever_does_not_read_persona_2_tuning(self):
        # Os knobs medidos da Persona 2 não podem governar o modo Explorar —
        # nem para "aproveitar" o valor já calibrado.
        import inspect

        from rag.components.retrievers import neo4j_catalog_retriever

        source = inspect.getsource(neo4j_catalog_retriever)
        self.assertNotIn("settings.EXPANSION_MODE", source)
        self.assertNotIn("settings.PRUNE_", source)


if __name__ == "__main__":
    unittest.main()

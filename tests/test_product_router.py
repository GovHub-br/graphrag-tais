"""Roteamento de sementes por produto (`SEED_PRODUCT_ROUTING`). Precisa do Haystack."""
import unittest

from haystack import Document

from rag.components.retrievers.product_router import (
    ProductSeedRouter,
    aliases_from_label,
    products_in_question,
    route,
)


def _doc(node_id: str, score: float, product: str, label: str) -> Document:
    return Document(
        id=node_id, content=node_id, score=score,
        meta={"node_id": node_id, "card_type": "table",
              "data_product": product, "data_product_label": label},
    )


def _pool():
    return [
        _doc("ouro.ouro_ts_resumo_uf", 0.9, "trabalho_social", "Trabalho Social"),
        _doc("ouro.ouro_pro_moradia_panorama", 0.8, "pro_moradia", "Pró-Moradia"),
        _doc("ouro.ouro_far_ficha", 0.5, "empreendimento_far", "Empreendimento FAR"),
        _doc("ouro.ouro_historico", 0.45, "", ""),
    ]


class AliasTest(unittest.TestCase):
    def test_label_forms(self):
        self.assertIn("pro moradia", aliases_from_label("Pró-Moradia"))
        self.assertIn("fnhis", aliases_from_label("FNHIS Sub-50"))

    def test_no_scope_vocabulary_by_default(self):
        # Sem stopwords configuradas: rótulo inteiro + siglas, nada mais.
        self.assertEqual(aliases_from_label("Empreendimento Rural"),
                         {"empreendimento rural"})

    def test_stopwords_come_from_config(self):
        self.assertEqual(
            aliases_from_label("Empreendimento Rural", ["empreendimento"]),
            {"empreendimento rural", "rural"},
        )
        self.assertIn("far", aliases_from_label("Empreendimento FAR", ["empreendimento"]))
        # Palavra no MEIO do rótulo não é cortada.
        self.assertEqual(aliases_from_label("Trabalho Social", ["social"]),
                         {"trabalho social"})

    def test_whole_word_only(self):
        aliases = {"empreendimento_far": {"far"}}
        self.assertEqual(products_in_question("empreendimentos FAR por UF", aliases),
                         {"empreendimento_far"})
        # "far" dentro de outra palavra não conta.
        self.assertEqual(products_in_question("o que vai faltar?", aliases), set())


class RouteTest(unittest.TestCase):
    def test_cited_product_moves_up(self):
        docs, found = route(_pool(), "10 UFs com mais unidades no FAR", boost=1.0)
        self.assertEqual(found, {"empreendimento_far"})
        self.assertEqual(docs[0].meta["node_id"], "ouro.ouro_far_ficha")

    def test_no_product_in_question_keeps_order(self):
        before = [d.meta["node_id"] for d in _pool()]
        docs, found = route(_pool(), "evolução do emprego na construção", boost=1.0)
        self.assertEqual(found, set())
        self.assertEqual([d.meta["node_id"] for d in docs], before)

    def test_priority_not_filter(self):
        docs, _ = route(_pool(), "unidades do FAR", boost=1.0)
        self.assertEqual(len(docs), 4)

    def test_stopwords_route_label_without_acronym(self):
        pool = [_doc("a", 0.9, "x", "Outro"),
                _doc("b", 0.5, "rural", "Empreendimento Rural")]
        docs, found = route(pool, "unidades do rural por UF", boost=1.0)
        self.assertEqual(found, set())
        docs, found = route(pool, "unidades do rural por UF", boost=1.0,
                            stopwords=["empreendimento"])
        self.assertEqual(found, {"rural"})
        self.assertEqual(docs[0].meta["node_id"], "b")

    def test_extra_aliases(self):
        pool = [_doc("a", 0.9, "x", "Outro"),
                _doc("b", 0.5, "entidades_fds", "Entidades FDS")]
        docs, found = route(pool, "obras do MCMV Entidades", boost=1.0,
                            extra_aliases={"entidades_fds": {"mcmv entidades"}})
        self.assertIn("entidades_fds", found)
        self.assertEqual(docs[0].meta["node_id"], "b")


class ComponentTest(unittest.TestCase):
    def test_cuts_documents_keeps_pool(self):
        out = ProductSeedRouter(top_k=2, boost=1.0).run(_pool(), "valor contratado do FAR")
        self.assertEqual(len(out["documents"]), 2)
        self.assertEqual(len(out["pool"]), 4)
        self.assertEqual(out["documents"][0].meta["node_id"], "ouro.ouro_far_ficha")


if __name__ == "__main__":
    unittest.main()

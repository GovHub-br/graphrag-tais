"""Expansão por PageRank personalizado (`EXPANSION_MODE=ppr|prize`).

Funções puras: roda no host, sem Haystack nem Neo4j.
"""
import importlib.util
import pathlib
import unittest

_PATH = pathlib.Path(__file__).resolve().parents[1] / "src/rag/components/retrievers/expansion.py"
_spec = importlib.util.spec_from_file_location("expansion", _PATH)
expansion = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(expansion)


def _edges(*pairs):
    return [{"u": u, "v": v} for u, v in pairs]


def _degree(edges):
    adj = expansion.table_adjacency(edges)
    return {k: len(v) for k, v in adj.items()}


class PersonalizedPageRankTest(unittest.TestCase):
    def test_mass_decays_with_distance(self):
        edges = _edges(("s", "a"), ("a", "b"))
        adj = expansion.table_adjacency(edges)
        w = expansion.transition_weights(adj, degree=_degree(edges), product={},
                                         degree_beta=0.0, cross_product=1.0)
        pi = expansion.personalized_pagerank({"s": 1.0}, w, alpha=0.3)
        self.assertGreater(pi["s"], pi["a"])
        self.assertGreater(pi["a"], pi["b"])
        self.assertAlmostEqual(sum(pi.values()), 1.0, places=6)

    def test_dangling_mass_returns_to_seeds(self):
        pi = expansion.personalized_pagerank({"s": 1.0}, {}, alpha=0.3)
        self.assertAlmostEqual(pi["s"], 1.0)


class SelectExpansionTest(unittest.TestCase):
    def test_hub_is_discounted(self):
        # `hub` liga a semente a 6 outras tabelas; `leaf` só à semente.
        pairs = [("s", "hub"), ("s", "leaf")] + [("hub", f"x{i}") for i in range(6)]
        edges = _edges(*pairs)
        chosen = expansion.select_expansion({"s": 1.0}, edges, degree=_degree(edges),
                                            product={}, budget=1, min_ratio=0.0)
        self.assertEqual(chosen, ["leaf"])

    def test_other_product_is_discounted(self):
        edges = _edges(("s", "mesmo"), ("s", "outro"))
        product = {"s": "far", "mesmo": "far", "outro": "rural"}
        chosen = expansion.select_expansion({"s": 1.0}, edges, degree=_degree(edges),
                                            product=product, budget=1, min_ratio=0.0)
        self.assertEqual(chosen, ["mesmo"])

    def test_missing_product_is_not_penalized(self):
        edges = _edges(("s", "a"), ("s", "b"))
        product = {"s": "far", "a": "", "b": "far"}
        chosen = expansion.select_expansion({"s": 1.0}, edges, degree=_degree(edges),
                                            product=product, budget=2, min_ratio=0.0)
        self.assertEqual(sorted(chosen), ["a", "b"])

    def test_connector_between_seeds_enters_first(self):
        pairs = [("s1", "ponte"), ("s2", "ponte"), ("s1", "folha")]
        edges = _edges(*pairs)
        chosen = expansion.select_expansion({"s1": 1.0, "s2": 0.5}, edges,
                                            degree=_degree(edges), product={},
                                            budget=1, min_ratio=0.0)
        self.assertEqual(chosen, ["ponte"])

    def test_budget_and_floor(self):
        edges = _edges(*[("s", f"n{i}") for i in range(10)])
        chosen = expansion.select_expansion({"s": 1.0}, edges, degree=_degree(edges),
                                            product={}, budget=3, min_ratio=0.0)
        self.assertEqual(len(chosen), 3)
        none = expansion.select_expansion({"s": 1.0}, edges, degree=_degree(edges),
                                          product={}, budget=3, min_ratio=10.0)
        self.assertEqual(none, [])

    def test_prize_prefers_neighbor_the_search_also_found(self):
        edges = _edges(("s", "achado"), ("s", "ignorado"))
        chosen = expansion.select_expansion(
            {"s": 1.0}, edges, degree=_degree(edges), product={},
            relevance={"achado": 0.8}, prize_floor=0.1, budget=1, min_ratio=0.0,
        )
        self.assertEqual(chosen, ["achado"])

    def test_no_edges_no_expansion(self):
        self.assertEqual(expansion.select_expansion({"s": 1.0}, [], degree={}, product={}), [])


class PoolRelevanceTest(unittest.TestCase):
    def test_best_card_per_table_normalized(self):
        rel = expansion.pool_relevance([("t1", 0.5), ("t1", 0.25), ("t2", 0.25), ("", 1.0)])
        self.assertEqual(rel, {"t1": 1.0, "t2": 0.5})


if __name__ == "__main__":
    unittest.main()

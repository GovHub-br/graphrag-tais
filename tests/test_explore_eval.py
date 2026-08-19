"""Testes do motor de métricas do modo Explorar (Persona 1).

Só stdlib: roda no host, sem Qdrant, Neo4j, Ollama ou OpenAI. Os valores
esperados de nDCG/MAP/MRR são calculados à mão no próprio teste (o cálculo está
escrito no comentário de cada asserção) — uma métrica que ninguém conferiu não
mede nada, só produz números com aparência de autoridade.
"""
from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

from eval.explore.cli import build_report, render_markdown
from eval.explore.metrics import (
    aggregate,
    aggregate_by_slice,
    catalog_fidelity,
    domain_metrics,
    gap_disclosure,
    groundedness,
    percentile,
    physical_name,
    ranking_metrics,
    score_case,
)
from eval.explore.models import ContractError, check_policy, load_dataset, validate_run

FIXTURES = Path(__file__).resolve().parents[1] / "eval" / "fixtures" / "explore"
DATASETS = Path(__file__).resolve().parents[1] / "eval" / "datasets" / "explore"
CONFIG = Path(__file__).resolve().parents[1] / "eval" / "config"

GRADED = {"a.t1": 3, "a.t2": 2, "b.t3": 1}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestNameNormalization(unittest.TestCase):
    def test_fqn_and_physical_are_the_same_table(self):
        # O gold pode declarar o FQN do catálogo; a resposta usa o nome físico.
        self.assertEqual(
            physical_name("Cidades.cidades.empreendimento_far.empreendimento"),
            physical_name("empreendimento_far.empreendimento"),
        )

    def test_case_and_backticks_are_ignored(self):
        self.assertEqual(physical_name("`Schema.Tabela`"), "schema.tabela")

    def test_bare_name_survives(self):
        self.assertEqual(physical_name("tabela"), "tabela")


class TestRankingMetrics(unittest.TestCase):
    def test_perfect_ranking(self):
        metrics = ranking_metrics(["a.t1", "a.t2", "b.t3"], GRADED)
        for name in ("table_recall", "core_recall", "hit", "mrr", "ndcg", "map"):
            self.assertEqual(metrics[f"{name}_at_5"], 1.0, name)
        self.assertEqual(metrics["table_precision_at_5"], 1.0)

    def test_degraded_ranking_exact_values(self):
        # top-3 = [c.t9 (irrelevante), b.t3 (rel 1), a.t1 (rel 3)]
        metrics = ranking_metrics(["c.t9", "b.t3", "a.t1"], GRADED)

        self.assertAlmostEqual(metrics["table_recall_at_5"], 2 / 3)
        self.assertAlmostEqual(metrics["table_precision_at_5"], 2 / 3)
        self.assertEqual(metrics["hit_at_5"], 1.0)
        # core = {a.t1}, presente
        self.assertEqual(metrics["core_recall_at_5"], 1.0)
        # primeiro relevante na posição 2
        self.assertAlmostEqual(metrics["mrr_at_5"], 0.5)

        # DCG = (2^1-1)/log2(3) + (2^3-1)/log2(4) = 0.63093 + 3.5
        dcg = 1 / math.log2(3) + 7 / math.log2(4)
        # IDCG = 7/log2(2) + 3/log2(3) + 1/log2(4)
        idcg = 7 / math.log2(2) + 3 / math.log2(3) + 1 / math.log2(4)
        self.assertAlmostEqual(metrics["ndcg_at_5"], dcg / idcg)

        # MAP = (1/2 + 2/3) / min(3, 5)
        self.assertAlmostEqual(metrics["map_at_5"], (1 / 2 + 2 / 3) / 3)

    def test_cutoff_actually_cuts(self):
        ranked = ["x.1", "x.2", "x.3", "x.4", "x.5", "a.t1"]
        metrics = ranking_metrics(ranked, {"a.t1": 3})
        # a.t1 está na posição 6: invisível em @5, visível em @10
        self.assertEqual(metrics["table_recall_at_5"], 0.0)
        self.assertEqual(metrics["hit_at_5"], 0.0)
        self.assertEqual(metrics["table_recall_at_10"], 1.0)
        self.assertAlmostEqual(metrics["mrr_at_10"], 1 / 6)

    def test_ndcg_penalizes_relevant_never_shown(self):
        # Só a tabela de relevância 1 aparece: nDCG tem de ser baixo, e não 1.0
        # (o ideal é calculado sobre o gold inteiro, não sobre o que foi mostrado).
        metrics = ranking_metrics(["b.t3"], GRADED)
        self.assertLess(metrics["ndcg_at_5"], 0.15)
        self.assertGreater(metrics["ndcg_at_5"], 0.0)

    def test_duplicates_do_not_inflate_recall(self):
        metrics = ranking_metrics(["a.t1", "a.t1", "a.t1"], GRADED)
        self.assertAlmostEqual(metrics["table_recall_at_5"], 1 / 3)

    def test_empty_ranking_scores_zero(self):
        metrics = ranking_metrics([], GRADED)
        self.assertEqual(metrics["table_recall_at_5"], 0.0)
        self.assertEqual(metrics["mrr_at_5"], 0.0)
        self.assertEqual(metrics["ndcg_at_5"], 0.0)

    def test_case_without_gold_tables_is_neutral(self):
        # Pergunta de catálogo puro: sem tabela esperada, ranking não penaliza.
        metrics = ranking_metrics(["qualquer.coisa"], {})
        self.assertEqual(metrics["table_recall_at_5"], 1.0)
        self.assertEqual(metrics["ndcg_at_5"], 1.0)


class TestAntiGaming(unittest.TestCase):
    def test_listing_the_whole_catalog_buys_recall_but_shows_up(self):
        """Despejar o catálogo garante recall 1,0 — e tem de aparecer em
        precision e listed_ratio, senão a métrica premia o comportamento ruim."""
        filler = [f"x.t{i}" for i in range(1, 20)]
        # As relevantes existem, mas diluídas no despejo — o caso realista.
        everything = filler[:5] + list(GRADED) + filler[5:]
        answer = {
            "summary": "tudo",
            "ranked_tables": everything,
            "tables": [{"table": t} for t in everything],
            "domains": [{"schema": "a", "domain": "a"}],
            "catalog_size": len(everything),
        }
        metrics = score_case(answer, {"table_relevance": GRADED})
        self.assertEqual(metrics["table_recall_at_15"], 1.0)
        self.assertLess(metrics["table_precision_at_15"], 0.25)
        self.assertEqual(metrics["listed_ratio"], 1.0)


class TestPersonaMetrics(unittest.TestCase):
    def test_catalog_fidelity_counts_invented_tables(self):
        result = catalog_fidelity(["a.t1", "a.t2", "b.t3"], ["z.zz"])
        self.assertAlmostEqual(result["catalog_fidelity"], 0.75)
        self.assertAlmostEqual(result["off_catalog_rate"], 0.25)
        self.assertEqual(result["_off_catalog"], ["z.zz"])

    def test_catalog_fidelity_is_one_when_nothing_invented(self):
        self.assertEqual(catalog_fidelity(["a.t1"], [])["catalog_fidelity"], 1.0)

    def test_domain_metrics_without_expectation_are_neutral(self):
        result = domain_metrics(["a", "b"], [])
        self.assertEqual(result["domain_recall"], 1.0)
        self.assertEqual(result["domain_precision"], 1.0)

    def test_domain_metrics_penalize_missing_domain(self):
        result = domain_metrics(["a"], ["a", "b"])
        self.assertAlmostEqual(result["domain_recall"], 0.5)
        self.assertEqual(result["_domains_missed"], ["b"])

    def test_gap_disclosure_ignores_accents(self):
        result = gap_disclosure(
            ["Não tenho execucao orcamentaria; tenho valor contratado"],
            ["execução orçamentária"],
        )
        self.assertEqual(result["gap_disclosure"], 1.0)

    def test_gap_disclosure_reports_what_was_not_said(self):
        result = gap_disclosure([], ["execução orçamentária", "rural"])
        self.assertEqual(result["gap_disclosure"], 0.0)
        self.assertEqual(result["_gaps_missed"], ["execução orçamentária", "rural"])

    def test_groundedness_flags_sql_over_unknown_table(self):
        result = groundedness(
            [{"table": "a.t1", "questions": ["q1"]}],
            "SELECT x FROM z.inexistente LIMIT 20",
            ["a.t1"],
        )
        self.assertEqual(result["sample_sql_grounded"], 0.0)
        self.assertEqual(result["_sql_tables_unknown"], ["z.inexistente"])

    def test_groundedness_accepts_sql_over_presented_table(self):
        result = groundedness(
            [{"table": "a.t1", "questions": ["q1", "q2"]}],
            "SELECT x FROM a.t1 LIMIT 20",
            ["a.t1"],
        )
        self.assertEqual(result["sample_sql_grounded"], 1.0)
        self.assertEqual(result["question_groundedness"], 1.0)
        self.assertEqual(result["questions_per_table"], 2.0)

    def test_answered_rate_requires_summary_and_tables(self):
        empty = score_case({"summary": "", "ranked_tables": []}, {"table_relevance": {}})
        self.assertEqual(empty["answered_rate"], 0.0)
        full = score_case(
            {"summary": "algo", "ranked_tables": ["a.t1"]}, {"table_relevance": {}}
        )
        self.assertEqual(full["answered_rate"], 1.0)


class TestAggregation(unittest.TestCase):
    def test_aggregate_averages_and_ignores_diagnostics(self):
        summary = aggregate([
            {"table_recall_at_5": 1.0, "_ranked": ["x"]},
            {"table_recall_at_5": 0.0, "_ranked": ["y"]},
        ])
        self.assertEqual(summary["table_recall_at_5"], 0.5)
        self.assertNotIn("_ranked", summary)

    def test_aggregate_by_slice_separates_themes(self):
        by_slice = aggregate_by_slice([
            {"slices": ["far"], "metrics": {"table_recall_at_5": 1.0}},
            {"slices": ["conjuntura"], "metrics": {"table_recall_at_5": 0.0}},
            {"slices": ["far", "conjuntura"], "metrics": {"table_recall_at_5": 0.5}},
        ])
        self.assertAlmostEqual(by_slice["far"]["table_recall_at_5"], 0.75)
        self.assertAlmostEqual(by_slice["conjuntura"]["table_recall_at_5"], 0.25)

    def test_percentile_edges(self):
        self.assertEqual(percentile([], 0.5), 0.0)
        self.assertEqual(percentile([1, 2, 3, 4], 0.0), 1)
        self.assertEqual(percentile([1, 2, 3, 4], 1.0), 4)


class TestDatasetContract(unittest.TestCase):
    def _base(self) -> dict:
        return {
            "name": "x", "version": "0.0.1", "status": "draft",
            "cases": [{"id": "c1", "question": "q", "table_relevance": {"a.t1": 3}}],
        }

    def test_valid_dataset_loads(self):
        dataset = load_dataset(self._base())
        self.assertEqual(len(dataset.cases), 1)
        self.assertFalse(dataset.is_gate_eligible)  # draft nunca é gate

    def test_accepted_dataset_is_gate_eligible(self):
        payload = self._base()
        payload["status"] = "accepted"
        self.assertTrue(load_dataset(payload).is_gate_eligible)

    def test_duplicate_id_is_rejected(self):
        payload = self._base()
        payload["cases"].append(dict(payload["cases"][0]))
        with self.assertRaises(ContractError):
            load_dataset(payload)

    def test_relevance_out_of_scale_is_rejected(self):
        payload = self._base()
        payload["cases"][0]["table_relevance"] = {"a.t1": 5}
        with self.assertRaises(ContractError):
            load_dataset(payload)

    def test_table_without_schema_is_rejected(self):
        payload = self._base()
        payload["cases"][0]["table_relevance"] = {"empreendimento": 3}
        with self.assertRaises(ContractError):
            load_dataset(payload)

    def test_case_with_nothing_to_evaluate_is_rejected(self):
        payload = self._base()
        payload["cases"][0]["table_relevance"] = {}
        with self.assertRaises(ContractError):
            load_dataset(payload)

    def test_catalog_only_case_needs_domains(self):
        payload = self._base()
        payload["cases"][0]["table_relevance"] = {}
        payload["cases"][0]["expected_domains"] = ["a"]
        self.assertEqual(len(load_dataset(payload).cases), 1)

    def test_run_with_unknown_case_is_rejected(self):
        dataset = load_dataset(self._base())
        with self.assertRaises(ContractError):
            validate_run({"cases": [{"case_id": "fantasma"}]}, dataset)

    def test_run_missing_cases_only_warns(self):
        dataset = load_dataset(self._base())
        warnings = validate_run({"cases": []}, dataset)
        self.assertTrue(any("ausentes" in w for w in warnings))


class TestRealDataset(unittest.TestCase):
    def test_shipped_gold_is_valid_and_draft(self):
        dataset = load_dataset(_read(DATASETS / "mcid-explore-v0.1.0.json"))
        self.assertEqual(dataset.status, "draft")
        self.assertFalse(dataset.is_gate_eligible)
        self.assertGreaterEqual(len(dataset.cases), 15)

    def test_gold_covers_the_ingested_schemas(self):
        dataset = load_dataset(_read(DATASETS / "mcid-explore-v0.1.0.json"))
        schemas = {
            name.split(".")[0]
            for case in dataset.cases
            for name in case.table_relevance
        }
        self.assertEqual(
            schemas,
            {"empreendimento_far", "entidades_fds", "conjuntura_gold", "conjuntura_silver"},
        )

    def test_gold_has_refusal_cases(self):
        dataset = load_dataset(_read(DATASETS / "mcid-explore-v0.1.0.json"))
        with_gaps = [c for c in dataset.cases if c.expected_gaps]
        self.assertGreaterEqual(len(with_gaps), 2)


class TestReportAndGate(unittest.TestCase):
    def test_perfect_run_scores_one(self):
        report = build_report(
            _read(FIXTURES / "synthetic-dataset.json"),
            _read(FIXTURES / "perfect-run.json"),
        )
        summary = report["summary"]
        self.assertEqual(summary["table_recall_at_10"], 1.0)
        self.assertEqual(summary["core_recall_at_10"], 1.0)
        self.assertEqual(summary["mrr_at_10"], 1.0)
        self.assertEqual(summary["catalog_fidelity"], 1.0)
        self.assertEqual(summary["gap_disclosure"], 1.0)
        self.assertEqual(summary["answered_rate"], 1.0)
        self.assertEqual(summary["error_rate"], 0.0)
        self.assertEqual(summary["case_count"], 2.0)

    def test_degraded_run_scores_below_perfect(self):
        report = build_report(
            _read(FIXTURES / "synthetic-dataset.json"),
            _read(FIXTURES / "degraded-run.json"),
        )
        summary = report["summary"]
        # caso 1: fidelidade 0.75; caso 2: resposta vazia -> 1.0 (nada citado)
        self.assertAlmostEqual(summary["catalog_fidelity"], (0.75 + 1.0) / 2)
        # caso 1: recall 2/3; caso 2: 0.0
        self.assertAlmostEqual(summary["table_recall_at_10"], (2 / 3) / 2)
        # caso 1 respondeu, caso 2 devolveu resposta vazia
        self.assertEqual(summary["answered_rate"], 0.5)
        self.assertEqual(summary["gap_disclosure"], 0.5)  # caso 2 não tem lacuna esperada

    def test_markdown_report_renders_the_key_blocks(self):
        report = build_report(
            _read(FIXTURES / "synthetic-dataset.json"),
            _read(FIXTURES / "degraded-run.json"),
        )
        markdown = render_markdown(report)
        self.assertIn("Ranking das tabelas apresentadas", markdown)
        self.assertIn("Contrato da persona", markdown)
        self.assertIn("Por slice", markdown)
        self.assertIn("Casos a investigar", markdown)
        # Gold em draft tem de avisar que o número não é gate.
        self.assertIn("draft", markdown)

    def test_policy_floor_fails_on_invented_table(self):
        report = build_report(
            _read(FIXTURES / "synthetic-dataset.json"),
            _read(FIXTURES / "degraded-run.json"),
        )
        policy = _read(CONFIG / "explore-policy-v0.1.0.json")
        passed, findings = check_policy(report["summary"], policy)
        self.assertFalse(passed)
        fidelity = [f for f in findings if f["metric"] == "catalog_fidelity"][0]
        self.assertFalse(fidelity["passed"])

    def test_policy_passes_on_perfect_run(self):
        report = build_report(
            _read(FIXTURES / "synthetic-dataset.json"),
            _read(FIXTURES / "perfect-run.json"),
        )
        policy = _read(CONFIG / "explore-policy-v0.1.0.json")
        passed, _ = check_policy(report["summary"], policy)
        self.assertTrue(passed)

    def test_regression_check_needs_a_baseline(self):
        policy = {"max_regression": {"mrr_at_10": 0.05}}
        _, findings = check_policy({"mrr_at_10": 0.1}, policy, baseline=None)
        self.assertTrue(findings[0]["passed"])
        self.assertIn("skipped", findings[0])

    def test_regression_is_caught_against_baseline(self):
        policy = {"max_regression": {"mrr_at_10": 0.05}}
        passed, _ = check_policy({"mrr_at_10": 0.80}, policy, baseline={"mrr_at_10": 0.90})
        self.assertFalse(passed)


if __name__ == "__main__":
    unittest.main()

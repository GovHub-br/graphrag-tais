from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from eval.retrieval.compare import compare_reports
from eval.retrieval.models import (
    ContractError,
    EvaluationReport,
    GatePolicy,
    RetrievalDataset,
    RetrievalRun,
)
from eval.retrieval.runner import evaluate_run


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "eval" / "fixtures" / "retrieval"
POLICY = ROOT / "eval" / "config" / "retrieval-policy-v0.1.0.json"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class RetrievalMetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = RetrievalDataset.from_json_file(
            FIXTURES / "synthetic-dataset.json"
        )

    def _report(self, fixture: str) -> EvaluationReport:
        run = RetrievalRun.from_json_file(FIXTURES / fixture)
        return evaluate_run(
            self.dataset,
            run,
            generated_at="2026-07-24T00:00:00Z",
        )

    def test_perfect_trace_recovers_structure_and_expands_usefully(self) -> None:
        report = self._report("perfect-run.json")
        metrics = report.cases["synthetic-join-001"]["metrics"]

        self.assertEqual(report.summary["error_rate"], 0)
        self.assertTrue(all(item.passed for item in report.invariants))
        self.assertEqual(metrics["seed_recall_at_10"], 1)
        self.assertEqual(metrics["node_recall"], 1)
        self.assertEqual(metrics["relationship_recall"], 1)
        self.assertEqual(metrics["join_eligibility_accuracy"], 1)
        self.assertEqual(metrics["unauthorized_join_rate"], 0)
        self.assertEqual(metrics["join_path_recall"], 1)
        self.assertEqual(metrics["structural_sufficiency"], 1)
        self.assertEqual(metrics["weighted_dependency_recall"], 1)
        self.assertEqual(metrics["expansion_recall_gain"], 0.5)
        self.assertEqual(metrics["marginal_expansion_precision"], 1)
        self.assertEqual(metrics["overexpansion"], 0)

    def test_degraded_trace_loses_required_join_and_sufficiency(self) -> None:
        report = self._report("degraded-run.json")
        metrics = report.cases["synthetic-join-001"]["metrics"]

        self.assertEqual(metrics["structural_sufficiency"], 0)
        self.assertEqual(metrics["eligible_join_relationship_recall"], 0)
        self.assertEqual(metrics["join_path_recall"], 0)
        self.assertLess(metrics["weighted_dependency_recall"], 1)
        self.assertLess(metrics["node_recall"], 1)

    def test_noisy_trace_keeps_recall_but_is_penalized_for_noise(self) -> None:
        perfect = self._report("perfect-run.json")
        noisy = self._report("noisy-run.json")
        perfect_metrics = perfect.cases["synthetic-join-001"]["metrics"]
        noisy_metrics = noisy.cases["synthetic-join-001"]["metrics"]

        self.assertEqual(noisy_metrics["structural_sufficiency"], 1)
        self.assertEqual(noisy_metrics["node_recall"], 1)
        self.assertGreater(noisy_metrics["overexpansion"], 0)
        self.assertGreater(noisy_metrics["disconnected_rate"], 0)
        self.assertLess(
            noisy_metrics["node_precision"],
            perfect_metrics["node_precision"],
        )
        self.assertLess(noisy_metrics["ndcg_at_10"], perfect_metrics["ndcg_at_10"])

    def test_context_observations_are_aggregated_without_becoming_a_score(self) -> None:
        payload = _json(FIXTURES / "perfect-run.json")
        payload["traces"][0]["metadata"]["context"] = {
            "document_count": 2,
            "schema_context_chars": 1234,
            "rendered_llm_prompt_chars": 1300,
        }
        report = evaluate_run(
            self.dataset,
            RetrievalRun.from_dict(payload),
            generated_at="2026-07-24T00:00:00Z",
        )

        case = report.cases["synthetic-join-001"]
        self.assertEqual(case["context"]["schema_context_chars"], 1234)
        self.assertEqual(
            report.summary["context_schema_context_chars_p50"],
            1234,
        )
        self.assertEqual(
            report.summary["context_rendered_llm_prompt_chars_max"],
            1300,
        )
        self.assertEqual(
            report.summary[
                "context_rendered_llm_prompt_chars_coverage_count"
            ],
            1,
        )
        self.assertEqual(
            report.summary[
                "context_rendered_llm_prompt_chars_coverage_rate"
            ],
            1,
        )
        self.assertNotIn("schema_context_chars", case["metrics"])

    def test_invalid_context_observation_is_reported_and_not_aggregated(self) -> None:
        payload = _json(FIXTURES / "perfect-run.json")
        payload["traces"][0]["metadata"]["context"] = {
            "schema_context_chars": -1,
            "rendered_llm_prompt_chars": 1300,
        }
        report = evaluate_run(
            self.dataset,
            RetrievalRun.from_dict(payload),
            generated_at="2026-07-24T00:00:00Z",
        )

        case = report.cases["synthetic-join-001"]
        self.assertEqual(
            case["invalid_context_fields"],
            ["schema_context_chars"],
        )
        self.assertNotIn("schema_context_chars", case["context"])
        self.assertNotIn(
            "context_schema_context_chars_mean",
            report.summary,
        )

    def test_expansion_gain_uses_selected_seeds_not_deeper_diagnostics(self) -> None:
        payload = _json(FIXTURES / "perfect-run.json")
        payload["traces"][0]["seeds"].append(
            {
                "node_id": "db.schema.dimension",
                "card_type": "Table",
                "rank": 3,
                "score": 0.50,
            }
        )
        report = evaluate_run(
            self.dataset,
            RetrievalRun.from_dict(payload),
            generated_at="2026-07-24T00:00:00Z",
        )

        metrics = report.cases["synthetic-join-001"]["metrics"]
        self.assertEqual(metrics["expansion_recall_gain"], 0.5)
        self.assertEqual(metrics["marginal_expansion_precision"], 1)

    def test_seed_at5_uses_actual_selected_ranking(self) -> None:
        payload = _json(FIXTURES / "perfect-run.json")
        payload["traces"][0]["seeds"].append(
            {
                "node_id": "db.schema.dimension",
                "card_type": "Table",
                "rank": 3,
                "score": 0.50,
            }
        )
        payload["traces"][0]["selected_seed_node_ids"] = [
            "db.schema.dimension"
        ]
        report = evaluate_run(
            self.dataset,
            RetrievalRun.from_dict(payload),
            generated_at="2026-07-24T00:00:00Z",
        )

        metrics = report.cases["synthetic-join-001"]["metrics"]
        self.assertEqual(metrics["seed_recall_at_5"], 0)
        self.assertEqual(metrics["seed_recall_at_10"], 1)

    def test_explicit_empty_selected_seeds_does_not_fall_back(self) -> None:
        payload = _json(FIXTURES / "perfect-run.json")
        payload["traces"][0]["selected_seed_node_ids"] = []
        report = evaluate_run(
            self.dataset,
            RetrievalRun.from_dict(payload),
            generated_at="2026-07-24T00:00:00Z",
        )

        metrics = report.cases["synthetic-join-001"]["metrics"]
        self.assertEqual(metrics["expansion_recall_gain"], 1)
        self.assertEqual(metrics["marginal_expansion_precision"], 1)

    def test_gate_passes_identical_report_and_rejects_regressions(self) -> None:
        policy = GatePolicy.from_json_file(POLICY)
        perfect = self._report("perfect-run.json")
        degraded = self._report("degraded-run.json")
        noisy = self._report("noisy-run.json")

        unchanged = compare_reports(
            perfect,
            perfect,
            policy,
            generated_at="2026-07-24T00:00:00Z",
        )
        degraded_gate = compare_reports(
            perfect,
            degraded,
            policy,
            generated_at="2026-07-24T00:00:00Z",
        )
        noisy_gate = compare_reports(
            perfect,
            noisy,
            policy,
            generated_at="2026-07-24T00:00:00Z",
        )

        self.assertTrue(unchanged["passed"])
        self.assertFalse(degraded_gate["passed"])
        self.assertFalse(noisy_gate["passed"])
        self.assertTrue(degraded_gate["violations"])
        self.assertTrue(noisy_gate["violations"])

    def test_gate_refuses_draft_dataset_even_with_identical_scores(self) -> None:
        policy = GatePolicy.from_json_file(POLICY)
        report = self._report("perfect-run.json")
        draft_payload = report.to_dict()
        draft_payload["dataset_status"] = "draft"
        draft_report = EvaluationReport.from_dict(draft_payload)

        result = compare_reports(
            draft_report,
            draft_report,
            policy,
            generated_at="2026-07-24T00:00:00Z",
        )

        self.assertFalse(result["passed"])
        self.assertTrue(
            any("requires 'accepted'" in item for item in result["violations"])
        )

    def test_gate_refuses_different_source_snapshot(self) -> None:
        policy = GatePolicy.from_json_file(POLICY)
        baseline = self._report("perfect-run.json")
        candidate_payload = baseline.to_dict()
        candidate_payload["run_id"] = "same-scores-other-source"
        candidate_payload["run_metadata"]["source_snapshot_hash"] = (
            "synthetic-source-v2"
        )
        candidate = EvaluationReport.from_dict(candidate_payload)

        result = compare_reports(
            baseline,
            candidate,
            policy,
            generated_at="2026-07-24T00:00:00Z",
        )

        self.assertFalse(result["passed"])
        self.assertTrue(
            any(
                "source_snapshot_hash" in item
                for item in result["violations"]
            )
        )


class RetrievalContractTest(unittest.TestCase):
    def test_dataset_rejects_seed_outside_all_gold_alternatives(self) -> None:
        payload = _json(FIXTURES / "synthetic-dataset.json")
        payload["cases"][0]["seed_relevance"]["db.schema.unknown"] = 1

        with self.assertRaisesRegex(
            ContractError,
            "absent from every gold alternative",
        ):
            RetrievalDataset.from_dict(payload)

    def test_trace_rejects_relationship_with_missing_endpoint(self) -> None:
        payload = _json(FIXTURES / "perfect-run.json")
        payload["traces"][0]["final_nodes"] = payload["traces"][0][
            "final_nodes"
        ][:-1]

        with self.assertRaisesRegex(ContractError, "absent from final_nodes"):
            RetrievalRun.from_dict(payload)

    def test_ineligible_relationship_cannot_be_authorized_as_join(self) -> None:
        dataset = RetrievalDataset.from_json_file(
            FIXTURES / "synthetic-dataset.json"
        )
        payload = _json(FIXTURES / "perfect-run.json")
        ineligible = copy.deepcopy(payload)
        relationship = next(
            item
            for item in ineligible["traces"][0]["relationships"]
            if item["id"] == "rel-fact-dimension"
        )
        relationship["sql_join_eligible"] = False
        run = RetrievalRun.from_dict(ineligible)
        report = evaluate_run(
            dataset,
            run,
            generated_at="2026-07-24T00:00:00Z",
        )

        invariant = next(
            item
            for item in report.invariants
            if item.name == "authorized_joins_are_eligible"
        )
        self.assertFalse(invariant.passed)
        metrics = report.cases["synthetic-join-001"]["metrics"]
        self.assertEqual(metrics["join_eligibility_accuracy"], 0)
        self.assertGreater(metrics["unauthorized_join_rate"], 0)

    def test_relationship_direction_is_part_of_the_gold_match(self) -> None:
        dataset = RetrievalDataset.from_json_file(
            FIXTURES / "synthetic-dataset.json"
        )
        payload = _json(FIXTURES / "perfect-run.json")
        relationship = next(
            item
            for item in payload["traces"][0]["relationships"]
            if item["id"] == "rel-fact-dimension"
        )
        relationship["source"], relationship["target"] = (
            relationship["target"],
            relationship["source"],
        )
        run = RetrievalRun.from_dict(payload)
        report = evaluate_run(
            dataset,
            run,
            generated_at="2026-07-24T00:00:00Z",
        )
        metrics = report.cases["synthetic-join-001"]["metrics"]

        self.assertEqual(metrics["eligible_join_relationship_recall"], 0)
        self.assertEqual(metrics["join_path_recall"], 0)
        self.assertEqual(metrics["structural_sufficiency"], 0)


if __name__ == "__main__":
    unittest.main()

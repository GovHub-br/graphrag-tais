"""Regression gate for two immutable retrieval evaluation reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .models import (
    DatasetStatus,
    EvaluationReport,
    GatePolicy,
    MetricDirection,
    MetricRule,
)


DEFAULT_METRIC_DIRECTIONS: dict[str, MetricDirection] = {
    "seed_recall_at_5": MetricDirection.HIGHER,
    "seed_precision_at_5": MetricDirection.HIGHER,
    "seed_hit_at_5": MetricDirection.HIGHER,
    "seed_table_recall_at_5": MetricDirection.HIGHER,
    "seed_column_recall_at_5": MetricDirection.HIGHER,
    "seed_recall_at_10": MetricDirection.HIGHER,
    "seed_precision_at_10": MetricDirection.HIGHER,
    "seed_hit_at_10": MetricDirection.HIGHER,
    "seed_table_recall_at_10": MetricDirection.HIGHER,
    "seed_column_recall_at_10": MetricDirection.HIGHER,
    "mrr_at_10": MetricDirection.HIGHER,
    "ndcg_at_10": MetricDirection.HIGHER,
    "expansion_recall_gain": MetricDirection.HIGHER,
    "marginal_expansion_precision": MetricDirection.HIGHER,
    "node_precision": MetricDirection.HIGHER,
    "node_recall": MetricDirection.HIGHER,
    "node_f1": MetricDirection.HIGHER,
    "table_recall": MetricDirection.HIGHER,
    "column_recall": MetricDirection.HIGHER,
    "relationship_precision": MetricDirection.HIGHER,
    "relationship_recall": MetricDirection.HIGHER,
    "relationship_f1": MetricDirection.HIGHER,
    "eligible_join_relationship_recall": MetricDirection.HIGHER,
    "join_eligibility_accuracy": MetricDirection.HIGHER,
    "unauthorized_join_rate": MetricDirection.LOWER,
    "path_recall": MetricDirection.HIGHER,
    "join_path_recall": MetricDirection.HIGHER,
    "structural_sufficiency": MetricDirection.HIGHER,
    "weighted_dependency_recall": MetricDirection.HIGHER,
    "overexpansion": MetricDirection.LOWER,
    "subgraph_size_ratio": MetricDirection.LOWER,
    "disconnected_rate": MetricDirection.LOWER,
    "latency_p50_ms": MetricDirection.LOWER,
    "latency_p95_ms": MetricDirection.LOWER,
    "error_rate": MetricDirection.LOWER,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _numeric(
    values: Mapping[str, Any],
    metric: str,
) -> float | None:
    value = values.get(metric)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _allowance(baseline: float, rule: MetricRule) -> float:
    return max(
        rule.absolute_tolerance,
        abs(baseline) * rule.relative_tolerance,
    )


def _regressed(
    baseline: float,
    candidate: float,
    rule: MetricRule,
) -> bool:
    allowance = _allowance(baseline, rule)
    if rule.direction is MetricDirection.HIGHER:
        return candidate < baseline - allowance
    return candidate > baseline + allowance


def _check_rule(
    baseline_values: Mapping[str, Any],
    candidate_values: Mapping[str, Any],
    rule: MetricRule,
    *,
    scope: str,
) -> tuple[dict[str, Any], str | None]:
    baseline = _numeric(baseline_values, rule.metric)
    candidate = _numeric(candidate_values, rule.metric)
    check: dict[str, Any] = {
        "scope": scope,
        "metric": rule.metric,
        "direction": rule.direction.value,
        "baseline": baseline,
        "candidate": candidate,
        "delta": (
            candidate - baseline
            if baseline is not None and candidate is not None
            else None
        ),
        "absolute_tolerance": rule.absolute_tolerance,
        "relative_tolerance": rule.relative_tolerance,
        "passed": True,
    }
    if baseline is None or candidate is None:
        check["passed"] = False
        return check, f"{scope}: metric {rule.metric!r} is missing or non-numeric"
    reasons: list[str] = []
    if _regressed(baseline, candidate, rule):
        reasons.append(
            f"regressed from {baseline:.6g} to {candidate:.6g} "
            f"(allowance {_allowance(baseline, rule):.6g})"
        )
    if rule.minimum is not None and candidate < rule.minimum:
        reasons.append(
            f"{candidate:.6g} is below minimum {rule.minimum:.6g}"
        )
    if rule.maximum is not None and candidate > rule.maximum:
        reasons.append(
            f"{candidate:.6g} is above maximum {rule.maximum:.6g}"
        )
    if reasons:
        check["passed"] = False
        return check, f"{scope}: {rule.metric}: " + "; ".join(reasons)
    return check, None


def _report_preconditions(
    baseline: EvaluationReport,
    candidate: EvaluationReport,
    policy: GatePolicy,
) -> list[str]:
    violations: list[str] = []
    if baseline.dataset_name != candidate.dataset_name:
        violations.append(
            "dataset names differ: "
            f"{baseline.dataset_name!r} != {candidate.dataset_name!r}"
        )
    if baseline.dataset_version != candidate.dataset_version:
        violations.append(
            "dataset versions differ: "
            f"{baseline.dataset_version!r} != "
            f"{candidate.dataset_version!r}"
        )
    if baseline.dataset_hash != candidate.dataset_hash:
        violations.append(
            "dataset hashes differ; comparisons across gold revisions "
            "are invalid"
        )
    if policy.require_accepted_dataset:
        for label, report in (
            ("baseline", baseline),
            ("candidate", candidate),
        ):
            if report.dataset_status is not DatasetStatus.ACCEPTED:
                violations.append(
                    f"{label} dataset is {report.dataset_status.value!r}; "
                    "policy requires 'accepted'"
                )
    if policy.reject_errors:
        if baseline.errors:
            violations.append(
                f"baseline contains {len(baseline.errors)} evaluation error(s)"
            )
        if candidate.errors:
            violations.append(
                f"candidate contains {len(candidate.errors)} evaluation error(s)"
            )
    if policy.reject_failed_invariants:
        for label, report in (
            ("baseline", baseline),
            ("candidate", candidate),
        ):
            failed = [
                invariant.name
                for invariant in report.invariants
                if not invariant.passed
            ]
            if failed:
                violations.append(
                    f"{label} has failed invariants: {', '.join(failed)}"
                )
    if set(baseline.cases) != set(candidate.cases):
        violations.append("baseline and candidate case IDs differ")
    for key in policy.required_matching_run_metadata:
        baseline_value = baseline.run_metadata.get(key)
        candidate_value = candidate.run_metadata.get(key)
        if baseline_value is None or candidate_value is None:
            violations.append(
                f"required run metadata {key!r} is missing from baseline "
                "or candidate"
            )
        elif baseline_value != candidate_value:
            violations.append(
                f"required run metadata {key!r} differs: "
                f"{baseline_value!r} != {candidate_value!r}"
            )
    return violations


def _case_metrics(
    report: EvaluationReport,
    case_id: str,
) -> Mapping[str, Any]:
    raw_case = report.cases.get(case_id, {})
    if not isinstance(raw_case, Mapping):
        return {}
    metrics = raw_case.get("metrics", {})
    return metrics if isinstance(metrics, Mapping) else {}


def _critical_case_checks(
    baseline: EvaluationReport,
    candidate: EvaluationReport,
    policy: GatePolicy,
    rule_by_metric: Mapping[str, MetricRule],
) -> tuple[list[dict[str, Any]], list[str]]:
    checks: list[dict[str, Any]] = []
    violations: list[str] = []
    if not policy.reject_critical_regressions:
        return checks, violations
    for case_id in sorted(set(baseline.cases) & set(candidate.cases)):
        candidate_case = candidate.cases[case_id]
        baseline_case = baseline.cases[case_id]
        if (
            not isinstance(candidate_case, Mapping)
            or not isinstance(baseline_case, Mapping)
        ):
            violations.append(f"case {case_id!r} has an invalid report shape")
            continue
        if not (
            bool(candidate_case.get("critical"))
            or bool(baseline_case.get("critical"))
        ):
            continue
        for metric in policy.critical_metrics:
            configured_rule = rule_by_metric.get(metric)
            direction = (
                configured_rule.direction
                if configured_rule is not None
                else DEFAULT_METRIC_DIRECTIONS.get(
                    metric,
                    MetricDirection.HIGHER,
                )
            )
            rule = configured_rule or MetricRule(
                metric=metric,
                direction=direction,
            )
            check, violation = _check_rule(
                _case_metrics(baseline, case_id),
                _case_metrics(candidate, case_id),
                rule,
                scope=f"critical case {case_id}",
            )
            checks.append(check)
            if violation:
                violations.append(violation)
    return checks, violations


def compare_reports(
    baseline: EvaluationReport,
    candidate: EvaluationReport,
    policy: GatePolicy,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Compare immutable reports; this function never writes a baseline."""

    violations = _report_preconditions(baseline, candidate, policy)
    checks: list[dict[str, Any]] = []
    for rule in policy.rules:
        check, violation = _check_rule(
            baseline.summary,
            candidate.summary,
            rule,
            scope="summary",
        )
        checks.append(check)
        if violation:
            violations.append(violation)
        for slice_name in rule.slices:
            baseline_slice = baseline.slices.get(slice_name)
            candidate_slice = candidate.slices.get(slice_name)
            if not isinstance(baseline_slice, Mapping) or not isinstance(
                candidate_slice,
                Mapping,
            ):
                violations.append(
                    f"slice {slice_name!r} required by metric "
                    f"{rule.metric!r} is missing"
                )
                continue
            slice_check, slice_violation = _check_rule(
                baseline_slice,
                candidate_slice,
                rule,
                scope=f"slice {slice_name}",
            )
            checks.append(slice_check)
            if slice_violation:
                violations.append(slice_violation)

    rule_by_metric = {rule.metric: rule for rule in policy.rules}
    critical_checks, critical_violations = _critical_case_checks(
        baseline,
        candidate,
        policy,
        rule_by_metric,
    )
    checks.extend(critical_checks)
    violations.extend(critical_violations)

    passed = not violations
    metadata_keys = sorted(
        set(baseline.run_metadata) | set(candidate.run_metadata)
    )
    metadata_diff = {
        key: {
            "baseline": baseline.run_metadata.get(key),
            "candidate": candidate.run_metadata.get(key),
        }
        for key in metadata_keys
        if baseline.run_metadata.get(key)
        != candidate.run_metadata.get(key)
    }
    return {
        "schema_version": "1",
        "generated_at": generated_at or _utc_now(),
        "policy": policy.to_dict(),
        "baseline": {
            "run_id": baseline.run_id,
            "fingerprint": baseline.fingerprint,
            "dataset_name": baseline.dataset_name,
            "dataset_version": baseline.dataset_version,
            "dataset_hash": baseline.dataset_hash,
            "dataset_status": baseline.dataset_status.value,
            "run_metadata": baseline.run_metadata,
        },
        "candidate": {
            "run_id": candidate.run_id,
            "fingerprint": candidate.fingerprint,
            "dataset_name": candidate.dataset_name,
            "dataset_version": candidate.dataset_version,
            "dataset_hash": candidate.dataset_hash,
            "dataset_status": candidate.dataset_status.value,
            "run_metadata": candidate.run_metadata,
        },
        "run_metadata_diff": metadata_diff,
        "passed": passed,
        "verdict": "passed" if passed else "failed",
        "checks": checks,
        "violations": violations,
    }


# Alias used by CI-oriented call sites.
gate = compare_reports

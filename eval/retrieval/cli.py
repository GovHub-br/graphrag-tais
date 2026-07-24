"""Command-line interface for dataset validation, scoring, and gating."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from .compare import compare_reports
from .models import (
    ContractError,
    EvaluationReport,
    GatePolicy,
    RetrievalDataset,
    RetrievalRun,
)
from .runner import evaluate_run


EXIT_OK = 0
EXIT_GATE_FAILED = 1
EXIT_INVALID_INPUT = 2


def _write_json(value: Any, path: str | None) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )
    if path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered + "\n", encoding="utf-8")
    return rendered


def _write_markdown(rendered: str, path: str | None) -> None:
    if not path:
        return
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered.rstrip() + "\n", encoding="utf-8")


def render_evaluation_markdown(report: EvaluationReport) -> str:
    summary = report.summary
    lines = [
        f"# Retrieval evaluation: {report.run_id}",
        "",
        f"- Dataset: `{report.dataset_name}` `{report.dataset_version}` "
        f"({report.dataset_status.value})",
        f"- Dataset hash: `{report.dataset_hash}`",
        f"- Retrieval fingerprint: `{report.fingerprint}`",
        f"- Cases: {summary.get('case_count', 0)}",
        f"- Error rate: {float(summary.get('error_rate', 0.0)):.4f}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for metric, value in sorted(summary.items()):
        if metric in {"case_count", "successful_case_count"}:
            continue
        if isinstance(value, (int, float)):
            lines.append(f"| `{metric}` | {float(value):.6f} |")
    lines.extend(["", "## Invariants", ""])
    for invariant in report.invariants:
        mark = "PASS" if invariant.passed else "FAIL"
        lines.append(
            f"- **{mark}** `{invariant.name}` — {invariant.detail}"
        )
    lines.extend(["", "## Cases", ""])
    for case_id, case in sorted(report.cases.items()):
        if not isinstance(case, Mapping):
            continue
        metrics = case.get("metrics", {})
        lines.append(
            f"- `{case_id}`"
            f"{' (critical)' if case.get('critical') else ''}: "
            f"gold=`{case.get('chosen_gold_subgraph_id')}`, "
            f"WDR={float(metrics.get('weighted_dependency_recall', 0.0)):.4f}, "
            f"structural={float(metrics.get('structural_sufficiency', 0.0)):.0f}"
            + (f", error={case.get('error')}" if case.get("error") else "")
        )
    if report.errors:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {error}" for error in report.errors)
    return "\n".join(lines)


def render_gate_markdown(result: Mapping[str, Any]) -> str:
    passed = bool(result.get("passed"))
    lines = [
        "# Retrieval regression gate",
        "",
        f"**Verdict: {'PASSED' if passed else 'FAILED'}**",
        "",
        f"- Baseline: `{result.get('baseline', {}).get('run_id', '')}`",
        f"- Candidate: `{result.get('candidate', {}).get('run_id', '')}`",
        "",
        "## Checks",
        "",
        "| Scope | Metric | Baseline | Candidate | Delta | Result |",
        "|---|---|---:|---:|---:|---|",
    ]
    for check in result.get("checks", []):
        lines.append(
            "| {scope} | `{metric}` | {baseline} | {candidate} | "
            "{delta} | {status} |".format(
                scope=check.get("scope", ""),
                metric=check.get("metric", ""),
                baseline=_format_optional(check.get("baseline")),
                candidate=_format_optional(check.get("candidate")),
                delta=_format_optional(check.get("delta")),
                status="PASS" if check.get("passed") else "FAIL",
            )
        )
    violations = result.get("violations", [])
    metadata_diff = result.get("run_metadata_diff", {})
    if isinstance(metadata_diff, Mapping) and metadata_diff:
        lines.extend(["", "## Experimental variables", ""])
        for key, values in sorted(metadata_diff.items()):
            if isinstance(values, Mapping):
                lines.append(
                    f"- `{key}`: `{values.get('baseline')}` → "
                    f"`{values.get('candidate')}`"
                )
    if violations:
        lines.extend(["", "## Violations", ""])
        lines.extend(f"- {violation}" for violation in violations)
    return "\n".join(lines)


def _format_optional(value: Any) -> str:
    return f"{float(value):.6f}" if isinstance(value, (int, float)) else "—"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval.retrieval.cli",
        description="Deterministic offline retrieval evaluation",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate",
        help="validate a versioned retrieval dataset",
    )
    validate.add_argument("--dataset", required=True)
    validate.add_argument("--output-json")

    score = subparsers.add_parser(
        "score",
        help="score a pre-LLM retrieval trace run",
    )
    score.add_argument("--dataset", required=True)
    score.add_argument("--run", required=True)
    score.add_argument("--output-json")
    score.add_argument("--output-md")

    gate = subparsers.add_parser(
        "gate",
        help="compare a candidate report with an immutable baseline",
    )
    gate.add_argument("--baseline", required=True)
    gate.add_argument("--candidate", required=True)
    gate.add_argument("--policy", required=True)
    gate.add_argument("--output-json")
    gate.add_argument("--output-md")
    return parser


def _validate(args: argparse.Namespace) -> int:
    dataset = RetrievalDataset.from_json_file(args.dataset)
    result = {
        "valid": True,
        "dataset_name": dataset.name,
        "dataset_version": dataset.version,
        "dataset_status": dataset.status.value,
        "dataset_hash": dataset.canonical_hash,
        "case_count": len(dataset.cases),
    }
    rendered = _write_json(result, args.output_json)
    print(rendered)
    return EXIT_OK


def _score(args: argparse.Namespace) -> int:
    dataset = RetrievalDataset.from_json_file(args.dataset)
    run = RetrievalRun.from_json_file(args.run)
    report = evaluate_run(dataset, run)
    rendered = _write_json(report.to_dict(), args.output_json)
    _write_markdown(render_evaluation_markdown(report), args.output_md)
    print(rendered)
    return EXIT_OK


def _gate(args: argparse.Namespace) -> int:
    baseline = EvaluationReport.from_json_file(args.baseline)
    candidate = EvaluationReport.from_json_file(args.candidate)
    policy = GatePolicy.from_json_file(args.policy)
    result = compare_reports(baseline, candidate, policy)
    rendered = _write_json(result, args.output_json)
    _write_markdown(render_gate_markdown(result), args.output_md)
    print(rendered)
    return EXIT_OK if result["passed"] else EXIT_GATE_FAILED


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            return _validate(args)
        if args.command == "score":
            return _score(args)
        if args.command == "gate":
            return _gate(args)
        parser.error(f"unknown command: {args.command}")
    except ContractError as exc:
        print(f"contract error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    except OSError as exc:
        print(f"I/O error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    return EXIT_INVALID_INPUT


if __name__ == "__main__":
    raise SystemExit(main())

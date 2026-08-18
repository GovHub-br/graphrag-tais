"""CLI do benchmark do modo Explorar: validate | score | gate.

Separação deliberada entre COLETAR (`eval.explore.live`, precisa dos serviços e
chama o LLM) e PONTUAR (aqui, offline e determinístico). O run guarda a resposta
crua, então uma mudança de métrica pode ser reprocessada sobre runs antigos sem
gastar LLM de novo — e sem a tentação de "rodar até dar certo".

    python -m eval.explore.cli validate --dataset <gold.json>
    python -m eval.explore.cli score --dataset <gold.json> --run <run.json> \
        --output-json <report.json> --output-md <report.md>
    python -m eval.explore.cli gate --candidate <report.json> \
        --policy <policy.json> [--baseline <baseline.json>]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from .metrics import CUTOFFS, aggregate, aggregate_by_slice, percentile, score_case
from .models import ContractError, check_policy, load_dataset, validate_run

EXIT_OK = 0
EXIT_GATE_FAILED = 1
EXIT_INVALID_INPUT = 2


def _read(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(value: Any, path: str | None) -> None:
    if not path:
        return
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_text(text: str, path: str | None) -> None:
    if not path:
        return
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text.rstrip() + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
def build_report(dataset_payload: Mapping, run_payload: Mapping) -> dict:
    dataset = load_dataset(dataset_payload)
    warnings = validate_run(run_payload, dataset)
    gold_by_id = {case.id: case for case in dataset.cases}

    cases: list[dict] = []
    for raw in run_payload.get("cases") or []:
        case = gold_by_id[str(raw["case_id"])]
        answer = raw.get("answer") or {}
        metrics = score_case(answer, case.as_gold())
        cases.append({
            "case_id": case.id,
            "question": case.question,
            "critical": case.critical,
            "slices": case.slices,
            "latency_ms": float(raw.get("latency_ms") or 0.0),
            "error": raw.get("error") or "",
            "metrics": metrics,
        })

    latencies = [c["latency_ms"] for c in cases if c["latency_ms"]]
    summary = aggregate([c["metrics"] for c in cases])
    summary.update(
        case_count=float(len(cases)),
        error_rate=(sum(1.0 for c in cases if c["error"]) / len(cases)) if cases else 0.0,
        latency_p50_ms=percentile(latencies, 0.50),
        latency_p95_ms=percentile(latencies, 0.95),
    )
    return {
        "dataset_name": dataset.name,
        "dataset_version": dataset.version,
        "dataset_status": dataset.status,
        "gate_eligible": dataset.is_gate_eligible,
        "run_id": run_payload.get("run_id", ""),
        "config": run_payload.get("config", {}),
        "warnings": warnings,
        "cases": cases,
        "summary": summary,
        "by_slice": aggregate_by_slice(cases),
    }


def render_markdown(report: Mapping) -> str:
    summary = report["summary"]
    lines = [
        f"# Explorar (Persona 1) — {report.get('run_id') or 'run'}",
        "",
        f"- Dataset: `{report['dataset_name']}` `{report['dataset_version']}` "
        f"({report['dataset_status']})",
        f"- Casos: {int(summary['case_count'])} · erro: {summary['error_rate']:.2%}",
        f"- Latência p50/p95: {summary['latency_p50_ms']:.0f} / "
        f"{summary['latency_p95_ms']:.0f} ms",
        "",
    ]
    if not report.get("gate_eligible"):
        lines += [
            "> ⚠️ **Gold em `draft`** — estes números são diagnóstico, não gate. "
            "Nenhuma decisão de promoção deve se apoiar neles.",
            "",
        ]
    for warning in report.get("warnings") or []:
        lines.append(f"> ⚠️ {warning}")
    if report.get("warnings"):
        lines.append("")

    lines += ["## Ranking das tabelas apresentadas", "",
              "| k | recall | core recall | precision | hit | MRR | nDCG | MAP |",
              "|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for k in CUTOFFS:
        lines.append(
            f"| {k} "
            f"| {summary[f'table_recall_at_{k}']:.3f} "
            f"| {summary[f'core_recall_at_{k}']:.3f} "
            f"| {summary[f'table_precision_at_{k}']:.3f} "
            f"| {summary[f'hit_at_{k}']:.3f} "
            f"| {summary[f'mrr_at_{k}']:.3f} "
            f"| {summary[f'ndcg_at_{k}']:.3f} "
            f"| {summary[f'map_at_{k}']:.3f} |"
        )

    lines += ["", "## Contrato da persona", "", "| métrica | valor |", "|---|--:|"]
    for name in ("catalog_fidelity", "off_catalog_rate", "domain_recall",
                 "domain_precision", "domain_f1", "gap_disclosure",
                 "question_groundedness", "questions_per_table",
                 "sample_sql_present", "sample_sql_grounded", "answered_rate",
                 "listed_ratio"):
        lines.append(f"| `{name}` | {summary[name]:.3f} |")
    lines += [
        "",
        "> `listed_ratio` é a fração do catálogo que a resposta despejou. Recall "
        "alto com `listed_ratio` alto não é cobertura — é listar tudo.",
        "",
    ]

    by_slice = report.get("by_slice") or {}
    if by_slice:
        lines += ["## Por slice", "",
                  "| slice | recall@10 | core@10 | MRR@10 | nDCG@10 | fidelidade |",
                  "|---|--:|--:|--:|--:|--:|"]
        for name, values in by_slice.items():
            lines.append(
                f"| `{name}` | {values['table_recall_at_10']:.3f} "
                f"| {values['core_recall_at_10']:.3f} "
                f"| {values['mrr_at_10']:.3f} | {values['ndcg_at_10']:.3f} "
                f"| {values['catalog_fidelity']:.3f} |"
            )
        lines.append("")

    failures = [
        c for c in report["cases"]
        if c["metrics"].get("core_recall_at_10", 1.0) < 1.0
        or c["metrics"].get("catalog_fidelity", 1.0) < 1.0
    ]
    if failures:
        lines += ["## Casos a investigar", ""]
        for case in failures:
            metrics = case["metrics"]
            detail = []
            if metrics.get("core_recall_at_10", 1.0) < 1.0:
                detail.append(f"core@10={metrics['core_recall_at_10']:.2f}")
            if metrics.get("_off_catalog"):
                detail.append(f"fora do catálogo: {metrics['_off_catalog']}")
            if metrics.get("_gaps_missed"):
                detail.append(f"lacunas não ditas: {metrics['_gaps_missed']}")
            lines.append(f"- `{case['case_id']}` — {'; '.join(detail)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="valida o contrato do gold")
    p_validate.add_argument("--dataset", required=True)

    p_score = sub.add_parser("score", help="pontua um run contra o gold")
    p_score.add_argument("--dataset", required=True)
    p_score.add_argument("--run", required=True)
    p_score.add_argument("--output-json")
    p_score.add_argument("--output-md")

    p_gate = sub.add_parser("gate", help="aplica a política de aceite")
    p_gate.add_argument("--candidate", required=True)
    p_gate.add_argument("--policy", required=True)
    p_gate.add_argument("--baseline")
    p_gate.add_argument("--output-json")

    args = parser.parse_args(argv)

    try:
        if args.command == "validate":
            dataset = load_dataset(_read(args.dataset))
            print(f"ok: {dataset.name} {dataset.version} ({dataset.status}), "
                  f"{len(dataset.cases)} casos")
            if not dataset.is_gate_eligible:
                print("aviso: dataset em `draft` — resultados são diagnósticos")
            return EXIT_OK

        if args.command == "score":
            report = build_report(_read(args.dataset), _read(args.run))
            _write(report, args.output_json)
            markdown = render_markdown(report)
            _write_text(markdown, args.output_md)
            print(markdown)
            return EXIT_OK

        if args.command == "gate":
            candidate = _read(args.candidate)
            baseline = _read(args.baseline)["summary"] if args.baseline else None
            passed, findings = check_policy(
                candidate["summary"], _read(args.policy), baseline
            )
            eligible = candidate.get("gate_eligible", False)
            result = {
                "passed": bool(passed and eligible),
                "policy_passed": passed,
                "gate_eligible": eligible,
                "findings": findings,
            }
            _write(result, args.output_json)
            for finding in findings:
                mark = "ok " if finding["passed"] else "FALHA"
                print(f"  {mark} {finding['check']:<15} {finding['metric']}")
            if not eligible:
                print("gate NÃO conclusivo: gold em `draft` (diagnóstico apenas)")
            return EXIT_OK if result["passed"] else EXIT_GATE_FAILED

    except (ContractError, KeyError, json.JSONDecodeError) as exc:
        print(f"entrada inválida: {type(exc).__name__}: {exc}")
        return EXIT_INVALID_INPUT
    return EXIT_INVALID_INPUT


if __name__ == "__main__":
    raise SystemExit(main())

"""Contratos do benchmark do modo Explorar (Persona 1).

Validação explícita e barata: o gold é escrito à mão por gente de domínio, e o
erro típico (id repetido, nota fora da escala, tabela sem schema) tem de falhar
alto na hora de validar, não virar métrica silenciosamente boa.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

VALID_STATUS = {"draft", "accepted", "deprecated"}
MIN_RELEVANCE, MAX_RELEVANCE = 1, 3


class ContractError(ValueError):
    """Erro de contrato de dataset ou run."""


@dataclass
class ExploreCase:
    id: str
    question: str
    table_relevance: dict[str, float] = field(default_factory=dict)
    expected_domains: list[str] = field(default_factory=list)
    expected_gaps: list[str] = field(default_factory=list)
    slices: list[str] = field(default_factory=list)
    critical: bool = False
    notes: str = ""

    def as_gold(self) -> dict[str, Any]:
        return {
            "table_relevance": self.table_relevance,
            "expected_domains": self.expected_domains,
            "expected_gaps": self.expected_gaps,
        }


@dataclass
class ExploreDataset:
    name: str
    version: str
    status: str
    cases: list[ExploreCase]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_gate_eligible(self) -> bool:
        """Só dataset `accepted` autoriza gate verde.

        Enquanto o gold for `draft`, os números são **diagnóstico** — mesma
        regra do benchmark de retrieval (ADR-0012). Um gold em rascunho que
        reprova ou aprova uma mudança é pior que nenhum: dá autoridade a um
        gabarito que ninguém revisou.
        """
        return self.status == "accepted"


def load_dataset(payload: Mapping[str, Any]) -> ExploreDataset:
    for key in ("name", "version", "status", "cases"):
        if key not in payload:
            raise ContractError(f"dataset sem campo obrigatório: {key}")
    status = str(payload["status"])
    if status not in VALID_STATUS:
        raise ContractError(f"status inválido: {status} (use {sorted(VALID_STATUS)})")

    cases: list[ExploreCase] = []
    seen: set[str] = set()
    for raw in payload["cases"]:
        case_id = str(raw.get("id") or "").strip()
        if not case_id:
            raise ContractError("caso sem `id`")
        if case_id in seen:
            raise ContractError(f"`id` duplicado: {case_id}")
        seen.add(case_id)
        if not str(raw.get("question") or "").strip():
            raise ContractError(f"{case_id}: `question` vazia")

        relevance: dict[str, float] = {}
        for name, grade in (raw.get("table_relevance") or {}).items():
            value = float(grade)
            if not MIN_RELEVANCE <= value <= MAX_RELEVANCE:
                raise ContractError(
                    f"{case_id}: relevância {value} fora da escala "
                    f"{MIN_RELEVANCE}..{MAX_RELEVANCE} em {name}"
                )
            if "." not in name:
                raise ContractError(
                    f"{case_id}: tabela `{name}` sem schema — use `schema.tabela` ou o FQN"
                )
            relevance[name] = value

        domains = [str(d) for d in (raw.get("expected_domains") or [])]
        if not relevance and not domains:
            raise ContractError(
                f"{case_id}: caso sem `table_relevance` e sem `expected_domains` "
                "— não há o que avaliar"
            )
        cases.append(ExploreCase(
            id=case_id,
            question=str(raw["question"]),
            table_relevance=relevance,
            expected_domains=domains,
            expected_gaps=[str(g) for g in (raw.get("expected_gaps") or [])],
            slices=[str(s) for s in (raw.get("slices") or [])],
            critical=bool(raw.get("critical")),
            notes=str(raw.get("notes") or ""),
        ))

    if not cases:
        raise ContractError("dataset sem casos")
    return ExploreDataset(
        name=str(payload["name"]),
        version=str(payload["version"]),
        status=status,
        cases=cases,
        metadata=dict(payload.get("metadata") or {}),
    )


def validate_run(payload: Mapping[str, Any], dataset: ExploreDataset) -> list[str]:
    """Confere o run contra o dataset. Devolve avisos; levanta só no que impede
    pontuar (caso desconhecido, resposta ausente)."""
    warnings: list[str] = []
    if payload.get("dataset_name") and payload["dataset_name"] != dataset.name:
        warnings.append(
            f"run é do dataset `{payload['dataset_name']}`, não de `{dataset.name}`"
        )
    if payload.get("dataset_version") and payload["dataset_version"] != dataset.version:
        warnings.append(
            f"run é da versão `{payload['dataset_version']}`, não de `{dataset.version}`"
        )

    known = {case.id for case in dataset.cases}
    seen = {str(c.get("case_id")) for c in (payload.get("cases") or [])}
    unknown = seen - known
    if unknown:
        raise ContractError(f"run tem casos fora do dataset: {sorted(unknown)}")
    missing = known - seen
    if missing:
        warnings.append(f"casos do dataset ausentes no run: {sorted(missing)}")
    return warnings


def check_policy(
    summary: Mapping[str, float],
    policy: Mapping[str, Any],
    baseline: Mapping[str, float] | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    """Aplica pisos absolutos e regressão máxima contra a baseline.

    Duas checagens porque elas respondem perguntas diferentes: o piso diz "isto é
    aceitável para ir ao usuário", a regressão diz "isto piorou o que já
    funcionava". Uma mudança pode passar no piso e ainda assim ser um retrocesso.
    """
    findings: list[dict[str, Any]] = []
    passed = True

    for name, floor in (policy.get("min") or {}).items():
        value = float(summary.get(name, 0.0))
        ok = value >= float(floor) - 1e-9
        passed = passed and ok
        findings.append({
            "check": "min", "metric": name, "value": round(value, 4),
            "threshold": float(floor), "passed": ok,
        })

    for name, tolerance in (policy.get("max_regression") or {}).items():
        if not baseline or name not in baseline:
            findings.append({
                "check": "max_regression", "metric": name,
                "passed": True, "skipped": "sem baseline",
            })
            continue
        delta = float(summary.get(name, 0.0)) - float(baseline[name])
        ok = delta >= -float(tolerance) - 1e-9
        passed = passed and ok
        findings.append({
            "check": "max_regression", "metric": name, "delta": round(delta, 4),
            "tolerance": float(tolerance), "passed": ok,
        })

    return passed, findings

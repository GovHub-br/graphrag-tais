"""Deterministic, infrastructure-free evaluation of retrieval traces."""

from .compare import compare_reports
from .models import (
    ContractError,
    DatasetStatus,
    EvaluationReport,
    GatePolicy,
    GoldSubgraph,
    GraphNode,
    GraphPath,
    GraphRelationship,
    MetricRule,
    RankedSeed,
    RetrievalCase,
    RetrievalDataset,
    RetrievalRun,
    RetrievalTrace,
)
from .runner import evaluate_run

__all__ = [
    "ContractError",
    "DatasetStatus",
    "EvaluationReport",
    "GatePolicy",
    "GoldSubgraph",
    "GraphNode",
    "GraphPath",
    "GraphRelationship",
    "MetricRule",
    "RankedSeed",
    "RetrievalCase",
    "RetrievalDataset",
    "RetrievalRun",
    "RetrievalTrace",
    "compare_reports",
    "evaluate_run",
]


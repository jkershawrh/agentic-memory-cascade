"""Routing decision audit trail model.

Captures the full provenance of a routing decision for observability:
which model was chosen, why, and what the benchmark grades say about it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .corpora import RubricScorecard


class RoutingDecision(BaseModel):
    """Immutable audit record for a single routing decision."""

    timestamp: str                                      # ISO-8601
    finding_id: str                                     # UUID of the finding being routed
    workload_type: str                                  # e.g. "fraud-triage"
    workload_confidence: float                          # bootstrapper confidence 0..1
    strategy: str                                       # e.g. "routing_ladder_int8"
    strategy_grade: str                                 # overall grade from strategy
    model: str                                          # e.g. "granite-2b-int8"
    serving_layer: str                                  # "ovms" | "vllm" | "llama-cpp"
    dtype: str                                          # "int8" | "bfloat16" | "Q4_K_M" | ...
    optimization: str                                   # "AMX-INT8" | "AMX-BF16" | ...
    tier: Literal["micro", "macro"]                     # model tier
    scorecard: RubricScorecard                          # full benchmark grades
    source: Literal[
        "corpora",
        "fallback_roundrobin",
        "explicit_preference",
    ] = "corpora"

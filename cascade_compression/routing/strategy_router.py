"""Strategy router — resolves workload types to rubric-graded inference strategies.

Each strategy carries optimization flags (use_int8, use_cascade, etc.) and
expected rubric grades derived from benchmark results.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Literal, Optional

import yaml
from pydantic import BaseModel

from ..resources import resource_path

Grade = Literal["green", "yellow", "red"]


class SignalDistribution(BaseModel):
    """Expected distribution of signal types for a workload."""
    alerts: float = 0.0
    logs: float = 0.0
    metrics: float = 0.0
    events: float = 0.0
    transactions: float = 0.0
    audit_logs: float = 0.0
    documents: float = 0.0
    compliance: float = 0.0
    network: float = 0.0


class InferenceStrategy(BaseModel):
    """Rubric-graded inference strategy with optimization flags."""

    name: str = "generic"
    display_name: str = "Generic Fallback"
    workload_type: str = "generic"

    # Rubric grades
    throughput_grade: Grade = "red"
    quality_grade: Grade = "red"
    latency_grade: Grade = "red"
    overall_grade: Grade = "red"

    # Optimization flags
    use_cascade: bool = False
    use_int8: bool = False
    use_speculative: bool = False
    use_batching: bool = False
    use_cache: bool = False
    use_ladder: bool = False


# Default strategy — all red, no optimizations
DEFAULT_STRATEGY = InferenceStrategy()


def _load_strategies(path: Optional[str] = None) -> Dict[str, InferenceStrategy]:
    """Load strategy definitions from strategies.yaml."""
    if path is None:
        path = str(resource_path("config", "strategies.yaml"))

    p = Path(path)
    if not p.exists():
        return {"generic": DEFAULT_STRATEGY}

    with open(p) as f:
        raw = yaml.safe_load(f)

    strategies: Dict[str, InferenceStrategy] = {}
    for key, val in raw.items():
        grades = val.get("grades", {})
        flags = val.get("flags", {})
        strategies[key] = InferenceStrategy(
            name=key,
            display_name=val.get("display_name", key),
            workload_type=val.get("workload_type", key),
            throughput_grade=grades.get("throughput", "red"),
            quality_grade=grades.get("quality", "red"),
            latency_grade=grades.get("latency", "red"),
            overall_grade=grades.get("overall", "red"),
            use_cascade=flags.get("use_cascade", False),
            use_int8=flags.get("use_int8", False),
            use_speculative=flags.get("use_speculative", False),
            use_batching=flags.get("use_batching", False),
            use_cache=flags.get("use_cache", False),
            use_ladder=flags.get("use_ladder", False),
        )

    # Ensure generic always exists
    if "generic" not in strategies:
        strategies["generic"] = DEFAULT_STRATEGY

    return strategies


class StrategyRouter:
    """Resolves workload types to inference strategies.

    Usage::

        router = StrategyRouter()
        strategy = router.resolve_strategy("fraud-triage")
        if strategy.use_int8:
            # pick INT8 model variant
            ...
    """

    def __init__(self, strategies_path: Optional[str] = None):
        self._strategies = _load_strategies(strategies_path)

    def resolve_strategy(self, workload_type: str) -> InferenceStrategy:
        """Return the strategy for the given workload type, or generic fallback."""
        return self._strategies.get(workload_type, self._strategies["generic"])

    @property
    def available_strategies(self) -> Dict[str, InferenceStrategy]:
        """All loaded strategies."""
        return dict(self._strategies)

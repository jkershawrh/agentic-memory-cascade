"""Task mapping — maps deepfield task types to benchmark workload shapes.

Two-level resolution:
1. Industry-specific override from verticals.yaml (e.g. FSI + classify_signal -> fraud-scoring)
2. Generic shape mapping (classify_signal -> classify-short)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ..resources import resource_path

# ---------------------------------------------------------------------------
# Generic task -> benchmark shape mapping
# ---------------------------------------------------------------------------

DEEPFIELD_TASK_TO_BENCHMARK_SHAPE: Dict[str, str] = {
    "classify_signal":           "classify-short",
    "filter_noise":              "classify-short",
    "summarize_finding":         "summarize-long",
    "explain_signal":            "summarize-long",
    "fleet_summary":             "summarize-long",
    "suggest_remediation":       "generate-qa",
    "root_cause_analysis":       "generate-qa",
    "deep_root_cause_analysis":  "generate-qa",
    "cross_cluster_correlation": "generate-qa",
    "correlate_findings":        "extract-medium",
    "capacity_estimate":         "extract-medium",
    # Embedding tasks
    "embed_signal":              "encode-text",
    "embed_document":            "encode-document",
    "semantic_search":           "similarity-search",
}

# ---------------------------------------------------------------------------
# Verticals loader (cached)
# ---------------------------------------------------------------------------

_verticals_cache: Optional[Dict[str, Any]] = None


def _load_verticals(path: Optional[str] = None) -> Dict[str, Any]:
    """Load vertical definitions from YAML (cached)."""
    global _verticals_cache
    if _verticals_cache is not None:
        return _verticals_cache

    if path is None:
        path = str(resource_path("config", "verticals.yaml"))

    p = Path(path)
    if not p.exists():
        _verticals_cache = {}
        return _verticals_cache

    with open(p) as f:
        _verticals_cache = yaml.safe_load(f) or {}

    return _verticals_cache


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_benchmark_task(
    task_type: str,
    industry: str = "basic",
    verticals_path: Optional[str] = None,
) -> str:
    """Resolve a deepfield task type to its benchmark task name.

    Resolution order:
    1. Industry-specific override from verticals.yaml ``task_overrides``
    2. Generic DEEPFIELD_TASK_TO_BENCHMARK_SHAPE mapping
    3. Default: ``classify-short``
    """
    verticals = _load_verticals(verticals_path)

    # Level 1: industry override
    vertical = verticals.get(industry, {})
    overrides = vertical.get("task_overrides", {})
    if task_type in overrides:
        return overrides[task_type]

    # Level 2: generic shape
    return DEEPFIELD_TASK_TO_BENCHMARK_SHAPE.get(task_type, "classify-short")


def get_vertical_sla(
    industry: str,
    verticals_path: Optional[str] = None,
) -> int:
    """Return the latency SLA (ms) for the given industry vertical."""
    verticals = _load_verticals(verticals_path)
    vertical = verticals.get(industry, {})
    return vertical.get("latency_sla_ms", 2000)


def get_vertical_quality_gaps(
    industry: str,
    verticals_path: Optional[str] = None,
) -> List[str]:
    """Return known quality gaps for the given industry vertical."""
    verticals = _load_verticals(verticals_path)
    vertical = verticals.get(industry, {})
    return vertical.get("quality_gaps", [])

"""Routing corpora — benchmark-graded model registry for CPU inference.

Provides typed models for the compiled corpora (benchmark results + rubric grades)
and a lazy-loaded singleton for runtime lookups.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from ..resources import resource_path

# ---------------------------------------------------------------------------
# Grade helpers
# ---------------------------------------------------------------------------
Grade = Literal["green", "yellow", "red"]

_GRADE_RANK = {"green": 0, "yellow": 1, "red": 2}


def _worst_grade(*grades: Grade) -> Grade:
    """Return the worst (highest-severity) grade from the inputs."""
    return max(grades, key=lambda g: _GRADE_RANK.get(g, 2))


# ---------------------------------------------------------------------------
# Core data models
# ---------------------------------------------------------------------------

class ModelConfig(BaseModel):
    """Hardware + serving configuration for a single model."""
    model: str                                         # e.g. "granite-2b-int8"
    params: float                                      # billions of parameters
    serving_layer: Literal["ovms", "vllm", "llama-cpp"]
    dtype: Literal["bfloat16", "int8", "Q4_K_M", "Q8_0", "i2_s"]
    optimization: str = ""                             # e.g. "AMX-INT8", "AMX-BF16"


class RubricScorecard(BaseModel):
    """All 10 benchmark metrics with value + rubric grade."""

    # --- quality ---
    quality_accuracy: float = 0.0
    quality_accuracy_grade: Grade = "red"
    quality_coherence: float = 0.0
    quality_coherence_grade: Grade = "red"
    quality_faithfulness: float = 0.0
    quality_faithfulness_grade: Grade = "red"

    # --- latency ---
    latency_ttft_ms: float = 0.0
    latency_ttft_grade: Grade = "red"
    latency_p95_ms: float = 0.0
    latency_p95_grade: Grade = "red"

    # --- throughput ---
    throughput_tok_s: float = 0.0
    throughput_tok_s_grade: Grade = "red"
    throughput_batch: float = 0.0
    throughput_batch_grade: Grade = "red"

    # --- stability ---
    variance_flagged: bool = False
    variance_grade: Grade = "green"

    # --- efficiency ---
    memory_gb: float = 0.0
    memory_grade: Grade = "green"

    # --- composite ---
    composite_score: float = 0.0
    composite_grade: Grade = "red"

    @property
    def overall_grade(self) -> Grade:
        """Worst of quality_accuracy, latency_p95, and throughput grades."""
        return _worst_grade(
            self.quality_accuracy_grade,
            self.latency_p95_grade,
            self.throughput_tok_s_grade,
        )


class CorporaEntry(BaseModel):
    """One model's benchmark result for a specific (industry, task, tier)."""
    config: ModelConfig
    tier: Literal["micro", "macro"]
    scorecard: RubricScorecard
    fallback: Optional[ModelConfig] = None
    alternatives: List[ModelConfig] = Field(default_factory=list)


class RoutingCorpora(BaseModel):
    """Top-level compiled corpora with strategy-aware lookup."""

    version: str = "1.0.0"
    compiled_at: str = ""
    cluster: str = "xeon-6-bench"
    hardware: str = "Intel Xeon 6 / 128 cores / 256 GB"
    instruction_sets: List[str] = Field(
        default_factory=lambda: ["AMX-INT8", "AMX-BF16", "AVX-512", "VNNI"]
    )
    model_roster: List[str] = Field(default_factory=list)

    # entries[industry][task][tier] → CorporaEntry
    entries: Dict[str, Dict[str, Dict[str, CorporaEntry]]] = Field(default_factory=dict)
    gaps: List[Dict[str, Any]] = Field(default_factory=list)

    def lookup(
        self,
        industry: str,
        task: str,
        tier: str = "micro",
        strategy: Any = None,
        excluded_models: Optional[set[str]] = None,
    ) -> Optional[CorporaEntry]:
        """Find the best corpora entry for (industry, task, tier).

        If *strategy* is provided and has ``use_int8 = True``, prefer INT8
        entries when multiple candidates exist at the same tier.

        If *excluded_models* is provided, entries whose primary model is
        in the set are skipped in favour of fallback / alternatives.
        Returns ``None`` when every candidate is excluded.
        """
        industry_entries = self.entries.get(industry, {})
        task_entries = industry_entries.get(task, {})
        entry = task_entries.get(tier)

        if entry is None:
            # Try fallback to "basic" industry
            basic_entries = self.entries.get("basic", {})
            basic_task = basic_entries.get(task, {})
            entry = basic_task.get(tier)

        if entry is None:
            return None

        # Strategy-aware selection: if strategy prefers INT8, check alternatives
        if strategy and getattr(strategy, "use_int8", False):
            if entry.config.dtype != "int8":
                # Look for an INT8 alternative
                for alt in entry.alternatives:
                    if alt.dtype == "int8":
                        # Swap: make the INT8 model primary
                        entry = CorporaEntry(
                            config=alt,
                            tier=entry.tier,
                            scorecard=entry.scorecard,
                            fallback=entry.config,
                            alternatives=[
                                a for a in entry.alternatives if a.model != alt.model
                            ],
                        )
                        break

        # Excluded-model filtering: try fallback then alternatives
        if excluded_models and entry.config.model in excluded_models:
            if entry.fallback and entry.fallback.model not in excluded_models:
                return CorporaEntry(
                    config=entry.fallback,
                    tier=entry.tier,
                    scorecard=entry.scorecard,
                    fallback=None,
                    alternatives=[
                        a for a in entry.alternatives
                        if a.model not in excluded_models
                    ],
                )
            for alt in entry.alternatives:
                if alt.model not in excluded_models:
                    return CorporaEntry(
                        config=alt,
                        tier=entry.tier,
                        scorecard=entry.scorecard,
                        fallback=None,
                        alternatives=[],
                    )
            return None

        return entry


# ---------------------------------------------------------------------------
# Task-type latency thresholds (milliseconds)
# ---------------------------------------------------------------------------

TASK_LATENCY_THRESHOLDS: Dict[str, Dict[str, int]] = {
    "classify-short": {"green": 200, "yellow": 500},
    "extract-medium": {"green": 2000, "yellow": 5000},
    "summarize-long": {"green": 3000, "yellow": 8000},
    "generate-qa": {"green": 5000, "yellow": 15000},
    # Industry tasks inherit from their shape
    "fraud-scoring": {"green": 200, "yellow": 500},
    "dispute-classification": {"green": 200, "yellow": 500},
    "claims-triage": {"green": 200, "yellow": 500},
    "ticket-routing": {"green": 200, "yellow": 500},
    "product-categorization": {"green": 200, "yellow": 500},
    "clinical-classification": {"green": 500, "yellow": 2000},
    "clinical-summarization": {"green": 3000, "yellow": 8000},
    "medical-ner": {"green": 2000, "yellow": 5000},
    "loan-document-extraction": {"green": 2000, "yellow": 5000},
    "compliance-screening": {"green": 1000, "yellow": 5000},
    "policy-extraction": {"green": 2000, "yellow": 5000},
    "underwriting-risk": {"green": 5000, "yellow": 15000},
    "review-sentiment": {"green": 500, "yellow": 2000},
    "demand-classification": {"green": 200, "yellow": 500},
    "network-anomaly": {"green": 500, "yellow": 2000},
    "churn-prediction": {"green": 5000, "yellow": 15000},
    # Embedding tasks
    "encode-text": {"green": 10, "yellow": 50},
    "encode-document": {"green": 50, "yellow": 200},
    "similarity-search": {"green": 10, "yellow": 50},
}


def grade_task_latency(task: str, latency_p95_ms: float) -> str:
    """Grade a task's P95 latency against its threshold.

    Returns ``"green"``, ``"yellow"``, or ``"red"`` based on the
    task-specific thresholds in :data:`TASK_LATENCY_THRESHOLDS`.
    Unknown tasks fall back to a default of 500 ms (green) / 2000 ms (yellow).
    """
    thresholds = TASK_LATENCY_THRESHOLDS.get(task, {"green": 500, "yellow": 2000})
    if latency_p95_ms <= thresholds["green"]:
        return "green"
    elif latency_p95_ms <= thresholds["yellow"]:
        return "yellow"
    return "red"


# ---------------------------------------------------------------------------
# Model endpoint mapping (benchmark alias → deepfield endpoint name)
# ---------------------------------------------------------------------------

# Task lane classification — determines which model pool to use
TASK_TO_LANE: Dict[str, str] = {
    # Classification lane — fast, sub-200ms, small models with replicas
    "classify-short": "classification",
    "fraud-scoring": "classification",
    "dispute-classification": "classification",
    "claims-triage": "classification",
    "ticket-routing": "classification",
    "product-categorization": "classification",
    "demand-classification": "classification",
    "clinical-classification": "classification",
    # Extraction lane — medium latency, 2-5s, mid-size models
    "extract-medium": "extraction",
    "loan-document-extraction": "extraction",
    "policy-extraction": "extraction",
    "medical-ner": "extraction",
    "review-sentiment": "extraction",
    "network-anomaly": "extraction",
    # Generation lane — medium-high latency, 3-8s, fluent output models
    "summarize-long": "generation",
    "clinical-summarization": "generation",
    "compliance-screening": "generation",
    # Reasoning lane — high latency, 5-18s, large/MoE models
    "generate-qa": "reasoning",
    "underwriting-risk": "reasoning",
    "churn-prediction": "reasoning",
    # Embedding lane — sub-50ms, vector-output models
    "encode-text": "embedding",
    "encode-document": "embedding",
    "similarity-search": "embedding",
}

# Preferred models per lane — benchmark-validated (2026-08-04 shootout)
# phi4-mini: 95% overall, 3.0s avg — best generative all-rounder
# gemma3-4b: 90%, 4.2s — 100% classification accuracy
# mistral-7b: 90%, 6.4s — 100% reasoning, backup
# llama32-1b: 81%, 1.8s — fastest, speed-critical fallback
LANE_MODELS: Dict[str, list] = {
    "classification": ["gemma3-4b", "phi4-mini", "llama32-1b"],
    "extraction": ["phi4-mini", "gemma3-4b", "mistral-7b"],
    "generation": ["phi4-mini", "gemma3-4b", "mistral-7b"],
    "reasoning": ["phi4-mini", "mistral-7b", "gemma3-4b"],
    "embedding": ["nomic-embed-text", "all-minilm-l6-v2", "bge-small-en"],
}


LANE_RESPONSE_FORMAT: Dict[str, Optional[Dict]] = {
    "classification": None,
    "extraction": {"type": "json_object"},
    "generation": None,
    "reasoning": None,
    "embedding": None,
}


def resolve_lane_response_format(task: str) -> Optional[Dict]:
    """Return the response_format config for a task's lane, if any."""
    lane = resolve_lane(task)
    return LANE_RESPONSE_FORMAT.get(lane)


LANE_LATENCY_TARGETS: Dict[str, Dict[str, int]] = {
    "classification": {"green": 3000, "yellow": 5000},
    "extraction": {"green": 4000, "yellow": 8000},
    "generation": {"green": 5000, "yellow": 10000},
    "reasoning": {"green": 8000, "yellow": 20000},
    "embedding": {"green": 10, "yellow": 50},
}


def resolve_lane(task: str) -> str:
    """Determine which model lane a task belongs to."""
    return TASK_TO_LANE.get(task, "classification")


_lane_idx: Dict[str, int] = {}


def resolve_lane_model(task: str, excluded_models: Optional[set] = None) -> Optional[str]:
    """Pick the next available model for a task's lane (round-robin)."""
    lane = resolve_lane(task)
    candidates = LANE_MODELS.get(lane, LANE_MODELS["classification"])
    excluded = excluded_models or set()
    available = [m for m in candidates if m not in excluded]
    if not available:
        return None
    idx = _lane_idx.get(lane, 0)
    model = available[idx % len(available)]
    _lane_idx[lane] = idx + 1
    return model


CORPORA_TO_ENDPOINT: Dict[str, str] = {
    # === Benchmark-validated models (2026-08-04 shootout) ===
    # Primary — phi4-mini: 95% quality, 3.0s, MIT license
    "phi4-mini":                     "phi4_mini_cpu_xeon",
    # Classification lead — gemma3-4b: 100% classify, 4.2s, Apache 2.0
    "gemma3-4b":                     "gemma3_4b_cpu_xeon",
    # Reasoning backup — mistral-7b: 100% reasoning, 6.4s, Apache 2.0
    "mistral-7b":                    "mistral_7b_cpu_xeon",
    # Speed fallback — llama32-1b: 81%, 1.8s
    "llama32-1b":                    "llama32_1b_cpu_xeon",
    # Other benchmarked models
    "smollm3-3b":                    "smollm3_3b_cpu_xeon",
    "gemma3-1b":                     "gemma3_1b_cpu_xeon",
    "qwen25-1.5b":                   "qwen25_15b_cpu_xeon",
    "qwen3-1.7b":                    "qwen3_17b_cpu_xeon",
    "smollm2-360m":                  "smollm2_360m_cpu_xeon",
    "qwen36-moe-35b-a3b":           "qwen36_moe_cpu_xeon",
    # === Legacy models (pre-benchmark, used by compiled corpora.json) ===
    "granite-350m":                  "granite_350m_cpu_xeon",
    "granite-4-0-h-tiny-cpu":        "granite_tiny_cpu_xeon",
    "granite-2b-cpu":                "granite_2b_cpu_xeon",
    "granite-4.1-3b":                "granite_41_3b_cpu_xeon",
    "granite-4.1-8b":                "granite_41_8b_cpu_xeon",
    "qwen25-3b-cpu":                 "qwen25_3b_cpu_xeon",
    "granite-2b-int8":               "granite_2b_int8_xeon",
    "granite-3-2-8b-instruct-cpu":   "granite_8b_cpu_xeon",
    "phi3-mini-cpu":                 "phi3_mini_cpu_xeon",
    "nomic-embed-text":              "nomic_embed_cpu_xeon",
    "all-minilm-l6-v2":             "minilm_embed_cpu_xeon",
    "bge-small-en":                  "bge_embed_cpu_xeon",
}


# ---------------------------------------------------------------------------
# Lazy singleton loader
# ---------------------------------------------------------------------------

_corpora_instance: Optional[RoutingCorpora] = None


def load_corpora(path: Optional[str] = None) -> RoutingCorpora:
    """Load (or return cached) RoutingCorpora from a JSON file.

    Default path: ``<package>/config/corpora.json``
    """
    global _corpora_instance
    if _corpora_instance is not None:
        return _corpora_instance

    if path is None:
        path = str(resource_path("config", "corpora.json"))

    p = Path(path)
    if not p.exists():
        # Return empty corpora when no compiled file exists yet
        _corpora_instance = RoutingCorpora()
        return _corpora_instance

    with open(p) as f:
        data = json.load(f)

    _corpora_instance = RoutingCorpora.model_validate(data)
    return _corpora_instance


def reload_corpora(path: Optional[str] = None) -> RoutingCorpora:
    """Force-reload the corpora singleton (e.g. after recompilation)."""
    global _corpora_instance
    _corpora_instance = None
    return load_corpora(path)

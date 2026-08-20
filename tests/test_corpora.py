"""Tests for cascade_compression.routing.corpora."""

import json
import tempfile
from pathlib import Path

import pytest

from cascade_compression.routing.corpora import (
    CORPORA_TO_ENDPOINT,
    LANE_MODELS,
    TASK_LATENCY_THRESHOLDS,
    TASK_TO_LANE,
    CorporaEntry,
    ModelConfig,
    RoutingCorpora,
    RubricScorecard,
    grade_task_latency,
    load_corpora,
    reload_corpora,
    resolve_lane,
    resolve_lane_model,
)


# ---------------------------------------------------------------------------
# RubricScorecard
# ---------------------------------------------------------------------------


class TestRubricScorecard:
    def test_overall_grade_all_green(self):
        sc = RubricScorecard(
            quality_accuracy_grade="green",
            latency_p95_grade="green",
            throughput_tok_s_grade="green",
        )
        assert sc.overall_grade == "green"

    def test_overall_grade_returns_worst(self):
        sc = RubricScorecard(
            quality_accuracy_grade="green",
            latency_p95_grade="yellow",
            throughput_tok_s_grade="green",
        )
        assert sc.overall_grade == "yellow"

    def test_overall_grade_red_dominates(self):
        sc = RubricScorecard(
            quality_accuracy_grade="green",
            latency_p95_grade="green",
            throughput_tok_s_grade="red",
        )
        assert sc.overall_grade == "red"

    def test_overall_grade_all_red(self):
        sc = RubricScorecard(
            quality_accuracy_grade="red",
            latency_p95_grade="red",
            throughput_tok_s_grade="red",
        )
        assert sc.overall_grade == "red"

    def test_overall_grade_worst_of_quality_latency_throughput(self):
        """overall_grade considers quality_accuracy, latency_p95, and throughput_tok_s."""
        sc = RubricScorecard(
            quality_accuracy_grade="red",
            quality_coherence_grade="green",  # not part of overall
            latency_p95_grade="green",
            throughput_tok_s_grade="green",
        )
        assert sc.overall_grade == "red"


# ---------------------------------------------------------------------------
# CorporaEntry serialization
# ---------------------------------------------------------------------------


class TestCorporaEntry:
    def test_serialization_round_trip(self):
        entry = CorporaEntry(
            config=ModelConfig(
                model="granite-2b-int8",
                params=2.0,
                serving_layer="ovms",
                dtype="int8",
                optimization="AMX-INT8",
            ),
            tier="micro",
            scorecard=RubricScorecard(
                quality_accuracy=0.85,
                quality_accuracy_grade="green",
                latency_p95_ms=450.0,
                latency_p95_grade="green",
                throughput_tok_s=22.0,
                throughput_tok_s_grade="green",
            ),
            fallback=ModelConfig(
                model="phi3-mini-int8",
                params=3.0,
                serving_layer="ovms",
                dtype="int8",
                optimization="AMX-INT8",
            ),
            alternatives=[
                ModelConfig(
                    model="qwen25-3b-int8",
                    params=3.0,
                    serving_layer="ovms",
                    dtype="int8",
                ),
            ],
        )

        data = entry.model_dump()
        restored = CorporaEntry.model_validate(data)

        assert restored.config.model == "granite-2b-int8"
        assert restored.tier == "micro"
        assert restored.scorecard.quality_accuracy == 0.85
        assert restored.fallback is not None
        assert restored.fallback.model == "phi3-mini-int8"
        assert len(restored.alternatives) == 1
        assert restored.alternatives[0].model == "qwen25-3b-int8"

    def test_json_round_trip(self):
        entry = CorporaEntry(
            config=ModelConfig(
                model="test-model",
                params=1.5,
                serving_layer="vllm",
                dtype="bfloat16",
            ),
            tier="micro",
            scorecard=RubricScorecard(),
        )
        json_str = entry.model_dump_json()
        restored = CorporaEntry.model_validate_json(json_str)
        assert restored.config.model == "test-model"


# ---------------------------------------------------------------------------
# RoutingCorpora lookup
# ---------------------------------------------------------------------------


class TestRoutingCorpora:
    @pytest.fixture
    def sample_corpora(self):
        return RoutingCorpora(
            entries={
                "fsi": {
                    "fraud-scoring": {
                        "micro": CorporaEntry(
                            config=ModelConfig(
                                model="granite-2b-int8",
                                params=2.0,
                                serving_layer="ovms",
                                dtype="int8",
                                optimization="AMX-INT8",
                            ),
                            tier="micro",
                            scorecard=RubricScorecard(
                                quality_accuracy_grade="green",
                                latency_p95_grade="green",
                                throughput_tok_s_grade="green",
                            ),
                            alternatives=[
                                ModelConfig(
                                    model="granite-2b-cpu",
                                    params=2.0,
                                    serving_layer="ovms",
                                    dtype="bfloat16",
                                ),
                            ],
                        ),
                        "macro": CorporaEntry(
                            config=ModelConfig(
                                model="phi4-mini-cpu",
                                params=3.8,
                                serving_layer="vllm",
                                dtype="bfloat16",
                            ),
                            tier="macro",
                            scorecard=RubricScorecard(
                                quality_accuracy_grade="green",
                                latency_p95_grade="yellow",
                                throughput_tok_s_grade="green",
                            ),
                            alternatives=[
                                ModelConfig(
                                    model="phi4-mini-int8",
                                    params=3.8,
                                    serving_layer="ovms",
                                    dtype="int8",
                                    optimization="AMX-INT8",
                                ),
                            ],
                        ),
                    },
                },
                "basic": {
                    "classify-short": {
                        "micro": CorporaEntry(
                            config=ModelConfig(
                                model="smollm2-1.7b-int8",
                                params=1.7,
                                serving_layer="ovms",
                                dtype="int8",
                            ),
                            tier="micro",
                            scorecard=RubricScorecard(
                                quality_accuracy_grade="yellow",
                                latency_p95_grade="green",
                                throughput_tok_s_grade="green",
                            ),
                        ),
                    },
                },
            },
        )

    def test_lookup_finds_entry(self, sample_corpora):
        entry = sample_corpora.lookup("fsi", "fraud-scoring", "micro")
        assert entry is not None
        assert entry.config.model == "granite-2b-int8"

    def test_lookup_macro_tier(self, sample_corpora):
        entry = sample_corpora.lookup("fsi", "fraud-scoring", "macro")
        assert entry is not None
        assert entry.config.model == "phi4-mini-cpu"

    def test_lookup_returns_none_for_missing(self, sample_corpora):
        entry = sample_corpora.lookup("fsi", "nonexistent-task", "micro")
        assert entry is None

    def test_lookup_falls_back_to_basic(self, sample_corpora):
        # healthcare doesn't exist, should fall back to basic
        entry = sample_corpora.lookup("healthcare", "classify-short", "micro")
        assert entry is not None
        assert entry.config.model == "smollm2-1.7b-int8"

    def test_strategy_aware_lookup_prefers_int8(self, sample_corpora):
        """When strategy.use_int8=True and primary isn't INT8, swap to INT8 alternative."""

        class MockStrategy:
            use_int8 = True

        entry = sample_corpora.lookup(
            "fsi", "fraud-scoring", "macro",
            strategy=MockStrategy(),
        )
        assert entry is not None
        assert entry.config.model == "phi4-mini-int8"
        assert entry.config.dtype == "int8"

    def test_strategy_aware_lookup_keeps_int8_primary(self, sample_corpora):
        """When primary is already INT8, strategy doesn't change it."""

        class MockStrategy:
            use_int8 = True

        entry = sample_corpora.lookup(
            "fsi", "fraud-scoring", "micro",
            strategy=MockStrategy(),
        )
        assert entry is not None
        assert entry.config.model == "granite-2b-int8"
        assert entry.config.dtype == "int8"


# ---------------------------------------------------------------------------
# CORPORA_TO_ENDPOINT
# ---------------------------------------------------------------------------


class TestCorporaToEndpoint:
    def test_has_models(self):
        assert len(CORPORA_TO_ENDPOINT) >= 10

    def test_known_entries(self):
        assert CORPORA_TO_ENDPOINT["granite-2b-cpu"] == "granite_2b_cpu_xeon"
        assert CORPORA_TO_ENDPOINT["granite-2b-int8"] == "granite_2b_int8_xeon"
        assert CORPORA_TO_ENDPOINT["phi4-mini"] == "phi4_mini_cpu_xeon"

    def test_all_values_end_with_xeon(self):
        for key, val in CORPORA_TO_ENDPOINT.items():
            assert val.endswith("_xeon"), f"{key} -> {val} should end with _xeon"


# ---------------------------------------------------------------------------
# load_corpora
# ---------------------------------------------------------------------------


class TestLoadCorpora:
    def test_load_from_nonexistent_returns_empty(self, tmp_path):
        # Reset singleton
        import cascade_compression.routing.corpora as mod
        mod._corpora_instance = None

        corpora = load_corpora(str(tmp_path / "nonexistent.json"))
        assert isinstance(corpora, RoutingCorpora)
        assert len(corpora.entries) == 0

        # Clean up singleton
        mod._corpora_instance = None

    def test_load_from_file(self, tmp_path):
        import cascade_compression.routing.corpora as mod
        mod._corpora_instance = None

        data = RoutingCorpora(
            version="test",
            model_roster=["test-model"],
        ).model_dump()

        path = tmp_path / "corpora.json"
        path.write_text(json.dumps(data))

        corpora = load_corpora(str(path))
        assert corpora.version == "test"
        assert "test-model" in corpora.model_roster

        mod._corpora_instance = None

    def test_reload_forces_fresh_load(self, tmp_path):
        import cascade_compression.routing.corpora as mod
        mod._corpora_instance = None

        data = RoutingCorpora(version="v1").model_dump()
        path = tmp_path / "corpora.json"
        path.write_text(json.dumps(data))

        c1 = load_corpora(str(path))
        assert c1.version == "v1"

        data["version"] = "v2"
        path.write_text(json.dumps(data))

        c2 = reload_corpora(str(path))
        assert c2.version == "v2"

        mod._corpora_instance = None


# ---------------------------------------------------------------------------
# grade_task_latency
# ---------------------------------------------------------------------------


class TestGradeTaskLatency:
    def test_grade_task_latency(self):
        """classify-short: 100ms = green, 300ms = yellow, 600ms = red."""
        assert grade_task_latency("classify-short", 100) == "green"
        assert grade_task_latency("classify-short", 300) == "yellow"
        assert grade_task_latency("classify-short", 600) == "red"

    def test_grade_task_latency_boundary_green(self):
        """Exactly at the green threshold should be green."""
        assert grade_task_latency("classify-short", 200) == "green"

    def test_grade_task_latency_boundary_yellow(self):
        """Exactly at the yellow threshold should be yellow."""
        assert grade_task_latency("classify-short", 500) == "yellow"

    def test_grade_task_latency_default(self):
        """Unknown task uses default thresholds (green=500, yellow=2000)."""
        assert grade_task_latency("unknown-task", 100) == "green"
        assert grade_task_latency("unknown-task", 500) == "green"
        assert grade_task_latency("unknown-task", 1000) == "yellow"
        assert grade_task_latency("unknown-task", 2000) == "yellow"
        assert grade_task_latency("unknown-task", 3000) == "red"

    def test_task_latency_thresholds_dict(self):
        """TASK_LATENCY_THRESHOLDS should contain expected tasks."""
        assert "classify-short" in TASK_LATENCY_THRESHOLDS
        assert "fraud-scoring" in TASK_LATENCY_THRESHOLDS
        assert "clinical-summarization" in TASK_LATENCY_THRESHOLDS
        assert len(TASK_LATENCY_THRESHOLDS) >= 20

    def test_embedding_task_latency(self):
        """Embedding tasks have sub-50ms green thresholds."""
        assert grade_task_latency("encode-text", 5) == "green"
        assert grade_task_latency("encode-text", 30) == "yellow"
        assert grade_task_latency("encode-text", 100) == "red"
        assert grade_task_latency("encode-document", 40) == "green"
        assert grade_task_latency("similarity-search", 10) == "green"


# ---------------------------------------------------------------------------
# Embedding lane routing
# ---------------------------------------------------------------------------


class TestEmbeddingLane:
    def test_embedding_tasks_resolve_to_embedding_lane(self):
        for task in ("encode-text", "encode-document", "similarity-search"):
            assert resolve_lane(task) == "embedding"

    def test_embedding_lane_has_models(self):
        assert len(LANE_MODELS["embedding"]) >= 2

    def test_resolve_lane_model_returns_embedding_model(self):
        import cascade_compression.routing.corpora as mod
        mod._lane_idx.pop("embedding", None)
        model = resolve_lane_model("encode-text")
        assert model in LANE_MODELS["embedding"]

    def test_resolve_lane_model_round_robins(self):
        import cascade_compression.routing.corpora as mod
        mod._lane_idx.pop("embedding", None)
        models = [resolve_lane_model("encode-text") for _ in range(3)]
        assert len(set(models)) > 1

    def test_resolve_lane_model_excludes(self):
        import cascade_compression.routing.corpora as mod
        mod._lane_idx.pop("embedding", None)
        first = LANE_MODELS["embedding"][0]
        model = resolve_lane_model("encode-text", excluded_models={first})
        assert model != first

    def test_embedding_endpoints_exist(self):
        for model in LANE_MODELS["embedding"]:
            assert model in CORPORA_TO_ENDPOINT, f"Missing endpoint for {model}"

    def test_all_five_lanes_have_models(self):
        for lane in ("classification", "extraction", "generation", "reasoning", "embedding"):
            assert len(LANE_MODELS[lane]) >= 2, f"Lane {lane} needs at least 2 models"

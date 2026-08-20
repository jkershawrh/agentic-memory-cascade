"""Tests for cascade_compression.infra.scaler."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

from cascade_compression.infra.scaler import (
    InferenceScaler,
    ModelFootprint,
    PressureSnapshot,
    PressureThresholds,
    ScalerState,
    estimate_memory_gb,
)
from cascade_compression.routing.corpora import (
    CorporaEntry,
    ModelConfig,
    RoutingCorpora,
    RubricScorecard,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_snapshot(**overrides) -> PressureSnapshot:
    """Return a PressureSnapshot with all zeros unless overridden."""
    defaults = dict(
        timestamp="2026-08-01T00:00:00+00:00",
        cpu_some_pct=0.0,
        cpu_full_pct=0.0,
        memory_some_pct=0.0,
        memory_full_pct=0.0,
        io_some_pct=0.0,
        memory_used_gb=0.0,
        memory_limit_gb=0.0,
        cpu_cores_available=32,
    )
    defaults.update(overrides)
    return PressureSnapshot(**defaults)


def _default_thresholds() -> PressureThresholds:
    return PressureThresholds()


def _small_roster() -> list[ModelFootprint]:
    """Three models of varying size for eviction tests."""
    return [
        ModelFootprint(model="big-8b", params_b=8.0, dtype="bfloat16",
                       serving_layer="ovms", memory_gb=21.0, cpu_cores_estimate=16),
        ModelFootprint(model="mid-2b", params_b=2.0, dtype="int8",
                       serving_layer="ovms", memory_gb=2.6, cpu_cores_estimate=4),
        ModelFootprint(model="tiny-350m", params_b=0.35, dtype="bfloat16",
                       serving_layer="ovms", memory_gb=0.91, cpu_cores_estimate=1),
    ]


def _make_scaler(roster=None, **kwargs) -> InferenceScaler:
    roster = roster or _small_roster()
    kwargs.setdefault("total_memory_gb", 64.0)
    kwargs.setdefault("total_cpu_cores", 32)
    kwargs.setdefault("thresholds", _default_thresholds())
    return InferenceScaler(model_roster=roster, **kwargs)


# ===================================================================
# Rubric grading (5 tests)
# ===================================================================


class TestRubricGrading:
    """Tests 1-5: rubric grading logic."""

    def test_all_green_snapshot(self):
        """1. All-green snapshot -> overall_grade='green', action='restore'."""
        scaler = _make_scaler()
        snap = _make_snapshot()  # all zeros = green for lower-is-better
        state = scaler.observe_pressure(snap)
        assert state.scorecard.overall_grade == "green"
        assert state.scorecard.action == "restore"

    def test_cpu_yellow_zone(self):
        """2. CPU at 20% (yellow zone) -> cpu_some_grade='yellow', action='hold'."""
        scaler = _make_scaler()
        snap = _make_snapshot(cpu_some_pct=20.0)
        state = scaler.observe_pressure(snap)
        assert state.scorecard.cpu_some_grade == "yellow"
        assert state.scorecard.action == "hold"

    def test_memory_used_red(self):
        """3. Memory at 90% (red) -> memory_used_grade='red', action='shed'."""
        scaler = _make_scaler()
        # memory_used_gb / memory_limit_gb = 90 / 100 = 90%
        snap = _make_snapshot(memory_used_gb=90.0, memory_limit_gb=100.0)
        state = scaler.observe_pressure(snap)
        assert state.scorecard.memory_used_grade == "red"
        assert state.scorecard.action in ("shed", "shed_aggressive")

    def test_cpu_red_and_memory_red_shed_aggressive(self):
        """4. CPU red + memory red -> action='shed_aggressive'."""
        scaler = _make_scaler()
        snap = _make_snapshot(
            cpu_some_pct=50.0,           # > 25 = red
            memory_used_gb=90.0,         # 90% = red
            memory_limit_gb=100.0,
        )
        state = scaler.observe_pressure(snap)
        assert state.scorecard.cpu_some_grade == "red"
        assert state.scorecard.memory_used_grade == "red"
        assert state.scorecard.action == "shed_aggressive"

    def test_overall_grade_is_worst(self):
        """5. overall_grade is worst of all grades."""
        scaler = _make_scaler()
        # cpu_some yellow (20%), everything else green
        snap = _make_snapshot(cpu_some_pct=20.0)
        state = scaler.observe_pressure(snap)
        assert state.scorecard.cpu_some_grade == "yellow"
        assert state.scorecard.cpu_full_grade == "green"
        assert state.scorecard.overall_grade == "yellow"


# ===================================================================
# Eviction / restore (7 tests)
# ===================================================================


class TestEvictionRestore:
    """Tests 6-12: eviction ordering, budget accounting, hysteresis."""

    def test_estimate_memory_gb(self):
        """6. estimate_memory_gb: granite-2b bf16 ~5.2, int8 ~2.6, 350m bf16 ~0.91."""
        assert abs(estimate_memory_gb(2.0, "bfloat16") - 5.2) < 0.01
        assert abs(estimate_memory_gb(2.0, "int8") - 2.6) < 0.01
        assert abs(estimate_memory_gb(0.35, "bfloat16") - 0.91) < 0.01

    def test_eviction_order_descending_memory(self):
        """7. Eviction order sorted by memory_gb descending."""
        scaler = _make_scaler()
        state = scaler.current_state()
        assert state.eviction_order == ["big-8b", "mid-2b", "tiny-350m"]

    def test_red_snapshot_evicts_largest(self):
        """8. Feed red snapshot -> largest model evicted, available_models shrinks."""
        scaler = _make_scaler()
        snap = _make_snapshot(cpu_some_pct=50.0)  # red
        state = scaler.observe_pressure(snap)
        assert "big-8b" not in state.available_models
        assert "big-8b" in state.evicted_models
        assert len(state.available_models) == 2

    def test_green_after_eviction_restores_smallest(self):
        """9. Feed green snapshot after eviction -> smallest evicted model restored."""
        # Relax models_available thresholds so having 1/3 loaded is still green
        thresholds = PressureThresholds(
            models_available_green=30.0,
            models_available_yellow=10.0,
        )
        scaler = _make_scaler(thresholds=thresholds)

        # Force-evict two models to have something to restore
        scaler.force_evict("big-8b")
        scaler.force_evict("mid-2b")

        # Simulate enough time for restore hysteresis
        with patch("cascade_compression.infra.scaler.time") as mock_time:
            mock_time.time.return_value = time.time() + 200
            snap = _make_snapshot()  # all green
            state = scaler.observe_pressure(snap)

        # mid-2b was evicted second (last), so it should be restored first
        assert "mid-2b" in state.available_models

    def test_default_availability_thresholds_do_not_trigger_shedding(self):
        """Low availability must request restoration when pressure is green."""
        scaler = _make_scaler()
        scaler.force_evict("big-8b")
        scaler.force_evict("mid-2b")

        state = scaler.observe_pressure(_make_snapshot())

        assert state.scorecard.models_available_grade == "red"
        assert state.scorecard.action == "restore"
        assert "mid-2b" in state.available_models

    def test_yellow_no_action(self):
        """10. Feed yellow snapshot -> no eviction or restoration."""
        scaler = _make_scaler()
        initial_loaded = set(scaler.available_models())

        snap = _make_snapshot(cpu_some_pct=20.0)  # yellow
        state = scaler.observe_pressure(snap)
        assert state.available_models == initial_loaded
        assert state.scorecard.action == "hold"

    def test_hysteresis_prevents_double_eviction(self):
        """11. Two red snapshots within 30s -> only one eviction."""
        scaler = _make_scaler()
        now = time.time()

        with patch("cascade_compression.infra.scaler.time") as mock_time:
            # First red snapshot
            mock_time.time.return_value = now
            snap = _make_snapshot(cpu_some_pct=50.0)
            state1 = scaler.observe_pressure(snap)

            # Second red snapshot 5 seconds later (within 30s hysteresis)
            mock_time.time.return_value = now + 5
            state2 = scaler.observe_pressure(snap)

        # Only the first should have evicted
        assert len(state1.evicted_models) == 1
        assert len(state2.evicted_models) == 1  # still just one

    def test_budget_increases_on_eviction(self):
        """12. Budget accounting: evicting a 21 GB model increases budget.memory_gb by 21."""
        scaler = _make_scaler()
        budget_before = scaler.current_state().budget.memory_gb
        scaler.force_evict("big-8b")
        budget_after = scaler.current_state().budget.memory_gb
        assert abs((budget_after - budget_before) - 21.0) < 0.01


# ===================================================================
# Integration (3 tests)
# ===================================================================


class TestIntegration:
    """Tests 13-15: corpora lookup with excluded models, serialization."""

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
                            fallback=ModelConfig(
                                model="granite-2b-cpu",
                                params=2.0,
                                serving_layer="ovms",
                                dtype="bfloat16",
                            ),
                            alternatives=[
                                ModelConfig(
                                    model="qwen25-3b-cpu",
                                    params=3.0,
                                    serving_layer="ovms",
                                    dtype="bfloat16",
                                ),
                            ],
                        ),
                    },
                },
            },
        )

    def test_excluded_models_returns_fallback(self, sample_corpora):
        """13. Corpora lookup with excluded_models: primary excluded -> returns fallback."""
        entry = sample_corpora.lookup(
            "fsi", "fraud-scoring", "micro",
            excluded_models={"granite-2b-int8"},
        )
        assert entry is not None
        assert entry.config.model == "granite-2b-cpu"

    def test_all_excluded_returns_none(self, sample_corpora):
        """14. Corpora lookup: all models excluded -> returns None."""
        entry = sample_corpora.lookup(
            "fsi", "fraud-scoring", "micro",
            excluded_models={"granite-2b-int8", "granite-2b-cpu", "qwen25-3b-cpu"},
        )
        assert entry is None

    def test_scaler_state_json_round_trip(self):
        """15. ScalerState serialization round-trip through JSON."""
        scaler = _make_scaler()
        state = scaler.current_state()

        json_str = state.model_dump_json()
        data = json.loads(json_str)
        restored = ScalerState.model_validate(data)

        assert set(restored.loaded_models) == set(state.loaded_models)
        assert restored.scorecard.overall_grade == state.scorecard.overall_grade
        assert restored.budget.memory_gb == state.budget.memory_gb
        assert restored.available_models == state.available_models

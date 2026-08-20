"""Tests for cascade_compression.infra.fleet_manager and ModelLifecycleManager."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from cascade_compression.infra.fleet_manager import (
    FleetManager,
    FleetPlan,
    FleetScorecard,
    ModelAllocation,
)
from cascade_compression.infra.scaler import ModelLifecycleManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fleet_manager():
    """FleetManager with default corpora and budget."""
    return FleetManager(memory_budget_gb=64.0, cpu_cores=32)


@pytest.fixture
def small_budget_manager():
    """FleetManager with a small memory budget to test constraints."""
    return FleetManager(memory_budget_gb=4.0, cpu_cores=8)


# ===================================================================
# Test 1: plan() for FSI workload produces allocations with granite-350m
# ===================================================================


class TestFSIPlan:
    def test_fsi_plan_includes_granite_350m(self, fleet_manager):
        """FSI fraud-triage workload should include granite-350m (classification champion)."""
        plan = fleet_manager.plan("fraud-triage")
        model_names = [a.model for a in plan.allocations]
        assert "granite-350m" in model_names, (
            f"Expected granite-350m in FSI plan allocations, got: {model_names}"
        )


# ===================================================================
# Test 2: plan() respects memory budget
# ===================================================================


class TestMemoryBudget:
    def test_total_memory_within_budget(self, fleet_manager):
        """Total memory used must not exceed the memory budget."""
        plan = fleet_manager.plan("fraud-triage")
        assert plan.total_memory_used_gb <= plan.memory_budget_gb, (
            f"Memory used {plan.total_memory_used_gb} GB exceeds budget "
            f"{plan.memory_budget_gb} GB"
        )

    def test_small_budget_still_within_limits(self, small_budget_manager):
        """Even with a tiny budget, memory used must not exceed it."""
        plan = small_budget_manager.plan("infrastructure-monitoring")
        assert plan.total_memory_used_gb <= plan.memory_budget_gb


# ===================================================================
# Test 3: Classification-tier models get more replicas than macro-tier
# ===================================================================


class TestReplicaCounts:
    def test_micro_gets_more_replicas_than_macro(self, fleet_manager):
        """Micro-tier (classification) models should get more replicas than macro."""
        plan = fleet_manager.plan("infrastructure-monitoring")
        micro_allocs = [a for a in plan.allocations if a.tier == "micro"]
        macro_allocs = [a for a in plan.allocations if a.tier == "macro"]

        if micro_allocs and macro_allocs:
            max_micro_replicas = max(a.replicas for a in micro_allocs)
            max_macro_replicas = max(a.replicas for a in macro_allocs)
            assert max_micro_replicas > max_macro_replicas, (
                f"Micro replicas ({max_micro_replicas}) should exceed "
                f"macro replicas ({max_macro_replicas})"
            )
        elif micro_allocs:
            # Only micro models — still valid; micro should have > 1 replica
            assert any(a.replicas > 1 for a in micro_allocs)


# ===================================================================
# Test 4: FleetScorecard grades — coverage
# ===================================================================


class TestCoverageGrading:
    def test_full_coverage_is_green(self):
        """All tiers covered = green."""
        allocations = [
            ModelAllocation(model="m1", tier="micro", replicas=4,
                            memory_per_replica_gb=0.5, total_memory_gb=2.0,
                            estimated_throughput_tok_s=80.0),
            ModelAllocation(model="m2", tier="macro", replicas=1,
                            memory_per_replica_gb=20.0, total_memory_gb=20.0,
                            estimated_throughput_tok_s=25.0),
        ]
        fm = FleetManager(memory_budget_gb=64.0)
        plan = FleetPlan(
            workload_type="test", strategy_name="test",
            memory_budget_gb=64.0, cpu_cores=32,
            allocations=allocations, total_memory_used_gb=22.0,
        )
        sc = fm._grade_plan(plan)
        assert sc.replica_coverage_grade == "green"

    def test_missing_macro_is_yellow(self):
        """Macro missing = yellow."""
        allocations = [
            ModelAllocation(model="m1", tier="micro", replicas=4,
                            memory_per_replica_gb=0.5, total_memory_gb=2.0,
                            estimated_throughput_tok_s=80.0),
        ]
        fm = FleetManager(memory_budget_gb=64.0)
        plan = FleetPlan(
            workload_type="test", strategy_name="test",
            memory_budget_gb=64.0, cpu_cores=32,
            allocations=allocations, total_memory_used_gb=2.0,
        )
        sc = fm._grade_plan(plan)
        assert sc.replica_coverage_grade == "yellow"

    def test_missing_micro_is_red(self):
        """Micro missing = red (only macro present, or no allocations)."""
        allocations = [
            ModelAllocation(model="m2", tier="macro", replicas=1,
                            memory_per_replica_gb=20.0, total_memory_gb=20.0,
                            estimated_throughput_tok_s=25.0),
        ]
        fm = FleetManager(memory_budget_gb=64.0)
        plan = FleetPlan(
            workload_type="test", strategy_name="test",
            memory_budget_gb=64.0, cpu_cores=32,
            allocations=allocations, total_memory_used_gb=20.0,
        )
        sc = fm._grade_plan(plan)
        assert sc.replica_coverage_grade == "red"


# ===================================================================
# Test 5: Memory headroom grading
# ===================================================================


class TestMemoryHeadroomGrading:
    def test_headroom_green(self):
        """>=20% headroom = green."""
        fm = FleetManager(memory_budget_gb=100.0)
        plan = FleetPlan(
            workload_type="test", strategy_name="test",
            memory_budget_gb=100.0, cpu_cores=32,
            allocations=[], total_memory_used_gb=75.0,
        )
        sc = fm._grade_plan(plan)
        assert sc.memory_headroom_pct == 25.0
        assert sc.memory_headroom_grade == "green"

    def test_headroom_yellow(self):
        """>=10% but <20% headroom = yellow."""
        fm = FleetManager(memory_budget_gb=100.0)
        plan = FleetPlan(
            workload_type="test", strategy_name="test",
            memory_budget_gb=100.0, cpu_cores=32,
            allocations=[], total_memory_used_gb=85.0,
        )
        sc = fm._grade_plan(plan)
        assert sc.memory_headroom_pct == 15.0
        assert sc.memory_headroom_grade == "yellow"

    def test_headroom_red(self):
        """<10% headroom = red."""
        fm = FleetManager(memory_budget_gb=100.0)
        plan = FleetPlan(
            workload_type="test", strategy_name="test",
            memory_budget_gb=100.0, cpu_cores=32,
            allocations=[], total_memory_used_gb=95.0,
        )
        sc = fm._grade_plan(plan)
        assert sc.memory_headroom_pct == 5.0
        assert sc.memory_headroom_grade == "red"


# ===================================================================
# Test 6: Aggregate throughput grading
# ===================================================================


class TestThroughputGrading:
    def test_throughput_green(self):
        """>=100 tok/s = green."""
        allocations = [
            ModelAllocation(model="m1", tier="micro", replicas=4,
                            memory_per_replica_gb=0.5, total_memory_gb=2.0,
                            estimated_throughput_tok_s=120.0),
        ]
        fm = FleetManager(memory_budget_gb=64.0)
        plan = FleetPlan(
            workload_type="test", strategy_name="test",
            memory_budget_gb=64.0, cpu_cores=32,
            allocations=allocations, total_memory_used_gb=2.0,
        )
        sc = fm._grade_plan(plan)
        assert sc.aggregate_throughput_tok_s == 120.0
        assert sc.aggregate_throughput_grade == "green"

    def test_throughput_yellow(self):
        """>=50 but <100 tok/s = yellow."""
        allocations = [
            ModelAllocation(model="m1", tier="micro", replicas=2,
                            memory_per_replica_gb=0.5, total_memory_gb=1.0,
                            estimated_throughput_tok_s=75.0),
        ]
        fm = FleetManager(memory_budget_gb=64.0)
        plan = FleetPlan(
            workload_type="test", strategy_name="test",
            memory_budget_gb=64.0, cpu_cores=32,
            allocations=allocations, total_memory_used_gb=1.0,
        )
        sc = fm._grade_plan(plan)
        assert sc.aggregate_throughput_grade == "yellow"

    def test_throughput_red(self):
        """<50 tok/s = red."""
        allocations = [
            ModelAllocation(model="m1", tier="micro", replicas=1,
                            memory_per_replica_gb=0.5, total_memory_gb=0.5,
                            estimated_throughput_tok_s=30.0),
        ]
        fm = FleetManager(memory_budget_gb=64.0)
        plan = FleetPlan(
            workload_type="test", strategy_name="test",
            memory_budget_gb=64.0, cpu_cores=32,
            allocations=allocations, total_memory_used_gb=0.5,
        )
        sc = fm._grade_plan(plan)
        assert sc.aggregate_throughput_grade == "red"


# ===================================================================
# Test 7: apply() with dry_run=True doesn't execute commands
# ===================================================================


class TestApplyDryRun:
    def test_dry_run_does_not_shell_out(self, fleet_manager):
        """apply() with dry_run=True should not call subprocess."""
        plan = fleet_manager.plan("fraud-triage")
        with patch("cascade_compression.infra.fleet_manager.subprocess.run") as mock_run:
            scorecard = fleet_manager.apply(plan, dry_run=True)
            mock_run.assert_not_called()
        assert isinstance(scorecard, FleetScorecard)


# ===================================================================
# Test 8: ModelLifecycleManager._model_to_deployment maps correctly
# ===================================================================


class TestModelToDeployment:
    @pytest.mark.parametrize(
        "model,expected",
        [
            ("granite-350m", "ovms-granite-350m"),
            ("smollm2-360m", "llama-smollm2-360m"),
            ("bitnet-2b", "llama-bitnet-2b"),
            ("granite-2b-int8", "ovms-granite-2b-int8"),
            ("phi4-mini", "llama-phi4-mini"),
            ("granite-4.1-8b", "ovms-granite-41-8b"),
            ("granite-8b-q4", "llama-granite-8b-q4"),
            ("qwen36-moe-35b-a3b", "llama-qwen36-moe"),
        ],
    )
    def test_known_model_mapping(self, model, expected):
        assert ModelLifecycleManager._model_to_deployment(model) == expected

    def test_unknown_model_gets_default(self):
        """Unknown models should get ovms- prefix as fallback."""
        result = ModelLifecycleManager._model_to_deployment("unknown-model-xyz")
        assert result == "ovms-unknown-model-xyz"


# ===================================================================
# Test 9: plan() with "slim" strategy loads only 2 models
# ===================================================================


class TestSlimStrategy:
    def test_slim_plan_loads_two_models(self, fleet_manager):
        """Slim classification strategy should load at most 2 models."""
        plan = fleet_manager.plan("slim-classification")
        assert len(plan.allocations) <= 2, (
            f"Slim strategy should load at most 2 models, got {len(plan.allocations)}"
        )
        assert len(plan.allocations) >= 1, "Slim strategy should load at least 1 model"


# ===================================================================
# Test 10: FleetPlan serialization round-trip
# ===================================================================


class TestSerialization:
    def test_fleet_plan_round_trip(self, fleet_manager):
        """FleetPlan should serialize to JSON and deserialize back identically."""
        plan = fleet_manager.plan("fraud-triage")
        json_str = plan.model_dump_json()
        restored = FleetPlan.model_validate_json(json_str)

        assert restored.workload_type == plan.workload_type
        assert restored.strategy_name == plan.strategy_name
        assert restored.memory_budget_gb == plan.memory_budget_gb
        assert restored.total_memory_used_gb == plan.total_memory_used_gb
        assert len(restored.allocations) == len(plan.allocations)
        for orig, rest in zip(plan.allocations, restored.allocations):
            assert orig.model == rest.model
            assert orig.replicas == rest.replicas
            assert orig.tier == rest.tier
        assert restored.scorecard.overall_grade == plan.scorecard.overall_grade

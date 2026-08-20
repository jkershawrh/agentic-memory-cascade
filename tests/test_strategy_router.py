"""Tests for cascade_compression.routing.strategy_router."""

import tempfile
from pathlib import Path

import pytest
import yaml

from cascade_compression.routing.strategy_router import (
    DEFAULT_STRATEGY,
    InferenceStrategy,
    StrategyRouter,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def router():
    """StrategyRouter loaded from the real config/strategies.yaml."""
    return StrategyRouter()


@pytest.fixture
def custom_router(tmp_path):
    """StrategyRouter loaded from a custom strategies file."""
    strategies = {
        "test-strategy": {
            "display_name": "Test Strategy",
            "workload_type": "test-strategy",
            "grades": {
                "throughput": "green",
                "quality": "green",
                "latency": "yellow",
                "overall": "yellow",
            },
            "flags": {
                "use_cascade": False,
                "use_int8": True,
                "use_speculative": False,
                "use_batching": True,
                "use_cache": True,
                "use_ladder": False,
            },
        },
        "generic": {
            "display_name": "Generic Fallback",
            "workload_type": "generic",
            "grades": {
                "throughput": "red",
                "quality": "red",
                "latency": "red",
                "overall": "red",
            },
            "flags": {
                "use_cascade": False,
                "use_int8": False,
                "use_speculative": False,
                "use_batching": False,
                "use_cache": False,
                "use_ladder": False,
            },
        },
    }
    path = tmp_path / "strategies.yaml"
    path.write_text(yaml.dump(strategies))
    return StrategyRouter(strategies_path=str(path))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStrategyRouter:
    def test_resolves_known_workload(self, router):
        strategy = router.resolve_strategy("fraud-triage")
        assert strategy.name == "fraud-triage"
        assert strategy.workload_type == "fraud-triage"

    def test_resolves_infrastructure_monitoring(self, router):
        strategy = router.resolve_strategy("infrastructure-monitoring")
        assert strategy.name == "infrastructure-monitoring"

    def test_unknown_workload_falls_back_to_generic(self, router):
        strategy = router.resolve_strategy("totally-unknown-workload")
        assert strategy.name == "generic"
        assert strategy.workload_type == "generic"

    def test_strategy_grades_populated(self, router):
        strategy = router.resolve_strategy("fraud-triage")
        assert strategy.throughput_grade in ("green", "yellow", "red")
        assert strategy.quality_grade in ("green", "yellow", "red")
        assert strategy.latency_grade in ("green", "yellow", "red")
        assert strategy.overall_grade in ("green", "yellow", "red")

    def test_fraud_triage_grades(self, router):
        """Fraud-triage should have all-green grades per strategies.yaml."""
        strategy = router.resolve_strategy("fraud-triage")
        assert strategy.throughput_grade == "green"
        assert strategy.quality_grade == "green"
        assert strategy.latency_grade == "green"
        assert strategy.overall_grade == "green"

    def test_strategy_flags(self, router):
        strategy = router.resolve_strategy("fraud-triage")
        assert strategy.use_int8 is True
        assert strategy.use_batching is True
        assert strategy.use_cache is True

    def test_default_strategy_has_red_overall(self):
        assert DEFAULT_STRATEGY.overall_grade == "red"
        assert DEFAULT_STRATEGY.throughput_grade == "red"
        assert DEFAULT_STRATEGY.quality_grade == "red"
        assert DEFAULT_STRATEGY.latency_grade == "red"

    def test_default_strategy_no_flags(self):
        assert DEFAULT_STRATEGY.use_cascade is False
        assert DEFAULT_STRATEGY.use_int8 is False
        assert DEFAULT_STRATEGY.use_speculative is False
        assert DEFAULT_STRATEGY.use_batching is False
        assert DEFAULT_STRATEGY.use_cache is False
        assert DEFAULT_STRATEGY.use_ladder is False


class TestCustomRouter:
    def test_custom_strategy(self, custom_router):
        strategy = custom_router.resolve_strategy("test-strategy")
        assert strategy.name == "test-strategy"
        assert strategy.use_int8 is True
        assert strategy.overall_grade == "yellow"

    def test_custom_generic_fallback(self, custom_router):
        strategy = custom_router.resolve_strategy("nonexistent")
        assert strategy.name == "generic"
        assert strategy.overall_grade == "red"

    def test_available_strategies(self, custom_router):
        strategies = custom_router.available_strategies
        assert "test-strategy" in strategies
        assert "generic" in strategies


class TestAllStrategiesPresent:
    """Verify all 9 expected strategies are loaded from the real config."""

    EXPECTED_STRATEGIES = [
        "fraud-triage",
        "dispute-resolution",
        "loan-document-intake",
        "compliance-screening",
        "infrastructure-monitoring",
        "claims-processing",
        "retail-operations",
        "telecom-operations",
        "generic",
    ]

    def test_all_strategies_present(self, router):
        available = router.available_strategies
        for name in self.EXPECTED_STRATEGIES:
            assert name in available, f"Missing strategy: {name}"

"""Synthetic test: exercise the full routing engine against real benchmark corpora.

Simulates signal flows for each industry vertical and verifies:
1. Bootstrapper classifies workload type correctly
2. Strategy router picks the right inference pattern
3. Model router resolves to benchmark-proven models from corpora.json
4. Rubric grades are propagated through the decision chain
"""

import json
from pathlib import Path

import pytest

from cascade_compression import (
    CORPORA_TO_ENDPOINT,
    RoutingCorpora,
    StrategyRouter,
    WorkloadBootstrapper,
    load_corpora,
    resolve_benchmark_task,
)
from cascade_compression.routing.bootstrapper import WorkloadProfile, _load_profiles
from cascade_compression.routing.corpora import reload_corpora


CORPORA_PATH = str(Path(__file__).parent.parent / "config" / "corpora.json")


@pytest.fixture(autouse=True)
def _reset_state():
    reload_corpora(CORPORA_PATH)
    # Clear cached verticals so updated YAML is loaded
    from cascade_compression.routing import task_mapping
    task_mapping._verticals_cache = None
    yield
    reload_corpora(CORPORA_PATH)
    task_mapping._verticals_cache = None


def _make_signal(signal_type: str, resource_kind: str, severity: str) -> dict:
    return {
        "signal_type": signal_type,
        "resource_kind": resource_kind,
        "severity": severity,
    }


def _make_decisions(failure_class: str = None) -> list:
    d = {"evidence": {}, "outcome": "keep"}
    if failure_class:
        d["evidence"] = {"failure_class": failure_class}
    return [d]


class TestEndToEndFraudTriage:
    """Simulate FSI fraud-triage workload from signal to model selection."""

    def _feed_fraud_signals(self, bootstrapper: WorkloadBootstrapper, n: int = 30):
        signals = [
            ("alert_critical", "Pod", "critical"),
            ("splunk_high_alert", "InferenceService", "high"),
            ("pod_crashloop", "Pod", "high"),
            ("event_backoff", "Pod", "medium"),
            ("kserve_not_ready", "InferenceService", "medium"),
            ("alert_critical", "Pod", "critical"),
            ("splunk_high_alert", "Route", "high"),
            ("pod_crashloop", "Pod", "medium"),
            ("event_backoff", "Pod", "low"),
            ("alert_critical", "Node", "high"),
        ]
        for i in range(n):
            sig = signals[i % len(signals)]
            bootstrapper.observe(
                _make_signal(*sig),
                _make_decisions("pods_crashlooping" if "crash" in sig[0] else None),
            )

    def test_bootstrapper_detects_fraud_triage(self):
        bootstrapper = WorkloadBootstrapper()
        self._feed_fraud_signals(bootstrapper, 30)
        wt, conf = bootstrapper.current_workload()
        assert wt == "fraud-triage"
        assert conf > 0.5

    def test_strategy_is_routing_ladder_int8(self):
        router = StrategyRouter()
        strategy = router.resolve_strategy("fraud-triage")
        assert strategy.name == "fraud-triage"
        assert strategy.use_int8 is True
        assert strategy.overall_grade == "green"

    def test_corpora_routes_classify_to_best_model(self):
        corpora = load_corpora(CORPORA_PATH)
        task = resolve_benchmark_task("classify_signal", "fsi")
        assert task == "fraud-scoring"

        entry = corpora.lookup("fsi", "fraud-scoring", "micro")
        assert entry is not None
        assert entry.scorecard.quality_accuracy_grade == "green"
        assert entry.config.model in corpora.model_roster

    def test_corpora_routes_dispute_classification(self):
        corpora = load_corpora(CORPORA_PATH)
        task = resolve_benchmark_task("correlate_findings", "fsi")
        assert task == "dispute-classification"

        entry = corpora.lookup("fsi", "dispute-classification", "micro")
        assert entry is not None
        assert entry.scorecard.quality_accuracy_grade == "green"

    def test_full_pipeline_fsi(self):
        """Full pipeline: bootstrap → strategy → model resolution."""
        bootstrapper = WorkloadBootstrapper()
        self._feed_fraud_signals(bootstrapper, 30)

        wt, conf = bootstrapper.current_workload()
        assert wt == "fraud-triage"

        strategy_router = StrategyRouter()
        strategy = strategy_router.resolve_strategy(wt)
        assert strategy.overall_grade == "green"

        corpora = load_corpora(CORPORA_PATH)
        benchmark_task = resolve_benchmark_task("classify_signal", "fsi")
        entry = corpora.lookup("fsi", benchmark_task, "micro", strategy=strategy)
        assert entry is not None
        assert entry.scorecard.quality_accuracy_grade != "red"

        endpoint = CORPORA_TO_ENDPOINT.get(entry.config.model)
        assert endpoint is not None, f"No endpoint mapping for {entry.config.model}"


class TestEndToEndInfraMonitoring:
    """Simulate basic infrastructure monitoring workload."""

    def _feed_infra_signals(self, bootstrapper: WorkloadBootstrapper, n: int = 40):
        """Feed signals characteristic of infrastructure monitoring.
        Heavy on node_pressure, pvc_pending, pod_pending — signals
        that distinguish infra from app-level workloads.
        """
        signals = [
            ("pod_crashloop", "Pod", "high"),
            ("node_pressure", "Node", "critical"),
            ("pvc_pending", "PersistentVolumeClaim", "medium"),
            ("route_unhealthy", "Route", "high"),
            ("event_backoff", "Pod", "medium"),
            ("pod_pending", "Pod", "low"),
            ("node_pressure", "Node", "high"),
            ("pvc_pending", "PersistentVolumeClaim", "low"),
            ("node_pressure", "Node", "medium"),
            ("pod_crashloop", "Pod", "medium"),
        ]
        for i in range(n):
            sig = signals[i % len(signals)]
            fc = None
            if sig[0] == "pod_crashloop":
                fc = "pods_crashlooping"
            elif sig[0] == "node_pressure":
                fc = "node_pressure"
            elif sig[0] == "pvc_pending":
                fc = "pvc_binding_failed"
            elif sig[0] == "route_unhealthy":
                fc = "image_pull_backoff"
            bootstrapper.observe(_make_signal(*sig), _make_decisions(fc))

    def test_bootstrapper_detects_infra(self):
        bootstrapper = WorkloadBootstrapper()
        self._feed_infra_signals(bootstrapper, 30)
        wt, conf = bootstrapper.current_workload()
        assert wt == "infrastructure-monitoring"
        assert conf > 0.5

    def test_strategy_for_infra(self):
        router = StrategyRouter()
        strategy = router.resolve_strategy("infrastructure-monitoring")
        assert strategy.name == "infrastructure-monitoring"
        assert strategy.overall_grade in ("green", "yellow")

    def test_corpora_classify_basic(self):
        corpora = load_corpora(CORPORA_PATH)
        task = resolve_benchmark_task("classify_signal", "basic")
        assert task == "classify-short"

        entry = corpora.lookup("basic", "classify-short", "micro")
        assert entry is not None
        assert entry.config.model == "smollm2-360m"
        assert entry.scorecard.throughput_tok_s_grade == "green"


class TestEndToEndRetail:
    """Simulate retail operations workload."""

    def _feed_retail_signals(self, bootstrapper: WorkloadBootstrapper, n: int = 30):
        signals = [
            ("pod_crashloop", "Pod", "high"),
            ("route_unhealthy", "Route", "high"),
            ("event_backoff", "Pod", "medium"),
            ("alert_warning", "Pod", "low"),
            ("kafka_lag_high", "KafkaTopic", "medium"),
            ("pod_crashloop", "Pod", "medium"),
            ("route_unhealthy", "Route", "medium"),
            ("event_backoff", "Pod", "low"),
            ("alert_warning", "Pod", "info"),
            ("kafka_lag_high", "KafkaTopic", "low"),
        ]
        for i in range(n):
            sig = signals[i % len(signals)]
            bootstrapper.observe(_make_signal(*sig), _make_decisions())

    def test_bootstrapper_detects_retail(self):
        bootstrapper = WorkloadBootstrapper()
        self._feed_retail_signals(bootstrapper, 30)
        wt, conf = bootstrapper.current_workload()
        assert wt == "retail-operations"
        assert conf > 0.3

    def test_corpora_routes_product_categorization(self):
        corpora = load_corpora(CORPORA_PATH)
        task = resolve_benchmark_task("classify_signal", "retail")
        assert task == "product-categorization"

        entry = corpora.lookup("retail", "product-categorization", "micro")
        assert entry is not None
        assert entry.scorecard.quality_accuracy_grade == "green"


class TestEndToEndTelecom:
    def test_corpora_routes_ticket_routing(self):
        corpora = load_corpora(CORPORA_PATH)
        task = resolve_benchmark_task("classify_signal", "telecom")
        assert task == "ticket-routing"

        entry = corpora.lookup("telecom", "ticket-routing", "micro")
        assert entry is not None
        assert entry.scorecard.quality_accuracy_grade == "green"


class TestEndToEndInsurance:
    def test_corpora_routes_claims_triage(self):
        corpora = load_corpora(CORPORA_PATH)
        task = resolve_benchmark_task("classify_signal", "insurance")
        assert task == "claims-triage"

        entry = corpora.lookup("insurance", "claims-triage", "micro")
        assert entry is not None
        assert entry.scorecard.quality_accuracy_grade == "green"


class TestQualityGaps:
    """Verify the routing engine correctly handles known quality gaps."""

    def test_generate_qa_has_no_entry(self):
        corpora = load_corpora(CORPORA_PATH)
        entry = corpora.lookup("basic", "generate-qa", "micro")
        assert entry is None

    def test_compliance_screening_gap(self):
        corpora = load_corpora(CORPORA_PATH)
        entry = corpora.lookup("fsi", "compliance-screening", "micro")
        assert entry is None

    def test_clinical_classification_gap(self):
        corpora = load_corpora(CORPORA_PATH)
        entry = corpora.lookup("healthcare", "clinical-classification", "micro")
        assert entry is None

    def test_gaps_are_documented(self):
        corpora = load_corpora(CORPORA_PATH)
        gap_keys = {(g["industry"], g["task"], g["tier"]) for g in corpora.gaps}
        assert ("basic", "generate-qa", "micro") in gap_keys
        assert ("fsi", "compliance-screening", "micro") in gap_keys
        assert ("healthcare", "clinical-classification", "micro") in gap_keys


class TestFallbackBehavior:
    """Verify fallback when corpora has no entry."""

    def test_unknown_industry_falls_back_to_basic(self):
        corpora = load_corpora(CORPORA_PATH)
        entry = corpora.lookup("automotive", "classify-short", "micro")
        # Should fall back to basic industry
        assert entry is not None
        assert entry.config.model == "smollm2-360m"

    def test_unknown_task_returns_none(self):
        corpora = load_corpora(CORPORA_PATH)
        entry = corpora.lookup("basic", "nonexistent-task", "micro")
        assert entry is None

    def test_generic_strategy_has_red_grades(self):
        router = StrategyRouter()
        strategy = router.resolve_strategy("unknown-workload")
        assert strategy.name == "generic"
        assert strategy.overall_grade == "red"


class TestEndpointMappingCompleteness:
    """Verify every model in the corpora has an endpoint mapping."""

    def test_all_corpora_models_have_endpoints(self):
        corpora = load_corpora(CORPORA_PATH)
        missing = []
        for industry, tasks in corpora.entries.items():
            for task, tiers in tasks.items():
                for tier, entry in tiers.items():
                    model = entry.config.model
                    if model not in CORPORA_TO_ENDPOINT:
                        missing.append(f"{industry}/{task}/{tier}: {model}")
                    if entry.fallback and entry.fallback.model not in CORPORA_TO_ENDPOINT:
                        missing.append(f"{industry}/{task}/{tier} fallback: {entry.fallback.model}")
        assert not missing, f"Models without endpoints: {missing}"

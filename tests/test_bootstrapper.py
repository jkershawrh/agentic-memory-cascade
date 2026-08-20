"""Tests for cascade_compression.routing.bootstrapper."""

import math

import pytest

from cascade_compression.routing.bootstrapper import (
    MIN_SAMPLES,
    ClassificationScorecard,
    WorkloadBootstrapper,
    WorkloadFingerprint,
    WorkloadProfile,
    _cosine_similarity,
)


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert abs(_cosine_similarity(v, v) - 1.0) < 1e-9

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine_similarity(a, b)) < 1e-9

    def test_zero_vector(self):
        a = [0.0, 0.0]
        b = [1.0, 2.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_different_length_vectors(self):
        a = [1.0, 0.0, 0.0]
        b = [1.0]
        # b is zero-padded to [1.0, 0.0, 0.0] -> cosine = 1.0
        assert abs(_cosine_similarity(a, b) - 1.0) < 1e-9

    def test_known_value(self):
        a = [1.0, 2.0, 3.0]
        b = [4.0, 5.0, 6.0]
        dot = 1*4 + 2*5 + 3*6  # 32
        mag_a = math.sqrt(14)
        mag_b = math.sqrt(77)
        expected = dot / (mag_a * mag_b)
        assert abs(_cosine_similarity(a, b) - expected) < 1e-9


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


class TestWorkloadFingerprint:
    def test_to_vector(self):
        fp = WorkloadFingerprint(
            signal_type_weights={"a": 0.5, "b": 0.3},
            resource_kind_weights={"pod": 0.7},
            severity_distribution={"high": 0.6},
            failure_class_weights={"crash": 0.4},
        )
        vec = fp.to_vector()
        # Keys are sorted: a=0.5, b=0.3, pod=0.7, high=0.6, crash=0.4
        assert vec == [0.5, 0.3, 0.7, 0.6, 0.4]

    def test_empty_fingerprint(self):
        fp = WorkloadFingerprint()
        assert fp.to_vector() == []


# ---------------------------------------------------------------------------
# Bootstrapper — fraud signals
# ---------------------------------------------------------------------------


class TestBootstrapperFraud:
    def _fraud_signals(self):
        """Return a list of signals matching the fraud-triage fingerprint distribution."""
        return [
            {"signal_type": "alert_critical",   "severity": "critical", "resource_kind": "Pod",              "failure_class": "pods_crashlooping"},
            {"signal_type": "alert_critical",   "severity": "high",     "resource_kind": "Pod",              "failure_class": "pods_crashlooping"},
            {"signal_type": "alert_critical",   "severity": "medium",   "resource_kind": "Pod",              "failure_class": "oom_killed"},
            {"signal_type": "splunk_high_alert", "severity": "high",    "resource_kind": "Pod",              "failure_class": "pods_crashlooping"},
            {"signal_type": "splunk_high_alert", "severity": "medium",  "resource_kind": "InferenceService", "failure_class": "oom_killed"},
            {"signal_type": "pod_crashloop",    "severity": "low",      "resource_kind": "InferenceService", "failure_class": "readiness_probe_failed"},
            {"signal_type": "event_backoff",    "severity": "info",     "resource_kind": "Route",            "failure_class": "pods_crashlooping"},
            {"signal_type": "kserve_not_ready", "severity": "medium",   "resource_kind": "Route",            "failure_class": "readiness_probe_failed"},
            {"signal_type": "alert_critical",   "severity": "low",      "resource_kind": "Node",             "failure_class": "oom_killed"},
            {"signal_type": "splunk_high_alert", "severity": "info",    "resource_kind": "Pod",              "failure_class": "pods_crashlooping"},
        ]

    def test_classifies_fraud_signals(self):
        bs = WorkloadBootstrapper()
        signals = self._fraud_signals()
        for i in range(30):
            bs.observe(signals[i % len(signals)])

        wtype, confidence = bs.current_workload()
        assert wtype == "fraud-triage"
        assert confidence > 0.5

    def test_fraud_industry_is_fsi(self):
        bs = WorkloadBootstrapper()
        signals = self._fraud_signals()
        for i in range(30):
            bs.observe(signals[i % len(signals)])

        assert bs.current_industry() == "fsi"


# ---------------------------------------------------------------------------
# Bootstrapper — infra signals
# ---------------------------------------------------------------------------


class TestBootstrapperInfra:
    def _infra_signals(self):
        """Return a list of signals matching the infrastructure-monitoring fingerprint."""
        return [
            {"signal_type": "pod_crashloop",   "severity": "info",     "resource_kind": "Pod",                  "failure_class": "pods_crashlooping"},
            {"signal_type": "pod_crashloop",   "severity": "low",      "resource_kind": "Pod",                  "failure_class": "image_pull_backoff"},
            {"signal_type": "node_pressure",   "severity": "low",      "resource_kind": "Node",                 "failure_class": "node_pressure"},
            {"signal_type": "node_pressure",   "severity": "info",     "resource_kind": "Node",                 "failure_class": "node_pressure"},
            {"signal_type": "pvc_pending",     "severity": "medium",   "resource_kind": "PersistentVolumeClaim", "failure_class": "pvc_binding_failed"},
            {"signal_type": "route_unhealthy", "severity": "info",     "resource_kind": "Route",                "failure_class": "scheduling_failed"},
            {"signal_type": "event_backoff",   "severity": "low",      "resource_kind": "Pod",                  "failure_class": "image_pull_backoff"},
            {"signal_type": "pod_pending",     "severity": "medium",   "resource_kind": "PersistentVolumeClaim", "failure_class": "scheduling_failed"},
            {"signal_type": "pod_crashloop",   "severity": "high",     "resource_kind": "Pod",                  "failure_class": "pods_crashlooping"},
            {"signal_type": "node_pressure",   "severity": "critical", "resource_kind": "Node",                 "failure_class": "node_pressure"},
        ]

    def test_classifies_infra_signals(self):
        bs = WorkloadBootstrapper()
        signals = self._infra_signals()
        for i in range(30):
            bs.observe(signals[i % len(signals)])

        wtype, confidence = bs.current_workload()
        assert wtype == "infrastructure-monitoring"
        assert confidence > 0.5

    def test_infra_industry_is_basic(self):
        bs = WorkloadBootstrapper()
        signals = self._infra_signals()
        for i in range(30):
            bs.observe(signals[i % len(signals)])

        assert bs.current_industry() == "basic"


# ---------------------------------------------------------------------------
# Bootstrapper — edge cases
# ---------------------------------------------------------------------------


class TestBootstrapperEdgeCases:
    def test_empty_window_returns_generic(self):
        bs = WorkloadBootstrapper()
        wtype, confidence = bs.current_workload()
        assert wtype == "generic"
        assert confidence == 0.0

    def test_empty_window_industry_is_basic(self):
        bs = WorkloadBootstrapper()
        assert bs.current_industry() == "basic"

    def test_minimum_sample_threshold(self):
        bs = WorkloadBootstrapper()
        # Add fewer than MIN_SAMPLES
        for _ in range(MIN_SAMPLES - 1):
            bs.observe({
                "signal_type": "transaction_alert",
                "severity": "high",
                "resource_kind": "transaction",
                "namespace": "fraud-prod",
                "failure_class": "anomalous_transaction",
            })

        wtype, confidence = bs.current_workload()
        assert wtype == "generic"
        assert confidence == 0.0

    def test_classification_after_min_samples(self):
        bs = WorkloadBootstrapper()
        for _ in range(MIN_SAMPLES + 5):
            bs.observe({
                "signal_type": "transaction_alert",
                "severity": "high",
                "resource_kind": "transaction",
                "namespace": "fraud-prod",
                "failure_class": "anomalous_transaction",
            })

        wtype, confidence = bs.current_workload()
        assert wtype != "generic"
        assert confidence > 0.0

    def test_accepts_dict_signals(self):
        """Bootstrapper should accept plain dicts, not require domain models."""
        bs = WorkloadBootstrapper()
        signal = {"signal_type": "alert", "severity": "low"}
        # Should not raise
        bs.observe(signal, decisions=[{"outcome": "keep"}])

    def test_accepts_minimal_dict(self):
        """Even an empty dict should work (all fields have defaults)."""
        bs = WorkloadBootstrapper()
        bs.observe({})
        assert len(bs._window) == 1


# ---------------------------------------------------------------------------
# Namespace pattern matching
# ---------------------------------------------------------------------------


class TestNamespacePatternMatching:
    def test_namespace_pattern_boosts_confidence(self):
        """Signals with namespace 'fraud-prod' should boost fraud-triage confidence."""
        # Without namespace
        bs_no_ns = WorkloadBootstrapper()
        fraud_signals_no_ns = [
            {
                "signal_type": "alert_critical",
                "severity": "critical",
                "resource_kind": "Pod",
                "failure_class": "pods_crashlooping",
            },
            {
                "signal_type": "splunk_high_alert",
                "severity": "high",
                "resource_kind": "Pod",
                "failure_class": "oom_killed",
            },
        ]
        for i in range(30):
            bs_no_ns.observe(fraud_signals_no_ns[i % len(fraud_signals_no_ns)])

        _, conf_no_ns = bs_no_ns.current_workload()

        # With namespace matching fraud-triage's "fraud-*" pattern
        bs_ns = WorkloadBootstrapper()
        fraud_signals_ns = [
            {
                "signal_type": "alert_critical",
                "severity": "critical",
                "resource_kind": "Pod",
                "namespace": "fraud-prod",
                "failure_class": "pods_crashlooping",
            },
            {
                "signal_type": "splunk_high_alert",
                "severity": "high",
                "resource_kind": "Pod",
                "namespace": "fraud-prod",
                "failure_class": "oom_killed",
            },
        ]
        for i in range(30):
            bs_ns.observe(fraud_signals_ns[i % len(fraud_signals_ns)])

        wtype, conf_ns = bs_ns.current_workload()
        assert wtype == "fraud-triage"
        assert conf_ns > conf_no_ns


# ---------------------------------------------------------------------------
# Exponential decay
# ---------------------------------------------------------------------------


class TestExponentialDecay:
    def test_exponential_decay_responds_faster(self):
        """Feed 100 infra signals then 30 fraud signals.

        With decay (default 0.95) the bootstrapper should reclassify to
        fraud-triage.  Without decay (factor=1.0) infra signals still
        dominate because all 130 samples are weighted equally.
        """
        infra_signals = [
            {
                "signal_type": "pod_crashloop",
                "severity": "info",
                "resource_kind": "Pod",
                "failure_class": "pods_crashlooping",
            },
            {
                "signal_type": "node_pressure",
                "severity": "low",
                "resource_kind": "Node",
                "failure_class": "node_pressure",
            },
            {
                "signal_type": "pvc_pending",
                "severity": "medium",
                "resource_kind": "PersistentVolumeClaim",
                "failure_class": "pvc_binding_failed",
            },
            {
                "signal_type": "event_backoff",
                "severity": "low",
                "resource_kind": "Pod",
                "failure_class": "image_pull_backoff",
            },
        ]
        fraud_signals = [
            {
                "signal_type": "alert_critical",
                "severity": "critical",
                "resource_kind": "Pod",
                "failure_class": "pods_crashlooping",
            },
            {
                "signal_type": "splunk_high_alert",
                "severity": "high",
                "resource_kind": "Pod",
                "failure_class": "oom_killed",
            },
            {
                "signal_type": "alert_critical",
                "severity": "high",
                "resource_kind": "InferenceService",
                "failure_class": "pods_crashlooping",
            },
            {
                "signal_type": "kserve_not_ready",
                "severity": "medium",
                "resource_kind": "Route",
                "failure_class": "readiness_probe_failed",
            },
        ]

        # With decay (default 0.95) — recent fraud signals dominate
        bs_decay = WorkloadBootstrapper()
        for i in range(100):
            bs_decay.observe(infra_signals[i % len(infra_signals)])
        for i in range(30):
            bs_decay.observe(fraud_signals[i % len(fraud_signals)])

        wtype_decay, _ = bs_decay.current_workload()

        # Without decay (factor=1.0) — all observations weighted equally
        bs_no_decay = WorkloadBootstrapper(decay_factor=1.0)
        for i in range(100):
            bs_no_decay.observe(infra_signals[i % len(infra_signals)])
        for i in range(30):
            bs_no_decay.observe(fraud_signals[i % len(fraud_signals)])

        wtype_no_decay, _ = bs_no_decay.current_workload()

        # With decay the bootstrapper should have shifted to fraud-triage
        assert wtype_decay == "fraud-triage"
        # Without decay infra signals still dominate
        assert wtype_no_decay != "fraud-triage"


# ---------------------------------------------------------------------------
# Adaptive reclassification
# ---------------------------------------------------------------------------


class TestAdaptiveReclassification:
    def test_adaptive_reclassification(self):
        """High confidence should reclassify less often (interval=20)."""
        bs = WorkloadBootstrapper()

        # Feed signals closely matching the fraud-triage fingerprint
        # distribution (multiple signal types, severities, resource kinds)
        fraud_signals = [
            {"signal_type": "alert_critical",    "severity": "critical", "resource_kind": "Pod",              "failure_class": "pods_crashlooping", "namespace": "fraud-prod"},
            {"signal_type": "alert_critical",    "severity": "high",     "resource_kind": "Pod",              "failure_class": "pods_crashlooping", "namespace": "fraud-prod"},
            {"signal_type": "alert_critical",    "severity": "medium",   "resource_kind": "Pod",              "failure_class": "oom_killed",        "namespace": "fraud-prod"},
            {"signal_type": "splunk_high_alert",  "severity": "high",    "resource_kind": "Pod",              "failure_class": "pods_crashlooping", "namespace": "fraud-prod"},
            {"signal_type": "splunk_high_alert",  "severity": "medium",  "resource_kind": "InferenceService", "failure_class": "oom_killed",        "namespace": "fraud-prod"},
            {"signal_type": "pod_crashloop",      "severity": "low",     "resource_kind": "InferenceService", "failure_class": "readiness_probe_failed", "namespace": "fraud-prod"},
            {"signal_type": "event_backoff",      "severity": "info",    "resource_kind": "Route",            "failure_class": "pods_crashlooping", "namespace": "fraud-prod"},
            {"signal_type": "kserve_not_ready",   "severity": "medium",  "resource_kind": "Route",            "failure_class": "readiness_probe_failed", "namespace": "fraud-prod"},
            {"signal_type": "alert_critical",     "severity": "low",     "resource_kind": "Node",             "failure_class": "oom_killed",        "namespace": "fraud-prod"},
            {"signal_type": "splunk_high_alert",  "severity": "info",    "resource_kind": "Pod",              "failure_class": "pods_crashlooping", "namespace": "fraud-prod"},
        ]
        for i in range(80):
            bs.observe(fraud_signals[i % len(fraud_signals)])

        _, confidence = bs.current_workload()
        assert confidence >= 0.9
        assert bs._reclassification_interval() == 20

    def test_low_confidence_reclassifies_aggressively(self):
        """Low confidence should reclassify every 2 observations."""
        bs = WorkloadBootstrapper()
        # Before any observations, confidence = 0.0
        assert bs._reclassification_interval() == 2

    def test_medium_confidence_default_interval(self):
        """Confidence between 0.5 and 0.9 should use default interval of 5."""
        bs = WorkloadBootstrapper()
        bs._cached_confidence = 0.6
        assert bs._reclassification_interval() == 5


# ---------------------------------------------------------------------------
# Classification scorecard
# ---------------------------------------------------------------------------


class TestClassificationScorecard:
    def test_scorecard_red_when_no_observations(self):
        bs = WorkloadBootstrapper()
        sc = bs.scorecard()
        assert sc.confidence_grade == "red"
        assert sc.namespace_match_grade == "red"
        assert sc.overall_grade == "red"

    def test_scorecard_green_confidence(self):
        bs = WorkloadBootstrapper()
        bs._cached_confidence = 0.85
        bs._namespace_matched = True
        sc = bs.scorecard()
        assert sc.confidence_grade == "green"
        assert sc.namespace_match_grade == "green"

    def test_scorecard_yellow_confidence(self):
        bs = WorkloadBootstrapper()
        bs._cached_confidence = 0.6
        sc = bs.scorecard()
        assert sc.confidence_grade == "yellow"

    def test_scorecard_overall_is_worst(self):
        """Overall grade should be the worst of all three sub-grades."""
        bs = WorkloadBootstrapper()
        bs._cached_confidence = 0.85  # green confidence
        bs._namespace_matched = False  # red namespace
        sc = bs.scorecard()
        assert sc.confidence_grade == "green"
        assert sc.namespace_match_grade == "red"
        assert sc.overall_grade == "red"

    def test_scorecard_reclassification_speed_yellow_at_high_conf(self):
        """When confidence >= 0.9, interval is 20, speed grade is yellow."""
        bs = WorkloadBootstrapper()
        bs._cached_confidence = 0.95
        sc = bs.scorecard()
        assert sc.reclassification_speed_grade == "yellow"

    def test_scorecard_model_validation(self):
        """ClassificationScorecard should be a valid Pydantic model."""
        sc = ClassificationScorecard(
            confidence_grade="green",
            reclassification_speed_grade="green",
            namespace_match_grade="green",
            overall_grade="green",
        )
        assert sc.confidence_grade == "green"
        assert sc.overall_grade == "green"

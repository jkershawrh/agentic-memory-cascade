"""Inverse cascade tests — TDD RED → GREEN.

Tests all six inversions: suppression archive, absence detection,
backward causal, synthetic baseline, agent export, self-monitoring.
"""

import pytest

from cascade_compression.cascade.memory import MemoryArchive
from cascade_compression.cascade.memory_intelligence import (
    AbsenceDetector,
    CausalGraph,
    MemoryIntelligence,
)
from cascade_compression.cascade.protocol import CascadeDecision, Outcome, Signal
from cascade_compression.cascade.inverse import (
    BaselineSnapshot,
    CascadeMetaCollector,
    SuppressionArchive,
    SuppressionPattern,
    export_learned_agents,
    find_all_gaps,
    generate_baseline,
    inverse_analysis,
    learn_expectations,
    missing_causes,
    wire_absence_detector,
)


def make_signal(signal_type="heartbeat", severity="info", content=None):
    return Signal(
        signal_type=signal_type,
        severity=severity,
        content=content or {"message": f"{signal_type} signal"},
    )


def make_decision(signal_id, agent="severity_gate", outcome=Outcome.DROP,
                  confidence=0.9, evidence="info severity dropped"):
    return CascadeDecision(
        signal_id=signal_id,
        agent_name=agent,
        outcome=outcome,
        confidence=confidence,
        evidence=evidence,
    )


# ===========================================================================
# Inversion 1: Suppression Archive
# ===========================================================================

class TestSuppressionArchive:
    def test_record_suppression(self):
        """Recording a suppression creates a pattern."""
        archive = SuppressionArchive()
        sig = make_signal()
        dec = make_decision(sig.signal_id)
        archive.record(dec, sig)
        assert archive.size == 1

    def test_ignores_non_suppression(self):
        """KEEP and ESCALATE decisions are not recorded."""
        archive = SuppressionArchive()
        sig = make_signal()
        dec = make_decision(sig.signal_id, outcome=Outcome.KEEP)
        archive.record(dec, sig)
        assert archive.size == 0

    def test_repeated_suppression_increases_count(self):
        """Same signal_type + agent pattern increments count."""
        archive = SuppressionArchive()
        for _ in range(10):
            sig = make_signal()
            dec = make_decision(sig.signal_id)
            archive.record(dec, sig)
        assert archive.size == 1
        assert archive.top_patterns(1)[0].count == 10

    def test_strength_increases_with_count(self):
        """More suppressions = higher strength (more confident it's noise)."""
        archive = SuppressionArchive()
        for _ in range(50):
            sig = make_signal()
            dec = make_decision(sig.signal_id)
            archive.record(dec, sig)
        pattern = archive.top_patterns(1)[0]
        assert pattern.strength > 0.4

    def test_frequency_distribution(self):
        """Frequency distribution groups by signal_type."""
        archive = SuppressionArchive()
        for _ in range(5):
            sig = make_signal(signal_type="heartbeat")
            archive.record(make_decision(sig.signal_id), sig)
        for _ in range(3):
            sig = make_signal(signal_type="probe_success")
            archive.record(make_decision(sig.signal_id), sig)
        dist = archive.frequency_distribution()
        assert dist["heartbeat"] == 5
        assert dist["probe_success"] == 3

    def test_record_batch(self):
        """record_batch processes multiple decisions at once."""
        archive = SuppressionArchive()
        signals = [make_signal(signal_type=f"type_{i}") for i in range(5)]
        decisions = [make_decision(s.signal_id) for s in signals]
        recorded = archive.record_batch(decisions, signals)
        assert recorded == 5
        assert archive.size == 5

    def test_capacity_bounded(self):
        """Archive doesn't exceed max_capacity."""
        archive = SuppressionArchive(max_capacity=5)
        for i in range(10):
            sig = make_signal(signal_type=f"type_{i}")
            archive.record(make_decision(sig.signal_id), sig)
        assert archive.size <= 5

    def test_to_dict_roundtrip(self):
        """Serialization roundtrip preserves data."""
        archive = SuppressionArchive()
        for i in range(3):
            sig = make_signal(signal_type=f"type_{i}")
            archive.record(make_decision(sig.signal_id), sig)
        data = archive.to_dict()
        restored = SuppressionArchive.from_dict(data)
        assert restored.size == archive.size
        assert restored._total_decisions == archive._total_decisions

    def test_stats(self):
        """Stats returns size and top patterns."""
        archive = SuppressionArchive()
        sig = make_signal()
        archive.record(make_decision(sig.signal_id), sig)
        stats = archive.stats()
        assert stats["size"] == 1
        assert stats["total_decisions"] == 1
        assert len(stats["top_suppressed"]) == 1


# ===========================================================================
# Inversion 2: Absence Detection from Learned Baseline
# ===========================================================================

class TestAbsenceFromBaseline:
    def test_learn_expectations(self):
        """Frequent suppression patterns produce expectations."""
        archive = SuppressionArchive()
        for _ in range(50):
            sig = make_signal(signal_type="heartbeat")
            archive.record(make_decision(sig.signal_id), sig)
        expectations = learn_expectations(archive, min_count=10)
        signal_types = [st for st, _ in expectations]
        assert "heartbeat" in signal_types

    def test_infrequent_not_expected(self):
        """Rare suppressions don't produce expectations."""
        archive = SuppressionArchive()
        for _ in range(2):
            sig = make_signal(signal_type="rare_event")
            archive.record(make_decision(sig.signal_id), sig)
        expectations = learn_expectations(archive, min_count=10)
        signal_types = [st for st, _ in expectations]
        assert "rare_event" not in signal_types

    def test_wire_absence_detector(self):
        """wire_absence_detector registers expectations automatically."""
        archive = SuppressionArchive()
        for _ in range(50):
            sig = make_signal(signal_type="heartbeat")
            archive.record(make_decision(sig.signal_id), sig)
        detector = AbsenceDetector()
        count = wire_absence_detector(detector, archive, min_count=10)
        assert count > 0
        assert "heartbeat" in detector.expectations


# ===========================================================================
# Inversion 3: Backward Causal Inference
# ===========================================================================

class TestBackwardCausal:
    def test_missing_cause_detected(self):
        """When an effect exists but its cause doesn't, report the gap."""
        graph = CausalGraph()
        graph.add_rule("node_disk_pressure", "event_provisioningfailed")
        archive = MemoryArchive()
        archive.store(make_signal(signal_type="event_provisioningfailed",
                                  severity="medium"),
                      classification="x")
        gaps = missing_causes(archive, graph, "event_provisioningfailed")
        assert len(gaps) == 1
        assert gaps[0]["expected_cause"] == "node_disk_pressure"

    def test_no_gap_when_cause_exists(self):
        """When both cause and effect exist, no gap reported."""
        graph = CausalGraph()
        graph.add_rule("node_disk_pressure", "event_provisioningfailed")
        archive = MemoryArchive()
        archive.store(make_signal(signal_type="node_disk_pressure", severity="high",
                                  content={"message": "disk pressure"}),
                      classification="x")
        archive.store(make_signal(signal_type="event_provisioningfailed",
                                  severity="medium",
                                  content={"message": "prov failed"}),
                      classification="x")
        gaps = missing_causes(archive, graph, "event_provisioningfailed")
        assert len(gaps) == 0

    def test_find_all_gaps(self):
        """find_all_gaps scans entire archive for missing causes."""
        graph = CausalGraph()
        graph.add_rule("a", "b")
        graph.add_rule("c", "d")
        archive = MemoryArchive()
        archive.store(make_signal(signal_type="b", severity="medium",
                                  content={"message": "b"}), classification="x")
        archive.store(make_signal(signal_type="d", severity="medium",
                                  content={"message": "d"}), classification="x")
        gaps = find_all_gaps(archive, graph)
        causes = [g["expected_cause"] for g in gaps]
        assert "a" in causes
        assert "c" in causes


# ===========================================================================
# Inversion 4: Synthetic Baseline
# ===========================================================================

class TestSyntheticBaseline:
    def test_generate_baseline(self):
        """Baseline is derived from suppression patterns."""
        archive = SuppressionArchive()
        for _ in range(20):
            sig = make_signal(signal_type="heartbeat")
            archive.record(make_decision(sig.signal_id), sig)
        baseline = generate_baseline(archive)
        assert "heartbeat" in baseline.signal_types

    def test_compare_detects_unexpected(self):
        """Signals not in baseline are flagged as unexpected."""
        baseline = BaselineSnapshot(
            signal_types={"heartbeat": {"frequency": 100}},
            generated_at="2026-08-14T00:00:00Z",
        )
        current = [make_signal(signal_type="ransomware_detected", severity="critical")]
        result = baseline.compare(current)
        assert len(result["unexpected"]) == 1
        assert result["unexpected"][0]["signal_type"] == "ransomware_detected"

    def test_compare_detects_missing(self):
        """Expected signals absent from current batch are flagged."""
        baseline = BaselineSnapshot(
            signal_types={"heartbeat": {"frequency": 100}},
            generated_at="2026-08-14T00:00:00Z",
        )
        current = [make_signal(signal_type="other_signal")]
        result = baseline.compare(current)
        assert len(result["missing"]) == 1
        assert result["missing"][0]["signal_type"] == "heartbeat"

    def test_compare_no_anomalies(self):
        """When current matches baseline, no anomalies."""
        baseline = BaselineSnapshot(
            signal_types={"heartbeat": {"frequency": 100}},
        )
        current = [make_signal(signal_type="heartbeat")]
        result = baseline.compare(current)
        assert len(result["unexpected"]) == 0
        assert len(result["missing"]) == 0


# ===========================================================================
# Inversion 5: Agent Knowledge Export
# ===========================================================================

class TestAgentExport:
    def test_export_from_bridge(self):
        """export_learned_agents produces a serializable dict."""
        from cascade_compression.bridge import CascadeBridge
        bridge = CascadeBridge()
        result = export_learned_agents(bridge)
        assert "agents" in result
        assert "activated_types" in result
        assert "known_patterns" in result
        assert isinstance(result["agents"], list)


# ===========================================================================
# Inversion 6: Self-Monitoring
# ===========================================================================

class TestCascadeMetaCollector:
    def test_collects_promotion_events(self):
        """Meta collector converts promotion log to signals."""
        from cascade_compression.bridge import CascadeBridge
        bridge = CascadeBridge()
        bridge._promotion_log = [
            {"event_type": "promotion", "agent_name": "test_agent",
             "from_tier": "draft", "to_tier": "candidate",
             "accuracy": 0.8, "false_negative_rate": 0.0, "reason": "met thresholds"},
        ]
        collector = CascadeMetaCollector(bridge)
        signals = collector.collect()
        assert len(signals) == 1
        assert signals[0].signal_type == "cascade_promotion"
        assert signals[0].severity == "info"

    def test_demotion_is_high_severity(self):
        """Demotion from activated tier is high severity."""
        from cascade_compression.bridge import CascadeBridge
        bridge = CascadeBridge()
        bridge._promotion_log = [
            {"event_type": "demotion", "agent_name": "failed_agent",
             "from_tier": "nano", "to_tier": "draft",
             "accuracy": 0.5, "false_negative_rate": 0.1, "reason": "false negative"},
        ]
        collector = CascadeMetaCollector(bridge)
        signals = collector.collect()
        assert len(signals) == 1
        assert signals[0].severity == "high"

    def test_no_duplicate_collection(self):
        """Second collect() doesn't re-emit the same events."""
        from cascade_compression.bridge import CascadeBridge
        bridge = CascadeBridge()
        bridge._promotion_log = [
            {"event_type": "promotion", "agent_name": "a",
             "from_tier": "draft", "to_tier": "candidate"},
        ]
        collector = CascadeMetaCollector(bridge)
        signals1 = collector.collect()
        signals2 = collector.collect()
        assert len(signals1) == 1
        assert len(signals2) == 0


# ===========================================================================
# Unified Inverse Analysis
# ===========================================================================

class TestInverseAnalysis:
    def test_full_inverse_analysis(self):
        """inverse_analysis returns all six perspectives."""
        suppression = SuppressionArchive()
        for _ in range(20):
            sig = make_signal(signal_type="heartbeat")
            suppression.record(make_decision(sig.signal_id), sig)

        memory = MemoryArchive()
        memory.store(make_signal(signal_type="outage", severity="critical",
                                 content={"message": "prod down"}),
                     classification="real_incident")

        intel = MemoryIntelligence()
        intel.causal_graph.add_rule("node_failure", "outage")

        result = inverse_analysis(suppression, memory, intel)
        assert "suppression" in result
        assert "baseline" in result
        assert "absence" in result
        assert "causal_gaps" in result
        assert "memory_vs_suppression" in result
        assert result["suppression"]["size"] == 1
        assert result["baseline"]["normal_signal_types"] >= 1

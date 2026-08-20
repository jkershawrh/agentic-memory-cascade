"""Cascade safety tests — fail-open verification.

These tests verify that the cascade NEVER drops a signal that should
reach inference. Every test case here represents a scenario where
dropping the signal would be a production incident.

Rule: if ANY test in this file fails, the cascade MUST NOT ship.
"""

import pytest
from uuid import uuid4

from cascade_compression.cascade.agents import (
    DeduplicateAgent,
    PatternClassifier,
    SeverityGate,
    ThresholdClassifier,
    TransientSuppressor,
    default_agents,
)
from cascade_compression.cascade.pipeline import CascadePipeline, CascadeResult
from cascade_compression.cascade.protocol import CascadeDecision, Outcome, Signal


def run_full_pipeline(signals):
    """Run through the complete default pipeline."""
    pipeline = CascadePipeline(default_agents())
    return pipeline.run(signals)


def assert_not_dropped(result: CascadeResult, signal: Signal, context: str = ""):
    """Assert a signal survived the cascade (is in remaining OR was escalated)."""
    remaining_ids = {s.signal_id for s in result.remaining}
    escalated_ids = {d.signal_id for d in result.decisions if d.outcome == Outcome.ESCALATE}
    survived = signal.signal_id in remaining_ids or signal.signal_id in escalated_ids
    assert survived, (
        f"CRITICAL: Signal {signal.signal_id} was dropped by cascade! {context}\n"
        f"  signal_type={signal.signal_type}, severity={signal.severity}\n"
        f"  content={signal.content}\n"
        f"  decisions={[d for d in result.decisions if d.signal_id == signal.signal_id]}"
    )


def assert_dropped(result: CascadeResult, signal: Signal):
    """Assert a signal was correctly dropped."""
    remaining_ids = {s.signal_id for s in result.remaining}
    assert signal.signal_id not in remaining_ids


# =========================================================================
# FALSE NEGATIVE TESTS — Critical signals that must NEVER be dropped
# =========================================================================


class TestCriticalSignalsNeverDropped:
    """Every signal here MUST survive the cascade. No exceptions."""

    def test_oomkilled_high_severity(self):
        s = Signal(signal_type="event", severity="high",
                   content={"message": "Container OOMKilled in production"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "OOMKilled must reach inference")

    def test_oomkilled_info_severity(self):
        """Info-severity OOMKill must be escalated, not dropped."""
        s = Signal(signal_type="event", severity="info",
                   content={"message": "OOMKilled: container exceeded 512Mi limit"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "OOMKilled at info severity must escalate")

    def test_segfault_any_severity(self):
        for sev in ("info", "low", "medium", "high", "critical"):
            s = Signal(signal_type="event", severity=sev,
                       content={"message": "segfault at 0x0000 in worker-pod"})
            result = run_full_pipeline([s])
            assert_not_dropped(result, s, f"Segfault at {sev} must survive")

    def test_kernel_panic(self):
        s = Signal(signal_type="event", severity="info",
                   content={"message": "Kernel panic - not syncing: Fatal exception"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Kernel panic must escalate even at info")

    def test_data_loss(self):
        s = Signal(signal_type="alert", severity="info",
                   content={"message": "Potential data loss detected on volume pvc-db-01"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Data loss must escalate")

    def test_data_corruption(self):
        s = Signal(signal_type="alert", severity="info",
                   content={"message": "Data corruption detected in index shard 3"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Data corruption must escalate")

    def test_security_breach(self):
        s = Signal(signal_type="security", severity="info",
                   content={"message": "Security breach: unauthorized access to admin API"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Security breach must escalate")

    def test_unauthorized_access(self):
        s = Signal(signal_type="event", severity="info",
                   content={"message": "Unauthorized access attempt from 10.0.5.42"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Unauthorized access must escalate")

    def test_sql_injection(self):
        s = Signal(signal_type="security", severity="info",
                   content={"message": "SQL injection attempt detected in login form"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Injection attack must escalate")

    def test_integrity_violation(self):
        s = Signal(signal_type="alert", severity="info",
                   content={"message": "Integrity check failed on backup volume"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Integrity violation must escalate")

    def test_fatal_error(self):
        s = Signal(signal_type="event", severity="info",
                   content={"message": "FATAL: database connection pool exhausted"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Fatal error must escalate")

    def test_critical_keyword_in_info(self):
        s = Signal(signal_type="event", severity="info",
                   content={"message": "Critical: payment processing pipeline stalled"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Critical keyword in info must escalate")

    def test_any_medium_severity_survives(self):
        s = Signal(signal_type="alert", severity="medium",
                   content={"message": "Something unusual happened"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Medium severity must always survive")

    def test_any_high_severity_survives(self):
        s = Signal(signal_type="alert", severity="high",
                   content={"message": "Service degradation detected"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "High severity must always survive")

    def test_any_critical_severity_survives(self):
        s = Signal(signal_type="alert", severity="critical",
                   content={"message": "Total outage"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Critical severity must always survive")

    def test_cpu_at_99_percent(self):
        s = Signal(signal_type="metric", severity="medium",
                   content={"cpu_percent": 99})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "CPU 99% must survive")

    def test_memory_at_98_percent(self):
        s = Signal(signal_type="metric", severity="medium",
                   content={"memory_percent": 98})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Memory 98% must survive")

    def test_disk_at_99_percent(self):
        s = Signal(signal_type="metric", severity="medium",
                   content={"disk_percent": 99})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Disk 99% must survive")


# =========================================================================
# DEDUP SAFETY — Signals that look similar but aren't duplicates
# =========================================================================


class TestDedupSafety:
    """Dedup must not merge signals that are actually different incidents."""

    def test_same_type_different_namespace(self):
        """Two OOMKills in different namespaces are separate incidents."""
        s1 = Signal(signal_type="event", source="pod-1", namespace="prod-billing",
                    severity="high", content={"message": "OOMKilled"})
        s2 = Signal(signal_type="event", source="pod-1", namespace="prod-payments",
                    severity="high", content={"message": "OOMKilled"})
        result = run_full_pipeline([s1, s2])
        assert_not_dropped(result, s1, "First OOMKill must survive")
        assert_not_dropped(result, s2, "Second OOMKill in different namespace must survive")

    def test_same_type_different_source(self):
        """Same error from different pods are separate incidents."""
        s1 = Signal(signal_type="event", source="web-frontend-1", namespace="prod",
                    severity="high", content={"message": "Connection refused"})
        s2 = Signal(signal_type="event", source="web-frontend-2", namespace="prod",
                    severity="high", content={"message": "Connection refused"})
        result = run_full_pipeline([s1, s2])
        assert_not_dropped(result, s1)
        assert_not_dropped(result, s2, "Same error from different source must survive")

    def test_same_type_different_severity(self):
        """Severity escalation should not be deduped."""
        s1 = Signal(signal_type="alert", source="disk-monitor", namespace="infra",
                    severity="medium", content={"message": "Disk 85%"})
        s2 = Signal(signal_type="alert", source="disk-monitor", namespace="infra",
                    severity="high", content={"message": "Disk 85%"})
        result = run_full_pipeline([s1, s2])
        # At least one must survive — the higher severity one
        assert_not_dropped(result, s2, "Higher severity version must survive dedup")

    def test_same_metadata_different_content_not_deduped(self):
        """Same type/source/namespace/severity but different message must NOT be deduped."""
        s1 = Signal(signal_type="alert", source="mon", namespace="prod",
                    severity="high", content={"message": "OOMKilled container web"})
        s2 = Signal(signal_type="alert", source="mon", namespace="prod",
                    severity="high", content={"message": "Disk full on volume data-01"})
        result = run_full_pipeline([s1, s2])
        assert_not_dropped(result, s1, "First alert must survive")
        assert_not_dropped(result, s2, "Different content must not be deduped")
        assert result.deduped_count == 0

    def test_rapid_same_signals_first_survives(self):
        """First of duplicates must always survive."""
        s1 = Signal(signal_type="event", source="pod-x", namespace="ns1",
                    severity="high", content={"message": "CrashLoopBackOff"})
        s2 = Signal(signal_type="event", source="pod-x", namespace="ns1",
                    severity="high", content={"message": "CrashLoopBackOff"})
        result = run_full_pipeline([s1, s2])
        assert_not_dropped(result, s1, "First of duplicates must always survive")


# =========================================================================
# TRANSIENT SUPPRESSION SAFETY
# =========================================================================


class TestTransientSafety:
    """Transient suppression must only apply to low/info severity."""

    def test_pod_restart_medium_not_suppressed(self):
        """Medium-severity pod restart is not transient — could be a pattern."""
        s = Signal(signal_type="pod_restart", severity="medium",
                   content={"message": "Pod restarted 5th time"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Medium pod_restart must not be suppressed")

    def test_pod_restart_high_not_suppressed(self):
        s = Signal(signal_type="pod_restart", severity="high",
                   content={"message": "Pod restart loop detected"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "High pod_restart must not be suppressed")

    def test_probe_failure_critical_not_suppressed(self):
        s = Signal(signal_type="probe_failure", severity="critical",
                   content={"message": "Liveness probe failed for 10 minutes"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Critical probe failure must not be suppressed")

    def test_dns_timeout_with_critical_content(self):
        """DNS timeout at low severity but with fatal content must survive."""
        s = Signal(signal_type="dns_timeout", severity="low",
                   content={"message": "DNS timeout causing fatal cascade failure"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Transient with fatal content must survive")

    def test_non_transient_type_low_severity_survives(self):
        """Low-severity signal with unknown type must not be suppressed."""
        s = Signal(signal_type="custom_alert", severity="low",
                   content={"message": "Something happened"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Unknown signal type must survive")


# =========================================================================
# ADVERSARIAL EDGE CASES
# =========================================================================


class TestAdversarialCases:
    """Signals designed to trick the cascade into wrong decisions."""

    def test_info_severity_with_hidden_critical(self):
        """Monitoring system marks it info, but content is critical."""
        s = Signal(signal_type="event", severity="info",
                   content={"message": "INFO: fatal database corruption detected in shard 7"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Fatal content in info signal must escalate")

    def test_looks_like_noise_but_is_attack(self):
        """Repeated auth failures look like noise but are a brute force attack."""
        s = Signal(signal_type="event", severity="info",
                   content={"message": "unauthorized login attempt from 10.0.5.42"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Auth failure must escalate (security keyword)")

    def test_scheduled_event_that_failed(self):
        """Scheduled maintenance that went wrong."""
        s = Signal(signal_type="event", severity="high",
                   content={"message": "Scheduled failover failed, primary unreachable"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Failed scheduled event must survive")

    def test_empty_content_high_severity(self):
        """Signal with no content but high severity must survive."""
        s = Signal(signal_type="unknown", severity="high", content={})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "High severity with empty content must survive")

    def test_empty_content_medium_severity(self):
        s = Signal(signal_type="unknown", severity="medium", content={})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Medium severity with empty content must survive")

    def test_unknown_signal_type_medium(self):
        """Completely unknown signal type must pass through."""
        s = Signal(signal_type="never_seen_before", severity="medium",
                   content={"message": "Something we haven't categorized"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Unknown signal type must pass through")

    def test_metric_with_no_numeric_values(self):
        """Metric signal with text content (malformed) must pass through."""
        s = Signal(signal_type="metric", severity="medium",
                   content={"message": "CPU usage extremely high"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Metric without numbers must pass through")

    def test_mixed_case_security_keywords(self):
        """Security keywords in weird casing must still escalate."""
        s = Signal(signal_type="event", severity="info",
                   content={"message": "UNAUTHORIZED ACCESS to admin panel"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Mixed case security keyword must escalate")

    def test_security_keyword_in_nested_content(self):
        """Security keyword buried in nested dict."""
        s = Signal(signal_type="event", severity="info",
                   content={"details": {"error": "unauthorized access detected"},
                            "source": "api-gateway"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Nested security keyword must escalate")


# =========================================================================
# PIPELINE INTEGRITY
# =========================================================================


class TestPipelineIntegrity:
    """Verify the pipeline itself doesn't lose signals."""

    def test_no_signal_lost_without_decision(self):
        """Every input signal must appear in remaining OR have a decision."""
        signals = [
            Signal(severity="high", content={"message": "alert 1"}),
            Signal(severity="medium", content={"message": "alert 2"}),
            Signal(severity="low", content={"message": "alert 3"}),
            Signal(severity="info", content={"message": "alert 4"}),
        ]
        result = run_full_pipeline(signals)
        decided_ids = {d.signal_id for d in result.decisions}
        remaining_ids = {s.signal_id for s in result.remaining}
        for s in signals:
            assert s.signal_id in decided_ids or s.signal_id in remaining_ids, \
                f"Signal {s.signal_id} ({s.severity}) vanished — neither decided nor remaining"

    def test_agent_crash_doesnt_drop_signals(self):
        """If an agent throws, signals pass through unaffected."""
        class CrashAgent:
            name = "crasher"
            stage = 1
            def process(self, signals):
                raise RuntimeError("Agent crashed")

        pipeline = CascadePipeline([CrashAgent()])
        signals = [Signal(severity="critical", content={"message": "must survive"})]
        result = pipeline.run(signals)
        assert len(result.remaining) == 1, "Signal must survive agent crash"

    def test_empty_input_produces_empty_output(self):
        result = run_full_pipeline([])
        assert result.total_signals == 0
        assert len(result.remaining) == 0
        assert result.compression_ratio == 0.0

    def test_single_critical_signal_passes_through(self):
        s = Signal(severity="critical", content={"message": "outage"})
        result = run_full_pipeline([s])
        assert len(result.remaining) == 1
        assert result.remaining[0].signal_id == s.signal_id

    def test_1000_signals_none_lost(self):
        """Bulk test — no signal vanishes."""
        signals = [Signal(severity="medium", signal_type=f"type_{i}",
                          source=f"src_{i}", namespace=f"ns_{i}",
                          content={"message": f"alert {i}"})
                   for i in range(1000)]
        result = run_full_pipeline(signals)
        decided_ids = {d.signal_id for d in result.decisions}
        remaining_ids = {s.signal_id for s in result.remaining}
        for s in signals:
            assert s.signal_id in decided_ids or s.signal_id in remaining_ids


# =========================================================================
# CORRECT DROPS — Signals that SHOULD be dropped
# =========================================================================


class TestCorrectDrops:
    """Verify noise actually gets filtered."""

    def test_info_heartbeat_dropped(self):
        s = Signal(signal_type="heartbeat", severity="info",
                   content={"message": "Pod healthy"})
        result = run_full_pipeline([s])
        assert_dropped(result, s)

    def test_info_probe_success_dropped(self):
        s = Signal(signal_type="probe_success", severity="info",
                   content={"message": "Liveness probe OK"})
        result = run_full_pipeline([s])
        assert_dropped(result, s)

    def test_transient_pod_restart_low_dropped(self):
        s = Signal(signal_type="pod_restart", severity="low",
                   content={"message": "Pod restarted once"})
        result = run_full_pipeline([s])
        assert_dropped(result, s)

    def test_transient_probe_failure_info_dropped(self):
        s = Signal(signal_type="probe_failure", severity="info",
                   content={"message": "Readiness probe failed once"})
        result = run_full_pipeline([s])
        assert_dropped(result, s)

    def test_duplicate_signals_deduped(self):
        s1 = Signal(signal_type="alert", source="mon", namespace="prod",
                    severity="medium", content={"message": "Disk 85%"})
        s2 = Signal(signal_type="alert", source="mon", namespace="prod",
                    severity="medium", content={"message": "Disk 85%"})
        result = run_full_pipeline([s1, s2])
        assert result.deduped_count == 1
        assert_not_dropped(result, s1, "First of duplicate must survive")


# =========================================================================
# REGRESSION TESTS — Add a test case for every real incident where
# the cascade made a wrong decision. This section grows over time.
# =========================================================================


class TestRegressions:
    """Every past false negative becomes a permanent test."""

    # Template for adding regression tests:
    # def test_incident_YYYYMMDD_description(self):
    #     """Incident: [date] [what happened] [why cascade missed it]"""
    #     s = Signal(...)
    #     result = run_full_pipeline([s])
    #     assert_not_dropped(result, s, "Regression: [description]")

    def test_none_severity_defaults_to_medium(self):
        """Signals with None/empty/invalid severity default to medium (fail-open)."""
        s = Signal(signal_type="alert", severity=None, content={"message": "something"})
        assert s.severity == "medium"
        result = run_full_pipeline([s])
        assert_not_dropped(result, s, "Invalid severity defaults to medium, must survive")

    def test_empty_severity_defaults_to_medium(self):
        s = Signal(signal_type="alert", severity="", content={"message": "something"})
        assert s.severity == "medium"

    def test_invalid_severity_defaults_to_medium(self):
        s = Signal(signal_type="alert", severity="banana", content={"message": "something"})
        assert s.severity == "medium"

    def test_placeholder_regression_framework_works(self):
        """Verify the regression test infrastructure works."""
        s = Signal(severity="critical", content={"message": "regression test"})
        result = run_full_pipeline([s])
        assert_not_dropped(result, s)


class TestMemoryDoesNotWeakenSafety:
    """Memory formation must NEVER alter pipeline decisions."""

    def test_memory_formation_after_pipeline(self):
        """Memory capture happens AFTER pipeline.run(), not during.
        Pipeline decisions must be identical with or without memory archive."""
        signals = [
            Signal(severity="critical", signal_type="oom", content={"message": "OOMKilled"}),
            Signal(severity="info", signal_type="heartbeat", content={"message": "ok"}),
            Signal(severity="high", signal_type="auth_fail", content={"message": "unauthorized"}),
        ]
        result_without = run_full_pipeline(signals)

        from cascade_compression.cascade.memory import MemoryArchive
        archive = MemoryArchive()
        result_with = run_full_pipeline(signals)
        for sig in result_with.remaining:
            archive.store(sig, classification="test")

        assert len(result_without.remaining) == len(result_with.remaining)
        without_ids = {s.signal_id for s in result_without.remaining}
        with_ids = {s.signal_id for s in result_with.remaining}
        assert without_ids == with_ids

    def test_memory_archive_full_does_not_block_processing(self):
        """Even if archive is at capacity, pipeline processing continues."""
        from cascade_compression.cascade.memory import MemoryArchive
        archive = MemoryArchive(max_capacity=2)
        for i in range(5):
            sig = Signal(severity="critical", signal_type=f"incident_{i}",
                         content={"message": f"incident {i}"})
            archive.store(sig, classification="test")
        assert archive.size <= 2

        signals = [Signal(severity="critical", content={"message": "new incident"})]
        result = run_full_pipeline(signals)
        assert_not_dropped(result, signals[0], "Pipeline must work even when memory archive is full")

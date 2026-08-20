"""Priming / attention weighting tests — TDD RED → GREEN.

Stage 1 (TDD): PrimingWindow mechanics — creation, decay, expiry.
Stage 2 (BDD): Safety — priming can ONLY escalate, NEVER suppress/drop.

RED tests — written before implementation.
"""

import pytest

from cascade_compression.cascade.agents import default_agents
from cascade_compression.cascade.memory import MemoryArchive, PrimingWindow
from cascade_compression.cascade.pipeline import CascadePipeline
from cascade_compression.cascade.protocol import Outcome, Signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_signal(signal_type="pod_crashloop", severity="high", source="node-01",
                namespace="production", content=None, labels=None):
    return Signal(
        signal_type=signal_type,
        severity=severity,
        source=source,
        namespace=namespace,
        content=content or {"message": f"{signal_type} detected"},
        labels=labels or {},
    )


def run_pipeline_with_priming(signals, priming_windows):
    """Run pipeline with PrimingEscalator injected at stage 0."""
    from cascade_compression.cascade.memory import PrimingEscalator
    agents = default_agents()
    pipeline = CascadePipeline(agents)
    if priming_windows:
        escalator = PrimingEscalator(priming_windows)
        pipeline.register(escalator)
    return pipeline.run(signals)


# ===========================================================================
# Stage 1: TDD — PrimingWindow Mechanics
# ===========================================================================

class TestPrimingWindow:
    def test_create_priming_window(self):
        """PrimingWindow can be created with signal_type and duration."""
        window = PrimingWindow(signal_type="pod_crashloop", duration_hours=4.0)
        assert window.signal_type == "pod_crashloop"
        assert window.duration_hours == 4.0
        assert window.opened_at is not None

    def test_effect_at_zero_elapsed(self):
        """At t=0, full priming effect (1.0)."""
        window = PrimingWindow(signal_type="pod_crashloop", duration_hours=4.0)
        assert window.effect(elapsed_hours=0.0) == 1.0

    def test_effect_at_half_elapsed(self):
        """At t=duration/2, priming effect is 0.5 (linear decay)."""
        window = PrimingWindow(signal_type="pod_crashloop", duration_hours=4.0)
        assert window.effect(elapsed_hours=2.0) == pytest.approx(0.5)

    def test_effect_at_full_elapsed(self):
        """At t=duration, priming effect is 0.0."""
        window = PrimingWindow(signal_type="pod_crashloop", duration_hours=4.0)
        assert window.effect(elapsed_hours=4.0) == pytest.approx(0.0)

    def test_effect_past_duration(self):
        """Past duration, effect is 0.0 (clamped)."""
        window = PrimingWindow(signal_type="pod_crashloop", duration_hours=4.0)
        assert window.effect(elapsed_hours=10.0) == 0.0

    def test_is_expired(self):
        """Window is expired when elapsed >= duration."""
        window = PrimingWindow(signal_type="pod_crashloop", duration_hours=4.0)
        assert not window.is_expired(elapsed_hours=2.0)
        assert window.is_expired(elapsed_hours=4.0)
        assert window.is_expired(elapsed_hours=5.0)


class TestPrimingEscalator:
    def test_escalates_primed_signal_type(self):
        """Signals matching a priming window's signal_type get escalated."""
        windows = {"pod_crashloop": PrimingWindow(signal_type="pod_crashloop", duration_hours=4.0)}
        signal = make_signal(signal_type="pod_crashloop", severity="info",
                             content={"message": "pod restarted"})
        result = run_pipeline_with_priming([signal], windows)
        escalated = [d for d in result.decisions if d.outcome == Outcome.ESCALATE]
        assert len(escalated) > 0

    def test_does_not_escalate_unrelated_type(self):
        """Signals not matching any priming window are unaffected."""
        windows = {"pod_crashloop": PrimingWindow(signal_type="pod_crashloop", duration_hours=4.0)}
        signal = make_signal(signal_type="disk_pressure", severity="info",
                             content={"message": "disk check"})
        result_with = run_pipeline_with_priming([signal], windows)
        result_without = run_pipeline_with_priming([signal], {})
        # Both should be the same — unrelated type not affected
        assert len(result_with.remaining) == len(result_without.remaining)


# ===========================================================================
# Stage 2: BDD — Safety Invariants
# ===========================================================================

class TestPrimingSafety:
    def test_priming_never_suppresses(self):
        """GIVEN active priming for signal_type X
        WHEN signal X arrives at medium severity
        THEN it is kept or escalated, never suppressed."""
        windows = {"disk_pressure": PrimingWindow(signal_type="disk_pressure", duration_hours=4.0)}
        signal = make_signal(signal_type="disk_pressure", severity="medium",
                             content={"message": "disk usage elevated"})
        result = run_pipeline_with_priming([signal], windows)
        for d in result.decisions:
            if d.signal_id == signal.signal_id:
                assert d.outcome != Outcome.SUPPRESS
                assert d.outcome != Outcome.DROP

    def test_priming_does_not_weaken_zero_fn(self):
        """GIVEN active priming
        WHEN critical signals arrive
        THEN they survive regardless (safety preserved)."""
        windows = {"pod_crashloop": PrimingWindow(signal_type="pod_crashloop", duration_hours=4.0)}
        critical_signals = [
            make_signal(severity="critical", content={"message": "OOMKilled"}),
            make_signal(signal_type="security_breach", severity="high",
                        content={"message": "unauthorized access"}),
        ]
        result = run_pipeline_with_priming(critical_signals, windows)
        remaining_ids = {s.signal_id for s in result.remaining}
        escalated_ids = {d.signal_id for d in result.decisions if d.outcome == Outcome.ESCALATE}
        for s in critical_signals:
            survived = s.signal_id in remaining_ids or s.signal_id in escalated_ids
            assert survived, f"Critical signal {s.signal_type} was dropped with priming active"

    def test_priming_escalates_info_during_window(self):
        """GIVEN a priming window for pod_crashloop
        WHEN an info-severity pod_crashloop arrives
        THEN it is escalated instead of being dropped by severity gate."""
        windows = {"pod_crashloop": PrimingWindow(signal_type="pod_crashloop", duration_hours=4.0)}
        signal = make_signal(signal_type="pod_crashloop", severity="info",
                             content={"message": "pod restarted normally"})
        result = run_pipeline_with_priming([signal], windows)
        escalated = {d.signal_id for d in result.decisions if d.outcome == Outcome.ESCALATE}
        remaining = {s.signal_id for s in result.remaining}
        assert signal.signal_id in escalated or signal.signal_id in remaining

    def test_expired_window_has_no_effect(self):
        """GIVEN an expired priming window
        WHEN a matching signal arrives
        THEN no priming escalation occurs."""
        window = PrimingWindow(signal_type="pod_crashloop", duration_hours=0.0)
        windows = {"pod_crashloop": window}
        signal = make_signal(signal_type="pod_crashloop", severity="info",
                             content={"message": "routine restart"})
        result_with = run_pipeline_with_priming([signal], windows)
        result_without = run_pipeline_with_priming([signal], {})
        assert len(result_with.remaining) == len(result_without.remaining)

    def test_window_cap_enforced(self):
        """PrimingWindow manager caps at max_windows."""
        archive = MemoryArchive()
        for i in range(15):
            archive.add_priming_window(
                PrimingWindow(signal_type=f"type_{i}", duration_hours=4.0),
                max_windows=10,
            )
        assert len(archive.get_priming_windows()) <= 10

    def test_oldest_window_evicted_at_cap(self):
        """When cap is reached, the oldest window is evicted."""
        archive = MemoryArchive()
        for i in range(10):
            archive.add_priming_window(
                PrimingWindow(signal_type=f"type_{i}", duration_hours=4.0),
                max_windows=10,
            )
        archive.add_priming_window(
            PrimingWindow(signal_type="new_type", duration_hours=4.0),
            max_windows=10,
        )
        windows = archive.get_priming_windows()
        assert "type_0" not in windows
        assert "new_type" in windows

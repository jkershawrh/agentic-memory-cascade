"""Tests for continuous validation: shadow sampling and time-bounded activation.

Validates:
- Shadow validation: suppressed signals sampled and sent to LLM for re-check
- Time-bounded activation: agents expire after TTL and must re-qualify
- Integration: shadow + TTL + GCL demotion all produce provenance events
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from cascade_compression.bridge import CascadeBridge, _is_noise_classification
from cascade_compression.cascade.pipeline import CascadeResult
from cascade_compression.cascade.promotion import AgentMetrics, PromotionEngine
from cascade_compression.cascade.protocol import CascadeDecision, Outcome, Signal


@pytest.fixture
def bridge():
    b = CascadeBridge(domain="test")
    b.enabled = True
    b._llm_url = ""
    b._shadow_sample_rate = 1.0  # 100% for deterministic tests
    return b


def _make_signal(signal_type="event_noise", severity="info"):
    return Signal(
        signal_id=uuid4(),
        signal_type=signal_type,
        severity=severity,
        source="test",
        namespace="test-ns",
        content={"message": "test signal"},
    )


class TestShadowSampling:
    def test_queue_shadow_samples_from_activated_agents(self, bridge):
        sig = _make_signal("event_noise")
        bridge._activated_types = {"event_noise"}
        result = CascadeResult(
            total_signals=1,
            decisions=[CascadeDecision(
                signal_id=sig.signal_id,
                agent_name="dominant_noise_suppressor",
                outcome=Outcome.SUPPRESS,
                confidence=0.9,
            )],
            remaining=[],
        )
        bridge._queue_shadow_samples(result, [sig])
        assert len(bridge._shadow_buffer) == 1
        assert bridge._shadow_buffer[0]["signal_type"] == "event_noise"

    def test_no_shadow_for_non_activated_types(self, bridge):
        sig = _make_signal("unknown_type")
        bridge._activated_types = {"event_noise"}
        result = CascadeResult(
            total_signals=1,
            decisions=[CascadeDecision(
                signal_id=sig.signal_id,
                agent_name="some_agent",
                outcome=Outcome.SUPPRESS,
                confidence=0.9,
            )],
            remaining=[],
        )
        bridge._queue_shadow_samples(result, [sig])
        assert len(bridge._shadow_buffer) == 0

    def test_no_shadow_for_keep_decisions(self, bridge):
        sig = _make_signal("event_noise")
        bridge._activated_types = {"event_noise"}
        result = CascadeResult(
            total_signals=1,
            decisions=[CascadeDecision(
                signal_id=sig.signal_id,
                agent_name="test",
                outcome=Outcome.KEEP,
                confidence=0.9,
            )],
            remaining=[sig],
        )
        bridge._queue_shadow_samples(result, [sig])
        assert len(bridge._shadow_buffer) == 0

    def test_shadow_buffer_capped(self, bridge):
        bridge._shadow_max_buffer = 5
        bridge._activated_types = {"event_noise"}
        signals = [_make_signal("event_noise") for _ in range(10)]
        result = CascadeResult(
            total_signals=10,
            decisions=[CascadeDecision(
                signal_id=s.signal_id,
                agent_name="test",
                outcome=Outcome.SUPPRESS,
                confidence=0.9,
            ) for s in signals],
            remaining=[],
        )
        bridge._queue_shadow_samples(result, signals)
        assert len(bridge._shadow_buffer) <= 5

    def test_shadow_sample_rate_zero_skips(self, bridge):
        bridge._shadow_sample_rate = 0.0
        sig = _make_signal("event_noise")
        bridge._activated_types = {"event_noise"}
        result = CascadeResult(
            total_signals=1,
            decisions=[CascadeDecision(
                signal_id=sig.signal_id,
                agent_name="test",
                outcome=Outcome.SUPPRESS,
                confidence=0.9,
            )],
            remaining=[],
        )
        bridge._queue_shadow_samples(result, [sig])
        assert len(bridge._shadow_buffer) == 0

    def test_shadow_demotion_on_important_classification(self, bridge):
        """When shadow LLM says a suppressed signal is important, demotion fires."""
        bridge._activated_types = {"event_noise"}
        bridge._activated_patterns = {"event_noise": "dominant_type"}
        metrics = AgentMetrics(
            name="noise_agent", tier="nano",
            config={"signal_type": "event_noise"},
        )
        bridge._agent_metrics["noise_agent"] = metrics

        bridge.record_feedback("event_noise", was_suppressed=True, is_important=True)

        assert metrics.tier == "draft"
        assert metrics.deactivated is True
        assert "event_noise" not in bridge._activated_types

    def test_noise_classification_helper(self):
        assert _is_noise_classification("routine_noise") is True
        assert _is_noise_classification("known_pattern noise") is True
        assert _is_noise_classification("needs_attention") is False
        assert _is_noise_classification("real_incident") is False


class TestTimeBoundedActivation:
    def test_fresh_agent_not_expired(self, bridge):
        bridge._activation_ttl_hours = 72
        bridge._activated_types = {"event_noise"}
        bridge._activated_patterns = {"event_noise": "dominant_type"}
        bridge._activation_timestamps = {
            "event_noise": datetime.now(timezone.utc).isoformat()
        }
        metrics = AgentMetrics(
            name="noise_agent", tier="nano",
            config={"signal_type": "event_noise"},
        )
        bridge._agent_metrics["noise_agent"] = metrics

        bridge._check_activation_ttl()

        assert metrics.tier == "nano"
        assert "event_noise" in bridge._activated_types

    def test_expired_agent_suspended(self, bridge):
        bridge._activation_ttl_hours = 72
        bridge._activated_types = {"event_noise"}
        bridge._activated_patterns = {"event_noise": "dominant_type"}
        expired_time = (datetime.now(timezone.utc) - timedelta(hours=73)).isoformat()
        bridge._activation_timestamps = {"event_noise": expired_time}
        metrics = AgentMetrics(
            name="noise_agent", tier="nano",
            config={"signal_type": "event_noise"},
        )
        bridge._agent_metrics["noise_agent"] = metrics

        bridge._check_activation_ttl()

        assert metrics.tier == "draft"
        assert metrics.deactivated is False  # reactivated, ready to re-climb
        assert metrics.samples_tested == 0
        assert "event_noise" not in bridge._activated_types
        assert "event_noise" not in bridge._activation_timestamps

    def test_expired_agent_emits_demotion_event(self, bridge):
        bridge._activation_ttl_hours = 1
        bridge._activated_types = {"event_noise"}
        bridge._activated_patterns = {"event_noise": "dominant_type"}
        expired_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        bridge._activation_timestamps = {"event_noise": expired_time}
        metrics = AgentMetrics(
            name="noise_agent", tier="nano",
            config={"signal_type": "event_noise"},
        )
        bridge._agent_metrics["noise_agent"] = metrics

        bridge._check_activation_ttl()

        events = bridge.promotion.drain_events()
        assert len(events) == 1
        assert events[0].event_type == "demotion"
        assert "TTL expired" in events[0].reason

    def test_ttl_zero_disables_expiry(self, bridge):
        bridge._activation_ttl_hours = 0
        bridge._activated_types = {"event_noise"}
        bridge._activated_patterns = {"event_noise": "dominant_type"}
        old_time = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        bridge._activation_timestamps = {"event_noise": old_time}
        metrics = AgentMetrics(
            name="noise_agent", tier="nano",
            config={"signal_type": "event_noise"},
        )
        bridge._agent_metrics["noise_agent"] = metrics

        bridge._check_activation_ttl()

        assert metrics.tier == "nano"
        assert "event_noise" in bridge._activated_types

    def test_expired_agent_can_requalify(self, bridge):
        """After TTL expiry, agent is at draft + reactivated — can climb again."""
        bridge._activation_ttl_hours = 1
        bridge._activated_types = {"event_noise"}
        bridge._activated_patterns = {"event_noise": "dominant_type"}
        expired_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        bridge._activation_timestamps = {"event_noise": expired_time}
        metrics = AgentMetrics(
            name="noise_agent", tier="nano",
            config={"signal_type": "event_noise"},
        )
        bridge._agent_metrics["noise_agent"] = metrics

        bridge._check_activation_ttl()

        assert metrics.tier == "draft"
        assert metrics.deactivated is False
        assert metrics.samples_tested == 0
        # Agent is ready to climb again via normal promotion path
        engine = bridge.promotion
        result = engine.check_promotion(metrics)
        assert result.tier == "draft"  # no samples yet, stays at draft


class TestStatsExposure:
    def test_stats_include_shadow_and_ttl(self, bridge):
        stats = bridge.get_stats()
        assert "shadow_checks" in stats
        assert "shadow_demotions" in stats
        assert "shadow_sample_rate" in stats
        assert "activation_ttl_hours" in stats
        assert stats["shadow_sample_rate"] == 1.0
        assert stats["activation_ttl_hours"] == 72


class TestStateRoundTrip:
    def test_activation_timestamps_persisted(self, bridge, tmp_path):
        state_file = str(tmp_path / "cascade_state.json")
        bridge._state_file = state_file
        bridge._activation_timestamps = {
            "event_noise": "2026-08-10T00:00:00+00:00",
        }
        bridge._verdict_watermark = "2026-08-10T01:00:00+00:00"

        bridge._save_state()

        with open(state_file) as f:
            state = json.load(f)
        assert state["activation_timestamps"]["event_noise"] == "2026-08-10T00:00:00+00:00"
        assert state["verdict_watermark"] == "2026-08-10T01:00:00+00:00"

        bridge2 = CascadeBridge(domain="test")
        bridge2._state_file = state_file
        bridge2._restore_state()
        assert bridge2._activation_timestamps["event_noise"] == "2026-08-10T00:00:00+00:00"
        assert bridge2._verdict_watermark == "2026-08-10T01:00:00+00:00"

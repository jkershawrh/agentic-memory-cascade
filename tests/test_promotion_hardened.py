"""Adversarial tests for the hardened promotion engine.

Validates:
- Zero FN invariant: nano+ agents with ANY false negative are instantly demoted
- Instant demotion with cooling-off: demoted agents reset to draft, samples zeroed
- Promotion provenance: every tier transition emits a PromotionEvent with full evidence
- Human gate: pending_approval blocks activation until human_approved=True
- Edge cases: 199 safe + 1 real incident, re-promotion after demotion
"""

import pytest

from cascade_compression.cascade.promotion import (
    ACTIVATED_TIERS,
    AgentMetrics,
    Baseline,
    PromotionEngine,
    PromotionEvent,
    RuleAgent,
)


@pytest.fixture
def baseline():
    return Baseline(normal_ranges={
        "metric": {
            "cpu_percent": {"low": 0, "high": 80},
        },
    })


@pytest.fixture
def cpu_rule():
    return RuleAgent({
        "name": "cpu_critical",
        "condition": {"field": "cpu_percent", "operator": "gt", "value": 90},
        "classification": "cpu_critical",
    })


@pytest.fixture
def engine():
    return PromotionEngine()


@pytest.fixture
def gated_engine():
    return PromotionEngine(human_gate_enabled=True)


def make_clean_signals(normal_count, abnormal_count):
    """Signals where the cpu_rule has zero false negatives."""
    signals = []
    for i in range(normal_count):
        signals.append({"signal_type": "metric", "features": {"cpu_percent": 50 + i % 30}})
    for i in range(abnormal_count):
        signals.append({"signal_type": "metric", "features": {"cpu_percent": 92 + i % 8}})
    return signals


def make_sneaky_signals(safe_count, incident_count):
    """Signals where incidents have cpu > 80 (abnormal) but <= 90 (rule misses them).

    The rule fires on cpu > 90, but these incidents sit at 81-89 —
    abnormal by baseline but invisible to the rule. Classic false negative.
    """
    signals = []
    for i in range(safe_count):
        signals.append({"signal_type": "metric", "features": {"cpu_percent": 50 + i % 30}})
    for i in range(incident_count):
        signals.append({"signal_type": "metric", "features": {"cpu_percent": 81 + i % 9}})
    return signals


def promote_to_nano(engine, cpu_rule, baseline):
    """Helper: climb an agent to nano tier with clean signals."""
    agent = AgentMetrics(name="cpu_critical", tier="draft")
    agent = engine.validate(agent, cpu_rule, make_clean_signals(30, 30), baseline)
    agent = engine.check_promotion(agent)
    assert agent.tier == "candidate"
    agent = engine.validate(agent, cpu_rule, make_clean_signals(120, 120), baseline)
    agent = engine.check_promotion(agent)
    assert agent.tier == "nano"
    return agent


class TestZeroFNInvariant:
    """An activated agent (nano+) with ANY false negative must be instantly demoted."""

    def test_nano_agent_one_fn_demoted(self, engine, cpu_rule, baseline):
        agent = promote_to_nano(engine, cpu_rule, baseline)
        sneaky = make_sneaky_signals(199, 1)
        agent = engine.validate(agent, cpu_rule, sneaky, baseline)
        assert agent.tier == "draft"
        assert agent.deactivated is True

    def test_nano_agent_zero_fn_survives(self, engine, cpu_rule, baseline):
        agent = promote_to_nano(engine, cpu_rule, baseline)
        clean = make_clean_signals(100, 100)
        agent = engine.validate(agent, cpu_rule, clean, baseline)
        assert agent.tier == "nano"
        assert agent.deactivated is False

    def test_candidate_with_fn_not_demoted(self, engine, cpu_rule, baseline):
        """Candidates are still under observation — FN is expected."""
        agent = AgentMetrics(name="test", tier="draft")
        agent = engine.validate(agent, cpu_rule, make_clean_signals(30, 30), baseline)
        agent = engine.check_promotion(agent)
        assert agent.tier == "candidate"
        sneaky = make_sneaky_signals(45, 5)
        agent = engine.validate(agent, cpu_rule, sneaky, baseline)
        assert agent.tier == "candidate"
        assert agent.deactivated is False

    def test_candidate_with_fn_blocked_from_nano(self, engine, cpu_rule, baseline):
        """A candidate with any FN rate > 0 cannot promote to nano (threshold is 0.0)."""
        agent = AgentMetrics(name="test", tier="candidate")
        sneaky = make_sneaky_signals(190, 10)
        agent = engine.validate(agent, cpu_rule, sneaky, baseline)
        agent = engine.check_promotion(agent)
        assert agent.tier == "candidate"

    def test_199_safe_1_real_incident(self, engine, cpu_rule, baseline):
        """The adversarial scenario: agent looks perfect on 199, drops the 200th."""
        agent = promote_to_nano(engine, cpu_rule, baseline)
        signals = make_sneaky_signals(199, 1)
        agent = engine.validate(agent, cpu_rule, signals, baseline)
        assert agent.tier == "draft"
        assert agent.deactivated is True
        assert agent.last_batch_fn_count == 1

    def test_zero_fn_threshold_in_defaults(self):
        """Verify the default thresholds enforce zero FN for all activated tiers."""
        engine = PromotionEngine()
        for tier in ACTIVATED_TIERS:
            reqs = engine.thresholds.get(tier, {})
            assert reqs.get("max_false_negative") == 0.0, (
                f"tier '{tier}' allows FN rate {reqs.get('max_false_negative')}"
            )


class TestInstantDemotion:
    """Demotion resets the agent to draft, deactivates it, and zeroes samples."""

    def test_demotion_resets_to_draft(self, engine, cpu_rule, baseline):
        agent = promote_to_nano(engine, cpu_rule, baseline)
        engine.demote(agent, reason="test demotion")
        assert agent.tier == "draft"

    def test_demotion_deactivates(self, engine, cpu_rule, baseline):
        agent = promote_to_nano(engine, cpu_rule, baseline)
        engine.demote(agent, reason="test")
        assert agent.deactivated is True
        assert agent.deactivated_at is not None

    def test_demotion_zeroes_samples(self, engine, cpu_rule, baseline):
        agent = promote_to_nano(engine, cpu_rule, baseline)
        assert agent.samples_tested > 0
        engine.demote(agent, reason="test")
        assert agent.samples_tested == 0

    def test_demotion_records_history(self, engine, cpu_rule, baseline):
        agent = promote_to_nano(engine, cpu_rule, baseline)
        engine.demote(agent, reason="found FN", batch_id="batch-123")
        assert len(agent.demotion_history) == 1
        record = agent.demotion_history[0]
        assert record["from_tier"] == "nano"
        assert record["to_tier"] == "draft"
        assert record["reason"] == "found FN"

    def test_deactivated_agent_cannot_promote(self, engine, cpu_rule, baseline):
        agent = promote_to_nano(engine, cpu_rule, baseline)
        engine.demote(agent, reason="test")
        agent = engine.validate(agent, cpu_rule, make_clean_signals(30, 30), baseline)
        agent = engine.check_promotion(agent)
        assert agent.tier == "draft"
        assert agent.deactivated is True

    def test_reactivate_allows_climbing(self, engine, cpu_rule, baseline):
        agent = promote_to_nano(engine, cpu_rule, baseline)
        engine.demote(agent, reason="test")
        engine.reactivate(agent)
        assert agent.deactivated is False
        agent = engine.validate(agent, cpu_rule, make_clean_signals(30, 30), baseline)
        agent = engine.check_promotion(agent)
        assert agent.tier == "candidate"

    def test_cooling_off_requires_fresh_samples(self, engine, cpu_rule, baseline):
        """After demotion, samples_tested is 0 — agent must re-accumulate."""
        agent = promote_to_nano(engine, cpu_rule, baseline)
        engine.demote(agent, reason="test")
        engine.reactivate(agent)
        assert agent.samples_tested == 0
        agent = engine.validate(agent, cpu_rule, make_clean_signals(10, 10), baseline)
        agent = engine.check_promotion(agent)
        assert agent.tier == "draft"  # only 20 samples, need 50 for candidate

    def test_demoted_agent_clears_human_approved(self, engine, cpu_rule, baseline):
        agent = promote_to_nano(engine, cpu_rule, baseline)
        agent.human_approved = True
        engine.demote(agent, reason="test")
        assert agent.human_approved is False


class TestPromotionProvenance:
    """Every tier transition emits a PromotionEvent with full evidence chain."""

    def test_promotion_emits_event(self, engine, cpu_rule, baseline):
        agent = AgentMetrics(name="cpu_critical", tier="draft")
        agent = engine.validate(agent, cpu_rule, make_clean_signals(30, 30), baseline)
        agent = engine.check_promotion(agent)
        events = engine.drain_events()
        assert len(events) == 1
        assert events[0].event_type == "promotion"
        assert events[0].from_tier == "draft"
        assert events[0].to_tier == "candidate"
        assert events[0].samples_tested == 60

    def test_demotion_emits_event(self, engine, cpu_rule, baseline):
        agent = promote_to_nano(engine, cpu_rule, baseline)
        engine.drain_events()  # clear promotion events
        sneaky = make_sneaky_signals(199, 1)
        agent = engine.validate(agent, cpu_rule, sneaky, baseline)
        events = engine.drain_events()
        assert len(events) == 1
        assert events[0].event_type == "demotion"
        assert events[0].from_tier == "nano"
        assert events[0].to_tier == "draft"
        assert events[0].false_negative_count == 1

    def test_event_to_dict_has_evidence(self, engine, cpu_rule, baseline):
        agent = AgentMetrics(name="cpu_critical", tier="draft")
        agent = engine.validate(agent, cpu_rule, make_clean_signals(30, 30), baseline)
        agent = engine.check_promotion(agent)
        event = engine.drain_events()[0]
        d = event.to_dict()
        assert "evidence" in d
        assert "samples_tested" in d["evidence"]
        assert "accuracy" in d["evidence"]
        assert "false_positive_rate" in d["evidence"]
        assert "false_negative_rate" in d["evidence"]
        assert "false_negative_count" in d["evidence"]

    def test_full_ladder_emits_all_events(self, engine, cpu_rule, baseline):
        agent = AgentMetrics(name="cpu_critical", tier="draft")
        agent = engine.validate(agent, cpu_rule, make_clean_signals(30, 30), baseline)
        agent = engine.check_promotion(agent)
        agent = engine.validate(agent, cpu_rule, make_clean_signals(120, 120), baseline)
        agent = engine.check_promotion(agent)
        agent = engine.validate(agent, cpu_rule, make_clean_signals(300, 300), baseline)
        agent.human_reviewed = True
        agent = engine.check_promotion(agent)
        events = engine.drain_events()
        tiers = [(e.from_tier, e.to_tier) for e in events]
        assert ("draft", "candidate") in tiers
        assert ("candidate", "nano") in tiers
        assert ("nano", "micro") in tiers

    def test_drain_clears_events(self, engine, cpu_rule, baseline):
        agent = AgentMetrics(name="test", tier="draft")
        agent = engine.validate(agent, cpu_rule, make_clean_signals(30, 30), baseline)
        agent = engine.check_promotion(agent)
        assert len(engine.drain_events()) == 1
        assert len(engine.drain_events()) == 0


class TestHumanGate:
    """When human_gate_enabled, agents pause at pending_approval before nano."""

    def test_candidate_goes_to_pending_approval(self, gated_engine, cpu_rule, baseline):
        agent = AgentMetrics(name="cpu_critical", tier="draft")
        agent = gated_engine.validate(agent, cpu_rule, make_clean_signals(30, 30), baseline)
        agent = gated_engine.check_promotion(agent)
        assert agent.tier == "candidate"
        agent = gated_engine.validate(agent, cpu_rule, make_clean_signals(120, 120), baseline)
        agent = gated_engine.check_promotion(agent)
        assert agent.tier == "pending_approval"

    def test_pending_approval_blocks_without_human(self, gated_engine, cpu_rule, baseline):
        agent = AgentMetrics(name="cpu_critical", tier="pending_approval")
        agent.accuracy = 0.95
        agent.samples_tested = 300
        agent = gated_engine.check_promotion(agent)
        assert agent.tier == "pending_approval"

    def test_pending_approval_activates_with_human(self, gated_engine, cpu_rule, baseline):
        agent = AgentMetrics(name="cpu_critical", tier="pending_approval")
        agent.accuracy = 0.95
        agent.samples_tested = 300
        agent.human_approved = True
        agent = gated_engine.check_promotion(agent)
        assert agent.tier == "nano"

    def test_no_gate_skips_pending_approval(self, engine, cpu_rule, baseline):
        """Without human_gate_enabled, candidate goes straight to nano."""
        agent = AgentMetrics(name="cpu_critical", tier="draft")
        agent = engine.validate(agent, cpu_rule, make_clean_signals(30, 30), baseline)
        agent = engine.check_promotion(agent)
        assert agent.tier == "candidate"
        agent = engine.validate(agent, cpu_rule, make_clean_signals(120, 120), baseline)
        agent = engine.check_promotion(agent)
        assert agent.tier == "nano"

    def test_human_gate_emits_provenance(self, gated_engine, cpu_rule, baseline):
        agent = AgentMetrics(name="cpu_critical", tier="draft")
        agent = gated_engine.validate(agent, cpu_rule, make_clean_signals(30, 30), baseline)
        agent = gated_engine.check_promotion(agent)
        agent = gated_engine.validate(agent, cpu_rule, make_clean_signals(120, 120), baseline)
        agent = gated_engine.check_promotion(agent)
        assert agent.tier == "pending_approval"
        events = gated_engine.drain_events()
        pending_event = [e for e in events if e.to_tier == "pending_approval"]
        assert len(pending_event) == 1
        assert "awaiting human approval" in pending_event[0].reason

    def test_human_approval_emits_provenance(self, gated_engine, cpu_rule, baseline):
        agent = AgentMetrics(name="cpu_critical", tier="pending_approval")
        agent.accuracy = 0.95
        agent.samples_tested = 300
        agent.human_approved = True
        agent = gated_engine.check_promotion(agent)
        events = gated_engine.drain_events()
        assert len(events) == 1
        assert events[0].to_tier == "nano"
        assert events[0].human_approved is True


class TestLedgerSchema:
    """PromotionEvent.to_dict() conforms to the agent-promotion.json schema."""

    def test_promotion_event_schema(self):
        event = PromotionEvent(
            agent_name="test_agent",
            event_type="promotion",
            from_tier="candidate",
            to_tier="nano",
            timestamp="2026-08-10T00:00:00+00:00",
            samples_tested=200,
            accuracy=0.95,
            false_positive_rate=0.05,
            false_negative_rate=0.0,
            false_negative_count=0,
            reason="meets nano thresholds",
        )
        d = event.to_dict()
        assert d["agent_name"] == "test_agent"
        assert d["event_type"] == "promotion"
        assert d["from_tier"] == "candidate"
        assert d["to_tier"] == "nano"
        assert d["reason"] == "meets nano thresholds"
        evidence = d["evidence"]
        assert evidence["samples_tested"] == 200
        assert evidence["accuracy"] == 0.95
        assert evidence["false_positive_rate"] == 0.05
        assert evidence["false_negative_rate"] == 0.0
        assert evidence["false_negative_count"] == 0

    def test_demotion_event_schema(self):
        event = PromotionEvent(
            agent_name="bad_agent",
            event_type="demotion",
            from_tier="nano",
            to_tier="draft",
            timestamp="2026-08-10T00:00:00+00:00",
            samples_tested=250,
            accuracy=0.80,
            false_positive_rate=0.10,
            false_negative_rate=0.05,
            false_negative_count=3,
            reason="3 false negative(s) in validation batch",
            batch_id="batch-abc",
        )
        d = event.to_dict()
        assert d["event_type"] == "demotion"
        assert d["evidence"]["false_negative_count"] == 3
        assert d["evidence"]["batch_id"] == "batch-abc"

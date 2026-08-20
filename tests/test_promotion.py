"""Tests for the agent promotion engine."""

import pytest

from cascade_compression.cascade.promotion import (
    AgentMetrics,
    Baseline,
    PromotionEngine,
    RuleAgent,
)


@pytest.fixture
def baseline():
    return Baseline(normal_ranges={
        "metric": {
            "cpu_percent": {"low": 0, "high": 80},
            "memory_percent": {"low": 0, "high": 85},
            "disk_percent": {"low": 0, "high": 90},
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


def make_signals(normal_count, abnormal_count):
    """Generate test signals with known ground truth."""
    signals = []
    for i in range(normal_count):
        signals.append({"signal_type": "metric", "features": {"cpu_percent": 50 + i % 30}})
    for i in range(abnormal_count):
        signals.append({"signal_type": "metric", "features": {"cpu_percent": 92 + i % 8}})
    return signals


class TestRuleAgent:
    def test_evaluate_gt(self):
        rule = RuleAgent({"name": "test", "condition": {"field": "cpu", "operator": "gt", "value": 90}})
        assert rule.evaluate({"cpu": 95}) is True
        assert rule.evaluate({"cpu": 85}) is False

    def test_evaluate_lt(self):
        rule = RuleAgent({"name": "test", "condition": {"field": "temp", "operator": "lt", "value": 32}})
        assert rule.evaluate({"temp": 20}) is True
        assert rule.evaluate({"temp": 40}) is False

    def test_evaluate_contains(self):
        rule = RuleAgent({"name": "test", "condition": {"field": "msg", "operator": "contains", "value": "error"}})
        assert rule.evaluate({"msg": "fatal error occurred"}) is True
        assert rule.evaluate({"msg": "all systems normal"}) is False

    def test_evaluate_missing_field(self):
        rule = RuleAgent({"name": "test", "condition": {"field": "missing", "operator": "gt", "value": 0}})
        assert rule.evaluate({"cpu": 95}) is False


class TestBaseline:
    def test_normal_within_range(self, baseline):
        assert baseline.is_abnormal("metric", {"cpu_percent": 50}) is False

    def test_abnormal_above_range(self, baseline):
        assert baseline.is_abnormal("metric", {"cpu_percent": 95}) is True

    def test_unknown_signal_type(self, baseline):
        assert baseline.is_abnormal("unknown", {"cpu_percent": 99}) is False

    def test_missing_feature(self, baseline):
        assert baseline.is_abnormal("metric", {"other_field": 99}) is False


class TestValidation:
    def test_perfect_agent(self, engine, cpu_rule, baseline):
        # All abnormal signals have cpu > 90 (rule triggers), all normal have cpu < 80
        signals = make_signals(50, 50)
        agent = AgentMetrics(name="cpu_critical")
        result = engine.validate(agent, cpu_rule, signals, baseline)
        assert result.accuracy > 0.9
        assert result.false_positive_rate < 0.1
        assert result.rubric_status == "green"

    def test_bad_agent(self, engine, baseline):
        # Rule triggers on everything — high false positive rate
        always_fire = RuleAgent({"name": "bad", "condition": {"field": "cpu_percent", "operator": "gt", "value": 0}})
        signals = make_signals(80, 20)
        agent = AgentMetrics(name="bad_rule")
        result = engine.validate(agent, always_fire, signals, baseline)
        assert result.false_positive_rate > 0.5
        assert result.rubric_status == "red"

    def test_false_negative_rate_prevents_green_status(self, engine, baseline):
        never_fire = RuleAgent({
            "name": "unsafe",
            "condition": {"field": "cpu_percent", "operator": "gt", "value": 100},
        })
        signals = make_signals(80, 20)
        result = engine.validate(
            AgentMetrics(name="unsafe"), never_fire, signals, baseline
        )
        assert result.accuracy == pytest.approx(0.8)
        assert result.false_negative_rate == 1.0
        assert result.rubric_status == "red"

    def test_empty_signals(self, engine, cpu_rule, baseline):
        agent = AgentMetrics(name="test")
        result = engine.validate(agent, cpu_rule, [], baseline)
        assert result.accuracy == 0.0
        assert result.samples_tested == 0


class TestPromotion:
    def test_draft_to_candidate(self, engine, cpu_rule, baseline):
        signals = make_signals(30, 30)
        agent = AgentMetrics(name="cpu_critical", tier="draft")
        agent = engine.validate(agent, cpu_rule, signals, baseline)
        agent = engine.check_promotion(agent)
        assert agent.tier == "candidate"
        assert len(agent.promotion_history) == 1
        assert agent.promotion_history[0]["from_tier"] == "draft"
        assert agent.promotion_history[0]["to_tier"] == "candidate"

    def test_no_promotion_insufficient_samples(self, engine, cpu_rule, baseline):
        signals = make_signals(10, 10)  # Only 20 samples, need 50 for candidate
        agent = AgentMetrics(name="test", tier="draft")
        agent = engine.validate(agent, cpu_rule, signals, baseline)
        agent = engine.check_promotion(agent)
        assert agent.tier == "draft"

    def test_no_promotion_low_accuracy(self, engine, baseline):
        bad_rule = RuleAgent({"name": "bad", "condition": {"field": "cpu_percent", "operator": "gt", "value": 0}})
        signals = make_signals(80, 20)
        agent = AgentMetrics(name="bad", tier="draft")
        agent = engine.validate(agent, bad_rule, signals, baseline)
        agent = engine.check_promotion(agent)
        assert agent.tier == "draft"

    def test_candidate_to_nano(self, engine, cpu_rule, baseline):
        signals = make_signals(120, 120)
        agent = AgentMetrics(name="cpu_critical", tier="candidate")
        agent = engine.validate(agent, cpu_rule, signals, baseline)
        agent = engine.check_promotion(agent)
        assert agent.tier == "nano"

    def test_nano_to_micro_requires_human_review(self, engine, cpu_rule, baseline):
        signals = make_signals(300, 300)
        agent = AgentMetrics(name="cpu_critical", tier="nano")
        agent = engine.validate(agent, cpu_rule, signals, baseline)
        agent = engine.check_promotion(agent)
        # Should NOT promote without human review
        assert agent.tier == "nano"
        # Now mark as reviewed
        agent.human_reviewed = True
        agent = engine.check_promotion(agent)
        assert agent.tier == "micro"

    def test_macro_is_terminal(self, engine, cpu_rule, baseline):
        agent = AgentMetrics(name="test", tier="macro")
        agent = engine.check_promotion(agent)
        assert agent.tier == "macro"

    def test_full_ladder(self, engine, cpu_rule, baseline):
        """An agent can climb the full ladder with enough evidence."""
        agent = AgentMetrics(name="cpu_critical", tier="draft")

        # Draft → candidate (50 samples)
        agent = engine.validate(agent, cpu_rule, make_signals(30, 30), baseline)
        agent = engine.check_promotion(agent)
        assert agent.tier == "candidate"

        # Candidate → nano (200 samples)
        agent = engine.validate(agent, cpu_rule, make_signals(120, 120), baseline)
        agent = engine.check_promotion(agent)
        assert agent.tier == "nano"

        # Nano → micro (500 samples + human review)
        agent = engine.validate(agent, cpu_rule, make_signals(300, 300), baseline)
        agent.human_reviewed = True
        agent = engine.check_promotion(agent)
        assert agent.tier == "micro"

        assert len(agent.promotion_history) == 3


class TestValidationRound:
    def test_batch_validation(self, engine, baseline):
        agents = [
            AgentMetrics(name="cpu_rule", tier="draft"),
            AgentMetrics(name="mem_rule", tier="draft"),
        ]
        rules = [
            RuleAgent({"name": "cpu", "condition": {"field": "cpu_percent", "operator": "gt", "value": 90}}),
            RuleAgent({"name": "mem", "condition": {"field": "memory_percent", "operator": "gt", "value": 90}}),
        ]
        signals = make_signals(30, 30)
        results = engine.run_validation_round(agents, rules, signals, baseline)
        assert len(results) == 2
        assert all(a.samples_tested > 0 for a in results)

    def test_mismatched_lengths_raises(self, engine, baseline):
        with pytest.raises(ValueError):
            engine.run_validation_round(
                [AgentMetrics(name="a")],
                [RuleAgent({"name": "r1", "condition": {}}), RuleAgent({"name": "r2", "condition": {}})],
                [], baseline,
            )


class TestRubricMatrix:
    def test_all_green(self):
        agents = [AgentMetrics(name=f"a{i}", rubric_status="green") for i in range(5)]
        matrix = PromotionEngine.rubric_matrix(agents)
        assert matrix["overall"] == "green"
        assert matrix["green"] == 5

    def test_all_red(self):
        agents = [AgentMetrics(name=f"a{i}", rubric_status="red") for i in range(5)]
        matrix = PromotionEngine.rubric_matrix(agents)
        assert matrix["overall"] == "red"

    def test_mixed(self):
        agents = [
            AgentMetrics(name="a1", rubric_status="green"),
            AgentMetrics(name="a2", rubric_status="yellow"),
            AgentMetrics(name="a3", rubric_status="red"),
        ]
        matrix = PromotionEngine.rubric_matrix(agents)
        assert matrix["overall"] == "yellow"

    def test_empty(self):
        matrix = PromotionEngine.rubric_matrix([])
        assert matrix["overall"] == "red"
        assert matrix["total"] == 0

"""Tests for the cascade framework."""

from uuid import uuid4

import pytest

from cascade_compression.cascade import (
    CascadeAgent,
    CascadeDecision,
    CascadePipeline,
    CascadeRouter,
    Signal,
)
from cascade_compression.cascade.protocol import Outcome


# --- Test agents ---

class DedupeAgent:
    name = "dedupe"
    stage = 1

    def process(self, signals):
        seen = set()
        decisions = []
        for s in signals:
            key = (s.signal_type, s.source, s.namespace)
            if key in seen:
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.DEDUPE, evidence="duplicate",
                ))
            seen.add(key)
        return decisions


class SeverityClassifier:
    name = "severity_classifier"
    stage = 2

    def process(self, signals):
        decisions = []
        for s in signals:
            if s.severity == "info":
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.DROP, evidence="info severity",
                ))
            elif "critical" in s.content.get("message", "").lower():
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.ESCALATE, evidence="critical keyword",
                ))
        return decisions


class PatternMatcher:
    name = "pattern_matcher"
    stage = 2

    def process(self, signals):
        decisions = []
        for s in signals:
            if s.signal_type == "known_noise":
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.SUPPRESS, evidence="known noise pattern",
                ))
        return decisions


# --- Protocol tests ---

class TestProtocol:
    def test_signal_defaults(self):
        s = Signal()
        assert s.severity == "info"
        assert s.content == {}

    def test_signal_with_fields(self):
        s = Signal(signal_type="event", severity="high", namespace="prod")
        assert s.signal_type == "event"
        assert s.severity == "high"

    def test_cascade_decision(self):
        d = CascadeDecision(agent_name="test", outcome=Outcome.ESCALATE)
        assert d.outcome == Outcome.ESCALATE

    def test_agent_protocol(self):
        agent = DedupeAgent()
        assert isinstance(agent, CascadeAgent)
        assert agent.name == "dedupe"
        assert agent.stage == 1


# --- Pipeline tests ---

class TestPipeline:
    def test_empty_pipeline(self):
        pipeline = CascadePipeline()
        result = pipeline.run([Signal(severity="high")])
        assert result.total_signals == 1
        assert len(result.remaining) == 1

    def test_empty_signals(self):
        pipeline = CascadePipeline([DedupeAgent()])
        result = pipeline.run([])
        assert result.total_signals == 0
        assert result.compression_ratio == 0.0

    def test_dedupe(self):
        pipeline = CascadePipeline([DedupeAgent()])
        signals = [
            Signal(signal_type="event", source="pod-1", namespace="ns1", severity="high"),
            Signal(signal_type="event", source="pod-1", namespace="ns1", severity="high"),
            Signal(signal_type="event", source="pod-2", namespace="ns1", severity="high"),
        ]
        result = pipeline.run(signals)
        assert result.deduped_count == 1
        assert len(result.remaining) == 2

    def test_suppress(self):
        pipeline = CascadePipeline([PatternMatcher()])
        signals = [
            Signal(signal_type="known_noise", severity="low"),
            Signal(signal_type="real_alert", severity="high"),
        ]
        result = pipeline.run(signals)
        assert result.suppressed_count == 1
        assert len(result.remaining) == 1
        assert result.remaining[0].signal_type == "real_alert"

    def test_info_dropped(self):
        pipeline = CascadePipeline([SeverityClassifier()])
        signals = [
            Signal(severity="info"),
            Signal(severity="high"),
        ]
        result = pipeline.run(signals)
        assert result.dropped_count == 1
        assert len(result.remaining) == 1

    def test_escalate(self):
        pipeline = CascadePipeline([SeverityClassifier()])
        signals = [
            Signal(severity="high", content={"message": "CRITICAL failure"}),
        ]
        result = pipeline.run(signals)
        assert result.escalated_count == 1
        assert len(result.remaining) == 1

    def test_info_escalated_not_dropped(self):
        """Info signals that get escalated by an earlier agent should survive the info drop."""

        class EscalateFirst:
            name = "escalate_first"
            stage = 1
            def process(self, signals):
                return [CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.ESCALATE, evidence="anomaly detected",
                ) for s in signals if "CRITICAL" in s.content.get("message", "")]

        pipeline = CascadePipeline([EscalateFirst()])
        signals = [
            Signal(severity="info", content={"message": "CRITICAL anomaly"}),
            Signal(severity="info"),
        ]
        result = pipeline.run(signals)
        assert result.escalated_count == 1
        assert len(result.remaining) == 1

    def test_multi_stage_pipeline(self):
        pipeline = CascadePipeline([
            DedupeAgent(),
            PatternMatcher(),
            SeverityClassifier(),
        ])
        signals = [
            Signal(signal_type="event", source="pod-1", namespace="ns1", severity="high"),
            Signal(signal_type="event", source="pod-1", namespace="ns1", severity="high"),  # dup
            Signal(signal_type="known_noise", source="noisy", namespace="ns2", severity="low"),  # noise
            Signal(signal_type="heartbeat", source="monitor", namespace="ns3", severity="info"),  # info drop
            Signal(signal_type="alert", source="prom", namespace="ns4", severity="medium"),  # survives
        ]
        result = pipeline.run(signals)
        assert result.total_signals == 5
        assert result.deduped_count == 1
        assert result.suppressed_count == 1
        assert result.dropped_count == 1
        assert len(result.remaining) == 2  # high + medium survive

    def test_compression_ratio(self):
        pipeline = CascadePipeline([DedupeAgent(), PatternMatcher()])
        signals = [Signal(signal_type="known_noise", severity="low")] * 10 + \
                  [Signal(severity="high")]
        result = pipeline.run(signals)
        assert result.compression_ratio > 0.5

    def test_register(self):
        pipeline = CascadePipeline()
        pipeline.register(SeverityClassifier())
        pipeline.register(DedupeAgent())  # stage 1, should sort before stage 2
        assert pipeline._agents[0].stage == 1
        assert pipeline._agents[1].stage == 2

    def test_agent_error_skipped(self):
        class BrokenAgent:
            name = "broken"
            stage = 1
            def process(self, signals):
                raise RuntimeError("boom")

        pipeline = CascadePipeline([BrokenAgent(), SeverityClassifier()])
        signals = [Signal(severity="info")]
        result = pipeline.run(signals)
        assert result.dropped_count == 1


# --- Cascade router tests ---

class TestCascadeRouter:
    def test_route_classification(self):
        router = CascadeRouter()
        req = router.route(uuid4(), "classify-short", severity="low")
        assert req.lane == "classification"
        assert req.tier == "micro"
        assert req.max_tokens == 10

    def test_route_reasoning(self):
        router = CascadeRouter()
        req = router.route(uuid4(), "generate-qa", severity="critical")
        assert req.lane == "reasoning"
        assert req.tier == "macro"

    def test_severity_to_tier(self):
        assert CascadeRouter._severity_to_tier("critical") == "macro"
        assert CascadeRouter._severity_to_tier("high") == "macro"
        assert CascadeRouter._severity_to_tier("medium") == "micro"
        assert CascadeRouter._severity_to_tier("low") == "micro"

    def test_excluded_models(self):
        router = CascadeRouter(excluded_models={"gemma3-4b"})
        req = router.route(uuid4(), "classify-short")
        assert req.model != "gemma3-4b"

    def test_update_excluded(self):
        router = CascadeRouter()
        router.update_excluded({"phi4-mini"})
        req = router.route(uuid4(), "extract-medium")
        assert req.model != "phi4-mini"

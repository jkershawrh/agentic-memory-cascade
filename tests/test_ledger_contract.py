"""Contract tests for decision record and ledger integration.

Validates that ledger.py output matches contracts/schemas/decision-record.json.
These tests run WITHOUT a ledger server — they test the contract, not the wire.
"""

import json
from pathlib import Path
from uuid import uuid4

import jsonschema
import pytest

from cascade_compression.integrations.ledger import build_decision_record

SCHEMA_DIR = Path(__file__).parent.parent / "contracts" / "schemas"


@pytest.fixture
def decision_schema():
    with open(SCHEMA_DIR / "decision-record.json") as f:
        return json.load(f)


@pytest.fixture
def verdict_schema():
    with open(SCHEMA_DIR / "audit-verdict.json") as f:
        return json.load(f)


class FakeDecision:
    def __init__(self, signal_id, outcome="drop", agent="deduplicate", confidence=0.95, evidence="duplicate content"):
        self.signal_id = signal_id
        self.outcome = type("O", (), {"value": outcome})()
        self.agent_name = agent
        self.confidence = confidence
        self.evidence = evidence


class FakeSignal:
    def __init__(self, signal_id, signal_type="pod_crashloop", severity="high", namespace="production"):
        self.signal_id = signal_id
        self.signal_type = signal_type
        self.severity = severity
        self.namespace = namespace


class FakeResult:
    def __init__(self, decisions):
        self.decisions = decisions


class TestDecisionRecordSchema:
    def test_schema_is_valid_json_schema(self, decision_schema):
        jsonschema.Draft202012Validator.check_schema(decision_schema)

    def test_minimal_valid_record(self, decision_schema):
        record = {
            "system_id": "test-system",
            "decisions": [{
                "subject_id": "abc-123",
                "subject_type": "event_warning",
                "severity": "medium",
                "outcome": "drop",
                "agent": "severity_gate",
                "confidence": 0.9,
            }],
        }
        jsonschema.validate(record, decision_schema)

    def test_missing_system_id_fails(self, decision_schema):
        record = {"decisions": [{"subject_id": "x", "subject_type": "y",
                                 "severity": "info", "outcome": "keep",
                                 "agent": "a", "confidence": 1.0}]}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(record, decision_schema)

    def test_empty_decisions_fails(self, decision_schema):
        record = {"system_id": "test", "decisions": []}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(record, decision_schema)

    def test_invalid_severity_fails(self, decision_schema):
        record = {"system_id": "test", "decisions": [{
            "subject_id": "x", "subject_type": "y",
            "severity": "EXTREME", "outcome": "keep",
            "agent": "a", "confidence": 1.0,
        }]}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(record, decision_schema)

    def test_invalid_outcome_fails(self, decision_schema):
        record = {"system_id": "test", "decisions": [{
            "subject_id": "x", "subject_type": "y",
            "severity": "info", "outcome": "destroy",
            "agent": "a", "confidence": 1.0,
        }]}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(record, decision_schema)

    def test_confidence_out_of_range_fails(self, decision_schema):
        record = {"system_id": "test", "decisions": [{
            "subject_id": "x", "subject_type": "y",
            "severity": "info", "outcome": "keep",
            "agent": "a", "confidence": 1.5,
        }]}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(record, decision_schema)


class TestAuditVerdictSchema:
    def test_schema_is_valid_json_schema(self, verdict_schema):
        jsonschema.Draft202012Validator.check_schema(verdict_schema)

    def test_survives_verdict(self, verdict_schema):
        verdict = {
            "decision_ref": "abc-123",
            "subject_type": "event_warning",
            "original_outcome": "drop",
            "verdict": "SURVIVES",
            "checks_passed": ["severity", "confidence"],
            "checks_failed": [],
            "reason": "Low severity, high confidence drop — correct.",
        }
        jsonschema.validate(verdict, verdict_schema)

    def test_fails_verdict(self, verdict_schema):
        verdict = {
            "decision_ref": "abc-456",
            "subject_type": "pod_crashloop",
            "original_outcome": "drop",
            "verdict": "FAILS",
            "checks_passed": ["confidence"],
            "checks_failed": ["severity"],
            "reason": "High-severity signal was dropped.",
        }
        jsonschema.validate(verdict, verdict_schema)

    def test_invalid_verdict_value_fails(self, verdict_schema):
        verdict = {
            "decision_ref": "x",
            "subject_type": "y",
            "original_outcome": "drop",
            "verdict": "MAYBE",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(verdict, verdict_schema)


class TestBuildDecisionRecord:
    def test_produces_valid_schema(self, decision_schema):
        sig_id = uuid4()
        signals = [FakeSignal(sig_id)]
        decisions = [FakeDecision(sig_id)]
        result = FakeResult(decisions)

        record = build_decision_record(result, signals, "cascade-k8s", "kubernetes")
        jsonschema.validate(record, decision_schema)

    def test_system_id_set(self):
        sig_id = uuid4()
        record = build_decision_record(
            FakeResult([FakeDecision(sig_id)]),
            [FakeSignal(sig_id)],
            "cascade-aap", "aap",
        )
        assert record["system_id"] == "cascade-aap"
        assert record["domain"] == "aap"

    def test_batch_id_is_unique(self):
        sig_id = uuid4()
        r1 = build_decision_record(FakeResult([FakeDecision(sig_id)]), [FakeSignal(sig_id)], "s", "d")
        r2 = build_decision_record(FakeResult([FakeDecision(sig_id)]), [FakeSignal(sig_id)], "s", "d")
        assert r1["batch_id"] != r2["batch_id"]

    def test_decision_fields_mapped(self, decision_schema):
        sig_id = uuid4()
        signals = [FakeSignal(sig_id, signal_type="task_runner_on_failed", severity="high", namespace="prod")]
        decisions = [FakeDecision(sig_id, outcome="keep", agent="severity_gate", confidence=0.85)]
        result = FakeResult(decisions)

        record = build_decision_record(result, signals, "test", "aap")
        d = record["decisions"][0]
        assert d["subject_id"] == str(sig_id)
        assert d["subject_type"] == "task_runner_on_failed"
        assert d["severity"] == "high"
        assert d["outcome"] == "keep"
        assert d["agent"] == "severity_gate"
        assert d["confidence"] == 0.85
        jsonschema.validate(record, decision_schema)

    def test_empty_result_returns_empty_decisions(self):
        record = build_decision_record(FakeResult([]), [], "test", "k8s")
        assert record["decisions"] == []

    def test_missing_signal_handles_gracefully(self, decision_schema):
        sig_id = uuid4()
        other_id = uuid4()
        signals = [FakeSignal(other_id)]
        decisions = [FakeDecision(sig_id)]
        result = FakeResult(decisions)

        record = build_decision_record(result, signals, "test", "k8s")
        d = record["decisions"][0]
        assert d["subject_type"] == ""
        assert d["severity"] == ""


class TestWriteDecisions:
    def test_no_url_is_silent(self):
        from cascade_compression.integrations.ledger import write_decisions
        write_decisions("", "", FakeResult([]), [], "k8s")

    def test_no_decisions_is_silent(self):
        from cascade_compression.integrations.ledger import write_decisions
        write_decisions("http://fake:28099", "", FakeResult([]), [], "k8s")

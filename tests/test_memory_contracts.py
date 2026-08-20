"""Stage 0: Memory record and memory event schema validation.

Validates that memory data structures conform to their JSON Schema contracts.
These tests run WITHOUT a running service — they test the contract, not the wire.
"""

import json
from pathlib import Path

import jsonschema
import pytest

SCHEMA_DIR = Path(__file__).parent.parent / "contracts" / "schemas"


@pytest.fixture
def memory_schema():
    with open(SCHEMA_DIR / "memory-record.json") as f:
        return json.load(f)


@pytest.fixture
def event_schema():
    with open(SCHEMA_DIR / "memory-event.json") as f:
        return json.load(f)


class TestMemoryRecordSchema:
    def test_schema_is_valid_json_schema(self, memory_schema):
        jsonschema.Draft202012Validator.check_schema(memory_schema)

    def test_minimal_valid_record(self, memory_schema):
        record = {
            "memory_id": "abc-123",
            "signal_type": "pod_crashloop",
            "severity": "high",
            "formed_at": "2026-08-13T00:00:00Z",
            "strength": 0.7,
            "content_hash": "sha256:deadbeef",
        }
        jsonschema.validate(record, memory_schema)

    def test_full_valid_record(self, memory_schema):
        record = {
            "memory_id": "abc-123",
            "signal_type": "pod_crashloop",
            "severity": "high",
            "formed_at": "2026-08-13T00:00:00Z",
            "strength": 0.7,
            "content_hash": "sha256:deadbeef",
            "recall_count": 3,
            "last_recalled_at": "2026-08-13T12:00:00Z",
            "consolidation_count": 1,
            "source_instance": "instance-001",
            "classification": "real_incident",
            "content": {"message": "CrashLoopBackOff"},
            "labels": {"app": "web"},
            "feature_vector": {"cpu_percent": 95.0, "memory_percent": 88.0},
            "source": "node-01",
            "namespace": "production",
            "cluster": "oberon",
        }
        jsonschema.validate(record, memory_schema)

    def test_missing_memory_id_fails(self, memory_schema):
        record = {
            "signal_type": "x", "severity": "info",
            "formed_at": "2026-08-13T00:00:00Z",
            "strength": 0.5, "content_hash": "sha256:abc",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(record, memory_schema)

    def test_invalid_severity_fails(self, memory_schema):
        record = {
            "memory_id": "x", "signal_type": "y",
            "severity": "EXTREME",
            "formed_at": "2026-08-13T00:00:00Z",
            "strength": 0.5, "content_hash": "sha256:abc",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(record, memory_schema)

    def test_strength_above_one_fails(self, memory_schema):
        record = {
            "memory_id": "x", "signal_type": "y", "severity": "info",
            "formed_at": "2026-08-13T00:00:00Z",
            "strength": 1.5, "content_hash": "sha256:abc",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(record, memory_schema)

    def test_strength_below_zero_fails(self, memory_schema):
        record = {
            "memory_id": "x", "signal_type": "y", "severity": "info",
            "formed_at": "2026-08-13T00:00:00Z",
            "strength": -0.1, "content_hash": "sha256:abc",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(record, memory_schema)

    def test_feature_vector_non_numeric_fails(self, memory_schema):
        record = {
            "memory_id": "x", "signal_type": "y", "severity": "info",
            "formed_at": "2026-08-13T00:00:00Z",
            "strength": 0.5, "content_hash": "sha256:abc",
            "feature_vector": {"cpu": "high"},
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(record, memory_schema)


class TestMemoryEventSchema:
    def test_schema_is_valid_json_schema(self, event_schema):
        jsonschema.Draft202012Validator.check_schema(event_schema)

    def test_formed_event(self, event_schema):
        event = {
            "memory_id": "abc-123",
            "event_type": "formed",
            "timestamp": "2026-08-13T00:00:00Z",
            "details": {"initial_strength": 0.7},
        }
        jsonschema.validate(event, event_schema)

    def test_evicted_event(self, event_schema):
        event = {
            "memory_id": "abc-123",
            "event_type": "evicted",
            "timestamp": "2026-08-13T00:00:00Z",
            "details": {"final_strength": 0.02, "reason": "below_threshold"},
        }
        jsonschema.validate(event, event_schema)

    def test_invalid_event_type_fails(self, event_schema):
        event = {
            "memory_id": "abc-123",
            "event_type": "destroyed",
            "timestamp": "2026-08-13T00:00:00Z",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(event, event_schema)

    def test_missing_event_type_fails(self, event_schema):
        event = {
            "memory_id": "abc-123",
            "timestamp": "2026-08-13T00:00:00Z",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(event, event_schema)


class TestMemoryToDict:
    """Validates that Memory.to_dict() produces schema-compliant output."""

    def test_memory_to_dict_validates(self, memory_schema):
        from cascade_compression.cascade.memory import Memory, MemoryArchive
        from cascade_compression.cascade.protocol import Signal

        signal = Signal(signal_type="pod_crashloop", severity="high",
                        source="node-01", namespace="production",
                        content={"message": "CrashLoopBackOff"})
        archive = MemoryArchive()
        memory = archive.store(signal, classification="real_incident")
        record = memory.to_dict()
        jsonschema.validate(record, memory_schema)

    def test_memory_event_to_dict_validates(self, event_schema):
        from cascade_compression.cascade.memory import MemoryEvent
        from uuid import uuid4

        event = MemoryEvent(
            memory_id=uuid4(),
            event_type="formed",
            details={"initial_strength": 0.7},
        )
        record = event.to_dict()
        jsonschema.validate(record, event_schema)

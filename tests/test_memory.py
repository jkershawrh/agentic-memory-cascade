"""Memory archive tests — TDD RED → GREEN.

Stage 1 (TDD): Mathematical and logical correctness of MemoryArchive.
Stage 2 (BDD): Behavioral scenarios for memory formation.
Stage 3 (BDD): API endpoint compliance.

RED tests — written before implementation.
"""

import time

import pytest

from cascade_compression.cascade.protocol import Signal
from cascade_compression.cascade.memory import Memory, MemoryArchive, MemoryEvent


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


# ===========================================================================
# Stage 1: TDD — Mathematical and Logical Correctness
# ===========================================================================

class TestMemoryStore:
    def test_store_creates_memory_with_uuid(self):
        """Storing a signal returns a Memory with a unique ID."""
        archive = MemoryArchive()
        signal = make_signal()
        memory = archive.store(signal, classification="real_incident")
        assert memory.memory_id is not None
        assert isinstance(memory, Memory)

    def test_store_creates_unique_ids(self):
        """Each stored memory gets a distinct ID."""
        archive = MemoryArchive()
        m1 = archive.store(make_signal(signal_type="a"), classification="x")
        m2 = archive.store(make_signal(signal_type="b"), classification="y")
        assert m1.memory_id != m2.memory_id

    def test_store_captures_signal_snapshot(self):
        """Stored memory contains the original signal data."""
        archive = MemoryArchive()
        signal = make_signal(signal_type="disk_pressure", severity="critical")
        memory = archive.store(signal, classification="real_incident")
        assert memory.signal.signal_type == "disk_pressure"
        assert memory.signal.severity == "critical"

    def test_store_sets_initial_strength(self):
        """Initial strength is derived from severity weight."""
        archive = MemoryArchive()
        memory = archive.store(make_signal(severity="critical"), classification="x")
        assert memory.strength > 0.0
        assert memory.strength <= 1.0

    def test_store_sets_formed_at(self):
        """formed_at is set to an ISO timestamp."""
        archive = MemoryArchive()
        memory = archive.store(make_signal(), classification="x")
        assert "T" in memory.formed_at

    def test_store_sets_content_hash(self):
        """content_hash is a non-empty string."""
        archive = MemoryArchive()
        memory = archive.store(make_signal(), classification="x")
        assert len(memory.content_hash) > 0

    def test_store_increments_archive_size(self):
        """Archive size increases by 1 per store."""
        archive = MemoryArchive()
        assert archive.size == 0
        archive.store(make_signal(), classification="x")
        assert archive.size == 1

    def test_store_emits_formed_event(self):
        """Storing a memory emits a 'formed' MemoryEvent."""
        archive = MemoryArchive()
        archive.store(make_signal(), classification="x")
        events = archive.drain_events()
        assert len(events) == 1
        assert events[0].event_type == "formed"


class TestMemoryAnalysis:
    def test_store_with_analysis(self):
        archive = MemoryArchive()
        analysis = {"root_cause": "ceph pool full", "confidence": 0.9}
        m = archive.store(make_signal(), classification="real_incident",
                          metadata={"analysis": analysis})
        assert m.analysis == analysis
        assert m.analysis["root_cause"] == "ceph pool full"

    def test_analysis_in_to_dict(self):
        archive = MemoryArchive()
        analysis = {"root_cause": "test", "remediation": ["fix it"]}
        m = archive.store(make_signal(), metadata={"analysis": analysis})
        d = m.to_dict()
        assert d["analysis"] == analysis

    def test_analysis_roundtrip_from_dict(self):
        from cascade_compression.cascade.memory import Memory
        archive = MemoryArchive()
        analysis = {"root_cause": "storage", "confidence": 0.85}
        m = archive.store(make_signal(), metadata={"analysis": analysis})
        d = m.to_dict()
        restored = Memory.from_dict(d)
        assert restored.analysis == analysis

    def test_store_without_analysis(self):
        archive = MemoryArchive()
        m = archive.store(make_signal())
        assert m.analysis is None

    def test_reinforce_updates_analysis(self):
        archive = MemoryArchive()
        sig = make_signal(signal_type="test_type")
        m1 = archive.store(sig)
        assert m1.analysis is None
        m2 = archive.store(sig, metadata={"analysis": {"root_cause": "found it"}})
        assert m2.analysis["root_cause"] == "found it"
        assert m2.memory_id == m1.memory_id


class TestMemoryQuery:
    def test_query_returns_all_when_no_filters(self):
        """Query with no filters returns all memories."""
        archive = MemoryArchive()
        archive.store(make_signal(signal_type="a"), classification="x")
        archive.store(make_signal(signal_type="b"), classification="y")
        results = archive.query()
        assert len(results) == 2

    def test_query_by_signal_type(self):
        """Query filters by signal_type exactly."""
        archive = MemoryArchive()
        archive.store(make_signal(signal_type="pod_crashloop"), classification="x")
        archive.store(make_signal(signal_type="disk_pressure"), classification="y")
        results = archive.query(signal_type="pod_crashloop")
        assert len(results) == 1
        assert results[0].signal.signal_type == "pod_crashloop"

    def test_query_by_min_strength(self):
        """Query excludes memories below min_strength."""
        archive = MemoryArchive()
        archive.store(make_signal(severity="info"), classification="x")
        archive.store(make_signal(severity="critical"), classification="y")
        results = archive.query(min_strength=0.5)
        assert all(m.strength >= 0.5 for m in results)

    def test_query_by_labels(self):
        """Query filters by label key-value match."""
        archive = MemoryArchive()
        archive.store(make_signal(labels={"app": "web"}), classification="x")
        archive.store(make_signal(labels={"app": "api"}), classification="y")
        results = archive.query(labels={"app": "web"})
        assert len(results) == 1

    def test_query_limit(self):
        """Query respects the limit parameter."""
        archive = MemoryArchive()
        for i in range(10):
            archive.store(make_signal(signal_type=f"type_{i}"), classification="x")
        results = archive.query(limit=3)
        assert len(results) == 3

    def test_query_returns_strongest_first(self):
        """Results are sorted by strength descending."""
        archive = MemoryArchive()
        archive.store(make_signal(severity="info", content={"message": "routine"}),
                      classification="x")
        archive.store(make_signal(severity="critical", content={"message": "critical event"}),
                      classification="y")
        results = archive.query()
        assert len(results) == 2
        assert results[0].strength >= results[1].strength


class TestMemoryGet:
    def test_get_by_id(self):
        """Get retrieves a specific memory by ID."""
        archive = MemoryArchive()
        memory = archive.store(make_signal(), classification="x")
        found = archive.get(memory.memory_id)
        assert found is not None
        assert found.memory_id == memory.memory_id

    def test_get_missing_returns_none(self):
        """Get returns None for unknown IDs."""
        from uuid import uuid4
        archive = MemoryArchive()
        assert archive.get(uuid4()) is None


class TestCapacityAndEviction:
    def test_capacity_evicts_weakest(self):
        """Archive at capacity evicts lowest-strength memories."""
        archive = MemoryArchive(max_capacity=5)
        for i in range(5):
            archive.store(make_signal(signal_type=f"type_{i}", severity="info"),
                          classification="x")
        archive.store(make_signal(signal_type="critical_one", severity="critical"),
                      classification="y")
        assert archive.size <= 5

    def test_eviction_preserves_strongest(self):
        """After eviction, strongest memories remain."""
        archive = MemoryArchive(max_capacity=5)
        for i in range(5):
            archive.store(make_signal(signal_type=f"weak_{i}", severity="info"),
                          classification="x")
        strong = archive.store(
            make_signal(signal_type="strong", severity="critical"),
            classification="y",
        )
        assert archive.get(strong.memory_id) is not None

    def test_eviction_emits_events(self):
        """Each eviction produces a MemoryEvent with type='evicted'."""
        archive = MemoryArchive(max_capacity=3)
        for i in range(3):
            archive.store(make_signal(signal_type=f"type_{i}", severity="info"),
                          classification="x")
        archive.drain_events()
        archive.store(make_signal(signal_type="trigger_eviction", severity="critical"),
                      classification="y")
        events = archive.drain_events()
        eviction_events = [e for e in events if e.event_type == "evicted"]
        assert len(eviction_events) > 0


class TestStrength:
    def test_severity_weight_ordering(self):
        """Higher severity produces higher initial strength."""
        archive = MemoryArchive()
        m_info = archive.store(make_signal(severity="info", content={"message": "info event"}),
                               classification="x")
        m_low = archive.store(make_signal(severity="low", content={"message": "low event"}),
                              classification="x")
        m_med = archive.store(make_signal(severity="medium", content={"message": "med event"}),
                              classification="x")
        m_high = archive.store(make_signal(severity="high", content={"message": "high event"}),
                               classification="x")
        m_crit = archive.store(make_signal(severity="critical", content={"message": "crit event"}),
                               classification="x")
        assert m_info.strength < m_low.strength
        assert m_low.strength < m_med.strength
        assert m_med.strength < m_high.strength
        assert m_high.strength < m_crit.strength

    def test_decay_reduces_strength(self):
        """decay_all reduces all strengths by exponential factor."""
        archive = MemoryArchive()
        memory = archive.store(make_signal(severity="critical"), classification="x")
        original = memory.strength
        archive.decay_all(lambda_rate=0.01, hours_elapsed=100)
        assert memory.strength < original

    def test_decay_all_typed_uses_per_type_rates(self):
        """Different signal types decay at different rates."""
        from cascade_compression.cascade.memory_intelligence import DecayConfig
        config = DecayConfig(default_rate=0.01)
        config.set_rate("event_deprecatedannotation", 0.1)
        config.set_rate("node_notready", 0.001)

        archive = MemoryArchive()
        archive.set_decay_config(config)
        m_fast = archive.store(make_signal(signal_type="event_deprecatedannotation",
                                           content={"message": "fast decay"}),
                               classification="x")
        m_slow = archive.store(make_signal(signal_type="node_notready",
                                           content={"message": "slow decay"}),
                               classification="x")
        m_fast.strength = 0.5
        m_slow.strength = 0.5
        archive.decay_all_typed(hours_elapsed=10)
        assert m_fast.strength < m_slow.strength

    def test_decay_all_typed_falls_back_to_default(self):
        """Unknown types use default rate."""
        from cascade_compression.cascade.memory_intelligence import DecayConfig
        config = DecayConfig(default_rate=0.05)
        archive = MemoryArchive()
        archive.set_decay_config(config)
        m = archive.store(make_signal(signal_type="unknown_type",
                                      content={"message": "unknown"}),
                          classification="x")
        original = m.strength
        archive.decay_all_typed(hours_elapsed=10)
        import math
        expected = original * math.exp(-0.05 * 10)
        assert abs(m.strength - expected) < 0.001

    def test_decay_all_typed_no_config_is_noop(self):
        """Without decay config, decay_all_typed does nothing."""
        archive = MemoryArchive()
        m = archive.store(make_signal(), classification="x")
        original = m.strength
        archive.decay_all_typed(hours_elapsed=100)
        assert m.strength == original

    def test_set_decay_config(self):
        """Decay config can be set after construction."""
        from cascade_compression.cascade.memory_intelligence import DecayConfig
        archive = MemoryArchive()
        assert archive._decay_config is None
        config = DecayConfig(default_rate=0.02)
        archive.set_decay_config(config)
        assert archive._decay_config is not None

    def test_decay_never_negative(self):
        """Strength never goes below zero after decay."""
        archive = MemoryArchive()
        memory = archive.store(make_signal(severity="info"), classification="x")
        archive.decay_all(lambda_rate=1.0, hours_elapsed=1000)
        assert memory.strength >= 0.0

    def test_reinforce_increases_strength(self):
        """Reinforcement increases strength."""
        archive = MemoryArchive()
        memory = archive.store(make_signal(severity="medium"), classification="x")
        original = memory.strength
        archive.reinforce(memory.memory_id)
        assert memory.strength > original

    def test_reinforce_asymptotic_to_one(self):
        """Repeated reinforcement approaches 1.0 but never exceeds it."""
        archive = MemoryArchive()
        memory = archive.store(make_signal(severity="critical"), classification="x")
        for _ in range(100):
            archive.reinforce(memory.memory_id)
        assert memory.strength <= 1.0
        assert memory.strength > 0.95


class TestContentNormalization:
    def test_normalize_strips_uuids(self):
        """Content with UUIDs normalizes identically."""
        from cascade_compression.cascade.memory import _normalize_content
        c1 = {"message": "Pod abc-12345678-1234-1234-1234-123456789abc crashed"}
        c2 = {"message": "Pod abc-87654321-4321-4321-4321-abcdef123456 crashed"}
        assert _normalize_content(c1) == _normalize_content(c2)

    def test_normalize_strips_pod_suffixes(self):
        """K8s pod suffixes are stripped."""
        from cascade_compression.cascade.memory import _normalize_content
        c1 = {"message": "Pod api-server-abc12 failed"}
        c2 = {"message": "Pod api-server-xyz99 failed"}
        assert _normalize_content(c1) == _normalize_content(c2)

    def test_normalize_strips_timestamps(self):
        """ISO timestamps are stripped."""
        from cascade_compression.cascade.memory import _normalize_content
        c1 = {"message": "Error at 2026-08-14T10:00:00Z"}
        c2 = {"message": "Error at 2026-08-15T15:30:00+00:00"}
        assert _normalize_content(c1) == _normalize_content(c2)

    def test_normalize_preserves_structure(self):
        """Non-dynamic content is preserved."""
        from cascade_compression.cascade.memory import _normalize_content
        c = {"message": "OOMKilled", "severity": "critical", "count": 5}
        n = _normalize_content(c)
        assert n["message"] == "OOMKilled"
        assert n["severity"] == "critical"
        assert n["count"] == 5

    def test_duplicate_pod_events_dedup(self):
        """Two K8s events differing only by pod suffix produce same hash."""
        archive = MemoryArchive()
        m1 = archive.store(
            make_signal(signal_type="pod_crashloop",
                        content={"message": "Pod web-app-abc12 CrashLoopBackOff"}),
            classification="x")
        m2 = archive.store(
            make_signal(signal_type="pod_crashloop",
                        content={"message": "Pod web-app-xyz99 CrashLoopBackOff"}),
            classification="x")
        assert m1.memory_id == m2.memory_id
        assert archive.size == 1

    def test_different_messages_still_separate(self):
        """Genuinely different content still creates separate memories."""
        archive = MemoryArchive()
        archive.store(make_signal(content={"message": "OOMKilled"}), classification="x")
        archive.store(make_signal(content={"message": "disk full"}), classification="x")
        assert archive.size == 2


class TestDedup:
    def test_same_content_reinforces_existing(self):
        """Storing a signal with same content_hash reinforces the existing memory."""
        archive = MemoryArchive()
        sig = make_signal(signal_type="pod_crashloop",
                          content={"message": "CrashLoopBackOff"})
        m1 = archive.store(sig, classification="x")
        original_strength = m1.strength

        sig2 = make_signal(signal_type="pod_crashloop",
                           content={"message": "CrashLoopBackOff"})
        m2 = archive.store(sig2, classification="x")
        assert m2.memory_id == m1.memory_id
        assert m2.strength > original_strength
        assert archive.size == 1

    def test_different_content_creates_new(self):
        """Different content creates a separate memory."""
        archive = MemoryArchive()
        archive.store(make_signal(content={"message": "error A"}), classification="x")
        archive.store(make_signal(content={"message": "error B"}), classification="x")
        assert archive.size == 2


class TestStats:
    def test_stats_reports_size(self):
        """Stats include archive size."""
        archive = MemoryArchive()
        archive.store(make_signal(), classification="x")
        stats = archive.stats()
        assert stats["size"] == 1

    def test_stats_reports_formed_total(self):
        """Stats track total memories ever formed."""
        archive = MemoryArchive()
        archive.store(make_signal(signal_type="a"), classification="x")
        archive.store(make_signal(signal_type="b"), classification="y")
        stats = archive.stats()
        assert stats["formed_total"] == 2

    def test_stats_reports_evictions(self):
        """Stats track total evictions."""
        archive = MemoryArchive(max_capacity=2)
        for i in range(3):
            archive.store(make_signal(signal_type=f"t_{i}"), classification="x")
        stats = archive.stats()
        assert stats["evictions_total"] > 0


class TestSerialization:
    def test_to_dict_roundtrip(self):
        """to_dict -> from_dict produces identical archive."""
        archive = MemoryArchive()
        archive.store(make_signal(signal_type="pod_crashloop", severity="high",
                                  labels={"app": "web"},
                                  content={"message": "crash"}),
                      classification="real_incident")
        archive.store(make_signal(signal_type="disk_pressure", severity="critical"),
                      classification="needs_attention")

        data = archive.to_dict()
        restored = MemoryArchive.from_dict(data)

        assert restored.size == archive.size
        for orig in archive.query():
            found = restored.get(orig.memory_id)
            assert found is not None
            assert found.strength == orig.strength
            assert found.signal.signal_type == orig.signal.signal_type
            assert found.content_hash == orig.content_hash
            assert found.classification == orig.classification

    def test_from_dict_empty(self):
        """from_dict with empty data produces empty archive."""
        archive = MemoryArchive.from_dict({"memories": [], "stats": {}})
        assert archive.size == 0


# ===========================================================================
# Stage 2: BDD — Behavioral Scenarios
# ===========================================================================

class TestMemoryFormationBehavior:
    def test_survivor_becomes_memory(self):
        """GIVEN signals processed through cascade
        WHEN some survive (remain after pipeline)
        THEN each survivor is stored in memory_archive with initial strength > 0"""
        archive = MemoryArchive()
        survivor = make_signal(signal_type="disk_pressure", severity="critical")
        memory = archive.store(survivor, classification="real_incident")
        assert memory.strength > 0
        assert memory.signal.signal_type == "disk_pressure"
        assert archive.size == 1

    def test_high_severity_survivor_stronger(self):
        """GIVEN a critical survivor and an info survivor
        WHEN both are stored
        THEN critical memory has higher initial strength"""
        archive = MemoryArchive()
        m_info = archive.store(make_signal(severity="info"), classification="x")
        m_crit = archive.store(make_signal(severity="critical",
                                           content={"message": "different"}),
                               classification="y")
        assert m_crit.strength > m_info.strength

    def test_duplicate_signal_reinforces(self):
        """GIVEN a memory already exists for signal_type+content
        WHEN same signal survives again
        THEN memory strength increases, recall_count unchanged"""
        archive = MemoryArchive()
        content = {"message": "OOMKilled"}
        m1 = archive.store(make_signal(content=content), classification="x")
        original_strength = m1.strength
        original_recall = m1.recall_count

        m2 = archive.store(make_signal(content=content), classification="x")
        assert m2.strength > original_strength
        assert m2.recall_count == original_recall

    def test_memory_archive_survives_high_volume(self):
        """GIVEN 1000 unique signals
        WHEN all stored in archive with capacity 500
        THEN archive size stays within bounds and strongest survive"""
        archive = MemoryArchive(max_capacity=500)
        for i in range(1000):
            sev = "critical" if i % 100 == 0 else "info"
            archive.store(
                make_signal(signal_type=f"type_{i}", severity=sev,
                            content={"message": f"event {i}"}),
                classification="x",
            )
        assert archive.size <= 500
        results = archive.query(min_strength=0.5)
        assert len(results) > 0


# ===========================================================================
# Stage 3: API — Endpoint Compliance (tested in test_api_memory.py if needed)
# ===========================================================================

class TestMemoryAPIIntegration:
    """Basic API behavior tested via FastAPI TestClient."""

    def test_stats_endpoint(self):
        """GET /memories/stats returns valid stats object."""
        from fastapi.testclient import TestClient
        from cascade_compression.service import app

        client = TestClient(app)
        resp = client.get("/memories/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "size" in data
        assert "formed_total" in data

    def test_query_endpoint(self):
        """POST /memories/query returns matching memories."""
        from fastapi.testclient import TestClient
        from cascade_compression.service import app

        client = TestClient(app)
        resp = client.post("/memories/query", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "memories" in data

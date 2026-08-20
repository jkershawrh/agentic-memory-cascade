"""Federation tests — TDD RED → GREEN.

Stage 1 (TDD): Export/import mechanics, cross-source correlation.
Stage 2 (BDD): Multi-instance memory sharing scenarios.

RED tests — written before implementation.
"""

import pytest

from cascade_compression.cascade.memory import MemoryArchive
from cascade_compression.cascade.protocol import Signal


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
# Stage 1: TDD — Export / Import Mechanics
# ===========================================================================

class TestExport:
    def test_export_includes_instance_id(self):
        """Export payload contains the instance's unique ID."""
        archive = MemoryArchive(instance_id="source-A")
        archive.store(make_signal(), classification="x")
        exported = archive.export_memories()
        assert exported["instance_id"] == "source-A"

    def test_export_includes_memories(self):
        """Export includes serialized memories."""
        archive = MemoryArchive()
        archive.store(make_signal(signal_type="incident_1",
                                  content={"message": "outage"}),
                      classification="real_incident")
        exported = archive.export_memories()
        assert len(exported["memories"]) == 1
        assert exported["memories"][0]["signal_type"] == "incident_1"

    def test_export_min_strength_filter(self):
        """Export respects min_strength filter."""
        archive = MemoryArchive()
        archive.store(make_signal(severity="info", content={"message": "weak"}),
                      classification="x")
        archive.store(make_signal(severity="critical", content={"message": "strong"}),
                      classification="y")
        exported = archive.export_memories(min_strength=0.5)
        assert len(exported["memories"]) == 1
        assert exported["memories"][0]["severity"] == "critical"

    def test_export_empty_archive(self):
        """Exporting empty archive returns empty list."""
        archive = MemoryArchive()
        exported = archive.export_memories()
        assert exported["memories"] == []


class TestImport:
    def test_import_stores_memories(self):
        """Imported memories are added to the archive."""
        source = MemoryArchive(instance_id="source-A")
        source.store(make_signal(signal_type="outage",
                                 content={"message": "production down"}),
                     classification="real_incident")
        exported = source.export_memories()

        target = MemoryArchive(instance_id="target-B")
        target.import_memories(exported)
        assert target.size == 1

    def test_import_preserves_source_instance(self):
        """Imported memories have source_instance set to exporter's ID."""
        source = MemoryArchive(instance_id="cluster-alpha")
        source.store(make_signal(content={"message": "event A"}),
                     classification="x")
        exported = source.export_memories()

        target = MemoryArchive(instance_id="cluster-beta")
        target.import_memories(exported)
        memories = target.query()
        assert len(memories) == 1
        assert memories[0].source_instance == "cluster-alpha"

    def test_import_dedup_same_content(self):
        """Importing a memory with same content_hash as existing reinforces."""
        archive = MemoryArchive()
        content = {"message": "shared incident"}
        m1 = archive.store(make_signal(content=content), classification="x")
        original_strength = m1.strength

        # Simulate export from another instance with same content
        other = MemoryArchive(instance_id="other")
        other.store(make_signal(content=content), classification="x")
        exported = other.export_memories()

        archive.import_memories(exported)
        assert archive.size == 1
        assert m1.strength > original_strength


class TestCrossSourceCorrelation:
    def test_same_content_from_two_sources_boosts_strength(self):
        """GIVEN same content_hash from 2 different instances
        WHEN both imported into a third
        THEN memory strength is boosted beyond either individual."""
        target = MemoryArchive(instance_id="central")
        content = {"message": "network partition detected"}

        source_a = MemoryArchive(instance_id="cluster-a")
        source_a.store(make_signal(severity="high", content=content),
                       classification="incident")
        target.import_memories(source_a.export_memories())

        first_memory = target.query()[0]
        strength_after_first = first_memory.strength

        source_b = MemoryArchive(instance_id="cluster-b")
        source_b.store(make_signal(severity="high", content=content),
                       classification="incident")
        target.import_memories(source_b.export_memories())

        assert first_memory.strength > strength_after_first

    def test_federated_sources_count(self):
        """Stats report distinct source instances."""
        target = MemoryArchive(instance_id="central")

        for name in ["alpha", "beta", "gamma"]:
            src = MemoryArchive(instance_id=name)
            src.store(make_signal(content={"message": f"event from {name}"}),
                      classification="x")
            target.import_memories(src.export_memories())

        stats = target.stats()
        assert stats.get("federated_sources", 0) >= 3


# ===========================================================================
# Stage 2: BDD — Multi-Instance Scenarios
# ===========================================================================

class TestFederationBehavior:
    def test_roundtrip_export_import(self):
        """GIVEN instance A with memories
        WHEN exported and imported to instance B
        THEN instance B can query them with correct metadata."""
        a = MemoryArchive(instance_id="site-east")
        a.store(make_signal(signal_type="disk_failure", severity="critical",
                            content={"message": "disk SMART failure"},
                            labels={"rack": "42"}),
                classification="real_incident")
        a.store(make_signal(signal_type="cpu_throttle", severity="medium",
                            content={"message": "CPU throttled to 80%"}),
                classification="needs_attention")
        exported = a.export_memories()

        b = MemoryArchive(instance_id="site-west")
        b.import_memories(exported)
        assert b.size == 2
        disk_memories = b.query(signal_type="disk_failure")
        assert len(disk_memories) == 1
        assert disk_memories[0].source_instance == "site-east"

    def test_import_does_not_exceed_capacity(self):
        """Importing into a full archive triggers eviction, not overflow."""
        target = MemoryArchive(instance_id="small", max_capacity=5)
        for i in range(5):
            target.store(make_signal(severity="info",
                                     content={"message": f"local {i}"}),
                         classification="x")

        source = MemoryArchive(instance_id="big")
        for i in range(3):
            source.store(make_signal(severity="critical",
                                     content={"message": f"remote {i}"}),
                         classification="y")
        target.import_memories(source.export_memories())
        assert target.size <= 5


class TestIncrementalExport:
    def test_export_with_since_filters(self):
        """Only memories modified after 'since' are exported."""
        archive = MemoryArchive()
        m1 = archive.store(make_signal(content={"message": "old event"}),
                           classification="x")
        m1.last_modified_at = "2026-08-14T10:00:00+00:00"

        m2 = archive.store(make_signal(content={"message": "new event"}),
                           classification="y")
        m2.last_modified_at = "2026-08-14T12:00:00+00:00"

        exported = archive.export_memories(since="2026-08-14T11:00:00+00:00")
        assert len(exported["memories"]) == 1
        assert exported["memories"][0]["content"]["message"] == "new event"

    def test_export_without_since_returns_all(self):
        """Backward compatible: no since = export everything."""
        archive = MemoryArchive()
        archive.store(make_signal(content={"message": "a"}), classification="x")
        archive.store(make_signal(content={"message": "b"}), classification="y")
        exported = archive.export_memories()
        assert len(exported["memories"]) == 2


class TestRejectionSet:
    def test_eviction_adds_to_rejection_set(self):
        """Evicted content hashes are added to rejection set."""
        archive = MemoryArchive(max_capacity=3)
        hashes = []
        for i in range(3):
            m = archive.store(make_signal(severity="info",
                                          content={"message": f"weak {i}"}),
                              classification="x")
            hashes.append(m.content_hash)
        archive.store(make_signal(severity="critical",
                                  content={"message": "strong"}),
                      classification="y")
        assert len(archive._rejection_set) > 0

    def test_import_skips_rejected_hashes(self):
        """Memories with rejected content hashes are not imported."""
        target = MemoryArchive(max_capacity=3)
        for i in range(3):
            target.store(make_signal(severity="info",
                                     content={"message": f"fill {i}"}),
                         classification="x")
        target.store(make_signal(severity="critical",
                                 content={"message": "trigger eviction"}),
                     classification="y")
        rejected_hashes = list(target._rejection_set)
        assert len(rejected_hashes) > 0

        source = MemoryArchive(instance_id="remote")
        source.store(make_signal(severity="info",
                                  content={"message": "fill 0"}),
                     classification="x")
        exported = source.export_memories()
        size_before = target.size
        target.import_memories(exported)
        assert target.size == size_before

    def test_re_import_cycle_does_not_reinforce_rejected(self):
        """Full cycle: evict → re-export from source → import → rejected."""
        source = MemoryArchive(instance_id="source")
        target = MemoryArchive(instance_id="target", max_capacity=5)

        weak = source.store(make_signal(severity="info",
                                         content={"message": "noise signal"}),
                            classification="noise")
        target.import_memories(source.export_memories())
        assert target.size == 1

        for i in range(5):
            target.store(make_signal(severity="critical",
                                      content={"message": f"strong {i}"}),
                         classification="important")

        assert weak.content_hash in target._rejection_set

        size_before = target.size
        target.import_memories(source.export_memories())
        assert target.size == size_before

    def test_rejection_set_persisted_in_state(self):
        """Rejection set survives to_dict/from_dict roundtrip."""
        archive = MemoryArchive(max_capacity=2)
        archive.store(make_signal(severity="info", content={"message": "a"}),
                      classification="x")
        archive.store(make_signal(severity="info", content={"message": "b"}),
                      classification="x")
        archive.store(make_signal(severity="critical", content={"message": "c"}),
                      classification="y")

        data = archive.to_dict()
        restored = MemoryArchive.from_dict(data)
        assert len(restored._rejection_set) == len(archive._rejection_set)
        assert set(restored._rejection_set) == set(archive._rejection_set)

    def test_rejection_set_capped(self):
        """Rejection set doesn't grow unbounded."""
        archive = MemoryArchive(max_capacity=2)
        for i in range(100):
            archive.store(make_signal(severity="info",
                                       content={"message": f"noise {i}"}),
                          classification="x")
        assert len(archive._rejection_set) <= archive._rejection_max


class TestFederationAPI:
    def test_export_endpoint(self):
        """GET /memories/export returns instance_id and memories."""
        from fastapi.testclient import TestClient
        from cascade_compression.service import app

        client = TestClient(app)
        resp = client.get("/memories/export")
        assert resp.status_code == 200
        data = resp.json()
        assert "instance_id" in data
        assert "memories" in data

    def test_import_endpoint(self):
        """POST /memories/import accepts exported data."""
        from fastapi.testclient import TestClient
        from cascade_compression.service import app

        client = TestClient(app)
        payload = {
            "instance_id": "remote-source",
            "memories": [],
        }
        resp = client.post("/memories/import", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "imported" in data

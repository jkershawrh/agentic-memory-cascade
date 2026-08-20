"""Consolidation tests — TDD RED → GREEN.

Stage 1 (TDD): Consolidation mechanics — re-cascade old memories, decay, eviction.
Stage 2 (BDD): Behavioral scenarios — memory compression over time.

RED tests — written before implementation.
"""

import pytest

from cascade_compression.cascade.agents import default_agents
from cascade_compression.cascade.memory import MemoryArchive
from cascade_compression.cascade.pipeline import CascadePipeline
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


def make_pipeline():
    """Default pipeline with all built-in agents."""
    return CascadePipeline(default_agents())


# ===========================================================================
# Stage 1: TDD — Consolidation Mechanics
# ===========================================================================

class TestConsolidationBasics:
    def test_consolidation_returns_stats(self):
        """consolidate() returns a dict with processed, evicted, compression_ratio."""
        archive = MemoryArchive()
        archive.store(make_signal(severity="critical"), classification="x")
        pipeline = make_pipeline
        result = archive.consolidate(pipeline)
        assert "processed" in result
        assert "evicted" in result
        assert "compression_ratio" in result

    def test_consolidation_processes_all_memories(self):
        """All memories in the archive are processed."""
        archive = MemoryArchive()
        for i in range(5):
            archive.store(
                make_signal(signal_type=f"type_{i}", severity="critical",
                            content={"message": f"incident {i}"}),
                classification="x",
            )
        pipeline = make_pipeline
        result = archive.consolidate(pipeline)
        assert result["processed"] == 5

    def test_empty_archive_consolidation(self):
        """Consolidating an empty archive is a no-op."""
        archive = MemoryArchive()
        pipeline = make_pipeline
        result = archive.consolidate(pipeline)
        assert result["processed"] == 0
        assert result["evicted"] == 0


class TestConsolidationStrengthChanges:
    def test_known_noise_loses_strength(self):
        """GIVEN a memory that the current pipeline suppresses (info heartbeat)
        WHEN consolidation runs
        THEN memory strength decreases."""
        archive = MemoryArchive()
        memory = archive.store(
            make_signal(signal_type="heartbeat", severity="info",
                        content={"message": "health check ok"}),
            classification="routine_noise",
        )
        original_strength = memory.strength
        pipeline = make_pipeline
        archive.consolidate(pipeline)
        assert memory.strength < original_strength

    def test_still_important_gains_consolidation_count(self):
        """GIVEN a memory that still survives the current pipeline (critical OOM)
        WHEN consolidation runs
        THEN consolidation_count increments."""
        archive = MemoryArchive()
        memory = archive.store(
            make_signal(signal_type="oom_killed", severity="critical",
                        content={"message": "OOMKilled: container web-app killed"}),
            classification="real_incident",
        )
        assert memory.consolidation_count == 0
        pipeline = make_pipeline
        archive.consolidate(pipeline)
        assert memory.consolidation_count == 1

    def test_survivor_gets_strength_boost(self):
        """Memories that survive consolidation get a small strength boost."""
        archive = MemoryArchive()
        memory = archive.store(
            make_signal(signal_type="security_breach", severity="critical",
                        content={"message": "unauthorized root access detected"}),
            classification="real_incident",
        )
        pipeline = make_pipeline
        # Critical memories start at 1.0 so we need one that starts lower
        memory_med = archive.store(
            make_signal(signal_type="auth_failure", severity="medium",
                        content={"message": "failed login attempt from 10.0.0.1"}),
            classification="needs_attention",
        )
        original = memory_med.strength
        archive.consolidate(pipeline)
        assert memory_med.consolidation_count >= 1
        assert memory_med.strength >= original


class TestConsolidationEviction:
    def test_weak_memory_evicted_after_decay(self):
        """GIVEN a memory weakened by consolidation decay
        WHEN its strength falls below eviction threshold
        THEN it is removed from the archive."""
        archive = MemoryArchive()
        memory = archive.store(
            make_signal(signal_type="heartbeat", severity="info",
                        content={"message": "ok"}),
            classification="routine_noise",
        )
        pipeline = make_pipeline
        # Run consolidation multiple times to decay the memory below threshold
        for _ in range(10):
            archive.consolidate(pipeline)
        # Info severity starts at 0.1, repeated suppression should evict it
        assert archive.get(memory.memory_id) is None

    def test_eviction_emits_events(self):
        """Evicted memories produce MemoryEvents."""
        archive = MemoryArchive()
        archive.store(
            make_signal(signal_type="heartbeat", severity="info",
                        content={"message": "ok"}),
            classification="routine_noise",
        )
        pipeline = make_pipeline
        archive.drain_events()
        for _ in range(10):
            archive.consolidate(pipeline)
        events = archive.drain_events()
        evictions = [e for e in events if e.event_type == "evicted"]
        assert len(evictions) > 0

    def test_strong_memories_never_evicted_by_consolidation(self):
        """Memories with high strength survive even repeated consolidation."""
        archive = MemoryArchive()
        memory = archive.store(
            make_signal(signal_type="data_corruption", severity="critical",
                        content={"message": "data corruption detected in table users"}),
            classification="real_incident",
        )
        pipeline = make_pipeline
        for _ in range(20):
            archive.consolidate(pipeline)
        assert archive.get(memory.memory_id) is not None
        assert memory.strength > 0.5


# ===========================================================================
# Stage 2: BDD — Behavioral Scenarios
# ===========================================================================

class TestConsolidationBehavior:
    def test_compression_ratio_measurable(self):
        """GIVEN an archive with a mix of noise and real signals
        WHEN consolidation runs
        THEN the compression ratio reflects how many were suppressed."""
        archive = MemoryArchive()
        # Noise signals the pipeline will suppress
        for i in range(5):
            archive.store(
                make_signal(signal_type="heartbeat", severity="info",
                            content={"message": f"health check {i}"}),
                classification="routine_noise",
            )
        # Real signals the pipeline will keep
        for i in range(5):
            archive.store(
                make_signal(signal_type=f"incident_{i}", severity="critical",
                            content={"message": f"critical incident {i}"}),
                classification="real_incident",
            )
        pipeline = make_pipeline
        result = archive.consolidate(pipeline)
        assert result["compression_ratio"] > 0.0

    def test_consolidation_shrinks_archive_over_time(self):
        """GIVEN an archive with mostly noise
        WHEN consolidation runs repeatedly
        THEN archive size decreases as weak memories are evicted."""
        archive = MemoryArchive()
        for i in range(20):
            archive.store(
                make_signal(signal_type="heartbeat", severity="info",
                            content={"message": f"check {i}"}),
                classification="routine_noise",
            )
        archive.store(
            make_signal(signal_type="real_outage", severity="critical",
                        content={"message": "service down"}),
            classification="real_incident",
        )
        initial_size = archive.size
        pipeline = make_pipeline
        for _ in range(15):
            archive.consolidate(pipeline)
        assert archive.size < initial_size

    def test_consolidation_preserves_institutional_knowledge(self):
        """GIVEN an archive with critical incidents
        WHEN consolidation runs repeatedly
        THEN those incidents remain as core memories."""
        archive = MemoryArchive()
        critical_ids = []
        for i in range(3):
            m = archive.store(
                make_signal(signal_type=f"outage_{i}", severity="critical",
                            content={"message": f"production outage #{i}"}),
                classification="real_incident",
            )
            critical_ids.append(m.memory_id)
        for i in range(10):
            archive.store(
                make_signal(signal_type="noise", severity="info",
                            content={"message": f"routine {i}"}),
                classification="routine_noise",
            )
        pipeline = make_pipeline
        for _ in range(20):
            archive.consolidate(pipeline)
        for mid in critical_ids:
            assert archive.get(mid) is not None, f"Critical memory {mid} was evicted"


class TestConsolidationBatching:
    def test_batch_processes_only_n_memories(self):
        """batch_size limits how many memories are processed."""
        archive = MemoryArchive()
        for i in range(20):
            archive.store(
                make_signal(signal_type=f"type_{i}", severity="critical",
                            content={"message": f"event {i}"}),
                classification="x",
            )
        result = archive.consolidate(make_pipeline, batch_size=5)
        assert result["processed"] == 5

    def test_batch_zero_processes_all(self):
        """batch_size=0 processes everything (backward compat)."""
        archive = MemoryArchive()
        for i in range(10):
            archive.store(
                make_signal(signal_type=f"type_{i}", severity="critical",
                            content={"message": f"event {i}"}),
                classification="x",
            )
        result = archive.consolidate(make_pipeline, batch_size=0)
        assert result["processed"] == 10

    def test_batch_prioritizes_oldest_unconsolidated(self):
        """Memories without last_consolidated_at are processed first."""
        archive = MemoryArchive()
        old = archive.store(
            make_signal(signal_type="old_event", severity="critical",
                        content={"message": "old"}),
            classification="x",
        )
        old.last_consolidated_at = None

        new = archive.store(
            make_signal(signal_type="new_event", severity="critical",
                        content={"message": "new"}),
            classification="x",
        )
        new.last_consolidated_at = "2099-01-01T00:00:00+00:00"

        result = archive.consolidate(make_pipeline, batch_size=1)
        assert result["processed"] == 1
        assert old.last_consolidated_at is not None

    def test_repeated_batches_cover_archive(self):
        """Multiple batch calls eventually process everything."""
        archive = MemoryArchive()
        for i in range(10):
            archive.store(
                make_signal(signal_type=f"type_{i}", severity="critical",
                            content={"message": f"event {i}"}),
                classification="x",
            )
        consolidated = set()
        for _ in range(5):
            archive.consolidate(make_pipeline, batch_size=3)
        for m in archive.query(limit=999):
            if m.last_consolidated_at:
                consolidated.add(m.memory_id)
        assert len(consolidated) == archive.size

    def test_last_consolidated_at_updated(self):
        """last_consolidated_at is set after processing."""
        archive = MemoryArchive()
        m = archive.store(
            make_signal(severity="critical", content={"message": "test"}),
            classification="x",
        )
        assert m.last_consolidated_at is None
        archive.consolidate(make_pipeline, batch_size=1)
        assert m.last_consolidated_at is not None


class TestConsolidationWithDecayConfig:
    def test_per_type_decay_differentiates_strength(self):
        """GIVEN two info-severity signal types with different decay rates
        WHEN both are suppressed during consolidation
        THEN the fast-decay type loses more strength."""
        from cascade_compression.cascade.memory_intelligence import DecayConfig
        config = DecayConfig(default_rate=0.01)
        config.set_rate("heartbeat", 0.1)
        config.set_rate("security_alert", 0.001)

        archive = MemoryArchive()
        archive.set_decay_config(config)
        m_fast = archive.store(
            make_signal(signal_type="heartbeat", severity="info",
                        content={"message": "health check ok fast"}),
            classification="x",
        )
        m_slow = archive.store(
            make_signal(signal_type="security_alert", severity="info",
                        content={"message": "low priority alert slow"}),
            classification="x",
        )
        m_fast.strength = 0.8
        m_slow.strength = 0.8

        archive.consolidate(make_pipeline, decay_config=config)
        assert m_fast.strength < m_slow.strength

    def test_consolidation_without_config_uses_flat_decay(self):
        """Without decay config, flat strength_decay is used (backward compat)."""
        archive = MemoryArchive()
        m = archive.store(
            make_signal(signal_type="heartbeat", severity="info",
                        content={"message": "check"}),
            classification="x",
        )
        original = m.strength
        archive.consolidate(make_pipeline)
        assert m.strength == original - 0.3 or m.strength == 0.0


class TestConsolidationAPI:
    def test_consolidate_endpoint(self):
        """POST /consolidate triggers consolidation and returns stats."""
        from fastapi.testclient import TestClient
        from cascade_compression.service import app

        client = TestClient(app)
        resp = client.post("/consolidate")
        assert resp.status_code == 200
        data = resp.json()
        assert "processed" in data
        assert "evicted" in data
        assert "compression_ratio" in data

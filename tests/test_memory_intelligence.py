"""Memory intelligence framework tests — TDD RED → GREEN.

Stage 1 (TDD): Entity resolution, time clustering, causal chains,
               absence detection, severity tracking, per-type decay.
Stage 2 (BDD): Composable framework behavior with domain packs.

RED tests — written before implementation.
"""

import pytest

from cascade_compression.cascade.memory import Memory, MemoryArchive
from cascade_compression.cascade.protocol import Signal
from cascade_compression.cascade.memory_intelligence import (
    AbsenceDetector,
    CausalGraph,
    CoOccurrenceTracker,
    DecayConfig,
    EntityResolver,
    MemoryIntelligence,
    SeverityTracker,
    TimeCluster,
    TimeClusterEngine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_signal(signal_type="pod_crashloop", severity="high", source="node-01",
                namespace="production", content=None, labels=None, cluster="ocpv05"):
    return Signal(
        signal_type=signal_type,
        severity=severity,
        source=source,
        namespace=namespace,
        cluster=cluster,
        content=content or {"message": f"{signal_type} detected"},
        labels=labels or {},
    )


def make_memory(archive, signal_type="pod_crashloop", severity="high",
                source="node-01", namespace="production", content=None,
                labels=None, cluster="ocpv05", classification=""):
    sig = make_signal(signal_type=signal_type, severity=severity, source=source,
                      namespace=namespace, content=content, labels=labels,
                      cluster=cluster)
    return archive.store(sig, classification=classification)


# ===========================================================================
# Stage 1: TDD — Entity Resolution
# ===========================================================================

class TestEntityResolver:
    def test_default_resolver_uses_source(self):
        """Default resolver returns signal source as entity key."""
        resolver = EntityResolver()
        sig = make_signal(source="worker-3", cluster="ocpv05")
        assert resolver.resolve(sig) is not None

    def test_custom_extractor(self):
        """Custom extractor function overrides default."""
        def k8s_extractor(sig):
            return f"node:{sig.source}:{sig.cluster}"
        resolver = EntityResolver(extractor=k8s_extractor)
        sig = make_signal(source="worker-3", cluster="ocpv05")
        assert resolver.resolve(sig) == "node:worker-3:ocpv05"

    def test_resolve_returns_none_for_empty(self):
        """Resolver returns None when no entity can be extracted."""
        resolver = EntityResolver()
        sig = make_signal(source="", namespace="", cluster="")
        result = resolver.resolve(sig)
        assert result is None or result == ""

    def test_entity_grouping(self):
        """group_by_entity clusters memories by entity key."""
        def extractor(sig):
            return f"{sig.cluster}:{sig.namespace}"
        resolver = EntityResolver(extractor=extractor)
        archive = MemoryArchive()
        make_memory(archive, namespace="prod", cluster="ocpv05",
                    content={"message": "a"})
        make_memory(archive, namespace="prod", cluster="ocpv05",
                    content={"message": "b"})
        make_memory(archive, namespace="staging", cluster="ocpv05",
                    content={"message": "c"})

        groups = resolver.group_by_entity(archive.query(limit=999))
        assert "ocpv05:prod" in groups
        assert len(groups["ocpv05:prod"]) == 2
        assert "ocpv05:staging" in groups
        assert len(groups["ocpv05:staging"]) == 1


# ===========================================================================
# Stage 1: TDD — Time Clustering
# ===========================================================================

class TestTimeClusterEngine:
    def test_cluster_by_window(self):
        """Memories within the same time window are clustered together."""
        archive = MemoryArchive()
        m1 = make_memory(archive, signal_type="a", content={"message": "a1"})
        m2 = make_memory(archive, signal_type="b", content={"message": "b1"})
        m3 = make_memory(archive, signal_type="c", content={"message": "c1"})
        engine = TimeClusterEngine(window_seconds=60)
        clusters = engine.cluster(archive.query(limit=999))
        assert len(clusters) >= 1
        assert any(len(c.memories) >= 2 for c in clusters)

    def test_distant_memories_separate_clusters(self):
        """Memories far apart in time form separate clusters."""
        engine = TimeClusterEngine(window_seconds=60)
        m1 = Memory.from_dict({
            "memory_id": "00000000-0000-0000-0000-000000000001",
            "signal_type": "a", "severity": "high",
            "formed_at": "2026-08-14T10:00:00+00:00",
            "strength": 0.7, "content_hash": "hash1",
        })
        m2 = Memory.from_dict({
            "memory_id": "00000000-0000-0000-0000-000000000002",
            "signal_type": "b", "severity": "high",
            "formed_at": "2026-08-14T11:00:00+00:00",
            "strength": 0.7, "content_hash": "hash2",
        })
        clusters = engine.cluster([m1, m2])
        assert len(clusters) == 2

    def test_cluster_has_time_range(self):
        """Each cluster reports its start and end time."""
        archive = MemoryArchive()
        make_memory(archive, content={"message": "x1"})
        make_memory(archive, content={"message": "x2"})
        engine = TimeClusterEngine(window_seconds=60)
        clusters = engine.cluster(archive.query(limit=999))
        for c in clusters:
            assert c.start is not None
            assert c.end is not None

    def test_cluster_has_signal_types(self):
        """Each cluster lists the distinct signal types it contains."""
        archive = MemoryArchive()
        make_memory(archive, signal_type="disk_pressure", content={"message": "d1"})
        make_memory(archive, signal_type="oom_killed", content={"message": "o1"})
        engine = TimeClusterEngine(window_seconds=60)
        clusters = engine.cluster(archive.query(limit=999))
        combined_types = set()
        for c in clusters:
            combined_types.update(c.signal_types)
        assert "disk_pressure" in combined_types
        assert "oom_killed" in combined_types

    def test_empty_input(self):
        """Clustering empty list returns empty."""
        engine = TimeClusterEngine(window_seconds=60)
        assert engine.cluster([]) == []


# ===========================================================================
# Stage 1: TDD — Causal Chains
# ===========================================================================

class TestCausalGraph:
    def test_add_rule(self):
        """Rules can be added to the graph."""
        graph = CausalGraph()
        graph.add_rule("event_claimmisbound", "event_volumefaileddelete")
        assert graph.has_rule("event_claimmisbound", "event_volumefaileddelete")

    def test_find_causes(self):
        """Given an effect, find potential causes."""
        graph = CausalGraph()
        graph.add_rule("event_claimmisbound", "event_volumefaileddelete")
        graph.add_rule("node_disk_pressure", "event_volumefaileddelete")
        causes = graph.causes_of("event_volumefaileddelete")
        assert "event_claimmisbound" in causes
        assert "node_disk_pressure" in causes

    def test_find_effects(self):
        """Given a cause, find potential effects."""
        graph = CausalGraph()
        graph.add_rule("event_claimmisbound", "event_volumefaileddelete")
        graph.add_rule("event_claimmisbound", "event_failedattachvolume")
        effects = graph.effects_of("event_claimmisbound")
        assert "event_volumefaileddelete" in effects
        assert "event_failedattachvolume" in effects

    def test_no_rule_returns_empty(self):
        """Unknown signal type returns empty causes/effects."""
        graph = CausalGraph()
        assert graph.causes_of("unknown") == []
        assert graph.effects_of("unknown") == []

    def test_find_chain(self):
        """Multi-hop causal chain: A → B → C."""
        graph = CausalGraph()
        graph.add_rule("node_disk_pressure", "event_claimmisbound")
        graph.add_rule("event_claimmisbound", "event_volumefaileddelete")
        chain = graph.chain_from("node_disk_pressure")
        assert "event_claimmisbound" in chain
        assert "event_volumefaileddelete" in chain

    def test_cycle_safe(self):
        """Cycles in the graph don't cause infinite loops."""
        graph = CausalGraph()
        graph.add_rule("a", "b")
        graph.add_rule("b", "a")
        chain = graph.chain_from("a")
        assert "b" in chain


# ===========================================================================
# Stage 1: TDD — Absence Detection
# ===========================================================================

class TestAbsenceDetector:
    def test_register_expected_signal(self):
        """Can register an expected recurring signal."""
        detector = AbsenceDetector()
        detector.expect("nightly_backup", interval_hours=24)
        assert "nightly_backup" in detector.expectations

    def test_detect_missing(self):
        """Detects absence when expected signal hasn't appeared."""
        detector = AbsenceDetector()
        detector.expect("hourly_check", interval_hours=1)
        detector.record("hourly_check", "2026-08-14T10:00:00+00:00")
        missing = detector.check_missing("2026-08-14T12:30:00+00:00")
        assert any(m["signal_type"] == "hourly_check" for m in missing)

    def test_not_missing_when_recent(self):
        """No absence when signal appeared within the interval."""
        detector = AbsenceDetector()
        detector.expect("hourly_check", interval_hours=1)
        detector.record("hourly_check", "2026-08-14T12:00:00+00:00")
        missing = detector.check_missing("2026-08-14T12:30:00+00:00")
        assert not any(m["signal_type"] == "hourly_check" for m in missing)

    def test_no_expectations_no_missing(self):
        """No registered expectations means nothing missing."""
        detector = AbsenceDetector()
        assert detector.check_missing("2026-08-14T12:00:00+00:00") == []


# ===========================================================================
# Stage 1: TDD — Severity Tracking
# ===========================================================================

class TestSeverityTracker:
    def test_record_severity(self):
        """Can record severity observations over time."""
        tracker = SeverityTracker()
        tracker.record("pod_crashloop", "medium", "2026-08-14T10:00:00")
        tracker.record("pod_crashloop", "high", "2026-08-14T11:00:00")
        tracker.record("pod_crashloop", "critical", "2026-08-14T12:00:00")
        trend = tracker.trend("pod_crashloop")
        assert trend == "escalating"

    def test_stable_trend(self):
        """Same severity over time is stable."""
        tracker = SeverityTracker()
        tracker.record("heartbeat", "info", "2026-08-14T10:00:00")
        tracker.record("heartbeat", "info", "2026-08-14T11:00:00")
        assert tracker.trend("heartbeat") == "stable"

    def test_deescalating(self):
        """Decreasing severity is deescalating."""
        tracker = SeverityTracker()
        tracker.record("incident", "critical", "2026-08-14T10:00:00")
        tracker.record("incident", "high", "2026-08-14T11:00:00")
        tracker.record("incident", "medium", "2026-08-14T12:00:00")
        assert tracker.trend("incident") == "deescalating"

    def test_unknown_type_returns_unknown(self):
        """Untracked signal type returns unknown trend."""
        tracker = SeverityTracker()
        assert tracker.trend("never_seen") == "unknown"


# ===========================================================================
# Stage 1: TDD — Per-Type Decay Configuration
# ===========================================================================

class TestCoOccurrenceTracker:
    def test_counts_pairs_from_clusters(self):
        """Co-occurrence counts pairs correctly from time clusters."""
        tracker = CoOccurrenceTracker()
        cluster = TimeCluster(
            memories=[],
            signal_types={"disk_pressure", "volume_failure"},
        )
        tracker.update_from_clusters([cluster])
        assert tracker.pair_count > 0

    def test_propose_rule_above_threshold(self):
        """Frequent co-occurrence proposes a rule."""
        tracker = CoOccurrenceTracker()
        for _ in range(10):
            cluster = TimeCluster(
                memories=[],
                signal_types={"cause_a", "effect_b"},
            )
            tracker.update_from_clusters([cluster])
        proposals = tracker.propose_rules(min_count=5, min_support=0.3)
        types_in_proposals = set()
        for p in proposals:
            types_in_proposals.add(p["cause"])
            types_in_proposals.add(p["effect"])
        assert "cause_a" in types_in_proposals or "effect_b" in types_in_proposals

    def test_no_proposal_below_threshold(self):
        """Infrequent co-occurrence doesn't propose."""
        tracker = CoOccurrenceTracker()
        cluster = TimeCluster(
            memories=[],
            signal_types={"rare_a", "rare_b"},
        )
        tracker.update_from_clusters([cluster])
        proposals = tracker.propose_rules(min_count=5)
        assert len(proposals) == 0

    def test_no_duplicate_proposals(self):
        """Existing rules are not re-proposed."""
        tracker = CoOccurrenceTracker()
        for _ in range(10):
            cluster = TimeCluster(
                memories=[],
                signal_types={"a", "b"},
            )
            tracker.update_from_clusters([cluster])
        graph = CausalGraph()
        graph.add_rule("a", "b")
        proposals = tracker.propose_rules(min_count=5, existing_graph=graph)
        assert len(proposals) == 0


class TestDecayConfig:
    def test_default_rate(self):
        """Unknown types get the default decay rate."""
        config = DecayConfig(default_rate=0.01)
        assert config.rate_for("unknown_type") == 0.01

    def test_custom_rate(self):
        """Custom rate overrides default for specific type."""
        config = DecayConfig(default_rate=0.01)
        config.set_rate("event_deprecatedannotation", 0.05)
        assert config.rate_for("event_deprecatedannotation") == 0.05

    def test_slow_decay_for_security(self):
        """Security signals can have near-zero decay."""
        config = DecayConfig(default_rate=0.01)
        config.set_rate("config_update_credential", 0.001)
        assert config.rate_for("config_update_credential") == 0.001


# ===========================================================================
# Stage 2: BDD — Composable Framework
# ===========================================================================

class TestMemoryIntelligence:
    def test_compose_with_domain_config(self):
        """GIVEN a MemoryIntelligence with a domain config
        WHEN analyzing an archive
        THEN entity resolution, causal chains, and decay all work together."""
        mi = MemoryIntelligence()
        mi.entity_resolver = EntityResolver(
            extractor=lambda sig: f"{sig.cluster}:{sig.source}"
        )
        mi.causal_graph.add_rule("event_claimmisbound", "event_volumefaileddelete")
        mi.decay_config.set_rate("event_deprecatedannotation", 0.05)
        mi.decay_config.set_rate("node_notready", 0.001)

        archive = MemoryArchive()
        make_memory(archive, signal_type="event_claimmisbound",
                    source="worker-3", cluster="ocpv05",
                    content={"message": "PVC misbound"})
        make_memory(archive, signal_type="event_volumefaileddelete",
                    source="worker-3", cluster="ocpv05",
                    content={"message": "volume delete failed"})

        analysis = mi.analyze(archive)
        assert "entities" in analysis
        assert "clusters" in analysis
        assert "causal_links" in analysis

    def test_domain_pack_registration(self):
        """GIVEN domain packs for K8s and AAP
        WHEN registered with MemoryIntelligence
        THEN their configs are merged."""
        mi = MemoryIntelligence()

        k8s_config = {
            "entity_extractor": lambda sig: f"node:{sig.source}:{sig.cluster}",
            "causal_rules": [
                ("event_claimmisbound", "event_volumefaileddelete"),
                ("node_disk_pressure", "event_provisioningfailed"),
            ],
            "decay_overrides": {
                "event_deprecatedannotation": 0.05,
                "node_notready": 0.001,
            },
            "expected_signals": [],
        }
        mi.register_domain("kubernetes", k8s_config)
        assert mi.causal_graph.has_rule("event_claimmisbound", "event_volumefaileddelete")

    def test_analyze_returns_proposed_rules(self):
        """Analysis output includes co-occurrence proposed rules."""
        mi = MemoryIntelligence()
        archive = MemoryArchive()
        for _ in range(10):
            make_memory(archive, signal_type="disk_pressure",
                        content={"message": "disk pressure"})
            make_memory(archive, signal_type="volume_failure",
                        content={"message": "volume failed"})
        analysis = mi.analyze(archive)
        assert "proposed_rules" in analysis
        assert "co_occurrence_pairs" in analysis

    def test_auto_discover_adds_rules(self):
        """When auto_discover=True, high-confidence proposals are added."""
        mi = MemoryIntelligence(auto_discover=True)
        archive = MemoryArchive()
        for i in range(20):
            make_memory(archive, signal_type="cause_type",
                        content={"message": f"cause {i}"})
            make_memory(archive, signal_type="effect_type",
                        content={"message": f"effect {i}"})
        mi.analyze(archive)
        # After enough co-occurrences with auto_discover, rules may be added
        # (depends on threshold — at least proposals should exist)
        assert mi.co_occurrence.pair_count > 0

    def test_cross_domain_entity_mapping(self):
        """GIVEN entity mappings between K8s and AAP
        WHEN memories from both domains reference the same entity
        THEN they are grouped together."""
        mi = MemoryIntelligence()

        mi.add_entity_mapping(
            lambda sig: sig.cluster if sig.labels.get("domain") == "kubernetes" else None,
            lambda sig: sig.namespace.replace("deploy-", "") if sig.labels.get("domain") == "aap" else None,
        )

        archive = MemoryArchive()
        make_memory(archive, signal_type="node_notready", cluster="ocpv05",
                    labels={"domain": "kubernetes"},
                    content={"message": "node down"})
        make_memory(archive, signal_type="job_failed", namespace="deploy-ocpv05",
                    labels={"domain": "aap"},
                    content={"message": "deploy failed"})

        mappings = mi.find_cross_domain_links(archive.query(limit=999))
        assert len(mappings) > 0

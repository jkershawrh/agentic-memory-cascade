"""Biography endpoint tests — structured platform autobiography.

Tests generate_biography() and _compute_health_score() with various
memory states: empty, populated, with causal rules, GPU analyses, etc.
"""

import pytest

from cascade_compression.cascade.memory import MemoryArchive
from cascade_compression.cascade.memory_intelligence import MemoryIntelligence
from cascade_compression.cascade.protocol import CascadeDecision, Outcome, Signal
from cascade_compression.cascade.inverse import SuppressionArchive
from cascade_compression.service import generate_biography, _compute_health_score


def make_signal(signal_type="heartbeat", severity="info", content=None):
    return Signal(
        signal_type=signal_type,
        severity=severity,
        content=content or {"message": f"{signal_type} signal"},
    )


def make_decision(signal_id, agent="severity_gate", outcome=Outcome.DROP):
    return CascadeDecision(
        signal_id=signal_id,
        agent_name=agent,
        outcome=outcome,
        confidence=0.9,
        evidence="test suppression",
    )


class TestGenerateBiography:
    """Tests for generate_biography() — the core data aggregation."""

    def test_empty_state_returns_valid_structure(self):
        """Biography with no bridge/memory/intel returns complete skeleton."""
        result = generate_biography(None, None, None)
        bio = result["biography"]
        assert "timeline" in bio
        assert "top_memories" in bio
        assert "patterns_learned" in bio
        assert "noise_profile" in bio
        assert "causal_chains" in bio
        assert "causal_gaps" in bio
        assert "absences" in bio
        assert "gpu_analyses" in bio
        assert "health" in bio

    def test_empty_state_has_generated_at(self):
        """Even with no data, timeline has a generation timestamp."""
        result = generate_biography(None, None, None)
        assert "generated_at" in result["biography"]["timeline"]

    def test_empty_state_health_score(self):
        """Empty state gets a health score (penalized for no memories)."""
        result = generate_biography(None, None, None)
        health = result["biography"]["health"]
        assert "score" in health
        assert "grade" in health
        assert "factors" in health
        assert health["factors"]["memory_count"] == 0

    def test_with_memory_archive(self):
        """Biography includes top memories from archive."""
        archive = MemoryArchive()
        archive.store(
            make_signal("pod_crash", "critical",
                        {"message": "CrashLoopBackOff on api-server"}),
            classification="real_incident",
        )
        archive.store(
            make_signal("node_ready", "info",
                        {"message": "Node ready"}),
            classification="routine",
        )

        result = generate_biography(None, archive, None)
        bio = result["biography"]
        assert len(bio["top_memories"]) == 2
        # Strongest memory should be first (critical > info)
        assert bio["top_memories"][0]["severity"] == "critical"
        assert bio["top_memories"][0]["signal_type"] == "pod_crash"
        assert "CrashLoopBackOff" in bio["top_memories"][0]["message"]

    def test_with_bridge(self):
        """Biography includes bridge timeline and patterns."""
        from cascade_compression.bridge import CascadeBridge
        bridge = CascadeBridge()

        # Simulate activated agents
        bridge._activated_types = {"event_normal", "heartbeat"}
        bridge._activated_patterns = {
            "event_normal": "dominant_type",
            "heartbeat": "repeat_flood",
        }
        bridge._llm_noise_counts["event_normal"] = 500
        bridge._llm_noise_counts["heartbeat"] = 300
        bridge._llm_important_counts["event_normal"] = 0
        bridge._activation_timestamps = {
            "event_normal": "2026-08-10T00:00:00+00:00",
            "heartbeat": "2026-08-11T00:00:00+00:00",
        }

        result = generate_biography(bridge, bridge.memory_archive, None)
        bio = result["biography"]

        assert bio["timeline"]["started_at"] == bridge.stats.started_at
        assert bio["timeline"]["domain"] == bridge.domain

        assert len(bio["patterns_learned"]) == 2
        types = {p["signal_type"] for p in bio["patterns_learned"]}
        assert types == {"event_normal", "heartbeat"}

        hb = next(p for p in bio["patterns_learned"]
                  if p["signal_type"] == "heartbeat")
        assert hb["pattern_type"] == "repeat_flood"
        assert hb["action"] == "repeat flood suppression"
        assert hb["noise_count"] == 300

    def test_noise_profile_from_suppression_archive(self):
        """Biography builds noise profile from suppression archive."""
        from cascade_compression.bridge import CascadeBridge
        bridge = CascadeBridge()

        # Record suppressions
        for _ in range(30):
            sig = make_signal("heartbeat")
            bridge.suppression_archive.record(make_decision(sig.signal_id), sig)
        for _ in range(10):
            sig = make_signal("probe_success")
            bridge.suppression_archive.record(make_decision(sig.signal_id), sig)

        result = generate_biography(bridge, bridge.memory_archive, None)
        noise = result["biography"]["noise_profile"]

        assert noise["baseline_types"] == 2
        assert noise["total_suppression_decisions"] == 40
        assert len(noise["top_noise"]) == 2
        assert "interpretation" in noise

    def test_causal_chains_from_intelligence(self):
        """Biography includes causal chains from memory intelligence."""
        intel = MemoryIntelligence()
        intel.causal_graph.add_rule("node_disk_pressure", "pod_eviction")
        intel.causal_graph.add_rule("pod_eviction", "service_degradation")

        result = generate_biography(None, None, intel)
        chains = result["biography"]["causal_chains"]

        assert len(chains) == 2
        causes = {c["cause"] for c in chains}
        assert "node_disk_pressure" in causes
        assert "pod_eviction" in causes

    def test_causal_gaps_detected(self):
        """Biography reports causal gaps (expected causes not in memory)."""
        archive = MemoryArchive()
        archive.store(
            make_signal("pod_eviction", "high",
                        {"message": "pod evicted"}),
            classification="incident",
        )

        intel = MemoryIntelligence()
        intel.causal_graph.add_rule("node_disk_pressure", "pod_eviction")

        result = generate_biography(None, archive, intel)
        gaps = result["biography"]["causal_gaps"]

        assert len(gaps) == 1
        assert gaps[0]["expected_cause"] == "node_disk_pressure"
        assert gaps[0]["effect"] == "pod_eviction"

    def test_no_gaps_when_causes_present(self):
        """No gaps when all causal causes are in memory."""
        archive = MemoryArchive()
        archive.store(
            make_signal("node_disk_pressure", "high",
                        {"message": "disk pressure detected"}),
            classification="cause",
        )
        archive.store(
            make_signal("pod_eviction", "high",
                        {"message": "pod evicted"}),
            classification="effect",
        )

        intel = MemoryIntelligence()
        intel.causal_graph.add_rule("node_disk_pressure", "pod_eviction")

        result = generate_biography(None, archive, intel)
        assert len(result["biography"]["causal_gaps"]) == 0

    def test_absences_from_detector(self):
        """Biography includes absence detection results."""
        intel = MemoryIntelligence()
        intel.absence_detector.expect("heartbeat", 0.1)
        # Don't record any heartbeats — they should show as absent

        result = generate_biography(None, None, intel)
        absences = result["biography"]["absences"]
        assert len(absences) == 1
        assert absences[0]["signal_type"] == "heartbeat"

    def test_gpu_analyses_summary(self):
        """Biography summarizes GPU analyses when present."""
        from cascade_compression.bridge import CascadeBridge
        bridge = CascadeBridge()
        bridge._gpu_analyses = [
            {
                "signal_type": "pod_crash",
                "severity": "critical",
                "root_cause": "Memory limit exceeded on api-server container",
                "confidence": 0.92,
                "model": "phi-4",
                "timestamp": "2026-08-15T10:00:00Z",
            },
            {
                "signal_type": "pod_crash",
                "severity": "high",
                "root_cause": "OOM killed by cgroup limit",
                "confidence": 0.85,
                "model": "phi-4",
                "timestamp": "2026-08-15T11:00:00Z",
            },
            {
                "signal_type": "node_pressure",
                "severity": "high",
                "root_cause": "Disk I/O saturation",
                "confidence": 0.78,
                "model": "phi-4",
                "timestamp": "2026-08-15T12:00:00Z",
            },
        ]

        result = generate_biography(bridge, bridge.memory_archive, None)
        gpu = result["biography"]["gpu_analyses"]

        assert gpu["count"] == 3
        assert "pod_crash" in gpu["by_signal_type"]
        assert gpu["by_signal_type"]["pod_crash"]["count"] == 2
        assert len(gpu["recent"]) == 3

    def test_promotion_log_milestones(self):
        """Biography extracts milestones from promotion log."""
        from cascade_compression.bridge import CascadeBridge
        bridge = CascadeBridge()
        bridge._promotion_log = [
            {"timestamp": "2026-08-10T00:00:00Z", "event": "discovered",
             "agent": "agent_heartbeat", "tier": "draft"},
            {"timestamp": "2026-08-10T01:00:00Z", "event": "promoted",
             "agent": "agent_heartbeat", "tier": "candidate"},
            {"timestamp": "2026-08-10T02:00:00Z", "event": "activated",
             "agent": "agent_heartbeat", "tier": "nano"},
            {"timestamp": "2026-08-11T00:00:00Z", "event": "activated",
             "agent": "agent_probe", "tier": "nano"},
        ]

        result = generate_biography(bridge, bridge.memory_archive, None)
        timeline = result["biography"]["timeline"]

        assert timeline["total_activations"] == 2
        assert timeline["first_activation"]["agent"] == "agent_heartbeat"
        assert timeline["latest_activation"]["agent"] == "agent_probe"
        assert timeline["milestone_count"] == 4

    def test_memory_with_analysis_includes_root_cause(self):
        """Top memories with GPU analysis include root_cause."""
        archive = MemoryArchive()
        sig = make_signal("pod_crash", "critical",
                          {"message": "CrashLoopBackOff"})
        m = archive.store(sig, classification="incident",
                          metadata={"analysis": {
                              "root_cause": "Memory limit exceeded",
                              "confidence": 0.9,
                          }})

        result = generate_biography(None, archive, None)
        top = result["biography"]["top_memories"][0]
        assert top["has_analysis"] is True
        assert "Memory limit exceeded" in top["root_cause"]


class TestComputeHealthScore:
    """Tests for the health scoring algorithm."""

    def test_perfect_health(self):
        """No gaps, no absences, strong memories = excellent score."""
        archive = MemoryArchive()
        archive.store(
            make_signal("x", "critical", {"message": "x"}),
            classification="x",
        )

        health = _compute_health_score(
            memory_archive=archive,
            causal_gaps=[],
            causal_chains=[{"cause": "a", "effect": "b"}],
            absences=[],
        )
        assert health["score"] >= 75
        assert health["grade"] in ("excellent", "good")

    def test_gaps_reduce_score(self):
        """Causal gaps reduce health score."""
        archive = MemoryArchive()
        archive.store(
            make_signal("x", "high", {"message": "x"}),
            classification="x",
        )

        chains = [{"cause": "a", "effect": "b"},
                  {"cause": "c", "effect": "d"}]
        gaps = [{"expected_cause": "a", "effect": "b"},
                {"expected_cause": "c", "effect": "d"}]

        health = _compute_health_score(
            memory_archive=archive,
            causal_gaps=gaps,
            causal_chains=chains,
            absences=[],
        )
        # All rules have gaps — score should be reduced
        assert health["factors"]["gap_ratio"] == 1.0
        assert health["score"] < 100

    def test_absences_reduce_score(self):
        """Missing expected signals reduce health score."""
        health = _compute_health_score(
            memory_archive=None,
            causal_gaps=[],
            causal_chains=[],
            absences=[
                {"signal_type": "hb", "hours_overdue": 2.0},
                {"signal_type": "probe", "hours_overdue": 1.0},
            ],
        )
        assert health["factors"]["absence_count"] == 2
        assert health["score"] < 100

    def test_no_memory_penalty(self):
        """Empty memory archive gets a score penalty."""
        health = _compute_health_score(
            memory_archive=None,
            causal_gaps=[],
            causal_chains=[],
            absences=[],
        )
        assert health["factors"]["memory_count"] == 0
        assert health["score"] == 90.0  # 100 - 10 for no memories

    def test_grade_mapping(self):
        """Grades map correctly to score ranges."""
        # Excellent: >= 90
        h = _compute_health_score(None, [], [], [])
        assert h["grade"] == "excellent" or h["score"] >= 90

        # Poor: many problems
        h = _compute_health_score(
            None,
            causal_gaps=[{"x": i} for i in range(20)],
            causal_chains=[{"x": i} for i in range(5)],
            absences=[{"x": i} for i in range(10)],
        )
        assert h["grade"] in ("poor", "fair")

    def test_strength_distribution_affects_score(self):
        """Weak memories reduce the health score."""
        archive = MemoryArchive()
        # Store many info-level (strength=0.1) memories
        for i in range(10):
            archive.store(
                make_signal(f"type_{i}", "info",
                            {"message": f"info signal {i}"}),
                classification="routine",
            )
        health = _compute_health_score(
            memory_archive=archive,
            causal_gaps=[],
            causal_chains=[],
            absences=[],
        )
        # Info signals start at 0.1 strength — below 0.3 threshold
        assert health["factors"]["avg_memory_strength"] < 0.3
        assert health["score"] < 100

    def test_score_bounds(self):
        """Score is always between 0 and 100."""
        # Worst case scenario
        health = _compute_health_score(
            memory_archive=None,
            causal_gaps=[{"x": i} for i in range(100)],
            causal_chains=[{"x": i} for i in range(10)],
            absences=[{"x": i} for i in range(50)],
        )
        assert 0 <= health["score"] <= 100

        # Best case
        archive = MemoryArchive()
        archive.store(
            make_signal("x", "critical", {"message": "x"}),
            classification="x",
        )
        health = _compute_health_score(archive, [], [], [])
        assert 0 <= health["score"] <= 100

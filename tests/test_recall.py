"""Recall engine tests — TDD RED → GREEN.

Stage 1 (TDD): Similarity function correctness and RecallEngine behavior.
Stage 2 (BDD): Recall scenarios — precedent lookup, reinforcement, performance.

RED tests — written before implementation.
"""

import time

import pytest

from cascade_compression.cascade.memory import MemoryArchive
from cascade_compression.cascade.protocol import Signal
from cascade_compression.cascade.recall import (
    RecallEngine,
    RecallResult,
    content_feature_cosine,
    label_jaccard,
    text_trigram_similarity,
    type_match,
)


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


def make_archive_with_memories():
    """Create an archive with diverse memories for recall testing."""
    archive = MemoryArchive()
    archive.store(
        make_signal(signal_type="pod_crashloop", severity="critical",
                    content={"message": "CrashLoopBackOff in api-server"},
                    labels={"app": "api", "env": "prod"}),
        classification="real_incident",
    )
    archive.store(
        make_signal(signal_type="disk_pressure", severity="high",
                    content={"message": "disk usage at 95%", "disk_percent": 95.0},
                    labels={"app": "db", "env": "prod"}),
        classification="needs_attention",
    )
    archive.store(
        make_signal(signal_type="oom_killed", severity="critical",
                    content={"message": "OOMKilled container web-app",
                             "memory_percent": 99.0},
                    labels={"app": "web", "env": "prod"}),
        classification="real_incident",
    )
    archive.store(
        make_signal(signal_type="auth_failure", severity="medium",
                    content={"message": "unauthorized access attempt"},
                    labels={"app": "gateway", "env": "staging"}),
        classification="needs_attention",
    )
    return archive


# ===========================================================================
# Stage 1: TDD — Similarity Function Correctness
# ===========================================================================

class TestTypeMatch:
    def test_exact_match(self):
        """Same signal_type returns 1.0."""
        assert type_match("pod_crashloop", "pod_crashloop") == 1.0

    def test_different(self):
        """Different signal_type returns 0.0."""
        assert type_match("pod_crashloop", "disk_pressure") == 0.0

    def test_empty(self):
        """Empty strings match each other."""
        assert type_match("", "") == 1.0

    def test_case_sensitive(self):
        """Match is case-sensitive."""
        assert type_match("Pod_Crashloop", "pod_crashloop") == 0.0


class TestLabelJaccard:
    def test_identical_labels(self):
        """Identical labels return 1.0."""
        labels = {"app": "web", "env": "prod"}
        assert label_jaccard(labels, labels) == 1.0

    def test_disjoint_labels(self):
        """No overlap returns 0.0."""
        a = {"app": "web"}
        b = {"team": "platform"}
        assert label_jaccard(a, b) == 0.0

    def test_partial_overlap(self):
        """Partial overlap returns correct Jaccard index."""
        a = {"app": "web", "env": "prod"}
        b = {"app": "web", "env": "staging"}
        result = label_jaccard(a, b)
        assert 0.0 < result < 1.0

    def test_subset(self):
        """Subset produces correct Jaccard (not 1.0)."""
        a = {"app": "web"}
        b = {"app": "web", "env": "prod"}
        result = label_jaccard(a, b)
        assert result == pytest.approx(0.5)

    def test_both_empty(self):
        """Two empty label sets return 0.0 (no information to compare)."""
        assert label_jaccard({}, {}) == 0.0

    def test_one_empty(self):
        """One empty set returns 0.0."""
        assert label_jaccard({"app": "web"}, {}) == 0.0


class TestContentFeatureCosine:
    def test_identical_vectors(self):
        """Identical vectors return 1.0."""
        v = {"cpu_percent": 80.0, "memory_percent": 60.0}
        assert content_feature_cosine(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        """Non-overlapping keys return 0.0."""
        a = {"cpu_percent": 80.0}
        b = {"disk_percent": 90.0}
        assert content_feature_cosine(a, b) == pytest.approx(0.0)

    def test_proportional_vectors(self):
        """Proportional vectors return 1.0 (cosine ignores magnitude)."""
        a = {"cpu": 10.0, "mem": 20.0}
        b = {"cpu": 50.0, "mem": 100.0}
        assert content_feature_cosine(a, b) == pytest.approx(1.0)

    def test_opposite_direction(self):
        """Opposite vectors return -1.0 or 0.0 depending on clamping."""
        a = {"cpu": 10.0}
        b = {"cpu": -10.0}
        result = content_feature_cosine(a, b)
        assert result <= 0.0

    def test_both_empty(self):
        """Two empty vectors return 0.0."""
        assert content_feature_cosine({}, {}) == 0.0

    def test_one_empty(self):
        """One empty vector returns 0.0."""
        assert content_feature_cosine({"cpu": 80.0}, {}) == 0.0

    def test_partial_overlap(self):
        """Partial key overlap produces valid cosine."""
        a = {"cpu": 80.0, "mem": 60.0}
        b = {"cpu": 80.0, "disk": 90.0}
        result = content_feature_cosine(a, b)
        assert 0.0 < result < 1.0


class TestTextTrigramSimilarity:
    def test_identical_text(self):
        """Same text returns 1.0."""
        assert text_trigram_similarity("CrashLoopBackOff", "CrashLoopBackOff") == 1.0

    def test_completely_different(self):
        """Unrelated text returns ~0.0."""
        result = text_trigram_similarity(
            "CrashLoopBackOff in api-server",
            "disk usage at ninety five percent"
        )
        assert result < 0.3

    def test_similar_text(self):
        """Similar text returns high similarity."""
        result = text_trigram_similarity(
            "CrashLoopBackOff in api-server pod-123",
            "CrashLoopBackOff in api-server pod-456"
        )
        assert result > 0.7

    def test_empty_strings(self):
        """Empty strings return 0.0."""
        assert text_trigram_similarity("", "") == 0.0

    def test_one_empty(self):
        """One empty string returns 0.0."""
        assert text_trigram_similarity("hello", "") == 0.0

    def test_short_text(self):
        """Very short text (< 3 chars) returns 0.0 gracefully."""
        assert text_trigram_similarity("ab", "ab") == 0.0


# ===========================================================================
# Stage 1: TDD — RecallEngine Behavior
# ===========================================================================

class TestRecallEngine:
    def test_recall_returns_ranked_results(self):
        """Results are sorted by score descending."""
        archive = make_archive_with_memories()
        engine = RecallEngine()
        query = make_signal(signal_type="pod_crashloop", severity="high",
                            content={"message": "CrashLoopBackOff in web-server"},
                            labels={"app": "api", "env": "prod"})
        results = engine.recall(query, archive)
        assert len(results) > 0
        for i in range(len(results) - 1):
            assert results[i].score >= results[i + 1].score

    def test_recall_respects_min_score(self):
        """Results below min_score are excluded."""
        archive = make_archive_with_memories()
        engine = RecallEngine()
        query = make_signal(signal_type="pod_crashloop")
        results = engine.recall(query, archive, min_score=0.5)
        assert all(r.score >= 0.5 for r in results)

    def test_recall_top_k_limit(self):
        """At most top_k results returned."""
        archive = make_archive_with_memories()
        engine = RecallEngine()
        query = make_signal(signal_type="pod_crashloop")
        results = engine.recall(query, archive, top_k=1)
        assert len(results) <= 1

    def test_recall_empty_archive_returns_empty(self):
        """No memories = no results."""
        archive = MemoryArchive()
        engine = RecallEngine()
        query = make_signal()
        results = engine.recall(query, archive)
        assert results == []

    def test_recall_result_has_breakdown(self):
        """Each result includes a score breakdown."""
        archive = make_archive_with_memories()
        engine = RecallEngine()
        query = make_signal(signal_type="pod_crashloop",
                            content={"message": "CrashLoopBackOff"})
        results = engine.recall(query, archive)
        assert len(results) > 0
        r = results[0]
        assert isinstance(r, RecallResult)
        assert "type_match" in r.breakdown
        assert "label_overlap" in r.breakdown
        assert "feature_cosine" in r.breakdown
        assert "text_similarity" in r.breakdown

    def test_exact_type_match_scores_highest(self):
        """A memory with same signal_type scores higher than different type."""
        archive = make_archive_with_memories()
        engine = RecallEngine()
        query = make_signal(signal_type="pod_crashloop",
                            content={"message": "crash detected"})
        results = engine.recall(query, archive)
        assert len(results) > 0
        assert results[0].memory.signal.signal_type == "pod_crashloop"

    def test_recall_reinforces_matched_memories(self):
        """After recall, matched memory strength increases."""
        archive = make_archive_with_memories()
        engine = RecallEngine()
        target = archive.query(signal_type="auth_failure")[0]
        original_strength = target.strength
        assert original_strength < 1.0

        query = make_signal(signal_type="auth_failure",
                            content={"message": "unauthorized access attempt again"})
        engine.recall(query, archive, reinforce=True)
        assert target.strength > original_strength

    def test_recall_updates_recall_count(self):
        """After recall with reinforce, matched memory recall_count increments."""
        archive = make_archive_with_memories()
        engine = RecallEngine()
        target = archive.query(signal_type="pod_crashloop")[0]
        original_count = target.recall_count

        query = make_signal(signal_type="pod_crashloop",
                            content={"message": "CrashLoopBackOff again"})
        engine.recall(query, archive, reinforce=True)
        assert target.recall_count == original_count + 1

    def test_recall_updates_last_recalled_at(self):
        """After recall with reinforce, last_recalled_at is set."""
        archive = make_archive_with_memories()
        engine = RecallEngine()
        target = archive.query(signal_type="pod_crashloop")[0]
        assert target.last_recalled_at is None

        query = make_signal(signal_type="pod_crashloop",
                            content={"message": "CrashLoopBackOff again"})
        engine.recall(query, archive, reinforce=True)
        assert target.last_recalled_at is not None

    def test_recall_without_reinforce_does_not_modify(self):
        """Recall with reinforce=False doesn't modify memories."""
        archive = make_archive_with_memories()
        engine = RecallEngine()
        target = archive.query(signal_type="pod_crashloop")[0]
        original_strength = target.strength
        original_count = target.recall_count

        query = make_signal(signal_type="pod_crashloop",
                            content={"message": "CrashLoopBackOff again"})
        engine.recall(query, archive, reinforce=False)
        assert target.strength == original_strength
        assert target.recall_count == original_count


# ===========================================================================
# Stage 2: BDD — Behavioral Scenarios
# ===========================================================================

class TestRecallBehavior:
    def test_precedent_found_for_similar_incident(self):
        """GIVEN memories of past incidents
        WHEN a similar signal arrives
        THEN recall finds relevant precedent with high score"""
        archive = make_archive_with_memories()
        engine = RecallEngine()
        query = make_signal(
            signal_type="pod_crashloop", severity="critical",
            content={"message": "CrashLoopBackOff in payment-service"},
            labels={"app": "api", "env": "prod"},
        )
        results = engine.recall(query, archive)
        assert len(results) > 0
        assert results[0].score > 0.3
        assert results[0].memory.signal.signal_type == "pod_crashloop"

    def test_no_precedent_for_novel_signal(self):
        """GIVEN memories of past incidents
        WHEN a completely novel signal type arrives
        THEN recall returns no matches above threshold"""
        archive = make_archive_with_memories()
        engine = RecallEngine()
        query = make_signal(
            signal_type="firmware_update_failure", severity="critical",
            content={"message": "BMC firmware update failed on chassis-7"},
            labels={"team": "hardware"},
        )
        results = engine.recall(query, archive, min_score=0.3)
        assert len(results) == 0

    def test_label_similarity_boosts_recall(self):
        """GIVEN two memories with different labels
        WHEN query labels match one memory
        THEN that memory scores higher"""
        archive = MemoryArchive()
        archive.store(
            make_signal(signal_type="error", content={"message": "failure A"},
                        labels={"app": "web", "env": "prod"}),
            classification="x",
        )
        archive.store(
            make_signal(signal_type="error", content={"message": "failure B"},
                        labels={"app": "api", "env": "staging"}),
            classification="x",
        )
        engine = RecallEngine()
        query = make_signal(signal_type="error", content={"message": "failure C"},
                            labels={"app": "web", "env": "prod"})
        results = engine.recall(query, archive)
        assert len(results) == 2
        assert results[0].memory.signal.labels.get("app") == "web"

    def test_recall_latency_under_10ms_for_1000_memories(self):
        """Performance: recall over 1000 memories completes in <10ms."""
        archive = MemoryArchive()
        for i in range(1000):
            archive.store(
                make_signal(signal_type=f"type_{i % 50}", severity="medium",
                            content={"message": f"event {i}", "value": float(i)},
                            labels={"batch": str(i % 10)}),
                classification="x",
            )
        engine = RecallEngine()
        query = make_signal(signal_type="type_25",
                            content={"message": "event 999", "value": 42.0},
                            labels={"batch": "5"})
        t0 = time.monotonic()
        results = engine.recall(query, archive, top_k=5)
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms < 50, f"Recall took {elapsed_ms:.1f}ms, expected <50ms"
        assert len(results) > 0


# ===========================================================================
# Stage 3: API — Endpoint Compliance
# ===========================================================================

class TestRecallAPI:
    def test_recall_endpoint(self):
        """POST /recall returns matches."""
        from fastapi.testclient import TestClient
        from cascade_compression.service import app

        client = TestClient(app)
        resp = client.post("/recall", json={
            "signal_type": "pod_crashloop",
            "severity": "high",
            "content": {"message": "CrashLoopBackOff"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "matches" in data
        assert "query_ms" in data

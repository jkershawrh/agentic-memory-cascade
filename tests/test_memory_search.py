"""Memory search engine tests — pgvector semantic search layer.

Tests the MemorySearchEngine in isolation (embedding logic, search results,
availability checks) WITHOUT requiring a live PostgreSQL instance.
The engine is designed to be optional — these tests verify both the happy
path (mocked pg) and the graceful degradation when deps are missing.
"""

import json
import math
from unittest import mock
from unittest.mock import MagicMock, patch, PropertyMock
from uuid import uuid4

import pytest

from cascade_compression.memory_search import (
    EMBEDDING_DIM,
    MemorySearchEngine,
    SearchResult,
    _hash_embed,
    compute_embedding,
    _available,
    _unavailable_reason,
)
from cascade_compression.cascade.memory import Memory, MemoryArchive
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


def make_memory(signal_type="pod_crashloop", severity="high",
                message="CrashLoopBackOff in api-server", namespace="production"):
    archive = MemoryArchive()
    sig = make_signal(signal_type=signal_type, severity=severity,
                      namespace=namespace,
                      content={"message": message})
    return archive.store(sig, classification="test")


def make_gpu_analysis(signal_type="pod_crashloop", severity="critical",
                      root_cause="OOM killer triggered",
                      impact="Service unavailable for 12 minutes",
                      confidence=0.92):
    return {
        "id": str(uuid4())[:16],
        "signal_type": signal_type,
        "severity": severity,
        "root_cause": root_cause,
        "impact": impact,
        "remediation": {"action": "increase_memory_limit", "target": "256Mi"},
        "confidence": confidence,
    }


# ===========================================================================
# Stage 1: Embedding Tests
# ===========================================================================

class TestHashEmbedding:
    """Hash-based fallback embedding produces valid vectors."""

    def test_output_dimension(self):
        """Hash embedding produces exactly EMBEDDING_DIM floats."""
        vec = _hash_embed("test signal")
        assert len(vec) == EMBEDDING_DIM

    def test_output_is_unit_vector(self):
        """Hash embedding is L2-normalized."""
        vec = _hash_embed("pod crashloop in production")
        magnitude = math.sqrt(sum(v * v for v in vec))
        assert magnitude == pytest.approx(1.0, abs=1e-6)

    def test_deterministic(self):
        """Same input always produces the same vector."""
        v1 = _hash_embed("disk pressure warning")
        v2 = _hash_embed("disk pressure warning")
        assert v1 == v2

    def test_different_inputs_different_vectors(self):
        """Different inputs produce different vectors."""
        v1 = _hash_embed("pod crashloop")
        v2 = _hash_embed("disk pressure")
        assert v1 != v2

    def test_empty_string(self):
        """Empty string produces a valid vector without error."""
        vec = _hash_embed("")
        assert len(vec) == EMBEDDING_DIM
        magnitude = math.sqrt(sum(v * v for v in vec))
        assert magnitude == pytest.approx(1.0, abs=1e-6)

    def test_long_text(self):
        """Long text produces a valid vector."""
        text = "CrashLoopBackOff " * 1000
        vec = _hash_embed(text)
        assert len(vec) == EMBEDDING_DIM


class TestComputeEmbedding:
    """compute_embedding uses the available backend."""

    def test_returns_correct_dimension(self):
        """Embedding has EMBEDDING_DIM dimensions regardless of backend."""
        vec = compute_embedding("test signal")
        assert len(vec) == EMBEDDING_DIM

    def test_returns_list_of_floats(self):
        """Embedding is a list of Python floats."""
        vec = compute_embedding("test signal")
        assert isinstance(vec, list)
        assert all(isinstance(v, float) for v in vec)


# ===========================================================================
# Stage 2: SearchResult Tests
# ===========================================================================

class TestSearchResult:
    def test_memory_result_to_dict(self):
        """Memory search result serializes with memory-specific fields."""
        r = SearchResult(
            id="abc-123",
            source_table="memories",
            similarity=0.8765,
            signal_type="pod_crashloop",
            severity="high",
            message="CrashLoopBackOff in api-server",
            namespace="production",
            source_instance="infra01",
            strength=0.85,
        )
        d = r.to_dict()
        assert d["id"] == "abc-123"
        assert d["source_table"] == "memories"
        assert d["similarity"] == 0.8765
        assert d["message"] == "CrashLoopBackOff in api-server"
        assert d["strength"] == 0.85
        assert "root_cause" not in d  # not a GPU analysis

    def test_analysis_result_to_dict(self):
        """GPU analysis search result serializes with analysis-specific fields."""
        r = SearchResult(
            id="gpu-001",
            source_table="gpu_analyses",
            similarity=0.91,
            signal_type="oom_killed",
            severity="critical",
            root_cause="Memory limit too low",
            impact="Pod restart loop",
            remediation={"action": "increase limit"},
            confidence=0.95,
        )
        d = r.to_dict()
        assert d["source_table"] == "gpu_analyses"
        assert d["root_cause"] == "Memory limit too low"
        assert d["confidence"] == 0.95
        assert "message" not in d  # not a memory

    def test_created_at_included_when_set(self):
        r = SearchResult(
            id="x", source_table="memories", similarity=0.5,
            created_at="2026-08-16T12:00:00+00:00",
        )
        d = r.to_dict()
        assert d["created_at"] == "2026-08-16T12:00:00+00:00"

    def test_created_at_omitted_when_none(self):
        r = SearchResult(id="x", source_table="memories", similarity=0.5)
        d = r.to_dict()
        assert "created_at" not in d


# ===========================================================================
# Stage 3: Engine Availability Checks
# ===========================================================================

class TestAvailability:
    def test_unavailable_reason_mentions_packages(self):
        """When deps are missing, the reason message names them."""
        reason = _unavailable_reason()
        # At minimum it should mention the install command
        assert "pip install" in reason

    def test_engine_init_without_connection(self):
        """Engine can be instantiated without connecting."""
        engine = MemorySearchEngine()
        assert engine.connected is False

    def test_connect_raises_without_deps(self):
        """connect() raises RuntimeError when psycopg2/pgvector missing."""
        engine = MemorySearchEngine()
        with patch("cascade_compression.memory_search._HAS_PG", False):
            with patch("cascade_compression.memory_search._available", return_value=False):
                with pytest.raises(RuntimeError, match="pgvector"):
                    engine.connect("postgresql://localhost/test")

    def test_search_memories_raises_when_not_connected(self):
        """search_memories raises when not connected."""
        engine = MemorySearchEngine()
        with pytest.raises(RuntimeError, match="Not connected"):
            engine.search_memories("test query")

    def test_search_analyses_raises_when_not_connected(self):
        """search_analyses raises when not connected."""
        engine = MemorySearchEngine()
        with pytest.raises(RuntimeError, match="Not connected"):
            engine.search_analyses("test query")

    def test_index_memory_raises_when_not_connected(self):
        """index_memory raises when not connected."""
        engine = MemorySearchEngine()
        with pytest.raises(RuntimeError, match="Not connected"):
            engine.index_memory({"signal_type": "test"})

    def test_stats_when_not_connected(self):
        """stats() returns a helpful dict when not connected."""
        engine = MemorySearchEngine()
        s = engine.stats()
        assert s["connected"] is False


# ===========================================================================
# Stage 4: Engine with Mocked PostgreSQL
# ===========================================================================

class TestEngineWithMockedPg:
    """Test the engine logic with a mocked database connection."""

    def _make_engine(self):
        """Create an engine with a mocked connection."""
        engine = MemorySearchEngine()
        mock_conn = MagicMock()
        # Default cursor mock returns 0 rows for COUNT(*) queries
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchone = MagicMock(return_value=(0,))
        mock_conn.cursor = MagicMock(return_value=mock_cur)
        engine._conn = mock_conn
        engine._connected = True
        return engine

    def test_index_memory_from_object(self):
        """index_memory accepts a Memory object."""
        engine = self._make_engine()
        memory = make_memory()
        mid = engine.index_memory(memory)
        assert mid == str(memory.memory_id)
        # Verify SQL was executed
        engine._conn.cursor().__enter__().execute.assert_called()

    def test_index_memory_from_dict(self):
        """index_memory accepts a dict."""
        engine = self._make_engine()
        d = {
            "memory_id": "test-id-123",
            "signal_type": "disk_pressure",
            "severity": "high",
            "content": {"message": "disk at 95%"},
            "namespace": "monitoring",
            "strength": 0.7,
            "source_instance": "infra01",
        }
        mid = engine.index_memory(d)
        assert mid == "test-id-123"

    def test_index_gpu_analysis(self):
        """index_gpu_analysis upserts analysis data."""
        engine = self._make_engine()
        analysis = make_gpu_analysis()
        aid = engine.index_gpu_analysis(analysis)
        assert aid == analysis["id"]
        engine._conn.cursor().__enter__().execute.assert_called()

    def test_index_gpu_analysis_generates_id(self):
        """When analysis has no id, one is generated from content hash."""
        engine = self._make_engine()
        analysis = {"root_cause": "OOM", "impact": "down"}
        aid = engine.index_gpu_analysis(analysis)
        assert len(aid) == 16  # sha256 prefix

    def test_ensure_schema(self):
        """ensure_schema creates extension and tables."""
        engine = self._make_engine()
        engine.ensure_schema()
        cur = engine._conn.cursor().__enter__()
        calls = [str(c) for c in cur.execute.call_args_list]
        # Should have calls for extension + 2 tables
        assert len(cur.execute.call_args_list) >= 3

    def test_close(self):
        """close() disconnects."""
        engine = self._make_engine()
        engine.close()
        assert engine.connected is False
        engine._conn.close.assert_called_once()

    def test_index_memories_bulk(self):
        """Bulk indexing processes all memories."""
        engine = self._make_engine()
        memories = [make_memory(message=f"event {i}") for i in range(5)]
        count = engine.index_memories_bulk(memories)
        assert count == 5

    def test_index_memories_bulk_handles_errors(self):
        """Bulk indexing continues past individual failures."""
        engine = self._make_engine()
        # Make execute fail on every other call
        call_count = [0]
        original_cursor = engine._conn.cursor

        def flaky_execute(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] % 2 == 0:
                raise Exception("connection lost")

        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.execute = flaky_execute
        engine._conn.cursor = MagicMock(return_value=mock_cur)

        memories = [make_memory(message=f"event {i}") for i in range(4)]
        count = engine.index_memories_bulk(memories)
        assert count < 4  # some should have failed

    def test_stats_when_connected(self):
        """stats() returns counts from the database."""
        engine = self._make_engine()
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchone = MagicMock(side_effect=[(42,), (7,)])
        engine._conn.cursor = MagicMock(return_value=mock_cur)

        s = engine.stats()
        assert s["connected"] is True
        assert s["memories_indexed"] == 42
        assert s["analyses_indexed"] == 7
        assert s["embedding_dim"] == EMBEDDING_DIM


# ===========================================================================
# Stage 5: JSONL Indexing
# ===========================================================================

class TestJsonlIndexing:
    def test_index_from_nonexistent_file(self):
        """index_analyses_from_jsonl returns 0 for missing file."""
        engine = MemorySearchEngine()
        engine._conn = MagicMock()
        engine._connected = True
        count = engine.index_analyses_from_jsonl("/nonexistent/path.jsonl")
        assert count == 0

    def test_index_from_jsonl(self, tmp_path):
        """index_analyses_from_jsonl reads and indexes each line."""
        engine = MemorySearchEngine()
        engine._conn = MagicMock()
        engine._connected = True

        analyses = [make_gpu_analysis(root_cause=f"cause {i}") for i in range(3)]
        path = tmp_path / "gpu-analyses.jsonl"
        with open(path, "w") as f:
            for a in analyses:
                f.write(json.dumps(a) + "\n")

        count = engine.index_analyses_from_jsonl(str(path))
        assert count == 3

    def test_index_from_jsonl_skips_bad_lines(self, tmp_path):
        """Malformed JSON lines are skipped, not fatal."""
        engine = MemorySearchEngine()
        engine._conn = MagicMock()
        engine._connected = True

        path = tmp_path / "gpu-analyses.jsonl"
        with open(path, "w") as f:
            f.write(json.dumps(make_gpu_analysis()) + "\n")
            f.write("this is not json\n")
            f.write(json.dumps(make_gpu_analysis()) + "\n")

        count = engine.index_analyses_from_jsonl(str(path))
        assert count == 2


# ===========================================================================
# Stage 6: Service Endpoint Tests
# ===========================================================================

class TestSearchEndpoints:
    """Test the /memories/search endpoints via FastAPI TestClient."""

    def test_search_without_pgvector(self):
        """When pgvector is unavailable, endpoint returns helpful error."""
        from fastapi.testclient import TestClient
        from cascade_compression.service import app

        client = TestClient(app)
        resp = client.get("/memories/search", params={"q": "pod crash"})
        assert resp.status_code == 200
        data = resp.json()
        # Should either return results or an error message
        assert "results" in data

    def test_search_stats_endpoint(self):
        """GET /memories/search/stats returns status info."""
        from fastapi.testclient import TestClient
        from cascade_compression.service import app

        client = TestClient(app)
        resp = client.get("/memories/search/stats")
        assert resp.status_code == 200

    def test_search_reindex_endpoint(self):
        """POST /memories/search/index returns without crashing."""
        from fastapi.testclient import TestClient
        from cascade_compression.service import app

        client = TestClient(app)
        resp = client.post("/memories/search/index")
        assert resp.status_code == 200

    def test_search_empty_query(self):
        """Search with empty q returns error."""
        from fastapi.testclient import TestClient
        from cascade_compression.service import app

        # Patch search engine as connected
        import cascade_compression.service as svc
        old_engine = svc._search_engine
        try:
            mock_engine = MagicMock()
            mock_engine.connected = True
            svc._search_engine = mock_engine
            # Also patch availability
            with patch("cascade_compression.service._search_available", return_value=True):
                client = TestClient(app)
                resp = client.get("/memories/search", params={"q": ""})
                assert resp.status_code == 200
                data = resp.json()
                assert "error" in data
        finally:
            svc._search_engine = old_engine

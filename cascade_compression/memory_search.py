"""Semantic search over cascade memories and GPU analyses via pgvector.

Optional integration — if PostgreSQL or pgvector isn't available, the engine
reports a clear error instead of breaking the service. Embeddings use
sentence-transformers (all-MiniLM-L6-v2, 384 dims) when available, falling
back to a deterministic hash-based vector for environments without the model.

    engine = MemorySearchEngine()
    await engine.connect("postgresql://user:pass@localhost/cascade")
    await engine.ensure_schema()
    engine.index_memory(memory)
    results = engine.search("storage failure on production node")
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

log = logging.getLogger(__name__)

EMBEDDING_DIM = 384

# ---------------------------------------------------------------------------
# Availability flags — set at import time, never raise on missing deps
# ---------------------------------------------------------------------------

_HAS_PG = False
_HAS_PGVECTOR = False
_HAS_SENTENCE_TRANSFORMERS = False

try:
    import psycopg2
    import psycopg2.extras
    _HAS_PG = True
except ImportError:
    psycopg2 = None  # type: ignore[assignment]

try:
    from pgvector.psycopg2 import register_vector
    _HAS_PGVECTOR = True
except ImportError:
    register_vector = None  # type: ignore[assignment]

try:
    from sentence_transformers import SentenceTransformer
    _HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    SentenceTransformer = None  # type: ignore[assignment]


def _available() -> bool:
    """Return True if all required dependencies are present."""
    return _HAS_PG and _HAS_PGVECTOR


def _unavailable_reason() -> str:
    missing = []
    if not _HAS_PG:
        missing.append("psycopg2-binary")
    if not _HAS_PGVECTOR:
        missing.append("pgvector")
    return (
        "Semantic search requires PostgreSQL with pgvector. "
        f"Missing packages: {', '.join(missing)}. "
        "Install with: pip install cascade-compression[search]"
    )


# ---------------------------------------------------------------------------
# Embedding: sentence-transformers preferred, hash fallback
# ---------------------------------------------------------------------------

_model_cache: Any = None


def _get_model():
    """Lazy-load the sentence-transformer model (CPU-only, ~80 MB)."""
    global _model_cache
    if _model_cache is not None:
        return _model_cache
    if _HAS_SENTENCE_TRANSFORMERS:
        _model_cache = SentenceTransformer("all-MiniLM-L6-v2")
        log.info("Loaded sentence-transformers model all-MiniLM-L6-v2")
    return _model_cache


def _hash_embed(text: str) -> List[float]:
    """Deterministic hash-based embedding fallback.

    Produces a 384-dim unit vector from a chain of SHA-256 hashes of the
    input text. Not semantically meaningful, but stable and fast — enough
    to prove the schema and pipeline work without sentence-transformers.
    """
    vec = []
    block = 0
    while len(vec) < EMBEDDING_DIM:
        h = hashlib.sha256(f"{text}:{block}".encode()).digest()
        for i in range(0, len(h), 4):
            raw = int.from_bytes(h[i:i + 4], "little", signed=True)
            vec.append(raw / (2 ** 31))
            if len(vec) >= EMBEDDING_DIM:
                break
        block += 1
    # Normalize to unit vector
    mag = math.sqrt(sum(v * v for v in vec))
    if mag > 0:
        vec = [v / mag for v in vec]
    return vec


def compute_embedding(text: str) -> List[float]:
    """Compute a 384-dim embedding for *text*."""
    model = _get_model()
    if model is not None:
        emb = model.encode(text, normalize_embeddings=True)
        return emb.tolist()
    return _hash_embed(text)


# ---------------------------------------------------------------------------
# Search result
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    """A single search hit from the vector store."""
    id: str
    source_table: str          # "memories" or "gpu_analyses"
    similarity: float
    signal_type: str = ""
    severity: str = ""
    message: str = ""
    namespace: str = ""
    source_instance: str = ""
    strength: float = 0.0
    # GPU analysis fields
    root_cause: str = ""
    impact: str = ""
    remediation: Optional[Dict[str, Any]] = None
    confidence: float = 0.0
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "source_table": self.source_table,
            "similarity": round(self.similarity, 4),
            "signal_type": self.signal_type,
            "severity": self.severity,
        }
        if self.source_table == "memories":
            d.update({
                "message": self.message,
                "namespace": self.namespace,
                "source_instance": self.source_instance,
                "strength": round(self.strength, 4),
            })
        else:
            d.update({
                "root_cause": self.root_cause,
                "impact": self.impact,
                "remediation": self.remediation,
                "confidence": round(self.confidence, 4),
            })
        if self.created_at:
            d["created_at"] = self.created_at
        return d


# ---------------------------------------------------------------------------
# SQL DDL
# ---------------------------------------------------------------------------

_CREATE_EXTENSION = "CREATE EXTENSION IF NOT EXISTS vector"

_CREATE_MEMORIES = """
CREATE TABLE IF NOT EXISTS memories (
    id              TEXT PRIMARY KEY,
    signal_type     TEXT NOT NULL DEFAULT '',
    severity        TEXT NOT NULL DEFAULT 'info',
    namespace       TEXT NOT NULL DEFAULT '',
    message         TEXT NOT NULL DEFAULT '',
    strength        REAL NOT NULL DEFAULT 0.0,
    embedding       vector({dim}),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_instance TEXT NOT NULL DEFAULT ''
)
""".format(dim=EMBEDDING_DIM)

_CREATE_ANALYSES = """
CREATE TABLE IF NOT EXISTS gpu_analyses (
    id              TEXT PRIMARY KEY,
    signal_type     TEXT NOT NULL DEFAULT '',
    severity        TEXT NOT NULL DEFAULT 'info',
    root_cause      TEXT NOT NULL DEFAULT '',
    impact          TEXT NOT NULL DEFAULT '',
    remediation_json TEXT NOT NULL DEFAULT '{{}}',
    confidence      REAL NOT NULL DEFAULT 0.0,
    embedding       vector({dim}),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""".format(dim=EMBEDDING_DIM)

_CREATE_MEM_IDX = """
CREATE INDEX IF NOT EXISTS memories_embedding_idx
    ON memories USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100)
"""

_CREATE_ANA_IDX = """
CREATE INDEX IF NOT EXISTS gpu_analyses_embedding_idx
    ON gpu_analyses USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100)
"""

# IVFFlat requires rows >= lists; fall back if table is too small
_CREATE_MEM_IDX_SMALL = """
CREATE INDEX IF NOT EXISTS memories_embedding_idx
    ON memories USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 10)
"""

_CREATE_ANA_IDX_SMALL = """
CREATE INDEX IF NOT EXISTS gpu_analyses_embedding_idx
    ON gpu_analyses USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 10)
"""


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class MemorySearchEngine:
    """Semantic search over cascade memories and GPU analyses.

    Requires PostgreSQL with the pgvector extension. When deps are missing,
    all public methods return helpful error messages instead of crashing.
    """

    def __init__(self):
        self._conn = None
        self._connected = False

    # -- lifecycle ----------------------------------------------------------

    def connect(self, db_url: str) -> None:
        """Connect to PostgreSQL.

        Args:
            db_url: PostgreSQL connection string,
                    e.g. ``postgresql://user:pass@host:5432/cascade``
        """
        if not _available():
            raise RuntimeError(_unavailable_reason())
        self._conn = psycopg2.connect(db_url)
        self._conn.autocommit = True
        register_vector(self._conn)
        self._connected = True
        log.info("Connected to pgvector database")

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def ensure_schema(self) -> None:
        """Create the pgvector extension, tables, and indexes if they don't exist."""
        if not self._connected:
            raise RuntimeError("Not connected — call connect() first")
        with self._conn.cursor() as cur:
            cur.execute(_CREATE_EXTENSION)
            cur.execute(_CREATE_MEMORIES)
            cur.execute(_CREATE_ANALYSES)
            # Indexes created lazily after enough rows exist
        log.info("Schema ensured (memories + gpu_analyses tables)")

    def _ensure_indexes(self) -> None:
        """Create IVFFlat indexes if enough rows exist."""
        if not self._connected:
            return
        with self._conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM memories")
            mem_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM gpu_analyses")
            ana_count = cur.fetchone()[0]

            if mem_count >= 100:
                try:
                    cur.execute(_CREATE_MEM_IDX)
                except Exception:
                    pass  # index may already exist with different params
            elif mem_count >= 10:
                try:
                    cur.execute(_CREATE_MEM_IDX_SMALL)
                except Exception:
                    pass

            if ana_count >= 100:
                try:
                    cur.execute(_CREATE_ANA_IDX)
                except Exception:
                    pass
            elif ana_count >= 10:
                try:
                    cur.execute(_CREATE_ANA_IDX_SMALL)
                except Exception:
                    pass

    # -- indexing -----------------------------------------------------------

    def index_memory(self, memory) -> str:
        """Compute embedding and upsert a Memory into the vector store.

        Args:
            memory: A ``Memory`` object (from cascade.memory) or a dict
                    with keys matching Memory.to_dict().

        Returns:
            The memory ID (string).
        """
        if not self._connected:
            raise RuntimeError("Not connected — call connect() first")

        if hasattr(memory, "to_dict"):
            d = memory.to_dict()
            mid = str(memory.memory_id)
        else:
            d = memory
            mid = str(d.get("memory_id", d.get("id", "")))

        message = str(d.get("content", {}).get("message", ""))
        signal_type = d.get("signal_type", "")
        severity = d.get("severity", "info")
        namespace = d.get("namespace", "")
        strength = d.get("strength", 0.0)
        source_instance = d.get("source_instance", "")
        created_at = d.get("formed_at") or datetime.now(timezone.utc).isoformat()

        # Build embedding text from signal_type + severity + message
        embed_text = f"{signal_type} {severity} {message}"
        embedding = compute_embedding(embed_text)

        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO memories (id, signal_type, severity, namespace, message,
                                      strength, embedding, created_at, source_instance)
                VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    strength = EXCLUDED.strength,
                    embedding = EXCLUDED.embedding,
                    message = EXCLUDED.message
                """,
                (mid, signal_type, severity, namespace, message,
                 strength, str(embedding), created_at, source_instance),
            )
        return mid

    def index_gpu_analysis(self, analysis: Dict[str, Any]) -> str:
        """Compute embedding and upsert a GPU analysis into the vector store.

        Args:
            analysis: A dict from gpu-analyses.jsonl with keys like
                      root_cause, impact, remediation, confidence.

        Returns:
            The analysis ID (string).
        """
        if not self._connected:
            raise RuntimeError("Not connected — call connect() first")

        aid = str(analysis.get("id", analysis.get("memory_id", "")))
        if not aid:
            aid = hashlib.sha256(
                json.dumps(analysis, sort_keys=True, default=str).encode()
            ).hexdigest()[:16]

        signal_type = analysis.get("signal_type", "")
        severity = analysis.get("severity", "info")
        root_cause = analysis.get("root_cause", "")
        impact = analysis.get("impact", "")
        remediation = analysis.get("remediation", {})
        confidence = analysis.get("confidence", 0.0)
        created_at = analysis.get("created_at") or datetime.now(timezone.utc).isoformat()

        remediation_json = json.dumps(remediation, default=str)

        # Build embedding text from the analysis content
        embed_text = f"{signal_type} {severity} {root_cause} {impact}"
        embedding = compute_embedding(embed_text)

        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO gpu_analyses (id, signal_type, severity, root_cause, impact,
                                          remediation_json, confidence, embedding, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s)
                ON CONFLICT (id) DO UPDATE SET
                    root_cause = EXCLUDED.root_cause,
                    impact = EXCLUDED.impact,
                    remediation_json = EXCLUDED.remediation_json,
                    confidence = EXCLUDED.confidence,
                    embedding = EXCLUDED.embedding
                """,
                (aid, signal_type, severity, root_cause, impact,
                 remediation_json, confidence, str(embedding), created_at),
            )
        return aid

    # -- search -------------------------------------------------------------

    def search(self, query_text: str, top_k: int = 10,
               min_similarity: float = 0.5) -> List[SearchResult]:
        """Semantic search across both memories and GPU analyses.

        Returns results from both tables, merged and ranked by cosine
        similarity to the query embedding.
        """
        mem_results = self.search_memories(query_text, top_k=top_k,
                                           min_similarity=min_similarity)
        ana_results = self.search_analyses(query_text, top_k=top_k,
                                           min_similarity=min_similarity)

        combined = mem_results + ana_results
        combined.sort(key=lambda r: r.similarity, reverse=True)
        return combined[:top_k]

    def search_memories(self, query_text: str, top_k: int = 10,
                        filters: Optional[Dict[str, str]] = None,
                        min_similarity: float = 0.5) -> List[SearchResult]:
        """Search the memories table with optional type/severity/namespace filters."""
        if not self._connected:
            raise RuntimeError("Not connected — call connect() first")

        embedding = compute_embedding(query_text)

        where_clauses = []
        params: list = [str(embedding), min_similarity]

        if filters:
            if "signal_type" in filters:
                where_clauses.append("signal_type = %s")
                params.append(filters["signal_type"])
            if "severity" in filters:
                where_clauses.append("severity = %s")
                params.append(filters["severity"])
            if "namespace" in filters:
                where_clauses.append("namespace = %s")
                params.append(filters["namespace"])

        where_sql = ""
        if where_clauses:
            where_sql = "AND " + " AND ".join(where_clauses)

        params.append(top_k)

        query = f"""
            SELECT id, signal_type, severity, namespace, message, strength,
                   source_instance, created_at,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM memories
            WHERE 1 - (embedding <=> %s::vector) >= %s
            {where_sql}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        # We need the embedding param twice in the query (once for similarity calc, once for ORDER BY)
        # Plus once more for the WHERE filter
        final_params = [str(embedding)] + params + [str(embedding)]

        # Rebuild query with correct parameter placement
        filter_where = ""
        filter_params: list = []
        if filters:
            if "signal_type" in filters:
                filter_where += " AND signal_type = %s"
                filter_params.append(filters["signal_type"])
            if "severity" in filters:
                filter_where += " AND severity = %s"
                filter_params.append(filters["severity"])
            if "namespace" in filters:
                filter_where += " AND namespace = %s"
                filter_params.append(filters["namespace"])

        sql = f"""
            SELECT id, signal_type, severity, namespace, message, strength,
                   source_instance, created_at,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM memories
            WHERE 1 - (embedding <=> %s::vector) >= %s
            {filter_where}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        emb_str = str(embedding)
        sql_params = [emb_str, emb_str, min_similarity] + filter_params + [emb_str, top_k]

        results = []
        with self._conn.cursor() as cur:
            cur.execute(sql, sql_params)
            for row in cur.fetchall():
                results.append(SearchResult(
                    id=row[0],
                    source_table="memories",
                    signal_type=row[1],
                    severity=row[2],
                    namespace=row[3],
                    message=row[4],
                    strength=row[5],
                    source_instance=row[6],
                    created_at=row[7].isoformat() if row[7] else None,
                    similarity=float(row[8]),
                ))
        return results

    def search_analyses(self, query_text: str, top_k: int = 10,
                        min_similarity: float = 0.5) -> List[SearchResult]:
        """Search the gpu_analyses table."""
        if not self._connected:
            raise RuntimeError("Not connected — call connect() first")

        embedding = compute_embedding(query_text)
        emb_str = str(embedding)

        sql = """
            SELECT id, signal_type, severity, root_cause, impact,
                   remediation_json, confidence, created_at,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM gpu_analyses
            WHERE 1 - (embedding <=> %s::vector) >= %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """

        results = []
        with self._conn.cursor() as cur:
            cur.execute(sql, (emb_str, emb_str, min_similarity, emb_str, top_k))
            for row in cur.fetchall():
                remediation = {}
                try:
                    remediation = json.loads(row[5]) if row[5] else {}
                except (json.JSONDecodeError, TypeError):
                    remediation = {"raw": row[5]}

                results.append(SearchResult(
                    id=row[0],
                    source_table="gpu_analyses",
                    signal_type=row[1],
                    severity=row[2],
                    root_cause=row[3],
                    impact=row[4],
                    remediation=remediation,
                    confidence=float(row[6]),
                    created_at=row[7].isoformat() if row[7] else None,
                    similarity=float(row[8]),
                ))
        return results

    # -- bulk indexing ------------------------------------------------------

    def index_memories_bulk(self, memories: list) -> int:
        """Index a batch of memories. Returns count indexed."""
        count = 0
        for memory in memories:
            try:
                self.index_memory(memory)
                count += 1
            except Exception as e:
                log.warning("Failed to index memory: %s", e)
        if count > 0:
            try:
                self._ensure_indexes()
            except Exception as e:
                log.warning("Failed to create indexes: %s", e)
        return count

    def index_analyses_from_jsonl(self, path: str) -> int:
        """Load and index GPU analyses from a JSONL file.

        Args:
            path: Path to the gpu-analyses.jsonl file.

        Returns:
            Count of analyses indexed.
        """
        import os
        if not os.path.exists(path):
            log.warning("GPU analyses file not found: %s", path)
            return 0

        count = 0
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    analysis = json.loads(line)
                    self.index_gpu_analysis(analysis)
                    count += 1
                except (json.JSONDecodeError, Exception) as e:
                    log.warning("Failed to index analysis line: %s", e)

        if count > 0:
            try:
                self._ensure_indexes()
            except Exception as e:
                log.warning("Failed to create indexes: %s", e)
        log.info("Indexed %d GPU analyses from %s", count, path)
        return count

    # -- stats --------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """Return counts and index status."""
        if not self._connected:
            return {"connected": False, "reason": "not connected"}

        with self._conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM memories")
            mem_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM gpu_analyses")
            ana_count = cur.fetchone()[0]

        return {
            "connected": True,
            "memories_indexed": mem_count,
            "analyses_indexed": ana_count,
            "embedding_dim": EMBEDDING_DIM,
            "embedding_model": (
                "all-MiniLM-L6-v2" if _HAS_SENTENCE_TRANSFORMERS
                else "hash-fallback"
            ),
        }

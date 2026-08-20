"""Standalone cascade compression service.

One container. Point it at a signal stream and an LLM. It discovers
what is noise, validates it, and compresses your signal volume.

    oc new-app https://github.com/jkershawrh/cascade-compression \
      -e CASCADE_LLM_URL=https://your-llm/v1 \
      -e CASCADE_LLM_KEY=sk-...

Environment variables:
    CASCADE_LLM_URL          LLM API base URL (required for agent discovery)
    CASCADE_LLM_KEY          LLM API key
    CASCADE_LLM_MODEL        LLM model name (deployment-specific, no default)
    CASCADE_MICRO_MODEL      Model for micro tier / medium severity (default: CASCADE_LLM_MODEL)
    CASCADE_MACRO_MODEL      Model for macro tier / high+critical severity (default: CASCADE_LLM_MODEL)
    CASCADE_DOMAIN           Domain pack (default: kubernetes)
    CASCADE_LEDGER_URL       Immutable ledger URL (optional, for governance)
    CASCADE_LEDGER_TOKEN     Ledger bearer token
    CASCADE_SHADOW_RATE      Shadow validation sample rate (default: 0.05)
    CASCADE_ACTIVATION_TTL_HOURS  Agent TTL in hours (default: 72)
    CASCADE_HUMAN_GATE       Set to 1 to require human approval before activation
    CASCADE_STATE_FILE       Path to persist agent state across restarts
"""

import importlib
import logging
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .bridge import CascadeBridge
from .cascade.memory import MemoryArchive
from .cascade.inverse import SuppressionArchive, inverse_analysis, export_learned_agents
from .cascade.memory_intelligence import MemoryIntelligence
from .cascade.recall import RecallEngine
def _search_available():
    return False

def _search_unavailable_reason():
    return "Semantic search module not included in this quickstart"

log = logging.getLogger(__name__)

_bridge: CascadeBridge = None
_memory_archive: MemoryArchive = None
_memory_intel: MemoryIntelligence = None
_search_engine = None


def _load_prompt(domain: str) -> str:
    try:
        mod = importlib.import_module(f"cascade_compression.domains.{domain}")
        return getattr(mod, "SYSTEM_PROMPT", "")
    except ImportError:
        return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bridge, _memory_archive, _memory_intel
    domain = os.getenv("CASCADE_DOMAIN", "kubernetes")
    _bridge = CascadeBridge(
        llm_url=os.getenv("CASCADE_LLM_URL", ""),
        llm_key=os.getenv("CASCADE_LLM_KEY", ""),
        llm_model=os.getenv("CASCADE_LLM_MODEL", ""),
        system_prompt=_load_prompt(domain),
        domain=domain,
        ledger_url=os.getenv("CASCADE_LEDGER_URL", ""),
        ledger_token=os.getenv("CASCADE_LEDGER_TOKEN", ""),
    )
    _memory_archive = _bridge.memory_archive
    _memory_intel = MemoryIntelligence()
    try:
        mod = importlib.import_module(f"cascade_compression.domains.{domain}")
        domain_config = getattr(mod, "MEMORY_CONFIG", None)
        if domain_config:
            _memory_intel.register_domain(domain, domain_config)
            if _memory_archive:
                _memory_archive.set_decay_config(_memory_intel.decay_config)
            log.info("Registered memory config for domain=%s", domain)
    except (ImportError, AttributeError):
        pass
    # Optional: connect pgvector search engine
    global _search_engine
    db_url = os.getenv("CASCADE_SEARCH_DB_URL", "")
    if db_url and _search_available():
        try:
            _search_engine = MemorySearchEngine()
            _search_engine.connect(db_url)
            _search_engine.ensure_schema()
            log.info("pgvector search engine connected")
        except Exception as e:
            log.warning("pgvector search engine failed to connect: %s", e)
            _search_engine = None

    log.info("Cascade compression ready (domain=%s)", domain)
    yield
    if _search_engine:
        _search_engine.close()


app = FastAPI(
    title="Cascade Compression",
    version="1.0.0",
    description="Self-tuning signal compression — discover what needs AI and what does not.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SignalInput(BaseModel):
    signal_type: str = ""
    severity: str = "info"
    source: str = ""
    namespace: str = ""
    content: Dict[str, Any] = Field(default_factory=dict)
    labels: Dict[str, str] = Field(default_factory=dict)


class BatchRequest(BaseModel):
    signals: List[SignalInput]


class _SignalAdapter:
    def __init__(self, s: SignalInput):
        from uuid import uuid4
        self.signal_id = uuid4()
        self.signal_type = s.signal_type
        self.severity = s.severity
        self.resource_name = s.source
        self.namespace = s.namespace
        self.cluster_id = ""
        self.resource_kind = ""
        self.evidence = s.content
        self.labels = s.labels


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "cascade-compression",
        "enabled": _bridge.enabled if _bridge else False,
    }


@app.get("/stats")
def stats():
    if not _bridge or not _bridge.enabled:
        return {"status": "not_ready"}
    return {
        "stats": _bridge.get_stats(),
        "llm": _bridge.get_llm_summary(),
        "activated_agents": list(_bridge._activated_types),
        "promotion_log": _bridge.get_promotion_log(20),
    }


@app.post("/cascade")
def cascade(request: BatchRequest):
    if not _bridge or not _bridge.enabled:
        return {"error": "cascade not ready"}

    adapted = [_SignalAdapter(s) for s in request.signals]

    t0 = __import__("time").monotonic()
    bridge_result = _bridge.process(adapted)
    cascade_ms = (__import__("time").monotonic() - t0) * 1000

    survivors = []
    if bridge_result.get("enabled"):
        for sig in _bridge._last_remaining or []:
            original = next(
                (s for a, s in zip(adapted, request.signals)
                 if a.signal_id == sig.signal_id), None)
            if original:
                survivors.append({
                    "signal_type": original.signal_type,
                    "severity": original.severity,
                    "source": original.source,
                    "namespace": original.namespace,
                    "content": original.content,
                })

    return {
        "total": len(request.signals),
        "compressed": len(request.signals) - len(survivors),
        "survivors": len(survivors),
        "compression_ratio": bridge_result.get("compression", 0),
        "cascade_ms": round(cascade_ms, 1),
        "signals_needing_attention": survivors,
    }


@app.get("/agents")
def agents():
    if not _bridge:
        return {"agents": []}
    return {
        "activated": list(_bridge._activated_types),
        "discovered": _bridge.get_discovered_agents(),
        "promotion_log": _bridge.get_promotion_log(50),
    }


@app.get("/meta")
def meta(limit: int = 50):
    """Meta-cascade: signals about the cascade's own operations."""
    if not _bridge:
        return {"meta_signals": [], "count": 0}
    signals = _bridge.get_meta_signals(limit)
    return {
        "meta_signals": signals,
        "count": len(signals),
        "meta_target": bool(_bridge._meta_target),
    }


class MemoryQueryRequest(BaseModel):
    signal_type: str = None
    labels: Dict[str, str] = None
    min_strength: float = 0.0
    limit: int = 100


@app.post("/recall")
def recall(signal: SignalInput):
    if not _memory_archive:
        return {"matches": [], "query_ms": 0}
    from .cascade.protocol import Signal as CascadeSignal
    query = CascadeSignal(
        signal_type=signal.signal_type,
        severity=signal.severity,
        source=signal.source,
        namespace=signal.namespace,
        content=signal.content,
        labels=signal.labels,
    )
    t0 = __import__("time").monotonic()
    engine = RecallEngine()
    results = engine.recall(query, _memory_archive, reinforce=True)
    query_ms = (__import__("time").monotonic() - t0) * 1000
    return {
        "matches": [{
            "memory_id": str(r.memory.memory_id),
            "score": round(r.score, 4),
            "breakdown": {k: round(v, 4) for k, v in r.breakdown.items()},
            "signal_type": r.memory.signal.signal_type,
            "classification": r.memory.classification,
            "strength": round(r.memory.strength, 4),
        } for r in results],
        "query_ms": round(query_ms, 2),
    }


@app.get("/inverse")
def inverse():
    archive = _bridge.memory_archive if _bridge else _memory_archive
    suppression = _bridge.suppression_archive if _bridge else None
    if not archive or not suppression:
        return {"error": "not ready"}
    return inverse_analysis(suppression, archive, _memory_intel)


@app.get("/agents/export")
def agents_export():
    if not _bridge:
        return {"agents": []}
    return export_learned_agents(_bridge)


@app.get("/analyze")
def analyze():
    archive = _bridge.memory_archive if _bridge else _memory_archive
    if not archive or not _memory_intel:
        return {"error": "memory not ready"}
    return _memory_intel.analyze(archive)


@app.post("/consolidate")
def consolidate(batch_size: int = 1000):
    if not _memory_archive or not _bridge:
        return {"processed": 0, "evicted": 0, "compression_ratio": 0.0}
    from .cascade.agents import default_agents
    from .cascade.pipeline import CascadePipeline
    result = _memory_archive.consolidate(
        lambda: CascadePipeline(default_agents()),
        batch_size=batch_size,
    )
    _bridge.check_consolidation_meta(result)
    return result


@app.get("/memories/stats")
def memory_stats():
    if not _memory_archive:
        return {"size": 0, "formed_total": 0, "evictions_total": 0}
    return _memory_archive.stats()


@app.get("/memories/export")
def memory_export(min_strength: float = 0.0, since: str = None):
    if not _memory_archive:
        return {"instance_id": "", "memories": []}
    return _memory_archive.export_memories(min_strength=min_strength, since=since)


@app.post("/memories/import")
def memory_import(data: Dict[str, Any]):
    if not _memory_archive:
        return {"imported": 0}
    count = _memory_archive.import_memories(data)
    return {"imported": count}


@app.post("/memories/query")
def memory_query(request: MemoryQueryRequest = None):
    if not _memory_archive:
        return {"memories": []}
    if request is None:
        request = MemoryQueryRequest()
    results = _memory_archive.query(
        signal_type=request.signal_type,
        labels=request.labels,
        min_strength=request.min_strength,
        limit=request.limit,
    )
    return {"memories": [m.to_dict() for m in results]}


# ---------------------------------------------------------------------------
# Semantic search endpoints (optional — requires pgvector)
# ---------------------------------------------------------------------------

@app.get("/memories/search")
def memory_search(q: str = "", top_k: int = 10, min_similarity: float = 0.5,
                  signal_type: str = None, severity: str = None,
                  namespace: str = None):
    """Semantic search over indexed memories and GPU analyses.

    Query params:
        q:              Search text (required)
        top_k:          Max results (default 10)
        min_similarity: Minimum cosine similarity threshold (default 0.5)
        signal_type:    Filter by signal type (optional)
        severity:       Filter by severity (optional)
        namespace:      Filter by namespace (optional)
    """
    if not _search_available():
        return {
            "error": _search_unavailable_reason(),
            "results": [],
        }
    if not _search_engine or not _search_engine.connected:
        return {
            "error": (
                "Semantic search is not connected. Set CASCADE_SEARCH_DB_URL "
                "to a PostgreSQL connection string with pgvector enabled."
            ),
            "results": [],
        }
    if not q:
        return {"error": "Query parameter 'q' is required", "results": []}

    filters = {}
    if signal_type:
        filters["signal_type"] = signal_type
    if severity:
        filters["severity"] = severity
    if namespace:
        filters["namespace"] = namespace

    try:
        if filters:
            results = _search_engine.search_memories(
                q, top_k=top_k, filters=filters, min_similarity=min_similarity,
            )
        else:
            results = _search_engine.search(
                q, top_k=top_k, min_similarity=min_similarity,
            )
        return {
            "query": q,
            "results": [r.to_dict() for r in results],
            "count": len(results),
        }
    except Exception as e:
        log.error("Search failed: %s", e)
        return {"error": str(e), "results": []}


@app.get("/memories/search/stats")
def memory_search_stats():
    """Return pgvector index statistics."""
    if not _search_available():
        return {"error": _search_unavailable_reason()}
    if not _search_engine:
        return {"error": "Search engine not initialized"}
    return _search_engine.stats()


@app.post("/memories/search/index")
def memory_search_reindex():
    """Trigger a full reindex of current memories into pgvector."""
    if not _search_available():
        return {"error": _search_unavailable_reason()}
    if not _search_engine or not _search_engine.connected:
        return {"error": "Search engine not connected"}
    if not _memory_archive:
        return {"indexed": 0}

    memories = _memory_archive.query(limit=_memory_archive._max_capacity)
    count = _search_engine.index_memories_bulk(memories)

    # Also index GPU analyses if the file exists
    gpu_path = "/state/gpu-analyses.jsonl"
    ana_count = _search_engine.index_analyses_from_jsonl(gpu_path)

    return {
        "memories_indexed": count,
        "analyses_indexed": ana_count,
    }


# ---------------------------------------------------------------------------
# Biography — structured platform autobiography from memory + inverse data
# ---------------------------------------------------------------------------

def generate_biography(bridge, memory_archive, memory_intel) -> Dict[str, Any]:
    """Generate a structured platform biography from cascade state.

    Pure data aggregation — no LLM calls, no file I/O. Tells the story
    of what the platform has experienced through its memory and inverse
    cascade data.
    """
    now = datetime.now(timezone.utc).isoformat()

    # ── 1. Timeline ──────────────────────────────────────────────────
    timeline = {"generated_at": now}
    if bridge:
        timeline["started_at"] = bridge.stats.started_at
        timeline["signals_processed"] = bridge.stats.signals_processed
        timeline["domain"] = bridge.domain

        # Extract milestone events from promotion log
        milestones = []
        for entry in bridge.get_promotion_log(1000):
            event = entry.get("event", "")
            if event in ("activated", "promoted", "discovered"):
                milestones.append({
                    "timestamp": entry.get("timestamp", ""),
                    "event": event,
                    "agent": entry.get("agent", ""),
                    "tier": entry.get("tier", ""),
                })
        activations = [m for m in milestones if m["event"] == "activated"]
        if activations:
            timeline["first_activation"] = activations[0]
            timeline["latest_activation"] = activations[-1]
            timeline["total_activations"] = len(activations)

        demotions = [e for e in bridge.get_promotion_log(1000)
                     if e.get("event") == "demotion"
                     or e.get("event_type") == "demotion"]
        if demotions:
            timeline["first_demotion"] = {
                "timestamp": demotions[0].get("timestamp", ""),
                "agent": demotions[0].get("agent",
                                          demotions[0].get("agent_name", "")),
                "reason": demotions[0].get("reason", ""),
            }
            timeline["total_demotions"] = len(demotions)

        timeline["milestone_count"] = len(milestones)

    # ── 2. Top Memories ──────────────────────────────────────────────
    top_memories = []
    if memory_archive and memory_archive.size > 0:
        strongest = memory_archive.query(min_strength=0.0, limit=20)
        for m in strongest:
            entry = {
                "signal_type": m.signal.signal_type,
                "severity": m.signal.severity,
                "strength": round(m.strength, 4),
                "formed_at": m.formed_at,
                "recall_count": m.recall_count,
                "consolidation_count": m.consolidation_count,
                "classification": m.classification,
                "message": m.signal.content.get("message", "")[:200],
            }
            if m.analysis:
                entry["has_analysis"] = True
                entry["root_cause"] = m.analysis.get("root_cause", "")[:200]
            top_memories.append(entry)

    # ── 3. Patterns Learned ──────────────────────────────────────────
    patterns_learned = []
    if bridge:
        for sig_type in sorted(bridge._activated_types):
            pattern_type = bridge._activated_patterns.get(sig_type, "unknown")
            noise_count = bridge._llm_noise_counts.get(sig_type, 0)
            important_count = bridge._llm_important_counts.get(sig_type, 0)
            activated_at = bridge._activation_timestamps.get(sig_type, "")
            patterns_learned.append({
                "signal_type": sig_type,
                "pattern_type": pattern_type,
                "noise_count": noise_count,
                "important_count": important_count,
                "activated_at": activated_at,
                "action": (
                    "repeat flood suppression"
                    if pattern_type == "repeat_flood"
                    else "dominant noise suppression"
                ),
            })

    # ── 4. Noise Profile ─────────────────────────────────────────────
    noise_profile: Dict[str, Any] = {"baseline_types": 0, "top_noise": []}
    suppression = bridge.suppression_archive if bridge else None
    if suppression and suppression.size > 0:
        from .cascade.inverse import generate_baseline
        baseline = generate_baseline(suppression)
        noise_profile["baseline_types"] = len(baseline.signal_types)
        noise_profile["total_suppression_decisions"] = suppression._total_decisions
        top_noise = sorted(
            [{"signal_type": k, "frequency": v.get("frequency", 0),
              "strength": round(v.get("strength", 0), 3),
              "agents": v.get("agents", [])}
             for k, v in baseline.signal_types.items()],
            key=lambda x: x["frequency"],
            reverse=True,
        )[:15]
        noise_profile["top_noise"] = top_noise
        noise_profile["interpretation"] = (
            f"The platform considers {len(baseline.signal_types)} signal types "
            f"to be normal background noise, learned from "
            f"{suppression._total_decisions} suppression decisions."
        )

    # ── 5. Causal Chains ─────────────────────────────────────────────
    causal_chains: List[Dict[str, str]] = []
    if memory_intel:
        graph = memory_intel.causal_graph
        for cause, effects in graph._forward.items():
            for effect in effects:
                causal_chains.append({"cause": cause, "effect": effect})

    # ── 6. Gaps ──────────────────────────────────────────────────────
    causal_gaps: List[Dict[str, Any]] = []
    if memory_archive and memory_intel:
        from .cascade.inverse import find_all_gaps
        causal_gaps = find_all_gaps(memory_archive, memory_intel.causal_graph)

    # ── 7. Absences ──────────────────────────────────────────────────
    absences: List[Dict[str, Any]] = []
    if memory_intel:
        detector = memory_intel.absence_detector
        if detector.expectations:
            absences = detector.check_missing(now)

    # ── 8. GPU Analyses ──────────────────────────────────────────────
    gpu_summary: Dict[str, Any] = {"count": 0, "analyses": []}
    if bridge and bridge._gpu_analyses:
        analyses = bridge._gpu_analyses
        gpu_summary["count"] = len(analyses)
        by_type: Dict[str, list] = defaultdict(list)
        for a in analyses:
            by_type[a.get("signal_type", "unknown")].append(a)
        gpu_summary["by_signal_type"] = {
            sig_type: {
                "count": len(items),
                "root_causes": list({
                    a.get("root_cause", "")[:150]
                    for a in items if a.get("root_cause")
                })[:5],
                "avg_confidence": round(
                    sum(a.get("confidence", 0) for a in items) / len(items), 3
                ) if items else 0,
            }
            for sig_type, items in sorted(
                by_type.items(), key=lambda kv: len(kv[1]), reverse=True
            )[:10]
        }
        gpu_summary["recent"] = [
            {
                "signal_type": a.get("signal_type", ""),
                "severity": a.get("severity", ""),
                "root_cause": a.get("root_cause", "")[:200],
                "confidence": a.get("confidence", 0),
                "model": a.get("model", ""),
                "timestamp": a.get("timestamp", ""),
            }
            for a in analyses[-5:]
        ]

    # ── 9. Health Score ──────────────────────────────────────────────
    health = _compute_health_score(
        memory_archive=memory_archive,
        causal_gaps=causal_gaps,
        causal_chains=causal_chains,
        absences=absences,
    )

    narrative = _generate_narrative(
        timeline, top_memories, patterns_learned, noise_profile,
        causal_gaps[:20], absences, gpu_summary, health,
    )

    return {
        "biography": {
            "timeline": timeline,
            "top_memories": top_memories,
            "patterns_learned": patterns_learned,
            "noise_profile": noise_profile,
            "causal_chains": causal_chains,
            "causal_gaps": causal_gaps[:20],
            "absences": absences,
            "gpu_analyses": gpu_summary,
            "health": health,
            "narrative": narrative,
        },
    }


def _compute_health_score(
    memory_archive,
    causal_gaps: List[Dict],
    causal_chains: List[Dict],
    absences: List[Dict],
) -> Dict[str, Any]:
    """Compute platform health from memory state and inverse data.

    Factors:
    - gap_ratio: causal gaps / total causal rules (lower = healthier)
    - absence_count: missing expected signals (lower = healthier)
    - strength_distribution: how strong memories are on average
    - memory_coverage: how full the archive is relative to capacity
    """
    total_rules = len(causal_chains) if causal_chains else 0
    gap_count = len(causal_gaps)
    gap_ratio = gap_count / max(1, total_rules)

    absence_count = len(absences)

    avg_strength = 0.0
    strength_std = 0.0
    memory_count = 0
    if memory_archive and memory_archive.size > 0:
        stats = memory_archive.stats()
        avg_strength = stats.get("avg_strength", 0.0)
        memory_count = stats.get("size", 0)
        memories = memory_archive.query(limit=memory_archive.size or 1)
        if memories:
            strengths = [m.strength for m in memories]
            mean = sum(strengths) / len(strengths)
            variance = sum((s - mean) ** 2 for s in strengths) / len(strengths)
            strength_std = variance ** 0.5

    score = 100.0
    score -= min(30.0, gap_ratio * 30.0)
    score -= min(20.0, absence_count * 5.0)
    if memory_count > 0:
        strength_penalty = max(0.0, (0.3 - avg_strength)) * 66.7
        score -= min(20.0, strength_penalty)
    if memory_count == 0:
        score -= 10.0
    score -= min(20.0, strength_std * 20.0)
    score = max(0.0, min(100.0, score))

    return {
        "score": round(score, 1),
        "grade": (
            "excellent" if score >= 90 else
            "good" if score >= 75 else
            "fair" if score >= 50 else
            "poor"
        ),
        "factors": {
            "gap_ratio": round(gap_ratio, 3),
            "gap_count": gap_count,
            "total_causal_rules": total_rules,
            "absence_count": absence_count,
            "avg_memory_strength": round(avg_strength, 4),
            "strength_std": round(strength_std, 4),
            "memory_count": memory_count,
        },
    }


def _generate_narrative(timeline, top_memories, patterns_learned, noise_profile,
                        causal_gaps, absences, gpu_summary, health) -> Dict[str, Any]:
    """Generate English prose chapters from structured biography data."""
    chapters = []
    signals = timeline.get("signals_processed", 0)
    domain = timeline.get("domain", "unknown")
    agents = timeline.get("total_activations", 0)
    grade = health.get("grade", "unknown")
    score = health.get("score", 0)
    mem_count = health.get("factors", {}).get("memory_count", 0)

    # Opening
    opening = f"This {domain} cascade has processed {signals:,} signals"
    started = timeline.get("started_at", "")
    if started:
        opening += f" since {started[:10]}"
    opening += f". It discovered {agents} suppression agents and formed {mem_count} memories."
    opening += f" Platform health: {grade} ({score:.0f}/100)."

    # Chapter 1: What it learned
    ch1_lines = []
    if patterns_learned:
        ch1_lines.append(f"The cascade taught itself {len(patterns_learned)} suppression rules by observing the signal stream.")
        for p in patterns_learned[:5]:
            noise = p.get("noise_count", 0)
            imp = p.get("important_count", 0)
            ch1_lines.append(
                f"  {p['signal_type']}: {noise:,} noise / {imp:,} important — {p.get('action', 'suppressed')}."
            )
        if len(patterns_learned) > 5:
            ch1_lines.append(f"  ...and {len(patterns_learned) - 5} more patterns.")
    else:
        ch1_lines.append("No suppression patterns discovered yet. The cascade is still learning.")
    chapters.append({"title": "What It Learned", "text": "\n".join(ch1_lines)})

    # Chapter 2: What it remembers
    ch2_lines = []
    if top_memories:
        all_max = all(m.get("strength", 0) >= 0.99 for m in top_memories[:10])
        if all_max:
            ch2_lines.append(f"Every top memory is at maximum strength. These are chronic conditions, not transient events.")
        else:
            ch2_lines.append(f"The strongest memories tell the story of what keeps happening.")

        from collections import Counter
        type_counts = Counter(m["signal_type"] for m in top_memories)
        for sig_type, count in type_counts.most_common(5):
            sample = next(m for m in top_memories if m["signal_type"] == sig_type)
            strength = sample.get("strength", 0)
            msg = sample.get("message", "")
            root = sample.get("root_cause", "")
            line = f"  {sig_type}: {count} memories at strength {strength:.2f}"
            if msg:
                line += f" — \"{msg[:80]}\""
            if root:
                line += f" Root cause: {root[:80]}"
            ch2_lines.append(line)
    else:
        ch2_lines.append("No memories formed yet. The cascade needs more time to identify what matters.")
    chapters.append({"title": "What It Remembers", "text": "\n".join(ch2_lines)})

    # Chapter 3: What it ignores
    ch3_lines = []
    baseline_count = noise_profile.get("baseline_types", 0)
    total_supp = noise_profile.get("total_suppression_decisions", 0)
    if total_supp > 0:
        ch3_lines.append(f"{total_supp:,} suppression decisions across {baseline_count} signal types. This is what the platform considers normal.")
        for n in noise_profile.get("top_noise", [])[:5]:
            ch3_lines.append(f"  {n['signal_type']}: {n.get('frequency', 0):,} occurrences (strength {n.get('strength', 0):.2f})")
        interp = noise_profile.get("interpretation", "")
        if interp:
            ch3_lines.append(interp)
    else:
        ch3_lines.append("No suppression baseline yet. The cascade hasn't processed enough signals to define normal.")
    chapters.append({"title": "What It Ignores", "text": "\n".join(ch3_lines)})

    # Chapter 4: What's missing
    ch4_lines = []
    if causal_gaps:
        ch4_lines.append(f"{len(causal_gaps)} causal gaps detected — effects observed without their expected upstream causes.")
        for g in causal_gaps[:5]:
            ch4_lines.append(f"  Expected {g.get('expected_cause', '?')} before {g.get('effect', '?')}")
            interp = g.get("interpretation", "")
            if interp:
                ch4_lines.append(f"    {interp}")
    if absences:
        ch4_lines.append(f"{len(absences)} expected signals are missing — monitoring blind spots.")
        for a in absences[:3]:
            overdue = a.get("hours_overdue")
            line = f"  {a['signal_type']}: expected every {a.get('expected_interval_hours', '?')}h"
            if overdue:
                line += f", overdue by {overdue:.1f}h"
            ch4_lines.append(line)
    if not causal_gaps and not absences:
        ch4_lines.append("No causal gaps or missing signals detected. Full observability coverage.")
    chapters.append({"title": "What's Missing", "text": "\n".join(ch4_lines)})

    # Chapter 5: What it analyzed (GPU)
    ch5_lines = []
    gpu_count = gpu_summary.get("count", 0)
    if gpu_count > 0:
        ch5_lines.append(f"{gpu_count} deep analyses produced by the GPU macro tier.")
        for a in gpu_summary.get("recent", [])[:3]:
            rc = a.get("root_cause", "")
            conf = a.get("confidence", 0)
            ch5_lines.append(f"  {a.get('signal_type', '?')} ({a.get('severity', '?')}): {rc[:100]} [confidence: {conf:.0%}]")
    else:
        ch5_lines.append("No GPU analyses yet. Critical signals have not reached the macro tier.")
    chapters.append({"title": "What It Analyzed", "text": "\n".join(ch5_lines)})

    return {"opening": opening, "chapters": chapters}


@app.get("/biography")
def biography():
    """Auto-generated structured biography from cascade memory and inverse data."""
    return generate_biography(_bridge, _memory_archive, _memory_intel)


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

if _FRONTEND.is_dir():
    @app.get("/")
    def dashboard():
        return FileResponse(_FRONTEND / "index.html")

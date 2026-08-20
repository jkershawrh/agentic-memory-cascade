"""Memory archive — survivors become institutional memory.

Signals that survive the cascade pipeline are stored as Memory objects
with lifecycle metadata: strength (decays over time, reinforced by recall),
consolidation count, and content-hash dedup.

Capacity-bounded, in-memory, with optional file persistence via to_dict/from_dict.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from .protocol import Signal

SEVERITY_WEIGHTS = {
    "info": 0.1,
    "low": 0.2,
    "medium": 0.4,
    "high": 0.7,
    "critical": 1.0,
}

_DEFAULT_MAX_CAPACITY = 10_000
_EVICTION_FRACTION = 0.1


@dataclass
class PrimingWindow:
    """Time-bounded attention window for a signal type."""
    signal_type: str
    duration_hours: float = 4.0
    opened_at: str = field(default="")

    def __post_init__(self):
        if not self.opened_at:
            self.opened_at = datetime.now(timezone.utc).isoformat()

    def effect(self, elapsed_hours: float) -> float:
        if self.duration_hours <= 0 or elapsed_hours >= self.duration_hours:
            return 0.0
        return max(0.0, 1.0 - elapsed_hours / self.duration_hours)

    def is_expired(self, elapsed_hours: float) -> bool:
        return elapsed_hours >= self.duration_hours


class PrimingEscalator:
    """Stage 0 cascade agent that escalates signals matching priming windows.

    Safety: can ONLY escalate, NEVER suppress or drop.
    """
    name = "priming_escalator"
    stage = 0

    def __init__(self, windows: Dict[str, "PrimingWindow"]):
        self._windows = windows

    def process(self, signals: List[Signal]) -> List:
        from .protocol import CascadeDecision, Outcome
        decisions = []
        for signal in signals:
            window = self._windows.get(signal.signal_type)
            if window is not None and not window.is_expired(elapsed_hours=0.0):
                decisions.append(CascadeDecision(
                    signal_id=signal.signal_id,
                    agent_name=self.name,
                    outcome=Outcome.ESCALATE,
                    confidence=window.effect(elapsed_hours=0.0),
                    evidence=f"priming window active for {signal.signal_type}",
                ))
        return decisions


@dataclass
class MemoryEvent:
    """Audit trail for memory lifecycle transitions."""
    memory_id: UUID
    event_type: str
    timestamp: str = field(default="")
    details: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": str(self.memory_id),
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "details": self.details,
        }


@dataclass
class Memory:
    """A survivor signal persisted as institutional memory."""
    memory_id: UUID
    signal: Signal
    formed_at: str
    strength: float
    recall_count: int = 0
    last_recalled_at: Optional[str] = None
    consolidation_count: int = 0
    source_instance: str = ""
    classification: str = ""
    content_hash: str = ""
    feature_vector: Dict[str, float] = field(default_factory=dict)
    last_modified_at: Optional[str] = None
    last_consolidated_at: Optional[str] = None
    analysis: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": str(self.memory_id),
            "signal_type": self.signal.signal_type,
            "severity": self.signal.severity,
            "formed_at": self.formed_at,
            "strength": self.strength,
            "recall_count": self.recall_count,
            "last_recalled_at": self.last_recalled_at,
            "consolidation_count": self.consolidation_count,
            "source_instance": self.source_instance,
            "classification": self.classification,
            "content_hash": self.content_hash,
            "content": self.signal.content,
            "labels": self.signal.labels,
            "feature_vector": self.feature_vector,
            "source": self.signal.source,
            "namespace": self.signal.namespace,
            "cluster": self.signal.cluster,
            "last_modified_at": self.last_modified_at,
            "last_consolidated_at": self.last_consolidated_at,
            "analysis": self.analysis,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> Memory:
        signal = Signal(
            signal_type=d.get("signal_type", ""),
            severity=d.get("severity", "info"),
            source=d.get("source", ""),
            namespace=d.get("namespace", ""),
            cluster=d.get("cluster", ""),
            content=d.get("content", {}),
            labels=d.get("labels", {}),
        )
        return Memory(
            memory_id=UUID(d["memory_id"]),
            signal=signal,
            formed_at=d.get("formed_at", ""),
            strength=d.get("strength", 0.0),
            recall_count=d.get("recall_count", 0),
            last_recalled_at=d.get("last_recalled_at"),
            consolidation_count=d.get("consolidation_count", 0),
            source_instance=d.get("source_instance", ""),
            classification=d.get("classification", ""),
            content_hash=d.get("content_hash", ""),
            feature_vector=d.get("feature_vector", {}),
            last_modified_at=d.get("last_modified_at"),
            last_consolidated_at=d.get("last_consolidated_at"),
            analysis=d.get("analysis"),
        )


import re

_UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)
_K8S_SUFFIX_RE = re.compile(r'(?<=\w)-[a-z0-9]{5,10}(?=\s|$)')
_TIMESTAMP_RE = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^\s]*')


def _normalize_value(value):
    """Strip dynamic identifiers from a string value."""
    if not isinstance(value, str):
        return value
    value = _UUID_RE.sub('<uuid>', value)
    value = _TIMESTAMP_RE.sub('<ts>', value)
    value = _K8S_SUFFIX_RE.sub('', value)
    return value


_PRESERVE_FIELDS = frozenset({
    "location", "span_id", "circuit_id", "node", "host",
    "region", "zone", "rack", "site", "sector",
    "patient_id", "account_id", "circuit", "prefix",
    "service", "instance",
})


def _normalize_content(content: dict) -> dict:
    """Normalize content dict for dedup — strip UUIDs, pod suffixes, timestamps.

    Location-bearing fields are preserved verbatim so that distinct-origin
    signals (e.g. fiber cuts at different locations) produce different hashes.
    """
    normalized = {}
    for key, value in content.items():
        if key in _PRESERVE_FIELDS:
            normalized[key] = value
        elif isinstance(value, dict):
            normalized[key] = _normalize_content(value)
        elif isinstance(value, str):
            normalized[key] = _normalize_value(value)
        else:
            normalized[key] = value
    return normalized


def _compute_content_hash(signal: Signal) -> str:
    normalized = json.dumps({
        "signal_type": signal.signal_type,
        "content": _normalize_content(signal.content),
    }, sort_keys=True)
    return hashlib.sha256(normalized.encode()).hexdigest()


def _extract_features(signal: Signal) -> Dict[str, float]:
    features = {}
    for key, value in signal.content.items():
        if isinstance(value, (int, float)):
            features[key] = float(value)
    return features


class MemoryArchive:
    """Capacity-bounded, in-memory survivor archive."""

    def __init__(self, max_capacity: int = _DEFAULT_MAX_CAPACITY,
                 instance_id: str = ""):
        self._memories: Dict[UUID, Memory] = {}
        self._hash_index: Dict[str, UUID] = {}
        self._max_capacity = max_capacity
        self._instance_id = instance_id or str(uuid4())[:8]
        self._events: List[MemoryEvent] = []
        self._formed_total = 0
        self._evictions_total = 0
        self._rejection_set: List[str] = []
        self._rejection_max = max_capacity * 2
        self._decay_config = None

    @property
    def size(self) -> int:
        return len(self._memories)

    def store(self, signal: Signal, classification: str = "",
              metadata: Optional[Dict[str, Any]] = None) -> Memory:
        content_hash = _compute_content_hash(signal)

        if content_hash in self._hash_index:
            existing_id = self._hash_index[content_hash]
            existing = self._memories.get(existing_id)
            if existing is not None:
                existing.strength += 0.1 * (1.0 - existing.strength)
                existing.last_modified_at = datetime.now(timezone.utc).isoformat()
                if metadata and "analysis" in metadata:
                    existing.analysis = metadata["analysis"]
                return existing

        if self.size >= self._max_capacity:
            self._evict_weakest()

        now = datetime.now(timezone.utc).isoformat()
        severity_weight = SEVERITY_WEIGHTS.get(signal.severity, 0.4)
        initial_strength = severity_weight

        memory = Memory(
            memory_id=uuid4(),
            signal=signal,
            formed_at=now,
            strength=initial_strength,
            source_instance=self._instance_id,
            classification=classification,
            content_hash=content_hash,
            feature_vector=_extract_features(signal),
            last_modified_at=now,
            analysis=metadata.get("analysis") if metadata else None,
        )

        self._memories[memory.memory_id] = memory
        self._hash_index[content_hash] = memory.memory_id
        self._formed_total += 1

        self._events.append(MemoryEvent(
            memory_id=memory.memory_id,
            event_type="formed",
            details={"initial_strength": initial_strength,
                     "classification": classification},
        ))

        return memory

    def get(self, memory_id: UUID) -> Optional[Memory]:
        return self._memories.get(memory_id)

    def query(self, signal_type: str = None, labels: Dict[str, str] = None,
              min_strength: float = 0.0, limit: int = 100) -> List[Memory]:
        results = list(self._memories.values())

        if signal_type is not None:
            results = [m for m in results if m.signal.signal_type == signal_type]

        if labels:
            results = [m for m in results
                       if all(m.signal.labels.get(k) == v for k, v in labels.items())]

        if min_strength > 0.0:
            results = [m for m in results if m.strength >= min_strength]

        results.sort(key=lambda m: m.strength, reverse=True)

        return results[:limit]

    def reinforce(self, memory_id: UUID) -> None:
        memory = self._memories.get(memory_id)
        if memory is not None:
            memory.strength += 0.1 * (1.0 - memory.strength)
            memory.strength = min(memory.strength, 1.0)
            memory.last_modified_at = datetime.now(timezone.utc).isoformat()

    def set_decay_config(self, config) -> None:
        self._decay_config = config

    def decay_all(self, lambda_rate: float, hours_elapsed: float) -> None:
        factor = math.exp(-lambda_rate * hours_elapsed)
        for memory in self._memories.values():
            memory.strength *= factor
            memory.strength = max(memory.strength, 0.0)

    def decay_all_typed(self, hours_elapsed: float) -> None:
        if not self._decay_config:
            return
        for memory in self._memories.values():
            rate = self._decay_config.rate_for(memory.signal.signal_type)
            memory.strength *= math.exp(-rate * hours_elapsed)
            memory.strength = max(memory.strength, 0.0)

    def _evict_weakest(self) -> None:
        count = max(1, int(self._max_capacity * _EVICTION_FRACTION))
        sorted_memories = sorted(self._memories.values(), key=lambda m: m.strength)
        to_evict = sorted_memories[:count]

        for memory in to_evict:
            self._events.append(MemoryEvent(
                memory_id=memory.memory_id,
                event_type="evicted",
                details={"final_strength": memory.strength,
                         "reason": "capacity"},
            ))
            self._add_to_rejection_set(memory.content_hash)
            self._hash_index.pop(memory.content_hash, None)
            del self._memories[memory.memory_id]
            self._evictions_total += 1

    def _add_to_rejection_set(self, content_hash: str) -> None:
        if content_hash and content_hash not in self._rejection_set:
            self._rejection_set.append(content_hash)
            if len(self._rejection_set) > self._rejection_max:
                self._rejection_set = self._rejection_set[-self._rejection_max:]

    def consolidate(self, pipeline_or_factory, strength_decay: float = 0.3,
                     strength_boost: float = 0.05,
                     eviction_threshold: float = 0.05,
                     decay_config=None,
                     batch_size: int = 0) -> Dict[str, Any]:
        """Re-cascade memories through the current pipeline.

        Memories the pipeline now suppresses lose strength.
        Memories that still survive gain consolidation_count and a small boost.
        Memories below eviction_threshold are removed.

        batch_size: when > 0, only process the N oldest unconsolidated memories
        plus any below eviction threshold. 0 = process all (backward compat).
        """
        from .protocol import Outcome

        all_memories = list(self._memories.values())
        if not all_memories:
            return {"processed": 0, "evicted": 0, "compression_ratio": 0.0}

        if batch_size > 0:
            urgent = [m for m in all_memories if m.strength < eviction_threshold * 2]
            rest = sorted(
                [m for m in all_memories if m.strength >= eviction_threshold * 2],
                key=lambda m: m.last_consolidated_at or "",
            )
            memories = urgent + rest[:max(0, batch_size - len(urgent))]
        else:
            memories = all_memories

        if not memories:
            return {"processed": 0, "evicted": 0, "compression_ratio": 0.0}

        if callable(pipeline_or_factory) and not hasattr(pipeline_or_factory, 'run'):
            pipeline = pipeline_or_factory()
        else:
            pipeline = pipeline_or_factory

        id_to_memory = {}
        signals = []
        for m in memories:
            fresh_id = uuid4()
            sig = Signal(
                signal_id=fresh_id,
                signal_type=m.signal.signal_type,
                severity=m.signal.severity,
                source=m.signal.source,
                namespace=m.signal.namespace,
                cluster=m.signal.cluster,
                content=dict(m.signal.content),
                labels=dict(m.signal.labels),
            )
            signals.append(sig)
            id_to_memory[fresh_id] = m

        result = pipeline.run(signals)

        survived_ids = {s.signal_id for s in result.remaining}
        suppressed_ids = set()
        for d in result.decisions:
            if d.outcome in (Outcome.SUPPRESS, Outcome.DEDUPE, Outcome.DROP):
                suppressed_ids.add(d.signal_id)

        effective_config = decay_config or self._decay_config
        now = datetime.now(timezone.utc).isoformat()
        evicted_count = 0
        for fresh_id, memory in id_to_memory.items():
            memory.last_consolidated_at = now
            if fresh_id in survived_ids:
                memory.consolidation_count += 1
                memory.strength = min(memory.strength + strength_boost, 1.0)
                memory.last_modified_at = now
            elif fresh_id in suppressed_ids:
                if effective_config:
                    rate = effective_config.rate_for(memory.signal.signal_type)
                    penalty = min(rate * 10, 0.5)
                else:
                    penalty = strength_decay
                memory.strength -= penalty
                memory.strength = max(memory.strength, 0.0)
                memory.last_modified_at = now

        to_evict = [m for m in self._memories.values()
                    if m.strength < eviction_threshold]
        for memory in to_evict:
            self._events.append(MemoryEvent(
                memory_id=memory.memory_id,
                event_type="evicted",
                details={"final_strength": memory.strength,
                         "reason": "consolidation"},
            ))
            self._add_to_rejection_set(memory.content_hash)
            self._hash_index.pop(memory.content_hash, None)
            del self._memories[memory.memory_id]
            self._evictions_total += 1
            evicted_count += 1

        all_fresh_ids = set(id_to_memory.keys())
        suppressed_count = len(suppressed_ids & all_fresh_ids)
        compression = suppressed_count / len(memories) if memories else 0.0

        return {
            "processed": len(memories),
            "evicted": evicted_count,
            "survived": len(survived_ids & {m.signal.signal_id for m in memories}),
            "suppressed": suppressed_count,
            "compression_ratio": round(compression, 3),
        }

    def export_memories(self, min_strength: float = 0.0,
                        since: Optional[str] = None) -> Dict[str, Any]:
        memories = self.query(min_strength=min_strength, limit=self._max_capacity)
        if since:
            memories = [m for m in memories
                        if (m.last_modified_at or m.formed_at) >= since]
        return {
            "instance_id": self._instance_id,
            "memories": [m.to_dict() for m in memories],
        }

    def import_memories(self, data: Dict[str, Any]) -> int:
        source_instance = data.get("instance_id", "unknown")
        imported = 0
        rejected = 0
        for entry in data.get("memories", []):
            content_hash = entry.get("content_hash", "")

            if content_hash and content_hash in self._rejection_set:
                rejected += 1
                continue

            if content_hash and content_hash in self._hash_index:
                existing = self._memories.get(self._hash_index[content_hash])
                if existing is not None:
                    existing.strength += 0.1 * (1.0 - existing.strength)
                    existing.strength = min(existing.strength, 1.0)
                    existing.last_modified_at = datetime.now(timezone.utc).isoformat()
                    continue

            signal = Signal(
                signal_type=entry.get("signal_type", ""),
                severity=entry.get("severity", "info"),
                source=entry.get("source", ""),
                namespace=entry.get("namespace", ""),
                cluster=entry.get("cluster", ""),
                content=entry.get("content", {}),
                labels=entry.get("labels", {}),
            )

            if self.size >= self._max_capacity:
                self._evict_weakest()

            memory = Memory(
                memory_id=uuid4(),
                signal=signal,
                formed_at=entry.get("formed_at", datetime.now(timezone.utc).isoformat()),
                strength=entry.get("strength", 0.5),
                recall_count=entry.get("recall_count", 0),
                last_recalled_at=entry.get("last_recalled_at"),
                consolidation_count=entry.get("consolidation_count", 0),
                source_instance=source_instance,
                classification=entry.get("classification", ""),
                content_hash=content_hash or _compute_content_hash(signal),
                feature_vector=entry.get("feature_vector", {}),
            )

            self._memories[memory.memory_id] = memory
            if memory.content_hash:
                self._hash_index[memory.content_hash] = memory.memory_id
            self._formed_total += 1
            imported += 1

            self._events.append(MemoryEvent(
                memory_id=memory.memory_id,
                event_type="federated",
                details={"source_instance": source_instance},
            ))

        return imported

    def add_priming_window(self, window: PrimingWindow,
                           max_windows: int = 10) -> None:
        if not hasattr(self, '_priming_windows'):
            self._priming_windows: Dict[str, PrimingWindow] = {}
        if len(self._priming_windows) >= max_windows:
            oldest_key = next(iter(self._priming_windows))
            del self._priming_windows[oldest_key]
        self._priming_windows[window.signal_type] = window

    def get_priming_windows(self) -> Dict[str, PrimingWindow]:
        if not hasattr(self, '_priming_windows'):
            self._priming_windows: Dict[str, PrimingWindow] = {}
        return dict(self._priming_windows)

    def drain_events(self) -> List[MemoryEvent]:
        events = list(self._events)
        self._events.clear()
        return events

    def stats(self) -> Dict[str, Any]:
        strengths = [m.strength for m in self._memories.values()]
        sources = {m.source_instance for m in self._memories.values() if m.source_instance}
        return {
            "size": self.size,
            "max_capacity": self._max_capacity,
            "formed_total": self._formed_total,
            "evictions_total": self._evictions_total,
            "instance_id": self._instance_id,
            "avg_strength": sum(strengths) / len(strengths) if strengths else 0.0,
            "min_strength": min(strengths) if strengths else 0.0,
            "max_strength": max(strengths) if strengths else 0.0,
            "federated_sources": len(sources),
            "rejection_set_size": len(self._rejection_set),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memories": [m.to_dict() for m in self._memories.values()],
            "stats": {
                "formed_total": self._formed_total,
                "evictions_total": self._evictions_total,
                "instance_id": self._instance_id,
                "max_capacity": self._max_capacity,
            },
            "rejection_set": list(self._rejection_set),
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> MemoryArchive:
        stats = data.get("stats", {})
        archive = MemoryArchive(
            max_capacity=stats.get("max_capacity", _DEFAULT_MAX_CAPACITY),
            instance_id=stats.get("instance_id", ""),
        )
        archive._formed_total = stats.get("formed_total", 0)
        archive._evictions_total = stats.get("evictions_total", 0)
        archive._rejection_set = list(data.get("rejection_set", []))

        for entry in data.get("memories", []):
            memory = Memory.from_dict(entry)
            archive._memories[memory.memory_id] = memory
            archive._hash_index[memory.content_hash] = memory.memory_id

        return archive

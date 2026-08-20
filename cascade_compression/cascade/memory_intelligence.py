"""Memory intelligence framework — composable analysis layer for memory archives.

Domain-agnostic primitives: entity resolution, time clustering, causal chains,
absence detection, severity tracking, per-type decay. Domain packs provide
extractors, rules, and overrides via register_domain().
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .memory import Memory, MemoryArchive
from .protocol import Signal

SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


class EntityResolver:
    """Extracts entity keys from signals for grouping related memories."""

    def __init__(self, extractor: Optional[Callable[[Signal], Optional[str]]] = None):
        self._extractor = extractor or self._default_extractor

    @staticmethod
    def _default_extractor(sig: Signal) -> Optional[str]:
        parts = [p for p in [sig.cluster, sig.namespace, sig.source] if p]
        return ":".join(parts) if parts else None

    def resolve(self, sig: Signal) -> Optional[str]:
        return self._extractor(sig)

    def group_by_entity(self, memories: List[Memory]) -> Dict[str, List[Memory]]:
        groups: Dict[str, List[Memory]] = defaultdict(list)
        for m in memories:
            key = self.resolve(m.signal)
            if key:
                groups[key].append(m)
        return dict(groups)


@dataclass
class TimeCluster:
    """A group of memories that formed within the same time window."""
    memories: List[Memory] = field(default_factory=list)
    start: Optional[str] = None
    end: Optional[str] = None
    signal_types: Set[str] = field(default_factory=set)


class TimeClusterEngine:
    """Clusters memories by formation time within a configurable window."""

    def __init__(self, window_seconds: int = 60):
        self._window = window_seconds

    def cluster(self, memories: List[Memory]) -> List[TimeCluster]:
        if not memories:
            return []

        def parse_ts(ts: str) -> float:
            try:
                dt = datetime.fromisoformat(ts)
                return dt.timestamp()
            except (ValueError, TypeError):
                return 0.0

        sorted_mems = sorted(memories, key=lambda m: parse_ts(m.formed_at))
        clusters: List[TimeCluster] = []
        current = TimeCluster(
            memories=[sorted_mems[0]],
            start=sorted_mems[0].formed_at,
            end=sorted_mems[0].formed_at,
            signal_types={sorted_mems[0].signal.signal_type},
        )

        for m in sorted_mems[1:]:
            t_current = parse_ts(current.end)
            t_new = parse_ts(m.formed_at)

            if t_new - t_current <= self._window:
                current.memories.append(m)
                current.end = m.formed_at
                current.signal_types.add(m.signal.signal_type)
            else:
                clusters.append(current)
                current = TimeCluster(
                    memories=[m],
                    start=m.formed_at,
                    end=m.formed_at,
                    signal_types={m.signal.signal_type},
                )

        clusters.append(current)
        return clusters


class CausalGraph:
    """Directed graph of cause → effect relationships between signal types."""

    def __init__(self):
        self._forward: Dict[str, Set[str]] = defaultdict(set)
        self._reverse: Dict[str, Set[str]] = defaultdict(set)

    def add_rule(self, cause: str, effect: str) -> None:
        self._forward[cause].add(effect)
        self._reverse[effect].add(cause)

    def has_rule(self, cause: str, effect: str) -> bool:
        return effect in self._forward.get(cause, set())

    def causes_of(self, signal_type: str) -> List[str]:
        return list(self._reverse.get(signal_type, set()))

    def effects_of(self, signal_type: str) -> List[str]:
        return list(self._forward.get(signal_type, set()))

    def chain_from(self, signal_type: str) -> List[str]:
        visited: Set[str] = set()
        result: List[str] = []
        queue = list(self._forward.get(signal_type, set()))
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            result.append(current)
            queue.extend(self._forward.get(current, set()))
        return result

    def find_links_in(self, memories: List[Memory]) -> List[Dict[str, Any]]:
        types_present = {m.signal.signal_type for m in memories}
        links = []
        for m in memories:
            for effect in self._forward.get(m.signal.signal_type, set()):
                if effect in types_present:
                    links.append({
                        "cause": m.signal.signal_type,
                        "effect": effect,
                        "cause_memory_id": str(m.memory_id),
                    })
        return links


class AbsenceDetector:
    """Detects missing expected signals based on registered intervals."""

    def __init__(self):
        self.expectations: Dict[str, float] = {}
        self._last_seen: Dict[str, str] = {}

    def expect(self, signal_type: str, interval_hours: float) -> None:
        self.expectations[signal_type] = interval_hours

    def record(self, signal_type: str, timestamp: str) -> None:
        self._last_seen[signal_type] = timestamp

    def check_missing(self, now: str) -> List[Dict[str, Any]]:
        try:
            now_dt = datetime.fromisoformat(now)
        except (ValueError, TypeError):
            return []

        missing = []
        for signal_type, interval_hours in self.expectations.items():
            last = self._last_seen.get(signal_type)
            if last is None:
                missing.append({
                    "signal_type": signal_type,
                    "expected_interval_hours": interval_hours,
                    "last_seen": None,
                    "hours_overdue": None,
                })
                continue
            try:
                last_dt = datetime.fromisoformat(last)
                elapsed_hours = (now_dt - last_dt).total_seconds() / 3600
                if elapsed_hours > interval_hours:
                    missing.append({
                        "signal_type": signal_type,
                        "expected_interval_hours": interval_hours,
                        "last_seen": last,
                        "hours_overdue": round(elapsed_hours - interval_hours, 2),
                    })
            except (ValueError, TypeError):
                continue
        return missing


class SeverityTracker:
    """Tracks severity progression over time for signal types."""

    def __init__(self):
        self._history: Dict[str, List[Tuple[str, str]]] = defaultdict(list)

    def record(self, signal_type: str, severity: str, timestamp: str) -> None:
        self._history[signal_type].append((timestamp, severity))

    def trend(self, signal_type: str) -> str:
        history = self._history.get(signal_type)
        if not history:
            return "unknown"

        if len(history) < 2:
            return "stable"

        values = [SEVERITY_ORDER.get(sev, 2) for _, sev in history]
        diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]
        avg_diff = sum(diffs) / len(diffs)

        if avg_diff > 0.3:
            return "escalating"
        if avg_diff < -0.3:
            return "deescalating"
        return "stable"


class DecayConfig:
    """Per-type memory decay rates."""

    def __init__(self, default_rate: float = 0.01):
        self._default = default_rate
        self._overrides: Dict[str, float] = {}

    def set_rate(self, signal_type: str, rate: float) -> None:
        self._overrides[signal_type] = rate

    def rate_for(self, signal_type: str) -> float:
        return self._overrides.get(signal_type, self._default)


class CoOccurrenceTracker:
    """Counts how often signal type pairs appear in the same time cluster."""

    def __init__(self):
        self._pair_counts: Dict[Tuple[str, str], int] = defaultdict(int)
        self._type_counts: Dict[str, int] = defaultdict(int)
        self._temporal_order: Dict[Tuple[str, str], int] = defaultdict(int)

    def update_from_clusters(self, clusters: List[TimeCluster]) -> None:
        for cluster in clusters:
            types = sorted(cluster.signal_types)
            for t in types:
                self._type_counts[t] += 1
            for i, a in enumerate(types):
                for b in types[i + 1:]:
                    self._pair_counts[(a, b)] += 1
                    # Track temporal order from cluster memories
                    a_times = [m.formed_at for m in cluster.memories
                               if m.signal.signal_type == a]
                    b_times = [m.formed_at for m in cluster.memories
                               if m.signal.signal_type == b]
                    if a_times and b_times:
                        if min(a_times) <= min(b_times):
                            self._temporal_order[(a, b)] += 1
                        else:
                            self._temporal_order[(b, a)] += 1

    def propose_rules(self, min_count: int = 5,
                      min_support: float = 0.3,
                      existing_graph: Optional[CausalGraph] = None,
                      ) -> List[Dict[str, Any]]:
        proposals = []
        for (a, b), count in self._pair_counts.items():
            if count < min_count:
                continue
            support = count / min(self._type_counts.get(a, 1),
                                  self._type_counts.get(b, 1))
            if support < min_support:
                continue
            if existing_graph and existing_graph.has_rule(a, b):
                continue
            if existing_graph and existing_graph.has_rule(b, a):
                continue
            # Direction: whichever appears first temporally more often is the cause
            ab_order = self._temporal_order.get((a, b), 0)
            ba_order = self._temporal_order.get((b, a), 0)
            if ab_order >= ba_order:
                cause, effect = a, b
            else:
                cause, effect = b, a

            proposals.append({
                "cause": cause,
                "effect": effect,
                "co_occurrence_count": count,
                "support": round(support, 3),
                "confidence": round(max(ab_order, ba_order) /
                                    max(1, ab_order + ba_order), 3),
            })

        return sorted(proposals, key=lambda p: p["co_occurrence_count"],
                       reverse=True)

    @property
    def pair_count(self) -> int:
        return len(self._pair_counts)


class MemoryIntelligence:
    """Composable analysis layer for memory archives.

    Combine entity resolution, time clustering, causal chains, absence
    detection, severity tracking, and per-type decay. Domain packs
    provide configuration via register_domain().
    """

    def __init__(self, auto_discover: bool = False):
        self.entity_resolver = EntityResolver()
        self.time_cluster_engine = TimeClusterEngine(window_seconds=120)
        self.causal_graph = CausalGraph()
        self.absence_detector = AbsenceDetector()
        self.severity_tracker = SeverityTracker()
        self.decay_config = DecayConfig()
        self.co_occurrence = CoOccurrenceTracker()
        self.auto_discover = auto_discover
        self._entity_mappings: List[Tuple[Callable, Callable]] = []
        self._domains: Dict[str, dict] = {}

    def register_domain(self, name: str, config: dict) -> None:
        self._domains[name] = config

        extractor = config.get("entity_extractor")
        if extractor:
            self.entity_resolver = EntityResolver(extractor=extractor)

        for cause, effect in config.get("causal_rules", []):
            self.causal_graph.add_rule(cause, effect)

        for signal_type, rate in config.get("decay_overrides", {}).items():
            self.decay_config.set_rate(signal_type, rate)

        for signal_type, interval in config.get("expected_signals", []):
            self.absence_detector.expect(signal_type, interval)

    def add_entity_mapping(self, extractor_a: Callable, extractor_b: Callable) -> None:
        self._entity_mappings.append((extractor_a, extractor_b))

    def find_cross_domain_links(self, memories: List[Memory]) -> List[Dict[str, Any]]:
        links = []
        for ext_a, ext_b in self._entity_mappings:
            index_a: Dict[str, List[Memory]] = defaultdict(list)
            index_b: Dict[str, List[Memory]] = defaultdict(list)

            for m in memories:
                key_a = ext_a(m.signal)
                key_b = ext_b(m.signal)
                if key_a:
                    index_a[key_a].append(m)
                if key_b:
                    index_b[key_b].append(m)

            for key in set(index_a) & set(index_b):
                links.append({
                    "entity": key,
                    "domain_a_memories": [str(m.memory_id) for m in index_a[key]],
                    "domain_b_memories": [str(m.memory_id) for m in index_b[key]],
                    "signal_types": list({m.signal.signal_type
                                          for m in index_a[key] + index_b[key]}),
                })
        return links

    def analyze(self, archive: MemoryArchive) -> Dict[str, Any]:
        memories = archive.query(limit=archive.size or 1)
        entities = self.entity_resolver.group_by_entity(memories)
        clusters = self.time_cluster_engine.cluster(memories)
        causal_links = self.causal_graph.find_links_in(memories)

        self.co_occurrence.update_from_clusters(clusters)
        proposed_rules = self.co_occurrence.propose_rules(
            existing_graph=self.causal_graph)

        if self.auto_discover:
            for rule in proposed_rules:
                if rule["co_occurrence_count"] >= 10 and rule["support"] >= 0.5:
                    self.causal_graph.add_rule(rule["cause"], rule["effect"])

        return {
            "total_memories": len(memories),
            "entities": {
                "count": len(entities),
                "top": sorted(entities.items(), key=lambda kv: len(kv[1]),
                               reverse=True)[:10],
            },
            "clusters": {
                "count": len(clusters),
                "multi_type": sum(1 for c in clusters if len(c.signal_types) > 1),
                "largest": max((len(c.memories) for c in clusters), default=0),
            },
            "causal_links": causal_links,
            "proposed_rules": proposed_rules[:10],
            "co_occurrence_pairs": self.co_occurrence.pair_count,
        }

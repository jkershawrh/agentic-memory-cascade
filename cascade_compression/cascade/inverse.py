"""Inverse cascade — the other side of every decision.

Six inversions built on the same framework primitives:
1. Suppression archive — what the cascade decided was noise
2. Absence detection — what should have appeared but didn't
3. Backward causal inference — what should have preceded an incident
4. Synthetic baseline — what healthy looks like
5. Agent knowledge export — transferable learned patterns
6. Self-monitoring — the cascade's own learning as signals

All reuse MemoryArchive, CausalGraph, AbsenceDetector, Baseline.
Zero new abstractions.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID, uuid4

from .memory import Memory, MemoryArchive, MemoryEvent, _compute_content_hash
from .memory_intelligence import (
    AbsenceDetector,
    CausalGraph,
    MemoryIntelligence,
)
from .protocol import CascadeDecision, Outcome, Signal

log = logging.getLogger(__name__)

SUPPRESSION_OUTCOMES = {Outcome.SUPPRESS, Outcome.DEDUPE, Outcome.DROP}


# ── Inversion 1: Suppression Archive ───────────────────────────────

class SuppressionArchive:
    """Parallel to MemoryArchive — stores suppression decisions.

    Strength is inverted: frequently suppressed patterns are STRONG
    (high confidence it's noise). Rarely suppressed patterns are WEAK
    (uncertain). This is the learned definition of "normal."
    """

    def __init__(self, max_capacity: int = 10_000):
        self._patterns: Dict[str, SuppressionPattern] = {}
        self._max_capacity = max_capacity
        self._total_decisions = 0

    def record(self, decision: CascadeDecision, signal: Signal) -> None:
        if decision.outcome not in SUPPRESSION_OUTCOMES:
            return

        key = f"{signal.signal_type}:{decision.agent_name}"
        self._total_decisions += 1

        if key in self._patterns:
            p = self._patterns[key]
            p.count += 1
            p.last_seen = datetime.now(timezone.utc).isoformat()
            p.strength = min(1.0, p.count / 100.0)
        else:
            if len(self._patterns) >= self._max_capacity:
                self._evict_weakest()
            self._patterns[key] = SuppressionPattern(
                signal_type=signal.signal_type,
                agent_name=decision.agent_name,
                outcome=decision.outcome.value,
                count=1,
                first_seen=datetime.now(timezone.utc).isoformat(),
                last_seen=datetime.now(timezone.utc).isoformat(),
                strength=0.01,
                evidence_sample=decision.evidence[:200],
            )

    def record_batch(self, decisions: List[CascadeDecision],
                     signals: List[Signal]) -> int:
        sig_map = {s.signal_id: s for s in signals}
        recorded = 0
        for d in decisions:
            sig = sig_map.get(d.signal_id)
            if sig:
                self.record(d, sig)
                recorded += 1
        return recorded

    def _evict_weakest(self) -> None:
        if not self._patterns:
            return
        weakest = min(self._patterns, key=lambda k: self._patterns[k].strength)
        del self._patterns[weakest]

    @property
    def size(self) -> int:
        return len(self._patterns)

    def top_patterns(self, limit: int = 20) -> List["SuppressionPattern"]:
        return sorted(self._patterns.values(),
                      key=lambda p: p.count, reverse=True)[:limit]

    def frequency_distribution(self) -> Dict[str, int]:
        dist: Dict[str, int] = defaultdict(int)
        for p in self._patterns.values():
            dist[p.signal_type] += p.count
        return dict(dist)

    def stats(self) -> Dict[str, Any]:
        return {
            "size": self.size,
            "total_decisions": self._total_decisions,
            "top_suppressed": [
                {"signal_type": p.signal_type, "agent": p.agent_name,
                 "count": p.count, "strength": round(p.strength, 3)}
                for p in self.top_patterns(10)
            ],
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "patterns": [
                {
                    "signal_type": p.signal_type,
                    "agent_name": p.agent_name,
                    "outcome": p.outcome,
                    "count": p.count,
                    "first_seen": p.first_seen,
                    "last_seen": p.last_seen,
                    "strength": p.strength,
                    "evidence_sample": p.evidence_sample,
                }
                for p in self._patterns.values()
            ],
            "total_decisions": self._total_decisions,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "SuppressionArchive":
        archive = SuppressionArchive()
        archive._total_decisions = data.get("total_decisions", 0)
        for entry in data.get("patterns", []):
            key = f"{entry['signal_type']}:{entry['agent_name']}"
            archive._patterns[key] = SuppressionPattern(
                signal_type=entry["signal_type"],
                agent_name=entry["agent_name"],
                outcome=entry.get("outcome", "suppress"),
                count=entry.get("count", 1),
                first_seen=entry.get("first_seen", ""),
                last_seen=entry.get("last_seen", ""),
                strength=entry.get("strength", 0.01),
                evidence_sample=entry.get("evidence_sample", ""),
            )
        return archive


@dataclass
class SuppressionPattern:
    signal_type: str
    agent_name: str
    outcome: str
    count: int = 0
    first_seen: str = ""
    last_seen: str = ""
    strength: float = 0.0
    evidence_sample: str = ""


# ── Inversion 2: Absence Detection from Learned Baseline ──────────

def learn_expectations(suppression_archive: SuppressionArchive,
                       min_count: int = 10,
                       window_hours: float = 1.0) -> List[Tuple[str, float]]:
    """Derive expected signal patterns from suppression frequency.

    If the cascade has been suppressing `heartbeat` 100 times per hour,
    we expect to see it every 36 seconds. If it stops, that's an absence.

    Returns: list of (signal_type, expected_interval_hours).
    """
    expectations = []
    freq = suppression_archive.frequency_distribution()
    total_decisions = suppression_archive._total_decisions

    if total_decisions == 0:
        return expectations

    for signal_type, count in freq.items():
        if count < min_count:
            continue
        rate_per_decision = count / total_decisions
        if rate_per_decision > 0.01:
            interval = max(0.1, window_hours / (count / max(1, total_decisions / 100)))
            expectations.append((signal_type, round(interval, 2)))

    return expectations


def wire_absence_detector(detector: AbsenceDetector,
                          suppression_archive: SuppressionArchive,
                          min_count: int = 10) -> int:
    """Auto-register expectations from suppression patterns."""
    expectations = learn_expectations(suppression_archive, min_count)
    for signal_type, interval in expectations:
        detector.expect(signal_type, interval)
    return len(expectations)


# ── Inversion 3: Backward Causal Inference ─────────────────────────

def missing_causes(memory_archive: MemoryArchive,
                   causal_graph: CausalGraph,
                   signal_type: str) -> List[Dict[str, Any]]:
    """Given an effect, find causes that should exist but don't.

    Walks the causal graph backward from the given signal_type.
    For each expected cause, checks if it exists in the memory archive.
    Returns missing causes.
    """
    missing = []
    causes = causal_graph.causes_of(signal_type)
    memory_types = {m.signal.signal_type for m in memory_archive.query(limit=9999)}

    for cause in causes:
        if cause not in memory_types:
            missing.append({
                "expected_cause": cause,
                "effect": signal_type,
                "interpretation": f"'{signal_type}' occurred but expected upstream cause '{cause}' was not observed",
            })

    return missing


def find_all_gaps(memory_archive: MemoryArchive,
                  causal_graph: CausalGraph) -> List[Dict[str, Any]]:
    """Find all causal gaps across the entire memory archive."""
    gaps = []
    seen_types = {m.signal.signal_type for m in memory_archive.query(limit=9999)}
    for signal_type in seen_types:
        for gap in missing_causes(memory_archive, causal_graph, signal_type):
            gaps.append(gap)
    return gaps


# ── Inversion 4: Synthetic Baseline ────────────────────────────────

@dataclass
class BaselineSnapshot:
    """What 'normal' looks like, derived from suppression patterns."""
    signal_types: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    generated_at: str = ""

    def compare(self, current_signals: List[Signal]) -> Dict[str, Any]:
        """Compare current signals against the baseline.

        Returns signals that are anomalous: present but not in baseline,
        or absent from current but expected by baseline.
        """
        current_types = defaultdict(int)
        for s in current_signals:
            current_types[s.signal_type] += 1

        unexpected = []
        for sig_type, count in current_types.items():
            if sig_type not in self.signal_types:
                unexpected.append({
                    "signal_type": sig_type,
                    "count": count,
                    "reason": "not in baseline — novel signal type",
                })

        missing_from_current = []
        for sig_type, info in self.signal_types.items():
            if sig_type not in current_types and info.get("frequency", 0) > 10:
                missing_from_current.append({
                    "signal_type": sig_type,
                    "expected_frequency": info.get("frequency", 0),
                    "reason": "expected but absent from current batch",
                })

        return {
            "unexpected": unexpected,
            "missing": missing_from_current,
            "baseline_types": len(self.signal_types),
            "current_types": len(current_types),
        }


def generate_baseline(suppression_archive: SuppressionArchive) -> BaselineSnapshot:
    """Generate a baseline from suppression patterns.

    The suppressed signals ARE the definition of normal.
    """
    baseline = BaselineSnapshot(
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    for pattern in suppression_archive._patterns.values():
        if pattern.signal_type not in baseline.signal_types:
            baseline.signal_types[pattern.signal_type] = {
                "frequency": pattern.count,
                "agents": [pattern.agent_name],
                "strength": pattern.strength,
                "first_seen": pattern.first_seen,
            }
        else:
            entry = baseline.signal_types[pattern.signal_type]
            entry["frequency"] += pattern.count
            entry["agents"].append(pattern.agent_name)
            entry["strength"] = max(entry["strength"], pattern.strength)

    return baseline


# ── Inversion 5: Agent Knowledge Export ────────────────────────────

def export_learned_agents(bridge) -> Dict[str, Any]:
    """Export all discovered agents as transferable knowledge.

    The agents ARE the cascade's learned understanding of normalcy.
    Another instance can import them to bootstrap its knowledge.
    """
    agents = []
    for name, metrics in bridge._agent_metrics.items():
        rule = bridge._agent_rules.get(name)
        agents.append({
            "name": name,
            "tier": metrics.tier,
            "accuracy": metrics.accuracy,
            "samples_tested": metrics.samples_tested,
            "false_positive_rate": metrics.false_positive_rate,
            "false_negative_rate": metrics.false_negative_rate,
            "rubric_status": metrics.rubric_status,
            "config": metrics.config,
            "rule": {
                "name": rule.name,
                "signal_types": rule.signal_types,
                "condition": rule.condition,
                "classification": rule.classification,
            } if rule else None,
            "deactivated": metrics.deactivated,
        })

    return {
        "instance_id": bridge.memory_archive._instance_id if bridge.memory_archive else "",
        "domain": bridge.domain,
        "agent_count": len(agents),
        "activated_count": len(bridge._activated_types),
        "agents": agents,
        "activated_types": list(bridge._activated_types),
        "known_patterns": list(bridge.corpus_analyzer._known_patterns),
    }


# ── Inversion 6: Self-Monitoring Collector ─────────────────────────

class CascadeMetaCollector:
    """Collects the cascade's own operational events as signals.

    Promotions, demotions, shadow disagreements, TTL expirations —
    the cascade's learning process becomes data for a meta-cascade.
    """

    def __init__(self, bridge):
        self._bridge = bridge
        self._last_log_index = 0

    def collect(self) -> List[Signal]:
        signals = []
        log_entries = self._bridge.get_promotion_log(100)

        for entry in log_entries[self._last_log_index:]:
            event_type = entry.get("event_type", "unknown")
            agent_name = entry.get("agent_name", "unknown")
            from_tier = entry.get("from_tier", "")
            to_tier = entry.get("to_tier", "")

            severity = self._map_severity(event_type, from_tier, to_tier)
            signal_type = f"cascade_{event_type}"

            signals.append(Signal(
                signal_type=signal_type,
                severity=severity,
                source=agent_name,
                namespace="cascade-meta",
                content={
                    "message": f"Agent '{agent_name}' {event_type}: {from_tier} → {to_tier}",
                    "agent_name": agent_name,
                    "event_type": event_type,
                    "from_tier": from_tier,
                    "to_tier": to_tier,
                    "accuracy": entry.get("accuracy", 0),
                    "false_negative_rate": entry.get("false_negative_rate", 0),
                    "reason": entry.get("reason", ""),
                },
                labels={"domain": "cascade-meta"},
            ))

        self._last_log_index = len(log_entries)
        return signals

    @staticmethod
    def _map_severity(event_type: str, from_tier: str, to_tier: str) -> str:
        if event_type == "demotion":
            if from_tier in ("nano", "micro", "macro"):
                return "high"
            return "medium"
        if event_type == "promotion":
            if to_tier in ("nano", "micro", "macro"):
                return "medium"
            return "info"
        return "info"


# ── Unified Inverse Analysis ───────────────────────────────────────

def inverse_analysis(suppression_archive: SuppressionArchive,
                     memory_archive: MemoryArchive,
                     intelligence: Optional[MemoryIntelligence] = None,
                     ) -> Dict[str, Any]:
    """Run all inversions and return a unified analysis."""

    baseline = generate_baseline(suppression_archive)
    expectations = learn_expectations(suppression_archive)

    causal_gaps = []
    if intelligence:
        causal_gaps = find_all_gaps(memory_archive, intelligence.causal_graph)

    suppression_stats = suppression_archive.stats()

    return {
        "suppression": suppression_stats,
        "baseline": {
            "normal_signal_types": len(baseline.signal_types),
            "top_normal": sorted(
                [{"type": k, **v} for k, v in baseline.signal_types.items()],
                key=lambda x: x.get("frequency", 0),
                reverse=True,
            )[:10],
        },
        "absence": {
            "learned_expectations": len(expectations),
            "expected_signals": [
                {"signal_type": st, "interval_hours": ih}
                for st, ih in expectations[:10]
            ],
        },
        "causal_gaps": causal_gaps[:10],
        "memory_vs_suppression": {
            "memories": memory_archive.size,
            "suppression_patterns": suppression_archive.size,
            "ratio": round(memory_archive.size / max(1, suppression_archive.size), 3),
            "interpretation": (
                "The system suppresses far more than it remembers"
                if suppression_archive.size > memory_archive.size * 5
                else "Memory and suppression are balanced"
            ),
        },
    }

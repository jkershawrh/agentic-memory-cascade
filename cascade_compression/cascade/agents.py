"""Built-in cascade agents — generic patterns that work across domains.

Domain-specific agents (K8s pod health, Kafka lag, etc.) live in domain packs.
These agents handle universal signal patterns.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import defaultdict
from typing import Dict, List, Set

from .protocol import CascadeAgent, CascadeDecision, Outcome, Signal


class DeduplicateAgent:
    """Drops duplicate signals within a time window.

    Compliance-labeled signals bypass dedup — each instance is individually
    reportable regardless of content similarity. Location-bearing fields
    (location, span_id, circuit_id, node, host) are included in the hash
    so that distinct-origin signals are not collapsed.
    """

    name = "deduplicate"
    stage = 1

    DEDUP_BYPASS_LABELS = frozenset({"compliance", "fraud", "sanctions"})
    LOCATION_FIELDS = ("location", "span_id", "circuit_id", "node", "host",
                       "region", "zone", "rack", "site", "service", "instance")

    def __init__(self, window_seconds: float = 60.0):
        self._window = window_seconds
        self._seen: Dict[str, float] = {}

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        now = time.monotonic()
        self._seen = {k: v for k, v in self._seen.items() if now - v < self._window}

        decisions = []
        for s in signals:
            if self._is_compliance(s):
                continue

            content_key = s.content.get("message", "") if s.content else ""
            location_key = self._location_key(s)
            key = hashlib.sha256(
                f"{s.signal_type}:{s.source}:{s.namespace}:{s.severity}:{content_key}:{location_key}".encode()
            ).hexdigest()
            if key in self._seen:
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.DEDUPE, evidence=f"duplicate of {key[:8]}",
                ))
            else:
                self._seen[key] = now
        return decisions

    def _is_compliance(self, s: Signal) -> bool:
        label = s.labels.get("label", "") if s.labels else ""
        return label in self.DEDUP_BYPASS_LABELS

    def _location_key(self, s: Signal) -> str:
        parts = []
        if s.content:
            for field in self.LOCATION_FIELDS:
                val = s.content.get(field)
                if val:
                    parts.append(str(val))
        return "|".join(parts)


class TransientSuppressor:
    """Suppresses transient signals that resolve within a window.

    Fail-open: signals with critical keywords in content are never suppressed,
    even if the signal type and severity would normally be transient.
    """

    name = "transient_suppressor"
    stage = 1

    TRANSIENT_TYPES = {"pod_restart", "container_backoff", "probe_failure", "dns_timeout"}
    NEVER_SUPPRESS = re.compile(
        r"(fatal|critical|oomkill|segfault|panic|breach|unauthorized|data.?loss|corruption)",
        re.I,
    )

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        decisions = []
        for s in signals:
            if s.signal_type in self.TRANSIENT_TYPES and s.severity in ("info", "low"):
                content_str = str(s.content) if s.content else ""
                if self.NEVER_SUPPRESS.search(content_str):
                    continue
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.SUPPRESS, evidence=f"transient {s.signal_type}",
                ))
        return decisions


class SeverityGate:
    """Drops info-severity signals unless they match escalation patterns.

    Compliance/fraud-labeled signals bypass the gate — regulatory events
    at any severity must reach the LLM for classification.
    """

    name = "severity_gate"
    stage = 1

    GATE_BYPASS_LABELS = frozenset({"compliance", "fraud", "sanctions"})

    ESCALATE_PATTERNS = [
        re.compile(r"(oomkill|segfault|panic|fatal|critical)", re.I),
        re.compile(r"(security|breach|unauthorized|injection)", re.I),
        re.compile(r"(data.?loss|corruption|integrity)", re.I),
    ]

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        decisions = []
        for s in signals:
            if s.severity != "info":
                continue

            label = s.labels.get("label", "") if s.labels else ""
            if label in self.GATE_BYPASS_LABELS:
                continue

            content_str = str(s.content)
            escalate = any(p.search(content_str) for p in self.ESCALATE_PATTERNS)

            if escalate:
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.ESCALATE, evidence="info with critical pattern",
                ))
            else:
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.DROP, evidence="info severity, no escalation pattern",
                ))
        return decisions


class PatternClassifier:
    """Classifies signals by regex patterns — handles known failure signatures."""

    name = "pattern_classifier"
    stage = 2

    PATTERNS = {
        "oom": (re.compile(r"(oomkill|out.of.memory|memory.limit)", re.I), "memory_exhaustion"),
        "disk": (re.compile(r"(disk.full|no.space|filesystem.full|disk.pressure)", re.I), "disk_pressure"),
        "cpu": (re.compile(r"(cpu.throttl|cpu.pressure|cpu.limit)", re.I), "cpu_pressure"),
        "network": (re.compile(r"(connection.refused|timeout|unreachable|dns.fail)", re.I), "network_failure"),
        "crash": (re.compile(r"(crashloop|segfault|panic|core.dump)", re.I), "application_crash"),
        "auth": (re.compile(r"(unauthorized|forbidden|403|401|auth.fail)", re.I), "auth_failure"),
        "scaling": (re.compile(r"(hpa.max|replica.limit|insufficient.cpu|pending.pod)", re.I), "scaling_limit"),
    }

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        decisions = []
        for s in signals:
            content_str = str(s.content)
            for pattern_name, (pattern, classification) in self.PATTERNS.items():
                if pattern.search(content_str):
                    decisions.append(CascadeDecision(
                        signal_id=s.signal_id, agent_name=self.name,
                        outcome=Outcome.CLASSIFY,
                        classification=classification,
                        evidence=f"matched {pattern_name} pattern",
                        confidence=0.85,
                    ))
                    break
        return decisions


class ThresholdClassifier:
    """Classifies signals based on numeric thresholds in content."""

    name = "threshold_classifier"
    stage = 2

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        decisions = []
        for s in signals:
            c = s.content

            # CPU usage
            cpu = c.get("cpu_percent") or c.get("cpu_usage")
            if cpu is not None and isinstance(cpu, (int, float)):
                if cpu > 95:
                    decisions.append(CascadeDecision(
                        signal_id=s.signal_id, agent_name=self.name,
                        outcome=Outcome.ESCALATE, classification="cpu_critical",
                        evidence=f"cpu={cpu}%", confidence=0.95,
                    ))
                    continue
                elif cpu > 80:
                    decisions.append(CascadeDecision(
                        signal_id=s.signal_id, agent_name=self.name,
                        outcome=Outcome.CLASSIFY, classification="cpu_warning",
                        evidence=f"cpu={cpu}%", confidence=0.90,
                    ))
                    continue

            # Memory usage
            mem = c.get("memory_percent") or c.get("memory_usage")
            if mem is not None and isinstance(mem, (int, float)):
                if mem > 95:
                    decisions.append(CascadeDecision(
                        signal_id=s.signal_id, agent_name=self.name,
                        outcome=Outcome.ESCALATE, classification="memory_critical",
                        evidence=f"memory={mem}%", confidence=0.95,
                    ))
                    continue
                elif mem > 80:
                    decisions.append(CascadeDecision(
                        signal_id=s.signal_id, agent_name=self.name,
                        outcome=Outcome.CLASSIFY, classification="memory_warning",
                        evidence=f"memory={mem}%", confidence=0.90,
                    ))
                    continue

            # Disk usage
            disk = c.get("disk_percent") or c.get("disk_usage")
            if disk is not None and isinstance(disk, (int, float)):
                if disk > 95:
                    decisions.append(CascadeDecision(
                        signal_id=s.signal_id, agent_name=self.name,
                        outcome=Outcome.ESCALATE, classification="disk_critical",
                        evidence=f"disk={disk}%", confidence=0.95,
                    ))
                elif disk > 80:
                    decisions.append(CascadeDecision(
                        signal_id=s.signal_id, agent_name=self.name,
                        outcome=Outcome.CLASSIFY, classification="disk_warning",
                        evidence=f"disk={disk}%", confidence=0.90,
                    ))

        return decisions


class TrendDetector:
    """Detects monotonic trends across sequential signals from the same entity.

    Runs at stage 0 — before severity gate and dedup. When a trend is detected
    (e.g. 4 declining vital signs), the latest signal is ESCALATED so it
    survives the severity gate even at info severity.

    Domain-agnostic: entity key and trend fields are discovered from the signal's
    labels and content. Any numeric field that moves monotonically over
    min_readings consecutive signals from the same entity triggers escalation.

    Safety: escalate-only, never suppresses. Same fail-open guarantee as
    PrimingEscalator.
    """

    name = "trend_detector"
    stage = 0

    ENTITY_KEYS = ("patient_id", "account_id", "device_id", "node",
                   "host", "circuit_id", "instance_id", "instance")
    IGNORE_FIELDS = frozenset({
        "message", "raw", "description", "name", "type",
        "reading_number", "sequence", "burst_id", "total",
    })
    TREND_FIELDS = re.compile(
        r"(heart_rate|bp_|spo2|temp|respiratory|pulse|"
        r"cpu_|memory_|disk_|latency|error_|fail_|"
        r"velocity_|restart|queue_|saturation|utilization|"
        r"packet_loss|jitter|signal_strength|consecutive_|"
        r"load_|iops|throughput|response_time)",
        re.I,
    )

    def __init__(self, min_readings: int = 3, window_seconds: float = 3600):
        self._min_readings = min_readings
        self._window = window_seconds
        self._history: Dict[str, List[dict]] = defaultdict(list)
        self._last_cleanup = 0.0

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        now = time.monotonic()
        if now - self._last_cleanup > 300:
            self._cleanup(now)
            self._last_cleanup = now

        decisions = []
        for s in signals:
            entity = self._entity_key(s)
            if not entity:
                continue

            numerics = self._extract_numerics(s)
            if not numerics:
                continue

            self._history[entity].append({"ts": now, "values": numerics, "signal_id": s.signal_id})

            history = self._history[entity]
            if len(history) < self._min_readings:
                continue

            recent = history[-self._min_readings:]
            osc_window = history[-max(self._min_readings + 1, 4):]
            trends = self._detect_trends(recent, osc_window)
            if trends:
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id, agent_name=self.name,
                    outcome=Outcome.ESCALATE,
                    evidence=f"trend detected: {', '.join(trends)}",
                    confidence=0.85,
                ))
        return decisions

    def _entity_key(self, s: Signal) -> str:
        if s.labels:
            for key in self.ENTITY_KEYS:
                val = s.labels.get(key)
                if val:
                    return f"{key}={val}"
        if s.content:
            for key in self.ENTITY_KEYS:
                val = s.content.get(key)
                if val:
                    return f"{key}={val}"
        return ""

    def _extract_numerics(self, s: Signal) -> Dict[str, float]:
        if not s.content:
            return {}
        result = {}
        for k, v in s.content.items():
            if k in self.IGNORE_FIELDS:
                continue
            if not self.TREND_FIELDS.search(k):
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                result[k] = float(v)
        return result

    def _detect_trends(self, readings: List[dict],
                       osc_readings: List[dict] = None) -> List[str]:
        if len(readings) < self._min_readings:
            return []

        all_fields: Set[str] = set()
        for r in readings:
            all_fields.update(r["values"].keys())

        trends = []
        for field in all_fields:
            values = [r["values"].get(field) for r in readings]
            if any(v is None for v in values):
                continue

            increasing = all(values[i] < values[i + 1] for i in range(len(values) - 1))
            decreasing = all(values[i] > values[i + 1] for i in range(len(values) - 1))

            if increasing:
                delta = values[-1] - values[0]
                pct = abs(delta / values[0]) * 100 if values[0] != 0 else 100
                if pct >= 5:
                    trends.append(f"{field} rising ({values[0]:.1f}→{values[-1]:.1f})")
            elif decreasing:
                delta = values[0] - values[-1]
                pct = abs(delta / values[0]) * 100 if values[0] != 0 else 100
                if pct >= 5:
                    trends.append(f"{field} falling ({values[0]:.1f}→{values[-1]:.1f})")

        if not trends and osc_readings and len(osc_readings) >= 4:
            osc_fields: Set[str] = set()
            for r in osc_readings:
                osc_fields.update(r["values"].keys())
            for field in osc_fields:
                values = [r["values"].get(field) for r in osc_readings]
                if any(v is None for v in values):
                    continue
                osc = self._detect_oscillation(field, values)
                if osc:
                    trends.append(osc)
        return trends

    def _detect_oscillation(self, field: str, values: List[float]) -> str:
        """Detect flapping — direction reverses on most consecutive readings."""
        if len(values) < 4:
            return ""
        reversals = 0
        for i in range(1, len(values) - 1):
            prev_dir = values[i] - values[i - 1]
            next_dir = values[i + 1] - values[i]
            if prev_dir * next_dir < 0:
                reversals += 1
        if reversals >= len(values) - 2:
            mn, mx = min(values), max(values)
            if mx - mn > 0:
                return f"{field} oscillating ({mn:.0f}↔{mx:.0f}, {reversals} reversals)"
        return ""

    def _cleanup(self, now: float):
        expired = []
        for entity, readings in self._history.items():
            self._history[entity] = [r for r in readings if now - r["ts"] < self._window]
            if not self._history[entity]:
                expired.append(entity)
        for e in expired:
            del self._history[e]


def default_agents() -> list:
    """Return the standard built-in cascade agents."""
    return [
        TrendDetector(),
        DeduplicateAgent(),
        TransientSuppressor(),
        SeverityGate(),
        PatternClassifier(),
        ThresholdClassifier(),
    ]

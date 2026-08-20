"""Corpus analyzer — discovers signal patterns and proposes draft agents.

Watches the signal stream, identifies dominant patterns the existing
agents aren't handling, and auto-generates draft RuleAgents for the
promotion engine to validate.

No frontier models required. Pattern discovery is pure statistics.
Optional: phi4-mini or any local LLM for naming/describing patterns.
"""

from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .promotion import AgentMetrics, RuleAgent
from .protocol import Signal

log = logging.getLogger(__name__)


@dataclass
class PatternCandidate:
    """A discovered pattern that could become an agent."""
    pattern_type: str
    key: str
    count: int
    total_signals: int
    frequency: float
    sample_signals: List[Dict] = field(default_factory=list)
    proposed_rule: Optional[Dict] = None
    proposed_agent: Optional[AgentMetrics] = None


class CorpusAnalyzer:
    """Discovers recurring signal patterns and proposes draft agents.

    Runs periodically against the observed signal buffer. Identifies:
    1. Repeat flooding — same signal type+source repeating excessively
    2. Dominant types — signal types making up >X% of traffic
    3. Threshold clusters — numeric values consistently above/below ranges
    4. Temporal bursts — signal types that spike in short windows
    """

    def __init__(
        self,
        min_frequency: float = 0.05,
        min_repeat_count: int = 10,
        repeat_window_seconds: float = 300,
        analysis_interval: float = 600,
    ):
        self._min_frequency = min_frequency
        self._min_repeat_count = min_repeat_count
        self._repeat_window = repeat_window_seconds
        self._analysis_interval = analysis_interval

        self._signal_buffer: List[Dict] = []
        self._buffer_max = 10000
        self._last_analysis: float = 0
        self._known_patterns: set = set()
        self._proposed_agents: List[Tuple[AgentMetrics, RuleAgent]] = []
        self._analysis_log: List[Dict] = []

    def observe(self, signals: List[Signal]) -> None:
        """Add signals to the observation buffer."""
        for s in signals:
            entry = {
                "signal_type": s.signal_type,
                "severity": s.severity,
                "source": s.source,
                "namespace": s.namespace,
                "content_keys": list(s.content.keys()) if s.content else [],
                "message": s.content.get("message", "")[:100] if s.content else "",
                "timestamp": time.monotonic(),
            }
            # Extract numeric features
            if s.content:
                for k, v in s.content.items():
                    if isinstance(v, (int, float)):
                        entry[f"feature_{k}"] = v

            self._signal_buffer.append(entry)

        if len(self._signal_buffer) > self._buffer_max:
            self._signal_buffer = self._signal_buffer[-self._buffer_max:]

    def maybe_analyze(self) -> List[Tuple[AgentMetrics, RuleAgent]]:
        """Run analysis if enough time has passed. Returns new draft agents."""
        now = time.monotonic()
        if now - self._last_analysis < self._analysis_interval:
            return []
        if len(self._signal_buffer) < 100:
            return []

        self._last_analysis = now
        return self.analyze()

    def analyze(self) -> List[Tuple[AgentMetrics, RuleAgent]]:
        """Analyze the signal buffer and propose new agents."""
        buf = self._signal_buffer
        total = len(buf)
        if total < 50:
            return []

        new_agents = []
        candidates = []

        # --- Pattern 1: Repeat flooding ---
        # Group by signal_type (not source+namespace) to avoid duplicates
        repeat_by_instance = Counter()
        repeat_samples = defaultdict(list)
        for s in buf:
            instance_key = f"{s['signal_type']}:{s['source']}:{s['namespace']}"
            repeat_by_instance[instance_key] += 1
            if len(repeat_samples[s["signal_type"]]) < 3:
                repeat_samples[s["signal_type"]].append(s)

        # Find signal types with many repeating instances
        type_instance_counts = defaultdict(int)
        for key, count in repeat_by_instance.items():
            if count >= self._min_repeat_count:
                sig_type = key.split(":")[0]
                type_instance_counts[sig_type] += count

        for sig_type, total_repeats in sorted(type_instance_counts.items(), key=lambda x: -x[1]):
            pattern_key = f"repeat:{sig_type}"
            if pattern_key in self._known_patterns:
                continue

            freq = total_repeats / total
            candidates.append(PatternCandidate(
                pattern_type="repeat_flood",
                key=pattern_key,
                count=total_repeats,
                total_signals=total,
                frequency=freq,
                sample_signals=repeat_samples.get(sig_type, []),
            ))

        # --- Pattern 2: Dominant signal types ---
        type_counts = Counter(s["signal_type"] for s in buf)
        for sig_type, count in type_counts.most_common(10):
            freq = count / total
            if freq < self._min_frequency:
                break
            pattern_key = f"dominant:{sig_type}"
            if pattern_key in self._known_patterns:
                continue

            samples = [s for s in buf if s["signal_type"] == sig_type][:3]
            candidates.append(PatternCandidate(
                pattern_type="dominant_type",
                key=pattern_key,
                count=count,
                total_signals=total,
                frequency=freq,
                sample_signals=samples,
            ))

        # --- Pattern 3: Severity distribution anomalies ---
        sev_by_type = defaultdict(Counter)
        for s in buf:
            sev_by_type[s["signal_type"]][s["severity"]] += 1

        for sig_type, sevs in sev_by_type.items():
            total_for_type = sum(sevs.values())
            if total_for_type < 20:
                continue

            # If >80% of a signal type is one severity, it's a pattern
            for sev, sev_count in sevs.items():
                if sev_count / total_for_type > 0.80:
                    pattern_key = f"mono_severity:{sig_type}:{sev}"
                    if pattern_key in self._known_patterns:
                        continue
                    candidates.append(PatternCandidate(
                        pattern_type="mono_severity",
                        key=pattern_key,
                        count=sev_count,
                        total_signals=total,
                        frequency=sev_count / total,
                        sample_signals=[s for s in buf if s["signal_type"] == sig_type][:3],
                    ))

        # --- Generate draft agents from candidates ---
        for c in candidates:
            agent, rule = self._candidate_to_agent(c)
            if agent and rule:
                self._known_patterns.add(c.key)
                new_agents.append((agent, rule))
                self._proposed_agents.append((agent, rule))
                c.proposed_agent = agent
                c.proposed_rule = rule.condition

        # Log analysis
        self._analysis_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "buffer_size": total,
            "candidates_found": len(candidates),
            "agents_proposed": len(new_agents),
            "patterns": [{"type": c.pattern_type, "key": c.key,
                          "count": c.count, "freq": round(c.frequency, 3)}
                         for c in candidates],
        })

        if new_agents:
            log.info("Corpus analyzer: %d new draft agents from %d candidates (%d signals)",
                     len(new_agents), len(candidates), total)

        return new_agents

    def _candidate_to_agent(self, c: PatternCandidate) -> Tuple[Optional[AgentMetrics], Optional[RuleAgent]]:
        """Convert a pattern candidate into a draft agent + rule."""

        if c.pattern_type == "repeat_flood":
            parts = c.key.split(":", 2)
            sig_type = parts[1]
            name = f"suppress_{sig_type}_repeat"

            rule = RuleAgent({
                "name": name,
                "signal_types": [sig_type],
                "condition": {
                    "field": "signal_type",
                    "operator": "eq",
                    "value": sig_type,
                },
                "classification": f"{sig_type}_repeat_noise",
            })

            agent = AgentMetrics(
                name=name,
                tier="draft",
                config={
                    "pattern_type": c.pattern_type,
                    "signal_type": sig_type,
                    "observed_count": c.count,
                    "observed_frequency": round(c.frequency, 3),
                    "discovered_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return agent, rule

        elif c.pattern_type == "dominant_type":
            sig_type = c.key.replace("dominant:", "")
            name = f"classify_{sig_type}"

            rule = RuleAgent({
                "name": name,
                "signal_types": [sig_type],
                "condition": {
                    "field": "signal_type",
                    "operator": "eq",
                    "value": sig_type,
                },
                "classification": f"{sig_type}_classified",
            })

            agent = AgentMetrics(
                name=name,
                tier="draft",
                config={
                    "pattern_type": c.pattern_type,
                    "signal_type": sig_type,
                    "observed_frequency": round(c.frequency, 3),
                    "discovered_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return agent, rule

        elif c.pattern_type == "mono_severity":
            _, sig_type, sev = c.key.split(":", 2)
            name = f"gate_{sig_type}_{sev}"

            rule = RuleAgent({
                "name": name,
                "signal_types": [sig_type],
                "condition": {
                    "field": "signal_type",
                    "operator": "eq",
                    "value": sig_type,
                },
                "classification": f"{sig_type}_{sev}_gate",
            })

            agent = AgentMetrics(
                name=name,
                tier="draft",
                config={
                    "pattern_type": c.pattern_type,
                    "signal_type": sig_type,
                    "dominant_severity": sev,
                    "observed_frequency": round(c.frequency, 3),
                    "discovered_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return agent, rule

        return None, None

    def get_analysis_log(self, limit: int = 20) -> list:
        return self._analysis_log[-limit:]

    def get_proposed_agents(self) -> list:
        return [{
            "name": a.name,
            "tier": a.tier,
            "config": a.config,
            "rule": r.condition,
        } for a, r in self._proposed_agents]

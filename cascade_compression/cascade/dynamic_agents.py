"""Dynamic agents — generated from corpus analysis and promoted through tiers.

These agents handle patterns that the built-in agents can't:
- Repeat flooding: same signal_type+source repeating excessively
- Dominant noise: signal types that are consistently noise at any severity
- Contextual noise: signal types that are noise in specific contexts
  (e.g. namespace, source) but important in others

Unlike built-in agents, dynamic agents are created at runtime from
observed patterns and only activate after promotion validates them.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, List

from .protocol import CascadeDecision, Outcome, Signal

_SUPPRESSIBLE_SEVERITIES = {"info", "low"}


class RepeatFloodSuppressor:
    """Suppresses signals that repeat from the same source excessively.

    Tracks signal_type+source+namespace combinations. After N occurrences
    within a time window, subsequent signals are suppressed. The first
    occurrence always passes through (fail-open).
    """

    name = "repeat_flood_suppressor"
    stage = 3  # Runs after built-in agents

    def __init__(self, max_repeats: int = 3, window_seconds: float = 300):
        self._max_repeats = max_repeats
        self._window = window_seconds
        self._counts: Dict[str, List[float]] = defaultdict(list)
        self._signal_types: set = set()

    def add_signal_type(self, signal_type: str):
        """Register a signal type for repeat suppression."""
        self._signal_types.add(signal_type)

    def remove_signal_type(self, signal_type: str):
        self._signal_types.discard(signal_type)

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        if not self._signal_types:
            return []

        now = time.monotonic()
        decisions = []

        for s in signals:
            if (
                s.signal_type not in self._signal_types
                or s.severity not in _SUPPRESSIBLE_SEVERITIES
            ):
                continue

            key = f"{s.signal_type}:{s.source}:{s.namespace}"

            # Expire old timestamps
            self._counts[key] = [t for t in self._counts[key] if now - t < self._window]

            self._counts[key].append(now)

            if len(self._counts[key]) > self._max_repeats:
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id,
                    agent_name=self.name,
                    outcome=Outcome.SUPPRESS,
                    evidence=f"repeat #{len(self._counts[key])} of {s.signal_type} from {s.source} in {self._window}s",
                    confidence=0.90,
                ))

        return decisions


class DominantNoiseSuppressor:
    """Suppresses signal types identified as dominant noise.

    When the corpus analyzer finds a signal type making up >X% of traffic
    and deepfield consistently drops it, this agent suppresses after
    the first occurrence per source per window.
    """

    name = "dominant_noise_suppressor"
    stage = 3

    def __init__(self, window_seconds: float = 300):
        self._window = window_seconds
        self._noise_types: set = set()
        self._seen: Dict[str, float] = {}

    def add_noise_type(self, signal_type: str):
        """Register a signal type as dominant noise."""
        self._noise_types.add(signal_type)

    def remove_noise_type(self, signal_type: str):
        self._noise_types.discard(signal_type)

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        if not self._noise_types:
            return []

        now = time.monotonic()
        # Expire old entries
        self._seen = {k: v for k, v in self._seen.items() if now - v < self._window}

        decisions = []
        for s in signals:
            if (
                s.signal_type not in self._noise_types
                or s.severity not in _SUPPRESSIBLE_SEVERITIES
            ):
                continue

            key = f"{s.signal_type}:{s.source}:{s.namespace}"
            if key in self._seen:
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id,
                    agent_name=self.name,
                    outcome=Outcome.SUPPRESS,
                    evidence=f"dominant noise: {s.signal_type} (already seen from {s.source})",
                    confidence=0.85,
                ))
            else:
                self._seen[key] = now

        return decisions


class ContextualNoiseSuppressor:
    """Suppresses signals by (signal_type, context) when the overall type
    has mixed importance but specific contexts are pure noise.

    Example: event_unhealthy is 24% important overall, but in namespace
    openshift-monitoring it's 100% noise. This agent suppresses only
    the proven-noise contexts while letting unknown contexts through.

    The context_key is configurable — defaults to "namespace" but could
    be "source", "cluster", or any Signal attribute. Framework-generic.
    """

    name = "contextual_noise_suppressor"
    stage = 3

    def __init__(self, context_key: str = "namespace", window_seconds: float = 300):
        self._context_key = context_key
        self._window = window_seconds
        self._noise_contexts: Dict[str, set] = defaultdict(set)
        self._seen: Dict[str, float] = {}

    def add_noise_context(self, signal_type: str, context_value: str):
        self._noise_contexts[signal_type].add(context_value)

    def remove_noise_context(self, signal_type: str, context_value: str):
        self._noise_contexts.get(signal_type, set()).discard(context_value)

    def remove_signal_type(self, signal_type: str):
        self._noise_contexts.pop(signal_type, None)

    def get_noise_contexts(self) -> Dict[str, list]:
        return {k: sorted(v) for k, v in self._noise_contexts.items()}

    def process(self, signals: List[Signal]) -> List[CascadeDecision]:
        if not self._noise_contexts:
            return []

        now = time.monotonic()
        self._seen = {k: v for k, v in self._seen.items() if now - v < self._window}

        decisions = []
        for s in signals:
            if s.signal_type not in self._noise_contexts:
                continue
            if s.severity not in _SUPPRESSIBLE_SEVERITIES:
                continue

            ctx = getattr(s, self._context_key, "")
            if ctx not in self._noise_contexts[s.signal_type]:
                continue

            dedup_key = f"{s.signal_type}:{ctx}:{s.source}"
            if dedup_key in self._seen:
                decisions.append(CascadeDecision(
                    signal_id=s.signal_id,
                    agent_name=self.name,
                    outcome=Outcome.SUPPRESS,
                    evidence=f"contextual noise: {s.signal_type} in {self._context_key}={ctx}",
                    confidence=0.88,
                ))
            else:
                self._seen[dedup_key] = now

        return decisions

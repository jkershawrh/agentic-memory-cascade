"""Workload bootstrapper — classifies live signal streams into workload types.

Uses a sliding window of observed signals and cosine similarity against
known workload fingerprints to determine the current workload type and
industry. This module is standalone — it does NOT import from deepfield's
domain models. Instead it accepts plain dicts (or any mapping-like object)
for signals and decisions.
"""

from __future__ import annotations

import fnmatch
import math
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, Field

from ..resources import resource_path

# ---------------------------------------------------------------------------
# Fingerprint models
# ---------------------------------------------------------------------------


class WorkloadFingerprint(BaseModel):
    """Characteristic weight distributions for a workload type."""

    signal_type_weights: Dict[str, float] = Field(default_factory=dict)
    resource_kind_weights: Dict[str, float] = Field(default_factory=dict)
    severity_distribution: Dict[str, float] = Field(default_factory=dict)
    namespace_patterns: List[str] = Field(default_factory=list)
    failure_class_weights: Dict[str, float] = Field(default_factory=dict)

    def to_vector(self, key_sets: Optional[List[List[str]]] = None) -> List[float]:
        """Flatten all weight dicts into a single ordered vector for similarity.

        If *key_sets* is provided (one sorted key list per dict), use those keys
        so that two different fingerprints produce vectors with aligned positions.
        Missing keys are treated as 0.0.
        """
        dicts = (
            self.signal_type_weights,
            self.resource_kind_weights,
            self.severity_distribution,
            self.failure_class_weights,
        )
        vec: List[float] = []
        if key_sets is None:
            for d in dicts:
                for k in sorted(d.keys()):
                    vec.append(d[k])
        else:
            for d, keys in zip(dicts, key_sets):
                for k in keys:
                    vec.append(d.get(k, 0.0))
        return vec


class WorkloadProfile(BaseModel):
    """A named workload profile with its fingerprint."""

    workload_type: str
    industry: str = "basic"
    display_name: str = ""
    fingerprint: WorkloadFingerprint = Field(default_factory=WorkloadFingerprint)
    sla_latency_ms: int = 2000


class ClassificationScorecard(BaseModel):
    """Grades summarising how well the bootstrapper is performing."""

    confidence_grade: str = "red"           # green >=0.8, yellow >=0.5, red <0.5
    reclassification_speed_grade: str = "green"
    namespace_match_grade: str = "red"      # green if namespace matched, red if not
    overall_grade: str = "red"


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors.

    Returns 0.0 if either vector has zero magnitude.
    Vectors are zero-padded to equal length.
    """
    max_len = max(len(a), len(b))
    a_padded = a + [0.0] * (max_len - len(a))
    b_padded = b + [0.0] * (max_len - len(b))

    dot = sum(x * y for x, y in zip(a_padded, b_padded))
    mag_a = math.sqrt(sum(x * x for x in a_padded))
    mag_b = math.sqrt(sum(x * x for x in b_padded))

    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0

    return dot / (mag_a * mag_b)


# ---------------------------------------------------------------------------
# Bootstrapper
# ---------------------------------------------------------------------------

# Minimum number of samples before classification is attempted
MIN_SAMPLES = 20

# Sliding window size
WINDOW_SIZE = 200


def _load_profiles(path: Optional[str] = None) -> Dict[str, WorkloadProfile]:
    """Load workload profiles from YAML."""
    if path is None:
        path = str(resource_path("config", "workload_profiles.yaml"))

    p = Path(path)
    if not p.exists():
        return {}

    with open(p) as f:
        raw = yaml.safe_load(f)

    profiles: Dict[str, WorkloadProfile] = {}
    for key, val in raw.items():
        fp_data = val.get("fingerprint", {})
        fp = WorkloadFingerprint(**fp_data)
        profiles[key] = WorkloadProfile(
            workload_type=key,
            industry=val.get("industry", "basic"),
            display_name=val.get("display_name", key),
            fingerprint=fp,
            sla_latency_ms=val.get("sla_latency_ms", 2000),
        )

    return profiles


class WorkloadBootstrapper:
    """Classifies the current workload from a sliding window of observed signals.

    Usage::

        bootstrapper = WorkloadBootstrapper()

        # Feed signals as dicts
        bootstrapper.observe(
            signal={"signal_type": "transaction_alert", "severity": "high",
                    "resource_kind": "transaction", "namespace": "fraud-prod",
                    "failure_class": "anomalous_transaction"},
            decisions=[{"outcome": "escalate"}],
        )

        wtype, confidence = bootstrapper.current_workload()
        industry = bootstrapper.current_industry()
    """

    def __init__(
        self,
        profiles_path: Optional[str] = None,
        decay_factor: float = 0.95,
    ):
        self._profiles = _load_profiles(profiles_path)
        self._window: deque = deque(maxlen=WINDOW_SIZE)
        self._cached_type: str = "generic"
        self._cached_confidence: float = 0.0
        self._cached_industry: str = "basic"
        self._decay_factor: float = decay_factor
        self._observation_count: int = 0
        self._namespace_matched: bool = False
        self._observed_namespaces: set = set()

    def observe(
        self,
        signal: Dict[str, Any],
        decisions: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Record one signal observation.

        ``signal`` should be a dict with keys like:
        - signal_type (str)
        - severity (str)
        - resource_kind (str)
        - namespace (str)
        - failure_class (str, optional)

        ``decisions`` is an optional list of dicts with at least an ``outcome`` key.
        """
        self._window.append({
            "signal_type": signal.get("signal_type", "unknown"),
            "severity": signal.get("severity", "info"),
            "resource_kind": signal.get("resource_kind", "unknown"),
            "namespace": signal.get("namespace", ""),
            "failure_class": signal.get("failure_class", ""),
            "decisions": decisions or [],
        })
        self._observation_count += 1

        # Adaptive reclassification interval
        interval = self._reclassification_interval()
        if (
            len(self._window) >= MIN_SAMPLES
            and self._observation_count % interval == 0
        ):
            self._reclassify()

    def current_workload(self) -> Tuple[str, float]:
        """Return (workload_type, confidence) based on current window."""
        if len(self._window) < MIN_SAMPLES:
            return ("generic", 0.0)
        return (self._cached_type, self._cached_confidence)

    def current_industry(self) -> str:
        """Return the industry associated with the current workload type."""
        if len(self._window) < MIN_SAMPLES:
            return "basic"
        return self._cached_industry

    def _reclassification_interval(self) -> int:
        """Return the adaptive reclassification interval based on confidence.

        - confidence >= 0.9 : every 20 observations (save CPU)
        - confidence >= 0.5 : every 5 observations (default)
        - confidence < 0.5  : every 2 observations (aggressive)
        """
        if self._cached_confidence >= 0.9:
            return 20
        elif self._cached_confidence >= 0.5:
            return 5
        return 2

    def scorecard(self) -> ClassificationScorecard:
        """Return a :class:`ClassificationScorecard` for the current state."""
        confidence = self._cached_confidence

        # Confidence grade
        if confidence >= 0.8:
            confidence_grade = "green"
        elif confidence >= 0.5:
            confidence_grade = "yellow"
        else:
            confidence_grade = "red"

        # Reclassification speed grade — green when the interval is
        # responsive (<=5), yellow when conservative (high confidence).
        interval = self._reclassification_interval()
        reclassification_speed_grade = "green" if interval <= 5 else "yellow"

        # Namespace match grade
        namespace_match_grade = "green" if self._namespace_matched else "red"

        # Overall grade — worst of the three
        grade_rank = {"green": 0, "yellow": 1, "red": 2}
        worst = max(
            (confidence_grade, reclassification_speed_grade, namespace_match_grade),
            key=lambda g: grade_rank[g],
        )

        return ClassificationScorecard(
            confidence_grade=confidence_grade,
            reclassification_speed_grade=reclassification_speed_grade,
            namespace_match_grade=namespace_match_grade,
            overall_grade=worst,
        )

    def _build_observed_fingerprint(self) -> WorkloadFingerprint:
        """Build a fingerprint from the current observation window.

        Uses exponential decay weighting so that recent observations
        contribute more to the fingerprint than older ones.  Also collects
        observed namespaces for namespace pattern matching.
        """
        signal_types: Dict[str, float] = {}
        resource_kinds: Dict[str, float] = {}
        severities: Dict[str, float] = {}
        failure_classes: Dict[str, float] = {}
        observed_namespaces: set = set()

        n = len(self._window)
        if n == 0:
            self._observed_namespaces = set()
            return WorkloadFingerprint()

        for i, obs in enumerate(self._window):
            weight = self._decay_factor ** (n - 1 - i)

            st = obs["signal_type"]
            signal_types[st] = signal_types.get(st, 0.0) + weight

            rk = obs["resource_kind"]
            resource_kinds[rk] = resource_kinds.get(rk, 0.0) + weight

            sev = obs["severity"]
            severities[sev] = severities.get(sev, 0.0) + weight

            fc = obs.get("failure_class", "")
            if fc:
                failure_classes[fc] = failure_classes.get(fc, 0.0) + weight

            ns = obs.get("namespace", "")
            if ns:
                observed_namespaces.add(ns)

        self._observed_namespaces = observed_namespaces

        # Normalize to proportions
        for d in (signal_types, resource_kinds, severities, failure_classes):
            total = sum(d.values())
            if total > 0:
                for k in d:
                    d[k] /= total

        return WorkloadFingerprint(
            signal_type_weights=signal_types,
            resource_kind_weights=resource_kinds,
            severity_distribution=severities,
            failure_class_weights=failure_classes,
        )

    def _check_namespace_match(self, profile: WorkloadProfile) -> bool:
        """Return True if any observed namespace matches the profile's patterns."""
        if not profile.fingerprint.namespace_patterns:
            return False
        for ns in self._observed_namespaces:
            for pattern in profile.fingerprint.namespace_patterns:
                if fnmatch.fnmatch(ns, pattern):
                    return True
        return False

    @staticmethod
    def _union_keys(*dicts: Dict[str, float]) -> List[str]:
        """Sorted union of keys across multiple dicts."""
        keys: set = set()
        for d in dicts:
            keys.update(d.keys())
        return sorted(keys)

    def _reclassify(self) -> None:
        """Reclassify workload based on cosine similarity to known profiles."""
        if not self._profiles:
            return

        observed = self._build_observed_fingerprint()

        best_type = "generic"
        best_confidence = 0.0
        best_industry = "basic"
        best_ns_match = False

        for wtype, profile in self._profiles.items():
            ref = profile.fingerprint

            # Build aligned key sets (union of observed + reference keys per dict)
            key_sets = [
                self._union_keys(observed.signal_type_weights, ref.signal_type_weights),
                self._union_keys(observed.resource_kind_weights, ref.resource_kind_weights),
                self._union_keys(observed.severity_distribution, ref.severity_distribution),
                self._union_keys(observed.failure_class_weights, ref.failure_class_weights),
            ]

            obs_vec = observed.to_vector(key_sets)
            ref_vec = ref.to_vector(key_sets)
            sim = _cosine_similarity(obs_vec, ref_vec)

            # Boost score if observed namespaces match the profile's patterns
            ns_match = self._check_namespace_match(profile)
            if ns_match:
                sim = min(sim + 0.1, 1.0)

            if sim > best_confidence:
                best_confidence = sim
                best_type = wtype
                best_industry = profile.industry
                best_ns_match = ns_match

        self._cached_type = best_type
        self._cached_confidence = round(best_confidence, 4)
        self._cached_industry = best_industry
        self._namespace_matched = best_ns_match

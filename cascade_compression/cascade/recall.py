"""Recall engine — query the memory archive for precedent.

Given a new signal, find similar memories from the archive using
composite similarity: type match, label overlap, content feature
cosine, and text trigram similarity. Results are ranked by composite
score weighted by memory strength.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .memory import Memory, MemoryArchive
from .protocol import Signal


def type_match(a: str, b: str) -> float:
    return 1.0 if a == b else 0.0


def label_jaccard(a: Dict[str, str], b: Dict[str, str]) -> float:
    if not a and not b:
        return 0.0
    set_a = set(a.items())
    set_b = set(b.items())
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def content_feature_cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    all_keys = set(a) | set(b)
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in all_keys)
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _trigrams(text: str) -> set:
    if len(text) < 3:
        return set()
    return {text[i:i + 3] for i in range(len(text) - 2)}


def text_trigram_similarity(a: str, b: str) -> float:
    tri_a = _trigrams(a.lower())
    tri_b = _trigrams(b.lower())
    if not tri_a or not tri_b:
        return 0.0
    intersection = len(tri_a & tri_b)
    union = len(tri_a | tri_b)
    return intersection / union if union > 0 else 0.0


def _extract_message(signal: Signal) -> str:
    return str(signal.content.get("message", ""))


def _extract_features(signal: Signal) -> Dict[str, float]:
    return {k: float(v) for k, v in signal.content.items()
            if isinstance(v, (int, float))}


@dataclass
class RecallResult:
    memory: Memory
    score: float
    breakdown: Dict[str, float] = field(default_factory=dict)


class RecallEngine:
    def __init__(self, w_type: float = 0.4, w_labels: float = 0.2,
                 w_features: float = 0.2, w_text: float = 0.2):
        self.w_type = w_type
        self.w_labels = w_labels
        self.w_features = w_features
        self.w_text = w_text

    def recall(self, signal: Signal, archive: MemoryArchive,
               top_k: int = 5, min_score: float = 0.1,
               reinforce: bool = False) -> List[RecallResult]:
        memories = archive.query(limit=archive.size or 1)
        if not memories:
            return []

        query_message = _extract_message(signal)
        query_features = _extract_features(signal)

        results = []
        for memory in memories:
            tm = type_match(signal.signal_type, memory.signal.signal_type)
            lj = label_jaccard(signal.labels, memory.signal.labels)
            fc = content_feature_cosine(query_features, memory.feature_vector)
            ts = text_trigram_similarity(query_message,
                                        _extract_message(memory.signal))

            raw_score = (self.w_type * tm + self.w_labels * lj +
                         self.w_features * fc + self.w_text * ts)
            score = raw_score * memory.strength

            if score >= min_score:
                results.append(RecallResult(
                    memory=memory,
                    score=score,
                    breakdown={
                        "type_match": tm,
                        "label_overlap": lj,
                        "feature_cosine": fc,
                        "text_similarity": ts,
                        "raw_score": raw_score,
                        "strength": memory.strength,
                    },
                ))

        results.sort(key=lambda r: r.score, reverse=True)
        results = results[:top_k]

        if reinforce:
            now = datetime.now(timezone.utc).isoformat()
            for r in results:
                archive.reinforce(r.memory.memory_id)
                r.memory.recall_count += 1
                r.memory.last_recalled_at = now

        return results

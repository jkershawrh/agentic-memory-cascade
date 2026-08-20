"""Precision metric for cascade LLM classifications.

Samples signals the LLM classified as "important" (needs_attention or real_incident)
and scores how many actually warranted escalation. Precision = true_important / classified_important.

Without ground truth labels, precision is estimated by a second LLM pass
with a stricter prompt that asks "given full context, was this worth escalating?"
"""

import json
import logging
import random
from dataclasses import dataclass
from typing import List

log = logging.getLogger(__name__)


@dataclass
class PrecisionSample:
    signal_type: str
    classification: str
    latency_ms: int
    content: str
    verdict: str = ""
    reason: str = ""


@dataclass
class PrecisionReport:
    total_important: int
    sampled: int
    true_important: int
    false_important: int
    inconclusive: int
    precision: float
    samples: List[PrecisionSample]


def sample_important_signals(llm_results: List[dict], n: int = 50, seed: int = 42) -> List[PrecisionSample]:
    important = [r for r in llm_results if r.get("classification", "") in
                 ("needs_attention", "real_incident")]
    if not important:
        return []

    rng = random.Random(seed)
    sampled = rng.sample(important, min(n, len(important)))

    return [PrecisionSample(
        signal_type=s["signal_type"],
        classification=s["classification"],
        latency_ms=s.get("latency_ms", 0),
        content=json.dumps({k: v for k, v in s.items()
                            if k not in ("classification", "latency_ms")}),
    ) for s in sampled]


def score_precision(samples: List[PrecisionSample]) -> PrecisionReport:
    true_imp = sum(1 for s in samples if s.verdict == "true_important")
    false_imp = sum(1 for s in samples if s.verdict == "false_important")
    inconclusive = sum(1 for s in samples if s.verdict not in ("true_important", "false_important"))

    return PrecisionReport(
        total_important=len(samples),
        sampled=len(samples),
        true_important=true_imp,
        false_important=false_imp,
        inconclusive=inconclusive,
        precision=true_imp / max(1, true_imp + false_imp),
        samples=samples,
    )


def auto_score_with_llm(
    samples: List[PrecisionSample],
    llm_url: str,
    llm_key: str,
    llm_model: str = "granite-3-2-8b-instruct-cpu",
) -> PrecisionReport:
    """Use a second LLM pass to estimate precision.

    Asks the LLM: "Given this signal, was classifying it as important correct?"
    Uses a stricter prompt than the original classification.
    """
    try:
        import httpx
    except ImportError:
        log.warning("httpx not installed — cannot auto-score")
        return score_precision(samples)

    system = """You are auditing a signal classification system. A previous classifier marked this signal as important (needs_attention or real_incident).

Your job: Was the classifier RIGHT to escalate this signal, or was it a false alarm?

Consider: Is this signal genuinely unusual? Would ignoring it risk missing a real problem? Or is this a routine event that got over-escalated?

Respond with exactly one word: correct (the signal truly needed attention) or false_alarm (the signal was routine noise that got over-escalated)."""

    with httpx.Client(timeout=30) as client:
        for sample in samples:
            prompt = f"Signal classified as '{sample.classification}':\n{sample.signal_type}: {sample.content}"
            try:
                r = client.post(
                    f"{llm_url}/v1/chat/completions",
                    json={
                        "model": llm_model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": 5,
                        "temperature": 0,
                    },
                    headers={"Authorization": f"Bearer {llm_key}"},
                )
                r.raise_for_status()
                answer = r.json()["choices"][0]["message"]["content"].strip().lower()
                if "correct" in answer:
                    sample.verdict = "true_important"
                elif "false" in answer or "alarm" in answer:
                    sample.verdict = "false_important"
                else:
                    sample.verdict = "inconclusive"
                    sample.reason = answer
            except Exception as e:
                sample.verdict = "inconclusive"
                sample.reason = str(e)[:60]

    return score_precision(samples)

"""Synthetic agent memory claim generator for cascade benchmarking.

Generates labeled claims with realistic patterns:
- Noise: session metadata, commit refs, config snapshots, expired state
- Known: cross-project duplicates (same claim from multiple projects)
- Novel: unique insights, lessons learned, decisions
- Critical: user corrections, contradictions
"""

import random
from dataclasses import dataclass
from typing import List


@dataclass
class SyntheticMemoryClaim:
    id: int
    source_system: str
    memory_type: str
    project: str
    claim_text: str
    claim_type: str
    section: str
    label: str
    label_detail: str


PROJECTS = [
    "fleet-llm-d", "cascade-compression", "deepfield", "geolux",
    "stargate", "are-immutable-ledger", "intel-quickstarts", "triforce",
    "governed-cognitive-loop", "novascan", "launchpad", "command-center",
    "agentobs", "slo-sli-automation", "edge-inference-at-scale",
    "ai-on-xeon",
]

NOISE_CLAIMS = [
    ("Branch: main, 3 commits ahead of origin", "session_noise"),
    ("Token expires in 23 hours", "session_noise"),
    ("Working tree clean, nothing to commit", "session_noise"),
    ("a4f2c91 fix: update deployment manifest", "session_noise"),
    ("3/3 pods Running, 1 CrashLoopBackOff (postgres, 12d)", "session_noise"),
    ("deepfield-backend-8565ddb666 2/2 Running 0 28h", "session_noise"),
    ("GATEWAY_PORT=28099", "config_ref"),
    ("oc config use-context deepfield/api-ocpv-infra01", "config_ref"),
    ("Stash: none", "session_noise"),
    ("Last commit: 8543b79 feat: expanded shootout scripts", "session_noise"),
]

# Claims that intentionally repeat across projects — the institutional knowledge signal
CROSS_PROJECT_CLAIMS = [
    "Follow CDD then TDD then BDD then EDD methodology with red/green rubric grids for all milestones.",
    "Granite-3-2-8b-instruct on Intel Xeon 6 CPU is the standard inference platform.",
    "Terse prompts outperform detailed context-rich prompts for classification tasks.",
    "Temperature=0 always. All models must be 100% deterministic.",
    "Self-contained repo — no external services, no separate frontend, no Docker unless required.",
    "Over-escalate (safe failure) rather than under-escalate (dangerous failure).",
    "The cascade framework must never be modified for domain-specific work.",
    "Production gates: red -> yellow -> green rubric before any deployment.",
]

NOVEL_CLAIMS = [
    ("The dedup agent is too aggressive on medium+ severity signals.", "fact"),
    ("PVC sizing is consistently underestimated on first deploy — start with 10Gi.", "fact"),
    ("oc context drifts silently between clusters when other sessions switch it.", "fact"),
    ("Fire-and-forget ledger writes: failures logged but never block the pipeline.", "decision"),
    ("At 243K signals the cascade reached a plateau: 37 agents, all green.", "fact"),
    ("LLM probe latency 12-17s per decision on racmaas CPU — fine for async audit.", "fact"),
    ("Severity-gated suppression: medium/high/critical can never be suppressed by learned agents.", "decision"),
    ("Content-hash dedup is always correct regardless of severity.", "decision"),
    ("The trust gap is quantified at 25% — granite challenges drops at that rate.", "fact"),
    ("Compression ranges from 61% (finance cold start) to 99.5% (K8s sustained).", "fact"),
]

CRITICAL_CLAIMS = [
    ("Don't mock the database in these tests — we got burned when mocked tests passed but prod migration failed.", "rule"),
    ("Stop summarizing what you just did at the end of every response.", "rule"),
    ("Cascade is the star — GCL and ledger are supplementary work.", "rule"),
    ("Do not use hivemind in anything.", "rule"),
    ("ALWAYS use infra01 context. Never Oberon.", "rule"),
    ("H100 system cost is $200-400K for full system, not $50K card only.", "fact"),
    ("Throughput numbers in data/ are ESTIMATED — not validated benchmarks.", "caveat"),
    ("Changed from Flask to FastAPI for the ledger gateway.", "decision"),
]


def generate(count: int = 1000, seed: int = 42) -> List[SyntheticMemoryClaim]:
    rng = random.Random(seed)

    noise_count = int(count * 0.55)
    known_count = int(count * 0.25)
    novel_count = int(count * 0.13)
    critical_count = count - noise_count - known_count - novel_count

    events = []
    event_id = 0

    for _ in range(noise_count):
        text, ctype = rng.choice(NOISE_CLAIMS)
        events.append(SyntheticMemoryClaim(
            id=event_id,
            source_system=rng.choice(["claude-memory", "session"]),
            memory_type=rng.choice(["handoff", "park", "standup"]),
            project=rng.choice(PROJECTS),
            claim_text=text,
            claim_type=ctype,
            section=rng.choice(["header", "what's running", "git status"]),
            label="noise",
            label_detail=ctype,
        ))
        event_id += 1

    for _ in range(known_count):
        text = rng.choice(CROSS_PROJECT_CLAIMS)
        events.append(SyntheticMemoryClaim(
            id=event_id,
            source_system="claude-memory",
            memory_type=rng.choice(["project", "feedback", "user"]),
            project=rng.choice(PROJECTS),
            claim_text=text,
            claim_type="rule" if "must" in text.lower() or "never" in text.lower() else "fact",
            section=rng.choice(["opening", "how to apply"]),
            label="known",
            label_detail="cross_project_duplicate",
        ))
        event_id += 1

    for _ in range(novel_count):
        text, ctype = rng.choice(NOVEL_CLAIMS)
        events.append(SyntheticMemoryClaim(
            id=event_id,
            source_system="claude-memory",
            memory_type=rng.choice(["project", "feedback"]),
            project=rng.choice(PROJECTS),
            claim_text=text,
            claim_type=ctype,
            section=rng.choice(["opening", "why"]),
            label="novel",
            label_detail=ctype,
        ))
        event_id += 1

    for _ in range(critical_count):
        text, ctype = rng.choice(CRITICAL_CLAIMS)
        events.append(SyntheticMemoryClaim(
            id=event_id,
            source_system="claude-memory",
            memory_type="feedback",
            project=rng.choice(PROJECTS),
            claim_text=text,
            claim_type=ctype,
            section="opening",
            label="critical",
            label_detail=ctype,
        ))
        event_id += 1

    rng.shuffle(events)
    return events


def label_summary(events: List[SyntheticMemoryClaim]) -> dict:
    by_label = {}
    by_type = {}
    unique_claims = set()
    for e in events:
        by_label[e.label] = by_label.get(e.label, 0) + 1
        by_type[e.claim_type] = by_type.get(e.claim_type, 0) + 1
        unique_claims.add(e.claim_text)
    return {
        "total": len(events),
        "unique_claims": len(unique_claims),
        "duplication_rate": round(1 - len(unique_claims) / max(1, len(events)), 3),
        "by_label": by_label,
        "by_type": by_type,
    }

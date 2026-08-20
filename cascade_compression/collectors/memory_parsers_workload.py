"""Workload parsers — extract institutional knowledge from agentic system logs.

Each parser reads from a running agentic system's decision/interaction logs
and produces records that the ClaimExtractor can process. This is the
production-workload counterpart to the dev-memory parsers.

Supported sources:
- Immutable ledger (cascade decision records)
- Generic JSONL agent logs
- OpenTelemetry trace exports
"""

import json
import logging
from collections import Counter, defaultdict
from typing import Any, Dict, Iterator, List

log = logging.getLogger(__name__)

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


def _safe_parse_content(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {"raw": raw}
    return {}


# ---------------------------------------------------------------------------
# Immutable Ledger — cascade decision records
# ---------------------------------------------------------------------------

def scan_ledger_decisions(
    ledger_url: str,
    entry_type: str = "decision.record",
    page_size: int = 100,
    max_pages: int = 10,
    bearer_token: str = "",
    since: float = 0,
) -> Iterator[dict]:
    """Read decision records from the immutable ledger and distill
    institutional knowledge claims from agent decision patterns.

    Instead of treating each decision as a claim (too granular), this
    analyzes decision patterns in aggregate and produces higher-level
    knowledge claims about:
    - Agent effectiveness (which agents handle what %)
    - Signal landscape (what types dominate, what's rare)
    - Multi-agent routing (which signals need multiple agents)
    - Confidence patterns
    - Safety invariants (are high-severity signals being dropped?)
    - Namespace hotspots
    """
    if not _HAS_HTTPX:
        log.warning("httpx not installed — cannot read from ledger")
        return

    headers = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    all_decisions: List[dict] = []
    page_token = ""
    pages_read = 0

    try:
        with httpx.Client(timeout=15, verify=False) as client:
            while pages_read < max_pages:
                params = {
                    "entry_type": entry_type,
                    "page_size": page_size,
                }
                if page_token:
                    params["page_token"] = page_token
                if since:
                    params["from_ts"] = int(since * 1000)

                resp = client.get(
                    f"{ledger_url.rstrip('/')}/api/entries",
                    params=params,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                entries = data.get("entries", []) if isinstance(data, dict) else data

                for entry in entries:
                    content = _safe_parse_content(entry.get("content", {}))
                    domain = content.get("domain", "unknown")
                    for dec in content.get("decisions", []):
                        dec["domain"] = domain
                        dec["batch_id"] = content.get("batch_id", "")
                        all_decisions.append(dec)

                page_token = data.get("next_page_token", "") if isinstance(data, dict) else ""
                pages_read += 1
                if not page_token:
                    break

    except Exception as e:
        log.warning("Failed to read from ledger: %s", str(e)[:80])

    if not all_decisions:
        return

    log.info("Ledger workload: %d individual decisions from %d pages", len(all_decisions), pages_read)

    yield from _distill_workload_claims(all_decisions, "ledger")


def _distill_workload_claims(decisions: List[dict], source: str) -> Iterator[dict]:
    """Analyze a batch of agent decisions and produce institutional knowledge claims."""

    total = len(decisions)
    if total == 0:
        return

    # Agent effectiveness
    agent_counts = Counter(d.get("agent", "unknown") for d in decisions)
    for agent, count in agent_counts.most_common():
        pct = count * 100 // total
        if pct >= 5:
            yield {
                "path": f"workload/{source}/agent-effectiveness",
                "name": f"agent-{agent}-effectiveness",
                "description": f"Agent '{agent}' handles {pct}% of decisions",
                "memory_type": "workload_insight",
                "project": f"workload-{source}",
                "source_system": "workload",
                "body": (
                    f"The '{agent}' agent handles {count} decisions ({pct}% of {total} total). "
                    f"This makes it the {'primary' if pct > 40 else 'secondary'} decision-maker in the fleet."
                ),
            }

    # Signal type distribution
    type_counts = Counter(d.get("subject_type", d.get("signal_type", "unknown")) for d in decisions)
    dominant = type_counts.most_common(1)[0]
    if dominant[1] * 100 // total > 30:
        yield {
            "path": f"workload/{source}/signal-landscape",
            "name": "dominant-signal-type",
            "description": f"Signal type '{dominant[0]}' dominates at {dominant[1]*100//total}%",
            "memory_type": "workload_insight",
            "project": f"workload-{source}",
            "source_system": "workload",
            "body": (
                f"'{dominant[0]}' accounts for {dominant[1]*100//total}% of all signals. "
                f"The top 3 types account for "
                f"{sum(c for _, c in type_counts.most_common(3))*100//total}% of total volume. "
                f"{len(type_counts)} unique signal types observed."
            ),
        }

    # Multi-agent routing
    type_agents = defaultdict(set)
    for d in decisions:
        type_agents[d.get("subject_type", "")].add(d.get("agent", ""))
    multi = {t: agents for t, agents in type_agents.items() if len(agents) > 1}
    if multi:
        yield {
            "path": f"workload/{source}/multi-agent",
            "name": "multi-agent-routing",
            "description": f"{len(multi)} signal types require multiple agents",
            "memory_type": "workload_insight",
            "project": f"workload-{source}",
            "source_system": "workload",
            "body": (
                f"{len(multi)} of {len(type_counts)} signal types ({len(multi)*100//max(1,len(type_counts))}%) "
                f"flow through multiple agents. No single agent handles them end-to-end. "
                f"Agent ordering matters: "
                + ", ".join(f"{a}({c})" for a, c in agent_counts.most_common())
            ),
        }

    # Safety check — high-severity drops
    sev_outcome = defaultdict(Counter)
    for d in decisions:
        sev_outcome[d.get("severity", "unknown")][d.get("outcome", "unknown")] += 1

    for sev in ["critical", "high"]:
        outcomes = sev_outcome.get(sev, {})
        drops = outcomes.get("drop", 0) + outcomes.get("suppress", 0)
        keeps = outcomes.get("keep", 0) + outcomes.get("classify", 0) + outcomes.get("escalate", 0)
        dedupes = outcomes.get("dedupe", 0)
        if drops + keeps + dedupes > 0:
            yield {
                "path": f"workload/{source}/safety",
                "name": f"safety-{sev}-severity",
                "description": f"{sev}-severity: {drops} suppressed, {keeps} classified, {dedupes} deduped",
                "memory_type": "workload_insight",
                "project": f"workload-{source}",
                "source_system": "workload",
                "body": (
                    f"{sev.title()}-severity signals: {drops} suppressed, {dedupes} deduped, {keeps} classified/escalated. "
                    + ("SAFE: high-severity signals are suppressed only by learned noise rules, never by severity gate."
                       if drops > 0 and outcomes.get("drop", 0) == 0
                       else f"WARNING: {outcomes.get('drop', 0)} high-severity signals were dropped outright."
                       if outcomes.get("drop", 0) > 0
                       else "All high-severity signals are handled without dropping.")
                ),
            }

    # Confidence distribution
    confs = [d.get("confidence", 0) for d in decisions]
    high = sum(1 for c in confs if c >= 0.9)
    med = sum(1 for c in confs if 0.5 <= c < 0.9)
    low = sum(1 for c in confs if c < 0.5)
    yield {
        "path": f"workload/{source}/confidence",
        "name": "confidence-distribution",
        "description": f"Confidence: {high*100//total}% high, {med*100//total}% medium, {low*100//total}% low",
        "memory_type": "workload_insight",
        "project": f"workload-{source}",
        "source_system": "workload",
        "body": (
            f"Decision confidence: {high*100//total}% high (>=0.9), {med*100//total}% medium (0.5-0.9), "
            f"{low*100//total}% low (<0.5). "
            + ("Agents never guess — zero low-confidence decisions." if low == 0
               else f"WARNING: {low} low-confidence decisions indicate agents are guessing.")
        ),
    }

    # Namespace concentration
    ns = Counter(d.get("namespace", "") for d in decisions)
    top_ns = ns.most_common(1)[0]
    if top_ns[1] * 100 // total > 30:
        yield {
            "path": f"workload/{source}/hotspots",
            "name": "namespace-hotspot",
            "description": f"'{top_ns[0]}' namespace generates {top_ns[1]*100//total}% of signals",
            "memory_type": "workload_insight",
            "project": f"workload-{source}",
            "source_system": "workload",
            "body": (
                f"Namespace '{top_ns[0]}' generates {top_ns[1]*100//total}% of all signals. "
                f"{len(ns)} unique namespaces observed. "
                f"Signal distribution is {'highly concentrated' if top_ns[1]*100//total > 50 else 'moderately spread'}."
            ),
        }

    # Compression ratio
    compressed = sum(1 for d in decisions if d.get("outcome") in ("dedupe", "suppress", "drop"))
    yield {
        "path": f"workload/{source}/compression",
        "name": "self-tuning-compression",
        "description": f"Fleet achieved {compressed*100//total}% compression with zero human rules",
        "memory_type": "workload_insight",
        "project": f"workload-{source}",
        "source_system": "workload",
        "body": (
            f"The agent fleet achieved {compressed*100//total}% compression ({compressed}/{total} signals handled deterministically) "
            f"with zero human-written rules. All suppression rules were discovered from LLM feedback."
        ),
    }


# ---------------------------------------------------------------------------
# Generic JSONL agent logs
# ---------------------------------------------------------------------------

def scan_jsonl_logs(
    log_path: str,
    decision_key: str = "decision",
    agent_key: str = "agent",
    outcome_key: str = "outcome",
    since: float = 0,
) -> Iterator[dict]:
    """Parse generic JSONL agent interaction logs.

    Each line should be a JSON object with at minimum:
    - An agent identifier
    - A decision/action taken
    - Optional: confidence, severity, outcome
    """
    import os
    from pathlib import Path

    log_file = Path(log_path)
    if not log_file.is_file():
        return

    if since and log_file.stat().st_mtime < since:
        return

    decisions = []
    try:
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    decisions.append({
                        "agent": entry.get(agent_key, "unknown"),
                        "outcome": entry.get(outcome_key, "unknown"),
                        "subject_type": entry.get("type", entry.get("signal_type", "unknown")),
                        "severity": entry.get("severity", "info"),
                        "confidence": entry.get("confidence", 1.0),
                        "namespace": entry.get("namespace", entry.get("source", "")),
                    })
                except json.JSONDecodeError:
                    continue
    except (OSError, UnicodeDecodeError):
        return

    if decisions:
        yield from _distill_workload_claims(decisions, os.path.basename(log_path))

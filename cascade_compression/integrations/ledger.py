"""Optional immutable ledger integration.

Writes decision records to any immutable ledger via REST gateway.
Fire-and-forget — failures are logged but never block the caller.
Uses the generic decision-record.json schema — not cascade-specific.
"""

import hashlib
import json
import logging
from typing import Any, Dict
from uuid import uuid4

log = logging.getLogger(__name__)


def build_decision_record(
    cascade_result: Any,
    cascade_signals: list,
    system_id: str,
    domain: str,
) -> Dict:
    """Build a generic decision record from cascade results.

    Returns a dict matching contracts/schemas/decision-record.json.
    """
    signal_lookup = {}
    for sig in cascade_signals:
        signal_lookup[sig.signal_id] = sig

    decisions = []
    for decision in getattr(cascade_result, "decisions", []):
        sig = signal_lookup.get(decision.signal_id)
        decisions.append({
            "subject_id": str(decision.signal_id),
            "subject_type": sig.signal_type if sig else "",
            "severity": sig.severity if sig else "",
            "namespace": sig.namespace if sig else "",
            "outcome": decision.outcome.value,
            "agent": decision.agent_name,
            "confidence": decision.confidence,
            "tier": "nano",
            "evidence": decision.evidence[:200] if decision.evidence else "",
        })

    return {
        "system_id": system_id,
        "batch_id": str(uuid4()),
        "domain": domain,
        "decisions": decisions,
    }


def write_decisions(
    ledger_url: str,
    ledger_token: str,
    cascade_result: Any,
    cascade_signals: list,
    domain: str,
) -> None:
    if not ledger_url:
        return

    try:
        import httpx
    except ImportError:
        log.debug("httpx not installed — ledger writes disabled")
        return

    record = build_decision_record(
        cascade_result, cascade_signals,
        system_id=f"cascade-{domain}", domain=domain,
    )

    if not record["decisions"]:
        return

    content = json.dumps(record, sort_keys=True, separators=(",", ":"))
    input_hash = hashlib.sha256(content.encode()).hexdigest()
    correlation_id = record["batch_id"]

    entry = {
        "entry_type": "decision.record",
        "agent_id": record["system_id"],
        "content": content,
        "content_type": "application/json",
        "source_id": record["system_id"],
        "correlation_id": correlation_id,
        "idempotency_key": hashlib.sha256(
            f"decision.record\0{correlation_id}".encode()
        ).hexdigest(),
        "input_hash": input_hash,
    }

    _post_to_ledger(ledger_url, ledger_token, entry)


def write_promotion_event(
    ledger_url: str,
    ledger_token: str,
    event_dict: Dict,
    domain: str,
) -> None:
    """Write an agent promotion/demotion event to the immutable ledger."""
    if not ledger_url:
        return

    try:
        import httpx  # noqa: F811
    except ImportError:
        log.debug("httpx not installed — ledger writes disabled")
        return

    content = json.dumps(event_dict, sort_keys=True, separators=(",", ":"))
    input_hash = hashlib.sha256(content.encode()).hexdigest()
    agent_name = event_dict.get("agent_name", "unknown")
    event_type = event_dict.get("event_type", "unknown")
    correlation_id = f"{agent_name}-{event_type}-{event_dict.get('timestamp', '')}"

    entry = {
        "entry_type": "agent.promotion",
        "agent_id": f"cascade-{domain}",
        "content": content,
        "content_type": "application/json",
        "source_id": f"cascade-{domain}",
        "correlation_id": correlation_id,
        "idempotency_key": hashlib.sha256(
            f"agent.promotion\0{correlation_id}".encode()
        ).hexdigest(),
        "input_hash": input_hash,
    }

    _post_to_ledger(ledger_url, ledger_token, entry)


def write_memory_event(
    ledger_url: str,
    ledger_token: str,
    event_dict: Dict,
    domain: str,
) -> None:
    """Write a memory lifecycle event to the immutable ledger."""
    if not ledger_url:
        return

    try:
        import httpx  # noqa: F811
    except ImportError:
        log.debug("httpx not installed — ledger writes disabled")
        return

    content = json.dumps(event_dict, sort_keys=True, separators=(",", ":"))
    input_hash = hashlib.sha256(content.encode()).hexdigest()
    memory_id = event_dict.get("memory_id", "unknown")
    event_type = event_dict.get("event_type", "unknown")
    correlation_id = f"{memory_id}-{event_type}-{event_dict.get('timestamp', '')}"

    entry = {
        "entry_type": f"memory.{event_type}",
        "agent_id": f"cascade-{domain}",
        "content": content,
        "content_type": "application/json",
        "source_id": f"cascade-{domain}",
        "correlation_id": correlation_id,
        "idempotency_key": hashlib.sha256(
            f"memory.{event_type}\0{correlation_id}".encode()
        ).hexdigest(),
        "input_hash": input_hash,
    }

    _post_to_ledger(ledger_url, ledger_token, entry)


def _post_to_ledger(ledger_url: str, ledger_token: str, entry: Dict) -> None:
    import httpx

    try:
        headers = {"Content-Type": "application/json"}
        if ledger_token:
            headers["Authorization"] = f"Bearer {ledger_token}"

        with httpx.Client(timeout=10) as client:
            response = client.post(
                f"{ledger_url.rstrip('/')}/api/receipts",
                json=entry,
                headers=headers,
            )
            response.raise_for_status()
    except Exception as e:
        log.debug("Ledger write failed: %s", str(e)[:60])

"""Governor collector — governance and policy decisions.

READ-ONLY. API reads only. Never approve/deny/override policies.

Polls the Governor API for policy decisions, approvals, and audit events.
Data access interface TBD — will be discovered when deployed.
"""

import json
import logging
import os
import ssl
from typing import Iterator, List, Optional
from urllib.request import Request, urlopen

from .base import BaseCollector

log = logging.getLogger(__name__)


class GovernorSignal:
    _counter = 0

    def __init__(self, record: dict):
        GovernorSignal._counter += 1
        self.signal_id = GovernorSignal._counter
        self.cluster_id = record.get("cluster", "governor")
        self.namespace = record.get("namespace", "governor")
        self.resource_kind = record.get("kind", "policy")
        self.resource_name = record.get("name", "")
        self.signal_type = record.get("signal_type", "unknown")
        self.severity = record.get("severity", "info")
        self.evidence = {"message": record.get("message", ""), **record}
        self.labels = {"domain": "governor"}


class GovernorCollector(BaseCollector):
    name = "governor"

    def __init__(self):
        self._api_url = ""
        self._token = ""
        self._connected = False

    def connect(self, config: dict) -> bool:
        self._api_url = (
            config.get("api_url", "")
            or os.getenv("GOVERNOR_API_URL", "")
        ).rstrip("/")
        self._token = config.get("token", "") or os.getenv("GOVERNOR_TOKEN", "")
        if not self._api_url:
            log.warning("No GOVERNOR_API_URL configured")
            return False
        data = self._get("/api/health")
        self._connected = data is not None
        if self._connected:
            log.info("Governor connected: %s", self._api_url)
        return self._connected

    def collect(self) -> list:
        signals = []
        decisions = self._get("/api/decisions?limit=50") or []
        items = decisions if isinstance(decisions, list) else decisions.get("results", [])
        for d in items:
            action = d.get("action", "")
            result = d.get("result", "")
            subject = d.get("subject", "")
            reason = d.get("reason", "")[:200]
            name = f"{action}-{subject}"

            if result == "denied":
                signals.append(GovernorSignal({
                    "signal_type": "policy_denied",
                    "severity": "high",
                    "kind": "decision", "name": name,
                    "message": f"Policy denied: {action} on {subject}: {reason}",
                    "action": action, "result": result, "subject": subject,
                }))
            elif result == "override":
                signals.append(GovernorSignal({
                    "signal_type": "policy_override",
                    "severity": "medium",
                    "kind": "decision", "name": name,
                    "message": f"Policy override: {action} on {subject}: {reason}",
                    "action": action, "result": result, "subject": subject,
                }))
            elif result == "approved":
                signals.append(GovernorSignal({
                    "signal_type": "policy_approved",
                    "severity": "info",
                    "kind": "decision", "name": name,
                    "message": f"Policy approved: {action} on {subject}",
                    "action": action, "result": result, "subject": subject,
                }))

        return signals

    def collect_all(self) -> list:
        return self.collect()

    def stream(self) -> Iterator:
        yield from self.collect()

    def describe(self) -> dict:
        return {"name": self.name, "connected": self._connected, "api_url": self._api_url}

    def _get(self, path: str) -> Optional:
        url = f"{self._api_url}{path}"
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            req = Request(url, headers=headers)
            ctx = ssl.create_default_context()
            if os.getenv("GOVERNOR_SSL_VERIFY", "true").lower() == "false":
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            with urlopen(req, timeout=15, context=ctx) as resp:  # nosec B310
                return json.loads(resp.read())
        except Exception as e:
            log.debug("Governor GET %s: %s", path, str(e)[:100])
            return None

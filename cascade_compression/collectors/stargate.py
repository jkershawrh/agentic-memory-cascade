"""Stargate collector — scan results, stuck instances, remediation events.

READ-ONLY. API reads only. Never trigger scans or remediations.

Polls the Stargate API for scan results, instance health, and
pool velocity metrics.
"""

import json
import logging
import os
import ssl
from typing import Iterator, List, Optional
from urllib.request import Request, urlopen

from .base import BaseCollector

log = logging.getLogger(__name__)


class StargateSignal:
    _counter = 0

    def __init__(self, record: dict):
        StargateSignal._counter += 1
        self.signal_id = StargateSignal._counter
        self.cluster_id = record.get("cluster", "stargate")
        self.namespace = record.get("namespace", "stargate")
        self.resource_kind = record.get("kind", "scan")
        self.resource_name = record.get("name", "")
        self.signal_type = record.get("signal_type", "unknown")
        self.severity = record.get("severity", "info")
        self.evidence = {"message": record.get("message", ""), **record}
        self.labels = {"domain": "stargate"}


class StargateCollector(BaseCollector):
    name = "stargate"

    def __init__(self):
        self._api_url = ""
        self._token = ""
        self._connected = False

    def connect(self, config: dict) -> bool:
        self._api_url = (
            config.get("api_url", "")
            or os.getenv("STARGATE_API_URL", "")
        ).rstrip("/")
        self._token = config.get("token", "") or os.getenv("STARGATE_TOKEN", "")
        if not self._api_url:
            log.warning("No STARGATE_API_URL configured")
            return False
        data = self._get("/health")
        self._connected = data is not None
        if self._connected:
            log.info("Stargate connected: %s (status=%s)", self._api_url, data.get("status", "?"))
        return self._connected

    def collect(self) -> list:
        signals = []

        cluster_data = self._get("/dashboard/clusters")
        if cluster_data:
            for cinfo in cluster_data.get("clusters", []):
                if not isinstance(cinfo, dict):
                    continue
                cname = cinfo.get("name", cinfo.get("cluster", "unknown"))
                failing = cinfo.get("failing_sandboxes", 0) or cinfo.get("failing", 0)
                total = cinfo.get("total_sandboxes", 0) or cinfo.get("total", 0)
                if isinstance(failing, int) and failing > 0:
                    signals.append(StargateSignal({
                        "signal_type": "cluster_sandboxes_failing",
                        "severity": "high" if failing > 5 else "medium",
                        "kind": "cluster", "name": cname, "cluster": cname,
                        "message": f"{cname}: {failing}/{total} sandboxes failing",
                        "failing": failing, "total": total,
                    }))

        pools = self._get("/dashboard/pools")
        if pools:
            for pool in pools.get("pools", []):
                pname = pool.get("name", "unknown")
                available = pool.get("available", 0)
                total_handles = pool.get("total", 0)
                if available == 0 and total_handles > 0:
                    signals.append(StargateSignal({
                        "signal_type": "pool_exhausted",
                        "severity": "critical",
                        "kind": "pool", "name": pname,
                        "message": f"Pool {pname} exhausted: 0/{total_handles}",
                        "available": available, "total": total_handles,
                    }))
                elif available <= 1 and total_handles > 0:
                    signals.append(StargateSignal({
                        "signal_type": "pool_low",
                        "severity": "high",
                        "kind": "pool", "name": pname,
                        "message": f"Pool {pname} low: {available}/{total_handles}",
                        "available": available, "total": total_handles,
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
            if os.getenv("STARGATE_SSL_VERIFY", "true").lower() == "false":
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            with urlopen(req, timeout=15, context=ctx) as resp:  # nosec B310
                return json.loads(resp.read())
        except Exception as e:
            log.debug("Stargate GET %s: %s", path, str(e)[:100])
            return None

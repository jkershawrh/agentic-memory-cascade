"""Sandbox-Conan collector — provisioning system metrics and health.

READ-ONLY. Scrapes Prometheus metrics from the sandbox-api service.
Tracks queue depth, DB connections, ratelimit slots per cluster.

Adapted from stargate-platform/collectors/sandbox_api/collect_sandbox_api.py.
"""

import json
import logging
import os
import re
import ssl
from typing import Iterator, List, Optional
from urllib.request import Request, urlopen

from .base import BaseCollector

log = logging.getLogger(__name__)


class SandboxConanSignal:
    _counter = 0

    def __init__(self, record: dict):
        SandboxConanSignal._counter += 1
        self.signal_id = SandboxConanSignal._counter
        self.cluster_id = record.get("cluster", "sandbox")
        self.namespace = record.get("namespace", "babylon-sandbox-api")
        self.resource_kind = "sandbox"
        self.resource_name = record.get("name", "")
        self.signal_type = record.get("signal_type", "unknown")
        self.severity = record.get("severity", "info")
        self.evidence = {"message": record.get("message", ""), **record}
        self.labels = {"domain": "sandbox"}


class SandboxConanCollector(BaseCollector):
    name = "sandbox_conan"

    def __init__(self):
        self._metrics_url = ""
        self._connected = False

    def connect(self, config: dict) -> bool:
        self._metrics_url = (
            config.get("metrics_url", "")
            or os.getenv("SANDBOX_METRICS_URL",
                         "http://sandbox-api.babylon-sandbox-api.svc:8080/metrics")
        ).rstrip("/")
        raw = self._fetch_metrics()
        self._connected = raw is not None
        if self._connected:
            log.info("Sandbox-Conan connected: %s", self._metrics_url)
        return self._connected

    def collect(self) -> list:
        raw = self._fetch_metrics()
        if raw is None:
            return []
        metrics = self._parse_prometheus_text(raw)
        return self._metrics_to_signals(metrics)

    def collect_all(self) -> list:
        return self.collect()

    def stream(self) -> Iterator:
        yield from self.collect()

    def describe(self) -> dict:
        return {"name": self.name, "connected": self._connected, "metrics_url": self._metrics_url}

    def _metrics_to_signals(self, metrics: dict) -> List[SandboxConanSignal]:
        signals = []

        db_active = metrics.get("sandbox_pg_connections_active")
        if isinstance(db_active, (int, float)):
            severity = "high" if db_active > 50 else "medium" if db_active > 30 else "info"
            signals.append(SandboxConanSignal({
                "signal_type": "sandbox_db_connections",
                "severity": severity,
                "name": "pg_connections",
                "message": f"Sandbox DB: {int(db_active)} active connections",
                "db_active": int(db_active),
                "db_idle": int(metrics.get("sandbox_pg_connections_idle", 0)),
            }))

        queue = metrics.get("sandbox_queued_placements_total")
        if isinstance(queue, (int, float)) and queue > 0:
            severity = "critical" if queue > 50 else "high" if queue > 20 else "medium"
            signals.append(SandboxConanSignal({
                "signal_type": "sandbox_queue_depth",
                "severity": severity,
                "name": "queued_placements",
                "message": f"Sandbox queue: {int(queue)} placements queued",
                "queue_depth": int(queue),
            }))

        ratelimit = metrics.get("sandbox_ratelimit_available_slots", [])
        ratelimit_max = metrics.get("sandbox_ratelimit_max", [])
        if isinstance(ratelimit, list):
            max_by_cluster = {}
            if isinstance(ratelimit_max, list):
                max_by_cluster = {e["labels"]["cluster"]: int(e["value"]) for e in ratelimit_max if "cluster" in e.get("labels", {})}
            for entry in ratelimit:
                cluster = entry.get("labels", {}).get("cluster", "")
                avail = int(entry["value"])
                total = max_by_cluster.get(cluster, 0)
                if not cluster:
                    continue
                if avail == 0 and total > 0:
                    signals.append(SandboxConanSignal({
                        "signal_type": "sandbox_ratelimit_exhausted",
                        "severity": "critical",
                        "cluster": cluster,
                        "name": f"ratelimit-{cluster}",
                        "message": f"Ratelimit exhausted on {cluster}: 0/{total} slots",
                        "available": avail, "max": total,
                    }))
                elif avail <= 2 and total > 0:
                    signals.append(SandboxConanSignal({
                        "signal_type": "sandbox_ratelimit_low",
                        "severity": "high",
                        "cluster": cluster,
                        "name": f"ratelimit-{cluster}",
                        "message": f"Ratelimit low on {cluster}: {avail}/{total} slots",
                        "available": avail, "max": total,
                    }))

        return signals

    def _fetch_metrics(self) -> Optional[str]:
        try:
            ctx = ssl.create_default_context()
            if os.getenv("SANDBOX_SSL_VERIFY", "true").lower() == "false":
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            req = Request(self._metrics_url)
            with urlopen(req, timeout=10, context=ctx) as resp:  # nosec B310
                return resp.read().decode()
        except Exception as e:
            log.debug("Sandbox metrics scrape failed: %s", str(e)[:100])
            return None

    @staticmethod
    def _parse_prometheus_text(text: str) -> dict:
        metrics = {}
        for line in text.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            match = re.match(r'^(\w+)(\{(.+)\})?\s+(.+)$', line)
            if not match:
                continue
            name, _, labels_str, value = match.groups()
            try:
                val = float(value)
            except ValueError:
                continue
            if labels_str:
                labels = dict(re.findall(r'(\w+)="([^"]*)"', labels_str))
                metrics.setdefault(name, []).append({"labels": labels, "value": val})
            else:
                metrics[name] = val
        return metrics

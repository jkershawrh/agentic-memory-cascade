"""Poolboy collector — ResourcePool capacity, ResourceClaim lifecycle.

READ-ONLY. Get only. Never create/patch/delete resources.

Polls K8s API for poolboy.gpte.redhat.com CRDs to track resource pool
capacity and claim health across the demo platform.

Field paths adapted from stargate-platform/collectors/poolboy/collect_poolboy.py.
"""

import json
import logging
import os
import ssl
from typing import Iterator, List, Optional
from urllib.request import Request, urlopen

from .base import BaseCollector

log = logging.getLogger(__name__)

API_GROUP = "poolboy.gpte.redhat.com"
API_VERSION = "v1"


class PoolboySignal:
    _counter = 0

    def __init__(self, record: dict):
        PoolboySignal._counter += 1
        self.signal_id = PoolboySignal._counter
        self.cluster_id = record.get("cluster", "poolboy")
        self.namespace = record.get("namespace", "poolboy")
        self.resource_kind = record.get("kind", "ResourcePool")
        self.resource_name = record.get("name", "")
        self.signal_type = record.get("signal_type", "unknown")
        self.severity = record.get("severity", "info")
        self.evidence = {"message": record.get("message", ""), **record}
        self.labels = {"domain": "poolboy"}


class PoolboyCollector(BaseCollector):
    name = "poolboy"

    def __init__(self):
        self._clusters: List[dict] = []
        self._connected = False

    def connect(self, config: dict) -> bool:
        api_url = config.get("api_url", "") or os.getenv("POOLBOY_API_URL", "")
        token = config.get("token", "") or os.getenv("POOLBOY_TOKEN", "")
        if api_url:
            self._clusters = [{"name": "single", "api_url": api_url.rstrip("/"), "token": token}]
        else:
            self._clusters = self._discover_clusters()
        if not self._clusters:
            in_cluster = self._detect_in_cluster()
            if in_cluster:
                self._clusters = [{"name": "local", "api_url": in_cluster, "token": self._load_sa_token()}]
        if not self._clusters:
            log.warning("No K8s API URL for poolboy")
            return False
        working = []
        for c in self._clusters:
            data = self._get(c, f"/apis/{API_GROUP}/{API_VERSION}/resourcepools?limit=1")
            if data is not None:
                working.append(c)
        if working:
            self._clusters = working
            self._connected = True
            log.info("Poolboy connected: %d clusters with CRDs", len(working))
            return True
        log.warning("Poolboy: no clusters have accessible ResourcePool CRDs")
        return False

    def collect(self) -> list:
        signals = []
        for cluster in self._clusters:
            signals.extend(self._collect_pools(cluster))
            signals.extend(self._collect_claims(cluster))
        return signals

    def collect_all(self) -> list:
        return self.collect()

    def stream(self) -> Iterator:
        yield from self.collect()

    def describe(self) -> dict:
        return {"name": self.name, "connected": self._connected, "api_url": self._api_url}

    def _collect_pools(self, cluster: dict = None) -> List[PoolboySignal]:
        signals = []
        cluster = cluster or (self._clusters[0] if self._clusters else {"name": "", "api_url": "", "token": ""})
        data = self._get(cluster, f"/apis/{API_GROUP}/{API_VERSION}/resourcepools")
        if not data:
            return signals

        for item in data.get("items", []):
            meta = item.get("metadata", {})
            spec = item.get("spec", {})
            status = item.get("status", {})
            name = meta.get("name", "unknown")
            ns = meta.get("namespace", "poolboy")
            min_available = spec.get("minAvailable", 0)

            handle_count = status.get("resourceHandleCount", {})
            if isinstance(handle_count, dict):
                available = handle_count.get("available", 0)
                ready = handle_count.get("ready", 0)
            else:
                available = handle_count or 0
                ready = 0

            if available == 0 and min_available > 0:
                signals.append(PoolboySignal({
                    "signal_type": "pool_exhausted",
                    "severity": "critical",
                    "kind": "ResourcePool", "name": name, "namespace": ns,
                    "message": f"ResourcePool {name} exhausted: 0/{min_available} available",
                    "available": available, "min_available": min_available,
                }))
            elif available <= 1 and min_available > 0:
                signals.append(PoolboySignal({
                    "signal_type": "pool_low",
                    "severity": "high",
                    "kind": "ResourcePool", "name": name, "namespace": ns,
                    "message": f"ResourcePool {name} low: {available}/{min_available} available",
                    "available": available, "min_available": min_available,
                }))
            else:
                signals.append(PoolboySignal({
                    "signal_type": "pool_healthy",
                    "severity": "info",
                    "kind": "ResourcePool", "name": name, "namespace": ns,
                    "message": f"ResourcePool {name}: {available}/{min_available} available, {ready} ready",
                    "available": available, "min_available": min_available, "ready": ready,
                }))

        return signals

    def _collect_claims(self, cluster: dict = None) -> List[PoolboySignal]:
        signals = []
        cluster = cluster or (self._clusters[0] if self._clusters else {"name": "", "api_url": "", "token": ""})
        data = self._get(cluster, f"/apis/{API_GROUP}/{API_VERSION}/resourceclaims?limit=500")
        if not data:
            return signals

        for item in data.get("items", []):
            meta = item.get("metadata", {})
            spec = item.get("spec", {})
            status = item.get("status", {})
            name = meta.get("name", "unknown")
            ns = meta.get("namespace", "unknown")
            labels = meta.get("labels", {})

            resources = status.get("resources", [])
            provider = spec.get("provider", {}).get("name", "")

            for r in resources:
                state = r.get("state", {}).get("spec", {}).get("vars", {})
                current = state.get("current_state", "")
                desired = state.get("desired_state", "")
                if current and current != desired and desired:
                    severity = "high" if desired == "started" and current in ("provision-failed", "stop-error") else "medium"
                    signals.append(PoolboySignal({
                        "signal_type": f"claim_{current.replace('-', '_')}",
                        "severity": severity,
                        "kind": "ResourceClaim", "name": name, "namespace": ns,
                        "message": f"ResourceClaim {name}: current={current} desired={desired}",
                        "current_state": current, "desired_state": desired,
                        "provider": provider,
                        "catalog_item": labels.get("babylon.gpte.redhat.com/catalogItemName", ""),
                    }))

        return signals

    @staticmethod
    def _discover_clusters() -> List[dict]:
        clusters = []
        for i in range(1, 20):
            url = os.getenv(f"CLUSTER_{i}_API_URL", "")
            if not url:
                continue
            clusters.append({
                "name": os.getenv(f"CLUSTER_{i}_NAME", f"cluster-{i}"),
                "api_url": url.rstrip("/"),
                "token": os.getenv(f"CLUSTER_{i}_TOKEN", ""),
            })
        return clusters

    def _get(self, cluster: dict, path: str) -> Optional[dict]:
        url = f"{cluster['api_url']}{path}"
        headers = {}
        if cluster.get("token"):
            headers["Authorization"] = f"Bearer {cluster['token']}"
        try:
            req = Request(url, headers=headers)
            ctx = ssl.create_default_context()
            ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
            if os.path.exists(ca_path):
                ctx.load_verify_locations(ca_path)
            try:
                with urlopen(req, timeout=15, context=ctx) as resp:  # nosec B310
                    return json.loads(resp.read())
            except Exception as _ssl_err:
                if "CERTIFICATE_VERIFY_FAILED" not in str(_ssl_err):
                    raise
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urlopen(req, timeout=15, context=ctx) as resp:  # nosec B310
                    return json.loads(resp.read())
        except Exception as e:
            log.debug("Poolboy GET %s: %s", path, str(e)[:100])
            return None

    @staticmethod
    def _load_sa_token() -> str:
        try:
            with open("/var/run/secrets/kubernetes.io/serviceaccount/token") as f:
                return f.read().strip()
        except Exception:
            return ""

    @staticmethod
    def _detect_in_cluster() -> str:
        host = os.getenv("KUBERNETES_SERVICE_HOST", "")
        port = os.getenv("KUBERNETES_SERVICE_PORT", "443")
        if host:
            return f"https://{host}:{port}"
        return ""

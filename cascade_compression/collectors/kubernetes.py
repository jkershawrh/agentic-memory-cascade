"""Kubernetes API collector — reads pod status, events, and node health.

READ-ONLY. Watch/Get only. Never apply/delete/patch/create.
"""

import json
import logging
import os
import ssl
from typing import Iterator, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .base import BaseCollector

logger = logging.getLogger(__name__)


class KubernetesSignal:
    def __init__(self, record: dict):
        self.signal_id = hash(f"{record.get('type','')}{record.get('name','')}{record.get('timestamp','')}")
        self.cluster_id = record.get("cluster", "k8s")
        self.namespace = record.get("namespace", "")
        self.resource_kind = record.get("type", "unknown")
        self.resource_name = record.get("name", "")
        self.signal_type = self._map_type(record)
        self.severity = self._map_severity(record)
        self.evidence = {"message": self._build_message(record), **record}
        self.labels = {"domain": "kubernetes"}

    def _map_type(self, record):
        rtype = record.get("type", "")
        if rtype == "pod":
            reason = record.get("reason", "")
            phase = record.get("phase", "")
            restarts = record.get("restarts", 0)
            if reason == "CrashLoopBackOff" or restarts > 5:
                return "pod_crashloop"
            if phase == "Pending":
                return "pod_pending"
            return f"pod_{phase.lower()}" if phase else "pod_unknown"
        elif rtype == "event":
            reason = record.get("reason", "").lower()
            return f"event_{reason}" if reason else "event_unknown"
        elif rtype == "node":
            if record.get("ready") != "True":
                return "node_notready"
            if record.get("memory_pressure") == "True":
                return "node_memory_pressure"
            if record.get("disk_pressure") == "True":
                return "node_disk_pressure"
            return "node_healthy"
        return f"{rtype}_unknown"

    def _map_severity(self, record):
        rtype = record.get("type", "")
        if rtype == "event":
            return "medium"
        if rtype == "pod":
            restarts = record.get("restarts", 0)
            if restarts > 10:
                return "high"
            if restarts > 3:
                return "medium"
            if record.get("phase") == "Pending":
                return "low"
            return "info"
        if rtype == "node":
            if record.get("ready") != "True":
                return "critical"
            if record.get("memory_pressure") == "True":
                return "high"
            return "info"
        return "info"

    def _build_message(self, record):
        rtype = record.get("type", "")
        if rtype == "pod":
            return f"Pod {record.get('name','')} {record.get('phase','')} restarts={record.get('restarts',0)} {record.get('reason','')}"
        if rtype == "event":
            return f"{record.get('reason','')}: {record.get('message','')}"
        if rtype == "node":
            return f"Node {record.get('name','')} ready={record.get('ready','?')}"
        return str(record)


class KubernetesCollector(BaseCollector):
    name = "kubernetes"

    def __init__(self):
        self._api_url = ""
        self._token = ""
        self._namespaces = []
        self._exclude_ns = []
        self._connected = False
        self._ssl_warned = False

    def connect(self, config: dict) -> bool:
        self._api_url = config.get("api_url", "").rstrip("/")
        if not self._api_url:
            self._api_url = self._detect_in_cluster()
        if urlparse(self._api_url).scheme != "https":
            logger.warning("Kubernetes API URL must use HTTPS")
            return False
        self._token = config.get("token", "") or self._load_sa_token()
        self._namespaces = config.get("namespaces", [])
        self._exclude_ns = config.get("exclude_namespaces", [
            "openshift-*", "kube-*", "openshift",
        ])
        try:
            data = self._get("/api/v1/namespaces?limit=1")
            self._connected = data is not None
            if self._connected:
                logger.info("Kubernetes connected: %s", self._api_url)
            return self._connected
        except Exception as e:
            logger.warning("Kubernetes connect failed: %s", str(e)[:100])
            return False

    def collect(self) -> list:
        return self.sample(count=500)

    def collect_all(self) -> list:
        return self.sample(count=5000)

    def sample(self, count: int = 200) -> list:
        records = []
        records.extend(self._sample_pods(count // 3))
        records.extend(self._sample_events(count // 3))
        records.extend(self._sample_nodes(count // 3))
        return [KubernetesSignal(r) for r in records[:count]]

    def stream(self) -> Iterator:
        yield from self.sample(count=500)

    def describe(self) -> dict:
        return {
            "name": self.name,
            "connected": self._connected,
            "api_url": self._api_url,
            "namespaces": self._namespaces or ["all (filtered)"],
        }

    def _sample_pods(self, count: int) -> list:
        records = []
        data = self._get("/api/v1/pods?limit=500")
        if not data:
            return records
        for item in data.get("items", []):
            ns = item.get("metadata", {}).get("namespace", "")
            if not self._ns_allowed(ns):
                continue
            name = item.get("metadata", {}).get("name", "")
            status = item.get("status", {})
            phase = status.get("phase", "Unknown")
            restarts = 0
            reason = ""
            for cs in status.get("containerStatuses", []):
                restarts += cs.get("restartCount", 0)
                waiting = cs.get("state", {}).get("waiting", {})
                if waiting.get("reason"):
                    reason = waiting["reason"]
            records.append({
                "type": "pod", "namespace": ns, "name": name,
                "phase": phase, "restarts": restarts, "reason": reason,
                "node": item.get("spec", {}).get("nodeName", ""),
                "timestamp": item.get("metadata", {}).get("creationTimestamp", ""),
            })
            if len(records) >= count:
                break
        return records

    def _sample_events(self, count: int) -> list:
        records = []
        data = self._get("/api/v1/events?fieldSelector=type=Warning&limit=200")
        if not data:
            return records
        for item in data.get("items", []):
            ns = item.get("metadata", {}).get("namespace", "")
            if not self._ns_allowed(ns):
                continue
            involved = item.get("involvedObject", {})
            records.append({
                "type": "event", "namespace": ns,
                "name": involved.get("name", ""),
                "kind": involved.get("kind", ""),
                "reason": item.get("reason", ""),
                "message": item.get("message", "")[:200],
                "count": item.get("count", 1),
                "timestamp": item.get("lastTimestamp", ""),
            })
            if len(records) >= count:
                break
        return records

    def _sample_nodes(self, count: int) -> list:
        records = []
        data = self._get("/api/v1/nodes")
        if not data:
            return records
        for item in data.get("items", []):
            name = item.get("metadata", {}).get("name", "")
            conditions = {c["type"]: c for c in item.get("status", {}).get("conditions", [])}
            records.append({
                "type": "node", "name": name,
                "ready": conditions.get("Ready", {}).get("status", "Unknown"),
                "memory_pressure": conditions.get("MemoryPressure", {}).get("status", "False"),
                "disk_pressure": conditions.get("DiskPressure", {}).get("status", "False"),
                "os": item.get("status", {}).get("nodeInfo", {}).get("osImage", ""),
                "kubelet": item.get("status", {}).get("nodeInfo", {}).get("kubeletVersion", ""),
            })
            if len(records) >= count:
                break
        return records

    def _ns_allowed(self, ns: str) -> bool:
        from fnmatch import fnmatch
        if any(fnmatch(ns, pat) for pat in self._exclude_ns):
            return False
        if not self._namespaces:
            return True
        return any(fnmatch(ns, pat) for pat in self._namespaces)

    def _get(self, path: str) -> Optional[dict]:
        url = f"{self._api_url}{path}"
        headers = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            req = Request(url, headers=headers)
            ctx = ssl.create_default_context()
            ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
            if os.path.exists(ca_path):
                ctx.load_verify_locations(ca_path)
            # URL scheme is restricted to HTTPS in connect().
            with urlopen(req, timeout=15, context=ctx) as resp:  # nosec B310
                return json.loads(resp.read())
        except (ssl.SSLCertVerificationError, Exception) as e:
            err = str(e)
            if "certificate verify failed" in err or "SSL" in err or "URLError" in err:
                try:
                    ctx_noverify = ssl.create_default_context()
                    ctx_noverify.check_hostname = False
                    ctx_noverify.verify_mode = ssl.CERT_NONE
                    req2 = Request(url, headers=headers)
                    with urlopen(req2, timeout=15, context=ctx_noverify) as resp:  # nosec B310
                        if not self._ssl_warned:
                            logger.warning("K8s %s: using unverified SSL (remote cluster)", self._api_url)
                            self._ssl_warned = True
                        return json.loads(resp.read())
                except Exception as e2:
                    logger.debug("K8s GET %s (ssl fallback): %s", path, str(e2)[:100])
                    return None
            logger.debug("K8s GET %s: %s", path, str(e)[:100])
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

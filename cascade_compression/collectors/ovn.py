"""OVN networking collector — network events and policy state via K8s API.

READ-ONLY. Get only. Never create/patch/delete network resources.

Polls K8s API for EgressFirewall, EgressIP, NetworkPolicy CRDs and
node network conditions.
"""

import json
import logging
import os
import ssl
from typing import Iterator, List, Optional
from urllib.request import Request, urlopen

from .base import BaseCollector

log = logging.getLogger(__name__)


class OVNSignal:
    _counter = 0

    def __init__(self, record: dict):
        OVNSignal._counter += 1
        self.signal_id = OVNSignal._counter
        self.cluster_id = record.get("cluster", "ovn")
        self.namespace = record.get("namespace", "")
        self.resource_kind = record.get("kind", "NetworkPolicy")
        self.resource_name = record.get("name", "")
        self.signal_type = record.get("signal_type", "unknown")
        self.severity = record.get("severity", "info")
        self.evidence = {"message": record.get("message", ""), **record}
        self.labels = {"domain": "ovn"}


class OVNCollector(BaseCollector):
    name = "ovn"

    def __init__(self):
        self._clusters: List[dict] = []
        self._connected = False

    def connect(self, config: dict) -> bool:
        api_url = config.get("api_url", "") or os.getenv("OVN_API_URL", "")
        token = config.get("token", "") or os.getenv("OVN_TOKEN", "")
        if api_url:
            self._clusters = [{"name": "single", "api_url": api_url.rstrip("/"), "token": token}]
        else:
            self._clusters = self._discover_clusters()
        if not self._clusters:
            in_cluster = self._detect_in_cluster()
            if in_cluster:
                self._clusters = [{"name": "local", "api_url": in_cluster, "token": self._load_sa_token()}]
        if not self._clusters:
            return False
        for c in self._clusters:
            data = self._get(c, "/api/v1/nodes?limit=1")
            if data is not None:
                self._connected = True
                log.info("OVN connected: %s (%d clusters)", c["name"], len(self._clusters))
                return True
        return False

    def collect(self) -> list:
        signals = []
        for cluster in self._clusters:
            signals.extend(self._collect_node_network(cluster))
            signals.extend(self._collect_egress_firewalls(cluster))
            signals.extend(self._collect_network_events(cluster))
        return signals

    def collect_all(self) -> list:
        return self.collect()

    def stream(self) -> Iterator:
        yield from self.collect()

    def describe(self) -> dict:
        return {"name": self.name, "connected": self._connected, "api_url": self._api_url}

    def _collect_node_network(self, cluster: dict = None) -> List[OVNSignal]:
        signals = []
        cluster = cluster or self._clusters[0]
        data = self._get(cluster, "/api/v1/nodes")
        if not data:
            return signals
        for item in data.get("items", []):
            name = item.get("metadata", {}).get("name", "")
            conditions = {c["type"]: c for c in item.get("status", {}).get("conditions", [])}
            net_unavailable = conditions.get("NetworkUnavailable", {})
            if net_unavailable.get("status") == "True":
                signals.append(OVNSignal({
                    "signal_type": "node_network_unavailable",
                    "severity": "critical",
                    "kind": "Node", "name": name,
                    "message": f"Node {name} network unavailable: {net_unavailable.get('message', '')}",
                }))
        return signals

    def _collect_egress_firewalls(self, cluster: dict = None) -> List[OVNSignal]:
        signals = []
        cluster = cluster or self._clusters[0]
        data = self._get(cluster, "/apis/k8s.ovn.org/v1/egressfirewalls")
        if not data:
            return signals
        for item in data.get("items", []):
            meta = item.get("metadata", {})
            status = item.get("status", {})
            name = meta.get("name", "unknown")
            ns = meta.get("namespace", "")
            state = status.get("status", "")
            if state and state != "EgressFirewall Rules applied":
                signals.append(OVNSignal({
                    "signal_type": "egress_firewall_error",
                    "severity": "high",
                    "kind": "EgressFirewall", "name": name, "namespace": ns,
                    "message": f"EgressFirewall {name} in {ns}: {state}",
                }))
        return signals

    def _collect_network_events(self, cluster: dict = None) -> List[OVNSignal]:
        signals = []
        cluster = cluster or self._clusters[0]
        data = self._get(cluster, "/api/v1/events?fieldSelector=reason=NetworkNotReady&limit=50")
        if not data:
            return signals
        for item in data.get("items", []):
            ns = item.get("metadata", {}).get("namespace", "")
            involved = item.get("involvedObject", {})
            signals.append(OVNSignal({
                "signal_type": "network_not_ready",
                "severity": "high",
                "kind": involved.get("kind", ""), "name": involved.get("name", ""),
                "namespace": ns,
                "message": f"NetworkNotReady: {item.get('message', '')[:200]}",
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
            log.debug("OVN GET %s: %s", path, str(e)[:100])
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

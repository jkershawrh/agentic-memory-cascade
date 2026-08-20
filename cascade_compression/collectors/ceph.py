"""Ceph/ODF storage collector — cluster health, pools, OSDs, PVC state.

READ-ONLY. Get only. Never create/patch/delete storage resources.

Polls K8s API for Rook-Ceph and ODF CRDs in openshift-storage namespace
to give the cascade direct visibility into storage root causes (OSD down,
pool full, CephCluster degraded) rather than only downstream K8s symptoms.
"""

import json
import logging
import os
import ssl
from typing import Iterator, List, Optional
from urllib.request import Request, urlopen

from .base import BaseCollector

log = logging.getLogger(__name__)

ROOK_GROUP = "ceph.rook.io"
ROOK_VERSION = "v1"
ODF_GROUP = "ocs.openshift.io"
ODF_VERSION = "v1"
STORAGE_NS = "openshift-storage"


class CephSignal:
    _counter = 0

    def __init__(self, record: dict):
        CephSignal._counter += 1
        self.signal_id = CephSignal._counter
        self.cluster_id = record.get("cluster", "ceph")
        self.namespace = record.get("namespace", STORAGE_NS)
        self.resource_kind = record.get("kind", "CephCluster")
        self.resource_name = record.get("name", "")
        self.signal_type = record.get("signal_type", "unknown")
        self.severity = record.get("severity", "info")
        self.evidence = {"message": record.get("message", ""), **record}
        self.labels = {"domain": "ceph"}


class CephCollector(BaseCollector):
    name = "ceph"

    def __init__(self):
        self._clusters: List[dict] = []
        self._connected = False

    def connect(self, config: dict) -> bool:
        api_url = config.get("api_url", "") or os.getenv("CEPH_API_URL", "")
        token = config.get("token", "") or os.getenv("CEPH_TOKEN", "")
        if api_url:
            self._clusters = [{"name": "single", "api_url": api_url.rstrip("/"), "token": token}]
        else:
            self._clusters = self._discover_clusters()
        if not self._clusters:
            in_cluster = self._detect_in_cluster()
            if in_cluster:
                self._clusters = [{"name": "local", "api_url": in_cluster, "token": self._load_sa_token()}]
        if not self._clusters:
            log.warning("No K8s API URL for ceph")
            return False
        working = []
        for c in self._clusters:
            data = self._get(c, f"/apis/{ROOK_GROUP}/{ROOK_VERSION}/namespaces/{STORAGE_NS}/cephclusters?limit=1")
            if data is not None:
                working.append(c)
        if working:
            self._clusters = working
            self._connected = True
            log.info("Ceph connected: %d clusters with CRDs", len(working))
            return True
        log.warning("Ceph: no clusters have accessible CephCluster CRDs")
        return False

    def collect(self) -> list:
        signals = []
        for cluster in self._clusters:
            signals.extend(self._collect_ceph_clusters(cluster))
            signals.extend(self._collect_block_pools(cluster))
            signals.extend(self._collect_filesystems(cluster))
            signals.extend(self._collect_storage_clusters(cluster))
            signals.extend(self._collect_osd_pods(cluster))
            signals.extend(self._collect_pvc_health(cluster))
        return signals

    def collect_all(self) -> list:
        return self.collect()

    def stream(self) -> Iterator:
        yield from self.collect()

    def describe(self) -> dict:
        return {"name": self.name, "connected": self._connected, "clusters": len(self._clusters)}

    def _collect_ceph_clusters(self, cluster: dict) -> List[CephSignal]:
        signals = []
        data = self._get(cluster, f"/apis/{ROOK_GROUP}/{ROOK_VERSION}/namespaces/{STORAGE_NS}/cephclusters")
        if not data:
            return signals
        for item in data.get("items", []):
            name = item.get("metadata", {}).get("name", "unknown")
            status = item.get("status", {})
            ceph = status.get("ceph", {})
            health = ceph.get("health", "")
            phase = status.get("phase", "")
            details = ceph.get("details", {})

            if health == "HEALTH_ERR":
                detail_msgs = "; ".join(f"{k}: {v.get('message', '')}" for k, v in details.items()) if isinstance(details, dict) else str(details)
                signals.append(CephSignal({
                    "signal_type": "ceph_health_err", "severity": "critical",
                    "kind": "CephCluster", "name": name, "cluster": cluster["name"],
                    "message": f"CephCluster {name} HEALTH_ERR: {detail_msgs[:200]}",
                    "health_details": detail_msgs[:500],
                }))
            elif health == "HEALTH_WARN":
                detail_msgs = "; ".join(f"{k}: {v.get('message', '')}" for k, v in details.items()) if isinstance(details, dict) else str(details)
                signals.append(CephSignal({
                    "signal_type": "ceph_health_warn", "severity": "medium",
                    "kind": "CephCluster", "name": name, "cluster": cluster["name"],
                    "message": f"CephCluster {name} HEALTH_WARN: {detail_msgs[:200]}",
                    "health_details": detail_msgs[:500],
                }))
            else:
                signals.append(CephSignal({
                    "signal_type": "ceph_health_ok", "severity": "info",
                    "kind": "CephCluster", "name": name, "cluster": cluster["name"],
                    "message": f"CephCluster {name} healthy (phase={phase})",
                }))

            if phase == "Error":
                signals.append(CephSignal({
                    "signal_type": "ceph_phase_error", "severity": "critical",
                    "kind": "CephCluster", "name": name, "cluster": cluster["name"],
                    "message": f"CephCluster {name} phase=Error",
                }))
        return signals

    def _collect_block_pools(self, cluster: dict) -> List[CephSignal]:
        signals = []
        data = self._get(cluster, f"/apis/{ROOK_GROUP}/{ROOK_VERSION}/namespaces/{STORAGE_NS}/cephblockpools")
        if not data:
            return signals
        for item in data.get("items", []):
            name = item.get("metadata", {}).get("name", "unknown")
            phase = item.get("status", {}).get("phase", "")
            if phase in ("Failure", "Failed"):
                signals.append(CephSignal({
                    "signal_type": "blockpool_failure", "severity": "critical",
                    "kind": "CephBlockPool", "name": name, "cluster": cluster["name"],
                    "message": f"CephBlockPool {name} phase={phase}",
                }))
            elif phase == "Progressing":
                signals.append(CephSignal({
                    "signal_type": "blockpool_progressing", "severity": "medium",
                    "kind": "CephBlockPool", "name": name, "cluster": cluster["name"],
                    "message": f"CephBlockPool {name} progressing",
                }))
        return signals

    def _collect_filesystems(self, cluster: dict) -> List[CephSignal]:
        signals = []
        data = self._get(cluster, f"/apis/{ROOK_GROUP}/{ROOK_VERSION}/namespaces/{STORAGE_NS}/cephfilesystems")
        if not data:
            return signals
        for item in data.get("items", []):
            name = item.get("metadata", {}).get("name", "unknown")
            phase = item.get("status", {}).get("phase", "")
            if phase in ("Failure", "Failed"):
                signals.append(CephSignal({
                    "signal_type": "cephfs_failure", "severity": "critical",
                    "kind": "CephFilesystem", "name": name, "cluster": cluster["name"],
                    "message": f"CephFilesystem {name} phase={phase}",
                }))
        return signals

    def _collect_storage_clusters(self, cluster: dict) -> List[CephSignal]:
        signals = []
        data = self._get(cluster, f"/apis/{ODF_GROUP}/{ODF_VERSION}/namespaces/{STORAGE_NS}/storageclusters")
        if not data:
            return signals
        for item in data.get("items", []):
            name = item.get("metadata", {}).get("name", "unknown")
            status = item.get("status", {})
            phase = status.get("phase", "")
            conditions = status.get("conditions", [])

            if phase == "Error":
                signals.append(CephSignal({
                    "signal_type": "storagecluster_error", "severity": "critical",
                    "kind": "StorageCluster", "name": name, "cluster": cluster["name"],
                    "message": f"StorageCluster {name} phase=Error",
                }))
            for cond in conditions:
                if cond.get("type") == "Degraded" and cond.get("status") == "True":
                    signals.append(CephSignal({
                        "signal_type": "storagecluster_degraded", "severity": "high",
                        "kind": "StorageCluster", "name": name, "cluster": cluster["name"],
                        "message": f"StorageCluster {name} degraded: {cond.get('message', '')[:200]}",
                    }))
        return signals

    def _collect_osd_pods(self, cluster: dict) -> List[CephSignal]:
        signals = []
        data = self._get(cluster, f"/api/v1/namespaces/{STORAGE_NS}/pods?labelSelector=app=rook-ceph-osd")
        if not data:
            return signals
        for item in data.get("items", []):
            name = item.get("metadata", {}).get("name", "unknown")
            phase = item.get("status", {}).get("phase", "")
            containers = item.get("status", {}).get("containerStatuses", [])

            restart_count = sum(c.get("restartCount", 0) for c in containers)
            waiting = any(
                c.get("state", {}).get("waiting", {}).get("reason", "") == "CrashLoopBackOff"
                for c in containers
            )

            if waiting:
                signals.append(CephSignal({
                    "signal_type": "osd_crashloop", "severity": "critical",
                    "kind": "Pod", "name": name, "cluster": cluster["name"],
                    "message": f"OSD {name} CrashLoopBackOff (restarts={restart_count})",
                    "restart_count": restart_count,
                }))
            elif phase != "Running":
                signals.append(CephSignal({
                    "signal_type": "osd_not_running", "severity": "critical",
                    "kind": "Pod", "name": name, "cluster": cluster["name"],
                    "message": f"OSD {name} phase={phase}",
                }))
            elif restart_count > 3:
                signals.append(CephSignal({
                    "signal_type": "osd_high_restarts", "severity": "high",
                    "kind": "Pod", "name": name, "cluster": cluster["name"],
                    "message": f"OSD {name} restarts={restart_count}",
                    "restart_count": restart_count,
                }))
        return signals

    def _collect_pvc_health(self, cluster: dict) -> List[CephSignal]:
        signals = []
        data = self._get(cluster, "/api/v1/persistentvolumeclaims?limit=500")
        if data:
            for item in data.get("items", []):
                name = item.get("metadata", {}).get("name", "unknown")
                ns = item.get("metadata", {}).get("namespace", "")
                phase = item.get("status", {}).get("phase", "")
                sc = item.get("spec", {}).get("storageClassName", "")
                if phase == "Pending":
                    signals.append(CephSignal({
                        "signal_type": "pvc_pending", "severity": "medium",
                        "kind": "PersistentVolumeClaim", "name": name,
                        "namespace": ns, "cluster": cluster["name"],
                        "message": f"PVC {ns}/{name} pending (storageClass={sc})",
                        "storage_class": sc,
                    }))
                elif phase == "Lost":
                    signals.append(CephSignal({
                        "signal_type": "pvc_lost", "severity": "critical",
                        "kind": "PersistentVolumeClaim", "name": name,
                        "namespace": ns, "cluster": cluster["name"],
                        "message": f"PVC {ns}/{name} LOST",
                        "storage_class": sc,
                    }))

        pv_data = self._get(cluster, "/api/v1/persistentvolumes?limit=500")
        if pv_data:
            for item in pv_data.get("items", []):
                name = item.get("metadata", {}).get("name", "unknown")
                phase = item.get("status", {}).get("phase", "")
                if phase == "Failed":
                    signals.append(CephSignal({
                        "signal_type": "pv_failed", "severity": "critical",
                        "kind": "PersistentVolume", "name": name, "cluster": cluster["name"],
                        "message": f"PV {name} phase=Failed",
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
            log.debug("Ceph GET %s: %s", path, str(e)[:100])
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

"""Babylon/Anarchy collector — orchestration lifecycle via K8s CRDs.

READ-ONLY. Get only. Never create/patch/delete AnarchySubjects.

Polls AnarchySubject CRDs for provisioning state, failures, and error
conditions. Tracks the orchestration layer that drives sandbox lifecycle.

CRD field paths adapted from stargate-platform/collectors/babylon/collect_anarchy_state.py.
"""

import json
import logging
import os
import ssl
from typing import Iterator, List, Optional
from urllib.request import Request, urlopen

from .base import BaseCollector

log = logging.getLogger(__name__)


class BabylonSignal:
    _counter = 0

    def __init__(self, record: dict):
        BabylonSignal._counter += 1
        self.signal_id = BabylonSignal._counter
        self.cluster_id = record.get("cluster", "babylon")
        self.namespace = record.get("namespace", "")
        self.resource_kind = record.get("kind", "AnarchySubject")
        self.resource_name = record.get("name", "")
        self.signal_type = record.get("signal_type", "unknown")
        self.severity = record.get("severity", "info")
        self.evidence = {"message": record.get("message", ""), **record}
        self.labels = {"domain": "babylon"}


class BabylonCollector(BaseCollector):
    name = "babylon"

    def __init__(self):
        self._clusters: List[dict] = []
        self._connected = False

    def connect(self, config: dict) -> bool:
        api_url = config.get("api_url", "") or os.getenv("BABYLON_API_URL", "")
        token = config.get("token", "") or os.getenv("BABYLON_TOKEN", "")
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
        working = []
        for c in self._clusters:
            data = self._get(c, "/apis/anarchy.gpte.redhat.com/v1/anarchysubjects?limit=1")
            if data is not None:
                working.append(c)
        if working:
            self._clusters = working
            self._connected = True
            log.info("Babylon connected: %d clusters with CRDs", len(working))
            return True
        log.warning("Babylon: no clusters have accessible AnarchySubject CRDs")
        return False

    def collect(self) -> list:
        signals = []
        for cluster in self._clusters:
            signals.extend(self._collect_subjects(cluster))
            signals.extend(self._collect_actions(cluster))
        return signals

    def collect_all(self) -> list:
        return self.collect()

    def stream(self) -> Iterator:
        yield from self.collect()

    def describe(self) -> dict:
        return {"name": self.name, "connected": self._connected, "api_url": self._api_url}

    def _collect_subjects(self, cluster: dict = None) -> List[BabylonSignal]:
        signals = []
        cluster = cluster or self._clusters[0]
        data = self._get(cluster, "/apis/anarchy.gpte.redhat.com/v1/anarchysubjects?limit=500")
        if not data:
            return signals

        for item in data.get("items", []):
            meta = item.get("metadata", {})
            status = item.get("status", {})
            spec = item.get("spec", {})
            name = meta.get("name", "unknown")
            ns = meta.get("namespace", "unknown")

            state = (
                status.get("state")
                or spec.get("vars", {}).get("current_state")
                or "unknown"
            )
            run_status = status.get("runStatus", "")
            has_error = any(
                c.get("status") == "True" and "error" in c.get("type", "").lower()
                for c in status.get("conditions", [])
            )
            if not status.get("conditions") and run_status == "failed":
                has_error = True

            tower_jobs = status.get("towerJobs", {})
            provision_ok = self._check_provision(tower_jobs)

            if has_error or "failed" in state:
                signals.append(BabylonSignal({
                    "signal_type": "anarchy_subject_failed",
                    "severity": "high",
                    "kind": "AnarchySubject", "name": name, "namespace": ns,
                    "message": f"AnarchySubject {name} failed: state={state} runStatus={run_status}",
                    "current_state": state, "run_status": run_status,
                }))
            elif state == "started" and provision_ok:
                signals.append(BabylonSignal({
                    "signal_type": "anarchy_subject_started",
                    "severity": "info",
                    "kind": "AnarchySubject", "name": name, "namespace": ns,
                    "message": f"AnarchySubject {name} started successfully",
                    "current_state": state,
                }))
            elif state not in ("started", "stopped", "unknown"):
                signals.append(BabylonSignal({
                    "signal_type": f"anarchy_subject_{state.replace('-', '_')}",
                    "severity": "medium" if "provision" in state else "low",
                    "kind": "AnarchySubject", "name": name, "namespace": ns,
                    "message": f"AnarchySubject {name}: state={state}",
                    "current_state": state,
                }))

        return signals

    def _collect_actions(self, cluster: dict = None) -> List[BabylonSignal]:
        signals = []
        cluster = cluster or self._clusters[0]
        data = self._get(cluster, "/apis/anarchy.gpte.redhat.com/v1/anarchyactions?limit=200")
        if not data:
            return signals

        for item in data.get("items", []):
            meta = item.get("metadata", {})
            spec = item.get("spec", {})
            status = item.get("status", {})
            name = meta.get("name", "unknown")
            ns = meta.get("namespace", "unknown")
            action = spec.get("action", "")
            state = status.get("state", "")

            if state == "failed":
                signals.append(BabylonSignal({
                    "signal_type": "anarchy_action_failed",
                    "severity": "high",
                    "kind": "AnarchyAction", "name": name, "namespace": ns,
                    "message": f"AnarchyAction {name} failed: action={action}",
                    "action": action,
                }))

        return signals

    @staticmethod
    def _check_provision(tower_jobs: dict) -> bool:
        jobs = tower_jobs.get("provision", tower_jobs.get("start", {}))
        if isinstance(jobs, dict):
            return bool(jobs.get("completeTimestamp"))
        if isinstance(jobs, list):
            return any(
                j.get("status") == "successful" or bool(j.get("completeTimestamp"))
                for j in jobs
            )
        return False

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
            log.debug("Babylon GET %s: %s", path, str(e)[:100])
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

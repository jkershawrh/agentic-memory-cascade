"""Signal collector sidecar — polls a data source and POSTs to a cascade service.

Runs as a standalone process or CronJob alongside a cascade deployment.
Collects signals from K8s clusters or AAP, converts them to the cascade
SignalInput format, and POSTs batches to the /cascade endpoint.

Usage:
    # K8s collector — polls 5 clusters, pushes to cascade-k8s
    python -m cascade_compression.collector_sidecar \
        --mode k8s \
        --target http://cascade-k8s:8090 \
        --interval 30

    # AAP collector — polls AAP database, pushes to cascade-aap
    python -m cascade_compression.collector_sidecar \
        --mode aap \
        --target http://cascade-aap:8090 \
        --interval 30

Environment variables (K8s mode):
    CLUSTER_N_NAME       Cluster name
    CLUSTER_N_API_URL    Cluster API URL
    CLUSTER_N_TOKEN      Cluster auth token
    CLUSTER_N_INCLUDE_NS Namespace include pattern (default: *)
    CLUSTER_N_EXCLUDE_NS Namespace exclude pattern (default: openshift-*,kube-*)

Environment variables (AAP mode):
    AAP_DB_HOST          AAP controller database host
    AAP_DB_PORT          Database port (default: 5432)
    AAP_DB_USER          Database user
    AAP_DB_PASS          Database password
    AAP_DB_NAME          Database name (default: automationcontroller)
"""

import argparse
import decimal
import json
import logging
import os
import time
from urllib.request import Request, urlopen


class _DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return float(o)
        return super().default(o)

log = logging.getLogger(__name__)


def _post_signals(target_url: str, signals: list) -> dict:
    batch = []
    for sig in signals:
        batch.append({
            "signal_type": getattr(sig, "signal_type", ""),
            "severity": getattr(sig, "severity", "info"),
            "source": getattr(sig, "resource_name", ""),
            "namespace": getattr(sig, "namespace", ""),
            "content": getattr(sig, "evidence", {}),
            "labels": getattr(sig, "labels", {}),
        })
    if not batch:
        return {"total": 0}
    payload = json.dumps({"signals": batch}, cls=_DecimalEncoder).encode()
    req = Request(
        f"{target_url.rstrip('/')}/cascade",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.error("POST to %s failed: %s", target_url, str(e)[:100])
        return {"error": str(e)[:100]}


def _discover_clusters() -> list:
    clusters = []
    for i in range(1, 20):
        name = os.getenv(f"CLUSTER_{i}_NAME", "")
        url = os.getenv(f"CLUSTER_{i}_API_URL", "")
        token = os.getenv(f"CLUSTER_{i}_TOKEN", "")
        if not url:
            continue
        include = os.getenv(f"CLUSTER_{i}_INCLUDE_NS", "*")
        exclude = os.getenv(f"CLUSTER_{i}_EXCLUDE_NS", "openshift-*,kube-*,openshift")
        clusters.append({
            "name": name or f"cluster-{i}",
            "api_url": url,
            "token": token,
            "namespaces": [ns.strip() for ns in include.split(",") if ns.strip() != "*"],
            "exclude_namespaces": [ns.strip() for ns in exclude.split(",")],
        })
    return clusters


def run_k8s_collector(target_url: str, interval: int):
    from .collectors.kubernetes import KubernetesCollector

    clusters = _discover_clusters()
    if not clusters:
        log.error("No clusters configured (CLUSTER_N_API_URL env vars)")
        return

    log.info("K8s collector: %d clusters, target=%s, interval=%ds",
             len(clusters), target_url, interval)

    collectors = []
    for cfg in clusters:
        c = KubernetesCollector()
        if c.connect(cfg):
            collectors.append((cfg["name"], c))
            log.info("  Connected: %s (%s)", cfg["name"], cfg["api_url"])
        else:
            log.warning("  Failed: %s (%s)", cfg["name"], cfg["api_url"])

    if not collectors:
        log.error("No clusters connected")
        return

    cycle = 0
    while True:
        cycle += 1
        total_signals = 0
        for name, collector in collectors:
            try:
                signals = collector.collect()
                if signals:
                    result = _post_signals(target_url, signals)
                    total_signals += len(signals)
                    log.info("  [%s] %d signals → %s", name, len(signals),
                             json.dumps(result)[:200])
            except Exception as e:
                log.error("  [%s] error: %s", name, str(e)[:100])

        log.info("Cycle %d: %d total signals from %d clusters",
                 cycle, total_signals, len(collectors))
        time.sleep(interval)


def run_aap_collector(target_url: str, interval: int):
    from .collectors.aap import AAPCollector

    collector = AAPCollector(poll_interval=interval)
    config = {
        "db_url": f"postgresql://{os.getenv('AAP_DB_USER', '')}:{os.getenv('AAP_DB_PASS', '')}@"
                  f"{os.getenv('AAP_DB_HOST', '')}:{os.getenv('AAP_DB_PORT', '5432')}/"
                  f"{os.getenv('AAP_DB_NAME', 'automationcontroller')}",
    }

    if not collector.connect(config):
        log.error("AAP collector: failed to connect to database")
        return

    log.info("AAP collector: target=%s, interval=%ds", target_url, interval)

    # Set start offsets if configured
    start_job = int(os.getenv("AAP_START_JOB_ID", "0"))
    start_event = int(os.getenv("AAP_START_EVENT_ID", "0"))
    start_activity = int(os.getenv("AAP_START_ACTIVITY_ID", "0"))
    if start_job:
        collector._last_job_id = start_job
    if start_event:
        collector._last_event_id = start_event
    if start_activity:
        collector._last_activity_id = start_activity

    cycle = 0
    while True:
        cycle += 1
        try:
            signals = collector.collect()
            if signals:
                result = _post_signals(target_url, signals)
                log.info("Cycle %d: %d signals → %s",
                         cycle, len(signals), json.dumps(result)[:200])
            else:
                log.debug("Cycle %d: no new signals", cycle)
        except Exception as e:
            log.error("Cycle %d error: %s", cycle, str(e)[:100])
        time.sleep(interval)


def run_macos_collector(target_url: str, interval: int):
    from .collectors.macos import MacOSCollector

    collector = MacOSCollector()
    log.info("macOS collector: target=%s, interval=%ds, watching %d repos",
             target_url, interval, len(collector._work_dirs))
    for d in collector._work_dirs:
        log.info("  Repo: %s", d)

    cycle = 0
    while True:
        cycle += 1
        try:
            signals = collector.collect()
            if signals:
                result = _post_signals(target_url, signals)
                non_info = [s for s in signals if s.severity != "info"]
                log.info("Cycle %d: %d signals (%d notable) → %s",
                         cycle, len(signals), len(non_info),
                         json.dumps(result)[:200])
            else:
                log.debug("Cycle %d: no signals", cycle)
        except Exception as e:
            log.error("Cycle %d error: %s", cycle, str(e)[:100])
        time.sleep(interval)


def _run_generic_collector(collector, target_url: str, interval: int, name: str):
    """Generic poll loop for any BaseCollector implementation."""
    log.info("%s collector: target=%s, interval=%ds", name, target_url, interval)
    cycle = 0
    while True:
        cycle += 1
        try:
            signals = collector.collect()
            if signals:
                result = _post_signals(target_url, signals)
                log.info("Cycle %d: %d signals → %s",
                         cycle, len(signals), json.dumps(result)[:200])
            else:
                log.debug("Cycle %d: no signals", cycle)
        except Exception as e:
            log.error("Cycle %d error: %s", cycle, str(e)[:100])
        time.sleep(interval)


_COLLECTOR_REGISTRY = {
    "prometheus": ("cascade_compression.collectors.prometheus", "PrometheusCollector"),
    "poolboy": ("cascade_compression.collectors.poolboy", "PoolboyCollector"),
    "sandbox_conan": ("cascade_compression.collectors.sandbox_conan", "SandboxConanCollector"),
    "babylon": ("cascade_compression.collectors.babylon", "BabylonCollector"),
    "gitops": ("cascade_compression.collectors.gitops", "GitOpsCollector"),
    "agnosticv": ("cascade_compression.collectors.agnosticv", "AgnosticVCollector"),
    "stargate": ("cascade_compression.collectors.stargate", "StargateCollector"),
    "ovn": ("cascade_compression.collectors.ovn", "OVNCollector"),
    "ceph": ("cascade_compression.collectors.ceph", "CephCollector"),
    "governor": ("cascade_compression.collectors.governor", "GovernorCollector"),
    "labagator": ("cascade_compression.collectors.labagator", "LabagatorCollector"),
    "jira": ("cascade_compression.collectors.jira", "JiraCollector"),
    "git": ("cascade_compression.collectors.git", "GitCollector"),
    "confluence": ("cascade_compression.collectors.confluence", "ConfluenceCollector"),
}


def run_registered_collector(mode: str, target_url: str, interval: int):
    """Instantiate and run a collector from the registry."""
    module_path, class_name = _COLLECTOR_REGISTRY[mode]
    import importlib
    module = importlib.import_module(module_path)
    collector_cls = getattr(module, class_name)
    collector = collector_cls()
    config = {}
    if hasattr(collector, "connect"):
        if not collector.connect(config):
            log.warning("%s collector failed to connect — will retry each cycle", mode)
    _run_generic_collector(collector, target_url, interval, mode)


def main():
    parser = argparse.ArgumentParser(description="Cascade signal collector sidecar")
    _builtin_modes = ["k8s", "aap", "macos"]
    _all_modes = _builtin_modes + sorted(_COLLECTOR_REGISTRY.keys())
    parser.add_argument("--mode", required=True,
                        choices=_all_modes,
                        help="Collector mode")
    parser.add_argument("--target", required=True,
                        help="Cascade service URL (e.g. http://cascade-k8s:8090)")
    parser.add_argument("--interval", type=int, default=30,
                        help="Poll interval in seconds (default: 30)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.mode == "k8s":
        run_k8s_collector(args.target, args.interval)
    elif args.mode == "aap":
        run_aap_collector(args.target, args.interval)
    elif args.mode == "macos":
        run_macos_collector(args.target, args.interval)
    elif args.mode in _COLLECTOR_REGISTRY:
        run_registered_collector(args.mode, args.target, args.interval)


if __name__ == "__main__":
    main()

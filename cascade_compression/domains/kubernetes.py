"""Kubernetes domain pack."""

from cascade_compression.collectors.kubernetes import KubernetesCollector

DOMAIN = "kubernetes"
COLLECTOR_CLASS = KubernetesCollector

SYSTEM_PROMPT = """You are classifying Kubernetes signals on a platform where student/sandbox namespaces frequently have crashlooping pods, resource issues, and misconfigurations. This is NORMAL for this environment.

Classify as exactly one of:
- routine_noise: expected on this platform, safe to suppress
- known_pattern: seen before, low priority
- needs_attention: unusual for this environment, investigate
- real_incident: critical, affects platform stability

Answer with one word only."""


def _k8s_entity_extractor(sig):
    """Entity key: node:source:cluster or namespace:cluster."""
    if sig.source:
        return f"node:{sig.source}:{sig.cluster}" if sig.cluster else f"node:{sig.source}"
    if sig.namespace and sig.cluster:
        return f"ns:{sig.namespace}:{sig.cluster}"
    return None


MEMORY_CONFIG = {
    "entity_extractor": _k8s_entity_extractor,

    "causal_rules": [
        ("node_disk_pressure", "event_provisioningfailed"),
        ("node_disk_pressure", "event_volumefaileddelete"),
        ("node_memory_pressure", "event_unhealthy"),
        ("node_memory_pressure", "pod_crashloop"),
        ("event_claimmisbound", "event_volumefaileddelete"),
        ("event_claimmisbound", "event_failedattachvolume"),
        ("event_failedscheduling", "pod_pending"),
        ("node_notready", "event_failedscheduling"),
        ("node_notready", "pod_pending"),
        ("event_backofflimitexceeded", "pod_crashloop"),
        ("metric_node_disk_pressure", "event_provisioningfailed"),
        ("metric_node_disk_pressure", "event_volumefaileddelete"),
        ("metric_node_memory_pressure", "event_unhealthy"),
        ("metric_node_memory_exhausted", "event_unhealthy"),
        ("metric_node_notready", "event_failedscheduling"),
        ("metric_node_cpu_saturated", "metric_api_server_slow"),
        ("metric_etcd_slow_commits", "metric_api_server_slow"),
        ("pool_exhausted", "anarchy_subject_failed"),
        ("pool_low", "anarchy_subject_failed"),
        ("sync_failed", "app_degraded"),
        ("ceph_health_err", "blockpool_failure"),
        ("ceph_health_err", "cephfs_failure"),
        ("ceph_health_err", "pvc_pending"),
        ("ceph_health_err", "pvc_lost"),
        ("ceph_health_warn", "pvc_pending"),
        ("blockpool_failure", "pvc_pending"),
        ("blockpool_failure", "event_failedattachvolume"),
        ("osd_crashloop", "ceph_health_err"),
        ("osd_not_running", "ceph_health_err"),
        ("metric_osd_down", "ceph_health_err"),
        ("metric_osd_down", "pvc_pending"),
        ("metric_ceph_pool_near_full", "ceph_health_warn"),
        ("metric_osd_near_full", "ceph_health_warn"),
        ("storagecluster_error", "ceph_health_err"),
        ("pvc_lost", "event_volumefaileddelete"),
        ("pv_failed", "event_failedattachvolume"),
    ],

    "decay_overrides": {
        "event_deprecatedannotation": 0.05,
        "event_ipaddresswrongreference": 0.03,
        "node_healthy": 0.1,
        "node_notready": 0.001,
        "pod_crashloop": 0.005,
        "event_claimmisbound": 0.005,
        "ceph_health_ok": 0.1,
        "blockpool_healthy": 0.1,
        "cephfs_healthy": 0.1,
        "ceph_health_err": 0.001,
        "osd_crashloop": 0.001,
        "pvc_lost": 0.002,
    },

    "expected_signals": [],
}

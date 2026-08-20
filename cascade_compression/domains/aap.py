"""Ansible Automation Platform domain pack."""

from cascade_compression.collectors.aap import AAPCollector

DOMAIN = "aap"
COLLECTOR_CLASS = AAPCollector

SYSTEM_PROMPT = """You are classifying Ansible Automation Platform (AAP) signals from three sources: playbook task events, job outcomes, and configuration changes.

Context: Infrastructure automation platform running scheduled operations, sandbox provisioning, and monitoring. Most tasks succeed routinely.

Classify as exactly one of: routine_noise, known_pattern, needs_attention, real_incident. Answer with one word only."""


def _aap_entity_extractor(sig):
    """Entity key: job:name or config:object."""
    source = sig.labels.get("source", "")
    if source == "job":
        return f"job:{sig.namespace}:{sig.source}" if sig.source else None
    if source == "task":
        job_id = sig.content.get("job_id", "")
        return f"job-run:{job_id}" if job_id else None
    if source == "activity":
        return f"config:{sig.namespace}"
    return None


MEMORY_CONFIG = {
    "entity_extractor": _aap_entity_extractor,

    "causal_rules": [
        ("config_update_credential", "job_failed"),
        ("config_update_inventory", "job_failed"),
        ("config_delete_credential", "job_failed"),
        ("config_delete_inventory", "job_failed"),
        ("task_runner_on_failed", "job_failed"),
        ("task_runner_on_unreachable", "job_failed"),
        ("job_failed", "job_error"),
    ],

    "decay_overrides": {
        "task_runner_on_skipped": 0.1,
        "task_verbose": 0.1,
        "task_runner_on_ok": 0.1,
        "job_successful": 0.08,
        "config_update_credential": 0.001,
        "config_delete_credential": 0.001,
        "config_update_inventory": 0.005,
        "job_failed": 0.005,
        "task_runner_on_failed": 0.005,
    },

    "expected_signals": [],
}

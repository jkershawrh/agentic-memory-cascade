"""macOS personal activity domain pack."""

from cascade_compression.collectors.macos import MacOSCollector

DOMAIN = "macos"
COLLECTOR_CLASS = MacOSCollector

SYSTEM_PROMPT = """You are classifying personal work activity signals from a macOS developer workstation. Signals include app focus changes, git commits, file changes, system load, and network activity.

Context: Software engineer working on AI infrastructure projects. Most activity is routine — editing code, switching between IDE and terminal, running tests.

Classify as exactly one of:
- routine_noise: expected work pattern, normal app switching, routine system metrics
- known_pattern: recognized work rhythm (morning coding, afternoon meetings)
- needs_attention: unusual pattern break (excessive context switching, no commits for hours, high system load)
- real_incident: significant anomaly (system resource exhaustion, unusually long focus break, crash indicators)

Answer with one word only."""


def _macos_entity_extractor(sig):
    """Entity key: app name or repo name."""
    category = sig.labels.get("category", "")
    if category == "focus":
        app = sig.content.get("app", "")
        return f"app:{app}" if app else None
    if category == "code":
        repo = sig.content.get("repo", "")
        return f"repo:{repo}" if repo else None
    if category == "system":
        return f"system:{sig.source or 'local'}"
    return None


MEMORY_CONFIG = {
    "entity_extractor": _macos_entity_extractor,

    "causal_rules": [
        ("high_context_switching", "git_large_uncommitted"),
        ("high_cpu_process", "system_load"),
        ("high_file_churn", "git_commits"),
    ],

    "decay_overrides": {
        "app_focus_end": 0.1,
        "system_load": 0.08,
        "memory_usage": 0.08,
        "network_connections": 0.1,
        "high_context_switching": 0.01,
        "git_commits": 0.02,
        "git_large_uncommitted": 0.005,
        "high_file_churn": 0.02,
        "high_cpu_process": 0.03,
    },

    "expected_signals": [],
}

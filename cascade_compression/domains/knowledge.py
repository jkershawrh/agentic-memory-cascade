"""Organizational knowledge domain pack.

Discovers institutional knowledge patterns from organizational signals:
meeting summaries, runbook edits, incident patterns, Slack messages,
git commit patterns, onboarding questions, expertise concentration.

The cascade compresses routine organizational noise (status updates,
scheduled meetings, regular commits) and surfaces the signals that
indicate knowledge gaps, expertise concentration, process decay,
and undocumented tribal knowledge.
"""

DOMAIN = "knowledge"

SYSTEM_PROMPT = """You are classifying organizational knowledge signals — events from meetings, documentation, incidents, code reviews, and team communication.

Classify as exactly one of:
- routine_noise: normal organizational activity (status update, scheduled meeting, regular commit, routine PR review)
- known_pattern: recurring organizational event, low priority (weekly standup, sprint review, regular deploy)
- needs_attention: knowledge gap indicator (same question asked multiple times, runbook edited repeatedly, decision revisited, expertise concentrated in one person)
- real_incident: critical knowledge risk (key person departing with undocumented expertise, process failing with no runbook, same incident recurring with no learning)

Answer with one word only."""


def _knowledge_entity_extractor(sig):
    """Entity key: team:person or topic:area."""
    labels = sig.labels if sig.labels else {}
    person = labels.get("person", "")
    team = labels.get("team", "")
    topic = labels.get("topic", "")
    if person and team:
        return f"person:{person}@{team}"
    if topic:
        return f"topic:{topic}"
    if person:
        return f"person:{person}"
    return None


MEMORY_CONFIG = {
    "entity_extractor": _knowledge_entity_extractor,

    "causal_rules": [
        ("expertise_departure", "knowledge_gap"),
        ("expertise_departure", "escalation_repeat"),
        ("expertise_concentration", "expertise_departure"),
        ("documentation_gap", "onboarding_delay"),
        ("documentation_gap", "escalation_repeat"),
        ("runbook_decay", "incident_repeat"),
        ("incident_repeat", "process_workaround"),
        ("process_workaround", "runbook_decay"),
        ("decision_revisited", "architecture_drift"),
        ("meeting_recurring_topic", "decision_revisited"),
        ("code_review_repeat", "documentation_gap"),
        ("hotfix_pattern", "incident_repeat"),
        ("onboarding_question", "documentation_gap"),
    ],

    "decay_overrides": {
        "meeting_summary": 0.05,
        "status_update": 0.1,
        "regular_commit": 0.08,
        "sprint_review": 0.05,
        "expertise_departure": 0.001,
        "knowledge_gap": 0.002,
        "incident_repeat": 0.003,
        "decision_revisited": 0.005,
        "expertise_concentration": 0.002,
        "documentation_gap": 0.003,
        "process_workaround": 0.005,
    },

    "expected_signals": [],
}

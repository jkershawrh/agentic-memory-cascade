"""Synthetic organizational knowledge signal generator.

Generates realistic organizational signals: meeting summaries, runbook edits,
incident patterns, code reviews, onboarding questions, expertise events.

Distribution based on typical engineering org patterns:
- ~85% routine (status updates, regular meetings, normal commits)
- ~8% operational (sprint reviews, deploy events, regular reviews)
- ~5% knowledge signals (repeated questions, runbook edits, decision revisits)
- ~2% critical (expertise departure, incident repeats, knowledge gaps)
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List


TEAMS = ["platform", "backend", "frontend", "sre", "data", "security", "devops", "ml"]
PEOPLE = [
    "alice", "bob", "carol", "dave", "eve", "frank", "grace", "heidi",
    "ivan", "judy", "karl", "lisa", "mike", "nina", "oscar", "pat",
]
TOPICS = [
    "auth-service", "deploy-pipeline", "database-migration", "api-gateway",
    "monitoring-stack", "cache-layer", "queue-processing", "load-balancer",
    "certificate-rotation", "secret-management", "backup-restore", "scaling-policy",
    "incident-response", "runbook-updates", "onboarding-process", "architecture-review",
]
SERVICES = [
    "auth-gateway", "user-service", "payment-api", "inventory-service",
    "notification-hub", "search-engine", "analytics-pipeline", "config-server",
]
CHANNELS = ["slack", "meeting", "confluence", "jira", "github", "email", "standup"]


@dataclass
class SyntheticKnowledgeEvent:
    id: int
    signal_type: str
    severity: str
    source: str
    person: str
    team: str
    topic: str
    content: str
    label: str
    label_detail: str


def generate(count: int = 10_000, seed: int = 42) -> List[SyntheticKnowledgeEvent]:
    rng = random.Random(seed)
    events = []
    eid = 0

    # Realistic org distribution
    routine_count = int(count * 0.8500)
    operational_count = int(count * 0.0800)
    knowledge_count = int(count * 0.0500)
    critical_count = max(1, count - routine_count - operational_count - knowledge_count)

    # --- Routine (85%) ---
    for _ in range(routine_count):
        eid += 1
        person = rng.choice(PEOPLE)
        team = rng.choice(TEAMS)
        event_type = rng.choice([
            "status_update", "regular_commit", "meeting_summary",
            "pr_merged", "standup_note", "ticket_closed",
        ])

        if event_type == "status_update":
            content = f"{person} posted status update in #{team}"
        elif event_type == "regular_commit":
            service = rng.choice(SERVICES)
            content = f"{person} committed to {service}: routine update"
        elif event_type == "meeting_summary":
            content = f"Weekly {team} standup — no action items"
        elif event_type == "pr_merged":
            service = rng.choice(SERVICES)
            content = f"PR merged by {person} in {service}: {rng.choice(['bugfix', 'feature', 'refactor', 'test', 'docs'])}"
        elif event_type == "standup_note":
            content = f"{person} ({team}): continuing work on {rng.choice(TOPICS)}"
        else:
            content = f"Ticket closed by {person}: routine {rng.choice(['bug', 'task', 'story'])}"

        events.append(SyntheticKnowledgeEvent(
            id=eid, signal_type=event_type, severity="info",
            source=rng.choice(CHANNELS), person=person, team=team,
            topic=rng.choice(TOPICS), content=content,
            label="normal", label_detail="routine_activity",
        ))

    # --- Operational (8%) ---
    for _ in range(operational_count):
        eid += 1
        person = rng.choice(PEOPLE)
        team = rng.choice(TEAMS)
        event_type = rng.choice([
            "sprint_review", "deploy_event", "code_review_feedback",
            "architecture_discussion", "retro_action_item",
        ])

        if event_type == "sprint_review":
            content = f"Sprint review: {team} completed {rng.randint(5, 15)} stories, {rng.randint(0, 3)} carried over"
        elif event_type == "deploy_event":
            service = rng.choice(SERVICES)
            content = f"{person} deployed {service} to {rng.choice(['staging', 'production'])}"
        elif event_type == "code_review_feedback":
            content = f"{person} reviewed PR: {rng.choice(['approved', 'changes requested', 'commented'])}"
        elif event_type == "architecture_discussion":
            topic = rng.choice(TOPICS)
            content = f"Architecture discussion: {topic} — {rng.choice(['proposal shared', 'options evaluated', 'decision pending'])}"
        else:
            content = f"Retro action item for {team}: {rng.choice(['improve test coverage', 'update runbooks', 'reduce on-call load', 'document architecture'])}"

        events.append(SyntheticKnowledgeEvent(
            id=eid, signal_type=event_type, severity="low",
            source=rng.choice(CHANNELS), person=person, team=team,
            topic=rng.choice(TOPICS), content=content,
            label="operational", label_detail=event_type,
        ))

    # --- Knowledge signals (5%) ---
    for _ in range(knowledge_count):
        eid += 1
        person = rng.choice(PEOPLE)
        team = rng.choice(TEAMS)
        event_type = rng.choice([
            "onboarding_question", "runbook_edit", "decision_revisited",
            "meeting_recurring_topic", "code_review_repeat",
            "escalation_repeat", "documentation_gap", "hotfix_pattern",
        ])

        if event_type == "onboarding_question":
            topic = rng.choice(TOPICS)
            content = f"New hire {person} asked: 'How does {topic} work?' No documentation found."
        elif event_type == "runbook_edit":
            service = rng.choice(SERVICES)
            edit_count = rng.randint(3, 8)
            content = f"Runbook for {service} edited {edit_count} times this month by {rng.choice(PEOPLE)}"
        elif event_type == "decision_revisited":
            topic = rng.choice(TOPICS)
            content = f"Architecture decision for {topic} revisited — same options discussed as last quarter"
        elif event_type == "meeting_recurring_topic":
            topic = rng.choice(TOPICS)
            content = f"{topic} discussed in {rng.randint(3, 6)} meetings this month with no resolution"
        elif event_type == "code_review_repeat":
            content = f"Same review feedback given {rng.randint(3, 7)} times this sprint: '{rng.choice(['missing error handling', 'no tests', 'hardcoded config', 'missing docs'])}'"
        elif event_type == "escalation_repeat":
            expert = rng.choice(PEOPLE)
            content = f"Issue escalated to {expert} again — {rng.randint(4, 10)} escalations this month for {rng.choice(TOPICS)}"
        elif event_type == "documentation_gap":
            topic = rng.choice(TOPICS)
            content = f"Support ticket referenced {topic} — no internal documentation exists"
        else:
            service = rng.choice(SERVICES)
            content = f"Hotfix to {service} — same module patched {rng.randint(3, 6)} times in {rng.randint(2, 4)} weeks"

        events.append(SyntheticKnowledgeEvent(
            id=eid, signal_type=event_type, severity="medium",
            source=rng.choice(CHANNELS), person=person, team=team,
            topic=rng.choice(TOPICS), content=content,
            label="knowledge_signal", label_detail=event_type,
        ))

    # --- Critical (2%) ---
    for _ in range(critical_count):
        eid += 1
        person = rng.choice(PEOPLE)
        team = rng.choice(TEAMS)
        event_type = rng.choice([
            "expertise_departure", "incident_repeat", "expertise_concentration",
            "process_failure_no_runbook", "knowledge_loss",
        ])

        if event_type == "expertise_departure":
            service = rng.choice(SERVICES)
            content = f"{person} leaving {team} — sole maintainer of {service}, no knowledge transfer scheduled"
        elif event_type == "incident_repeat":
            service = rng.choice(SERVICES)
            count = rng.randint(3, 7)
            content = f"Same incident in {service} for the {count}th time — root cause identified each time but fix never implemented"
        elif event_type == "expertise_concentration":
            expert = rng.choice(PEOPLE)
            pct = rng.randint(60, 95)
            service = rng.choice(SERVICES)
            content = f"{expert} handles {pct}% of {service} incidents — single point of failure"
        elif event_type == "process_failure_no_runbook":
            service = rng.choice(SERVICES)
            content = f"{service} outage — no runbook exists, resolution required {rng.choice(PEOPLE)} to be called at {rng.randint(1,5)}am"
        else:
            content = f"Knowledge lost: {person} left {team} last month, {rng.choice(SERVICES)} deployment process undocumented, team can't deploy"

        events.append(SyntheticKnowledgeEvent(
            id=eid, signal_type=event_type, severity=rng.choice(["high", "critical"]),
            source=rng.choice(CHANNELS), person=person, team=team,
            topic=rng.choice(TOPICS), content=content,
            label="critical", label_detail=event_type,
        ))

    rng.shuffle(events)
    return events

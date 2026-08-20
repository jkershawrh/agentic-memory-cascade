"""Agent promotion engine — agents earn their tier through empirical validation.

Agents start as drafts and promote through tiers as they prove accuracy
against observed ground truth. No human labeling required for early tiers —
ground truth is derived from baselines (normal ranges).

Tier ladder:
  draft → candidate → [pending_approval] → nano → micro → macro

Each promotion requires meeting empirical thresholds for accuracy,
false positive rate, false negative rate, and sample count.

Zero-FN invariant: activated agents (nano+) with ANY false negative
are instantly demoted to draft with a cooling-off period.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

DEFAULT_THRESHOLDS = {
    "candidate": {"min_samples": 50, "min_accuracy": 0.60, "max_false_positive": 0.30, "max_false_negative": 0.20},
    "nano": {"min_samples": 200, "min_accuracy": 0.75, "max_false_positive": 0.15, "max_false_negative": 0.0},
    "micro": {"min_samples": 500, "min_accuracy": 0.85, "max_false_positive": 0.10, "max_false_negative": 0.0, "human_reviewed": True},
    "macro": {"min_samples": 1000, "min_accuracy": 0.85, "max_false_positive": 0.05, "max_false_negative": 0.0, "human_reviewed": True},
}

TIER_ORDER = ["draft", "candidate", "nano", "micro", "macro"]

ACTIVATED_TIERS = {"nano", "micro", "macro"}


@dataclass
class PromotionEvent:
    """Full evidence chain for a tier transition, written to the immutable ledger."""
    agent_name: str
    event_type: str  # "promotion" or "demotion"
    from_tier: str
    to_tier: str
    timestamp: str
    samples_tested: int
    accuracy: float
    false_positive_rate: float
    false_negative_rate: float
    false_negative_count: int
    reason: str
    batch_id: Optional[str] = None
    human_approved: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "event_type": self.event_type,
            "from_tier": self.from_tier,
            "to_tier": self.to_tier,
            "timestamp": self.timestamp,
            "evidence": {
                "samples_tested": self.samples_tested,
                "accuracy": round(self.accuracy, 4),
                "false_positive_rate": round(self.false_positive_rate, 4),
                "false_negative_rate": round(self.false_negative_rate, 4),
                "false_negative_count": self.false_negative_count,
                "human_approved": self.human_approved,
                "batch_id": self.batch_id,
            },
            "reason": self.reason,
        }


@dataclass
class AgentMetrics:
    """Empirical metrics for an agent, computed from validation rounds."""
    name: str = ""
    tier: str = "draft"
    samples_tested: int = 0
    accuracy: float = 0.0
    false_positive_rate: float = 0.0
    false_negative_rate: float = 0.0
    coverage: float = 0.0
    rubric_status: str = "red"
    human_reviewed: bool = False
    human_approved: bool = False
    promoted_at: Optional[datetime] = None
    promotion_history: List[Dict[str, Any]] = field(default_factory=list)
    demotion_history: List[Dict[str, Any]] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    deactivated: bool = False
    deactivated_at: Optional[datetime] = None
    last_batch_fn_count: int = 0


@dataclass
class Baseline:
    """Normal operating ranges for signal features.

    Used as ground truth for validation: values outside normal ranges
    are considered abnormal. Domain packs populate these from historical data.
    """
    normal_ranges: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def is_abnormal(self, signal_type: str, features: Dict[str, Any]) -> bool:
        ranges = self.normal_ranges.get(signal_type, {})
        for key, bounds in ranges.items():
            if isinstance(bounds, dict) and "low" in bounds and "high" in bounds:
                val = features.get(key)
                if val is not None and isinstance(val, (int, float)):
                    if val < bounds["low"] or val > bounds["high"]:
                        return True
        return False


class RuleAgent:
    """A configurable rule-based agent that can be validated and promoted.

    Rules are defined as:
        condition: {field: "cpu_percent", operator: "gt", value: 90}
        classification: "cpu_critical"
    """

    OPERATORS = {
        "gt": lambda a, b: a > b,
        "lt": lambda a, b: a < b,
        "gte": lambda a, b: a >= b,
        "lte": lambda a, b: a <= b,
        "eq": lambda a, b: a == b,
        "ne": lambda a, b: a != b,
        "contains": lambda a, b: str(b) in str(a),
    }

    def __init__(self, config: Dict[str, Any]):
        self.name = config.get("name", "unnamed")
        self.condition = config.get("condition", {})
        self.classification = config.get("classification", "unknown")
        self.signal_types = config.get("signal_types", [])

    def evaluate(self, features: Dict[str, Any]) -> bool:
        field = self.condition.get("field", "")
        val = features.get(field)
        if val is None:
            return False
        op_name = self.condition.get("operator", "gt")
        op = self.OPERATORS.get(op_name)
        if op is None:
            return False
        try:
            return op(float(val), float(self.condition.get("value", 0)))
        except (ValueError, TypeError):
            return op(str(val), str(self.condition.get("value", "")))


class PromotionEngine:
    """Evaluates agents against baselines and promotes them through tiers.

    When human_gate_enabled=True, agents pause at pending_approval between
    candidate and nano, requiring human_approved=True to activate.
    """

    def __init__(self, thresholds: Optional[Dict] = None, human_gate_enabled: bool = False):
        self.thresholds = thresholds or DEFAULT_THRESHOLDS
        self.human_gate_enabled = human_gate_enabled
        self._pending_events: List[PromotionEvent] = []

    @property
    def events(self) -> List[PromotionEvent]:
        """Promotion/demotion events generated since last drain."""
        return list(self._pending_events)

    def drain_events(self) -> List[PromotionEvent]:
        """Return and clear pending promotion/demotion events."""
        events = self._pending_events
        self._pending_events = []
        return events

    def validate(
        self,
        agent: AgentMetrics,
        rule: RuleAgent,
        signals: List[Dict[str, Any]],
        baseline: Baseline,
        batch_id: Optional[str] = None,
    ) -> AgentMetrics:
        """Run a validation round: test the agent against signals with known ground truth."""
        if not signals:
            return agent

        tp = fp = tn = fn = classified = 0

        for sig in signals:
            signal_type = sig.get("signal_type", "")
            features = sig.get("features", sig.get("content", {}))

            actually_abnormal = baseline.is_abnormal(signal_type, features)
            agent_flagged = rule.evaluate(features)

            if agent_flagged:
                classified += 1

            if agent_flagged and actually_abnormal:
                tp += 1
            elif not agent_flagged and not actually_abnormal:
                tn += 1
            elif agent_flagged and not actually_abnormal:
                fp += 1
            else:
                fn += 1

        total = len(signals)
        agent.samples_tested += total
        agent.accuracy = (tp + tn) / total if total > 0 else 0.0
        agent.false_positive_rate = fp / (fp + tn) if (fp + tn) > 0 else 0.0
        agent.false_negative_rate = fn / (fn + tp) if (fn + tp) > 0 else 0.0
        agent.coverage = classified / total if total > 0 else 0.0
        agent.last_batch_fn_count = fn

        if agent.false_negative_rate > 0.20:
            agent.rubric_status = "red"
        elif (
            agent.accuracy >= 0.75
            and agent.false_positive_rate <= 0.15
        ):
            agent.rubric_status = "green"
        elif agent.accuracy >= 0.60:
            agent.rubric_status = "yellow"
        else:
            agent.rubric_status = "red"

        if agent.tier in ACTIVATED_TIERS and fn > 0:
            self.demote(agent, reason=f"{fn} false negative(s) in validation batch", batch_id=batch_id)

        return agent

    def demote(self, agent: AgentMetrics, reason: str, batch_id: Optional[str] = None) -> AgentMetrics:
        """Instantly demote an agent to draft and deactivate it."""
        now = datetime.now(timezone.utc)
        from_tier = agent.tier

        event = PromotionEvent(
            agent_name=agent.name,
            event_type="demotion",
            from_tier=from_tier,
            to_tier="draft",
            timestamp=now.isoformat(),
            samples_tested=agent.samples_tested,
            accuracy=agent.accuracy,
            false_positive_rate=agent.false_positive_rate,
            false_negative_rate=agent.false_negative_rate,
            false_negative_count=agent.last_batch_fn_count,
            reason=reason,
            batch_id=batch_id,
        )
        self._pending_events.append(event)

        agent.demotion_history.append(event.to_dict())
        agent.tier = "draft"
        agent.deactivated = True
        agent.deactivated_at = now
        agent.samples_tested = 0
        agent.rubric_status = "red"
        agent.human_approved = False

        log.warning("Agent '%s' DEMOTED: %s → draft (%s)", agent.name, from_tier, reason)
        return agent

    def check_promotion(self, agent: AgentMetrics) -> AgentMetrics:
        """Check if an agent meets requirements for promotion to next tier."""
        if agent.deactivated:
            return agent

        if agent.tier == "pending_approval":
            if agent.human_approved:
                self._do_promote(agent, "pending_approval", "nano",
                                 reason="human approved for activation")
            return agent

        current_idx = TIER_ORDER.index(agent.tier) if agent.tier in TIER_ORDER else 0
        if current_idx >= len(TIER_ORDER) - 1:
            return agent

        next_tier = TIER_ORDER[current_idx + 1]
        reqs = self.thresholds.get(next_tier, {})

        if self._meets_requirements(agent, reqs):
            if self.human_gate_enabled and agent.tier == "candidate" and next_tier == "nano":
                self._do_promote(agent, "candidate", "pending_approval",
                                 reason="meets nano thresholds, awaiting human approval")
            else:
                self._do_promote(agent, agent.tier, next_tier,
                                 reason=f"meets {next_tier} thresholds")

        return agent

    def _do_promote(self, agent: AgentMetrics, from_tier: str, to_tier: str, reason: str) -> None:
        now = datetime.now(timezone.utc)
        event = PromotionEvent(
            agent_name=agent.name,
            event_type="promotion",
            from_tier=from_tier,
            to_tier=to_tier,
            timestamp=now.isoformat(),
            samples_tested=agent.samples_tested,
            accuracy=agent.accuracy,
            false_positive_rate=agent.false_positive_rate,
            false_negative_rate=agent.false_negative_rate,
            false_negative_count=agent.last_batch_fn_count,
            reason=reason,
            human_approved=agent.human_approved,
        )
        self._pending_events.append(event)

        agent.promotion_history.append(event.to_dict())
        agent.tier = to_tier
        agent.promoted_at = now
        if to_tier in ACTIVATED_TIERS:
            agent.deactivated = False
        log.info("Agent '%s' promoted: %s → %s (acc=%.2f, FP=%.2f)",
                  agent.name, from_tier, to_tier, agent.accuracy, agent.false_positive_rate)

    def reactivate(self, agent: AgentMetrics) -> AgentMetrics:
        """Clear deactivated state so the agent can begin climbing again."""
        agent.deactivated = False
        agent.deactivated_at = None
        log.info("Agent '%s' reactivated at tier '%s'", agent.name, agent.tier)
        return agent

    def _meets_requirements(self, agent: AgentMetrics, reqs: Dict) -> bool:
        if agent.samples_tested < reqs.get("min_samples", 0):
            return False
        if agent.accuracy < reqs.get("min_accuracy", 0):
            return False
        if agent.false_positive_rate > reqs.get("max_false_positive", 1.0):
            return False
        if "max_false_negative" in reqs and agent.false_negative_rate > reqs["max_false_negative"]:
            return False
        if reqs.get("human_reviewed") and not agent.human_reviewed:
            return False
        return True

    def run_validation_round(
        self,
        agents: List[AgentMetrics],
        rules: List[RuleAgent],
        signals: List[Dict[str, Any]],
        baseline: Baseline,
        batch_id: Optional[str] = None,
    ) -> List[AgentMetrics]:
        """Validate and attempt promotion for a batch of agents."""
        if len(agents) != len(rules):
            raise ValueError("agents and rules must be same length")

        updated = []
        for agent, rule in zip(agents, rules):
            validated = self.validate(agent, rule, signals, baseline, batch_id=batch_id)
            promoted = self.check_promotion(validated)
            updated.append(promoted)
        return updated

    @staticmethod
    def rubric_matrix(agents: List[AgentMetrics]) -> Dict:
        """Generate a red/yellow/green rubric matrix for all agents."""
        greens = sum(1 for a in agents if a.rubric_status == "green")
        yellows = sum(1 for a in agents if a.rubric_status == "yellow")
        total = len(agents)

        if total == 0:
            overall = "red"
        elif greens / total >= 0.5:
            overall = "green"
        elif (greens + yellows) / total >= 0.3:
            overall = "yellow"
        else:
            overall = "red"

        return {
            "overall": overall,
            "total": total,
            "green": greens,
            "yellow": yellows,
            "red": total - greens - yellows,
            "agents": [{
                "name": a.name,
                "tier": a.tier,
                "status": a.rubric_status,
                "accuracy": round(a.accuracy, 3),
                "fp_rate": round(a.false_positive_rate, 3),
                "fn_rate": round(a.false_negative_rate, 3),
                "samples": a.samples_tested,
            } for a in agents],
        }

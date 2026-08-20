"""Domain-agnostic cascade protocol.

Consumers (deepfield, deepfield-engine, domain packs) implement CascadeAgent
for their domain. The framework handles pipeline orchestration and routing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Protocol, runtime_checkable
from uuid import UUID, uuid4


class Outcome(str, Enum):
    KEEP = "keep"
    DROP = "drop"
    SUPPRESS = "suppress"
    DEDUPE = "dedupe"
    ESCALATE = "escalate"
    CLASSIFY = "classify"


VALID_SEVERITIES = {"info", "low", "medium", "high", "critical"}


@dataclass
class Signal:
    """Generic input signal — domain packs map their types to this."""
    signal_id: UUID = field(default_factory=uuid4)
    signal_type: str = ""
    severity: str = "info"
    source: str = ""
    content: Dict[str, Any] = field(default_factory=dict)
    labels: Dict[str, str] = field(default_factory=dict)
    namespace: str = ""
    cluster: str = ""

    def __post_init__(self):
        if not self.severity or self.severity not in VALID_SEVERITIES:
            self.severity = "medium"


@dataclass
class CascadeDecision:
    """Result of a nanoagent processing a signal."""
    signal_id: UUID = field(default_factory=uuid4)
    agent_name: str = ""
    outcome: Outcome = Outcome.KEEP
    confidence: float = 1.0
    evidence: str = ""
    classification: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class CascadeAgent(Protocol):
    """Interface for deterministic cascade agents.

    Agents receive a batch of signals and return decisions for any
    signals they can handle. Signals without decisions pass through
    to the next agent in the pipeline.
    """
    name: str
    stage: int  # 1=noise, 2=classify, 3=domain

    def process(self, signals: List[Signal]) -> List[CascadeDecision]: ...

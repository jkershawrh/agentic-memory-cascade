"""Session boundary and recall adapter around the Cascade OSS primitives."""

from datetime import datetime, timezone
from typing import Dict, List
from uuid import uuid4

from cascade_compression.cascade import CascadePipeline, MemoryArchive, RecallEngine, Signal

from .models import (
    MemoryObservation,
    ObservationResult,
    RecalledFact,
    RecallRequest,
    SessionRecord,
)


class UnknownSessionError(KeyError):
    """Raised when a caller refers to a session that was not created here."""


class AgenticMemoryEngine:
    """Curate observations once and recall them from a later agent session."""

    def __init__(self, *, max_memories: int = 1_000):
        self._pipeline = CascadePipeline()
        self._archive = MemoryArchive(max_capacity=max_memories, instance_id="local-proof")
        self._recall = RecallEngine()
        self._sessions: Dict[str, SessionRecord] = {}

    @property
    def memory_count(self) -> int:
        return self._archive.size

    def start_session(self) -> SessionRecord:
        record = SessionRecord(
            session_id=str(uuid4()),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._sessions[record.session_id] = record
        return record

    def observe(self, session_id: str, observation: MemoryObservation) -> ObservationResult:
        self._require_session(session_id)
        labels = {
            **observation.labels,
            "domain": "agentic-memory",
            "subject_id": observation.subject_id,
            "kind": observation.kind,
        }
        signal = Signal(
            signal_type=f"agent_memory_{observation.kind}",
            severity=observation.severity,
            source=session_id,
            namespace=observation.subject_id,
            content={"message": observation.text},
            labels=labels,
        )
        result = self._pipeline.run([signal])
        if not result.remaining:
            return ObservationResult(remembered=False, compressed=True)

        memory = self._archive.store(
            result.remaining[0],
            classification="consequential_agent_memory",
        )
        return ObservationResult(
            remembered=True,
            memory_id=str(memory.memory_id),
            compressed=False,
        )

    def recall(self, session_id: str, request: RecallRequest) -> List[RecalledFact]:
        self._require_session(session_id)
        query = Signal(
            signal_type="agent_memory_query",
            severity="medium",
            source=session_id,
            namespace=request.subject_id,
            content={"message": request.question},
            labels={"domain": "agentic-memory", "subject_id": request.subject_id},
        )
        matches = self._recall.recall(
            query,
            self._archive,
            top_k=request.limit,
            # Subject identity is an explicit boundary in this proof. Keep the
            # semantic threshold low enough to return every bounded fact for
            # that subject, then apply the hard subject filter below.
            min_score=0.05,
            reinforce=True,
        )
        facts = []
        for match in matches:
            memory = match.memory
            if memory.signal.labels.get("subject_id") != request.subject_id:
                continue
            facts.append(RecalledFact(
                memory_id=str(memory.memory_id),
                subject_id=request.subject_id,
                kind=memory.signal.labels.get("kind", "fact"),
                text=str(memory.signal.content.get("message", "")),
                score=round(match.score, 4),
                source_session=memory.signal.source,
            ))
        return facts

    def _require_session(self, session_id: str) -> None:
        if session_id not in self._sessions:
            raise UnknownSessionError(session_id)

"""Public request and evidence models for the cross-session proof."""

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class MemoryObservation(BaseModel):
    subject_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    text: str = Field(min_length=1)
    severity: Literal["info", "low", "medium", "high", "critical"] = "high"
    labels: Dict[str, str] = Field(default_factory=dict)


class RecallRequest(BaseModel):
    subject_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)


class RecalledFact(BaseModel):
    memory_id: str
    subject_id: str
    kind: str
    text: str
    score: float
    source_session: str


class ObservationResult(BaseModel):
    remembered: bool
    memory_id: Optional[str] = None
    compressed: bool


class SessionRecord(BaseModel):
    session_id: str
    created_at: str


class QuestionResult(BaseModel):
    question: str
    expected_key: str
    expected_value: str
    answer: str
    passed: bool
    recalled_memory_ids: List[str] = Field(default_factory=list)

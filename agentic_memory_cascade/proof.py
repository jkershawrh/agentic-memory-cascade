"""Deterministic A/B evidence for cross-session agent memory."""

from typing import Dict, Iterable, List, Optional

from pydantic import BaseModel, Field
from cascade_compression.cascade.protocol import chat_completions_url

from .engine import AgenticMemoryEngine
from .models import MemoryObservation, QuestionResult, RecallRequest, RecalledFact


SUBJECT_ID = "synthetic-incident-42"
EXPECTED_FACTS = {
    "cause": "A stale feature flag routed traffic to the retired parser.",
    "constraint": "The parser cannot be restarted during settlement processing.",
    "resolution": "Disable the stale flag and drain the old parser after settlement.",
}
QUESTIONS = {
    "cause": "What caused synthetic incident 42?",
    "constraint": "What operational constraint applies to synthetic incident 42?",
    "resolution": "How was synthetic incident 42 resolved?",
}


class ProofReport(BaseModel):
    schema_version: str = "agentic-memory-proof.v1"
    claim: str
    source_session_id: str
    recall_session_id: str
    sessions_are_distinct: bool
    observations_total: int
    observations_compressed: int
    memories_formed: int
    control_score: int
    treatment_score: int
    maximum_score: int
    unsupported_claims: int
    passed: bool
    illustrative_answers: Dict[str, str] = Field(default_factory=dict)
    control: List[QuestionResult] = Field(default_factory=list)
    treatment: List[QuestionResult] = Field(default_factory=list)


def generate_ollama_answers(
    engine: AgenticMemoryEngine,
    session_id: str,
    *,
    base_url: str,
    model: str,
) -> Dict[str, str]:
    """Generate presentation-only answers; never contributes to proof scoring."""
    import httpx

    answers = {}
    with httpx.Client(timeout=60) as client:
        for key, question in QUESTIONS.items():
            facts = engine.recall(
                session_id,
                RecallRequest(subject_id=SUBJECT_ID, question=question),
            )
            context = "\n".join(f"- {fact.kind}: {fact.text}" for fact in facts)
            response = client.post(
                chat_completions_url(base_url),
                json={
                    "model": model,
                    "temperature": 0,
                    "max_tokens": 100,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Answer only from the supplied cross-session memories. "
                                "Say UNKNOWN if they do not contain the answer."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"Memories:\n{context}\n\nQuestion: {question}",
                        },
                    ],
                },
            )
            response.raise_for_status()
            answers[key] = response.json()["choices"][0]["message"]["content"].strip()
    return answers


def _deterministic_answer(facts: Iterable[RecalledFact], expected_key: str) -> str:
    for fact in facts:
        if fact.kind == expected_key:
            return fact.text
    return "UNKNOWN"


def _score_condition(
    engine: AgenticMemoryEngine,
    session_id: str,
    *,
    memory_enabled: bool,
) -> List[QuestionResult]:
    results = []
    for key, question in QUESTIONS.items():
        facts = (
            engine.recall(
                session_id,
                RecallRequest(subject_id=SUBJECT_ID, question=question),
            )
            if memory_enabled else []
        )
        answer = _deterministic_answer(facts, key)
        expected = EXPECTED_FACTS[key]
        results.append(QuestionResult(
            question=question,
            expected_key=key,
            expected_value=expected,
            answer=answer,
            passed=answer == expected,
            recalled_memory_ids=[fact.memory_id for fact in facts],
        ))
    return results


def run_proof(
    engine: Optional[AgenticMemoryEngine] = None,
    *,
    ollama_url: Optional[str] = None,
    ollama_model: str = "granite3.2:8b-instruct",
) -> ProofReport:
    engine = engine or AgenticMemoryEngine()
    source = engine.start_session()
    observations = [
        MemoryObservation(
            subject_id=SUBJECT_ID,
            kind=key,
            text=value,
            severity="high",
        )
        for key, value in EXPECTED_FACTS.items()
    ]
    observations.extend(
        MemoryObservation(
            subject_id=SUBJECT_ID,
            kind="chatter",
            text=f"Routine status acknowledgement {index}.",
            severity="info",
        )
        for index in range(12)
    )

    outcomes = [engine.observe(source.session_id, item) for item in observations]
    recall_session = engine.start_session()
    control = _score_condition(engine, recall_session.session_id, memory_enabled=False)
    treatment = _score_condition(engine, recall_session.session_id, memory_enabled=True)
    control_score = sum(item.passed for item in control)
    treatment_score = sum(item.passed for item in treatment)
    recalled_answers = {item.answer for item in treatment if item.answer != "UNKNOWN"}
    unsupported = len(recalled_answers - set(EXPECTED_FACTS.values()))
    passed = (
        source.session_id != recall_session.session_id
        and control_score == 0
        and treatment_score == len(EXPECTED_FACTS)
        and unsupported == 0
    )
    illustrative_answers = (
        generate_ollama_answers(
            engine,
            recall_session.session_id,
            base_url=ollama_url,
            model=ollama_model,
        )
        if ollama_url else {}
    )
    return ProofReport(
        claim="Cascade-curated memory remains available to a new agent session.",
        source_session_id=source.session_id,
        recall_session_id=recall_session.session_id,
        sessions_are_distinct=source.session_id != recall_session.session_id,
        observations_total=len(observations),
        observations_compressed=sum(item.compressed for item in outcomes),
        memories_formed=sum(item.remembered for item in outcomes),
        control_score=control_score,
        treatment_score=treatment_score,
        maximum_score=len(EXPECTED_FACTS),
        unsupported_claims=unsupported,
        passed=passed,
        illustrative_answers=illustrative_answers,
        control=control,
        treatment=treatment,
    )

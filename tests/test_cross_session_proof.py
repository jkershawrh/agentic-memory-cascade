from fastapi.testclient import TestClient

from agentic_memory_cascade.engine import AgenticMemoryEngine
from agentic_memory_cascade.models import MemoryObservation, RecallRequest
from agentic_memory_cascade.proof import EXPECTED_FACTS, SUBJECT_ID, run_proof
from agentic_memory_cascade.service import app


def _observation(subject: str, kind: str, text: str, severity: str = "high"):
    return MemoryObservation(
        subject_id=subject,
        kind=kind,
        text=text,
        severity=severity,
    )


def test_deterministic_proof_passes_across_distinct_sessions():
    report = run_proof()

    assert report.passed is True
    assert report.sessions_are_distinct is True
    assert report.observations_compressed == 12
    assert report.memories_formed == 3
    assert report.control_score == 0
    assert report.treatment_score == report.maximum_score == 3
    assert report.unsupported_claims == 0


def test_info_chatter_is_compressed_and_not_remembered():
    engine = AgenticMemoryEngine()
    session = engine.start_session()

    result = engine.observe(
        session.session_id,
        _observation(SUBJECT_ID, "chatter", "Routine acknowledgement.", "info"),
    )

    assert result.compressed is True
    assert result.remembered is False
    assert engine.memory_count == 0


def test_recall_is_hard_scoped_to_subject():
    engine = AgenticMemoryEngine()
    source = engine.start_session()
    engine.observe(
        source.session_id,
        _observation("incident-a", "cause", "Cause for incident A."),
    )
    engine.observe(
        source.session_id,
        _observation("incident-b", "cause", "Cause for incident B."),
    )
    later = engine.start_session()

    facts = engine.recall(
        later.session_id,
        RecallRequest(subject_id="incident-a", question="What was the cause?"),
    )

    assert [fact.text for fact in facts] == ["Cause for incident A."]
    assert all(fact.subject_id == "incident-a" for fact in facts)


def test_optional_labels_cannot_override_memory_boundaries():
    engine = AgenticMemoryEngine()
    source = engine.start_session()
    engine.observe(
        source.session_id,
        MemoryObservation(
            subject_id="incident-a",
            kind="cause",
            text="Cause for incident A.",
            labels={"subject_id": "incident-b", "kind": "chatter"},
        ),
    )
    later = engine.start_session()

    facts = engine.recall(
        later.session_id,
        RecallRequest(subject_id="incident-a", question="What was the cause?"),
    )

    assert [(fact.subject_id, fact.kind) for fact in facts] == [("incident-a", "cause")]


def test_repeated_observation_deduplicates_memory():
    engine = AgenticMemoryEngine()
    source = engine.start_session()
    item = _observation(SUBJECT_ID, "cause", EXPECTED_FACTS["cause"])

    first = engine.observe(source.session_id, item)
    second = engine.observe(source.session_id, item)

    assert first.memory_id == second.memory_id
    assert engine.memory_count == 1


def test_http_surface_runs_equivalent_proof_and_validates_sessions():
    with TestClient(app) as client:
        proof = client.post("/v1/proof/run")
        missing = client.post(
            "/v1/sessions/not-a-session/recall",
            json={"subject_id": SUBJECT_ID, "question": "What happened?"},
        )
        malformed = client.post("/v1/sessions", json={})
        session_id = malformed.json()["session_id"]
        invalid_observation = client.post(
            f"/v1/sessions/{session_id}/observations",
            json={"subject_id": "", "kind": "cause", "text": ""},
        )

    assert proof.status_code == 200
    assert proof.json()["passed"] is True
    assert missing.status_code == 404
    assert invalid_observation.status_code == 422

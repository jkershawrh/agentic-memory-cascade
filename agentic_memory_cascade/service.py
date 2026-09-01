"""Local HTTP surface for the cross-session memory proof."""

from fastapi import FastAPI, HTTPException

from .engine import AgenticMemoryEngine, UnknownSessionError
from .models import MemoryObservation, RecallRequest
from .proof import run_proof

app = FastAPI(title="Agentic Memory Cascade", version="0.1.0")
engine = AgenticMemoryEngine()


@app.get("/health")
def health():
    return {"status": "ok", "memory_count": engine.memory_count}


@app.post("/v1/sessions")
def start_session():
    return engine.start_session()


@app.post("/v1/sessions/{session_id}/observations")
def observe(session_id: str, observation: MemoryObservation):
    try:
        return engine.observe(session_id, observation)
    except UnknownSessionError:
        raise HTTPException(status_code=404, detail="unknown session")


@app.post("/v1/sessions/{session_id}/recall")
def recall(session_id: str, request: RecallRequest):
    try:
        return {"facts": engine.recall(session_id, request)}
    except UnknownSessionError:
        raise HTTPException(status_code=404, detail="unknown session")


@app.post("/v1/proof/run")
def proof():
    return run_proof()

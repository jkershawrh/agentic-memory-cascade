"""FastAPI cascade service — sits in front of model services.

Receives signals, runs them through the cascade pipeline, and forwards
only the survivors to the appropriate model service for inference.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field

from .agents import default_agents
from .pipeline import CascadePipeline, CascadeResult
from .protocol import Signal
from .router import CascadeRouter

log = logging.getLogger(__name__)
app = FastAPI(title="Intel Inference Cascade", version="1.0.0")

pipeline = CascadePipeline(default_agents())
router = CascadeRouter()

# Model service endpoints (configured at startup)
MODEL_SERVICES: Dict[str, str] = {
    "classification": "http://llama-gemma3-4b:8080",
    "extraction": "http://llama-phi4-mini:8080",
    "generation": "http://llama-phi4-mini:8080",
    "reasoning": "http://llama-phi4-mini:8080",
}

MODEL_ALIASES: Dict[str, str] = {
    "classification": "gemma3-4b",
    "extraction": "phi4-mini",
    "generation": "phi4-mini",
    "reasoning": "phi4-mini",
}


class SignalInput(BaseModel):
    signal_id: Optional[UUID] = None
    signal_type: str = ""
    severity: str = "info"
    source: str = ""
    content: Dict[str, Any] = Field(default_factory=dict)
    labels: Dict[str, str] = Field(default_factory=dict)
    namespace: str = ""
    cluster: str = ""


class BatchRequest(BaseModel):
    signals: List[SignalInput]
    forward_to_inference: bool = True


class CascadeStats(BaseModel):
    total_processed: int = 0
    total_compressed: int = 0
    total_forwarded: int = 0
    avg_compression_ratio: float = 0.0
    avg_cascade_latency_ms: float = 0.0


_stats = CascadeStats()
_latencies: list = []


def _to_signal(signal: SignalInput) -> Signal:
    """Convert API input without losing the caller's correlation ID."""
    return Signal(
        signal_id=signal.signal_id or uuid4(),
        signal_type=signal.signal_type,
        severity=signal.severity,
        source=signal.source,
        content=signal.content,
        labels=signal.labels,
        namespace=signal.namespace,
        cluster=signal.cluster,
    )


@app.get("/health")
def health():
    return {"status": "ok", "agents": len(pipeline._agents)}


@app.get("/stats")
def stats():
    return _stats


@app.post("/cascade")
async def cascade_signals(request: BatchRequest):
    """Run signals through the cascade pipeline.

    Returns which signals were handled (compressed) and which need inference.
    If forward_to_inference is True, automatically calls the model services.
    """
    global _stats

    t0 = time.monotonic()

    signals = [_to_signal(signal) for signal in request.signals]

    result = pipeline.run(signals)
    cascade_ms = (time.monotonic() - t0) * 1000

    # Update stats
    _stats.total_processed += result.total_signals
    _stats.total_compressed += result.total_signals - result.needs_inference_count
    _stats.total_forwarded += result.needs_inference_count
    _latencies.append(cascade_ms)
    if _latencies:
        _stats.avg_cascade_latency_ms = sum(_latencies[-100:]) / len(_latencies[-100:])
    if _stats.total_processed > 0:
        _stats.avg_compression_ratio = _stats.total_compressed / _stats.total_processed

    # Forward survivors to inference if requested
    inference_results = []
    if request.forward_to_inference and result.remaining:
        inference_results = await _forward_to_inference(result)

    return {
        "cascade_ms": round(cascade_ms, 1),
        "total": result.total_signals,
        "compressed": result.total_signals - result.needs_inference_count,
        "needs_inference": result.needs_inference_count,
        "compression_ratio": round(result.compression_ratio, 3),
        "suppressed": result.suppressed_count,
        "deduped": result.deduped_count,
        "dropped": result.dropped_count,
        "escalated": result.escalated_count,
        "classified": result.classified_count,
        "inference_results": inference_results,
    }


async def _forward_to_inference(result: CascadeResult) -> list:
    """Forward remaining signals to the appropriate model services."""
    responses = []

    async with httpx.AsyncClient(timeout=60) as client:
        for signal in result.remaining:
            # Determine task type from signal
            task_type = _signal_to_task(signal)
            req = router.route(
                signal_id=signal.signal_id,
                task_type=task_type,
                severity=signal.severity,
                prompt=_signal_to_prompt(signal),
            )

            url = MODEL_SERVICES.get(req.lane, MODEL_SERVICES.get("generation"))
            alias = MODEL_ALIASES.get(req.lane, "phi4-mini")

            try:
                t0 = time.monotonic()
                r = await client.post(f"{url}/v1/chat/completions", json={
                    "model": alias,
                    "messages": [
                        {"role": "system", "content": "Analyze concisely."},
                        {"role": "user", "content": req.prompt},
                    ],
                    "max_tokens": req.max_tokens,
                })
                elapsed = (time.monotonic() - t0) * 1000
                data = r.json()
                content = data["choices"][0]["message"]["content"].strip()
                responses.append({
                    "signal_id": str(signal.signal_id),
                    "lane": req.lane,
                    "model": req.model,
                    "response": content[:200],
                    "latency_ms": round(elapsed),
                })
            except Exception as e:
                responses.append({
                    "signal_id": str(signal.signal_id),
                    "lane": req.lane,
                    "error": str(e)[:100],
                })

    return responses


def _signal_to_task(signal: Signal) -> str:
    """Map a signal to a benchmark task type for lane routing."""
    stype = signal.signal_type.lower()
    if stype in ("fraud", "transaction", "wire_transfer"):
        return "fraud-scoring"
    if stype in ("alert", "incident", "event"):
        return "claims-triage"
    if stype in ("ticket", "support", "request"):
        return "ticket-routing"
    if signal.severity in ("critical", "high"):
        return "generate-qa"
    return "classify-short"


def _signal_to_prompt(signal: Signal) -> str:
    """Build an inference prompt from a signal."""
    parts = [f"Signal type: {signal.signal_type}"]
    if signal.severity:
        parts.append(f"Severity: {signal.severity}")
    if signal.namespace:
        parts.append(f"Namespace: {signal.namespace}")
    if signal.content:
        msg = signal.content.get("message", "")
        if msg:
            parts.append(f"Message: {msg}")
        for k, v in signal.content.items():
            if k != "message" and v:
                parts.append(f"{k}: {v}")
    return "\n".join(parts)

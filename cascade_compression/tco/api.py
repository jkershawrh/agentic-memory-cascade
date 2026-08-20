"""FastAPI application for the TCO calculator.

Endpoints match the OpenAPI spec at contracts/openapi/tco-calculator.yaml.
"""

import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ..resources import resource_path
from .calculator import calculate_full_comparison
from .models import (
    Assumptions,
    CalculateRequest,
    TCOResult,
)
from .scenarios import get_scenarios

ROOT = Path(__file__).resolve().parent.parent.parent

app = FastAPI(
    title="Intel TCO Calculator",
    version="1.0.0",
    description="Intel vs GPU vs Cloud API TCO calculator for FSI sales",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load_json(filename: str) -> dict:
    """Load a JSON file from the data directory."""
    path = resource_path("data", filename)
    with open(path) as f:
        return json.load(f)


@app.get("/health")
def health():
    """Health check."""
    return {"status": "healthy"}


@app.get("/api/v1/hardware")
def list_hardware():
    """List available hardware profiles."""
    data = _load_json("hardware_profiles.json")
    return {"profiles": data["profiles"]}


@app.get("/api/v1/workloads")
def list_workloads():
    """List pre-built workload profiles."""
    data = _load_json("workload_profiles.json")
    return {"profiles": data["profiles"]}


@app.get("/api/v1/scenarios")
def list_scenarios():
    """List pre-built FSI scenarios."""
    scenarios = get_scenarios()
    return {"scenarios": [s.model_dump() for s in scenarios]}


@app.post("/api/v1/calculate")
def calculate_tco(request: CalculateRequest) -> TCOResult:
    """Calculate full TCO comparison for a workload."""
    hardware_profiles = _load_json("hardware_profiles.json")
    assumptions = request.assumptions or Assumptions()
    result = calculate_full_comparison(
        request.workload, hardware_profiles, assumptions
    )
    return result


@app.get("/api/v1/benchmarks")
def list_benchmarks():
    """Full benchmark results matrix with rubric grades."""
    return _load_json("benchmark_matrix.json")


@app.get("/api/v1/benchmarks/{model}")
def get_benchmark_model(model: str):
    """Detailed benchmark results for a specific model."""
    data = _load_json("benchmark_matrix.json")
    for entry in data.get("matrix", []):
        if entry["model"] == model:
            return entry
    return {"error": f"Model '{model}' not found", "available": [m["model"] for m in data["matrix"]]}


# Mount frontend static files (if the directory exists)
frontend_dir = ROOT / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "cascade_compression.tco.api:app",
        host=os.getenv("CASCADE_TCO_HOST", "127.0.0.1"),
        port=8090,
        reload=True,
    )

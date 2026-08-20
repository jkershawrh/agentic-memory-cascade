"""Stage 3: API contract compliance tests.

Tests that the FastAPI endpoints match the OpenAPI spec.
"""

import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from cascade_compression.tco.api import app


@pytest.fixture
def client():
    """Create a FastAPI test client."""
    return TestClient(app)


# --- Health endpoint ---

class TestHealthEndpoint:
    """Test GET /health."""

    def test_health_returns_200(self, client):
        """Health endpoint returns 200."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_healthy(self, client):
        """Health endpoint returns {status: healthy}."""
        response = client.get("/health")
        data = response.json()
        assert data["status"] == "healthy"


# --- Hardware endpoint ---

class TestHardwareEndpoint:
    """Test GET /api/v1/hardware."""

    def test_hardware_returns_200(self, client):
        """Hardware endpoint returns 200."""
        response = client.get("/api/v1/hardware")
        assert response.status_code == 200

    def test_hardware_returns_profiles(self, client):
        """Hardware endpoint returns profiles array."""
        response = client.get("/api/v1/hardware")
        data = response.json()
        assert "profiles" in data
        assert isinstance(data["profiles"], list)
        assert len(data["profiles"]) >= 3

    def test_hardware_profiles_have_required_fields(self, client):
        """Each hardware profile has id, name, type."""
        response = client.get("/api/v1/hardware")
        for profile in response.json()["profiles"]:
            assert "id" in profile
            assert "name" in profile
            assert "type" in profile
            assert profile["type"] in ("cpu", "gpu", "cloud_api")


# --- Workloads endpoint ---

class TestWorkloadsEndpoint:
    """Test GET /api/v1/workloads."""

    def test_workloads_returns_200(self, client):
        """Workloads endpoint returns 200."""
        response = client.get("/api/v1/workloads")
        assert response.status_code == 200

    def test_workloads_returns_profiles(self, client):
        """Workloads endpoint returns profiles array."""
        response = client.get("/api/v1/workloads")
        data = response.json()
        assert "profiles" in data
        assert isinstance(data["profiles"], list)
        assert len(data["profiles"]) >= 4

    def test_workload_profiles_have_required_fields(self, client):
        """Each workload profile has required fields."""
        response = client.get("/api/v1/workloads")
        for profile in response.json()["profiles"]:
            assert "id" in profile
            assert "name" in profile
            assert "daily_volume" in profile
            assert "signal_distribution" in profile


# --- Scenarios endpoint ---

class TestScenariosEndpoint:
    """Test GET /api/v1/scenarios."""

    def test_scenarios_returns_200(self, client):
        """Scenarios endpoint returns 200."""
        response = client.get("/api/v1/scenarios")
        assert response.status_code == 200

    def test_scenarios_returns_list(self, client):
        """Scenarios endpoint returns scenarios array."""
        response = client.get("/api/v1/scenarios")
        data = response.json()
        assert "scenarios" in data
        assert isinstance(data["scenarios"], list)
        assert len(data["scenarios"]) >= 3

    def test_scenarios_have_required_fields(self, client):
        """Each scenario has required fields."""
        response = client.get("/api/v1/scenarios")
        for scenario in response.json()["scenarios"]:
            assert "id" in scenario
            assert "name" in scenario
            assert "workload_id" in scenario


# --- Calculate endpoint ---

class TestCalculateEndpoint:
    """Test POST /api/v1/calculate."""

    def test_calculate_returns_200(self, client):
        """Calculate endpoint returns 200 for valid input."""
        payload = {
            "workload": {
                "id": "test",
                "name": "Test",
                "description": "Test workload",
                "daily_volume": 100000,
                "signal_distribution": {
                    "routine_pct": 85,
                    "ambiguous_pct": 12,
                    "complex_pct": 3,
                },
                "tokens_per_signal": {"input": 150, "output": 20},
                "latency_requirement_ms": 500,
                "model_by_tier": {
                    "nano": None,
                    "micro": "granite-2b",
                    "macro": "phi3-mini",
                },
            }
        }
        response = client.post("/api/v1/calculate", json=payload)
        assert response.status_code == 200

    def test_calculate_returns_tco_result(self, client):
        """Calculate endpoint returns a full TCO result."""
        payload = {
            "workload": {
                "id": "test",
                "name": "Test",
                "description": "Test workload",
                "daily_volume": 100000,
                "signal_distribution": {
                    "routine_pct": 85,
                    "ambiguous_pct": 12,
                    "complex_pct": 3,
                },
                "tokens_per_signal": {"input": 150, "output": 20},
                "latency_requirement_ms": 500,
                "model_by_tier": {
                    "nano": None,
                    "micro": "granite-2b",
                    "macro": "phi3-mini",
                },
            }
        }
        response = client.post("/api/v1/calculate", json=payload)
        data = response.json()
        assert "workload_id" in data
        assert "cascade_summary" in data
        assert "comparisons" in data
        assert len(data["comparisons"]) >= 3

    def test_calculate_cascade_summary_fields(self, client):
        """Cascade summary has all required fields."""
        payload = {
            "workload": {
                "id": "test",
                "name": "Test",
                "description": "Test workload",
                "daily_volume": 100000,
                "signal_distribution": {
                    "routine_pct": 85,
                    "ambiguous_pct": 12,
                    "complex_pct": 3,
                },
                "tokens_per_signal": {"input": 150, "output": 20},
                "latency_requirement_ms": 500,
                "model_by_tier": {
                    "nano": None,
                    "micro": "granite-2b",
                    "macro": "phi3-mini",
                },
            }
        }
        response = client.post("/api/v1/calculate", json=payload)
        cascade = response.json()["cascade_summary"]
        assert "total_signals_per_day" in cascade
        assert "nano_signals_per_day" in cascade
        assert "micro_signals_per_day" in cascade
        assert "macro_signals_per_day" in cascade
        assert "inference_signals_per_day" in cascade
        assert "compression_ratio" in cascade

    def test_calculate_comparison_fields(self, client):
        """Each comparison has all required fields."""
        payload = {
            "workload": {
                "id": "test",
                "name": "Test",
                "description": "Test workload",
                "daily_volume": 100000,
                "signal_distribution": {
                    "routine_pct": 85,
                    "ambiguous_pct": 12,
                    "complex_pct": 3,
                },
                "tokens_per_signal": {"input": 150, "output": 20},
                "latency_requirement_ms": 500,
                "model_by_tier": {
                    "nano": None,
                    "micro": "granite-2b",
                    "macro": "phi3-mini",
                },
            }
        }
        response = client.post("/api/v1/calculate", json=payload)
        for comp in response.json()["comparisons"]:
            assert "hardware_id" in comp
            assert "hardware_name" in comp
            assert "hardware_type" in comp
            assert "units_required" in comp
            assert "hardware_cost_usd" in comp
            assert "annual_power_cost_usd" in comp
            assert "annual_inference_cost_usd" in comp
            assert "three_year_tco_usd" in comp
            assert "cost_per_signal_usd" in comp

    def test_calculate_rejects_invalid_input(self, client):
        """Calculate endpoint returns 422 for invalid input."""
        response = client.post("/api/v1/calculate", json={"bad": "data"})
        assert response.status_code == 422

    def test_calculate_with_assumptions(self, client):
        """Calculate endpoint accepts custom assumptions."""
        payload = {
            "workload": {
                "id": "test",
                "name": "Test",
                "description": "Test workload",
                "daily_volume": 100000,
                "signal_distribution": {
                    "routine_pct": 85,
                    "ambiguous_pct": 12,
                    "complex_pct": 3,
                },
                "tokens_per_signal": {"input": 150, "output": 20},
                "latency_requirement_ms": 500,
                "model_by_tier": {
                    "nano": None,
                    "micro": "granite-2b",
                    "macro": "phi3-mini",
                },
            },
            "assumptions": {
                "power_cost_per_kwh": 0.12,
                "idle_hardware": True,
                "tco_years": 5,
            },
        }
        response = client.post("/api/v1/calculate", json=payload)
        assert response.status_code == 200

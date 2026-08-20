"""Shared fixtures for TCO calculator tests."""

import json
import os
import sys
from pathlib import Path

import pytest

# Add app to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def project_root():
    """Return the project root directory."""
    return ROOT


@pytest.fixture
def hardware_profiles():
    """Load hardware profiles from data file."""
    path = ROOT / "data" / "hardware_profiles.json"
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def workload_profiles():
    """Load workload profiles from data file."""
    path = ROOT / "data" / "workload_profiles.json"
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def model_profiles():
    """Load model profiles from data file."""
    path = ROOT / "data" / "model_profiles.json"
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def dispute_workload(workload_profiles):
    """Return the dispute resolution workload profile."""
    for p in workload_profiles["profiles"]:
        if p["id"] == "dispute-resolution":
            return p
    raise ValueError("dispute-resolution workload not found")


@pytest.fixture
def fraud_workload(workload_profiles):
    """Return the fraud case triage workload profile."""
    for p in workload_profiles["profiles"]:
        if p["id"] == "fraud-case-triage":
            return p
    raise ValueError("fraud-case-triage workload not found")


@pytest.fixture
def xeon6_profile(hardware_profiles):
    """Return the Xeon 6 hardware profile."""
    for p in hardware_profiles["profiles"]:
        if p["id"] == "xeon6-6780e":
            return p
    raise ValueError("xeon6-6780e hardware profile not found")


@pytest.fixture
def h100_profile(hardware_profiles):
    """Return the H100 hardware profile."""
    for p in hardware_profiles["profiles"]:
        if p["id"] == "h100-sxm":
            return p
    raise ValueError("h100-sxm hardware profile not found")


@pytest.fixture
def cloud_api_profile(hardware_profiles):
    """Return the frontier cloud API hardware profile."""
    for p in hardware_profiles["profiles"]:
        if p["id"] == "cloud-api-frontier":
            return p
    raise ValueError("cloud-api-frontier hardware profile not found")

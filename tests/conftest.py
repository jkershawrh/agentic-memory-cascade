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

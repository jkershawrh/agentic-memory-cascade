"""Stage 0: Contract and schema validation tests.

Validates that all contracts and schemas are well-formed.
"""

import json
from pathlib import Path

import jsonschema
import yaml
import pytest


ROOT = Path(__file__).resolve().parent.parent


class TestJSONSchemas:
    """Validate the JSON schemas themselves."""

    @pytest.mark.parametrize("schema_name", [
        "agent-promotion",
        "audit-verdict",
        "decision-record",
        "memory-event",
        "memory-record",
    ])
    def test_schema_parses(self, schema_name):
        path = ROOT / "contracts" / "schemas" / f"{schema_name}.json"
        with open(path) as f:
            schema = json.load(f)
        jsonschema.Draft202012Validator.check_schema(schema)


class TestValidationMatrix:
    """Validate the validation matrix itself."""

    def test_matrix_parses(self):
        path = ROOT / "tests" / "validation_matrix.yaml"
        with open(path) as f:
            matrix = yaml.safe_load(f)
        assert "stages" in matrix

    def test_matrix_has_stages(self):
        path = ROOT / "tests" / "validation_matrix.yaml"
        with open(path) as f:
            matrix = yaml.safe_load(f)
        assert len(matrix["stages"]) >= 3, "Validation matrix should have at least 3 stages"

    def test_matrix_criteria_have_points(self):
        path = ROOT / "tests" / "validation_matrix.yaml"
        with open(path) as f:
            matrix = yaml.safe_load(f)
        for stage_name, stage in matrix["stages"].items():
            for crit_name, crit in stage.get("criteria", {}).items():
                assert "points" in crit, f"{stage_name}.{crit_name} missing points"


class TestDeployManifests:
    """Validate deployment YAML parses."""

    def test_openshift_manifest_parses(self):
        path = ROOT / "deploy" / "openshift.yaml"
        if not path.exists():
            pytest.skip("No openshift.yaml")
        docs = list(yaml.safe_load_all(path.read_text()))
        assert len(docs) > 0

    def test_config_yaml_parses(self):
        config_dir = ROOT / "config"
        if not config_dir.exists():
            pytest.skip("No config directory")
        for f in config_dir.glob("*.yaml"):
            data = yaml.safe_load(f.read_text())
            assert data is not None, f"{f.name} is empty"

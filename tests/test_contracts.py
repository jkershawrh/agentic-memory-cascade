"""Stage 0: Contract and schema validation tests.

Validates that all contracts, schemas, and data files are well-formed
and internally consistent.
"""

import json
from pathlib import Path

import jsonschema
import yaml
import pytest


ROOT = Path(__file__).resolve().parent.parent


# --- OpenAPI spec validation ---

class TestOpenAPISpec:
    """Validate the OpenAPI specification."""

    def test_openapi_spec_parses(self):
        """OpenAPI 3.1 spec loads as valid YAML."""
        path = ROOT / "contracts" / "openapi" / "tco-calculator.yaml"
        with open(path) as f:
            spec = yaml.safe_load(f)
        assert spec is not None

    def test_openapi_version(self):
        """Spec declares OpenAPI 3.1.0."""
        path = ROOT / "contracts" / "openapi" / "tco-calculator.yaml"
        with open(path) as f:
            spec = yaml.safe_load(f)
        assert spec["openapi"] == "3.1.0"

    def test_openapi_has_required_paths(self):
        """Spec includes all required API paths."""
        path = ROOT / "contracts" / "openapi" / "tco-calculator.yaml"
        with open(path) as f:
            spec = yaml.safe_load(f)
        required_paths = ["/health", "/api/v1/hardware", "/api/v1/workloads",
                          "/api/v1/scenarios", "/api/v1/calculate"]
        for p in required_paths:
            assert p in spec["paths"], f"Missing path: {p}"

    def test_openapi_calculate_is_post(self):
        """The /calculate endpoint must be POST."""
        path = ROOT / "contracts" / "openapi" / "tco-calculator.yaml"
        with open(path) as f:
            spec = yaml.safe_load(f)
        assert "post" in spec["paths"]["/api/v1/calculate"]

    def test_openapi_has_schemas(self):
        """Spec defines required component schemas."""
        path = ROOT / "contracts" / "openapi" / "tco-calculator.yaml"
        with open(path) as f:
            spec = yaml.safe_load(f)
        required_schemas = ["HardwareProfile", "WorkloadProfile", "TCOResult",
                            "SignalDistribution", "CascadeSummary",
                            "HardwareComparison", "CalculateRequest"]
        for s in required_schemas:
            assert s in spec["components"]["schemas"], f"Missing schema: {s}"


# --- JSON Schema validation ---

class TestJSONSchemas:
    """Validate the JSON schemas themselves."""

    def test_hardware_schema_parses(self):
        """Hardware schema is valid JSON Schema."""
        path = ROOT / "contracts" / "schemas" / "hardware.json"
        with open(path) as f:
            schema = json.load(f)
        # Should not raise
        jsonschema.Draft202012Validator.check_schema(schema)

    def test_workload_schema_parses(self):
        """Workload schema is valid JSON Schema."""
        path = ROOT / "contracts" / "schemas" / "workload.json"
        with open(path) as f:
            schema = json.load(f)
        jsonschema.Draft202012Validator.check_schema(schema)

    def test_tco_result_schema_parses(self):
        """TCO result schema is valid JSON Schema."""
        path = ROOT / "contracts" / "schemas" / "tco-result.json"
        with open(path) as f:
            schema = json.load(f)
        jsonschema.Draft202012Validator.check_schema(schema)


# --- Data file validation against schemas ---

class TestDataFiles:
    """Validate data files against their schemas."""

    def test_hardware_profiles_valid(self, hardware_profiles):
        """Each hardware profile validates against the hardware schema."""
        schema_path = ROOT / "contracts" / "schemas" / "hardware.json"
        with open(schema_path) as f:
            schema = json.load(f)
        validator = jsonschema.Draft202012Validator(schema)
        for profile in hardware_profiles["profiles"]:
            errors = list(validator.iter_errors(profile))
            assert len(errors) == 0, (
                f"Hardware profile '{profile['id']}' validation errors: "
                f"{[e.message for e in errors]}"
            )

    def test_workload_profiles_valid(self, workload_profiles):
        """Each workload profile validates against the workload schema."""
        schema_path = ROOT / "contracts" / "schemas" / "workload.json"
        with open(schema_path) as f:
            schema = json.load(f)
        validator = jsonschema.Draft202012Validator(schema)
        for profile in workload_profiles["profiles"]:
            errors = list(validator.iter_errors(profile))
            assert len(errors) == 0, (
                f"Workload profile '{profile['id']}' validation errors: "
                f"{[e.message for e in errors]}"
            )

    def test_hardware_profiles_has_all_types(self, hardware_profiles):
        """Hardware profiles include at least one cpu, gpu, and cloud_api."""
        types = {p["type"] for p in hardware_profiles["profiles"]}
        assert "cpu" in types, "Missing CPU hardware profile"
        assert "gpu" in types, "Missing GPU hardware profile"
        assert "cloud_api" in types, "Missing cloud API hardware profile"

    def test_workload_profiles_count(self, workload_profiles):
        """At least 4 workload profiles exist."""
        assert len(workload_profiles["profiles"]) >= 4

    def test_signal_distributions_sum_to_100(self, workload_profiles):
        """Every workload's signal distribution sums to 100%."""
        for profile in workload_profiles["profiles"]:
            dist = profile["signal_distribution"]
            total = dist["routine_pct"] + dist["ambiguous_pct"] + dist["complex_pct"]
            assert total == 100, (
                f"Workload '{profile['id']}' signal distribution sums to "
                f"{total}, not 100"
            )

    def test_workload_models_exist_in_hardware(self, workload_profiles,
                                                hardware_profiles):
        """Models referenced by workloads exist in at least one hardware profile."""
        # Collect all models from hardware throughput data
        all_models = set()
        for hp in hardware_profiles["profiles"]:
            if "inference_throughput" in hp:
                all_models.update(hp["inference_throughput"].keys())

        for wp in workload_profiles["profiles"]:
            tiers = wp["model_by_tier"]
            for tier_name, model_id in tiers.items():
                if model_id is not None:
                    assert model_id in all_models, (
                        f"Workload '{wp['id']}' tier '{tier_name}' references "
                        f"model '{model_id}' not found in any hardware profile"
                    )


# --- Validation matrix ---

class TestValidationMatrix:
    """Validate the validation matrix itself."""

    def test_matrix_parses(self):
        """Validation matrix is valid YAML."""
        path = ROOT / "tests" / "validation_matrix.yaml"
        with open(path) as f:
            matrix = yaml.safe_load(f)
        assert "stages" in matrix

    def test_matrix_has_all_stages(self):
        """Matrix includes all 4 stages."""
        path = ROOT / "tests" / "validation_matrix.yaml"
        with open(path) as f:
            matrix = yaml.safe_load(f)
        expected = ["stage_0_contracts", "stage_1_calculations",
                    "stage_2_scenarios", "stage_3_api"]
        for stage in expected:
            assert stage in matrix["stages"], f"Missing stage: {stage}"

    def test_matrix_criteria_have_points(self):
        """Every criterion in the matrix has points assigned."""
        path = ROOT / "tests" / "validation_matrix.yaml"
        with open(path) as f:
            matrix = yaml.safe_load(f)
        for stage_name, stage in matrix["stages"].items():
            for crit_name, crit in stage["criteria"].items():
                assert "points" in crit, (
                    f"Criterion '{crit_name}' in '{stage_name}' missing points"
                )
                assert crit["points"] > 0

"""Tests for cascade_compression.routing.task_mapping."""

import pytest

from cascade_compression.routing.task_mapping import (
    DEEPFIELD_TASK_TO_BENCHMARK_SHAPE,
    get_vertical_quality_gaps,
    get_vertical_sla,
    resolve_benchmark_task,
)


# ---------------------------------------------------------------------------
# Reset verticals cache between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_cache():
    import cascade_compression.routing.task_mapping as mod
    mod._verticals_cache = None
    yield
    mod._verticals_cache = None


# ---------------------------------------------------------------------------
# Industry override resolution
# ---------------------------------------------------------------------------


class TestResolveIndustryOverride:
    def test_fsi_classify_signal_returns_fraud_scoring(self):
        """FSI + classify_signal should resolve to 'fraud-scoring' (industry override)."""
        result = resolve_benchmark_task("classify_signal", industry="fsi")
        assert result == "fraud-scoring"

    def test_fsi_summarize_finding_returns_loan_document_extraction(self):
        result = resolve_benchmark_task("summarize_finding", industry="fsi")
        assert result == "loan-document-extraction"

    def test_fsi_suggest_remediation_returns_compliance_screening(self):
        result = resolve_benchmark_task("suggest_remediation", industry="fsi")
        assert result == "compliance-screening"

    def test_healthcare_classify_signal_returns_clinical_classification(self):
        result = resolve_benchmark_task("classify_signal", industry="healthcare")
        assert result == "clinical-classification"

    def test_telecom_root_cause_analysis_returns_churn_prediction(self):
        result = resolve_benchmark_task("root_cause_analysis", industry="telecom")
        assert result == "churn-prediction"


# ---------------------------------------------------------------------------
# Generic shape resolution
# ---------------------------------------------------------------------------


class TestResolveGenericShape:
    def test_basic_classify_signal_returns_classify_short(self):
        """basic + classify_signal should use generic shape (no override)."""
        result = resolve_benchmark_task("classify_signal", industry="basic")
        assert result == "classify-short"

    def test_basic_root_cause_analysis(self):
        result = resolve_benchmark_task("root_cause_analysis", industry="basic")
        assert result == "generate-qa"

    def test_basic_summarize_finding(self):
        result = resolve_benchmark_task("summarize_finding", industry="basic")
        assert result == "summarize-long"

    def test_basic_suggest_remediation(self):
        result = resolve_benchmark_task("suggest_remediation", industry="basic")
        assert result == "generate-qa"


# ---------------------------------------------------------------------------
# Default fallback
# ---------------------------------------------------------------------------


class TestResolveDefault:
    def test_unknown_task_type_returns_classify_short(self):
        """Unknown task_type should fall back to 'classify-short'."""
        result = resolve_benchmark_task("totally_unknown_task", industry="basic")
        assert result == "classify-short"

    def test_unknown_task_in_fsi_with_no_override(self):
        """FSI + unknown task (no override) falls back to generic shape or default."""
        result = resolve_benchmark_task("totally_unknown_task", industry="fsi")
        assert result == "classify-short"

    def test_unknown_industry_unknown_task(self):
        result = resolve_benchmark_task("unknown_task", industry="nonexistent")
        assert result == "classify-short"


# ---------------------------------------------------------------------------
# Shape mapping completeness
# ---------------------------------------------------------------------------


class TestShapeMappings:
    EXPECTED_TASKS = [
        "classify_signal",
        "filter_noise",
        "summarize_finding",
        "explain_signal",
        "fleet_summary",
        "suggest_remediation",
        "root_cause_analysis",
        "deep_root_cause_analysis",
        "cross_cluster_correlation",
        "correlate_findings",
        "capacity_estimate",
        "embed_signal",
        "embed_document",
        "semantic_search",
    ]

    def test_all_tasks_mapped(self):
        assert len(DEEPFIELD_TASK_TO_BENCHMARK_SHAPE) == 14

    def test_expected_tasks_present(self):
        for task in self.EXPECTED_TASKS:
            assert task in DEEPFIELD_TASK_TO_BENCHMARK_SHAPE, f"Missing: {task}"

    def test_shapes_are_valid(self):
        valid_shapes = {
            "classify-short",
            "extract-medium",
            "summarize-long",
            "generate-qa",
            "encode-text",
            "encode-document",
            "similarity-search",
        }
        for task, shape in DEEPFIELD_TASK_TO_BENCHMARK_SHAPE.items():
            assert shape in valid_shapes, f"{task} -> {shape} not a valid shape"

    def test_embedding_tasks_mapped(self):
        assert DEEPFIELD_TASK_TO_BENCHMARK_SHAPE["embed_signal"] == "encode-text"
        assert DEEPFIELD_TASK_TO_BENCHMARK_SHAPE["embed_document"] == "encode-document"
        assert DEEPFIELD_TASK_TO_BENCHMARK_SHAPE["semantic_search"] == "similarity-search"


# ---------------------------------------------------------------------------
# SLA + quality gaps
# ---------------------------------------------------------------------------


class TestVerticalHelpers:
    def test_fsi_sla(self):
        sla = get_vertical_sla("fsi")
        assert sla == 200

    def test_basic_sla(self):
        sla = get_vertical_sla("basic")
        assert sla == 500

    def test_unknown_sla_default(self):
        sla = get_vertical_sla("nonexistent")
        assert sla == 2000

    def test_fsi_quality_gaps(self):
        gaps = get_vertical_quality_gaps("fsi")
        assert len(gaps) > 0
        assert any(
            "compliance" in g.get("task", "").lower()
            or "compliance" in g.get("note", "").lower()
            for g in gaps
        )

    def test_basic_no_quality_gaps(self):
        gaps = get_vertical_quality_gaps("basic")
        assert gaps == []

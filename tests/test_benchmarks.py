"""Tests for the benchmark harness, metrics, rubric, and reporter."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

BENCH_DIR = Path(__file__).parent.parent / "benchmarks"


class TestWorkloadParsing:
    """Validate all workload YAML files parse correctly."""

    @pytest.fixture
    def workload_files(self):
        return list((BENCH_DIR / "workloads").glob("*.yaml"))

    def test_all_industries_have_workloads(self, workload_files):
        names = {f.stem for f in workload_files}
        assert {"fsi", "healthcare", "insurance", "retail", "telecom"}.issubset(names)

    def test_workloads_parse(self, workload_files):
        for f in workload_files:
            data = yaml.safe_load(f.read_text())
            assert "industry" in data
            assert "tasks" in data
            assert len(data["tasks"]) >= 2

    def test_tasks_have_required_fields(self, workload_files):
        required = {"id", "name", "cascade_tier", "model_assignments", "max_tokens", "prompt"}
        for f in workload_files:
            data = yaml.safe_load(f.read_text())
            for task in data["tasks"]:
                missing = required - set(task.keys())
                assert not missing, f"{f.stem}/{task.get('id', '?')} missing: {missing}"

    def test_cascade_tiers_valid(self, workload_files):
        for f in workload_files:
            data = yaml.safe_load(f.read_text())
            for task in data["tasks"]:
                assert task["cascade_tier"] in ("nano", "micro", "macro"), (
                    f"{f.stem}/{task['id']}: invalid tier '{task['cascade_tier']}'"
                )

    def test_workflows_reference_valid_tasks(self, workload_files):
        for f in workload_files:
            data = yaml.safe_load(f.read_text())
            task_ids = {t["id"] for t in data["tasks"]}
            workflow = data.get("workflow")
            if workflow:
                for step in workflow["steps"]:
                    assert step["task"] in task_ids, (
                        f"{f.stem} workflow references unknown task '{step['task']}'"
                    )


class TestModelConfig:
    """Validate model roster config."""

    @pytest.fixture
    def models_config(self):
        path = BENCH_DIR / "configs" / "models.yaml"
        return yaml.safe_load(path.read_text())

    def test_has_cluster_info(self, models_config):
        assert "cluster" in models_config
        assert models_config["cluster"]["name"] == "oberon"

    def test_has_baseline_models(self, models_config):
        baseline = models_config["models"]["baseline"]
        assert len(baseline) >= 6
        aliases = {m["alias"] for m in baseline}
        assert "granite-2b-cpu" in aliases
        assert "qwen25-3b-cpu" in aliases

    def test_has_optimized_models(self, models_config):
        optimized = models_config["models"]["optimized"]
        assert len(optimized) >= 2
        aliases = {m["alias"] for m in optimized}
        assert "granite-2b-int8" in aliases
        assert "granite-2b-cpu-speculative" in aliases

    def test_has_model_ladder(self, models_config):
        ladder = models_config["ladder"]
        assert len(ladder) >= 6
        assert "granite-350m" in ladder


class TestBenchmarkMatrix:
    """Validate benchmark matrix thresholds."""

    @pytest.fixture
    def matrix_config(self):
        path = BENCH_DIR / "benchmark_matrix.yaml"
        return yaml.safe_load(path.read_text())

    def test_has_required_metrics(self, matrix_config):
        metrics = matrix_config["metrics"]
        required = {"throughput_tok_s", "latency_p95_ms", "quality_accuracy",
                     "cold_start_ms", "optimization_speedup", "batch_scaling"}
        assert required.issubset(set(metrics.keys()))

    def test_thresholds_are_ordered(self, matrix_config):
        for name, cfg in matrix_config["metrics"].items():
            t = cfg.get("thresholds", {})
            green = t.get("green", 0)
            yellow = t.get("yellow", 0)
            direction = cfg.get("direction", "higher_is_better")
            if direction == "lower_is_better":
                assert green <= yellow, f"{name}: green ({green}) should be <= yellow ({yellow})"
            else:
                assert green >= yellow, f"{name}: green ({green}) should be >= yellow ({yellow})"

    def test_has_protocol(self, matrix_config):
        protocol = matrix_config["protocol"]
        assert protocol["cold_start_samples"] >= 1
        assert protocol["warmup_samples"] >= 1
        assert protocol["steady_state_samples"] >= 10


class TestLeverConfigs:
    """Validate all lever config files."""

    @pytest.fixture
    def lever_files(self):
        return list((BENCH_DIR / "levers").glob("*.yaml"))

    def test_all_levers_exist(self, lever_files):
        names = {f.stem for f in lever_files}
        expected = {"baseline", "quantization", "speculative", "prefix_cache",
                    "adaptive_cache", "model_ladder", "routing", "batching", "composed"}
        assert expected.issubset(names)

    def test_levers_parse(self, lever_files):
        for f in lever_files:
            data = yaml.safe_load(f.read_text())
            assert "lever" in data, f"{f.stem} missing 'lever' field"


class TestMetricsAggregation:
    """Test metric calculation logic."""

    def test_percentile_basic(self):
        from cascade_compression.benchmarks.metrics import percentile
        data = list(range(1, 101))
        assert percentile(data, 50) == 50.5
        assert percentile(data, 95) == 95.05
        assert percentile(data, 99) == 99.01

    def test_percentile_empty(self):
        from cascade_compression.benchmarks.metrics import percentile
        assert percentile([], 50) == 0.0

    def test_aggregate_basic(self):
        from cascade_compression.benchmarks.metrics import SampleResult, aggregate
        samples = [
            SampleResult(
                model="test", task_id="t1", industry="fsi", lever="baseline",
                latency_ms=100, output_tokens=20, quality="correct",
            ),
            SampleResult(
                model="test", task_id="t1", industry="fsi", lever="baseline",
                latency_ms=200, output_tokens=30, quality="correct",
            ),
            SampleResult(
                model="test", task_id="t1", industry="fsi", lever="baseline",
                latency_ms=150, output_tokens=25, quality="incorrect",
            ),
        ]
        agg = aggregate(samples)
        assert agg.samples == 3
        assert agg.quality_accuracy == pytest.approx(0.6667, abs=0.01)
        assert agg.throughput_tok_s > 0

    def test_aggregate_cold_warm_split(self):
        from cascade_compression.benchmarks.metrics import SampleResult, aggregate
        samples = [
            SampleResult(
                model="test", task_id="t1", industry="fsi", lever="baseline",
                latency_ms=1000, output_tokens=20, is_cold=True,
            ),
            SampleResult(
                model="test", task_id="t1", industry="fsi", lever="baseline",
                latency_ms=100, output_tokens=20, is_cold=False,
            ),
        ]
        agg = aggregate(samples)
        assert agg.cold_start_ms == 1000
        assert agg.warm_steady_ms == 100
        assert agg.warm_cache_speedup == 10.0


class TestQualityChecks:
    """Test quality evaluation functions."""

    def test_exact_match(self):
        from cascade_compression.benchmarks.harness import check_quality
        task = {"quality_check": "exact_match", "quality_target": "discharge_summary"}
        assert check_quality("discharge_summary", task) == "correct"
        assert check_quality("DISCHARGE_SUMMARY", task) == "correct"
        assert check_quality("progress_note", task) == "incorrect"

    def test_substring(self):
        from cascade_compression.benchmarks.harness import check_quality
        task = {"quality_check": "substring", "quality_target": "Metformin"}
        assert check_quality("Found: Metformin 500mg", task) == "correct"
        assert check_quality("No entities found", task) == "incorrect"

    def test_structured_json(self):
        from cascade_compression.benchmarks.harness import check_quality
        task = {"quality_check": "structured_json", "quality_target": "risk_score"}
        assert check_quality('{"risk_score": 85, "risk_level": "high"}', task) == "correct"
        assert check_quality('{"other_field": 1}', task) == "incorrect"
        assert check_quality("not json at all with risk_score", task) == "correct"

    def test_length_check(self):
        from cascade_compression.benchmarks.harness import check_quality
        task = {"quality_check": "length_and_content", "quality_target": None}
        assert check_quality("A " * 50, task) == "correct"
        assert check_quality("Too short", task) == "incorrect"


class TestRubricGrading:
    """Test rubric grade evaluation."""

    def test_higher_is_better(self):
        from cascade_compression.benchmarks.rubric import grade_metric
        config = {
            "metrics": {
                "throughput_tok_s": {
                    "thresholds": {"green": 40, "yellow": 20},
                }
            }
        }
        assert grade_metric("throughput_tok_s", 50, config) == "green"
        assert grade_metric("throughput_tok_s", 30, config) == "yellow"
        assert grade_metric("throughput_tok_s", 10, config) == "red"

    def test_lower_is_better(self):
        from cascade_compression.benchmarks.rubric import grade_metric
        config = {
            "metrics": {
                "latency_p95_ms": {
                    "direction": "lower_is_better",
                    "thresholds": {"green": 500, "yellow": 2000},
                }
            }
        }
        assert grade_metric("latency_p95_ms", 300, config) == "green"
        assert grade_metric("latency_p95_ms", 1000, config) == "yellow"
        assert grade_metric("latency_p95_ms", 5000, config) == "red"

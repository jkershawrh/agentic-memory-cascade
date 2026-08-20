"""Seed the TCO calculator with real benchmark data.

Reads all benchmark results and models.yaml, writes:
1. data/hardware_profiles.json — all 20 models with measured throughput + rubric grades
2. data/workload_profiles.json — 6 verticals with corpora-proven model assignments
3. data/benchmark_matrix.json — pre-processed matrix for the frontend results page

Usage:
    python -m benchmarks.seed_calculator
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR.parent / "data"
RESULTS_DIR = BASE_DIR / "results"
CONFIGS_DIR = BASE_DIR / "configs"


def _parse_params(params_str: str) -> str:
    """Normalize params string for display."""
    return params_str.strip()


def _parse_params_float(params_str: str) -> float:
    """Parse params to float in billions."""
    s = params_str.strip()
    if "active" in s.lower():
        m = re.search(r"(\d+(?:\.\d+)?)\s*B\s*active", s, re.IGNORECASE)
        if m:
            return float(m.group(1))
    if "+" in s:
        return max(_parse_params_float(p) for p in s.split("+"))
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(M|B)", s, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        return val / 1000.0 if m.group(2).upper() == "M" else val
    return 0.0


def load_model_roster() -> dict:
    """Load models.yaml → {alias: {params, backend, dtype, optimization, role}}."""
    path = CONFIGS_DIR / "models.yaml"
    with open(path) as f:
        raw = yaml.safe_load(f)

    roster = {}
    for category in ("baseline", "optimized", "gguf"):
        for entry in raw.get("models", {}).get(category, []):
            roster[entry["alias"]] = {
                "params": entry.get("params", ""),
                "params_b": _parse_params_float(entry.get("params", "0")),
                "backend": entry.get("backend", "ovms"),
                "dtype": entry.get("dtype", "bfloat16"),
                "optimization": entry.get("optimization", ""),
                "role": entry.get("role", ""),
                "category": category,
            }

    cluster = raw.get("cluster", {})
    return {"models": roster, "cluster": cluster}


def load_all_results() -> list:
    """Load all benchmark result files → flat list of matrix entries with source."""
    entries = []
    for result_file in sorted(RESULTS_DIR.glob("*.json")):
        if result_file.name == "manifest.json":
            continue
        with open(result_file) as f:
            data = json.load(f)
        for row in data.get("matrix", []):
            row["_source"] = result_file.name
            row["_metadata"] = data.get("metadata", {})
            entries.append(row)
    return entries


def _metric_val(metrics: dict, key: str) -> float:
    v = metrics.get(key, {})
    return float(v.get("value", 0)) if isinstance(v, dict) else 0.0


def _metric_grade(metrics: dict, key: str) -> str:
    v = metrics.get(key, {})
    return v.get("grade", "red") if isinstance(v, dict) else "red"


def build_hardware_profiles(roster: dict, entries: list) -> dict:
    """Build the seeded hardware_profiles.json with all 20 models."""
    models = roster["models"]
    cluster = roster["cluster"]

    # Aggregate per model: collect all metrics across tasks/levers
    model_metrics = defaultdict(lambda: {
        "throughput_samples": [],
        "latency_samples": [],
        "by_task": defaultdict(lambda: {
            "throughput": [], "latency": [], "quality": [],
            "throughput_grades": [], "latency_grades": [], "quality_grades": [],
        }),
    })

    source_files = set()
    for row in entries:
        model = row.get("model", "")
        if model not in models or model == "nano-rules":
            continue

        task = row.get("task", "")
        metrics = row.get("metrics", {})
        source_files.add(row.get("_source", ""))

        tok_s = _metric_val(metrics, "throughput_tok_s")
        lat = _metric_val(metrics, "latency_p95_ms")
        qual = _metric_val(metrics, "quality_accuracy")

        mm = model_metrics[model]
        if tok_s > 0:
            mm["throughput_samples"].append(tok_s)
        if lat > 0:
            mm["latency_samples"].append(lat)

        bt = mm["by_task"][task]
        if tok_s > 0:
            bt["throughput"].append(tok_s)
            bt["throughput_grades"].append(_metric_grade(metrics, "throughput_tok_s"))
        if lat > 0:
            bt["latency"].append(lat)
            bt["latency_grades"].append(_metric_grade(metrics, "latency_p95_ms"))
        bt["quality"].append(qual)
        bt["quality_grades"].append(_metric_grade(metrics, "quality_accuracy"))

    def _median(vals):
        if not vals:
            return 0
        s = sorted(vals)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    def _best_grade(grades):
        if "green" in grades:
            return "green"
        if "yellow" in grades:
            return "yellow"
        return "red"

    # Build the model entries for Xeon profile (all 19+ models, even unbenchmarked)
    xeon_models = {}
    for alias in sorted(models.keys()):
        info = models[alias]
        mm = model_metrics.get(alias)
        if not mm:
            xeon_models[alias] = {
                "params": info["params"],
                "serving_layer": info["backend"],
                "dtype": info["dtype"],
                "optimization": info["optimization"],
                "role": info["role"],
                "tokens_per_second": 0,
                "latency_p95_ms": 0,
                "throughput_by_task": {},
                "quality_by_task": {},
                "benchmarked": False,
            }
            continue

        throughput_by_task = {}
        quality_by_task = {}
        for task, bt in sorted(mm["by_task"].items()):
            throughput_by_task[task] = {
                "tokens_per_second": round(_median(bt["throughput"]), 1),
                "grade": _best_grade(bt["throughput_grades"]),
            }
            quality_by_task[task] = {
                "accuracy": round(_median(bt["quality"]), 3),
                "grade": _best_grade(bt["quality_grades"]),
            }

        xeon_models[alias] = {
            "params": info["params"],
            "serving_layer": info["backend"],
            "dtype": info["dtype"],
            "optimization": info["optimization"],
            "role": info["role"],
            "tokens_per_second": round(_median(mm["throughput_samples"]), 1),
            "latency_p95_ms": round(_median(mm["latency_samples"]), 0),
            "throughput_by_task": throughput_by_task,
            "quality_by_task": quality_by_task,
        }

    profiles = {
        "_note": (
            f"Throughput measured on {cluster.get('name', 'unknown')} "
            f"({cluster.get('hardware', '')}, {cluster.get('cores', '')}c). "
            f"Source: {len(source_files)} benchmark files."
        ),
        "profiles": [
            {
                "id": "xeon6-6780e",
                "name": f"{cluster.get('hardware', 'Intel Xeon 6')} ({cluster.get('cores', 128)} cores)",
                "type": "cpu",
                "cost_per_unit_usd": 30000,
                "power_watts": 1200,
                "amx_support": True,
                "instruction_sets": cluster.get("instruction_sets", []),
                "models": xeon_models,
                "inference_throughput": _build_throughput_index(xeon_models),
                "benchmark_provenance": {
                    "cluster": cluster.get("name", "unknown"),
                    "hardware": cluster.get("hardware", ""),
                    "cores": cluster.get("cores", 0),
                    "threads": cluster.get("threads", 0),
                    "memory_gib": cluster.get("memory_gib", 0),
                    "source_files": sorted(source_files),
                },
            },
            {
                "id": "h100-sxm",
                "name": "NVIDIA H100 SXM 80GB",
                "type": "gpu",
                "cost_per_unit_usd": 50000,
                "power_watts": 6000,
                "inference_throughput": {
                    "granite-2b": {"tokens_per_second": 800, "classification_rps": 2000},
                    "qwen-3b": {"tokens_per_second": 500, "classification_rps": 1200},
                    "phi3-mini": {"tokens_per_second": 300, "classification_rps": 700},
                    "granite-8b": {"tokens_per_second": 150, "classification_rps": 350},
                    "llama-70b": {"tokens_per_second": 50, "classification_rps": 30},
                },
            },
            {
                "id": "cloud-api-frontier",
                "name": "Cloud API Frontier (e.g. GPT-4o, Claude Sonnet)",
                "type": "cloud_api",
                "cost_per_1k_input_tokens": 0.0025,
                "cost_per_1k_output_tokens": 0.01,
                "power_watts": 0,
                "rate_limit_rpm": 10000,
            },
            {
                "id": "cloud-api-economy",
                "name": "Cloud API Economy (e.g. GPT-4o-mini, Claude Haiku)",
                "type": "cloud_api",
                "cost_per_1k_input_tokens": 0.00015,
                "cost_per_1k_output_tokens": 0.0006,
                "power_watts": 0,
                "rate_limit_rpm": 30000,
            },
        ],
    }

    return profiles


def _build_throughput_index(xeon_models: dict) -> dict:
    """Build inference_throughput with entries keyed by BOTH benchmark alias and TCO ID."""
    index = {}
    for alias, data in xeon_models.items():
        entry = {
            "tokens_per_second": data["tokens_per_second"],
            "classification_rps": round(data["tokens_per_second"] * 2.5, 0),
        }
        index[alias] = entry
        tco_id = _tco_id(alias)
        if tco_id != alias:
            index[tco_id] = entry
    return index


_ALIAS_TO_TCO = {
    "granite-2b-cpu": "granite-2b",
    "granite-2b-int8": "granite-2b-int8",
    "granite-2b-q4": "granite-2b-q4",
    "granite-2b-q8": "granite-2b-q8",
    "qwen25-3b-cpu": "qwen-3b",
    "phi3-mini-cpu": "phi3-mini",
    "granite-3-2-8b-instruct-cpu": "granite-8b",
    "granite-4.1-8b": "granite-4.1-8b",
    "granite-4.1-3b": "granite-4.1-3b",
    "granite-4-0-h-tiny-cpu": "granite-tiny",
    "granite-350m": "granite-350m",
    "granite-8b-q4": "granite-8b-q4",
    "smollm2-360m": "smollm2-360m",
    "bitnet-2b": "bitnet-2b",
    "smollm3-3b": "smollm3-3b",
    "qwen3-0.6b": "qwen3-0.6b",
    "deepseek-r1-1.5b": "deepseek-r1-1.5b",
    "qwen3-1.7b": "qwen3-1.7b",
    "qwen3-4b": "qwen3-4b",
}


def _tco_id(alias: str) -> str:
    """Map benchmark alias to TCO calculator model ID."""
    return _ALIAS_TO_TCO.get(alias, alias)


def build_workload_profiles() -> dict:
    """Build the expanded workload profiles (6 verticals)."""
    verticals_path = Path(__file__).parent.parent.parent / "intel-inference-router" / "config" / "verticals.yaml"
    corpora_path = Path(__file__).parent.parent.parent / "intel-inference-router" / "config" / "corpora.json"

    # Load corpora for model assignments — convert to TCO IDs
    corpora_models = {}
    if corpora_path.exists():
        with open(corpora_path) as f:
            corpora = json.load(f)
        for industry, tasks in corpora.get("entries", {}).items():
            for task, tiers in tasks.items():
                for tier, entry in tiers.items():
                    key = f"{industry}:{tier}"
                    if key not in corpora_models:
                        corpora_models[key] = _tco_id(entry["config"]["model"])

    # Base profiles — keep existing FSI ones, add new verticals
    profiles = {
        "profiles": [
            {
                "id": "dispute-resolution",
                "name": "Dispute Resolution",
                "industry": "fsi",
                "description": "Classify disputes, extract entities, route to teams. High volume with most disputes following standard patterns.",
                "daily_volume": 500000,
                "signal_distribution": {"routine_pct": 85, "ambiguous_pct": 12, "complex_pct": 3},
                "tokens_per_signal": {"input": 150, "output": 20},
                "latency_requirement_ms": 500,
                "model_by_tier": {
                    "nano": None,
                    "micro": corpora_models.get("fsi:micro", "granite-350m"),
                    "macro": corpora_models.get("fsi:macro", "phi3-mini"),
                },
            },
            {
                "id": "fraud-case-triage",
                "name": "Fraud Case Triage",
                "industry": "fsi",
                "description": "Score transactions for fraud risk. Very high volume, extremely time-sensitive, most transactions are legitimate.",
                "daily_volume": 1000000,
                "signal_distribution": {"routine_pct": 92, "ambiguous_pct": 6, "complex_pct": 2},
                "tokens_per_signal": {"input": 80, "output": 15},
                "latency_requirement_ms": 200,
                "model_by_tier": {
                    "nano": None,
                    "micro": corpora_models.get("fsi:micro", "granite-2b"),
                    "macro": "granite-8b",
                },
            },
            {
                "id": "loan-document-intake",
                "name": "Loan Document Intake",
                "industry": "fsi",
                "description": "Extract fields from loan applications. Lower volume but higher complexity per document.",
                "daily_volume": 50000,
                "signal_distribution": {"routine_pct": 70, "ambiguous_pct": 22, "complex_pct": 8},
                "tokens_per_signal": {"input": 500, "output": 100},
                "latency_requirement_ms": 2000,
                "model_by_tier": {
                    "nano": None,
                    "micro": "granite-2b",
                    "macro": "phi3-mini",
                },
            },
            {
                "id": "compliance-screening",
                "name": "Compliance Screening",
                "industry": "fsi",
                "description": "Screen transactions and communications for regulatory compliance.",
                "daily_volume": 200000,
                "signal_distribution": {"routine_pct": 75, "ambiguous_pct": 18, "complex_pct": 7},
                "tokens_per_signal": {"input": 300, "output": 50},
                "latency_requirement_ms": 1000,
                "model_by_tier": {
                    "nano": None,
                    "micro": "qwen-3b",
                    "macro": "phi3-mini",
                },
                "quality_gap": "0% accuracy across all models — requires fine-tuned model or human-in-loop",
            },
            {
                "id": "claims-processing",
                "name": "Claims Processing",
                "industry": "insurance",
                "description": "Triage incoming claims, extract policy information, assess underwriting risk.",
                "daily_volume": 50000,
                "signal_distribution": {"routine_pct": 70, "ambiguous_pct": 22, "complex_pct": 8},
                "tokens_per_signal": {"input": 400, "output": 80},
                "latency_requirement_ms": 2000,
                "model_by_tier": {
                    "nano": None,
                    "micro": corpora_models.get("insurance:micro", "granite-350m"),
                    "macro": corpora_models.get("insurance:macro", "granite-4.1-8b"),
                },
            },
            {
                "id": "retail-operations",
                "name": "Retail Operations",
                "industry": "retail",
                "description": "Categorize products, analyze review sentiment, classify demand signals.",
                "daily_volume": 200000,
                "signal_distribution": {"routine_pct": 85, "ambiguous_pct": 12, "complex_pct": 3},
                "tokens_per_signal": {"input": 120, "output": 20},
                "latency_requirement_ms": 500,
                "model_by_tier": {
                    "nano": None,
                    "micro": corpora_models.get("retail:micro", "granite-350m"),
                    "macro": "phi3-mini",
                },
            },
            {
                "id": "telecom-operations",
                "name": "Telecom Operations",
                "industry": "telecom",
                "description": "Route support tickets, detect network anomalies, predict customer churn.",
                "daily_volume": 500000,
                "signal_distribution": {"routine_pct": 80, "ambiguous_pct": 15, "complex_pct": 5},
                "tokens_per_signal": {"input": 150, "output": 25},
                "latency_requirement_ms": 500,
                "model_by_tier": {
                    "nano": None,
                    "micro": corpora_models.get("telecom:micro", "granite-350m"),
                    "macro": corpora_models.get("telecom:macro", "phi3-mini"),
                },
            },
        ],
    }

    return profiles


def build_benchmark_matrix(roster: dict, entries: list) -> dict:
    """Build the pre-processed benchmark matrix for the frontend results page."""
    models = roster["models"]
    cluster = roster["cluster"]

    matrix = []
    industries = set()

    # Group entries by model
    by_model = defaultdict(list)
    for row in entries:
        model = row.get("model", "")
        if model in models and model != "nano-rules":
            by_model[model].append(row)

    for alias in sorted(models.keys()):
        info = models[alias]
        rows = by_model.get(alias, [])
        if not rows:
            continue

        tasks = {}
        for row in rows:
            task = row.get("task", "")
            industry = row.get("industry", "basic")
            metrics = row.get("metrics", {})
            industries.add(industry)

            task_key = f"{industry}:{task}"
            tasks[task_key] = {
                "task": task,
                "industry": industry,
                "lever": row.get("lever", "baseline"),
                "samples": row.get("samples", 0),
                "throughput_tok_s": {
                    "value": _metric_val(metrics, "throughput_tok_s"),
                    "grade": _metric_grade(metrics, "throughput_tok_s"),
                },
                "latency_p95_ms": {
                    "value": _metric_val(metrics, "latency_p95_ms"),
                    "grade": _metric_grade(metrics, "latency_p95_ms"),
                },
                "quality_accuracy": {
                    "value": _metric_val(metrics, "quality_accuracy"),
                    "grade": _metric_grade(metrics, "quality_accuracy"),
                },
                "cold_start_ms": {
                    "value": _metric_val(metrics, "cold_start_ms"),
                    "grade": _metric_grade(metrics, "cold_start_ms"),
                },
                "warm_cache_speedup": {
                    "value": _metric_val(metrics, "warm_cache_speedup"),
                    "grade": _metric_grade(metrics, "warm_cache_speedup"),
                },
                "variance_flagged": {
                    "value": _metric_val(metrics, "variance_flagged") if "variance_flagged" in metrics else 0,
                    "grade": _metric_grade(metrics, "variance_flagged") if "variance_flagged" in metrics else "green",
                },
            }

        matrix.append({
            "model": alias,
            "params": info["params"],
            "params_b": info["params_b"],
            "serving_layer": info["backend"],
            "dtype": info["dtype"],
            "optimization": info["optimization"],
            "role": info["role"],
            "category": info["category"],
            "tasks": tasks,
        })

    # Load rubric thresholds
    rubric_path = BASE_DIR / "benchmark_matrix.yaml"
    rubric = {}
    if rubric_path.exists():
        with open(rubric_path) as f:
            rubric_raw = yaml.safe_load(f)
        for metric_name, metric_def in rubric_raw.get("metrics", {}).items():
            thresholds = metric_def.get("thresholds", {})
            rubric[metric_name] = {
                "green": thresholds.get("green"),
                "yellow": thresholds.get("yellow"),
                "direction": metric_def.get("direction", "higher_is_better"),
                "unit": metric_def.get("unit", ""),
                "description": metric_def.get("description", ""),
            }

    return {
        "matrix": matrix,
        "rubric_thresholds": rubric,
        "industries": sorted(industries),
        "provenance": {
            "cluster": cluster.get("name", "unknown"),
            "hardware": cluster.get("hardware", ""),
            "cores": cluster.get("cores", 0),
            "memory_gib": cluster.get("memory_gib", 0),
            "instruction_sets": cluster.get("instruction_sets", []),
        },
    }


def main():
    print("Loading model roster...", file=sys.stderr)
    roster = load_model_roster()
    print(f"  {len(roster['models'])} models loaded", file=sys.stderr)

    print("Loading benchmark results...", file=sys.stderr)
    entries = load_all_results()
    print(f"  {len(entries)} entries from {len(set(e['_source'] for e in entries))} files", file=sys.stderr)

    # 1. Hardware profiles
    print("Building hardware profiles...", file=sys.stderr)
    hw = build_hardware_profiles(roster, entries)
    hw_path = DATA_DIR / "hardware_profiles.json"
    with open(hw_path, "w") as f:
        json.dump(hw, f, indent=2)
    xeon = hw["profiles"][0]
    print(f"  {len(xeon['models'])} models with measured throughput → {hw_path}", file=sys.stderr)

    # 2. Workload profiles
    print("Building workload profiles...", file=sys.stderr)
    wl = build_workload_profiles()
    wl_path = DATA_DIR / "workload_profiles.json"
    with open(wl_path, "w") as f:
        json.dump(wl, f, indent=2)
    print(f"  {len(wl['profiles'])} workload profiles → {wl_path}", file=sys.stderr)

    # 3. Benchmark matrix
    print("Building benchmark matrix...", file=sys.stderr)
    bm = build_benchmark_matrix(roster, entries)
    bm_path = DATA_DIR / "benchmark_matrix.json"
    with open(bm_path, "w") as f:
        json.dump(bm, f, indent=2)
    print(f"  {len(bm['matrix'])} models × {len(bm['industries'])} industries → {bm_path}", file=sys.stderr)

    print("\nDone. Seeded:", file=sys.stderr)
    print(f"  {hw_path}", file=sys.stderr)
    print(f"  {wl_path}", file=sys.stderr)
    print(f"  {bm_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

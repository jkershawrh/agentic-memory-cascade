"""GPU comparison reference data.

Pulls Gaudi benchmark results from a local receipts directory and published
H100 reference data. Generates side-by-side comparison with CPU results.
"""
from __future__ import annotations

import os
import json
import sys
from pathlib import Path

RECEIPTS_DIR = Path(os.environ.get("CASCADE_RECEIPTS_DIR", "benchmarks/receipts"))

# Published H100 SXM reference throughput (tokens/second, single-stream)
# Sources: NVIDIA MLPerf submissions, vLLM benchmarks, community reports
H100_REFERENCE = {
    "granite-2b": {"tokens_per_second": 800, "source": "estimated from vLLM H100 benchmarks"},
    "qwen-3b": {"tokens_per_second": 500, "source": "estimated"},
    "phi3-mini": {"tokens_per_second": 300, "source": "estimated"},
    "granite-8b": {"tokens_per_second": 150, "source": "estimated"},
}

# Cloud API pricing (per 1K tokens, as of July 2026)
CLOUD_API_PRICING = {
    "frontier": {
        "name": "Cloud API Frontier (e.g. GPT-4o, Claude Sonnet)",
        "cost_per_1k_input": 0.0025,
        "cost_per_1k_output": 0.01,
    },
    "economy": {
        "name": "Cloud API Economy (e.g. GPT-4o-mini, Claude Haiku)",
        "cost_per_1k_input": 0.00015,
        "cost_per_1k_output": 0.0006,
    },
}


def load_gaudi_benchmarks() -> list[dict]:
    """Load Gaudi benchmark results from CASCADE_RECEIPTS_DIR."""
    results = []
    if not RECEIPTS_DIR.exists():
        return results

    for path in sorted(RECEIPTS_DIR.glob("benchmark-suite-*.json")):
        with open(path) as f:
            data = json.load(f)
        meta = data.get("metadata", {})

        for model_key, model_data in data.get("by_model", {}).items():
            if not model_data.get("hardware") == "gaudi":
                continue
            for task_key, task_data in model_data.get("by_task", {}).items():
                results.append({
                    "source_file": path.name,
                    "timestamp": meta.get("timestamp", ""),
                    "model": model_key,
                    "task": task_key,
                    "hardware": "gaudi",
                    "latency_median_ms": task_data.get("latency_median_ms", 0),
                    "latency_p95_ms": task_data.get("latency_p95_ms", 0),
                    "output_tokens_median": task_data.get("output_tokens_median", 0),
                    "quality": task_data.get("quality", "unknown"),
                })

    return results


def generate_comparison(cpu_results_path: str) -> dict:
    """Generate a side-by-side comparison: CPU vs GPU vs Cloud."""
    with open(cpu_results_path) as f:
        cpu_data = json.load(f)

    cpu_matrix = cpu_data.get("matrix", [])
    gaudi_data = load_gaudi_benchmarks()

    comparison = {
        "cpu": [],
        "gpu_gaudi": gaudi_data,
        "gpu_h100_reference": H100_REFERENCE,
        "cloud_api": CLOUD_API_PRICING,
        "cpu_vs_gpu": [],
    }

    for row in cpu_matrix:
        model = row["model"]
        tok_s = row.get("metrics", {}).get("throughput_tok_s", {}).get("value", 0)

        # Find H100 reference for comparison
        model_short = model.replace("-cpu", "").replace("granite-2b", "granite-2b").replace("granite-3-2-8b-instruct", "granite-8b").replace("qwen25-3b", "qwen-3b")
        h100_ref = H100_REFERENCE.get(model_short, {})
        h100_tok_s = h100_ref.get("tokens_per_second", 0)

        if tok_s > 0 and h100_tok_s > 0:
            comparison["cpu_vs_gpu"].append({
                "model": model,
                "cpu_tok_s": tok_s,
                "h100_tok_s": h100_tok_s,
                "ratio": round(h100_tok_s / tok_s, 2),
                "task": row.get("task", ""),
                "industry": row.get("industry", ""),
            })

        comparison["cpu"].append({
            "model": model,
            "task": row.get("task", ""),
            "throughput_tok_s": tok_s,
            "latency_p95_ms": row.get("metrics", {}).get("latency_p95_ms", {}).get("value", 0),
            "quality": row.get("metrics", {}).get("quality_accuracy", {}).get("value", 0),
        })

    return comparison


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m benchmarks.gpu_reference <cpu-results.json>")
        print("Generates side-by-side CPU vs GPU vs Cloud comparison.")
        sys.exit(1)

    comparison = generate_comparison(sys.argv[1])
    out_path = sys.argv[1].replace(".json", "-platform-comparison.json")
    with open(out_path, "w") as f:
        json.dump(comparison, f, indent=2)
    print(f"Platform comparison saved to {out_path}")

    # Print summary
    print(f"\nCPU vs H100 throughput ratios:")
    for entry in comparison["cpu_vs_gpu"]:
        print(f"  {entry['model']:35s} CPU: {entry['cpu_tok_s']:>6.1f} tok/s  "
              f"H100: {entry['h100_tok_s']:>6.0f} tok/s  "
              f"Ratio: {entry['ratio']:.1f}x")


if __name__ == "__main__":
    main()

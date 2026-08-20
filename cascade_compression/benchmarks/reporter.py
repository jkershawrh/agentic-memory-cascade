"""Reporter — transforms benchmark results into TCO calculator data.

Generates:
1. Updated hardware_profiles.json with real throughput numbers
2. Extended benchmark profile with batch/optimized dimensions
3. Markdown report with colored matrix tables
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR.parent / "data"


def load_results(results_path: str) -> dict:
    with open(results_path) as f:
        return json.load(f)


def generate_hardware_profiles(results: dict, existing_profiles_path: str | None = None) -> dict:
    """Transform benchmark results into hardware_profiles.json format.

    Reads the existing profiles and replaces placeholder throughput values
    with measured data from benchmark results.
    """
    profiles_path = existing_profiles_path or str(DATA_DIR / "hardware_profiles.json")
    with open(profiles_path) as f:
        profiles = json.load(f)

    # Build throughput map from benchmark results: model_alias → metrics
    throughput_map = {}
    for row in results.get("matrix", []):
        model = row["model"]
        metrics = row.get("metrics", {})
        tok_s = metrics.get("throughput_tok_s", {}).get("value", 0)

        if model not in throughput_map or tok_s > throughput_map[model]["tok_s"]:
            throughput_map[model] = {
                "tok_s": tok_s,
                "latency_p95": metrics.get("latency_p95_ms", {}).get("value", 0),
                "quality": metrics.get("quality_accuracy", {}).get("value", 0),
            }

    # Model alias → TCO calculator model ID mapping
    alias_to_id = {
        "granite-2b-cpu": "granite-2b",
        "granite-2b-int8": "granite-2b",
        "qwen25-3b-cpu": "qwen-3b",
        "phi3-mini-cpu": "phi3-mini",
        "granite-3-2-8b-instruct-cpu": "granite-8b",
    }

    # Update Xeon profile throughput
    for profile in profiles["profiles"]:
        if profile["type"] != "cpu":
            continue
        throughput = profile.get("inference_throughput", {})
        for alias, tco_id in alias_to_id.items():
            if alias in throughput_map and tco_id in throughput:
                measured = throughput_map[alias]
                throughput[tco_id]["tokens_per_second"] = round(measured["tok_s"], 1)

    # Update the note
    meta = results.get("metadata", {})
    ts = meta.get("timestamp", "unknown")
    cluster = meta.get("cluster", "unknown")
    profiles["_note"] = (
        f"Throughput numbers measured on {cluster} "
        f"(benchmark run: {ts}). "
        f"Lever: {meta.get('lever', 'baseline')}."
    )

    return profiles


def generate_extended_profiles(results: dict) -> dict:
    """Generate extended benchmark profiles with full dimensionality."""
    extended = {}

    for row in results.get("matrix", []):
        model = row["model"]
        lever = row["lever"]
        concurrency = row.get("concurrency", 1)
        metrics = row.get("metrics", {})

        if model not in extended:
            extended[model] = {}

        key = f"{lever}_c{concurrency}"
        extended[model][key] = {
            "tokens_per_second": metrics.get("throughput_tok_s", {}).get("value", 0),
            "latency_p95_ms": metrics.get("latency_p95_ms", {}).get("value", 0),
            "quality_accuracy": metrics.get("quality_accuracy", {}).get("value", 0),
            "cold_start_ms": metrics.get("cold_start_ms", {}).get("value", 0),
            "warm_cache_speedup": metrics.get("warm_cache_speedup", {}).get("value", 0),
            "concurrency": concurrency,
            "lever": lever,
        }

    return extended


def generate_markdown_report(evaluated: dict) -> str:
    """Generate a markdown report with benchmark results."""
    lines = ["# CPU Inference Benchmark Report\n"]

    meta = evaluated.get("metadata", {})
    lines.append(f"**Cluster**: {meta.get('cluster', 'unknown')}")
    lines.append(f"**Date**: {meta.get('timestamp', 'unknown')}")
    lines.append(f"**Lever**: {meta.get('lever', 'unknown')}")
    lines.append(f"**Mode**: {meta.get('mode', 'unknown')}")
    lines.append("")

    summary = evaluated.get("summary", {})
    total = summary.get("total", 0)
    lines.append("## Summary\n")
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|------:|")
    lines.append(f"| Total combinations | {total} |")
    lines.append(f"| Green (pass) | {summary.get('green', 0)} |")
    lines.append(f"| Yellow (marginal) | {summary.get('yellow', 0)} |")
    lines.append(f"| Red (fail) | {summary.get('red', 0)} |")
    lines.append(f"| Pass rate | {summary.get('pass_rate', 0):.0%} |")
    lines.append("")

    # By industry
    lines.append("## By Industry\n")
    lines.append("| Industry | Green | Yellow | Red | Pass Rate |")
    lines.append("|----------|------:|-------:|----:|----------:|")
    for ind, counts in summary.get("by_industry", {}).items():
        total_ind = sum(counts.values())
        pct = counts["green"] / total_ind if total_ind > 0 else 0
        lines.append(f"| {ind} | {counts['green']} | {counts['yellow']} | {counts['red']} | {pct:.0%} |")
    lines.append("")

    # By model
    lines.append("## By Model\n")
    lines.append("| Model | Green | Yellow | Red | Pass Rate |")
    lines.append("|-------|------:|-------:|----:|----------:|")
    for model, counts in summary.get("by_model", {}).items():
        total_m = sum(counts.values())
        pct = counts["green"] / total_m if total_m > 0 else 0
        lines.append(f"| {model} | {counts['green']} | {counts['yellow']} | {counts['red']} | {pct:.0%} |")
    lines.append("")

    # Detail table
    lines.append("## Detail Matrix\n")
    lines.append("| Model | Task | Industry | tok/s | p95 (ms) | Quality | Grade |")
    lines.append("|-------|------|----------|------:|---------:|--------:|------:|")
    for row in evaluated.get("evaluated", []):
        grades = row.get("grades", {})
        tok_s = grades.get("throughput_tok_s", {}).get("value", 0)
        p95 = grades.get("latency_p95_ms", {}).get("value", 0)
        quality = grades.get("quality_accuracy", {}).get("value", 0)
        overall = row.get("overall", "red")
        emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(overall, "⚪")
        lines.append(f"| {row['model']} | {row['task']} | {row['industry']} | "
                      f"{tok_s:.1f} | {p95:.0f} | {quality:.2f} | {emoji} {overall} |")
    lines.append("")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m benchmarks.reporter <results.json> [--update-profiles] [--markdown]")
        sys.exit(1)

    results_path = sys.argv[1]
    results = load_results(results_path)
    flags = sys.argv[2:]

    if "--update-profiles" in flags:
        profiles = generate_hardware_profiles(results)
        out_path = str(DATA_DIR / "hardware_profiles.json")
        with open(out_path, "w") as f:
            json.dump(profiles, f, indent=2)
        print(f"Updated {out_path} with benchmark data")

    if "--extended" in flags:
        extended = generate_extended_profiles(results)
        out_path = str(DATA_DIR / "hardware_profiles_extended.json")
        with open(out_path, "w") as f:
            json.dump(extended, f, indent=2)
        print(f"Extended profiles saved to {out_path}")

    if "--markdown" in flags:
        # Need evaluated data
        from .rubric import evaluate_results
        evaluated = evaluate_results(results_path)
        report = generate_markdown_report(evaluated)
        out_path = results_path.replace(".json", "-report.md")
        with open(out_path, "w") as f:
            f.write(report)
        print(f"Markdown report saved to {out_path}")


if __name__ == "__main__":
    main()

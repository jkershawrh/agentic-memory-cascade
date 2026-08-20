"""Red/Green/Yellow matrix evaluator.

Loads benchmark_matrix.yaml thresholds, evaluates benchmark results,
and outputs a colored terminal table + JSON matrix.
"""

import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("pip install pyyaml")
    sys.exit(1)

BASE_DIR = Path(__file__).parent

COLORS = {
    "green": "\033[92m",
    "yellow": "\033[93m",
    "red": "\033[91m",
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
}


def load_matrix_config() -> dict:
    with open(BASE_DIR / "benchmark_matrix.yaml") as f:
        return yaml.safe_load(f)


def grade_metric(metric_name: str, value: float, config: dict) -> str:
    """Evaluate a single metric value against thresholds."""
    metric_config = config.get("metrics", {}).get(metric_name, {})
    thresholds = metric_config.get("thresholds", {})
    direction = metric_config.get("direction", "higher_is_better")

    green = thresholds.get("green", 0)
    yellow = thresholds.get("yellow", 0)

    if direction == "lower_is_better":
        if value <= green:
            return "green"
        if value <= yellow:
            return "yellow"
        return "red"
    else:
        if value >= green:
            return "green"
        if value >= yellow:
            return "yellow"
        return "red"


def evaluate_results(results_path: str) -> dict:
    """Load benchmark results and evaluate against the rubric."""
    config = load_matrix_config()

    with open(results_path) as f:
        results = json.load(f)

    matrix = results.get("matrix", [])
    evaluated = []

    for row in matrix:
        evaluated_row = {
            "model": row["model"],
            "task": row["task"],
            "industry": row["industry"],
            "lever": row["lever"],
            "concurrency": row.get("concurrency", 1),
            "samples": row.get("samples", 0),
            "grades": {},
        }

        for metric_name, metric_data in row.get("metrics", {}).items():
            value = metric_data.get("value", 0)
            grade = grade_metric(metric_name, value, config)
            evaluated_row["grades"][metric_name] = {
                "value": value,
                "grade": grade,
            }

        # Overall grade: worst of all individual grades
        grades = [g["grade"] for g in evaluated_row["grades"].values()]
        if "red" in grades:
            evaluated_row["overall"] = "red"
        elif "yellow" in grades:
            evaluated_row["overall"] = "yellow"
        else:
            evaluated_row["overall"] = "green"

        evaluated.append(evaluated_row)

    return {
        "evaluated": evaluated,
        "metadata": results.get("metadata", {}),
        "summary": _summarize(evaluated),
    }


def _summarize(evaluated: list) -> dict:
    """Generate summary statistics from evaluated matrix."""
    total = len(evaluated)
    green = sum(1 for r in evaluated if r["overall"] == "green")
    yellow = sum(1 for r in evaluated if r["overall"] == "yellow")
    red = sum(1 for r in evaluated if r["overall"] == "red")

    by_industry = {}
    for r in evaluated:
        ind = r["industry"]
        if ind not in by_industry:
            by_industry[ind] = {"green": 0, "yellow": 0, "red": 0}
        by_industry[ind][r["overall"]] += 1

    by_model = {}
    for r in evaluated:
        model = r["model"]
        if model not in by_model:
            by_model[model] = {"green": 0, "yellow": 0, "red": 0}
        by_model[model][r["overall"]] += 1

    return {
        "total": total,
        "green": green,
        "yellow": yellow,
        "red": red,
        "pass_rate": round(green / total, 4) if total > 0 else 0,
        "by_industry": by_industry,
        "by_model": by_model,
    }


def print_matrix(evaluated: dict):
    """Print a colored terminal matrix."""
    c = COLORS
    summary = evaluated["summary"]

    print(f"\n{c['bold']}═══ BENCHMARK MATRIX ═══{c['reset']}")
    meta = evaluated.get("metadata", {})
    if meta:
        print(f"{c['dim']}Cluster: {meta.get('cluster', '?')} | "
              f"Lever: {meta.get('lever', '?')} | "
              f"Mode: {meta.get('mode', '?')}{c['reset']}")

    print(f"\n{c['bold']}Overall: {c['reset']}"
          f"{c['green']}{summary['green']} green{c['reset']} | "
          f"{c['yellow']}{summary['yellow']} yellow{c['reset']} | "
          f"{c['red']}{summary['red']} red{c['reset']} | "
          f"Pass rate: {summary['pass_rate']:.0%}")

    # Per-industry
    print(f"\n{c['bold']}By Industry:{c['reset']}")
    for ind, counts in summary.get("by_industry", {}).items():
        total = sum(counts.values())
        pct = counts["green"] / total if total > 0 else 0
        bar = _color_bar(counts)
        print(f"  {ind:15s} {bar}  ({pct:.0%} pass)")

    # Per-model
    print(f"\n{c['bold']}By Model:{c['reset']}")
    for model, counts in summary.get("by_model", {}).items():
        total = sum(counts.values())
        pct = counts["green"] / total if total > 0 else 0
        bar = _color_bar(counts)
        print(f"  {model:35s} {bar}  ({pct:.0%} pass)")

    # Detail rows
    print(f"\n{c['bold']}{'Model':30s} {'Task':25s} {'Ind':10s} "
          f"{'tok/s':>8s} {'p95ms':>8s} {'quality':>8s} {'Grade':>6s}{c['reset']}")
    print("─" * 100)

    for row in evaluated["evaluated"]:
        grades = row["grades"]
        tok_s = grades.get("throughput_tok_s", {})
        p95 = grades.get("latency_p95_ms", {})
        quality = grades.get("quality_accuracy", {})
        overall = row["overall"]

        def _fmt(metric_data):
            if not metric_data:
                return f"{c['dim']}   —{c['reset']}"
            val = metric_data.get("value", 0)
            grade = metric_data.get("grade", "red")
            color = c[grade]
            return f"{color}{val:>8.1f}{c['reset']}"

        grade_color = c[overall]
        print(f"  {row['model']:28s} {row['task']:25s} {row['industry']:10s} "
              f"{_fmt(tok_s)} {_fmt(p95)} {_fmt(quality)} "
              f"{grade_color}{overall:>6s}{c['reset']}")


def _color_bar(counts: dict) -> str:
    """Generate a small colored bar from grade counts."""
    c = COLORS
    parts = []
    for _ in range(counts.get("green", 0)):
        parts.append(f"{c['green']}█{c['reset']}")
    for _ in range(counts.get("yellow", 0)):
        parts.append(f"{c['yellow']}█{c['reset']}")
    for _ in range(counts.get("red", 0)):
        parts.append(f"{c['red']}█{c['reset']}")
    return "".join(parts)


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m benchmarks.rubric <results.json>")
        sys.exit(1)

    results_path = sys.argv[1]
    evaluated = evaluate_results(results_path)
    print_matrix(evaluated)

    # Save evaluated matrix
    out_path = results_path.replace(".json", "-evaluated.json")
    with open(out_path, "w") as f:
        json.dump(evaluated, f, indent=2)
    print(f"\nEvaluated matrix saved to {out_path}")


if __name__ == "__main__":
    main()

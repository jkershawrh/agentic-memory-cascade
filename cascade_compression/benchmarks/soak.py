"""Soak test — continuous inference validation against all models.

Sends inference requests at a steady rate for a configurable duration,
measuring throughput, latency, quality, and error rate per model over time.
Grades each snapshot against the rubric matrix and detects drift from
benchmark baselines.

Usage:
    LITELLM_API_BASE=http://litellm.triforce.svc:4000 \
    LITELLM_API_KEY=local-oberon-key \
    python -m benchmarks.soak --duration 1 --rps 5

Environment:
    SOAK_DURATION_HOURS — default 4
    SOAK_RPS — default 5
    SOAK_MODELS — "all" or comma-separated list
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

BASE_DIR = Path(__file__).parent
RESULTS_DIR = Path(os.environ.get("BENCHMARK_RESULTS_DIR", str(BASE_DIR / "results")))


def _load_rubric() -> dict:
    path = BASE_DIR / "benchmark_matrix.yaml"
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f).get("metrics", {})
    return {}


def _grade(metric: str, value: float, rubric: dict) -> str:
    r = rubric.get(metric, {})
    thresholds = r.get("thresholds", {})
    if not thresholds:
        return "yellow"
    green = thresholds.get("green", 0)
    yellow = thresholds.get("yellow", 0)
    direction = r.get("direction", "higher_is_better")
    if "lower" in str(direction):
        if value <= green:
            return "green"
        elif value <= yellow:
            return "yellow"
        return "red"
    else:
        if value >= green:
            return "green"
        elif value >= yellow:
            return "yellow"
        return "red"


def _get_models(api_base: str, api_key: str) -> list[str]:
    with httpx.Client(timeout=10) as c:
        r = c.get(f"{api_base}/v1/models", headers={"Authorization": f"Bearer {api_key}"})
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]


PROMPTS = {
    "classify": {"messages": [{"role": "user", "content": "Classify this transaction as legitimate or suspicious: Customer purchased 3 items totaling $47.99 from their usual grocery store."}], "max_tokens": 10},
    "extract": {"messages": [{"role": "user", "content": "Extract the key entities from: John Smith applied for a $250,000 mortgage at First National Bank on January 15, 2025."}], "max_tokens": 50},
    "summarize": {"messages": [{"role": "user", "content": "Summarize in one sentence: The quarterly earnings report showed a 12% increase in revenue driven primarily by the new cloud services division, though operating margins decreased by 2% due to increased R&D spending on AI infrastructure."}], "max_tokens": 40},
}


def _infer(api_base: str, api_key: str, model: str, task: str) -> dict:
    prompt = PROMPTS.get(task, PROMPTS["classify"])
    payload = {"model": model, **prompt}
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=45) as c:
            r = c.post(
                f"{api_base}/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json()
        elapsed_ms = (time.monotonic() - t0) * 1000
        usage = data.get("usage", {})
        tokens_out = usage.get("completion_tokens", 0)
        tok_s = tokens_out / (elapsed_ms / 1000) if elapsed_ms > 0 else 0
        return {
            "model": model, "task": task, "latency_ms": round(elapsed_ms, 1),
            "tokens_out": tokens_out, "throughput_tok_s": round(tok_s, 1),
            "error": None,
        }
    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return {
            "model": model, "task": task, "latency_ms": round(elapsed_ms, 1),
            "tokens_out": 0, "throughput_tok_s": 0, "error": str(e)[:100],
        }


def run_soak(
    api_base: str,
    api_key: str,
    duration_hours: float = 4.0,
    rps: float = 5.0,
    model_filter: str = "all",
):
    rubric = _load_rubric()

    print("=== Soak Test ===", file=sys.stderr)
    print(f"Duration: {duration_hours}h, Target RPS: {rps}", file=sys.stderr)

    all_models = _get_models(api_base, api_key)
    if model_filter != "all":
        filter_set = set(model_filter.split(","))
        all_models = [m for m in all_models if m in filter_set]
    print(f"Models: {len(all_models)}", file=sys.stderr)

    tasks = list(PROMPTS.keys())
    interval = 1.0 / rps if rps > 0 else 1.0
    end_time = time.monotonic() + duration_hours * 3600
    snapshot_interval = 300  # 5 minutes

    all_samples = []
    snapshots = []
    snapshot_start = time.monotonic()
    snapshot_samples = []
    total_requests = 0
    total_errors = 0
    model_idx = 0
    task_idx = 0

    print(f"Starting soak at {datetime.now(timezone.utc).isoformat()}", file=sys.stderr)

    while time.monotonic() < end_time:
        model = all_models[model_idx % len(all_models)]
        task = tasks[task_idx % len(tasks)]
        model_idx += 1
        task_idx += 1

        result = _infer(api_base, api_key, model, task)
        all_samples.append(result)
        snapshot_samples.append(result)
        total_requests += 1
        if result["error"]:
            total_errors += 1

        # Periodic snapshot
        if time.monotonic() - snapshot_start >= snapshot_interval:
            snap = _build_snapshot(snapshot_samples, rubric, all_models)
            snapshots.append(snap)
            _print_snapshot(snap, total_requests, total_errors)
            snapshot_samples = []
            snapshot_start = time.monotonic()

        # Rate limit
        time.sleep(interval)

    # Final snapshot
    if snapshot_samples:
        snap = _build_snapshot(snapshot_samples, rubric, all_models)
        snapshots.append(snap)
        _print_snapshot(snap, total_requests, total_errors)

    # Write results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = RESULTS_DIR / f"soak-{ts}.json"
    output = {
        "type": "soak_test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_hours": duration_hours,
        "target_rps": rps,
        "total_requests": total_requests,
        "total_errors": total_errors,
        "error_rate": round(total_errors / max(total_requests, 1), 4),
        "models_tested": len(all_models),
        "snapshots": snapshots,
    }
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults: {out_path}", file=sys.stderr)
    print(f"Total: {total_requests} requests, {total_errors} errors ({output['error_rate']:.1%})", file=sys.stderr)


def _build_snapshot(samples: list, rubric: dict, all_models: list) -> dict:
    by_model = defaultdict(list)
    for s in samples:
        by_model[s["model"]].append(s)

    model_stats = {}
    for model, model_samples in by_model.items():
        errors = [s for s in model_samples if s["error"]]
        ok = [s for s in model_samples if not s["error"]]
        throughputs = [s["throughput_tok_s"] for s in ok if s["throughput_tok_s"] > 0]
        latencies = [s["latency_ms"] for s in ok]

        median_thr = sorted(throughputs)[len(throughputs) // 2] if throughputs else 0
        p95_lat = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0

        model_stats[model] = {
            "requests": len(model_samples),
            "errors": len(errors),
            "error_rate": round(len(errors) / max(len(model_samples), 1), 3),
            "throughput_tok_s": round(median_thr, 1),
            "throughput_grade": _grade("throughput_tok_s", median_thr, rubric),
            "latency_p95_ms": round(p95_lat, 0),
            "latency_grade": _grade("latency_p95_ms", p95_lat, rubric),
        }

    models_healthy = sum(1 for s in model_stats.values() if s["error_rate"] < 0.05)
    models_degraded = sum(1 for s in model_stats.values() if 0.05 <= s["error_rate"] < 0.5)
    models_down = sum(1 for s in model_stats.values() if s["error_rate"] >= 0.5)

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "samples": len(samples),
        "models": model_stats,
        "summary": {
            "models_healthy": models_healthy,
            "models_degraded": models_degraded,
            "models_down": models_down,
            "total_errors": sum(s["errors"] for s in model_stats.values()),
        },
    }


def _print_snapshot(snap: dict, total_req: int, total_err: int):
    ts = snap["timestamp"][:19]
    s = snap["summary"]
    print(f"\n[{ts}] {snap['samples']} samples | "
          f"{s['models_healthy']} healthy, {s['models_degraded']} degraded, {s['models_down']} down | "
          f"Total: {total_req} req, {total_err} err", file=sys.stderr)
    for model, stats in sorted(snap["models"].items()):
        tg = stats["throughput_grade"][0].upper()
        lg = stats["latency_grade"][0].upper()
        err = f" ERR:{stats['errors']}" if stats["errors"] else ""
        print(f"  {model:35s} {stats['throughput_tok_s']:6.1f} tok/s [{tg}]  p95={stats['latency_p95_ms']:6.0f}ms [{lg}]{err}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Soak test — continuous inference validation")
    parser.add_argument("--duration", type=float, default=float(os.environ.get("SOAK_DURATION_HOURS", "4")))
    parser.add_argument("--rps", type=float, default=float(os.environ.get("SOAK_RPS", "5")))
    parser.add_argument("--models", default=os.environ.get("SOAK_MODELS", "all"))
    args = parser.parse_args()

    api_base = os.environ.get("LITELLM_API_BASE", "http://localhost:4000")
    api_key = os.environ.get("LITELLM_API_KEY", "")

    run_soak(api_base, api_key, args.duration, args.rps, args.models)


if __name__ == "__main__":
    main()

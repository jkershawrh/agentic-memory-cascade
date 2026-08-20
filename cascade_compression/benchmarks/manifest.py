"""Run manifest — tracks all benchmark runs with metadata and status.

Writes to benchmarks/results/manifest.json as an append-only log.
Each entry records what was run, when, how long, what succeeded/failed.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

RESULTS_DIR = Path(os.environ.get("BENCHMARK_RESULTS_DIR", str(Path(__file__).parent / "results")))
MANIFEST_PATH = RESULTS_DIR / "manifest.json"


def _git_hash() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


def _git_dirty() -> bool:
    try:
        result = subprocess.check_output(
            ["git", "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return len(result) > 0
    except Exception:
        return False


def load_manifest() -> list[dict]:
    if MANIFEST_PATH.exists():
        with open(MANIFEST_PATH) as f:
            return json.load(f)
    return []


def save_manifest(entries: list[dict]):
    RESULTS_DIR.mkdir(exist_ok=True)
    with open(MANIFEST_PATH, "w") as f:
        json.dump(entries, f, indent=2, default=str)


def start_run(
    mode: str,
    lever: str,
    industries: list[str],
    samples: int,
    endpoint: str,
    models: list[str],
) -> dict:
    """Record the start of a benchmark run. Returns the entry to update later."""
    entry = {
        "run_id": datetime.now().strftime("%Y%m%d-%H%M%S"),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "status": "running",
        "mode": mode,
        "lever": lever,
        "industries": industries,
        "samples": samples,
        "endpoint": endpoint,
        "models": models,
        "total_combinations": 0,
        "total_api_calls": 0,
        "errors": 0,
        "results_file": None,
        "git_hash": _git_hash(),
        "git_dirty": _git_dirty(),
        "duration_seconds": None,
    }

    entries = load_manifest()
    entries.append(entry)
    save_manifest(entries)
    return entry


def complete_run(
    run_id: str,
    results_file: str,
    total_combinations: int,
    total_api_calls: int,
    errors: int,
    status: str = "completed",
):
    """Update a run entry with completion info."""
    entries = load_manifest()
    for entry in entries:
        if entry["run_id"] == run_id:
            entry["completed_at"] = datetime.now(timezone.utc).isoformat()
            entry["status"] = status
            entry["results_file"] = results_file
            entry["total_combinations"] = total_combinations
            entry["total_api_calls"] = total_api_calls
            entry["errors"] = errors
            started = datetime.fromisoformat(entry["started_at"])
            completed = datetime.fromisoformat(entry["completed_at"])
            entry["duration_seconds"] = int((completed - started).total_seconds())
            break
    save_manifest(entries)


def print_manifest():
    """Print the run manifest as a table."""
    entries = load_manifest()
    if not entries:
        print("No benchmark runs recorded.")
        return

    print(f"\n{'Run ID':18s} {'Status':10s} {'Mode':8s} {'Lever':12s} "
          f"{'Combos':>7s} {'Errors':>7s} {'Duration':>10s}")
    print("─" * 80)
    for e in entries:
        dur = e.get("duration_seconds")
        dur_str = f"{dur}s" if dur else "—"
        print(f"{e['run_id']:18s} {e['status']:10s} {e['mode']:8s} "
              f"{e['lever']:12s} {e.get('total_combinations', 0):>7d} "
              f"{e.get('errors', 0):>7d} {dur_str:>10s}")

"""Corpora compiler — produces corpora.json from benchmark results.

Reads the actual benchmark output format from intel-tco-calculator:
- benchmarks/results/*.json  — matrix entries with {value, grade} metrics
- benchmarks/configs/models.yaml — model roster keyed by alias
- benchmarks/benchmark_matrix.yaml — rubric thresholds
- data/workload_profiles.json — SLA requirements

Usage::

    python -m cascade_compression.routing.compile_corpora \
        --benchmark-dir /path/to/intel-tco-calculator/benchmarks \
        --output config/corpora.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import yaml

from .corpora import (
    CorporaEntry,
    ModelConfig,
    RoutingCorpora,
    RubricScorecard,
    _GRADE_RANK,
)


def _parse_params_to_float(params_str: str) -> float:
    """Convert params string like '350M', '2B', '3.8B', '2B+350M', '35B total / 3B active (MoE)' to float in billions."""
    s = params_str.strip()
    # Handle MoE: use active params
    if "active" in s.lower():
        match = re.search(r'(\d+(?:\.\d+)?)\s*B\s*active', s, re.IGNORECASE)
        if match:
            return float(match.group(1))
    # Handle speculative: 2B+350M → use the target (larger)
    if "+" in s:
        parts = s.split("+")
        return max(_parse_params_to_float(p) for p in parts)
    # Simple: 350M, 1B, 2B, 3.8B, 8B
    match = re.match(r'^(\d+(?:\.\d+)?)\s*(M|B)', s, re.IGNORECASE)
    if match:
        val = float(match.group(1))
        unit = match.group(2).upper()
        return val / 1000.0 if unit == "M" else val
    return 0.0


def _load_model_roster(models_path: Path) -> Dict[str, ModelConfig]:
    """Load models.yaml with alias-keyed categories (baseline/optimized/gguf)."""
    if not models_path.exists():
        print(f"WARNING: models.yaml not found at {models_path}", file=sys.stderr)
        return {}

    with open(models_path) as f:
        raw = yaml.safe_load(f)

    roster: Dict[str, ModelConfig] = {}
    for category in ("baseline", "optimized", "gguf"):
        for entry in raw.get("models", {}).get(category, []):
            alias = entry["alias"]
            dtype_raw = entry.get("dtype", "bfloat16")
            # Normalize dtype for Literal compatibility
            dtype_clean = dtype_raw
            if "1.58-bit" in dtype_raw or "i2_s" in dtype_raw:
                dtype_clean = "i2_s"
            elif dtype_raw not in ("bfloat16", "int8", "Q4_K_M", "Q8_0"):
                dtype_clean = dtype_raw

            roster[alias] = ModelConfig(
                model=alias,
                params=_parse_params_to_float(entry.get("params", "0")),
                serving_layer=entry.get("backend", "ovms"),
                dtype=dtype_clean,
                optimization=entry.get("optimization", ""),
            )
    return roster


def _load_cluster_info(models_path: Path) -> dict:
    """Extract cluster metadata from models.yaml."""
    if not models_path.exists():
        return {}
    with open(models_path) as f:
        raw = yaml.safe_load(f)
    return raw.get("cluster", {})


def _tier_for_params(params_b: float) -> Literal["micro", "macro"]:
    """Micro tier: ≤ 3B. Macro tier: > 3B."""
    return "micro" if params_b <= 3.0 else "macro"


def _extract_scorecard(metrics: Dict[str, Any], lever: str) -> RubricScorecard:
    """Build a RubricScorecard from a benchmark matrix entry's metrics dict.

    Each metric is {value: float, grade: str}.
    """
    def _val(key: str) -> float:
        v = metrics.get(key, {})
        if isinstance(v, dict):
            return float(v.get("value", 0))
        return float(v) if v else 0.0

    def _grade(key: str) -> str:
        v = metrics.get(key, {})
        if isinstance(v, dict):
            return v.get("grade", "red")
        return "red"

    vf = metrics.get("variance_flagged", {})
    vf_val = vf.get("value", 0) if isinstance(vf, dict) else (vf or 0)
    vf_grade = vf.get("grade", "green") if isinstance(vf, dict) else "green"

    # Parse strategy from lever (e.g. "composed:routing_ladder_int8" → "routing_ladder_int8")
    strategy = lever
    if ":" in lever:
        strategy = lever.split(":", 1)[1]

    return RubricScorecard(
        quality_accuracy=_val("quality_accuracy"),
        quality_accuracy_grade=_grade("quality_accuracy"),
        quality_coherence=0.0,
        quality_coherence_grade="green",
        quality_faithfulness=0.0,
        quality_faithfulness_grade="green",
        latency_ttft_ms=_val("ttft_p50_ms"),
        latency_ttft_grade=_grade("ttft_p50_ms") if "ttft_p50_ms" in metrics else "green",
        latency_p95_ms=_val("latency_p95_ms"),
        latency_p95_grade=_grade("latency_p95_ms"),
        throughput_tok_s=_val("throughput_tok_s"),
        throughput_tok_s_grade=_grade("throughput_tok_s"),
        throughput_batch=0.0,
        throughput_batch_grade="green",
        variance_flagged=bool(vf_val > 0.2 if isinstance(vf_val, (int, float)) else False),
        variance_grade=vf_grade if isinstance(vf_grade, str) else "green",
        memory_gb=0.0,
        memory_grade="green",
        composite_score=0.0,
        composite_grade="green",
    )


def _rank_key(entry: Tuple[ModelConfig, RubricScorecard]) -> Tuple:
    """Sort key for model ranking (lower = better).

    Priority: quality → latency → not variance → throughput → smaller params.
    """
    cfg, sc = entry
    return (
        _GRADE_RANK.get(sc.quality_accuracy_grade, 2),
        _GRADE_RANK.get(sc.latency_p95_grade, 2),
        1 if sc.variance_flagged else 0,
        -sc.throughput_tok_s,
        cfg.params,
    )


def compile_corpora(
    benchmark_dir: str,
    output_path: str,
    data_dir: Optional[str] = None,
) -> RoutingCorpora:
    """Compile benchmark results into a RoutingCorpora.

    Args:
        benchmark_dir: Path to benchmarks/ directory
        output_path: Where to write corpora.json
        data_dir: Path to data/ directory (for workload_profiles.json). Defaults to ../data relative to benchmark_dir.
    """
    bench_dir = Path(benchmark_dir)

    # 1. Load model roster
    models_path = bench_dir / "configs" / "models.yaml"
    roster = _load_model_roster(models_path)
    cluster_info = _load_cluster_info(models_path)
    print(f"Loaded {len(roster)} models from {models_path}", file=sys.stderr)

    # 2. Load SLA requirements
    sla_map: Dict[str, int] = {}
    if data_dir:
        profiles_path = Path(data_dir) / "workload_profiles.json"
    else:
        profiles_path = bench_dir.parent / "data" / "workload_profiles.json"
    if profiles_path.exists():
        with open(profiles_path) as f:
            profiles_raw = json.load(f)
        profile_list = profiles_raw
        if isinstance(profiles_raw, dict):
            profile_list = profiles_raw.get("profiles", [])
        if isinstance(profile_list, list):
            for wp in profile_list:
                if isinstance(wp, dict):
                    sla_map[wp.get("id", "")] = wp.get("latency_requirement_ms", 2000)
        print(f"Loaded {len(sla_map)} SLA profiles", file=sys.stderr)

    # 3. Read all result JSONs
    results_dir = bench_dir / "results"
    all_entries: List[Tuple[str, str, str, str, ModelConfig, RubricScorecard]] = []
    source_files: List[str] = []

    if results_dir.exists():
        for result_file in sorted(results_dir.glob("*.json")):
            if result_file.name == "manifest.json":
                continue
            source_files.append(result_file.name)

            with open(result_file) as f:
                data = json.load(f)

            matrix = data.get("matrix", [])
            if not isinstance(matrix, list):
                continue

            for entry in matrix:
                model_alias = entry.get("model", "")
                task = entry.get("task", "")
                industry = entry.get("industry", "basic")
                lever = entry.get("lever", "baseline")
                metrics = entry.get("metrics", {})

                if not model_alias or not task or not metrics:
                    continue

                # Skip nano-rules pseudo-model
                if model_alias == "nano-rules":
                    continue

                config = roster.get(model_alias)
                if config is None:
                    continue

                scorecard = _extract_scorecard(metrics, lever)
                all_entries.append((industry, task, lever, result_file.name, config, scorecard))

    print(f"Ingested {len(all_entries)} benchmark entries from {len(source_files)} files", file=sys.stderr)

    # 4. Group by (industry, task)
    grouped: Dict[str, Dict[str, List[Tuple[ModelConfig, RubricScorecard]]]] = {}
    for industry, task, lever, src, config, scorecard in all_entries:
        grouped.setdefault(industry, {}).setdefault(task, []).append((config, scorecard))

    # 5. Select best per tier
    entries: Dict[str, Dict[str, Dict[str, Any]]] = {}
    gaps: List[Dict[str, Any]] = []

    for industry, tasks in sorted(grouped.items()):
        for task, candidates in sorted(tasks.items()):
            for tier in ("micro", "macro"):
                tier_candidates = [
                    (cfg, sc) for cfg, sc in candidates
                    if _tier_for_params(cfg.params) == tier
                ]

                # Quality gate: exclude red quality
                quality_passed = [
                    (cfg, sc) for cfg, sc in tier_candidates
                    if sc.quality_accuracy_grade != "red"
                ]

                if not quality_passed:
                    best_q = max((sc.quality_accuracy for _, sc in tier_candidates), default=0.0)
                    gaps.append({
                        "industry": industry,
                        "task": task,
                        "tier": tier,
                        "best_quality": round(best_q, 3),
                        "models_tested": len(tier_candidates),
                        "reason": "no model passed quality gate",
                    })
                    continue

                # Rank
                quality_passed.sort(key=_rank_key)

                best_cfg, best_sc = quality_passed[0]
                fallback_cfg = quality_passed[1][0] if len(quality_passed) > 1 else None
                alt_cfgs = [cfg for cfg, _ in quality_passed[2:5]]

                entry = CorporaEntry(
                    config=best_cfg,
                    tier=tier,
                    scorecard=best_sc,
                    fallback=fallback_cfg,
                    alternatives=alt_cfgs,
                )
                entries.setdefault(industry, {}).setdefault(task, {})[tier] = entry.model_dump()

    # 6. Build corpora
    corpora = RoutingCorpora(
        version="1.0.0",
        compiled_at=datetime.now(timezone.utc).isoformat(),
        cluster=cluster_info.get("name", "unknown"),
        hardware=cluster_info.get("hardware", "unknown"),
        instruction_sets=cluster_info.get("instruction_sets", []),
        model_roster=sorted(roster.keys()),
        entries=entries,
        gaps=gaps,
    )

    # 7. Write output
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(corpora.model_dump(), f, indent=2)

    total_entries = sum(
        len(tiers) for tasks in entries.values() for tiers in tasks.values()
    )
    print(
        f"Compiled: {len(entries)} industries, {total_entries} entries, "
        f"{len(gaps)} gaps → {out_path}",
        file=sys.stderr,
    )

    return corpora


def main():
    parser = argparse.ArgumentParser(
        description="Compile benchmark results into corpora.json",
    )
    parser.add_argument(
        "--benchmark-dir",
        required=True,
        help="Path to benchmarks/ directory (contains results/, configs/)",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Path to data/ directory (for workload_profiles.json)",
    )
    parser.add_argument(
        "--output",
        default="config/corpora.json",
        help="Output path for corpora.json",
    )
    args = parser.parse_args()
    compile_corpora(args.benchmark_dir, args.output, args.data_dir)


if __name__ == "__main__":
    main()

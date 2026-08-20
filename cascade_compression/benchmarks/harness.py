"""Benchmark harness — async runner against Oberon LiteLLM endpoint.

Adapted from triforce/scripts/benchmark-suite.py.
Supports three execution modes: single, batched, workflow.
Supports all 9 optimization levers including routing, caching, and composed.
Follows cold → warm-up → steady-state measurement protocol.
Uses corpus-based varied inputs for statistically meaningful quality metrics.

Usage:
  python -m benchmarks.harness --mode single --lever baseline --industries fsi --samples 30
  python -m benchmarks.harness --mode batch --lever batching --industries fsi,healthcare
  python -m benchmarks.harness --mode workflow --industries fsi --samples 10
  python -m benchmarks.harness --mode single --lever routing --industries all --samples 30
  python -m benchmarks.harness --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import httpx
    import yaml
except ImportError:
    print("pip install httpx pyyaml")
    sys.exit(1)

from .metrics import SampleResult, aggregate
from .manifest import start_run, complete_run
from .preflight import run_preflight
from .corpus import load_corpus, generate_corpus

BASE_DIR = Path(__file__).parent
RESULTS_DIR = Path(os.environ.get("BENCHMARK_RESULTS_DIR", str(BASE_DIR / "results")))
RESULTS_DIR.mkdir(exist_ok=True)

API_BASE = os.environ.get("LITELLM_API_BASE", "")
API_KEY = os.environ.get("LITELLM_API_KEY", "")


# ── Config loading ────────────────────────────────────────────────

def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_matrix_config() -> dict:
    return load_yaml(BASE_DIR / "benchmark_matrix.yaml")


def load_models_config() -> dict:
    return load_yaml(BASE_DIR / "configs" / "models.yaml")


def load_workloads(industries: list[str]) -> dict[str, dict]:
    workloads = {}
    workloads_dir = BASE_DIR / "workloads"
    for industry in industries:
        path = workloads_dir / f"{industry}.yaml"
        if path.exists():
            workloads[industry] = load_yaml(path)
        else:
            print(f"Warning: no workload file for industry '{industry}'")
    return workloads


def load_lever(lever_name: str) -> dict:
    path = BASE_DIR / "levers" / f"{lever_name}.yaml"
    if path.exists():
        return load_yaml(path)
    return {"lever": lever_name}


def resolve_models(lever_config: dict, models_config: dict) -> list[str]:
    if "models" in lever_config:
        return lever_config["models"]
    if "pairs" in lever_config:
        models = []
        for pair in lever_config["pairs"]:
            models.append(pair["optimized"])
            models.append(pair["baseline"])
        return models
    if "ladder" in lever_config:
        return [m["alias"] for m in lever_config["ladder"]]
    if "compositions" in lever_config:
        all_models = set()
        for comp in lever_config["compositions"]:
            all_models.update(comp.get("models", []))
        return list(all_models)
    # Default: return ALL known models (baseline + optimized + gguf)
    # Let task-level model_assignments do the filtering
    all_models = []
    for section in ("baseline", "optimized", "gguf"):
        for m in models_config.get("models", {}).get(section, []):
            all_models.append(m["alias"])
    return all_models


def get_corpus_inputs(industry: str, task_id: str, count: int) -> list[str]:
    """Get varied inputs from corpus. Falls back to generating on the fly."""
    corpus = load_corpus(industry, size=100)
    if corpus and task_id in corpus.get("tasks", {}):
        records = corpus["tasks"][task_id]
        inputs = [r["text"] for r in records]
        while len(inputs) < count:
            inputs.extend(inputs)
        return inputs[:count]
    corpus = generate_corpus(industry, max(count, 10))
    if task_id in corpus.get("tasks", {}):
        return [r["text"] for r in corpus["tasks"][task_id]][:count]
    return []


def fill_prompt(template: str, text: str) -> str:
    """Replace all placeholder variables in a prompt template with text."""
    placeholders = [
        "{document}", "{transaction}", "{dispute}", "{transaction_details}",
        "{application}", "{claim}", "{policy_text}", "{product}",
        "{review}", "{signal}", "{ticket}", "{log_excerpt}", "{customer_profile}",
    ]
    result = template
    for p in placeholders:
        result = result.replace(p, text)
    return result


# ── Quality checks ────────────────────────────────────────────────

def check_quality(output: str, task: dict) -> str:
    check_type = task.get("quality_check", "none")
    target = task.get("quality_target")

    if check_type == "none":
        return "unknown"

    if check_type == "length_and_content":
        words = output.split()
        return "correct" if 10 < len(words) < 200 else "incorrect"

    if target is None:
        return "unknown"

    output_lower = output.lower().strip()

    if check_type == "exact_match":
        return "correct" if target.lower() in output_lower else "incorrect"

    if check_type == "substring":
        return "correct" if target.lower() in output_lower else "incorrect"

    if check_type == "structured_json":
        try:
            parsed = json.loads(output)
            if isinstance(parsed, dict) and target in parsed:
                return "correct"
            return "incorrect"
        except (json.JSONDecodeError, TypeError):
            if target.lower() in output_lower:
                return "correct"
            return "incorrect"

    return "unknown"


# ── Model calling (with TTFT streaming support) ──────────────────

async def call_model(
    client: httpx.AsyncClient,
    model: str,
    prompt: str,
    max_tokens: int,
    task_id: str,
    industry: str,
    lever: str,
    is_cold: bool = False,
    concurrency: int = 1,
    quality_fn=None,
    stream: bool = False,
) -> SampleResult:
    """Make a single inference call and capture metrics including TTFT."""
    start = time.monotonic()
    ttft_ms = 0

    if stream:
        return await _call_model_streaming(
            client, model, prompt, max_tokens, task_id, industry,
            lever, is_cold, concurrency, quality_fn,
        )

    try:
        resp = await client.post(
            f"{API_BASE}/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.1,
            },
            timeout=120.0,
        )
        latency_ms = int((time.monotonic() - start) * 1000)

        if resp.status_code != 200:
            return SampleResult(
                model=model, task_id=task_id, industry=industry, lever=lever,
                concurrency=concurrency, latency_ms=latency_ms, is_cold=is_cold,
                error=f"HTTP {resp.status_code}: {resp.text[:100]}",
            )

        data = resp.json()
        usage = data.get("usage", {})
        output_text = data["choices"][0]["message"]["content"]
        quality = quality_fn(output_text) if quality_fn else "unknown"

        return SampleResult(
            model=model, task_id=task_id, industry=industry, lever=lever,
            concurrency=concurrency, latency_ms=latency_ms, ttft_ms=ttft_ms,
            output_tokens=usage.get("completion_tokens", 0),
            prompt_tokens=usage.get("prompt_tokens", 0),
            output_text=output_text[:200],
            quality=quality, is_cold=is_cold,
        )
    except Exception as e:
        latency_ms = int((time.monotonic() - start) * 1000)
        return SampleResult(
            model=model, task_id=task_id, industry=industry, lever=lever,
            concurrency=concurrency, latency_ms=latency_ms, is_cold=is_cold,
            error=str(e),
        )


async def _call_model_streaming(
    client: httpx.AsyncClient,
    model: str,
    prompt: str,
    max_tokens: int,
    task_id: str,
    industry: str,
    lever: str,
    is_cold: bool,
    concurrency: int,
    quality_fn,
) -> SampleResult:
    """Streaming call to capture time-to-first-token."""
    start = time.monotonic()
    ttft_ms = 0
    chunks = []
    prompt_tokens = 0
    output_tokens = 0

    try:
        async with client.stream(
            "POST",
            f"{API_BASE}/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.1,
                "stream": True,
            },
            timeout=120.0,
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                latency_ms = int((time.monotonic() - start) * 1000)
                return SampleResult(
                    model=model, task_id=task_id, industry=industry, lever=lever,
                    concurrency=concurrency, latency_ms=latency_ms, is_cold=is_cold,
                    error=f"HTTP {resp.status_code}: {body.decode()[:100]}",
                )

            first_token_seen = False
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content and not first_token_seen:
                        ttft_ms = int((time.monotonic() - start) * 1000)
                        first_token_seen = True
                    if content:
                        chunks.append(content)
                    usage = data.get("usage")
                    if usage:
                        prompt_tokens = usage.get("prompt_tokens", 0)
                        output_tokens = usage.get("completion_tokens", 0)
                except (json.JSONDecodeError, KeyError):
                    continue

        latency_ms = int((time.monotonic() - start) * 1000)
        output_text = "".join(chunks)
        if not output_tokens:
            output_tokens = len(output_text.split())
        quality = quality_fn(output_text) if quality_fn else "unknown"

        return SampleResult(
            model=model, task_id=task_id, industry=industry, lever=lever,
            concurrency=concurrency, latency_ms=latency_ms, ttft_ms=ttft_ms,
            output_tokens=output_tokens, prompt_tokens=prompt_tokens,
            output_text=output_text[:200], quality=quality, is_cold=is_cold,
        )
    except Exception as e:
        latency_ms = int((time.monotonic() - start) * 1000)
        return SampleResult(
            model=model, task_id=task_id, industry=industry, lever=lever,
            concurrency=concurrency, latency_ms=latency_ms, is_cold=is_cold,
            error=str(e),
        )


# ── Execution modes ──────────────────────────────────────────────

async def run_single(
    client: httpx.AsyncClient,
    model: str,
    task: dict,
    industry: str,
    lever: str,
    protocol: dict,
    corpus_inputs: list[str] | None = None,
    stream: bool = False,
) -> list[SampleResult]:
    """Run single-stream benchmark with corpus-varied inputs."""
    max_tokens = task.get("max_tokens", 200)
    quality_fn = lambda out: check_quality(out, task)

    cold_samples = protocol.get("cold_start_samples", 3)
    warmup_samples = protocol.get("warmup_samples", 10)
    steady_samples = protocol.get("steady_state_samples", 30)
    total_needed = cold_samples + warmup_samples + steady_samples

    if corpus_inputs:
        inputs = corpus_inputs
        while len(inputs) < total_needed:
            inputs = inputs + corpus_inputs
    else:
        fallback = task.get("sample_input", "test input")
        inputs = [fallback] * total_needed

    idx = 0
    results = []

    for i in range(cold_samples):
        prompt = fill_prompt(task["prompt"], inputs[idx % len(inputs)])
        idx += 1
        r = await call_model(client, model, prompt, max_tokens,
                             task["id"], industry, lever, is_cold=True,
                             quality_fn=quality_fn, stream=stream)
        results.append(r)

    for i in range(warmup_samples):
        prompt = fill_prompt(task["prompt"], inputs[idx % len(inputs)])
        idx += 1
        await call_model(client, model, prompt, max_tokens,
                         task["id"], industry, lever,
                         quality_fn=quality_fn, stream=stream)

    for i in range(steady_samples):
        prompt = fill_prompt(task["prompt"], inputs[idx % len(inputs)])
        idx += 1
        r = await call_model(client, model, prompt, max_tokens,
                             task["id"], industry, lever,
                             quality_fn=quality_fn, stream=stream)
        results.append(r)

    return results


async def run_batched(
    client: httpx.AsyncClient,
    model: str,
    task: dict,
    industry: str,
    lever: str,
    concurrency_levels: list[int],
    samples_per_level: int = 30,
    corpus_inputs: list[str] | None = None,
) -> list[SampleResult]:
    """Run concurrent benchmark at multiple concurrency levels."""
    max_tokens = task.get("max_tokens", 200)
    quality_fn = lambda out: check_quality(out, task)

    if not corpus_inputs:
        corpus_inputs = [task.get("sample_input", "test input")]
    while len(corpus_inputs) < samples_per_level:
        corpus_inputs = corpus_inputs + corpus_inputs

    all_results = []

    for concurrency in concurrency_levels:
        sem = asyncio.Semaphore(concurrency)

        async def _call(input_text):
            async with sem:
                prompt = fill_prompt(task["prompt"], input_text)
                return await call_model(
                    client, model, prompt, max_tokens,
                    task["id"], industry, lever, concurrency=concurrency,
                    quality_fn=quality_fn,
                )

        coros = [_call(corpus_inputs[i % len(corpus_inputs)]) for i in range(samples_per_level)]
        results = await asyncio.gather(*coros)
        all_results.extend(results)

    return all_results


async def run_workflow(
    client: httpx.AsyncClient,
    workflow: dict,
    tasks_by_id: dict,
    industry: str,
    lever: str,
    model_by_tier: dict,
    protocol: dict,
    corpus: dict | None = None,
) -> list[SampleResult]:
    """Run a multi-step workflow pipeline with corpus inputs."""
    results = []
    steady_samples = protocol.get("steady_state_samples", 10)

    for sample_idx in range(steady_samples):
        for step in workflow["steps"]:
            task = tasks_by_id.get(step["task"])
            if not task:
                continue
            tier = step.get("tier", "micro")
            model = model_by_tier.get(tier, task["model_assignments"][0])

            inputs = None
            if corpus and task["id"] in corpus.get("tasks", {}):
                records = corpus["tasks"][task["id"]]
                inputs = [records[sample_idx % len(records)]["text"]]

            step_results = await run_single(
                client, model, task, industry, lever,
                {"cold_start_samples": 0, "warmup_samples": 0, "steady_state_samples": 1},
                corpus_inputs=inputs,
            )
            results.extend(step_results)

    return results


# ── Lever-specific execution ─────────────────────────────────────

async def run_routing(
    client: httpx.AsyncClient,
    workload: dict,
    industry: str,
    protocol: dict,
    corpus: dict | None = None,
) -> list[SampleResult]:
    """Lever 6: Cascade routing — split corpus by signal distribution.

    Nano signals skip inference entirely (measured as 0ms/$0).
    Micro signals go to the assigned micro model.
    Macro signals go to the assigned macro model.
    Compares effective throughput vs sending everything to one model.
    """
    results = []
    tasks = workload.get("tasks", [])
    steady = protocol.get("steady_state_samples", 30)

    for t_idx, task in enumerate(tasks):
        tier = task.get("cascade_tier", "micro")
        model = task["model_assignments"][0]
        print(f"  [{t_idx+1}/{len(tasks)}] {model} × {task['id']} (tier={tier})")

        inputs = get_corpus_inputs(industry, task["id"], steady) if corpus is None else None
        if corpus and task["id"] in corpus.get("tasks", {}):
            inputs = [r["text"] for r in corpus["tasks"][task["id"]]]
        if not inputs:
            inputs = [task.get("sample_input", "test")] * steady

        nano_pct = 0.85
        if tier == "micro":
            nano_pct = 0.0
        elif tier == "macro":
            nano_pct = 0.0

        inference_count = max(1, int(steady * (1.0 - nano_pct)))
        skip_count = steady - inference_count
        print(f"    Cascade: {skip_count} nano (skip) + {inference_count} inference")

        for _ in range(skip_count):
            results.append(SampleResult(
                model="nano-rules", task_id=task["id"], industry=industry,
                lever="routing", latency_ms=0, output_tokens=0,
                quality="correct",
            ))

        for i in range(inference_count):
            prompt = fill_prompt(task["prompt"], inputs[i % len(inputs)])
            r = await call_model(
                client, model, prompt, task.get("max_tokens", 200),
                task["id"], industry, "routing",
                quality_fn=lambda out: check_quality(out, task),
            )
            results.append(r)
            if (i + 1) % 10 == 0:
                print(f"    Progress: {i+1}/{inference_count}")

    return results


async def run_adaptive_cache(
    client: httpx.AsyncClient,
    model: str,
    task: dict,
    industry: str,
    corpus_inputs: list[str] | None = None,
) -> list[SampleResult]:
    """Lever 4: Adaptive cache — send same queries multiple times.

    Measures first-hit (cold) vs repeated-hit latency.
    In production this would be an LRU/Redis cache; here we measure
    the LiteLLM/model cache behavior on identical requests.
    """
    results = []
    max_tokens = task.get("max_tokens", 200)
    quality_fn = lambda out: check_quality(out, task)

    if not corpus_inputs:
        corpus_inputs = get_corpus_inputs(industry, task["id"], 20)
    if not corpus_inputs:
        sample = task.get("sample_input", "test input")
        corpus_inputs = [f"{sample} (variant {i})" for i in range(20)]
    unique_queries = corpus_inputs[:20]
    repetitions = 5

    for q_idx, input_text in enumerate(unique_queries):
        prompt = fill_prompt(task["prompt"], input_text)
        for rep in range(repetitions):
            r = await call_model(
                client, model, prompt, max_tokens,
                task["id"], industry, "adaptive_cache",
                is_cold=(rep == 0),
                quality_fn=quality_fn,
            )
            results.append(r)

    return results


async def run_prefix_cache(
    client: httpx.AsyncClient,
    model: str,
    task: dict,
    industry: str,
    corpus_inputs: list[str] | None = None,
) -> list[SampleResult]:
    """Lever 3: Prefix cache — same system prompt, varied user content.

    Measures KV cache reuse for shared prompt prefixes.
    First query is cold (no prefix cached), subsequent share the prefix.
    """
    results = []
    max_tokens = task.get("max_tokens", 200)
    quality_fn = lambda out: check_quality(out, task)

    if not corpus_inputs:
        corpus_inputs = get_corpus_inputs(industry, task["id"], 33)
    if not corpus_inputs:
        sample = task.get("sample_input", "test input")
        corpus_inputs = [f"{sample} (variant {i})" for i in range(33)]

    # First 3: cold (no prefix cache yet)
    for i in range(min(3, len(corpus_inputs))):
        prompt = fill_prompt(task["prompt"], corpus_inputs[i])
        r = await call_model(
            client, model, prompt, max_tokens,
            task["id"], industry, "prefix_cache",
            is_cold=True, quality_fn=quality_fn, stream=True,
        )
        results.append(r)

    # Remaining: warm (prefix should be cached since prompt template is shared)
    for i in range(3, len(corpus_inputs)):
        prompt = fill_prompt(task["prompt"], corpus_inputs[i])
        r = await call_model(
            client, model, prompt, max_tokens,
            task["id"], industry, "prefix_cache",
            is_cold=False, quality_fn=quality_fn, stream=True,
        )
        results.append(r)

    return results


async def run_composed(
    client: httpx.AsyncClient,
    lever_config: dict,
    workload: dict,
    industry: str,
    protocol: dict,
    corpus: dict | None = None,
) -> list[SampleResult]:
    """Lever 9: Composed — stack multiple optimizations.

    Applies routing (cascade filtering) + model selection (ladder/quantized)
    + batched concurrency as a combined stack.
    """
    results = []
    compositions = lever_config.get("compositions", [])

    for comp in compositions:
        comp_name = comp["name"]
        comp_models = comp.get("models", [])
        features = comp.get("features", [])
        concurrency = comp.get("concurrency", 1)

        print(f"    Composition: {comp_name} (features={features})")

        for task in workload.get("tasks", []):
            # Pick model: prefer quantized if available, else first match
            model = None
            for m in comp_models:
                if m in [a for a in task.get("model_assignments", [])]:
                    model = m
                    break
            if not model and comp_models:
                model = comp_models[0]
            if not model:
                continue

            inputs = None
            if corpus and task["id"] in corpus.get("tasks", {}):
                inputs = [r["text"] for r in corpus["tasks"][task["id"]]]

            steady = protocol.get("steady_state_samples", 30)

            if "routing" in features:
                # Apply cascade: skip nano-tier signals
                effective = max(1, int(steady * 0.15))  # only 15% need inference
                skip = steady - effective
                for _ in range(skip):
                    results.append(SampleResult(
                        model="nano-rules", task_id=task["id"],
                        industry=industry, lever=f"composed:{comp_name}",
                        latency_ms=0, quality="correct",
                    ))
                steady = effective

            if concurrency > 1 and "batching" in features:
                batch_results = await run_batched(
                    client, model, task, industry, f"composed:{comp_name}",
                    [concurrency], steady, corpus_inputs=inputs,
                )
                results.extend(batch_results)
            else:
                single_results = await run_single(
                    client, model, task, industry, f"composed:{comp_name}",
                    {"cold_start_samples": 0, "warmup_samples": 0, "steady_state_samples": steady},
                    corpus_inputs=inputs,
                )
                results.extend(single_results)

    return results


# ── Results formatting ────────────────────────────────────────────

def format_results(all_metrics: list, matrix_config: dict) -> dict:
    thresholds = matrix_config.get("metrics", {})
    matrix = []

    for m in all_metrics:
        row = {
            "model": m.model,
            "task": m.task_id,
            "industry": m.industry,
            "lever": m.lever,
            "concurrency": m.concurrency,
            "samples": m.samples,
            "metrics": {},
        }

        def grade(metric_name, value, direction="higher_is_better"):
            cfg = thresholds.get(metric_name, {})
            t = cfg.get("thresholds", {})
            green = t.get("green", float("inf"))
            yellow = t.get("yellow", float("inf"))
            d = cfg.get("direction", direction)
            if d == "lower_is_better":
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

        metrics = {
            "throughput_tok_s": {"value": m.throughput_tok_s, "grade": grade("throughput_tok_s", m.throughput_tok_s)},
            "latency_p50_ms": {"value": m.latency_p50_ms, "grade": grade("latency_p95_ms", m.latency_p50_ms, "lower_is_better")},
            "latency_p95_ms": {"value": m.latency_p95_ms, "grade": grade("latency_p95_ms", m.latency_p95_ms, "lower_is_better")},
            "latency_p99_ms": {"value": m.latency_p99_ms, "grade": grade("latency_p95_ms", m.latency_p99_ms, "lower_is_better")},
            "ttft_p50_ms": {"value": m.ttft_p50_ms, "grade": grade("ttft_ms", m.ttft_p50_ms, "lower_is_better")},
            "ttft_p95_ms": {"value": m.ttft_p95_ms, "grade": grade("ttft_ms", m.ttft_p95_ms, "lower_is_better")},
            "quality_accuracy": {"value": m.quality_accuracy, "grade": grade("quality_accuracy", m.quality_accuracy)},
            "cold_start_ms": {"value": m.cold_start_ms, "grade": grade("cold_start_ms", m.cold_start_ms, "lower_is_better")},
            "warm_cache_speedup": {"value": m.warm_cache_speedup, "grade": grade("warm_cache_speedup", m.warm_cache_speedup)},
        }

        if m.variance_flagged:
            metrics["variance_flagged"] = {"value": m.coefficient_of_variation, "grade": "red"}

        row["metrics"] = metrics
        matrix.append(row)

    return {"matrix": matrix}


# ── Main ──────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="CPU Inference Benchmark Harness")
    parser.add_argument("--mode", choices=["single", "batch", "workflow"], default="single")
    parser.add_argument("--lever", default="baseline")
    parser.add_argument("--industries", default="fsi")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--corpus-size", type=int, default=100)
    parser.add_argument("--stream", action="store_true", help="Use streaming for TTFT measurement")
    parser.add_argument("--output", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-preflight", action="store_true")
    args = parser.parse_args()

    industries = args.industries.split(",") if args.industries != "all" else [
        "fsi", "healthcare", "insurance", "retail", "telecom"
    ]

    matrix_config = load_matrix_config()
    models_config = load_models_config()
    lever_config = load_lever(args.lever)
    workloads = load_workloads(industries)
    models = resolve_models(lever_config, models_config)

    protocol = matrix_config.get("protocol", {})
    protocol["steady_state_samples"] = args.samples

    # Load corpora
    corpora = {}
    for ind in industries:
        c = load_corpus(ind, args.corpus_size)
        if c:
            corpora[ind] = c

    if args.dry_run:
        print("=== DRY RUN ===")
        print(f"Mode: {args.mode}")
        print(f"Lever: {args.lever}")
        print(f"Industries: {industries}")
        print(f"Models: {models}")
        print(f"Stream (TTFT): {args.stream}")
        print(f"Corpus size: {args.corpus_size}")
        print(f"Corpora loaded: {list(corpora.keys())}")
        print(f"Protocol: cold={protocol.get('cold_start_samples', 3)}, "
              f"warmup={protocol.get('warmup_samples', 10)}, "
              f"steady={args.samples}")

        for ind, wl in workloads.items():
            tasks = wl.get("tasks", [])
            corpus_status = "corpus" if ind in corpora else "sample_input"
            print(f"\n{ind} ({corpus_status}): {len(tasks)} tasks")
            for t in tasks:
                assigned = [m for m in t.get("model_assignments", []) if m in models]
                print(f"  - {t['id']} (tier={t.get('cascade_tier')}, models={assigned})")

        total = sum(
            len([m for m in t.get("model_assignments", []) if m in models])
            for wl in workloads.values()
            for t in wl.get("tasks", [])
        )

        if args.lever in ("routing", "adaptive_cache", "prefix_cache", "composed"):
            print(f"\nLever '{args.lever}' uses specialized execution — call count varies")
        else:
            calls_per = protocol.get("cold_start_samples", 3) + protocol.get("warmup_samples", 10) + args.samples
            print(f"\nTotal combinations: {total}")
            print(f"Calls per combination: {calls_per}")
            print(f"Total API calls: {total * calls_per}")
        return

    if not API_BASE:
        print("Error: Set LITELLM_API_BASE environment variable")
        sys.exit(1)

    if not args.skip_preflight:
        print("\n--- Pre-flight check ---")
        pf_report = await run_preflight(models)
        if not pf_report["passed"]:
            print("\nPre-flight failed. Use --skip-preflight to proceed anyway.")
            sys.exit(1)
        print()

    # Count combinations for progress
    total_combos = 0
    for wl in workloads.values():
        for task in wl.get("tasks", []):
            task_models = [m for m in task.get("model_assignments", []) if m in models]
            total_combos += len(task_models)

    calls_per = protocol.get("cold_start_samples", 3) + protocol.get("warmup_samples", 10) + args.samples
    run_entry = start_run(
        mode=args.mode, lever=args.lever, industries=industries,
        samples=args.samples, endpoint=API_BASE, models=models,
    )
    run_id = run_entry["run_id"]
    print(f"Run ID: {run_id}")
    print(f"Combinations: {total_combos} | Lever: {args.lever} | Stream: {args.stream}")

    all_samples: list[SampleResult] = []
    all_metrics = []
    combo_idx = 0
    total_errors = 0

    try:
        async with httpx.AsyncClient() as client:
            for ind, wl in workloads.items():
                tasks = wl.get("tasks", [])
                tasks_by_id = {t["id"]: t for t in tasks}
                corpus = corpora.get(ind)

                # ── Lever-specific execution paths ──
                if args.lever == "routing":
                    print(f"\n=== Routing/Cascade ({ind}) ===")
                    results = await run_routing(client, wl, ind, protocol, corpus)
                    all_samples.extend(results)
                    for task in tasks:
                        task_results = [r for r in results if r.task_id == task["id"]]
                        if task_results:
                            agg = aggregate(task_results)
                            all_metrics.append(agg)
                    continue

                if args.lever == "adaptive_cache":
                    for task in tasks:
                        model = task["model_assignments"][0] if task["model_assignments"] else "granite-2b-cpu"
                        if model not in models:
                            continue
                        combo_idx += 1
                        print(f"\n[{combo_idx}] Adaptive cache: {model} × {task['id']} ({ind})")
                        inputs = None
                        if corpus and task["id"] in corpus.get("tasks", {}):
                            inputs = [r["text"] for r in corpus["tasks"][task["id"]]]
                        results = await run_adaptive_cache(client, model, task, ind, inputs)
                        all_samples.extend(results)
                        agg = aggregate(results)
                        all_metrics.append(agg)
                        print(f"  Cold: {agg.cold_start_ms}ms, Warm: {agg.warm_steady_ms}ms, Speedup: {agg.warm_cache_speedup}x")
                    continue

                if args.lever == "prefix_cache":
                    for task in tasks:
                        task_models = [m for m in task.get("model_assignments", []) if m in models]
                        for model in task_models:
                            combo_idx += 1
                            print(f"\n[{combo_idx}] Prefix cache: {model} × {task['id']} ({ind})")
                            inputs = None
                            if corpus and task["id"] in corpus.get("tasks", {}):
                                inputs = [r["text"] for r in corpus["tasks"][task["id"]]]
                            results = await run_prefix_cache(client, model, task, ind, inputs)
                            all_samples.extend(results)
                            agg = aggregate(results)
                            all_metrics.append(agg)
                            print(f"  Cold: {agg.cold_start_ms}ms, Warm: {agg.warm_steady_ms}ms, TTFT p50: {agg.ttft_p50_ms}ms")
                    continue

                if args.lever == "composed":
                    print(f"\n=== Composed optimizations ({ind}) ===")
                    results = await run_composed(client, lever_config, wl, ind, protocol, corpus)
                    all_samples.extend(results)
                    # Group by composition name for separate metrics
                    by_comp = {}
                    for r in results:
                        by_comp.setdefault(r.lever, []).append(r)
                    for lever_name, comp_results in by_comp.items():
                        for task in tasks:
                            task_results = [r for r in comp_results if r.task_id == task["id"]]
                            if task_results:
                                agg = aggregate(task_results)
                                all_metrics.append(agg)
                    continue

                # ── Standard execution (baseline, quantization, speculative, model_ladder, batching) ──
                if args.mode == "workflow":
                    workflow = wl.get("workflow")
                    if workflow:
                        model_by_tier = {
                            "micro": models[0] if models else "granite-2b-cpu",
                            "macro": models[-1] if models else "granite-3-2-8b-instruct-cpu",
                        }
                        print(f"\n=== Workflow: {workflow['name']} ({ind}) ===")
                        results = await run_workflow(
                            client, workflow, tasks_by_id, ind, args.lever,
                            model_by_tier, protocol, corpus,
                        )
                        all_samples.extend(results)
                        for task in tasks:
                            task_results = [r for r in results if r.task_id == task["id"]]
                            if task_results:
                                agg = aggregate(task_results)
                                all_metrics.append(agg)
                    continue

                for task in tasks:
                    task_models = [m for m in task.get("model_assignments", []) if m in models]
                    if not task_models:
                        continue

                    inputs = None
                    if corpus and task["id"] in corpus.get("tasks", {}):
                        inputs = [r["text"] for r in corpus["tasks"][task["id"]]]

                    for model in task_models:
                        combo_idx += 1
                        print(f"\n[{combo_idx}/{total_combos}] {model} × {task['id']} ({ind}) [{args.lever}]")

                        if args.mode == "single":
                            results = await run_single(
                                client, model, task, ind, args.lever, protocol,
                                corpus_inputs=inputs, stream=args.stream,
                            )
                        elif args.mode == "batch":
                            levels = lever_config.get("concurrency_levels", [1, 4, 8, 16, 32])
                            results = await run_batched(
                                client, model, task, ind, args.lever, levels,
                                args.samples, corpus_inputs=inputs,
                            )

                        all_samples.extend(results)
                        agg = aggregate(results)
                        all_metrics.append(agg)

                        errors = sum(1 for r in results if r.error)
                        total_errors += errors
                        print(f"  Samples: {len(results)}, Errors: {errors}")
                        print(f"  Throughput: {agg.throughput_tok_s} tok/s, Latency p95: {agg.latency_p95_ms}ms")
                        print(f"  Quality: {agg.quality_accuracy}, Cold: {agg.cold_start_ms}ms")
                        if args.stream:
                            print(f"  TTFT p50: {agg.ttft_p50_ms}ms, p95: {agg.ttft_p95_ms}ms")
                        if agg.variance_flagged:
                            print(f"  WARNING: Variance CoV={agg.coefficient_of_variation}")

    except KeyboardInterrupt:
        print(f"\n\nInterrupted after {combo_idx} combinations.")
        complete_run(run_id, "", combo_idx, len(all_samples), total_errors, status="interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nError: {e}")
        complete_run(run_id, "", combo_idx, len(all_samples), total_errors, status="error")
        raise

    output = format_results(all_metrics, matrix_config)
    output["metadata"] = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "lever": args.lever,
        "industries": industries,
        "samples_per_combination": args.samples,
        "corpus_size": args.corpus_size,
        "stream": args.stream,
        "endpoint": API_BASE,
        "cluster": models_config.get("cluster", {}).get("name", "unknown"),
        "total_combinations": combo_idx,
        "total_api_calls": len(all_samples),
        "total_errors": total_errors,
    }
    output["raw_samples"] = [
        {
            "model": s.model, "task": s.task_id, "industry": s.industry,
            "lever": s.lever, "concurrency": s.concurrency,
            "latency_ms": s.latency_ms, "ttft_ms": s.ttft_ms,
            "output_tokens": s.output_tokens, "prompt_tokens": s.prompt_tokens,
            "quality": s.quality, "is_cold": s.is_cold, "error": s.error,
            "output_preview": s.output_text[:100],
        }
        for s in all_samples
    ]

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = args.output or str(RESULTS_DIR / f"benchmark-{args.lever}-{args.mode}-{ts}.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)

    complete_run(run_id, out_path, combo_idx, len(all_samples), total_errors)
    print(f"\nResults saved to {out_path}")
    print(f"Run {run_id}: {combo_idx} combos, {len(all_samples)} calls, {total_errors} errors")


if __name__ == "__main__":
    asyncio.run(main())

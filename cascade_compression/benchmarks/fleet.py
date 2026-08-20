"""Agentic fleet benchmark — jury voting, speculative swarm, pipeline, saturation.

Tests what happens when you run many cheap model instances in parallel on CPU.
BitNet at ~70ms/token with 2-4 cores means 30-60 instances on a single Xeon.

Usage:
  python -m benchmarks.fleet --pattern jury --agents 3,5,10 --model bitnet-2b
  python -m benchmarks.fleet --pattern swarm --agents 5 --model bitnet-2b
  python -m benchmarks.fleet --pattern saturation --agents 1,5,10,20,30,60 --model bitnet-2b
  python -m benchmarks.fleet --pattern all --model bitnet-2b
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    import httpx
    import yaml
except ImportError:
    print("pip install httpx pyyaml")
    sys.exit(1)

from .metrics import SampleResult, aggregate

BASE_DIR = Path(__file__).parent
RESULTS_DIR = Path(os.environ.get("BENCHMARK_RESULTS_DIR", str(BASE_DIR / "results")))

API_BASE = os.environ.get("LITELLM_API_BASE", "")
API_KEY = os.environ.get("LITELLM_API_KEY", "")

SWARM_FRAMINGS = [
    {
        "name": "direct",
        "system": "You are a precise classifier. Respond with only the category name.",
        "temperature": 0.1,
    },
    {
        "name": "chain_of_thought",
        "system": "Think step by step about the classification, then give your final answer on the last line as just the category name.",
        "temperature": 0.3,
    },
    {
        "name": "role_play",
        "system": "You are a senior financial analyst with 20 years of experience. Classify this input precisely. Respond with only the category name.",
        "temperature": 0.1,
    },
    {
        "name": "adversarial",
        "system": "Consider the most unlikely classification first and explain why it's wrong, then give the correct classification. Final line should be just the category name.",
        "temperature": 0.4,
    },
    {
        "name": "confidence",
        "system": "Classify this input and rate your confidence 1-10. Format: CATEGORY (confidence: N/10)",
        "temperature": 0.2,
    },
]

CLASSIFY_PROMPT = "Classify this text into exactly one category: positive, negative, neutral. Respond with only the category."
CLASSIFY_INPUT = "The new product launch exceeded all expectations with record sales and customer satisfaction scores."
CLASSIFY_EXPECTED = "positive"

EXTRACT_PROMPT = "Extract all named entities from this text as a JSON array. Each entity: {text, type}. Respond with only the JSON."
EXTRACT_INPUT = "John Smith met with Sarah Chen at the Google office in Mountain View on January 15th."
EXTRACT_EXPECTED = "John"


async def call_model(
    client: httpx.AsyncClient,
    model: str,
    prompt: str,
    system: str = "",
    max_tokens: int = 64,
    temperature: float = 0.1,
) -> dict:
    """Single inference call returning raw response data."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    start = time.monotonic()
    try:
        resp = await client.post(
            f"{API_BASE}/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature},
            timeout=120.0,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "latency_ms": latency_ms}
        data = resp.json()
        output = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return {
            "output": output,
            "latency_ms": latency_ms,
            "output_tokens": usage.get("completion_tokens", 0),
            "prompt_tokens": usage.get("prompt_tokens", 0),
        }
    except Exception as e:
        return {"error": str(e), "latency_ms": int((time.monotonic() - start) * 1000)}


def majority_vote(outputs: list[str], expected_lower: str) -> dict:
    """Determine majority vote result and quality."""
    cleaned = []
    for o in outputs:
        o_lower = o.strip().lower()
        for word in o_lower.split():
            if word in ("positive", "negative", "neutral"):
                cleaned.append(word)
                break
        else:
            cleaned.append(o_lower[:20])

    counts = Counter(cleaned)
    winner, count = counts.most_common(1)[0]
    return {
        "winner": winner,
        "votes": dict(counts),
        "agreement": count / len(outputs),
        "correct": expected_lower in winner,
        "total_agents": len(outputs),
    }


async def run_jury_voting(
    client: httpx.AsyncClient,
    model: str,
    agent_counts: list[int],
    samples: int = 20,
) -> dict:
    """Jury voting: N agents classify same input, majority vote wins."""
    results = {"pattern": "jury_voting", "model": model, "trials": []}

    for n_agents in agent_counts:
        print(f"  Jury N={n_agents}...")
        trial = {"n_agents": n_agents, "samples": samples, "correct": 0, "total_latency_ms": 0}

        for _ in range(samples):
            coros = [
                call_model(client, model, f"{CLASSIFY_PROMPT}\n\n{CLASSIFY_INPUT}")
                for _ in range(n_agents)
            ]
            start = time.monotonic()
            responses = await asyncio.gather(*coros)
            wall_ms = int((time.monotonic() - start) * 1000)

            outputs = [r.get("output", "") for r in responses if "error" not in r]
            if outputs:
                vote = majority_vote(outputs, CLASSIFY_EXPECTED)
                if vote["correct"]:
                    trial["correct"] += 1
                trial["total_latency_ms"] += wall_ms

        trial["accuracy"] = trial["correct"] / samples if samples > 0 else 0
        trial["avg_wall_latency_ms"] = trial["total_latency_ms"] // samples if samples > 0 else 0
        results["trials"].append(trial)
        print(f"    Accuracy: {trial['accuracy']:.0%}, Avg wall latency: {trial['avg_wall_latency_ms']}ms")

    return results


async def run_speculative_swarm(
    client: httpx.AsyncClient,
    model: str,
    n_agents: int = 5,
    samples: int = 20,
) -> dict:
    """Speculative swarm: N agents with different framings, best-of-N selection."""
    framings = SWARM_FRAMINGS[:n_agents]
    results = {
        "pattern": "speculative_swarm",
        "model": model,
        "n_agents": n_agents,
        "framings": [f["name"] for f in framings],
        "samples": samples,
        "correct_single": 0,
        "correct_swarm": 0,
        "correct_per_framing": {f["name"]: 0 for f in framings},
        "total_wall_latency_ms": 0,
    }

    for _ in range(samples):
        coros = [
            call_model(
                client, model,
                f"{CLASSIFY_PROMPT}\n\n{CLASSIFY_INPUT}",
                system=f["system"],
                temperature=f["temperature"],
            )
            for f in framings
        ]
        start = time.monotonic()
        responses = await asyncio.gather(*coros)
        wall_ms = int((time.monotonic() - start) * 1000)
        results["total_wall_latency_ms"] += wall_ms

        # Track per-framing accuracy
        for i, (f, r) in enumerate(zip(framings, responses)):
            if "error" not in r:
                output = r["output"].strip().lower()
                if CLASSIFY_EXPECTED in output:
                    results["correct_per_framing"][f["name"]] += 1

        # Single agent baseline (first framing only)
        if "error" not in responses[0]:
            if CLASSIFY_EXPECTED in responses[0].get("output", "").lower():
                results["correct_single"] += 1

        # Swarm consensus (majority vote across all framings)
        outputs = [r.get("output", "") for r in responses if "error" not in r]
        if outputs:
            vote = majority_vote(outputs, CLASSIFY_EXPECTED)
            if vote["correct"]:
                results["correct_swarm"] += 1

    results["accuracy_single"] = results["correct_single"] / samples
    results["accuracy_swarm"] = results["correct_swarm"] / samples
    results["quality_uplift"] = results["accuracy_swarm"] - results["accuracy_single"]
    results["avg_wall_latency_ms"] = results["total_wall_latency_ms"] // samples

    for f_name in results["correct_per_framing"]:
        results["correct_per_framing"][f_name] /= samples

    print(f"  Single agent accuracy: {results['accuracy_single']:.0%}")
    print(f"  Swarm accuracy ({n_agents} agents): {results['accuracy_swarm']:.0%}")
    print(f"  Quality uplift: {results['quality_uplift']:+.0%}")
    print(f"  Avg wall latency: {results['avg_wall_latency_ms']}ms (parallel)")
    print(f"  Per-framing accuracy: {results['correct_per_framing']}")

    return results


async def run_pipeline_parallel(
    client: httpx.AsyncClient,
    model: str,
    samples: int = 10,
) -> dict:
    """Pipeline parallelism: each workflow step on its own agent instance."""
    steps = [
        {"name": "classify", "prompt": f"{CLASSIFY_PROMPT}\n\n{CLASSIFY_INPUT}", "max_tokens": 32},
        {"name": "extract", "prompt": f"{EXTRACT_PROMPT}\n\n{EXTRACT_INPUT}", "max_tokens": 128},
        {"name": "summarize", "prompt": f"Summarize in one sentence: {CLASSIFY_INPUT}", "max_tokens": 64},
    ]

    # Sequential baseline
    print("  Sequential pipeline...")
    seq_latencies = []
    for _ in range(samples):
        total_ms = 0
        for step in steps:
            r = await call_model(client, model, step["prompt"], max_tokens=step["max_tokens"])
            total_ms += r.get("latency_ms", 0)
        seq_latencies.append(total_ms)

    # Parallel pipeline (all steps fire simultaneously)
    print("  Parallel pipeline...")
    par_latencies = []
    for _ in range(samples):
        start = time.monotonic()
        coros = [
            call_model(client, model, step["prompt"], max_tokens=step["max_tokens"])
            for step in steps
        ]
        await asyncio.gather(*coros)
        par_latencies.append(int((time.monotonic() - start) * 1000))

    seq_avg = sum(seq_latencies) // len(seq_latencies) if seq_latencies else 0
    par_avg = sum(par_latencies) // len(par_latencies) if par_latencies else 0
    speedup = seq_avg / par_avg if par_avg > 0 else 0

    results = {
        "pattern": "pipeline_parallel",
        "model": model,
        "steps": len(steps),
        "samples": samples,
        "sequential_avg_ms": seq_avg,
        "parallel_avg_ms": par_avg,
        "speedup": round(speedup, 2),
    }

    print(f"  Sequential avg: {seq_avg}ms")
    print(f"  Parallel avg: {par_avg}ms")
    print(f"  Speedup: {speedup:.2f}x")

    return results


async def run_saturation(
    client: httpx.AsyncClient,
    model: str,
    agent_counts: list[int],
    calls_per_agent: int = 10,
) -> dict:
    """Saturation test: scale concurrent agents until throughput plateaus."""
    results = {"pattern": "saturation", "model": model, "levels": []}

    for n_agents in agent_counts:
        total_calls = n_agents * calls_per_agent
        print(f"  Saturation N={n_agents} ({total_calls} calls)...")

        sem = asyncio.Semaphore(n_agents)

        async def _call():
            async with sem:
                return await call_model(
                    client, model,
                    f"{CLASSIFY_PROMPT}\n\n{CLASSIFY_INPUT}",
                    max_tokens=32,
                )

        start = time.monotonic()
        responses = await asyncio.gather(*[_call() for _ in range(total_calls)])
        wall_seconds = time.monotonic() - start

        errors = sum(1 for r in responses if "error" in r)
        total_tokens = sum(r.get("output_tokens", 0) for r in responses if "error" not in r)
        agg_tok_s = total_tokens / wall_seconds if wall_seconds > 0 else 0
        latencies = [r.get("latency_ms", 0) for r in responses if "error" not in r]
        avg_latency = sum(latencies) // len(latencies) if latencies else 0

        level = {
            "n_agents": n_agents,
            "total_calls": total_calls,
            "wall_seconds": round(wall_seconds, 2),
            "aggregate_tok_s": round(agg_tok_s, 2),
            "avg_latency_ms": avg_latency,
            "errors": errors,
        }
        results["levels"].append(level)
        print(f"    Agg throughput: {agg_tok_s:.1f} tok/s, Avg latency: {avg_latency}ms, Errors: {errors}")

    return results


async def main():
    parser = argparse.ArgumentParser(description="Agentic Fleet Benchmark")
    parser.add_argument("--pattern", default="all", choices=["jury", "swarm", "pipeline", "saturation", "all"])
    parser.add_argument("--model", default="bitnet-2b")
    parser.add_argument("--agents", default="3,5,10")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    agent_counts = [int(x) for x in args.agents.split(",")]

    if not API_BASE:
        print("Error: Set LITELLM_API_BASE")
        sys.exit(1)

    results = {
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": args.model,
            "pattern": args.pattern,
            "agent_counts": agent_counts,
            "samples": args.samples,
            "endpoint": API_BASE,
        },
        "benchmarks": [],
    }

    async with httpx.AsyncClient() as client:
        if args.pattern in ("jury", "all"):
            print(f"\n=== Jury Voting ({args.model}) ===")
            r = await run_jury_voting(client, args.model, agent_counts, args.samples)
            results["benchmarks"].append(r)

        if args.pattern in ("swarm", "all"):
            print(f"\n=== Speculative Swarm ({args.model}) ===")
            for n in agent_counts:
                r = await run_speculative_swarm(client, args.model, min(n, len(SWARM_FRAMINGS)), args.samples)
                results["benchmarks"].append(r)

        if args.pattern in ("pipeline", "all"):
            print(f"\n=== Pipeline Parallelism ({args.model}) ===")
            r = await run_pipeline_parallel(client, args.model, args.samples)
            results["benchmarks"].append(r)

        if args.pattern in ("saturation", "all"):
            sat_counts = [1, 5, 10, 20, 30] if agent_counts == [3, 5, 10] else agent_counts
            print(f"\n=== Saturation Test ({args.model}) ===")
            r = await run_saturation(client, args.model, sat_counts)
            results["benchmarks"].append(r)

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = args.output or str(RESULTS_DIR / f"fleet-{args.model}-{args.pattern}-{ts}.json")
    RESULTS_DIR.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())

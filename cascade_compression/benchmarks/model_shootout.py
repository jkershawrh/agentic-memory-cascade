"""Model Shootout — benchmark all GGUF models for real quality + throughput.

Deploys each model one at a time via llama-cpp, runs 20 real classification
tests, measures throughput, and produces the definitive comparison matrix.

Usage:
    python -m benchmarks.model_shootout

Requires:
    - oc CLI authenticated to Oberon
    - intel-inference namespace with model-cache PVC
    - bench-model deployment + service already created
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

NAMESPACE = "intel-inference"
DEPLOY = "bench-model"
SERVICE = "bench-model"
PORT = 8080

MODELS = [
    # (name, gguf_path, max_tokens, threads, memory_limit, is_thinking_model)
    ("qwen3-0.6b", "/models/gguf/qwen3-0.6b/Qwen_Qwen3-0.6B-Q4_K_M.gguf", 500, 4, "4Gi", True),
    ("smollm2-360m", "/models/gguf/smollm2-360m/SmolLM2-360M-Instruct-Q8_0.gguf", 20, 4, "2Gi", False),
    ("tinyllama-1.1b", "/models/gguf/tinyllama-1.1b/TinyLlama-1.1B-Chat-v1.0.Q4_K_M.gguf", 20, 4, "2Gi", False),
    ("gemma3-1b", "/models/gguf/gemma3-1b/google_gemma-3-1b-it-Q4_K_M.gguf", 20, 4, "4Gi", False),
    ("llama32-1b", "/models/gguf/llama32-1b/Llama-3.2-1B-Instruct-Q4_K_M.gguf", 20, 4, "4Gi", False),
    ("qwen25-1.5b", "/models/gguf/qwen25-1.5b/qwen2.5-1.5b-instruct-q4_k_m.gguf", 20, 4, "4Gi", False),
    ("deepseek-r1-1.5b", "/models/gguf/deepseek-r1-1.5b/DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf", 500, 4, "4Gi", True),
    ("qwen3-1.7b", "/models/gguf/qwen3-1.7b/Qwen_Qwen3-1.7B-Q4_K_M.gguf", 500, 4, "4Gi", True),
    ("smollm3-3b", "/models/gguf/smollm3-3b/SmolLM3-Q4_K_M.gguf", 500, 8, "8Gi", True),
    ("phi4-mini", "/models/gguf/phi4-mini/microsoft_Phi-4-mini-instruct-Q4_K_M.gguf", 20, 8, "8Gi", False),
    ("gemma3-4b", "/models/gguf/gemma3-4b/google_gemma-3-4b-it-Q4_K_M.gguf", 20, 8, "8Gi", False),
    ("qwen3-4b", "/models/gguf/qwen3-4b/Qwen_Qwen3-4B-Q4_K_M.gguf", 500, 8, "8Gi", True),
    ("mistral-7b", "/models/gguf/mistral-7b/Mistral-7B-Instruct-v0.3-Q4_K_M.gguf", 20, 16, "16Gi", False),
]

TESTS = [
    {"prompt": "Is this suspicious or legitimate? Wire transfer $95K to offshore account at 3am from dormant account.", "expected": "suspicious", "cat": "fraud"},
    {"prompt": "Is this suspicious or legitimate? Customer bought groceries for $47.99 at their usual store.", "expected": "legitimate", "cat": "fraud"},
    {"prompt": "Is this suspicious or legitimate? Three ATM withdrawals of $9,999 in one hour from different cities.", "expected": "suspicious", "cat": "fraud"},
    {"prompt": "Is this suspicious or legitimate? Monthly salary deposit of $5,200 from employer on record.", "expected": "legitimate", "cat": "fraud"},
    {"prompt": "Is this suspicious or legitimate? $50K wire to new payee in sanctioned country.", "expected": "suspicious", "cat": "fraud"},
    {"prompt": "Severity: critical, high, medium, or low? Server disk at 98%, alerts firing, users impacted.", "expected": "critical", "cat": "triage"},
    {"prompt": "Severity: critical, high, medium, or low? Minor CSS rendering glitch on admin page.", "expected": "low", "cat": "triage"},
    {"prompt": "Severity: critical, high, medium, or low? Database primary node down, failover activated.", "expected": "critical", "cat": "triage"},
    {"prompt": "Severity: critical, high, medium, or low? Scheduled maintenance completed 5 minutes late.", "expected": "low", "cat": "triage"},
    {"prompt": "Severity: critical, high, medium, or low? Memory leak causing gradual degradation over 24 hours.", "expected": "medium", "cat": "triage"},
    {"prompt": "Sentiment: positive, negative, or mixed? Product arrived damaged and support was unhelpful.", "expected": "negative", "cat": "sentiment"},
    {"prompt": "Sentiment: positive, negative, or mixed? Best purchase ever, exceeded expectations.", "expected": "positive", "cat": "sentiment"},
    {"prompt": "Sentiment: positive, negative, or mixed? Product damaged but support shipped replacement same day.", "expected": "mixed", "cat": "sentiment"},
    {"prompt": "Route to: network, billing, or support? Customer reports dropped calls downtown peak hours.", "expected": "network", "cat": "routing"},
    {"prompt": "Route to: network, billing, or support? Customer disputes charge on latest invoice.", "expected": "billing", "cat": "routing"},
    {"prompt": "Route to: network, billing, or support? Customer cannot log into their account.", "expected": "support", "cat": "routing"},
    {"prompt": "Extract as JSON: {name, amount, bank} from: John Smith applied for $250K mortgage at First National Bank.", "expected": "john", "cat": "extract"},
    {"prompt": "Summarize in one sentence: Q3 showed 12% revenue increase from cloud, margins down 2% from R&D.", "expected": "revenue", "cat": "summarize"},
    {"prompt": "Does this need immediate human review? Patient allergy alert triggered during medication order. Yes or no.", "expected": "yes", "cat": "binary"},
    {"prompt": "Does this need immediate human review? Routine log rotation completed successfully. Yes or no.", "expected": "no", "cat": "binary"},
]

SYSTEM_PROMPT = "You are a classification assistant. Answer with exactly one word. No explanation. No thinking."


def run_cmd(cmd: str, timeout: int = 30) -> str:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return result.stdout + result.stderr


def swap_model(name: str, gguf_path: str, threads: int, mem_limit: str, is_thinking: bool = False):
    """Patch the bench-model deployment to load a different model."""
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"DEPLOYING: {name} ({'thinking' if is_thinking else 'standard'})", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    args = [
        "--model", gguf_path,
        "--host", "0.0.0.0",
        "--port", "8080",
        "--ctx-size", "2048",
        "--threads", str(threads),
        "--alias", "bench",
    ]
    if is_thinking:
        args.extend(["--reasoning-format", "none"])

    patch = json.dumps({
        "spec": {"template": {"spec": {"containers": [{
            "name": "llama",
            "args": args,
            "resources": {
                "requests": {"cpu": str(threads // 2), "memory": mem_limit},
                "limits": {"cpu": str(threads), "memory": mem_limit},
            },
        }]}}}
    })

    run_cmd(f"oc patch deploy/{DEPLOY} -n {NAMESPACE} -p '{patch}' --type=merge", timeout=15)

    # Wait for rollout
    for attempt in range(12):
        time.sleep(10)
        pods = run_cmd(f"oc get pods -n {NAMESPACE} -l app={DEPLOY} --no-headers")
        if "1/1" in pods and "Running" in pods:
            print(f"  Model loaded ({(attempt+1)*10}s)", file=sys.stderr)
            return True
        print(f"  Waiting... ({(attempt+1)*10}s)", file=sys.stderr)

    print(f"  TIMEOUT — model failed to load", file=sys.stderr)
    return False


def wait_for_model_ready(max_wait: int = 120) -> bool:
    """Wait until the model responds to inference."""
    for i in range(max_wait // 5):
        try:
            with httpx.Client(timeout=10) as c:
                r = c.post(
                    f"http://localhost:8080/v1/chat/completions",
                    json={"model": "bench", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 3},
                )
                if r.status_code == 200:
                    return True
        except:
            pass
        time.sleep(5)
    return False


def run_quality_test(max_tokens: int) -> dict:
    """Run all 20 tests and return results."""
    from collections import defaultdict

    by_cat = defaultdict(lambda: {"correct": 0, "total": 0})
    total_correct = 0
    latencies = []

    for test in TESTS:
        t0 = time.monotonic()
        try:
            with httpx.Client(timeout=120) as c:
                r = c.post(
                    "http://localhost:8080/v1/chat/completions",
                    json={
                        "model": "bench",
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": test["prompt"]},
                        ],
                        "max_tokens": max_tokens,
                    },
                )
                d = r.json()
            elapsed = (time.monotonic() - t0) * 1000
            raw = d["choices"][0]["message"]["content"].strip()
            clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            if not clean and "<think>" in raw:
                clean = "(thinking only)"
            answer = clean.lower()[:40]
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            answer = f"ERR"

        latencies.append(elapsed)
        correct = test["expected"] in answer
        total_correct += 1 if correct else 0
        by_cat[test["cat"]]["total"] += 1
        by_cat[test["cat"]]["correct"] += 1 if correct else 0

        mark = "PASS" if correct else "FAIL"
        print(f"  [{mark}] {test['cat']:10s} exp={test['expected']:12s} got=\"{answer[:25]}\" ({elapsed:.0f}ms)", file=sys.stderr)

    pct = total_correct / len(TESTS) * 100
    avg_lat = sum(latencies) / len(latencies)

    return {
        "overall_pct": round(pct, 1),
        "correct": total_correct,
        "total": len(TESTS),
        "avg_latency_ms": round(avg_lat),
        "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.95)]),
        "by_category": {
            k: {"correct": v["correct"], "total": v["total"], "pct": round(v["correct"] / v["total"] * 100)}
            for k, v in by_cat.items()
        },
    }


def run_throughput_test() -> dict:
    """Quick throughput measurement — 5 requests, measure tok/s."""
    tok_rates = []
    for _ in range(5):
        t0 = time.monotonic()
        try:
            with httpx.Client(timeout=60) as c:
                r = c.post(
                    "http://localhost:8080/v1/chat/completions",
                    json={
                        "model": "bench",
                        "messages": [{"role": "user", "content": "Classify: groceries $47.99"}],
                        "max_tokens": 10,
                    },
                )
                d = r.json()
            elapsed = (time.monotonic() - t0) * 1000
            toks = d.get("usage", {}).get("completion_tokens", 0)
            tok_s = toks / (elapsed / 1000) if elapsed > 0 else 0
            tok_rates.append(tok_s)
        except:
            tok_rates.append(0)

    return {
        "avg_tok_s": round(sum(tok_rates) / len(tok_rates), 1),
        "max_tok_s": round(max(tok_rates), 1),
    }


def main():
    # Set up port-forward
    print("Setting up port-forward...", file=sys.stderr)
    subprocess.Popen(
        f"oc port-forward -n {NAMESPACE} svc/{SERVICE} 8080:{PORT}",
        shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(3)

    all_results = {}

    for name, gguf_path, max_tokens, threads, mem_limit, is_thinking in MODELS:
        # Deploy model
        if not swap_model(name, gguf_path, threads, mem_limit, is_thinking):
            all_results[name] = {"error": "failed to deploy"}
            continue

        # Reconnect port-forward after deployment change
        subprocess.run("pkill -f 'port-forward.*bench-model'", shell=True, capture_output=True)
        time.sleep(2)
        subprocess.Popen(
            f"oc port-forward -n {NAMESPACE} svc/{SERVICE} 8080:{PORT}",
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(3)

        # Wait for model ready
        if not wait_for_model_ready():
            print(f"  Model not responding — skipping", file=sys.stderr)
            all_results[name] = {"error": "not responding"}
            continue

        # Run quality test
        print(f"\n  Running quality test ({max_tokens} max_tokens)...", file=sys.stderr)
        quality = run_quality_test(max_tokens)

        # Run throughput test
        print(f"\n  Running throughput test...", file=sys.stderr)
        throughput = run_throughput_test()

        result = {**quality, **throughput, "gguf_path": gguf_path}
        all_results[name] = result

        print(f"\n  RESULT: {quality['overall_pct']:.0f}% quality, {throughput['avg_tok_s']} tok/s, {quality['avg_latency_ms']}ms avg", file=sys.stderr)

    # Final summary
    print(f"\n{'='*80}", file=sys.stderr)
    print("MODEL SHOOTOUT — FINAL RESULTS", file=sys.stderr)
    print(f"{'='*80}", file=sys.stderr)

    cats = sorted(set(t["cat"] for t in TESTS))
    header = f"{'Model':20s} {'Quality':>8s} {'tok/s':>7s} {'Avg ms':>8s} {'p95 ms':>8s}"
    for c in cats:
        header += f" {c:>10s}"
    print(header, file=sys.stderr)
    print("-" * (55 + 11 * len(cats)), file=sys.stderr)

    for name, _, _, _, _, _ in MODELS:
        r = all_results.get(name, {})
        if "error" in r:
            print(f"  {name:20s} {'ERROR':>8s} — {r['error']}", file=sys.stderr)
            continue
        line = f"  {name:20s} {r['overall_pct']:7.0f}% {r['avg_tok_s']:7.1f} {r['avg_latency_ms']:8d} {r['p95_latency_ms']:8d}"
        for c in cats:
            cd = r["by_category"].get(c, {"pct": 0})
            line += f" {cd['pct']:9.0f}%"
        print(line, file=sys.stderr)

    # Save
    out = Path("benchmarks/results/model-shootout-20260803.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump({
            "type": "model_shootout",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "models_tested": len([r for r in all_results.values() if "error" not in r]),
            "results": all_results,
        }, f, indent=2)
    print(f"\nResults: {out}", file=sys.stderr)

    # Cleanup
    subprocess.run("pkill -f 'port-forward.*bench-model'", shell=True, capture_output=True)


if __name__ == "__main__":
    main()

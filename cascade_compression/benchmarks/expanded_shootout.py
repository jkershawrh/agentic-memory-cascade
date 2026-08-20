"""Expanded Model Shootout — all task types, all working models.

Tests classification, extraction, reasoning, compliance, document classification,
intent detection, anomaly detection, and temporal reasoning.

Models tested: all that scored >0% in the initial shootout, plus retesting
the Western models without the --reasoning-format bug.
"""

import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import httpx

NAMESPACE = "intel-inference"
DEPLOY = "bench-model"
PORT = 8080

# Models that work — tested properly without the reasoning-format bug
MODELS = [
    # (name, gguf_path, is_thinking, threads, mem)
    ("phi4-mini", "/models/gguf/phi4-mini/microsoft_Phi-4-mini-instruct-Q4_K_M.gguf", False, 8, "8Gi"),
    ("gemma3-4b", "/models/gguf/gemma3-4b/google_gemma-3-4b-it-Q4_K_M.gguf", False, 8, "8Gi"),
    ("mistral-7b", "/models/gguf/mistral-7b/Mistral-7B-Instruct-v0.3-Q4_K_M.gguf", False, 16, "16Gi"),
    ("qwen3-0.6b", "/models/gguf/qwen3-0.6b/Qwen_Qwen3-0.6B-Q4_K_M.gguf", True, 4, "4Gi"),
    ("qwen3-1.7b", "/models/gguf/qwen3-1.7b/Qwen_Qwen3-1.7B-Q4_K_M.gguf", True, 4, "4Gi"),
    ("qwen3-4b", "/models/gguf/qwen3-4b/Qwen_Qwen3-4B-Q4_K_M.gguf", True, 8, "8Gi"),
    ("deepseek-r1-1.5b", "/models/gguf/deepseek-r1-1.5b/DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf", True, 4, "4Gi"),
    ("smollm3-3b", "/models/gguf/smollm3-3b/SmolLM3-Q4_K_M.gguf", True, 8, "8Gi"),
    ("llama32-1b", "/models/gguf/llama32-1b/Llama-3.2-1B-Instruct-Q4_K_M.gguf", False, 4, "4Gi"),
    ("gemma3-1b", "/models/gguf/gemma3-1b/google_gemma-3-1b-it-Q4_K_M.gguf", False, 4, "4Gi"),
    ("qwen25-1.5b", "/models/gguf/qwen25-1.5b/qwen2.5-1.5b-instruct-q4_k_m.gguf", False, 4, "4Gi"),
]

EXPANDED_TESTS = [
    # === ORIGINAL TESTS ===
    {"prompt": "Is this suspicious or legitimate? Wire transfer $95K to offshore at 3am from dormant account.", "expected": "suspicious", "cat": "fraud"},
    {"prompt": "Is this suspicious or legitimate? Customer bought groceries $47.99 at usual store.", "expected": "legitimate", "cat": "fraud"},
    {"prompt": "Is this suspicious or legitimate? Three ATM withdrawals $9,999 in one hour different cities.", "expected": "suspicious", "cat": "fraud"},
    {"prompt": "Severity: critical, high, medium, or low? Server disk at 98%, alerts firing.", "expected": "critical", "cat": "triage"},
    {"prompt": "Severity: critical, high, medium, or low? Minor CSS glitch on admin page.", "expected": "low", "cat": "triage"},
    {"prompt": "Severity: critical, high, medium, or low? Database primary down, failover activated.", "expected": "critical", "cat": "triage"},
    {"prompt": "Sentiment: positive, negative, or mixed? Product damaged and support unhelpful.", "expected": "negative", "cat": "sentiment"},
    {"prompt": "Sentiment: positive, negative, or mixed? Best purchase ever, exceeded expectations.", "expected": "positive", "cat": "sentiment"},
    {"prompt": "Sentiment: positive, negative, or mixed? Product damaged but support shipped replacement same day.", "expected": "mixed", "cat": "sentiment"},
    {"prompt": "Route to: network, billing, or support? Dropped calls downtown peak hours.", "expected": "network", "cat": "routing"},
    {"prompt": "Route to: network, billing, or support? Customer disputes charge on invoice.", "expected": "billing", "cat": "routing"},
    {"prompt": "Route to: network, billing, or support? Cannot log into account.", "expected": "support", "cat": "routing"},

    # === COMPLIANCE ===
    {"prompt": "Screen result: clear, match, or potential_match? Entity 'Petrov Trading LLC' registered in Cyprus, sending $340K to Dubai.", "expected": "match", "cat": "compliance"},
    {"prompt": "Screen result: clear, match, or potential_match? Entity 'Johnson & Johnson' NYSE listed, routine quarterly dividend.", "expected": "clear", "cat": "compliance"},
    {"prompt": "Screen result: clear, match, or potential_match? Entity 'Al-Rashid Imports' partial name match with sanctions list, different country.", "expected": "potential", "cat": "compliance"},

    # === DOCUMENT CLASSIFICATION ===
    {"prompt": "Document type: invoice, contract, application, or report? Contains line items, quantities, unit prices, tax, and total due.", "expected": "invoice", "cat": "doc_class"},
    {"prompt": "Document type: invoice, contract, application, or report? Contains terms and conditions, signatures from two parties, effective date.", "expected": "contract", "cat": "doc_class"},
    {"prompt": "Document type: invoice, contract, application, or report? Contains applicant name, SSN, employment history, requested amount.", "expected": "application", "cat": "doc_class"},

    # === INTENT DETECTION ===
    {"prompt": "Intent: question, command, complaint, or feedback? 'Why was I charged twice for the same order?'", "expected": "complaint", "cat": "intent"},
    {"prompt": "Intent: question, command, complaint, or feedback? 'Cancel my subscription immediately.'", "expected": "command", "cat": "intent"},
    {"prompt": "Intent: question, command, complaint, or feedback? 'What are your business hours?'", "expected": "question", "cat": "intent"},
    {"prompt": "Intent: question, command, complaint, or feedback? 'The new checkout flow is much faster, great improvement.'", "expected": "feedback", "cat": "intent"},

    # === ANOMALY DETECTION ===
    {"prompt": "Normal or anomalous? CPU usage at 45% during business hours on a weekday.", "expected": "normal", "cat": "anomaly"},
    {"prompt": "Normal or anomalous? CPU spike to 99% at 3am on Sunday with no scheduled jobs.", "expected": "anomalous", "cat": "anomaly"},
    {"prompt": "Normal or anomalous? Network traffic 10x normal volume from a single internal IP.", "expected": "anomalous", "cat": "anomaly"},

    # === PRIORITY RANKING ===
    {"prompt": "Which is more urgent? A: slow page load on marketing site. B: payment processing failing for all customers. Answer A or B.", "expected": "b", "cat": "priority"},
    {"prompt": "Which is more urgent? A: CEO cannot access email. B: test environment database backup failed. Answer A or B.", "expected": "a", "cat": "priority"},

    # === BINARY DECISIONS ===
    {"prompt": "Does this need immediate human review? Patient allergy alert triggered during medication order. Yes or no.", "expected": "yes", "cat": "binary"},
    {"prompt": "Does this need immediate human review? Routine log rotation completed. Yes or no.", "expected": "no", "cat": "binary"},
    {"prompt": "Is this PII? 'John Smith, SSN 123-45-6789, DOB 01/15/1980'. Yes or no.", "expected": "yes", "cat": "binary"},
    {"prompt": "Is this PII? 'The quarterly report shows 12% revenue growth'. Yes or no.", "expected": "no", "cat": "binary"},
]

SYSTEM_PROMPT = "Answer with exactly one word. No explanation."


def swap_model(name, gguf_path, is_thinking, threads, mem):
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"DEPLOYING: {name} ({'thinking' if is_thinking else 'standard'})", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    args = ["--model", gguf_path, "--host", "0.0.0.0", "--port", "8080",
            "--ctx-size", "2048", "--threads", str(threads), "--alias", "bench"]
    if is_thinking:
        args.extend(["--reasoning-format", "none"])

    patch = json.dumps({"spec": {"template": {"spec": {"containers": [{"name": "llama", "args": args,
        "resources": {"requests": {"cpu": str(threads//2), "memory": mem}, "limits": {"cpu": str(threads), "memory": mem}}}]}}}})

    subprocess.run(f"oc patch deploy/{DEPLOY} -n {NAMESPACE} -p '{patch}' --type=merge", shell=True, capture_output=True, timeout=15)

    for i in range(12):
        time.sleep(10)
        r = subprocess.run(f"oc get pods -n {NAMESPACE} -l app={DEPLOY} --no-headers", shell=True, capture_output=True, text=True)
        if "1/1" in r.stdout and "Running" in r.stdout:
            print(f"  Loaded ({(i+1)*10}s)", file=sys.stderr)
            return True
    return False


def reconnect_portforward():
    subprocess.run("pkill -f 'port-forward.*bench-model'", shell=True, capture_output=True)
    time.sleep(2)
    subprocess.Popen(f"oc port-forward -n {NAMESPACE} svc/{DEPLOY} 8080:{PORT}",
                     shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5)


def wait_ready():
    for i in range(24):
        try:
            with httpx.Client(timeout=10) as c:
                r = c.post("http://localhost:8080/v1/chat/completions",
                           json={"model": "bench", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 3})
                if r.status_code == 200:
                    return True
        except:
            pass
        time.sleep(5)
    return False


def run_tests(max_tokens):
    by_cat = defaultdict(lambda: {"correct": 0, "total": 0})
    total_correct = 0
    latencies = []

    for test in EXPANDED_TESTS:
        t0 = time.monotonic()
        try:
            with httpx.Client(timeout=120) as c:
                r = c.post("http://localhost:8080/v1/chat/completions", json={
                    "model": "bench",
                    "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": test["prompt"]}],
                    "max_tokens": max_tokens,
                })
                d = r.json()
            elapsed = (time.monotonic() - t0) * 1000
            raw = d["choices"][0]["message"]["content"].strip()
            clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            if not clean and "<think>" in raw:
                clean = "(thinking)"
            answer = clean.lower()[:40]
        except:
            elapsed = (time.monotonic() - t0) * 1000
            answer = "ERR"

        latencies.append(elapsed)
        correct = test["expected"] in answer
        total_correct += 1 if correct else 0
        by_cat[test["cat"]]["total"] += 1
        by_cat[test["cat"]]["correct"] += 1 if correct else 0
        mark = "PASS" if correct else "FAIL"
        print(f"  [{mark}] {test['cat']:10s} exp={test['expected']:15s} got=\"{clean[:25] if clean else answer[:25]}\" ({elapsed:.0f}ms)", file=sys.stderr)

    return {
        "overall_pct": round(total_correct / len(EXPANDED_TESTS) * 100, 1),
        "correct": total_correct, "total": len(EXPANDED_TESTS),
        "avg_ms": round(sum(latencies) / len(latencies)),
        "p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95)]),
        "by_category": {k: {"correct": v["correct"], "total": v["total"],
                            "pct": round(v["correct"] / v["total"] * 100)} for k, v in by_cat.items()},
    }


def main():
    reconnect_portforward()
    all_results = {}

    for name, path, thinking, threads, mem in MODELS:
        if not swap_model(name, path, thinking, threads, mem):
            all_results[name] = {"error": "deploy failed"}
            continue
        reconnect_portforward()
        if not wait_ready():
            all_results[name] = {"error": "not responding"}
            continue

        max_tokens = 500 if thinking else 20
        print(f"\n  Testing ({max_tokens} max_tokens)...", file=sys.stderr)
        result = run_tests(max_tokens)
        all_results[name] = result
        print(f"\n  RESULT: {result['overall_pct']:.0f}% quality, {result['avg_ms']}ms avg", file=sys.stderr)

    # Summary
    print(f"\n{'='*80}", file=sys.stderr)
    print("EXPANDED SHOOTOUT — ALL TASK TYPES", file=sys.stderr)
    print(f"{'='*80}", file=sys.stderr)
    cats = sorted(set(t["cat"] for t in EXPANDED_TESTS))
    header = f"{'Model':20s} {'Overall':>8s} {'ms':>6s}"
    for c in cats:
        header += f" {c[:8]:>9s}"
    print(header, file=sys.stderr)
    print("-" * (36 + 10 * len(cats)), file=sys.stderr)
    for name, _, _, _, _ in MODELS:
        r = all_results.get(name, {})
        if "error" in r:
            print(f"  {name:20s} ERROR", file=sys.stderr)
            continue
        line = f"  {name:20s} {r['overall_pct']:7.0f}% {r['avg_ms']:5d}"
        for c in cats:
            cd = r["by_category"].get(c, {"pct": 0})
            line += f" {cd['pct']:8.0f}%"
        print(line, file=sys.stderr)

    out = Path("benchmarks/results/expanded-shootout-20260804.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump({"type": "expanded_shootout", "timestamp": datetime.now(timezone.utc).isoformat(),
                    "tests": len(EXPANDED_TESTS), "categories": cats,
                    "results": all_results}, f, indent=2)
    print(f"\nResults: {out}", file=sys.stderr)
    subprocess.run("pkill -f 'port-forward.*bench'", shell=True, capture_output=True)


if __name__ == "__main__":
    main()

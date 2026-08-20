"""Full Inference Type Shootout — all models, all inference types.

Tests classification + extraction + JSON + summarization +
generation + reasoning + code gen across all working models.

v2: Fixed model swapping — patches the FULL container spec and verifies
the correct model is loaded by checking pod args before testing.
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

# Full container spec (everything except args + resources, which vary per model)
CONTAINER_BASE = {
    "name": "llama",
    "image": "ghcr.io/ggml-org/llama.cpp:server",
    "imagePullPolicy": "IfNotPresent",
    "ports": [{"containerPort": 8080, "protocol": "TCP"}],
    "volumeMounts": [{"mountPath": "/models", "name": "models"}],
}

MODELS = [
    ("gemma3-4b", "/models/gguf/gemma3-4b/google_gemma-3-4b-it-Q4_K_M.gguf", False, 8, "8Gi"),
    ("phi4-mini", "/models/gguf/phi4-mini/microsoft_Phi-4-mini-instruct-Q4_K_M.gguf", False, 8, "8Gi"),
    ("mistral-7b", "/models/gguf/mistral-7b/Mistral-7B-Instruct-v0.3-Q4_K_M.gguf", False, 16, "16Gi"),
    ("qwen3-4b", "/models/gguf/qwen3-4b/Qwen_Qwen3-4B-Q4_K_M.gguf", True, 8, "8Gi"),
    ("smollm3-3b", "/models/gguf/smollm3-3b/SmolLM3-Q4_K_M.gguf", True, 8, "8Gi"),
    ("qwen3-1.7b", "/models/gguf/qwen3-1.7b/Qwen_Qwen3-1.7B-Q4_K_M.gguf", True, 4, "4Gi"),
    ("deepseek-r1-1.5b", "/models/gguf/deepseek-r1-1.5b/DeepSeek-R1-Distill-Qwen-1.5B-Q4_K_M.gguf", True, 4, "4Gi"),
    ("qwen3-0.6b", "/models/gguf/qwen3-0.6b/Qwen_Qwen3-0.6B-Q4_K_M.gguf", True, 4, "4Gi"),
    ("llama32-1b", "/models/gguf/llama32-1b/Llama-3.2-1B-Instruct-Q4_K_M.gguf", False, 4, "4Gi"),
    ("gemma3-1b", "/models/gguf/gemma3-1b/google_gemma-3-1b-it-Q4_K_M.gguf", False, 4, "4Gi"),
    ("qwen25-1.5b", "/models/gguf/qwen25-1.5b/qwen2.5-1.5b-instruct-q4_k_m.gguf", False, 4, "4Gi"),
]

TESTS = [
    # CLASSIFICATION (5 tests covering key types)
    {"prompt": "Is this suspicious or legitimate? Wire transfer $95K offshore 3am dormant account.", "checks": [("suspicious", True)], "cat": "classify", "max_t": 10, "sys": "Answer with one word."},
    {"prompt": "Severity: critical/high/medium/low? Disk 98%, alerts firing.", "checks": [("critical", True)], "cat": "classify", "max_t": 10, "sys": "Answer with one word."},
    {"prompt": "Route to network/billing/support? Dropped calls downtown.", "checks": [("network", True)], "cat": "classify", "max_t": 10, "sys": "Answer with one word."},
    {"prompt": "Sentiment: positive/negative/mixed? Product arrived broken and late, but support gave full refund and free replacement.", "checks": [("mixed", True)], "cat": "classify", "max_t": 10, "sys": "Answer with one word."},
    {"prompt": "Document type: invoice/contract/application/report? Contains line items, quantities, prices, tax, total due.", "checks": [("invoice", True)], "cat": "classify", "max_t": 10, "sys": "Answer with one word."},

    # ENTITY EXTRACTION (4 tests)
    {"prompt": "Extract as JSON: {person, amount, bank}: John Smith applied for $250K mortgage at First National Bank.", "checks": [("john", True), ("250", True), ("first national", True)], "cat": "extraction", "max_t": 100, "sys": "Respond with only JSON."},
    {"prompt": "Extract as JSON: {policy_number, claimant, damage_type}: Policy #HO-2026-44891, Jane Wilson, water damage burst pipe.", "checks": [("44891", True), ("jane", True), ("water", True)], "cat": "extraction", "max_t": 100, "sys": "Respond with only JSON."},
    {"prompt": "Extract as JSON: {medication, dosage, condition}: Patient on metformin 500mg BID for Type 2 diabetes.", "checks": [("metformin", True), ("500", True), ("diabetes", True)], "cat": "extraction", "max_t": 100, "sys": "Respond with only JSON."},
    {"prompt": "Extract as JSON: {sender, receiver, amount}: Wire $95K from Acme Corp to BVI Holdings.", "checks": [("acme", True), ("bvi", True), ("95", True)], "cat": "extraction", "max_t": 100, "sys": "Respond with only JSON."},

    # STRUCTURED JSON (3 tests)
    {"prompt": "Respond with ONLY JSON, no markdown fences: {\"risk_score\": 0-100, \"classification\": \"legitimate\" or \"suspicious\"}\nWire $95K offshore 3am dormant account.", "checks": [("{", True), ("suspicious", True)], "cat": "json", "max_t": 100, "sys": "Respond with only valid JSON. No markdown code fences."},
    {"prompt": "Respond with ONLY JSON, no markdown fences: {\"type\": \"complaint\" or \"request\" or \"inquiry\", \"severity\": \"critical\" or \"high\" or \"medium\" or \"low\"}\nDropped calls downtown peak hours, affecting 15 business lines.", "checks": [("{", True)], "cat": "json", "max_t": 100, "sys": "Respond with only valid JSON. No markdown code fences."},
    {"prompt": "Respond with ONLY JSON, no markdown fences: {\"sentiment\": \"positive\" or \"negative\" or \"mixed\", \"score\": -1.0 to 1.0}\nProduct arrived broken AND two weeks late, BUT support gave full refund AND free upgraded replacement.", "checks": [("{", True), ("mixed", True)], "cat": "json", "max_t": 100, "sys": "Respond with only valid JSON. No markdown code fences."},

    # SUMMARIZATION (3 tests)
    {"prompt": "Summarize in 2 sentences: 67yo male, Type 2 DM, HTN, CKD3. A1C improved 8.2 to 7.1 on metformin+empagliflozin. BP 132/78 on lisinopril. eGFR stable 48. Continue regimen, recheck 3 months.", "checks": [("a1c", True)], "cat": "summarize", "max_t": 150, "sys": "Summarize concisely."},
    {"prompt": "Summarize in 1 sentence for executive: 3 pods OOMKilled in fraud-scoring, node memory 95%, HPA maxed, requests doubled from end-of-month batch.", "checks": [("memory", True)], "cat": "summarize", "max_t": 100, "sys": "Summarize concisely."},
    {"prompt": "Summarize for CRM in 1 sentence: Customer called 3 times about billing error. First agent promised callback, never happened. Second said fixed but charge reappeared. Threatening to cancel 5-year account.", "checks": [("billing", True)], "cat": "summarize", "max_t": 100, "sys": "Summarize concisely."},

    # REASONING (2 tests)
    {"prompt": "Root cause in 2 sentences: 3 pods OOMKilled, node memory 95%, HPA maxed 10 replicas, requests doubled last hour.", "checks": [("memory", True)], "cat": "reasoning", "max_t": 200, "sys": "Analyze concisely."},
    {"prompt": "Approve or deny with 1 sentence reason: Applicant 780 credit score, $145K income, $250K mortgage, 8yr employment.", "checks": [("approv", True)], "cat": "reasoning", "max_t": 100, "sys": "Answer concisely."},

    # TEXT GENERATION (2 tests)
    {"prompt": "Write a 3-step remediation plan for: Server disk at 98%, alerts firing.", "checks": [("1", True), ("disk", True)], "cat": "generation", "max_t": 200, "sys": "Be concise and actionable."},
    {"prompt": "Draft a brief customer apology for: Product arrived damaged, support was unhelpful.", "checks": [("apolog", True)], "cat": "generation", "max_t": 150, "sys": "Be professional and brief."},

    # CODE GENERATION (2 tests)
    {"prompt": "Generate the kubectl command to restart all pods in namespace fraud-scoring.", "checks": [("kubectl", True), ("fraud-scoring", True)], "cat": "code", "max_t": 50, "sys": "Output only the command."},
    {"prompt": "Generate the oc command to scale deployment web-frontend to 5 replicas in namespace production.", "checks": [("oc", True), ("5", True)], "cat": "code", "max_t": 50, "sys": "Output only the command."},
]


def swap_model(name, path, thinking, threads, mem):
    """Swap the model by applying the FULL container spec via JSON patch."""
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"DEPLOYING: {name}", file=sys.stderr)

    # Step 1: Scale to 0 to kill all pods cleanly
    subprocess.run(f"oc scale deploy/{DEPLOY} -n {NAMESPACE} --replicas=0",
                   shell=True, capture_output=True, timeout=15)

    # Wait for pods to terminate
    for _ in range(12):
        r = subprocess.run(f"oc get pods -n {NAMESPACE} -l app={DEPLOY} --no-headers 2>/dev/null | wc -l",
                           shell=True, capture_output=True, text=True, timeout=10)
        if r.stdout.strip() == "0":
            break
        time.sleep(5)
    else:
        print(f"  WARN: pods didn't terminate, forcing", file=sys.stderr)
        subprocess.run(f"oc delete pods -n {NAMESPACE} -l app={DEPLOY} --force --grace-period=0",
                       shell=True, capture_output=True, timeout=15)
        time.sleep(5)

    # Step 2: Build complete container spec
    args = ["--model", path, "--host", "0.0.0.0", "--port", "8080",
            "--ctx-size", "2048", "--threads", str(threads), "--alias", "bench"]
    if thinking:
        args.extend(["--reasoning-format", "none"])

    container = {
        **CONTAINER_BASE,
        "args": args,
        "resources": {
            "requests": {"cpu": str(threads // 2), "memory": mem},
            "limits": {"cpu": str(threads), "memory": mem},
        },
    }

    # Step 3: Use JSON Patch (RFC 6902) to REPLACE the containers array
    # This is unambiguous — replace, not merge
    json_patch = json.dumps([
        {"op": "replace", "path": "/spec/template/spec/containers", "value": [container]},
        {"op": "replace", "path": "/spec/template/metadata/annotations",
         "value": {"restartedAt": datetime.now(timezone.utc).isoformat()}},
    ])

    r = subprocess.run(
        f"oc patch deploy/{DEPLOY} -n {NAMESPACE} --type=json -p '{json_patch}'",
        shell=True, capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        print(f"  PATCH FAILED: {r.stderr.strip()}", file=sys.stderr)
        return False

    # Step 4: Scale back to 1
    subprocess.run(f"oc scale deploy/{DEPLOY} -n {NAMESPACE} --replicas=1",
                   shell=True, capture_output=True, timeout=15)

    # Step 5: Wait for new pod with correct model (up to 10 min for large models)
    for i in range(60):
        time.sleep(10)
        r = subprocess.run(f"oc get pods -n {NAMESPACE} -l app={DEPLOY} --no-headers",
                           shell=True, capture_output=True, text=True)
        if "1/1" not in r.stdout or "Running" not in r.stdout:
            continue

        # VERIFY: check the pod is running the correct model
        pod_name = r.stdout.split()[0]
        args_check = subprocess.run(
            f"oc get pod {pod_name} -n {NAMESPACE} -o jsonpath='{{.spec.containers[0].args}}'",
            shell=True, capture_output=True, text=True, timeout=10,
        )
        if path in args_check.stdout:
            # Double-check: verify llama-cpp logs show model loaded
            logs = subprocess.run(
                f"oc logs {pod_name} -n {NAMESPACE} --tail=50",
                shell=True, capture_output=True, text=True, timeout=10,
            )
            if "model loaded" in logs.stdout.lower() or "listening" in logs.stdout.lower():
                print(f"  Loaded {name} ({(i+1)*10}s) — verified in pod args", file=sys.stderr)
                return True
            else:
                print(f"  Pod running but model not ready yet ({(i+1)*10}s)", file=sys.stderr)
        else:
            print(f"  WRONG MODEL in pod! Expected {path}, got {args_check.stdout[:60]}", file=sys.stderr)

    print(f"  TIMEOUT", file=sys.stderr)
    return False


def reconnect():
    subprocess.run("pkill -f 'port-forward.*bench'", shell=True, capture_output=True)
    time.sleep(2)
    subprocess.Popen(f"oc port-forward -n {NAMESPACE} svc/{DEPLOY} 8080:{PORT}",
                     shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5)


def wait_ready():
    for _ in range(24):
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


def run_tests(thinking):
    by_cat = defaultdict(lambda: {"pass": 0, "total": 0})
    total_pass = 0
    latencies = []

    for test in TESTS:
        max_t = test["max_t"]
        if thinking and max_t < 100:
            max_t = 500

        t0 = time.monotonic()
        try:
            with httpx.Client(timeout=120) as c:
                r = c.post("http://localhost:8080/v1/chat/completions", json={
                    "model": "bench",
                    "messages": [{"role": "system", "content": test["sys"]}, {"role": "user", "content": test["prompt"]}],
                    "max_tokens": max_t,
                })
                d = r.json()
            elapsed = (time.monotonic() - t0) * 1000
            raw = d["choices"][0]["message"]["content"].strip()
            clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            if not clean and "<think>" in raw:
                clean = "(thinking)"
            # Strip markdown code fences — models often wrap JSON in ```json ... ```
            clean = re.sub(r"^```(?:json)?\s*\n?", "", clean, flags=re.MULTILINE)
            clean = re.sub(r"\n?```\s*$", "", clean, flags=re.MULTILINE)
            clean = clean.strip()
            answer = clean.lower()
        except:
            elapsed = (time.monotonic() - t0) * 1000
            answer = ""
            clean = "ERR"

        latencies.append(elapsed)
        passed = all((kw in answer) == present for kw, present in test["checks"])
        total_pass += 1 if passed else 0
        by_cat[test["cat"]]["total"] += 1
        by_cat[test["cat"]]["pass"] += 1 if passed else 0
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {test['cat']:12s} ({elapsed:.0f}ms) {clean[:55]}", file=sys.stderr)

    return {
        "overall_pct": round(total_pass / len(TESTS) * 100, 1),
        "correct": total_pass, "total": len(TESTS),
        "avg_ms": round(sum(latencies) / len(latencies)),
        "p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95)]),
        "by_category": {k: {"pass": v["pass"], "total": v["total"],
                            "pct": round(v["pass"] / v["total"] * 100)} for k, v in by_cat.items()},
    }


def main():
    reconnect()
    results = {}

    for name, path, thinking, threads, mem in MODELS:
        if not swap_model(name, path, thinking, threads, mem):
            results[name] = {"error": "deploy failed"}
            continue
        reconnect()
        if not wait_ready():
            results[name] = {"error": "not responding"}
            continue

        result = run_tests(thinking)
        results[name] = result
        print(f"\n  RESULT: {result['overall_pct']:.0f}% ({result['avg_ms']}ms)", file=sys.stderr)

    # Summary
    print(f"\n{'='*90}", file=sys.stderr)
    print("FULL INFERENCE SHOOTOUT v2 — ALL MODELS x ALL TYPES", file=sys.stderr)
    print(f"{'='*90}", file=sys.stderr)
    cats = sorted(set(t["cat"] for t in TESTS))
    header = f"{'Model':20s} {'All':>5s} {'ms':>6s}"
    for c in cats:
        header += f" {c[:7]:>8s}"
    print(header, file=sys.stderr)
    print("-" * (33 + 9 * len(cats)), file=sys.stderr)
    for name, _, _, _, _ in MODELS:
        r = results.get(name, {})
        if "error" in r:
            print(f"  {name:20s} ERR — {r['error']}", file=sys.stderr)
            continue
        line = f"  {name:20s} {r['overall_pct']:4.0f}% {r['avg_ms']:5d}"
        for c in cats:
            cd = r["by_category"].get(c, {"pct": 0})
            line += f" {cd['pct']:7.0f}%"
        print(line, file=sys.stderr)

    out = Path("benchmarks/results/full-inference-shootout-v2.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump({"type": "full_inference_shootout_v2",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "tests": len(TESTS), "categories": cats,
                    "results": results}, f, indent=2)
    print(f"\nResults: {out}", file=sys.stderr)
    subprocess.run("pkill -f 'port-forward.*bench'", shell=True, capture_output=True)


if __name__ == "__main__":
    main()

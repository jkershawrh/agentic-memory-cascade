"""Live routing test — send mixed-lane traffic through the model ladder.

Tests the full routing concept:
  - Classification → gemma3-4b (svc: llama-gemma3-4b)
  - Extraction/Generation/Reasoning/Code/JSON → phi4-mini (svc: llama-phi4-mini)
  - Fallback → llama32-1b (svc: llama-llama32-1b)

Runs N requests per lane concurrently, measures latency, accuracy, throughput.
"""

import json
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx

NAMESPACE = "intel-inference"

LANE_SERVICES = {
    "classification": "llama-gemma3-4b",
    "extraction": "llama-phi4-mini",
    "generation": "llama-phi4-mini",
    "reasoning": "llama-phi4-mini",
    "code": "llama-phi4-mini",
    "json": "llama-phi4-mini",
    "fallback": "llama-llama32-1b",
}

LANE_ALIASES = {
    "classification": "gemma3-4b",
    "extraction": "phi4-mini",
    "generation": "phi4-mini",
    "reasoning": "phi4-mini",
    "code": "phi4-mini",
    "json": "phi4-mini",
    "fallback": "llama32-1b",
}

TESTS = [
    # Classification (→ gemma3-4b)
    {"lane": "classification", "prompt": "Is this suspicious or legitimate? Wire transfer $95K offshore 3am dormant account.", "checks": [("suspicious", True)], "sys": "Answer with one word.", "max_t": 10},
    {"lane": "classification", "prompt": "Severity: critical/high/medium/low? Disk 98%, alerts firing.", "checks": [("critical", True)], "sys": "Answer with one word.", "max_t": 10},
    {"lane": "classification", "prompt": "Route to network/billing/support? Dropped calls downtown.", "checks": [("network", True)], "sys": "Answer with one word.", "max_t": 10},
    {"lane": "classification", "prompt": "Document type: invoice/contract/application/report? Contains line items, quantities, prices, tax, total due.", "checks": [("invoice", True)], "sys": "Answer with one word.", "max_t": 10},

    # Extraction (→ phi4-mini)
    {"lane": "extraction", "prompt": "Extract as JSON: {person, amount, bank}: John Smith applied for $250K mortgage at First National Bank.", "checks": [("john", True), ("250", True)], "sys": "Respond with only JSON.", "max_t": 100},
    {"lane": "extraction", "prompt": "Extract as JSON: {medication, dosage, condition}: Patient on metformin 500mg BID for Type 2 diabetes.", "checks": [("metformin", True), ("500", True)], "sys": "Respond with only JSON.", "max_t": 100},

    # JSON output (→ phi4-mini)
    {"lane": "json", "prompt": "Respond with ONLY JSON, no markdown fences: {\"risk_score\": 0-100, \"classification\": \"legitimate\" or \"suspicious\"}\nWire $95K offshore 3am dormant account.", "checks": [("{", True), ("suspicious", True)], "sys": "Respond with only valid JSON. No markdown code fences.", "max_t": 100},
    {"lane": "json", "prompt": "Respond with ONLY JSON, no markdown fences: {\"sentiment\": \"positive\" or \"negative\" or \"mixed\", \"score\": -1.0 to 1.0}\nProduct arrived broken AND late, BUT support gave full refund AND free upgraded replacement.", "checks": [("{", True), ("mixed", True)], "sys": "Respond with only valid JSON. No markdown code fences.", "max_t": 100},

    # Summarization / Generation (→ phi4-mini)
    {"lane": "generation", "prompt": "Summarize in 2 sentences: 67yo male, Type 2 DM, HTN, CKD3. A1C improved 8.2 to 7.1 on metformin+empagliflozin. BP 132/78 on lisinopril. eGFR stable 48. Continue regimen, recheck 3 months.", "checks": [("a1c", True)], "sys": "Summarize concisely.", "max_t": 150},
    {"lane": "generation", "prompt": "Write a 3-step remediation plan for: Server disk at 98%, alerts firing.", "checks": [("1", True), ("disk", True)], "sys": "Be concise and actionable.", "max_t": 200},

    # Reasoning (→ phi4-mini)
    {"lane": "reasoning", "prompt": "Root cause in 2 sentences: 3 pods OOMKilled, node memory 95%, HPA maxed 10 replicas, requests doubled last hour.", "checks": [("memory", True)], "sys": "Analyze concisely.", "max_t": 200},
    {"lane": "reasoning", "prompt": "Approve or deny with 1 sentence reason: Applicant 780 credit score, $145K income, $85K mortgage, 8yr employment, no other debts, $50K savings.", "checks": [("approv", True)], "sys": "Answer concisely.", "max_t": 100},

    # Code gen (→ phi4-mini)
    {"lane": "code", "prompt": "Generate the kubectl command to restart all pods in namespace fraud-scoring.", "checks": [("kubectl", True), ("fraud-scoring", True)], "sys": "Output only the command.", "max_t": 50},

    # Fallback test (→ llama32-1b)
    {"lane": "fallback", "prompt": "Is this suspicious or legitimate? Customer bought groceries $47.99 at usual store.", "checks": [("legitimate", True)], "sys": "Answer with one word.", "max_t": 10},
    {"lane": "fallback", "prompt": "Scheduled maintenance completed successfully on time. Severity?", "checks": [("low", True)], "sys": "Answer with one word.", "max_t": 10},
]

PORT_MAP = {}


def setup_port_forwards():
    subprocess.run("pkill -f 'port-forward.*intel-inference'", shell=True, capture_output=True)
    time.sleep(2)

    ports = {"llama-phi4-mini": 8081, "llama-gemma3-4b": 8082, "llama-llama32-1b": 8083}
    for svc, port in ports.items():
        subprocess.Popen(
            f"oc port-forward -n {NAMESPACE} svc/{svc} {port}:8080",
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        PORT_MAP[svc] = port
    time.sleep(5)


def run_one_test(test):
    import re
    lane = test["lane"]
    svc = LANE_SERVICES[lane]
    alias = LANE_ALIASES[lane]
    port = PORT_MAP[svc]

    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=60) as c:
            r = c.post(f"http://localhost:{port}/v1/chat/completions", json={
                "model": alias,
                "messages": [
                    {"role": "system", "content": test["sys"]},
                    {"role": "user", "content": test["prompt"]},
                ],
                "max_tokens": test["max_t"],
            })
            d = r.json()
        elapsed = (time.monotonic() - t0) * 1000
        raw = d["choices"][0]["message"]["content"].strip()
        clean = re.sub(r"```(?:json)?\s*\n?", "", raw, flags=re.MULTILINE)
        clean = re.sub(r"\n?```\s*$", "", clean, flags=re.MULTILINE).strip()
        answer = clean.lower()
    except Exception as e:
        elapsed = (time.monotonic() - t0) * 1000
        answer = ""
        clean = f"ERR: {e}"

    passed = all((kw in answer) == present for kw, present in test["checks"])
    return {
        "lane": lane,
        "svc": svc,
        "passed": passed,
        "elapsed_ms": round(elapsed),
        "answer": clean[:60],
    }


def main():
    print("Setting up port-forwards...", file=sys.stderr)
    setup_port_forwards()

    # Verify all services respond
    for svc, port in PORT_MAP.items():
        for _ in range(12):
            try:
                with httpx.Client(timeout=10) as c:
                    r = c.post(f"http://localhost:{port}/v1/chat/completions",
                               json={"model": "test", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 3})
                    if r.status_code == 200:
                        print(f"  {svc} ✓", file=sys.stderr)
                        break
            except:
                time.sleep(5)
        else:
            print(f"  {svc} NOT RESPONDING", file=sys.stderr)

    # Run all tests concurrently (simulates real mixed traffic)
    print(f"\n{'='*70}", file=sys.stderr)
    print(f"ROUTING TEST — {len(TESTS)} requests across {len(set(t['lane'] for t in TESTS))} lanes", file=sys.stderr)
    print(f"{'='*70}", file=sys.stderr)

    results = []
    t_start = time.monotonic()

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(run_one_test, t): t for t in TESTS}
        for f in as_completed(futures):
            r = f.result()
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"  [{mark}] {r['lane']:15s} → {r['svc']:20s} ({r['elapsed_ms']}ms) {r['answer'][:45]}", file=sys.stderr)
            results.append(r)

    wall_clock = (time.monotonic() - t_start) * 1000

    # Summary by lane
    by_lane = defaultdict(lambda: {"pass": 0, "total": 0, "latencies": []})
    for r in results:
        by_lane[r["lane"]]["total"] += 1
        by_lane[r["lane"]]["pass"] += 1 if r["passed"] else 0
        by_lane[r["lane"]]["latencies"].append(r["elapsed_ms"])

    print(f"\n{'='*70}", file=sys.stderr)
    print("ROUTING TEST RESULTS", file=sys.stderr)
    print(f"{'='*70}", file=sys.stderr)
    print(f"{'Lane':15s} {'Model':20s} {'Accuracy':>10s} {'Avg ms':>8s} {'P95 ms':>8s}", file=sys.stderr)
    print("-" * 65, file=sys.stderr)

    total_pass = 0
    total_tests = 0
    for lane in sorted(by_lane.keys()):
        d = by_lane[lane]
        svc = LANE_SERVICES[lane]
        pct = d["pass"] / d["total"] * 100
        avg = sum(d["latencies"]) / len(d["latencies"])
        p95 = sorted(d["latencies"])[int(len(d["latencies"]) * 0.95)]
        total_pass += d["pass"]
        total_tests += d["total"]
        print(f"  {lane:15s} {svc:20s} {pct:9.0f}% {avg:7.0f} {p95:7.0f}", file=sys.stderr)

    overall = total_pass / total_tests * 100
    avg_all = sum(r["elapsed_ms"] for r in results) / len(results)
    print(f"\n  OVERALL: {overall:.0f}% accuracy, {avg_all:.0f}ms avg, {wall_clock:.0f}ms wall clock ({len(TESTS)} requests)", file=sys.stderr)
    print(f"  Effective throughput: {len(TESTS) / (wall_clock / 1000):.1f} req/s", file=sys.stderr)

    # Save results
    out = Path("benchmarks/results/routing-test.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump({
            "type": "routing_test",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "models": {"classification": "gemma3-4b x2", "generation": "phi4-mini x3", "fallback": "llama32-1b x1"},
            "tests": len(TESTS),
            "overall_pct": round(overall, 1),
            "avg_ms": round(avg_all),
            "wall_clock_ms": round(wall_clock),
            "throughput_rps": round(len(TESTS) / (wall_clock / 1000), 2),
            "by_lane": {lane: {"pass": d["pass"], "total": d["total"],
                               "pct": round(d["pass"] / d["total"] * 100),
                               "avg_ms": round(sum(d["latencies"]) / len(d["latencies"])),
                               } for lane, d in by_lane.items()},
            "results": results,
        }, f, indent=2)
    print(f"\nResults: {out}", file=sys.stderr)

    subprocess.run("pkill -f 'port-forward.*intel-inference'", shell=True, capture_output=True)


if __name__ == "__main__":
    main()

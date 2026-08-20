"""Five-lane synthetic simulation.

Generates signals for each workload vertical, routes through the
bootstrapper → strategy router → lane router → real inference,
measures actual latency and throughput per lane.

Usage:
    LITELLM_API_BASE=http://localhost:4000 \
    LITELLM_API_KEY=local-oberon-key \
    python -m benchmarks.simulate_lanes --rounds 5
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

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "intel-inference-router"))

from cascade_compression import (
    StrategyRouter,
    WorkloadBootstrapper,
)
from cascade_compression.routing.corpora import (
    resolve_lane,
    resolve_lane_model,
)

API_BASE = os.environ.get("LITELLM_API_BASE", "http://localhost:4000")
API_KEY = os.environ.get("LITELLM_API_KEY", "")

VERTICALS = {
    "fsi": {
        "signals": [
            ("alert_critical", "Pod", "critical"),
            ("splunk_high_alert", "InferenceService", "high"),
            ("pod_crashloop", "Pod", "high"),
            ("event_backoff", "Pod", "medium"),
            ("kserve_not_ready", "InferenceService", "medium"),
        ],
        "tasks": {
            "classify_signal": "fraud-scoring",
            "correlate_findings": "dispute-classification",
            "summarize_finding": "loan-document-extraction",
        },
    },
    "retail": {
        "signals": [
            ("pod_crashloop", "Pod", "high"),
            ("route_unhealthy", "Route", "high"),
            ("event_backoff", "Pod", "medium"),
            ("alert_warning", "Pod", "low"),
            ("kafka_lag_high", "KafkaTopic", "medium"),
        ],
        "tasks": {
            "classify_signal": "product-categorization",
            "correlate_findings": "review-sentiment",
            "filter_noise": "demand-classification",
        },
    },
    "telecom": {
        "signals": [
            ("alert_critical", "Pod", "critical"),
            ("node_pressure", "Node", "critical"),
            ("pod_crashloop", "Pod", "high"),
            ("kafka_lag_high", "KafkaTopic", "medium"),
            ("event_backoff", "Pod", "medium"),
        ],
        "tasks": {
            "classify_signal": "ticket-routing",
            "correlate_findings": "network-anomaly",
            "root_cause_analysis": "churn-prediction",
        },
    },
    "insurance": {
        "signals": [
            ("pod_crashloop", "Pod", "high"),
            ("splunk_error_spike", "Pod", "high"),
            ("event_backoff", "Pod", "medium"),
            ("pvc_pending", "PersistentVolumeClaim", "medium"),
            ("alert_high", "Pod", "high"),
        ],
        "tasks": {
            "classify_signal": "claims-triage",
            "summarize_finding": "policy-extraction",
            "root_cause_analysis": "underwriting-risk",
        },
    },
    "healthcare": {
        "signals": [
            ("alert_critical", "Pod", "critical"),
            ("pod_crashloop", "Pod", "high"),
            ("splunk_high_alert", "InferenceService", "high"),
            ("kserve_not_ready", "InferenceService", "medium"),
            ("pvc_pending", "PersistentVolumeClaim", "medium"),
        ],
        "tasks": {
            "classify_signal": "clinical-classification",
            "summarize_finding": "clinical-summarization",
            "correlate_findings": "medical-ner",
        },
    },
}

from .industry_prompts import INDUSTRY_PROMPTS, validate_output

# Build TASK_PROMPTS from industry standards + test data
TASK_TEST_DATA = {
    "fraud-scoring": "Wire transfer of $95,000 to unknown offshore account BVI-2847 at 03:14 UTC from dormant account ending 4492. Originator: J. Doe, no prior international transfers.",
    "dispute-classification": "Customer claims unauthorized charge of $2,400 on Visa ending 8891 at MerchantX on 2026-07-15. Customer states card was in possession, no PIN used.",
    "compliance-screening": "Screen entity: Petrov Trading LLC, registered in Cyprus, beneficial owner Viktor Petrov, sending $340K to Dubai Free Zone account.",
    "loan-document-extraction": "Mortgage Application: Applicant John Smith, age 42, SSN ***-**-7734, applying for $250,000 30-year fixed at First National Bank. Credit score 780. Employed 8 years at TechCorp Inc, annual income $145,000. Property: 123 Oak Street, Dallas TX 75201.",
    "product-categorization": "Wireless noise-canceling over-ear headphones with 30-hour battery life, Bluetooth 5.3, USB-C charging, foldable design, active noise cancellation with transparency mode.",
    "review-sentiment": "Product arrived damaged - one earpad was crushed and the headband was bent. Packaging was just a thin plastic bag, no box. However, customer service was excellent - they shipped a replacement the same day with express delivery.",
    "demand-classification": "Electronics category sales up 40% week-over-week driven by back-to-school promotions. Laptop inventory at 3 of 12 warehouses below safety stock. Wireless headphone SKUs sold through at 2 locations.",
    "ticket-routing": "Customer reports intermittent dropped calls in downtown Chicago area during peak hours 5-7pm. Affecting both voice and video calls. Started 3 days ago. Customer on business plan with 15 lines.",
    "network-anomaly": "BGP route flapping detected on PE-router-07 (10.0.0.7) in Chicago POP. 47 route withdrawals in 5 minutes from AS65001. Affecting downstream sites: ORD-01, ORD-02, ORD-03. No maintenance window scheduled.",
    "churn-prediction": "Enterprise customer TechStart Inc (account #A-4492): Monthly usage dropped from 450GB to 180GB over 3 months. 2 unresolved support tickets (P2 severity, open 15+ days). Current contract: $8,500/month, expires in 30 days. Competitor proposal from VerizonBiz received by customer.",
    "claims-triage": "First Notice of Loss: Policyholder Jane Wilson, Policy #HO-2026-44891. Water damage to finished basement from burst pipe during cold snap. Estimated damage $35,000. No injuries. Temporary housing needed.",
    "policy-extraction": "Commercial General Liability Policy #CGL-2026-00142. Named Insured: Acme Manufacturing LLC. Effective 01/01/2026 to 01/01/2027. Per Occurrence Limit $1,000,000. General Aggregate $2,000,000. Deductible $5,000. Premium $12,400. Endorsement: Additional Insured - Blanket.",
    "underwriting-risk": "New business submission: 25-unit apartment complex, built 1985, wood frame, flat roof replaced 2020. Located in flood zone B. Prior claims: 2 water damage claims in 5 years totaling $45K. Current coverage request: $5M property, $2M liability.",
    "clinical-classification": "Document: Radiology report for chest CT with contrast. Patient presents with persistent cough for 6 weeks and unintentional weight loss. Ordered by Dr. Patel, Pulmonology.",
    "clinical-summarization": "Progress Note: 67-year-old male with Type 2 DM, HTN, CKD Stage 3. A1C improved from 8.2 to 7.1 on metformin 1000mg BID + empagliflozin 10mg daily. BP 132/78 on lisinopril 20mg. eGFR stable at 48. Continue current regimen. Recheck A1C in 3 months. Referral to nephrology if eGFR declines.",
    "medical-ner": "Patient started on metformin 500mg BID for newly diagnosed Type 2 diabetes mellitus. Also prescribed lisinopril 10mg daily for hypertension. History of osteoarthritis in bilateral knees. Scheduled for knee MRI next week.",
}

TASK_PROMPTS = {}
for industry, tasks in INDUSTRY_PROMPTS.items():
    for task_name, cfg in tasks.items():
        test_data = TASK_TEST_DATA.get(task_name, "Test input")
        TASK_PROMPTS[task_name] = (cfg["system"] + "\n\nInput: " + test_data, cfg["max_tokens"])


def infer(model: str, prompt: str, max_tokens: int) -> dict:
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=120) as c:
            r = c.post(
                f"{API_BASE}/v1/chat/completions",
                json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            )
            r.raise_for_status()
            d = r.json()
        elapsed = (time.monotonic() - t0) * 1000
        usage = d.get("usage", {})
        toks = usage.get("completion_tokens", 0)
        content = d["choices"][0]["message"]["content"].strip().replace("\n", " ")[:60]
        tok_s = toks / (elapsed / 1000) if elapsed > 0 else 0
        return {"ok": True, "latency_ms": elapsed, "tokens": toks, "tok_s": tok_s, "content": content}
    except Exception as e:
        elapsed = (time.monotonic() - t0) * 1000
        return {"ok": False, "latency_ms": elapsed, "tokens": 0, "tok_s": 0, "content": "", "error": str(e)[:80]}


def run_simulation(rounds: int = 5):
    print("=" * 90, file=sys.stderr)
    print("FIVE-LANE SYNTHETIC SIMULATION", file=sys.stderr)
    print(f"Verticals: {list(VERTICALS.keys())}", file=sys.stderr)
    print(f"Rounds per vertical: {rounds}", file=sys.stderr)
    print("=" * 90, file=sys.stderr)

    strategy_router = StrategyRouter()
    results = defaultdict(list)

    for vertical, cfg in VERTICALS.items():
        print(f"\n{'=' * 80}", file=sys.stderr)
        print(f"VERTICAL: {vertical.upper()}", file=sys.stderr)
        print(f"{'=' * 80}", file=sys.stderr)

        # Bootstrap workload classification
        bootstrapper = WorkloadBootstrapper()
        for i in range(30):
            sig = cfg["signals"][i % len(cfg["signals"])]
            bootstrapper.observe(
                {"signal_type": sig[0], "resource_kind": sig[1], "severity": sig[2]},
                [{"evidence": {}, "outcome": "keep"}],
            )
        wt, conf = bootstrapper.current_workload()
        industry = bootstrapper.current_industry()
        strategy = strategy_router.resolve_strategy(wt)

        print(f"  Bootstrapper: {wt} (confidence={conf:.2f}, industry={industry})", file=sys.stderr)
        print(f"  Strategy: {strategy.name} (grade={strategy.overall_grade})", file=sys.stderr)
        print(file=sys.stderr)

        # Run tasks through lane routing
        for deepfield_task, benchmark_task in cfg["tasks"].items():
            lane = resolve_lane(benchmark_task)
            lane_model = resolve_lane_model(benchmark_task)
            litellm_model = lane_model  # direct model name for LiteLLM

            prompt, max_tokens = TASK_PROMPTS.get(benchmark_task, ("Hello", 5))

            print(f"  {benchmark_task:30s} → lane={lane:15s} → model={lane_model}", file=sys.stderr)

            for r in range(rounds):
                result = infer(litellm_model, prompt, max_tokens)
                results[f"{vertical}:{lane}:{lane_model}"].append(result)

                # Validate output against industry standard
                valid = False
                if result["ok"] and result["content"]:
                    valid = validate_output(vertical, benchmark_task, result["content"])
                result["standard_valid"] = valid

                status = "OK" if result["ok"] else "ERR"
                vmark = "✓" if valid else "✗"
                print(f"    round {r+1}: {result['latency_ms']:7.0f}ms  {result['tok_s']:5.1f} tok/s  [{status}] [{vmark}]  {result['content'][:40]}", file=sys.stderr)

    # Summary
    print(f"\n{'=' * 90}", file=sys.stderr)
    print("RESULTS SUMMARY", file=sys.stderr)
    print(f"{'=' * 90}", file=sys.stderr)

    print(f"\n{'Vertical:Lane:Model':50s} {'Avg ms':>8s} {'p95 ms':>8s} {'tok/s':>8s} {'OK':>4s} {'Err':>4s} {'Std%':>5s}", file=sys.stderr)
    print("-" * 90, file=sys.stderr)

    summary = {}
    for key, samples in sorted(results.items()):
        ok_samples = [s for s in samples if s["ok"]]
        err_count = len(samples) - len(ok_samples)
        if ok_samples:
            lats = sorted(s["latency_ms"] for s in ok_samples)
            avg_lat = sum(lats) / len(lats)
            p95_lat = lats[int(len(lats) * 0.95)] if len(lats) > 1 else lats[0]
            avg_tok = sum(s["tok_s"] for s in ok_samples) / len(ok_samples)
        else:
            avg_lat = p95_lat = avg_tok = 0

        parts = key.split(":")
        lane = parts[1] if len(parts) > 1 else "?"

        from cascade_compression.routing.corpora import LANE_LATENCY_TARGETS
        targets = LANE_LATENCY_TARGETS.get(lane, {"green": 500, "yellow": 2000})
        if avg_lat <= targets["green"]:
            grade = "GREEN"
        elif avg_lat <= targets["yellow"]:
            grade = "YELLOW"
        else:
            grade = "RED"

        std_valid = sum(1 for s in ok_samples if s.get("standard_valid")) if ok_samples else 0
        std_pct = std_valid / len(ok_samples) * 100 if ok_samples else 0

        print(f"  {key:50s} {avg_lat:8.0f} {p95_lat:8.0f} {avg_tok:8.1f} {len(ok_samples):4d} {err_count:4d} {std_pct:4.0f}%  [{grade}]", file=sys.stderr)
        summary[key] = {"avg_ms": avg_lat, "p95_ms": p95_lat, "tok_s": avg_tok, "ok": len(ok_samples), "errors": err_count, "standard_compliance_pct": std_pct, "grade": grade}

    # Write results
    out_dir = Path(os.environ.get("BENCHMARK_RESULTS_DIR", str(Path(__file__).parent / "results")))
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"simulation-{ts}.json"
    with open(out_path, "w") as f:
        json.dump({"type": "five_lane_simulation", "timestamp": ts, "rounds": rounds, "summary": summary}, f, indent=2)
    print(f"\nResults: {out_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()
    run_simulation(args.rounds)


if __name__ == "__main__":
    main()

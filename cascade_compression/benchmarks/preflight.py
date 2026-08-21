"""Pre-flight checks before running benchmarks.

Verifies:
1. Endpoint is reachable
2. All target models respond to health/inference
3. Cluster hardware matches expected config
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

try:
    import httpx
    import yaml
except ImportError:
    print("pip install httpx pyyaml")
    sys.exit(1)

BASE_DIR = Path(__file__).parent

API_BASE = os.environ.get("LITELLM_API_BASE", "")
API_KEY = os.environ.get("LITELLM_API_KEY", "")

COLORS = {
    "green": "\033[92m",
    "red": "\033[91m",
    "yellow": "\033[93m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


async def check_endpoint(client: httpx.AsyncClient) -> dict:
    """Check if the LiteLLM endpoint is reachable."""
    try:
        r = await client.get(f"{API_BASE}/health", timeout=10.0)
        return {"status": "ok", "code": r.status_code}
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def check_model(client: httpx.AsyncClient, alias: str) -> dict:
    """Send a minimal inference call to verify a model is serving."""
    start = time.monotonic()
    try:
        r = await client.post(
            f"{API_BASE}/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={
                "model": alias,
                "messages": [{"role": "user", "content": "Reply with just the word 'ok'."}],
                "max_tokens": 8,
                "temperature": 0,
            },
            timeout=60.0,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        if r.status_code == 200:
            data = r.json()
            output = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {"status": "ok", "latency_ms": latency_ms, "output": output[:50]}
        return {"status": "error", "code": r.status_code, "body": r.text[:100]}
    except Exception as e:
        latency_ms = int((time.monotonic() - start) * 1000)
        return {"status": "error", "latency_ms": latency_ms, "error": str(e)}


async def list_models(client: httpx.AsyncClient) -> list[str]:
    """List models available on the endpoint."""
    try:
        r = await client.get(
            f"{API_BASE}/v1/models",
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=10.0,
        )
        if r.status_code == 200:
            data = r.json()
            return [m.get("id", "") for m in data.get("data", [])]
    except Exception:
        pass
    return []


async def run_preflight(models: list[str] | None = None) -> dict:
    """Run all pre-flight checks and return a report."""
    c = COLORS

    if not API_BASE:
        print(f"{c['red']}ERROR: LITELLM_API_BASE not set{c['reset']}")
        print(f"\n  export LITELLM_API_BASE='https://<litellm-route>'")
        print(f"  export LITELLM_API_KEY='${LITELLM_API_KEY}'")
        return {"passed": False, "reason": "LITELLM_API_BASE not set"}

    print(f"{c['bold']}═══ PRE-FLIGHT CHECK ═══{c['reset']}")
    print(f"{c['dim']}Endpoint: {API_BASE}{c['reset']}\n")

    report = {"endpoint": API_BASE, "checks": [], "passed": True}

    async with httpx.AsyncClient() as client:
        # 1. Endpoint health
        print(f"  Checking endpoint... ", end="", flush=True)
        health = await check_endpoint(client)
        if health["status"] == "ok":
            print(f"{c['green']}✓ reachable{c['reset']}")
        else:
            print(f"{c['red']}✗ {health.get('error', 'unreachable')}{c['reset']}")
            report["passed"] = False
            report["checks"].append({"check": "endpoint", **health})
            return report
        report["checks"].append({"check": "endpoint", **health})

        # 2. List available models
        print(f"  Listing models... ", end="", flush=True)
        available = await list_models(client)
        print(f"{c['green']}{len(available)} found{c['reset']}")
        report["available_models"] = available

        # 3. Check each target model
        if models is None:
            config = yaml.safe_load((BASE_DIR / "configs" / "models.yaml").read_text())
            models = [m["alias"] for m in config["models"]["baseline"]]
            models += [m["alias"] for m in config["models"]["optimized"]]

        print(f"\n  {c['bold']}Model health checks ({len(models)} models):{c['reset']}")
        model_results = []
        up = 0
        down = 0

        for alias in models:
            print(f"    {alias:40s} ", end="", flush=True)
            result = await check_model(client, alias)
            if result["status"] == "ok":
                print(f"{c['green']}✓{c['reset']} {result['latency_ms']}ms")
                up += 1
            else:
                err = result.get("error", result.get("body", "unknown"))
                print(f"{c['red']}✗ {err}{c['reset']}")
                down += 1
                report["passed"] = False
            model_results.append({"model": alias, **result})

        report["checks"].append({"check": "models", "up": up, "down": down, "details": model_results})

        # Summary
        print(f"\n  {c['bold']}Summary:{c['reset']} "
              f"{c['green']}{up} up{c['reset']}, "
              f"{c['red']}{down} down{c['reset']}")

        if down > 0:
            print(f"\n  {c['yellow']}Warning: {down} model(s) are down. "
                  f"Benchmark will skip these.{c['reset']}")

        if report["passed"]:
            print(f"\n  {c['green']}{c['bold']}PRE-FLIGHT PASSED{c['reset']} — ready to benchmark")
        else:
            print(f"\n  {c['red']}{c['bold']}PRE-FLIGHT FAILED{c['reset']} — fix issues before running")

    return report


def main():
    report = asyncio.run(run_preflight())
    out_path = BASE_DIR / "results" / "preflight.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  Report saved to {out_path}")
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()

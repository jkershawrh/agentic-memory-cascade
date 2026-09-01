"""Command-line entry point for reproducible proof evidence."""

import argparse
import json
from pathlib import Path

from .proof import run_proof


def render_summary(report) -> str:
    status = "PASS" if report.passed else "FAIL"
    return "\n".join([
        "Agentic Memory Cascade — Cross-Session Proof",
        "",
        f"Session A observations: {report.observations_total}",
        f"  Routine chatter compressed: {report.observations_compressed}",
        f"  Consequential memories formed: {report.memories_formed}",
        "",
        f"Session B is distinct: {'yes' if report.sessions_are_distinct else 'no'}",
        f"  Without memory: {report.control_score}/{report.maximum_score}",
        f"  With memory:    {report.treatment_score}/{report.maximum_score}",
        f"  Unsupported claims: {report.unsupported_claims}",
        "",
        f"Result: {status}",
        report.claim,
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the cross-session agent memory proof")
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    parser.add_argument(
        "--ollama-url",
        help="Optional presentation-only Ollama base URL, for example http://localhost:11434",
    )
    parser.add_argument("--ollama-model", default="granite3.2:8b-instruct")
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a presentation-friendly summary instead of full JSON",
    )
    args = parser.parse_args()
    report = run_proof(ollama_url=args.ollama_url, ollama_model=args.ollama_model)
    payload = report.model_dump_json(indent=2)
    print(render_summary(report) if args.summary else payload)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

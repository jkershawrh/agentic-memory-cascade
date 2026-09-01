"""Command-line entry point for reproducible proof evidence."""

import argparse
import json
from pathlib import Path

from .proof import run_proof


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the cross-session agent memory proof")
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    parser.add_argument(
        "--ollama-url",
        help="Optional presentation-only Ollama base URL, for example http://localhost:11434",
    )
    parser.add_argument("--ollama-model", default="granite3.2:8b-instruct")
    args = parser.parse_args()
    report = run_proof(ollama_url=args.ollama_url, ollama_model=args.ollama_model)
    payload = report.model_dump_json(indent=2)
    print(payload)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

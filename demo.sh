#!/usr/bin/env bash
set -euo pipefail

args=(--summary --output results/proof.json)
if [[ -n "${OLLAMA_URL:-}" ]]; then
  args+=(--ollama-url "$OLLAMA_URL")
  args+=(--ollama-model "${OLLAMA_MODEL:-granite3.2:8b-instruct}")
fi

python -m agentic_memory_cascade.cli "${args[@]}"

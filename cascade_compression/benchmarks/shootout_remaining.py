"""Run the 4 remaining models that failed due to context switch."""

import json
import sys
from pathlib import Path

# Patch MODELS to only include the 4 we missed
import benchmarks.full_inference_shootout as shootout

REMAINING = [
    ("qwen3-0.6b", "/models/gguf/qwen3-0.6b/Qwen_Qwen3-0.6B-Q4_K_M.gguf", True, 4, "4Gi"),
    ("llama32-1b", "/models/gguf/llama32-1b/Llama-3.2-1B-Instruct-Q4_K_M.gguf", False, 4, "4Gi"),
    ("gemma3-1b", "/models/gguf/gemma3-1b/google_gemma-3-1b-it-Q4_K_M.gguf", False, 4, "4Gi"),
    ("qwen25-1.5b", "/models/gguf/qwen25-1.5b/qwen2.5-1.5b-instruct-q4_k_m.gguf", False, 4, "4Gi"),
]

shootout.MODELS = REMAINING
shootout.main()

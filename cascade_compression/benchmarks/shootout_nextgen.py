"""Next-gen model shootout — with re-auth before each model swap."""

import os
import subprocess
import sys
import time
import benchmarks.full_inference_shootout as shootout

NEXTGEN = [
    ("gemma4-e2b", "/models/gguf/gemma4-e2b/google_gemma-4-E2B-it-Q4_K_M.gguf", False, 4, "8Gi"),
    ("phi4-mini-reasoning", "/models/gguf/phi4-mini-reasoning/microsoft_Phi-4-mini-reasoning-Q4_K_M.gguf", True, 8, "8Gi"),
    ("gemma4-moe-26b", "/models/gguf/gemma4-moe/google_gemma-4-26B-A4B-it-Q4_K_M.gguf", False, 16, "48Gi"),
    ("gemma4-e4b", "/models/gguf/gemma4-e4b/google_gemma-4-E4B-it-Q4_K_M.gguf", False, 8, "8Gi"),
]

# Wrap swap_model to re-authenticate before each swap
_original_swap = shootout.swap_model

def swap_with_reauth(name, path, thinking, threads, mem):
    """Re-authenticate before each model swap.

    Credentials come from the environment — never hardcode them here:
        export OC_LOGIN_URL=https://api.example.com:6443
        export OC_LOGIN_TOKEN=$(oc whoami -t)
    """
    login_url = os.environ.get("OC_LOGIN_URL", "")
    login_token = os.environ.get("OC_LOGIN_TOKEN", "")
    if login_url and login_token:
        print("\n  Re-authenticating to the cluster...", file=sys.stderr)
        subprocess.run(
            ["oc", "login", login_url, f"--token={login_token}"],
            capture_output=True, timeout=15,
        )
    else:
        print(
            "\n  Skipping re-auth: set OC_LOGIN_URL and OC_LOGIN_TOKEN to enable.",
            file=sys.stderr,
        )
    return _original_swap(name, path, thinking, threads, mem)

shootout.swap_model = swap_with_reauth
shootout.MODELS = NEXTGEN
shootout.main()

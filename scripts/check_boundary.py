"""Fail when tracked files cross the public proof repository boundary."""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PREFIXES = (
    "deploy/",
    "evidence/",
    "results/",
    "customer-data/",
    "worklogs/",
)
FORBIDDEN_NAMES = {".env", "kubeconfig", "proof.json"}


def tracked_files():
    output = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True,
    )
    return [
        Path(line) for line in output.splitlines()
        if line and (ROOT / line).exists()
    ]


def main() -> int:
    violations = []
    for relative in tracked_files():
        posix = relative.as_posix()
        forbidden_name = (
            relative.name in FORBIDDEN_NAMES
            or relative.name.startswith(".env")
            or relative.name.startswith("kubeconfig")
            or relative.name.endswith(".local.yaml")
        )
        if posix.startswith(FORBIDDEN_PREFIXES) or forbidden_name:
            violations.append(f"forbidden tracked path: {posix}")
        if posix == "scripts/check_boundary.py":
            continue
        path = ROOT / relative
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        token_marker = "sha256" + "~"
        cluster_marker = "api." + "ocpv-"
        private_key_marker = "BEGIN " + "PRIVATE KEY"
        for marker in (token_marker, cluster_marker, private_key_marker):
            if marker in content:
                violations.append(f"sensitive marker in tracked file: {posix}")
                break

    if violations:
        print("\n".join(violations), file=sys.stderr)
        return 1
    print("Repository boundary check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

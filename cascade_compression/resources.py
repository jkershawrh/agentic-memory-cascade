"""Locate configuration and data files in source and installed layouts."""

from __future__ import annotations

import sysconfig
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_INSTALLED_ROOT = (
    Path(sysconfig.get_path("data")) / "share" / "cascade-compression"
)


def resource_path(category: str, filename: str) -> Path:
    """Return a packaged resource path or fail with an actionable error."""
    candidates = (
        _PROJECT_ROOT / category / filename,
        _INSTALLED_ROOT / category / filename,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(
        f"cascade-compression resource {category}/{filename} was not found; "
        f"searched: {searched}"
    )

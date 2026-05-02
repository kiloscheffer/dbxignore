"""Shared helpers for platform-specific marker backends."""
from __future__ import annotations

from pathlib import Path


def require_absolute(path: Path) -> None:
    if not path.is_absolute():
        raise ValueError(f"markers requires an absolute path; got {path!r}")

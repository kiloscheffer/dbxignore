"""Shared helpers for platform-specific install backends.

Currently exposes detect_invocation() — the logic for finding the
right `dbxignored` invocation in the running install (PyInstaller frozen
binary → PATH shim → `python3 -m dbxignore daemon` fallback). Originally
inline in linux_systemd.py; extracted here when macos_launchd.py needed
the same logic.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


def detect_invocation() -> tuple[Path, str]:
    """Return (executable, arguments) to run the daemon in the current install.

    Frozen PyInstaller bundle: returns the bundled binary path with empty args.
    Otherwise: searches PATH for `dbxignored` shim (uv tool install pattern).
    Final fallback: `python3 -m dbxignore daemon` against the active interpreter.

    Raises RuntimeError if no python3 is on PATH and no shim is found —
    callers (cli.install / cli.uninstall) translate this to a clean error
    rather than a raw traceback.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable), ""
    exe = shutil.which("dbxignored")
    if exe:
        return Path(exe), ""
    python = shutil.which("python3") or sys.executable
    if not python:
        raise RuntimeError(
            "dbxignored not on PATH and no python3 found; "
            "run `uv tool install .` from the dbxignore checkout first"
        )
    return Path(python), "-m dbxignore daemon"

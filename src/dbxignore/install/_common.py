"""Shared helpers for platform-specific install backends.

Currently exposes detect_invocation() — the logic for finding the
right dbxignore invocation in the running install (PyInstaller frozen
binary → shutil.which("dbxignore") → `python -m dbxignore daemon` fallback).
Originally inline in linux_systemd.py; extracted here when macos_launchd.py
needed the same logic.
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def detect_invocation() -> tuple[Path, str]:
    """Return (executable_path, args_string) for the installed service entry.

    Frozen (PyInstaller binary): the binary is dbxignore[.exe]; invoke it
    with "daemon" as the single argument. The pre-#30 three-step "find
    dbxignored shim" logic is gone — there is no separate dbxignored
    binary after #30 unification.

    Non-frozen (uv tool install / pip install): use the Python interpreter
    with `-m dbxignore daemon`. On Windows, prefer `pythonw.exe` for the
    windowless launch (per BACKLOG #100); fall back to `sys.executable` if
    `pythonw.exe` doesn't exist (Microsoft Store Python, embedded
    interpreters).
    """
    if getattr(sys, "frozen", False):
        # PyInstaller frozen path. After #30 there's only one binary —
        # use sys.executable's path directly.
        return Path(sys.executable), "daemon"

    # Non-frozen path.
    if sys.platform == "win32":
        pythonw_path = Path(sys.executable).with_name("pythonw.exe")
        if pythonw_path.exists():
            return pythonw_path, "-m dbxignore daemon"
        # Pythonw.exe absent (Store Python etc.) — fall back to python.exe
        # with a logged warning. The warning + fallback shape was added in
        # PR #229 (item #100).
        logger.warning(
            "pythonw.exe not found next to %s; falling back to python.exe. "
            "The daemon launched at logon may briefly flash a console window.",
            sys.executable,
        )
        return Path(sys.executable), "-m dbxignore daemon"

    # Linux / macOS non-frozen: shutil.which("dbxignore") if it exists,
    # else sys.executable with -m.
    dbxignore_in_path = shutil.which("dbxignore")
    if dbxignore_in_path:
        return Path(dbxignore_in_path), "daemon"
    return Path(sys.executable), "-m dbxignore daemon"


def detect_cli_invocation() -> str:
    """Return a quoted command-line prefix for the dbxignore CLI.

    Output is a registry-ready string: the executable plus any leading
    arguments needed before a subcommand (e.g. `"<python>" -m dbxignore`).
    Callers concatenate the subcommand + `"%1"` placeholder when building
    the full ``HKCU\\…\\shell\\<verb>\\command`` default value.

    Three branches mirror ``detect_invocation()``:

    1. **Frozen (PyInstaller).** Prefer the ``dbxignore.exe`` sibling next
       to ``sys.executable``. Both binaries ship from the same PyInstaller
       Analysis; if the user invoked ``dbxignore.exe install`` the sibling
       check returns ``sys.executable`` unchanged.
    2. **`shutil.which("dbxignore")`** — the pip/uv-install PATH shim.
    3. **Fallback** — ``"<sys.executable>" -m dbxignore``. Used when no
       shim is on PATH (typical for an editable ``uv pip install -e .``
       working directory that hasn't been exposed via ``uv tool install``).

    Raises ``RuntimeError`` if all three branches are unviable — same
    defensive guard as ``detect_invocation`` (empty ``sys.executable``
    on embedded interpreters / misconfigured frozen deployments).
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        cli_name = "dbxignore.exe" if sys.platform == "win32" else "dbxignore"
        if exe.name == cli_name:
            return f'"{exe}"'
        sibling = exe.parent / cli_name
        if sibling.exists():
            return f'"{sibling}"'
        # Fall through — shipped frozen installs always have the sibling,
        # but defend against truncated bundles by falling through.
    shim = shutil.which("dbxignore")
    if shim:
        return f'"{shim}"'
    python = sys.executable
    if not python:
        raise RuntimeError(
            "dbxignore not on PATH and sys.executable is empty; "
            "run `uv tool install .` from the dbxignore checkout first"
        )
    return f'"{python}" -m dbxignore'

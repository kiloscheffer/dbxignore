"""Shared helpers for platform-specific install backends.

Exposes detect_invocation() and detect_cli_invocation() — unified binary
lookup logic for the daemon and CLI entry points after PR #30. Frozen
(PyInstaller) paths use the single dbxignore binary directly. Non-frozen
paths prefer shutil.which("dbxignore") on Linux/macOS and pythonw.exe on
Windows (with python.exe fallback), else `python -m dbxignore`.
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
    """Return (executable_path, args_string) for the installed daemon entry.

    Frozen (PyInstaller) on Windows: prefer the ``dbxignorew.exe`` sibling
    next to ``sys.executable``. The GUI-subsystem helper launches silently
    at logon (no console flash). Falls back to ``sys.executable`` with a
    WARNING if the sibling is missing (truncated-bundle defense).

    Frozen on Linux / macOS: ``sys.executable`` is the single binary; the
    daemon runs as ``dbxignore daemon``.

    Non-frozen (uv tool install / pip install): use the Python interpreter
    with ``-m dbxignore daemon``. On Windows, prefer ``pythonw.exe`` for
    the windowless launch (item #100); fall back to ``sys.executable`` if
    ``pythonw.exe`` doesn't exist (Microsoft Store Python, embedded
    interpreters).
    """
    if getattr(sys, "frozen", False):
        # PyInstaller frozen path. On Windows, prefer the dbxignorew.exe
        # sibling — the GUI-subsystem binary launches silently at logon
        # (no console flash, no orphan conhost.exe). On Linux/macOS the
        # single binary doubles as daemon and CLI, so sys.executable is fine.
        exe = Path(sys.executable)
        if sys.platform == "win32":
            helper = exe.with_name("dbxignorew.exe")
            if helper.exists():
                return helper, "daemon"
            logger.warning(
                "dbxignorew.exe not found next to %s; falling back to dbxignore.exe. "
                "The daemon launched at logon may briefly flash a console window.",
                exe,
            )
        return exe, "daemon"

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
    # else sys.executable with -m dbxignore daemon. If sys.executable is
    # empty (some embedded interpreters) or unset, fall back to python3 on
    # PATH; if neither, raise RuntimeError so the service entry is never
    # written with a broken executable.
    dbxignore_in_path = shutil.which("dbxignore")
    if dbxignore_in_path:
        return Path(dbxignore_in_path), "daemon"
    if not sys.executable:
        python3 = shutil.which("python3")
        if not python3:
            raise RuntimeError(
                "Cannot determine Python interpreter for service entry: "
                "sys.executable is empty and python3 not on PATH.",
            )
        return Path(python3), "-m dbxignore daemon"
    return Path(sys.executable), "-m dbxignore daemon"


def detect_cli_invocation() -> str:
    """Return a quoted command-line prefix for shell-verb registry entries.

    Output is a registry-ready string: the executable plus any leading
    arguments needed before a subcommand (e.g. ``"<python>" -m dbxignore``).
    Callers concatenate the subcommand + ``"%1"`` placeholder when building
    the full ``HKCU\\…\\shell\\<verb>\\command`` default value.

    Branches:

    1. **Frozen on Windows.** Prefer the ``dbxignorew.exe`` sibling next to
       ``sys.executable`` — shell-verb invocations route through the
       GUI-subsystem binary so output flows through MessageBox and there's
       no console flash. Defensive fallback to ``sys.executable`` with a
       WARNING if the sibling is missing.
    2. **Frozen on Linux / macOS.** Prefer the ``dbxignore`` sibling
       (single binary; no GUI-subsystem split).
    3. **``shutil.which("dbxignore")``** — the pip/uv-install PATH shim.
    4. **Fallback** — ``"<sys.executable>" -m dbxignore``. Used when no
       shim is on PATH (typical for an editable ``uv pip install -e .``
       working directory that hasn't been exposed via ``uv tool install``).

    Raises ``RuntimeError`` if all branches are unviable.
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        if sys.platform == "win32":
            # Shell-verb invocations must route through dbxignorew.exe so
            # they don't flash a console window and so output flows
            # through MessageBox (no stdio in that context).
            helper = exe.parent / "dbxignorew.exe"
            if helper.exists():
                return f'"{helper}"'
            # Truncated-bundle defensive fallback — same WARNING shape as
            # detect_invocation. The verb invocation will flash a console
            # briefly until the user reinstalls.
            logger.warning(
                "dbxignorew.exe not found next to %s; falling back to %s for shell-verb registry. "
                "Verb invocations may briefly flash a console window.",
                exe,
                exe.name,
            )
            return f'"{exe}"'
        # Non-Windows frozen: sys.executable is the single binary.
        cli_name = "dbxignore"
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

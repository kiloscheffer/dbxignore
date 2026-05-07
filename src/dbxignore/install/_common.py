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

    Frozen PyInstaller bundle: prefers the `dbxignored` sibling binary that
    ships alongside `dbxignore` (both emitted from the same PyInstaller
    Analysis), falling back to `(sys.executable, "daemon")` only if the
    sibling is somehow absent. Resolution rules:

    1. If `sys.executable` itself is the `dbxignored` shim (user invoked
       `dbxignored install` directly), return it with empty args.
    2. Else look for a `dbxignored` sibling next to `sys.executable` (the
       common case — user invoked `dbxignore install` from the long-form
       binary). Return the sibling with empty args.
    3. Else fall through to `(sys.executable, "daemon")` so the service
       manager invokes the long-form binary with the `daemon` subcommand.
       Defensive only; PyInstaller specs always emit both binaries.

    Why "daemon" with empty args matters: launchd / systemd / Task Scheduler
    each invoke `ProgramArguments` / `ExecStart` / `<Arguments>` literally.
    The previous frozen-branch behavior returned `(sys.executable, "")`,
    which translated to running the long-form `dbxignore` binary with no
    subcommand — Click prints help and exits with status 2, the service
    manager's KeepAlive policy retries on the same loop. The launchctl
    print symptom is `last exit code = 2 / runs = N` with no daemon ever
    actually starting (v0.4 beta-tester report 2026-05-01).

    Non-frozen branch is platform-conditional:

    - **Windows** (Task Scheduler logon launch): use ``pythonw.exe`` —
      the windowless interpreter sibling next to ``sys.executable`` — to
      avoid the console flash + orphan ``conhost.exe`` that ``python.exe``
      would produce. The ``shutil.which("dbxignored")`` PATH-shim lookup
      is intentionally skipped on Windows: the typical Windows dev path
      is ``.venv/Scripts/python.exe``, and any PATH shim would still
      launch ``python.exe`` with a console.
    - **Linux/macOS** (systemd / launchd): try ``shutil.which("dbxignored")``
      first (the ``uv tool install`` PATH-shim case); fall back to
      ``python3 -m dbxignore daemon`` otherwise.

    Raises ``RuntimeError`` if no ``dbxignored`` shim is on PATH AND
    ``python3`` isn't on PATH AND ``sys.executable`` is empty/None. This
    last-ditch case is rare in practice (``sys.executable`` is normally
    set), but Python's docs allow it for embedded interpreters or
    misconfigured frozen deployments. CLI callers (``cli.install`` /
    ``cli.uninstall``) translate the RuntimeError to a clean error rather
    than a raw traceback.
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        daemon_name = "dbxignored.exe" if sys.platform == "win32" else "dbxignored"
        if exe.name == daemon_name:
            return exe, ""
        sibling = exe.parent / daemon_name
        if sibling.exists():
            return sibling, ""
        return exe, "daemon"
    if sys.platform == "win32":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        return pythonw, "-m dbxignore daemon"
    exe_str = shutil.which("dbxignored")
    if exe_str:
        return Path(exe_str), ""
    python = shutil.which("python3") or sys.executable
    if not python:
        # ``sys.executable`` can be ``""`` or ``None`` on embedded
        # interpreters or misconfigured frozen deployments per Python's
        # docs. Without this guard, ``Path("")`` would silently produce
        # ``PosixPath('.')`` (broken install) and ``Path(None)`` would
        # raise a raw ``TypeError`` mid-install.
        raise RuntimeError(
            "dbxignored not on PATH and no python3 found; "
            "run `uv tool install .` from the dbxignore checkout first"
        )
    return Path(python), "-m dbxignore daemon"

"""Shared helpers for platform-specific install backends.

Exposes detect_invocation() and detect_cli_invocation() — unified binary
lookup logic for the daemon and CLI entry points. Frozen
(PyInstaller) paths: on Windows, prefer the dbxignorew.exe sibling
next to sys.executable (GUI-subsystem binary, silent at logon); on Linux /
macOS, use sys.executable directly. Non-frozen
paths prefer shutil.which("dbxignore") on Linux/macOS and pythonw.exe on
Windows (with python.exe fallback), else `python -m dbxignore`.
Shared by both linux_systemd.py and macos_launchd.py.
"""

from __future__ import annotations

import logging
import shutil
import struct
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# PE\0\0 signature (4 bytes) + COFF header (20 bytes) + offset of the
# Subsystem field within the optional header (68 bytes). Layout is identical
# for PE32 and PE32+. Subsystem 2 = Windows GUI, 3 = Windows console.
_PE_SUBSYSTEM_OFFSET = 4 + 20 + 68
_IMAGE_SUBSYSTEM_WINDOWS_GUI = 2


def _windows_helper_path(exe: Path) -> Path | None:
    """Return the ``dbxignorew.exe`` sibling next to ``exe`` if it exists.

    Defensive-fallback callers log a WARNING on None and proceed with
    ``exe`` itself (which produces a console flash but works).
    """
    helper = exe.with_name("dbxignorew.exe")
    return helper if helper.exists() else None


def _is_gui_subsystem(exe: Path) -> bool:
    """Return True if ``exe`` is a Windows GUI-subsystem PE image.

    uv-created venvs ship a ``pythonw.exe`` that is a byte-identical copy of
    the console-subsystem ``python.exe`` trampoline — the name promises a
    windowless launch the binary doesn't deliver. A Task Scheduler action
    pointing at such a ``pythonw.exe`` allocates a visible console for the
    daemon's whole lifetime. Reading the PE Subsystem field is the only
    reliable capability check; the filename is not load-bearing.

    Any read/parse failure returns False — the caller then falls back to a
    launcher it can reason about rather than trusting an unverifiable binary.
    """
    try:
        with exe.open("rb") as fh:
            mz = fh.read(0x40)
            if mz[:2] != b"MZ":
                return False
            (pe_offset,) = struct.unpack_from("<I", mz, 0x3C)
            fh.seek(pe_offset)
            pe = fh.read(_PE_SUBSYSTEM_OFFSET + 2)
            if pe[:4] != b"PE\x00\x00":
                return False
            (subsystem,) = struct.unpack_from("<H", pe, _PE_SUBSYSTEM_OFFSET)
    except (OSError, struct.error):
        return False
    return bool(subsystem == _IMAGE_SUBSYSTEM_WINDOWS_GUI)


def _dbxignorew_launches_windowless(dbxignorew: Path) -> bool:
    """Return True if the non-frozen ``dbxignorew.exe`` trampoline is windowless.

    A pip/uv-generated ``dbxignorew.exe`` is a GUI-script *trampoline*: it is
    GUI-subsystem itself, but it re-execs the sibling ``pythonw.exe`` in the
    same ``Scripts`` directory to do the actual work. The launch is therefore
    only windowless when that sibling ``pythonw.exe`` is itself GUI-subsystem.
    uv project venvs (``uv sync`` / ``uv run``) ship a console-subsystem
    ``pythonw.exe``, so their ``dbxignorew.exe`` still allocates a console
    window despite being GUI-subsystem — a windowed launcher in disguise.

    This check is sound for a ``dbxignorew.exe`` that sits next to
    ``sys.executable`` (a venv ``Scripts`` layout, where interpreter and
    scripts are co-located). It is not used for PATH-resolved launchers,
    whose interpreter need not be a sibling.
    """
    return _is_gui_subsystem(dbxignorew.with_name("pythonw.exe"))


def detect_invocation() -> tuple[Path, str]:
    """Return (executable_path, args_string) for the installed daemon entry.

    Frozen (PyInstaller) on Windows: prefer the ``dbxignorew.exe`` sibling
    next to ``sys.executable``. The GUI-subsystem helper launches silently
    at logon (no console flash). Falls back to ``sys.executable`` with a
    WARNING if the sibling is missing (truncated-bundle defense).

    Frozen on Linux / macOS: ``sys.executable`` is the single binary; the
    daemon runs as ``dbxignore daemon``.

    Non-frozen (uv tool install / pip install) on Windows, in order:
    (1) the ``dbxignorew.exe`` GUI-script trampoline sibling of
    ``sys.executable`` (declared in pyproject.toml ``[project.gui-scripts]``),
    but only when its companion ``pythonw.exe`` is GUI-subsystem — the
    trampoline re-execs that ``pythonw.exe``, and uv project venvs ship a
    console-subsystem one; (2) the ``pythonw.exe`` sibling, likewise only when
    it is genuinely GUI-subsystem; (3) ``dbxignorew`` on PATH; (4)
    ``sys.executable`` with a WARNING. The ``sys.executable``-anchored options
    rank above the PATH lookup because a PATH entry could resolve to a
    different dbxignore install. ``uv run`` from a source checkout reaches (4)
    — uv project venvs have no windowless launcher; that is a developer-only
    path, real installs use ``uv tool install`` / ``pip install``.

    Non-frozen on Linux / macOS: use the Python interpreter with
    ``-m dbxignore daemon``, or the ``dbxignore`` PATH shim if present.
    """
    if getattr(sys, "frozen", False):
        # PyInstaller frozen path. On Windows, prefer the dbxignorew.exe
        # sibling — the GUI-subsystem binary launches silently at logon
        # (no console flash, no orphan conhost.exe). On Linux/macOS the
        # single binary doubles as daemon and CLI, so sys.executable is fine.
        exe = Path(sys.executable)
        if sys.platform == "win32":
            helper = _windows_helper_path(exe)
            if helper is not None:
                return helper, "daemon"
            logger.warning(
                "dbxignorew.exe not found next to %s; falling back to dbxignore.exe. "
                "The daemon launched at logon may briefly flash a console window.",
                exe,
            )
        return exe, "daemon"

    # Non-frozen path.
    if sys.platform == "win32":
        # Launcher precedence on Windows non-frozen: prefer launchers tied to
        # the *current* package over PATH lookups (which could resolve to a
        # different dbxignore install), and windowless launchers over
        # console-subsystem ones.
        #
        # 1. dbxignorew.exe sibling of sys.executable. pip/uv generate this
        #    GUI-script trampoline — the non-frozen analogue of the frozen
        #    dbxignorew.exe. It is GUI-subsystem itself but re-execs the
        #    sibling pythonw.exe, so it is only genuinely windowless when that
        #    pythonw.exe is GUI-subsystem too. uv project venvs (uv sync /
        #    uv run) ship a console-subsystem pythonw.exe, so their
        #    dbxignorew.exe still allocates a console — reject it here and
        #    fall through to the WARNING rather than registering a windowed
        #    launcher dressed up as a windowless one.
        dbxignorew_sibling = Path(sys.executable).with_name("dbxignorew.exe")
        if dbxignorew_sibling.exists() and _dbxignorew_launches_windowless(dbxignorew_sibling):
            return dbxignorew_sibling, "daemon"
        # 2. pythonw.exe sibling of sys.executable, but only if it is
        #    genuinely GUI-subsystem. Local and windowless: `-m dbxignore
        #    daemon` runs in sys.executable's environment, so it cannot be
        #    the wrong package. uv-created venvs ship a console-subsystem
        #    pythonw.exe (a byte-identical copy of python.exe) — trusting the
        #    name there allocates a visible console window for the daemon's
        #    whole lifetime, so the subsystem check is load-bearing.
        pythonw_path = Path(sys.executable).with_name("pythonw.exe")
        if pythonw_path.exists() and _is_gui_subsystem(pythonw_path):
            return pythonw_path, "-m dbxignore daemon"
        # 3. dbxignorew on PATH. Windowless, but only reached when there is
        #    no local windowless launcher — a PATH entry could belong to a
        #    different dbxignore install/version, so it ranks below the
        #    sys.executable-anchored options above (uv tool install drops the
        #    trampoline in a bin dir; pip install --user puts it in the user
        #    scripts dir).
        dbxignorew_in_path = shutil.which("dbxignorew")
        if dbxignorew_in_path:
            return Path(dbxignorew_in_path), "daemon"
        # 4. Last resort: python.exe (always console-subsystem). The daemon
        # launched at logon will show a console window; warn so the cause
        # is discoverable. The warning + fallback covers the pythonw.exe-absent
        # case and the console-subsystem-pythonw.exe /
        # console-delegating-dbxignorew.exe cases — the common one being
        # `uv run` from a source checkout, whose uv project venv ships
        # console-subsystem trampolines.
        logger.warning(
            "no windowless launcher found for %s: the dbxignorew.exe / pythonw.exe "
            "next to it are absent or console-subsystem (uv project venvs created by "
            "`uv sync` / `uv run` ship console-subsystem trampolines). Falling back to "
            "python.exe; the daemon launched at logon will show a console window. "
            "Install with `uv tool install` or `pip install` for a windowless daemon.",
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
            helper = _windows_helper_path(exe)
            if helper is not None:
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
        # Fall through — frozen bundles always have the sibling, but
        # defend against truncated bundles by falling through.
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

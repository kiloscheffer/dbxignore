"""Windows console attach + double-click MessageBox for the unified binary.

This module exists so the Windows PyInstaller binary can be built as a
GUI-subsystem executable (no console flash at Task Scheduler logon) yet
still flow output to the parent's console when launched from one.

Called from src/dbxignore/__main__.py:main_entry() BEFORE any other
imports that capture sys.stdout (notably rich-click / rich).
"""

from __future__ import annotations

import contextlib
import ctypes
import sys

_ATTACH_PARENT_PROCESS = -1
_MB_OK_ICONINFO = 0x00000040  # MB_OK (0) | MB_ICONINFORMATION (0x40)
_MESSAGE_TITLE = "dbxignore"
_MESSAGE_BODY = (
    "dbxignore is a command-line tool.\n\n"
    "Open Windows Terminal, PowerShell, or Command Prompt and run:\n\n"
    "    dbxignore --help\n\n"
    "for the list of available commands."
)


def early_init() -> None:
    """Three-context Windows entry-point setup. No-op on non-Windows.

    1. Attach to parent's console if one exists -> terminal-CLI behavior.
    2. No parent console, argv has subcommand -> silent (Task Scheduler).
    3. No parent console, argv empty -> MessageBox + exit (Explorer double-click).
    """
    if sys.platform != "win32":
        return
    if _attach_parent_console():
        _redirect_stdio_to_attached_console()
        return
    if len(sys.argv) > 1:
        return
    _show_help_message_box()
    sys.exit(0)


def _is_stream_connected(stream: object) -> bool:
    """Return True if `stream` has a valid backing FD (already wired to
    something — parent console, pipe, or file). Returns False for None
    or streams whose .fileno() raises (the GUI-subsystem launch had no
    inherited handle for this slot).
    """
    if stream is None:
        return False
    try:
        stream.fileno()  # type: ignore[union-attr, attr-defined, unused-ignore]
    except (AttributeError, OSError, ValueError):
        return False
    return True


def _attach_parent_console() -> bool:
    """Try to attach this process to the parent's console.

    Returns True if attached. False if the parent has no console
    (Task Scheduler, Explorer double-click) or attach otherwise failed.
    """
    try:
        return bool(ctypes.windll.kernel32.AttachConsole(_ATTACH_PARENT_PROCESS))
    except OSError:
        return False


def _show_help_message_box() -> None:
    """Pop a MessageBox saying dbxignore is a CLI tool.

    Wrapped in try/except so an unusual session state (no window station,
    locked-down desktop) falls through to silent exit rather than crashing.
    """
    with contextlib.suppress(OSError):
        ctypes.windll.user32.MessageBoxW(
            None,
            _MESSAGE_BODY,
            _MESSAGE_TITLE,
            _MB_OK_ICONINFO,
        )


def _redirect_stdio_to_attached_console() -> None:
    """Reopen each stream against CONOUT$ / CONIN$ ONLY if it's missing or
    invalid. Each stream is handled independently — preserves mixed cases
    like `dbxignore --version 2> err.log` (stdout to console, stderr to file).

    CRITICAL: don't replace streams that have valid inherited FDs. If the user
    ran `dbxignore --version > out.txt` or `dbxignore --version | findstr ...`
    from a shell, the inherited stdio is the redirected file or pipe —
    overwriting with CONOUT$ would send output to the console instead,
    breaking the redirection contract.
    """
    if not _is_stream_connected(sys.stdout):
        with contextlib.suppress(OSError):
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
    if not _is_stream_connected(sys.stderr):
        with contextlib.suppress(OSError):
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
    if not _is_stream_connected(sys.stdin):
        with contextlib.suppress(OSError):
            sys.stdin = open("CONIN$", "r", encoding="utf-8")  # noqa: SIM115, UP015

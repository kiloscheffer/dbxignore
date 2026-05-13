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
import os
import sys

_ATTACH_PARENT_PROCESS = -1
_MB_OK_ICONINFO = 0x00000040  # MB_OK (0) | MB_ICONINFORMATION (0x40)
_MESSAGE_TITLE = "dbxignore"

# Win32 standard-handle constants (GetStdHandle argument values)
_STD_INPUT_HANDLE = -10
_STD_OUTPUT_HANDLE = -11
_STD_ERROR_HANDLE = -12
_INVALID_HANDLE_VALUE = -1
_MESSAGE_BODY = (
    "dbxignore is a command-line tool.\n\n"
    "Open Windows Terminal, PowerShell, or Command Prompt and run:\n\n"
    "    dbxignore --help\n\n"
    "for the list of available commands."
)


def early_init() -> None:
    """Three-context Windows entry-point setup. No-op on non-Windows.

    1. Process already has a console (CUI binary, e.g., the non-frozen
       trampoline from pip install / uv tool install) -> no-op, let click
       handle argv normally. This is the most common case for interactive
       users.
    2. Attach to parent's console (GUI binary launched from a terminal)
       -> terminal-CLI behavior with per-stream stdio preservation.
    3. No parent console, argv has subcommand -> silent (Task Scheduler).
    4. No parent console, argv empty -> MessageBox + exit (Explorer
       double-click).
    """
    if sys.platform != "win32":
        return
    if _has_console():
        return
    if _attach_parent_console():
        _redirect_stdio_to_attached_console()
        return
    if len(sys.argv) > 1:
        return
    _show_help_message_box()
    sys.exit(0)


def _has_console() -> bool:
    """True if this process already has a console attached at startup
    (e.g., CUI-subsystem binary, or a process launched into a session
    with an inherited console). Returns False for GUI-subsystem binaries
    launched from Explorer, Task Scheduler, or a terminal-less context.
    """
    try:
        return bool(ctypes.windll.kernel32.GetConsoleWindow())  # type: ignore[attr-defined, unused-ignore]
    except (OSError, AttributeError):
        # AttributeError covers non-Windows (ctypes.windll doesn't exist);
        # OSError covers Windows API failures in unusual session states.
        return False


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
        return bool(ctypes.windll.kernel32.AttachConsole(_ATTACH_PARENT_PROCESS))  # type: ignore[attr-defined, unused-ignore]
    except (OSError, AttributeError):
        # AttributeError covers non-Windows (ctypes.windll doesn't exist);
        # OSError covers Windows API failures.
        return False


def _show_help_message_box() -> None:
    """Pop a MessageBox saying dbxignore is a CLI tool.

    Wrapped in try/except so an unusual session state (no window station,
    locked-down desktop) falls through to silent exit rather than crashing.
    """
    with contextlib.suppress(OSError, AttributeError):
        # AttributeError covers non-Windows (ctypes.windll doesn't exist);
        # OSError covers MessageBox failures in unusual session states.
        ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined, unused-ignore]
            None,
            _MESSAGE_BODY,
            _MESSAGE_TITLE,
            _MB_OK_ICONINFO,
        )


def _restore_inherited_stdio() -> None:
    """Rehydrate sys.stdin/stdout/stderr from Win32-inherited OS handles.

    PyInstaller's noconsole bootloader sets sys.stdio to None at startup,
    even when the parent shell passed in inherited file/pipe handles via
    STARTF_USESTDHANDLES. Recover the handles via GetStdHandle() and wrap
    them as Python file objects. Streams that are already valid (e.g. on
    the CUI trampoline path) are left untouched. Streams whose Win32
    handle is missing/invalid (Task Scheduler at logon) remain unset —
    the caller's CONOUT$/CONIN$ fallback fills those.
    """
    import msvcrt

    streams: list[tuple[int, str, str, int]] = [
        (_STD_INPUT_HANDLE, "stdin", "r", os.O_RDONLY),
        (_STD_OUTPUT_HANDLE, "stdout", "w", os.O_APPEND),
        (_STD_ERROR_HANDLE, "stderr", "w", os.O_APPEND),
    ]
    for std_handle_const, sys_attr, mode, fd_flags in streams:
        if _is_stream_connected(getattr(sys, sys_attr)):
            continue
        try:
            handle = ctypes.windll.kernel32.GetStdHandle(std_handle_const)  # type: ignore[attr-defined, unused-ignore]
            if handle in (0, _INVALID_HANDLE_VALUE):
                continue
            fd = msvcrt.open_osfhandle(handle, fd_flags)
            stream = os.fdopen(fd, mode, encoding="utf-8", buffering=1)
            setattr(sys, sys_attr, stream)
        except (OSError, AttributeError, ValueError):
            continue


def _redirect_stdio_to_attached_console() -> None:
    """Reopen each stream against CONOUT$ / CONIN$ ONLY if it's missing or
    invalid AFTER rehydrating any Win32-inherited handles. Each stream is
    handled independently — preserves mixed cases like
    `dbxignore --version 2> err.log` (stdout to console, stderr to file).

    The rehydrate-first order is load-bearing for the PyInstaller frozen
    binary: noconsole mode sets sys.stdio to None even when the parent
    shell passed in redirected file/pipe handles. Without the
    GetStdHandle pass, `dbxignore --version > out.txt` would overwrite
    the inherited file handle with CONOUT$ and write to the console.
    """
    _restore_inherited_stdio()
    if not _is_stream_connected(sys.stdout):
        with contextlib.suppress(OSError):
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
    if not _is_stream_connected(sys.stderr):
        with contextlib.suppress(OSError):
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
    if not _is_stream_connected(sys.stdin):
        with contextlib.suppress(OSError):
            sys.stdin = open("CONIN$", "r", encoding="utf-8")  # noqa: SIM115, UP015

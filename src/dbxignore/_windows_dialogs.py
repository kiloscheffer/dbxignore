"""Windows MessageBox dialogs for the GUI-subsystem dbxignorew.exe binary.

When `dbxignorew.exe` is invoked — by Windows Task Scheduler at logon, by
an Explorer shell-verb registry entry (right-click → Ignore from Dropbox),
or by an Explorer double-click — the process has no console window. The
console-detection probe in `should_use_gui_dialogs()` checks
`GetConsoleWindow() == 0` to route output through MessageBox instead of
the click.echo / click.confirm paths that would be invisible.

The console-subsystem `dbxignore.exe` binary always has a console at
startup, so `should_use_gui_dialogs()` returns False there — the click
paths run normally.
"""

from __future__ import annotations

import contextlib
import ctypes
import sys

# Win32 MessageBoxW button + icon flags
_MB_OK = 0x00000000
_MB_YESNO = 0x00000004
_MB_ICONERROR = 0x00000010
_MB_ICONWARNING = 0x00000030
_MB_ICONINFORMATION = 0x00000040
_IDYES = 6
_DEFAULT_TITLE = "dbxignore"


def should_use_gui_dialogs() -> bool:
    """True if the current process has no console window — the GUI-subsystem
    `dbxignorew.exe` path (Task Scheduler daemon, shell-verb invocations,
    Explorer double-click).

    Returns False on the console-subsystem `dbxignore.exe` path, on the
    trampoline (uv tool install / pip install) which inherits a console,
    and on non-Windows.
    """
    if sys.platform != "win32":
        return False
    try:
        return not bool(ctypes.windll.kernel32.GetConsoleWindow())  # type: ignore[attr-defined, unused-ignore]
    except (OSError, AttributeError):
        # AttributeError covers non-Windows (defensive; sys.platform check
        # should already have returned). OSError covers Windows API
        # failures in unusual session states — fall through to "treat as
        # GUI" so destructive operations don't silently confirm.
        return True


def confirm_destructive(message: str, title: str = _DEFAULT_TITLE) -> bool:
    """Show a MessageBox warning dialog (yellow triangle, Yes/No buttons).
    Returns True if the user clicked Yes, False otherwise (No, dialog
    dismissed, or call failed).
    """
    try:
        result: int = ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined, unused-ignore]
            None,
            message,
            title,
            _MB_YESNO | _MB_ICONWARNING,
        )
    except (OSError, AttributeError):
        return False
    return result == _IDYES


def show_error(message: str, title: str = _DEFAULT_TITLE) -> None:
    """Show a MessageBox error dialog (red X, OK button). Silent on failure
    (unusual session state, non-Windows)."""
    with contextlib.suppress(OSError, AttributeError):
        ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined, unused-ignore]
            None,
            message,
            title,
            _MB_OK | _MB_ICONERROR,
        )

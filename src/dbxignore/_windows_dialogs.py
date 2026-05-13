"""Windows MessageBox dialogs for the GUI-subsystem dbxignorew.exe binary.

When `dbxignorew.exe` is invoked — by Windows Task Scheduler at logon, by
an Explorer shell-verb registry entry (right-click → Ignore from Dropbox),
or by an Explorer double-click — the process has no console window and no
inherited stdio handle. `should_use_gui_dialogs()` checks both conditions
to route output through MessageBox instead of the click.echo / click.confirm
paths that would be invisible.

The console-subsystem `dbxignore.exe` binary always has a console at
startup, so `should_use_gui_dialogs()` returns False there — the click
paths run normally. `dbxignore.exe` launched with `CREATE_NO_WINDOW` from
automation also returns False — the inherited pipe handle is real and output
should flow there, not a MessageBox the parent script can't see.
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
    """True if the current process has no console AND stdio has no real backing.

    Routes output through MessageBox in two scenarios:
    - `dbxignorew.exe` invoked by Task Scheduler / Explorer shell verbs /
      double-click: no console, PyInstaller noconsole-bootloader stub writer
      whose `fileno()` raises.
    - Unusual session states where the GetConsoleWindow probe itself fails
      (defensive fall-through).

    Returns False on:
    - The console-subsystem `dbxignore.exe` (has a real console at startup).
    - `dbxignore.exe` launched with `CREATE_NO_WINDOW` from automation
      (no console, but `sys.stdout` is a real inherited pipe — output
      should flow there, not a MessageBox the parent can't see).
    - The pip/uv-tool trampoline (inherits a console from the launching shell).
    - Non-Windows.
    """
    if sys.platform != "win32":
        return False
    try:
        if ctypes.windll.kernel32.GetConsoleWindow():  # type: ignore[attr-defined, unused-ignore]
            return False
    except (OSError, AttributeError):
        # AttributeError on non-Windows (defensive); OSError on unusual
        # session states. Conservative fall-through: treat as GUI so
        # destructive operations get a visible MessageBox confirmation
        # rather than silently auto-confirming via click.confirm.
        return True
    # No console. Is sys.stdout a real inherited handle (CREATE_NO_WINDOW
    # parent's pipe) or the PyInstaller noconsole-bootloader stub? Only
    # the latter routes through MessageBox.
    if sys.stdout is None:
        return True
    try:
        sys.stdout.fileno()
    except (AttributeError, OSError, ValueError):
        return True
    return False


def _show_messagebox(message: str, title: str, flags: int) -> None:
    """Call user32.MessageBoxW; silent on failure (unusual session state, non-Windows)."""
    with contextlib.suppress(OSError, AttributeError):
        ctypes.windll.user32.MessageBoxW(None, message, title, flags)  # type: ignore[attr-defined, unused-ignore]


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
    """Show a MessageBox error dialog (red X, OK button)."""
    _show_messagebox(message, title, _MB_OK | _MB_ICONERROR)


def show_info(message: str, title: str = _DEFAULT_TITLE) -> None:
    """Show a MessageBox info dialog (blue info icon, OK button)."""
    _show_messagebox(message, title, _MB_OK | _MB_ICONINFORMATION)

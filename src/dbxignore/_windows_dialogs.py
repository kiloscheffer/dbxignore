"""Windows MessageBox dialogs for shell-verb interactive subcommands.

When the Explorer shell-integration verbs registered by `dbxignore install`
invoke the binary (e.g. right-click -> Ignore from Dropbox), the resulting
process has no console — the GUI-subsystem binary built by #30 leaves
sys.stdio at None, so click.confirm and click.echo are invisible.

This module provides MessageBox-based replacements for the
destructive-confirmation + error-reporting paths that those subcommands
need. Used only when sys.stdout is None at the cli-handler runtime
(the "no stdio" signal that the shell-verb context produces).
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
    """True if the current process is in the no-stdio Windows GUI context
    (PyInstaller noconsole binary launched without an inherited console
    or a parent console — i.e., the Explorer shell-verb invocation path).

    Returns False on the trampoline path, in terminals, and on non-Windows.
    """
    if sys.platform != "win32":
        return False
    return sys.stdout is None


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

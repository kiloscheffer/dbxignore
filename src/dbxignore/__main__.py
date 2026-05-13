"""Entry point for `python -m dbxignore` and the `dbxignore` console script.

Both Windows binaries (dbxignore.exe and dbxignorew.exe) ship from this
same entry. The console-presence probe in
src/dbxignore/_windows_dialogs.py:should_use_gui_dialogs() decides
whether interactive subcommands route output through MessageBox.

On Windows we additionally guard against the Explorer double-click case
on the GUI helper: if dbxignorew.exe is launched with no arguments and
no console, click would write its usage to a stream that goes nowhere
and the process would exit silently. The pre-cli hook below pops a
"this is a command-line tool" MessageBox instead.
"""

from __future__ import annotations

import sys


def main_entry() -> None:
    if sys.platform == "win32":
        _handle_explorer_double_click()
    from dbxignore.cli import main

    main()


def _handle_explorer_double_click() -> None:
    """Pop a help MessageBox + exit if invoked by Explorer double-click on
    the GUI helper (dbxignorew.exe).

    Explorer double-click is identified by the combination of (a) empty
    argv beyond the program name and (b) no attached console window.
    Task Scheduler invocations always pass "daemon" as an argument;
    shell-verb invocations always pass a subcommand like "ignore" or
    "unignore" + the target path. Both bypass this hook by virtue of (a).

    The console-subsystem dbxignore.exe binary always has a console at
    startup, so should_use_gui_dialogs() returns False and the hook
    bypasses via (b) — letting click print usage to the terminal normally.
    """
    if len(sys.argv) > 1:
        return
    from dbxignore import _windows_dialogs

    if not _windows_dialogs.should_use_gui_dialogs():
        return
    _windows_dialogs.show_info(
        "dbxignore is a command-line tool.\n\n"
        "Open Windows Terminal, PowerShell, or Command Prompt and run:\n\n"
        "    dbxignore --help\n\n"
        "for the list of available commands."
    )
    sys.exit(0)


if __name__ == "__main__":
    main_entry()

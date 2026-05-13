"""Entry point for `python -m dbxignore` and the `dbxignore` console script.

Both Windows binaries (dbxignore.exe and dbxignorew.exe) ship from this
same entry. The console-presence probe in
src/dbxignore/_windows_dialogs.py:should_use_gui_dialogs() decides
whether interactive subcommands route output through MessageBox; nothing
about that decision needs to happen before click parses argv, so this
entry is platform-agnostic.
"""

from __future__ import annotations


def main_entry() -> None:
    from dbxignore.cli import main

    main()


if __name__ == "__main__":
    main_entry()

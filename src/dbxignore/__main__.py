"""Entry point for `python -m dbxignore` and (after Task 10's pyproject
change) for the `dbxignore` console script.

On Windows, _windows_console.early_init() runs BEFORE the cli import:
- Attaches the GUI-subsystem binary to the parent console if one exists.
- Pops a MessageBox on Explorer double-click (no parent + no argv).
- No-op on Linux / macOS.

The cli import is deferred so rich-click's rich.console.Console() (which
captures sys.stdout at module-import time) sees the post-attach stdout.
"""

from __future__ import annotations

import sys


def main_entry() -> None:
    if sys.platform == "win32":
        from dbxignore import _windows_console

        _windows_console.early_init()  # may sys.exit(0) on double-click path
    from dbxignore.cli import main  # deferred import — after stdio redirect

    main()


if __name__ == "__main__":
    main_entry()

"""Windows Explorer right-click verb registration (backlog #65).

Writes two HKCU registry keys that surface "Ignore from Dropbox" and
"Restore to Dropbox" verbs on every file and directory inside discovered
Dropbox roots. The actual marker write is routed through `dbxignore.exe`
rather than re-implementing the ADS write inline, so the ``\\\\?\\`` long-path
correctness in `_backends/windows_ads.py` is reused.

The module loads on every platform but its public functions only do work
on Windows — `winreg` is imported lazily inside `install_shell_integration`
and `uninstall_shell_integration` so non-Windows imports succeed cleanly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# Registry layout — fixed paths so uninstall can target exactly our keys
# without touching other shell extensions.
_REG_BASE = r"Software\Classes\AllFilesystemObjects\shell"
_IGNORE_VERB = "DbxignoreIgnore"
_RESTORE_VERB = "DbxignoreRestore"


def _format_applies_to_query(roots: list[Path]) -> str:
    """Build the AppliesTo query string for the given Dropbox roots.

    Each root produces two clauses ORed together:
    - ``System.ItemPathDisplay:="<root>"`` — exact match the root itself
    - ``System.ItemPathDisplay:~<"<root>\\\\"`` — prefix match for descendants

    The trailing-backslash variant prevents false matches on sibling
    folders (e.g. matching ``C:\\Dropbox-other`` when scoped to ``C:\\Dropbox``).

    Inside the AppliesTo quoted string literals, ``\\\\`` is the escape for a
    single backslash. So every backslash in the input path is doubled
    before being embedded in the query.

    Raises ``RuntimeError`` if any root contains a literal ``"`` — escaping
    quotes inside AppliesTo's grammar is unsound for typical filesystem
    paths and Explorer rejects them for most operations anyway.
    """
    clauses: list[str] = []
    for root in roots:
        root_str = str(root)
        if '"' in root_str:
            raise RuntimeError(
                f"Dropbox root path {root_str!r} contains a quote character; "
                "cannot generate AppliesTo query"
            )
        escaped_root = root_str.replace("\\", "\\\\")
        clauses.append(f'System.ItemPathDisplay:="{escaped_root}"')
        prefix = root_str + "\\"
        escaped_prefix = prefix.replace("\\", "\\\\")
        clauses.append(f'System.ItemPathDisplay:~<"{escaped_prefix}"')
    return " OR ".join(clauses)

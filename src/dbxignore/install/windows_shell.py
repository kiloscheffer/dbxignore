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

from dbxignore.install._common import detect_cli_invocation

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
        # `root_str + "\\"` would double-append on drive roots like `D:\` where
        # `str(Path)` already ends in a backslash; `.rstrip` + re-append normalizes.
        prefix = root_str.rstrip("\\") + "\\"
        escaped_prefix = prefix.replace("\\", "\\\\")
        clauses.append(f'System.ItemPathDisplay:~<"{escaped_prefix}"')
    return " OR ".join(clauses)


def install_shell_integration(dropbox_roots: list[Path]) -> None:
    """Write the two HKCU verb keys for the given Dropbox roots.

    Raises ``RuntimeError`` if any root contains a literal ``"`` (propagated
    from ``_format_applies_to_query``). On ``OSError`` mid-write, calls
    ``uninstall_shell_integration()`` to clean up partially-written keys
    and re-raises — the result is "nothing or everything," never a
    half-installed state.
    """
    import winreg  # noqa: PLC0415  # lazy import — module loads on non-Windows

    applies_to = _format_applies_to_query(dropbox_roots)
    cli_prefix = detect_cli_invocation()

    verbs = [
        (_IGNORE_VERB, "Ignore from Dropbox", f'{cli_prefix} ignore "%1"'),
        (_RESTORE_VERB, "Restore to Dropbox", f'{cli_prefix} unignore --yes "%1"'),
    ]

    try:
        for verb_key, mui_verb, command in verbs:
            verb_path = f"{_REG_BASE}\\{verb_key}"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, verb_path) as key:
                winreg.SetValueEx(key, "MUIVerb", 0, winreg.REG_SZ, mui_verb)
                winreg.SetValueEx(key, "AppliesTo", 0, winreg.REG_SZ, applies_to)
            command_path = f"{verb_path}\\command"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, command_path) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    except OSError as exc:
        logger.warning("shell-integration install failed mid-write (%s); attempting cleanup", exc)
        try:
            uninstall_shell_integration()
        except Exception:  # noqa: BLE001 — cleanup is best-effort; must not mask original OSError
            # Cleanup failure on top of install failure — log but don't mask
            # the original exception below.
            logger.warning("shell-integration cleanup after failed install also failed")
        raise

    logger.info("Installed Explorer right-click integration (HKCU verbs).")


def uninstall_shell_integration(*, errors: list[tuple[str, str]] | None = None) -> None:
    """Remove the two HKCU verb keys. Idempotent.

    Walks each verb's tree in reverse order: command subkey first
    (winreg.DeleteKey only deletes leaf keys), then the verb key itself.
    FileNotFoundError on any DeleteKey call is treated as "already gone."

    On non-FileNotFoundError OSError: if ``errors`` is provided, append
    ``(registry_key_path, message)``; otherwise log WARNING. Loop always
    continues to the next key — never aborts partway.
    """
    import winreg  # noqa: PLC0415  # lazy import — module loads on non-Windows

    for verb_key in (_IGNORE_VERB, _RESTORE_VERB):
        for subpath in (f"{_REG_BASE}\\{verb_key}\\command", f"{_REG_BASE}\\{verb_key}"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subpath)
            except FileNotFoundError:
                pass
            except OSError as exc:
                msg = f"DeleteKey failed: {exc}"
                if errors is not None:
                    errors.append((f"HKCU\\{subpath}", msg))
                else:
                    logger.warning("shell-integration uninstall: %s on %s", exc, subpath)

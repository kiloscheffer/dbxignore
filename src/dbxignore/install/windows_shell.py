"""Windows Explorer right-click verb registration.

Writes two HKCU registry keys that surface "Ignore from Dropbox" and
"Restore to Dropbox" verbs on every file and directory inside discovered
Dropbox roots. The actual marker write is routed through `dbxignore.exe`
rather than re-implementing the ADS write inline, so the ``\\\\?\\`` long-path
correctness in `_backends/windows_ads.py` is reused.

The module loads on every platform but its public functions only do work
on Windows — `winreg` is imported lazily inside `install_shell_integration`
and `uninstall_shell_integration` so non-Windows imports succeed cleanly.

Icon delivery: the verb icon ships inside the wheel / frozen bundle at
``dbxignore/_resources/context-menu.ico`` and is copied at install time
to ``state.user_state_dir() / "icons" / context-menu.ico``. Explorer reads
the registry's ``Icon`` value lazily on every menu render, including
while dbxignore is not running, so the icon must live at a path that
survives outside any process lifetime. ``uninstall_shell_integration``
removes both the file and (if empty) the ``icons/`` subdirectory.
"""

from __future__ import annotations

import contextlib
import logging
from importlib.resources import files
from typing import TYPE_CHECKING

from dbxignore import state
from dbxignore.install._common import detect_cli_invocation

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# Registry layout — fixed paths so uninstall can target exactly our keys
# without touching other shell extensions.
_REG_BASE = r"Software\Classes\AllFilesystemObjects\shell"
_IGNORE_VERB = "DbxignoreIgnore"
_RESTORE_VERB = "DbxignoreRestore"

# Icon delivery. Resource name (inside the bundled `dbxignore._resources`
# package) and installed file name are intentionally identical so the
# install-time copy is a straight name-preserving operation. The
# `icons/` subdir of the per-user state dir isolates the icon from
# state.json / daemon.log* — `_purge_local_state`'s glob patterns leave it
# alone, so the icon-removal arm in `uninstall_shell_integration` is
# authoritative for icon lifecycle.
_ICON_RESOURCE_NAME = "context-menu.ico"
_ICON_DIR_NAME = "icons"
_ICON_INSTALLED_NAME = "context-menu.ico"


def _icon_install_path() -> Path:
    """Return the on-disk path the verb icon is copied to during install."""
    return state.user_state_dir() / _ICON_DIR_NAME / _ICON_INSTALLED_NAME


def _install_icon() -> Path:
    """Copy the bundled verb icon to the per-user state dir.

    Returns the absolute destination path. The caller writes this path
    into the verb keys' ``Icon`` REG_SZ value.

    Overwrites any existing file at the destination (re-install or
    version upgrade). The parent directory is created with ``parents=True``
    if missing. Any ``OSError`` propagates so the caller's cleanup arm
    fires.

    Writes via temp file + ``Path.replace`` (atomic on NTFS for same-
    volume renames) so Explorer's lazy reads of the ``Icon`` registry
    value during a re-install never see a half-written .ico. Mirrors the
    invariant ``state.write()`` follows for ``state.json``.
    """
    src_resource = files("dbxignore._resources").joinpath(_ICON_RESOURCE_NAME)
    dst = _icon_install_path()
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.parent / (dst.name + ".tmp")
    tmp.write_bytes(src_resource.read_bytes())
    tmp.replace(dst)
    return dst


def _uninstall_icon(*, errors: list[tuple[str, str]] | None = None) -> None:
    """Remove the verb icon and (if empty) its enclosing ``icons/`` dir.

    Idempotent: missing file is not an error. On non-``FileNotFoundError``
    ``OSError``, mirrors the registry-removal contract — append to
    ``errors`` when provided, otherwise log a WARNING. The empty-dir rmdir
    is best-effort: a non-empty ``icons/`` (e.g. a future second icon
    dropped by the user) is preserved silently.
    """
    dst = _icon_install_path()
    try:
        dst.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        msg = f"icon unlink failed: {exc}"
        if errors is not None:
            errors.append((str(dst), msg))
        else:
            logger.warning("shell-integration uninstall: %s on %s", exc, dst)
    # Remove the icons/ subdir only if empty — preserves anything else a
    # user or future feature might have dropped there. OSError (ENOTEMPTY,
    # ENOENT, permission denied) is swallowed: this is cosmetic cleanup.
    with contextlib.suppress(OSError):
        dst.parent.rmdir()


def _format_applies_to_query(roots: list[Path]) -> str:
    """Build the AppliesTo query string for the given Dropbox roots.

    Each root produces two clauses ORed together:
    - ``System.ItemPathDisplay:="<root>"`` — exact match the root itself
    - ``System.ItemPathDisplay:~<"<root>\\"`` — prefix match for descendants

    The trailing-backslash variant prevents false matches on sibling
    folders (e.g. matching ``C:\\Dropbox-other`` when scoped to ``C:\\Dropbox``).

    AQS does no escape interpretation inside quoted string literals: backslashes
    are stored and matched literally. A single trailing ``\\`` immediately before
    the closing ``"`` is parsed as a literal trailing backslash without escaping
    the quote. So embed each path with its natural single backslashes — no
    doubling.

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
        # AQS does no escape interpretation inside quoted string literals:
        # backslashes are literal, and a single trailing `\` before the
        # closing `"` is parsed as a literal trailing backslash without
        # escaping the quote. So embed the path with its natural single
        # backslashes — no doubling.
        clauses.append(f'System.ItemPathDisplay:="{root_str}"')
        # Prefix clause matches descendants. Trailing `\` disambiguates
        # `C:\Dropbox\` from `C:\Dropbox-other` (which would otherwise
        # also start with `C:\Dropbox`). Drive-root case `D:\` already
        # ends in `\`; rstrip+re-append normalizes without double-trailing.
        prefix = root_str.rstrip("\\") + "\\"
        clauses.append(f'System.ItemPathDisplay:~<"{prefix}"')
    return " OR ".join(clauses)


def install_shell_integration(dropbox_roots: list[Path]) -> None:
    """Write the two HKCU verb keys for the given Dropbox roots.

    Raises ``RuntimeError`` if any root contains a literal ``"`` (propagated
    from ``_format_applies_to_query``). On ``OSError`` mid-write, calls
    ``uninstall_shell_integration()`` to clean up partially-written keys
    and the copied icon file, then re-raises — the result is "nothing or
    everything," never a half-installed state.
    """
    import winreg  # noqa: PLC0415  # lazy import — module loads on non-Windows

    applies_to = _format_applies_to_query(dropbox_roots)
    cli_prefix = detect_cli_invocation()

    # Copy the icon to its stable per-user path before any registry writes.
    # A failure here propagates without polluting the registry. If registry
    # writes later fail, uninstall_shell_integration() in the cleanup arm
    # removes both the icon and the keys.
    icon_value = str(_install_icon())

    verbs = [
        (_IGNORE_VERB, "Ignore from Dropbox", f'{cli_prefix} ignore "%1"'),
        (_RESTORE_VERB, "Restore to Dropbox", f'{cli_prefix} unignore --yes "%1"'),
    ]

    try:
        for verb_key, mui_verb, command in verbs:
            verb_path = f"{_REG_BASE}\\{verb_key}"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, verb_path) as key:  # type: ignore[attr-defined, unused-ignore]
                winreg.SetValueEx(key, "MUIVerb", 0, winreg.REG_SZ, mui_verb)  # type: ignore[attr-defined, unused-ignore]
                winreg.SetValueEx(key, "AppliesTo", 0, winreg.REG_SZ, applies_to)  # type: ignore[attr-defined, unused-ignore]
                winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, icon_value)  # type: ignore[attr-defined, unused-ignore]
            command_path = f"{verb_path}\\command"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, command_path) as key:  # type: ignore[attr-defined, unused-ignore]
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)  # type: ignore[attr-defined, unused-ignore]
    except OSError as exc:
        logger.warning("shell-integration install failed mid-write (%s); attempting cleanup", exc)
        try:
            uninstall_shell_integration()
        except Exception:  # noqa: BLE001 — cleanup is best-effort; must not mask original OSError
            # Cleanup failure on top of install failure — log but don't mask
            # the original exception below.
            logger.warning("shell-integration cleanup after failed install also failed")
        raise


def uninstall_shell_integration(*, errors: list[tuple[str, str]] | None = None) -> None:
    """Remove the two HKCU verb keys and the installed icon. Idempotent.

    Walks each verb's tree in reverse order: command subkey first
    (winreg.DeleteKey only deletes leaf keys), then the verb key itself.
    FileNotFoundError on any DeleteKey call is treated as "already gone."

    On non-FileNotFoundError OSError: if ``errors`` is provided, append
    ``(registry_key_path, message)``; otherwise log WARNING. Loop always
    continues to the next key — never aborts partway. After the registry
    sweep, ``_uninstall_icon`` removes the copied icon file under the
    same errors-or-WARNING contract.
    """
    import winreg  # noqa: PLC0415  # lazy import — module loads on non-Windows

    for verb_key in (_IGNORE_VERB, _RESTORE_VERB):
        for subpath in (f"{_REG_BASE}\\{verb_key}\\command", f"{_REG_BASE}\\{verb_key}"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subpath)  # type: ignore[attr-defined, unused-ignore]
            except FileNotFoundError:
                pass
            except OSError as exc:
                msg = f"DeleteKey failed: {exc}"
                if errors is not None:
                    errors.append((f"HKCU\\{subpath}", msg))
                else:
                    logger.warning("shell-integration uninstall: %s on %s", exc, subpath)
    _uninstall_icon(errors=errors)

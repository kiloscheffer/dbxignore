"""Read/write the Dropbox 'ignore' xattr on macOS.

Dropbox on macOS treats a path as ignored if it carries an extended
attribute with a non-empty value.  Two different attribute names exist
because Dropbox itself runs in two different sync modes on macOS:

- **Legacy mode** — Dropbox folder at ``~/Dropbox``, synced by Dropbox's
  own daemon. Watches for ``com.dropbox.ignored``. The historic mode;
  still in use by some installs that haven't migrated.
- **File Provider mode** — Dropbox folder at
  ``~/Library/CloudStorage/Dropbox/``, synced by Apple's File Provider
  extension via ``DropboxFileProvider.appex``. Default for new installs
  since 2023. Watches for ``com.apple.fileprovider.ignore#P``. The
  ``#P`` suffix is Apple's "persistent across reboots" marker convention
  for File Provider attributes.

This module auto-detects which mode is active on the host and selects
the matching attribute name.  See ``_detected_attr_name()`` below.
The detection is cached at module-call granularity so the per-file
reconcile loop doesn't re-stat on every operation.

(Note: macOS xattrs have no ``user.`` namespace prefix — that is a Linux
convention.)

This module uses the ``xattr`` PyPI package with ``symlink=True`` (which
maps internally to ``XATTR_NOFOLLOW``) to mirror the
``os.walk(followlinks=False)`` walk discipline in ``reconcile_subtree``.

Symlink note: unlike Linux (which refuses ``user.*`` xattrs on symlinks with
``EPERM``), macOS allows setting xattrs on symlinks via the NOFOLLOW path.
A symlink matched by a rule therefore gets marked directly — the symlink
itself is marked, not its target.  This is the intentional macOS-vs-Linux
behavioral divergence documented in the spec.  ``reconcile._reconcile_path``'s
``PermissionError`` arm is dormant on macOS for this reason.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import subprocess
from pathlib import Path

import xattr

from . import require_absolute as _require_absolute

logger = logging.getLogger(__name__)

ATTR_LEGACY = "com.dropbox.ignored"
ATTR_FILEPROVIDER = "com.apple.fileprovider.ignore#P"
_MARKER_VALUE = b"1"
_DROPBOX_FILEPROVIDER_BUNDLE_ID = "com.getdropbox.dropbox.fileprovider"

# errno.ENOATTR (93) is macOS/BSD-specific; Python on Linux omits it. The
# defensive getattr keeps this module importable on Linux (where the unit
# tests run), falling back to the raw value.
_NO_ATTR_ERRNO = getattr(errno, "ENOATTR", 93)

# Cache of the detected (attr_names, summary) tuple so the per-file reconcile
# loop doesn't re-invoke `pluginkit` on every marker call. Module-local; reset
# to None for tests, or to re-detect after the user changes Dropbox sync modes
# (which itself requires a daemon restart — the cache outlives one daemon
# process by design, and the daemon's own restart semantics handle re-detection).
#
# The cached tuple is `(attr_names, summary)`:
#   attr_names: 1- or 2-element list. Single name in the decisive cases
#     (legacy vs. File Provider). Two names ([ATTR_LEGACY, ATTR_FILEPROVIDER])
#     in the genuinely-uncertain case where pluginkit is unavailable AND
#     info.json gave no decisive path signal — see `_detect()` below for
#     the always-write-both rationale (followup item 58).
#   summary: human-readable "<mode>: <reason>" string surfaced via
#     `detection_summary()` to the daemon's INFO log at startup and to
#     `dbxignore status` on darwin (followup item 37).
_decision_cache: tuple[list[str], str] | None = None


def _read_dropbox_paths_from_info() -> list[str]:
    """Read configured sync paths from ``~/.dropbox/info.json``.

    Returns a list because info.json can list multiple accounts (typically
    ``personal`` and ``business`` keys), each with its own ``path`` field.
    Returns an empty list if info.json is missing, malformed, or unreadable —
    callers should treat that as "no Dropbox configured" rather than as an
    error.
    """
    home = os.environ.get("HOME")
    if not home:
        return []
    info_path = Path(home) / ".dropbox" / "info.json"
    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    paths: list[str] = []
    for account in data.values():
        if isinstance(account, dict):
            p = account.get("path")
            if isinstance(p, str) and p:
                paths.append(p)
    return paths


def _pluginkit_extension_state() -> str:
    """Query Apple's PluginKit registry for Dropbox's File Provider extension.

    Returns one of:

    - ``"disabled"`` — line found with ``-`` prefix (user explicitly toggled
      the extension off via ``pluginkit -e ignore`` or System Settings →
      Login Items & Extensions). Dropbox falls back to legacy sync in this
      state.
    - ``"allowed"`` — line found, no ``-`` prefix (default state or ``+``
      prefix). The extension is registered and the OS is willing to dispatch
      File Provider events to it. Whether *this account* is actually using
      File Provider is a separate question answered by info.json's path field.
    - ``"not_registered"`` — no matching line. Either Dropbox.app isn't
      installed, or the version doesn't ship the File Provider extension.
    - ``"unknown"`` — pluginkit invocation errored (binary missing on
      non-macOS test hosts, hung past the timeout, or other OSError). Caller
      should treat as "can't decide from pluginkit, fall through to other
      signals."

    Output format verified against macOS Tahoe 26.4 / Dropbox 250.4 on
    2026-05-01: one line per matching extension, prefix character (or
    leading whitespace) indicates user-toggled state.
    """
    try:
        result = subprocess.run(  # noqa: S603,S607 — hardcoded args, no user data
            ["pluginkit", "-m", "-A", "-i", _DROPBOX_FILEPROVIDER_BUNDLE_ID],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "unknown"
    matching = [
        line for line in result.stdout.splitlines() if _DROPBOX_FILEPROVIDER_BUNDLE_ID in line
    ]
    if not matching:
        return "not_registered"
    for line in matching:
        bundle_idx = line.index(_DROPBOX_FILEPROVIDER_BUNDLE_ID)
        prefix = line[:bundle_idx]
        if "-" in prefix:
            return "disabled"
    return "allowed"


def _detect() -> tuple[list[str], str]:
    """Return ``(attr_names, summary)`` for the active Dropbox sync mode.

    Detection is **path-primary, pluginkit-disambiguating**:

    1. Read Dropbox's configured sync paths from ``~/.dropbox/info.json``
       (multi-account aware).  The ``path`` field is the user-level fact —
       it tells us which sync mechanism this account is actually using,
       which is what we need.
    2. Query pluginkit for the File Provider extension's user-toggled
       state (allowed / disabled / not_registered / unknown).
    3. Combine:

       - **Extension disabled** → legacy.  User explicitly opted out of
         File Provider; Dropbox falls back to legacy sync (or doesn't sync
         at all) regardless of where info.json's path points.
       - **Any path under** ``~/Library/CloudStorage/`` → File Provider.
         The common case for users who migrated.  Apple's File Provider
         framework manages this folder; if Dropbox is syncing there, it's
         in File Provider mode for that account.
       - **Path elsewhere + extension allowed** → File Provider (external
         drive).  Dropbox supports File Provider on mounted external
         drives via an eligibility-gated feature; the sync folder is on
         ``/Volumes/...`` but the framework handles it.
       - **Pluginkit unknown + no decisive path signal** → both attributes.
         Genuinely uncertain: pluginkit is unavailable (subprocess error,
         missing binary, hung query) and info.json gave no path under
         CloudStorage or /Volumes.  Write both ``com.dropbox.ignored`` and
         ``com.apple.fileprovider.ignore#P`` so whichever sync stack
         actually reads its own attribute name finds it; the other stack
         simply ignores the stray attribute (followup item 58).
       - **Otherwise** → legacy.  Confident default when extension is
         not_registered (pure legacy install) or allowed but no path is
         under CloudStorage / /Volumes (legacy install with the extension
         ambient on disk).

    Why path-primary rather than pluginkit-primary: PluginKit registration
    is a *system-level* fact (does macOS know about ``DropboxFileProvider.appex``?).
    The user-level fact (which mode is *this account* in?) lives in
    ``info.json``'s path field.  v0.4.0a4 conflated the two and
    misdetected users who had Dropbox.app installed but had declined the
    File Provider migration.  Path-primary fixes that.

    Cached on first call.  The race between concurrent first-callers
    under the daemon's ``ThreadPoolExecutor`` is benign — both compute
    the same value from the same on-disk + registry state, and Python's
    GIL makes the tuple assignment to the cache atomic.
    """
    global _decision_cache
    if _decision_cache is not None:
        return _decision_cache

    paths = _read_dropbox_paths_from_info()
    extension_state = _pluginkit_extension_state()

    if extension_state == "disabled":
        result = (
            [ATTR_LEGACY],
            "legacy: File Provider extension explicitly disabled",
        )
        _decision_cache = result
        logger.debug("Sync mode detection: %s", result[1])
        return result

    home = os.environ.get("HOME")
    if home:
        cloud_storage = Path(os.path.realpath(Path(home) / "Library" / "CloudStorage"))
        for p in paths:
            try:
                if Path(os.path.realpath(p)).is_relative_to(cloud_storage):
                    result = (
                        [ATTR_FILEPROVIDER],
                        f"file_provider: path under ~/Library/CloudStorage/ ({p})",
                    )
                    _decision_cache = result
                    logger.debug("Sync mode detection: %s", result[1])
                    return result
            except OSError:
                # realpath / is_relative_to syscall failure on a path entry —
                # skip and try the next one.
                continue

    # External-drive File Provider: path on `/Volumes/...` + extension allowed.
    # Scoped narrowly to the /Volumes prefix so we don't false-positive on
    # users who have Dropbox.app installed (so the extension is registered
    # in PluginKit) but who declined the File Provider migration and are
    # still on legacy sync from their home dir. Per Dropbox docs, File
    # Provider doesn't permit relocation outside `~/Library/CloudStorage/`
    # except for the eligibility-gated external-drive feature, which puts
    # the folder on a mounted `/Volumes/<DriveName>/...` path.
    if extension_state == "allowed":
        for p in paths:
            try:
                real_parts = Path(os.path.realpath(p)).parts
            except OSError:
                continue
            if len(real_parts) >= 3 and real_parts[1] == "Volumes":
                result = (
                    [ATTR_FILEPROVIDER],
                    f"file_provider: external drive ({p})",
                )
                _decision_cache = result
                logger.debug("Sync mode detection: %s", result[1])
                return result

    if extension_state == "unknown":
        result = (
            [ATTR_LEGACY, ATTR_FILEPROVIDER],
            "both: pluginkit unavailable; writing both attributes defensively",
        )
        _decision_cache = result
        logger.debug("Sync mode detection: %s", result[1])
        return result

    result = (
        [ATTR_LEGACY],
        f"legacy: default (paths={paths}, extension_state={extension_state})",
    )
    _decision_cache = result
    logger.debug("Sync mode detection: %s", result[1])
    return result


def _detected_attr_names() -> list[str]:
    """Return the list of xattr names to read/write/clear on this system.

    Single-element list in decisive cases (legacy or File Provider). Two
    elements ([legacy, File Provider]) in the genuinely-uncertain case —
    see ``_detect()`` for when each shape applies.
    """
    return _detect()[0]


def _detected_attr_name() -> str:
    """Return the *first* xattr name from `_detected_attr_names()`.

    Back-compat shim for tests written when detection always produced one
    name. Production callers (``is_ignored``/``set_ignored``/``clear_ignored``)
    iterate ``_detected_attr_names()``.
    """
    return _detected_attr_names()[0]


def detection_summary() -> str:
    """Human-readable summary of the sync mode decision: ``<mode>: <reason>``.

    Surfaced at INFO log at daemon startup and in ``dbxignore status`` on
    darwin (followup item 37).  Caches with ``_detect()`` — calling once
    per process is enough.
    """
    return _detect()[1]


def is_ignored(path: Path) -> bool:
    """Return True if ``path`` bears a non-empty Dropbox-ignore xattr.

    Reads whichever attribute name(s) Dropbox is watching on this host —
    auto-detected via `_detected_attr_names()`. In single-attr cases reads
    one name; in the dual-attr case reads both and returns True on the
    first non-empty hit.  ``path`` must be absolute (``ValueError``
    otherwise).  Returns False when no detected attribute is set on the
    path.  Raises ``FileNotFoundError`` if the path itself does not exist
    (ENOENT).
    """
    _require_absolute(path)
    for name in _detected_attr_names():
        try:
            value = xattr.getxattr(str(path), name, symlink=True)
        except OSError as exc:
            if exc.errno == _NO_ATTR_ERRNO:
                continue
            if exc.errno == errno.ENOENT:
                raise FileNotFoundError(str(path)) from exc
            raise
        if value:
            return True
    return False


def set_ignored(path: Path) -> None:
    """Mark ``path`` as ignored by Dropbox.

    Writes whichever attribute name(s) Dropbox is watching on this host —
    auto-detected via `_detected_attr_names()`.  ``path`` must be absolute
    (``ValueError`` otherwise).  On macOS, the NOFOLLOW path
    (``symlink=True`` on the xattr wrapper) allows marking symlinks
    directly — unlike Linux where the kernel raises ``EPERM``.

    In the dual-attr case (pluginkit unavailable + no decisive path
    signal — see `_detect()`), writes both names; if the first call
    succeeds and the second raises, the partial state propagates with
    the second's exception, mirroring the single-attr contract that
    set_ignored is either fully successful or raises.
    """
    _require_absolute(path)
    for name in _detected_attr_names():
        xattr.setxattr(str(path), name, _MARKER_VALUE, symlink=True)


def clear_ignored(path: Path) -> None:
    """Remove the Dropbox ignore marker from ``path`` (no-op if absent or gone).

    Removes whichever attribute name(s) Dropbox is watching on this host —
    auto-detected via `_detected_attr_names()`.  ``path`` must be absolute
    (``ValueError`` otherwise).  Per-attribute ENOATTR is a no-op (xattr
    was simply not set).  Path-level ENOENT short-circuits the loop —
    once the path is gone, no further removexattr calls would succeed.
    Other ``OSError`` subclasses propagate.
    """
    _require_absolute(path)
    for name in _detected_attr_names():
        try:
            xattr.removexattr(str(path), name, symlink=True)
        except OSError as exc:
            if exc.errno == _NO_ATTR_ERRNO:
                logger.debug("clear_ignored: xattr %s absent on %s", name, path)
                continue
            if exc.errno == errno.ENOENT:
                logger.debug("clear_ignored: path gone: %s", path)
                return
            raise

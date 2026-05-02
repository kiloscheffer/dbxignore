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

logger = logging.getLogger(__name__)

ATTR_LEGACY = "com.dropbox.ignored"
ATTR_FILEPROVIDER = "com.apple.fileprovider.ignore#P"
_MARKER_VALUE = b"1"
_DROPBOX_FILEPROVIDER_BUNDLE_ID = "com.getdropbox.dropbox.fileprovider"

# errno.ENOATTR (93) is macOS/BSD-specific; Python on Linux omits it. The
# defensive getattr keeps this module importable on Linux (where the unit
# tests run), falling back to the raw value.
_NO_ATTR_ERRNO = getattr(errno, "ENOATTR", 93)

# Cache of the detected attribute name so the per-file reconcile loop
# doesn't re-invoke `pluginkit` on every marker call. Module-local; reset
# to None for tests, or to re-detect after the user changes Dropbox sync
# modes (which itself requires a daemon restart — the cache outlives one
# daemon process by design, and the daemon's own restart semantics handle
# re-detection).
_attr_name_cache: str | None = None


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
        line for line in result.stdout.splitlines()
        if _DROPBOX_FILEPROVIDER_BUNDLE_ID in line
    ]
    if not matching:
        return "not_registered"
    for line in matching:
        bundle_idx = line.index(_DROPBOX_FILEPROVIDER_BUNDLE_ID)
        prefix = line[:bundle_idx]
        if "-" in prefix:
            return "disabled"
    return "allowed"


def _detected_attr_name() -> str:
    """Return the xattr name Dropbox watches on this system.

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
       - **Otherwise** → legacy.  Defensive default for: no info.json
         (Dropbox not installed); info.json paths all outside CloudStorage
         and extension not_registered (pure legacy install); pluginkit
         unknown and no path-existence fallback signal.

    Why path-primary rather than pluginkit-primary: PluginKit registration
    is a *system-level* fact (does macOS know about ``DropboxFileProvider.appex``?).
    The user-level fact (which mode is *this account* in?) lives in
    ``info.json``'s path field.  v0.4.0a4 conflated the two and
    misdetected users who had Dropbox.app installed but had declined the
    File Provider migration.  Path-primary fixes that.

    Cached on first call.  The race between concurrent first-callers
    under the daemon's ``ThreadPoolExecutor`` is benign — both compute
    the same value from the same on-disk + registry state, and Python's
    GIL makes the string assignment to the cache atomic.
    """
    global _attr_name_cache
    if _attr_name_cache is not None:
        return _attr_name_cache

    paths = _read_dropbox_paths_from_info()
    extension_state = _pluginkit_extension_state()

    if extension_state == "disabled":
        _attr_name_cache = ATTR_LEGACY
        logger.debug(
            "Detected legacy mode: File Provider extension explicitly disabled"
        )
        return _attr_name_cache

    home = os.environ.get("HOME")
    if home:
        cloud_storage = Path(os.path.realpath(Path(home) / "Library" / "CloudStorage"))
        for p in paths:
            try:
                if Path(os.path.realpath(p)).is_relative_to(cloud_storage):
                    _attr_name_cache = ATTR_FILEPROVIDER
                    logger.debug(
                        "Detected File Provider mode: %s under ~/Library/CloudStorage/",
                        p,
                    )
                    return _attr_name_cache
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
                _attr_name_cache = ATTR_FILEPROVIDER
                logger.debug(
                    "Detected File Provider mode (external drive): %s", p
                )
                return _attr_name_cache

    _attr_name_cache = ATTR_LEGACY
    logger.debug(
        "Detected legacy mode (default): paths=%s, extension_state=%s",
        paths, extension_state,
    )
    return _attr_name_cache


def _require_absolute(path: Path) -> None:
    if not path.is_absolute():
        raise ValueError(f"markers requires an absolute path; got {path!r}")


def is_ignored(path: Path) -> bool:
    """Return True if ``path`` bears a non-empty Dropbox-ignore xattr.

    Reads whichever attribute name Dropbox is watching on this host
    (legacy or File Provider — auto-detected).  ``path`` must be
    absolute (``ValueError`` otherwise).  Returns False when the xattr
    is absent (ENOATTR).  Raises ``FileNotFoundError`` if the path
    itself does not exist (ENOENT).
    """
    _require_absolute(path)
    try:
        value = xattr.getxattr(str(path), _detected_attr_name(), symlink=True)
    except OSError as exc:
        if exc.errno == _NO_ATTR_ERRNO:
            return False
        if exc.errno == errno.ENOENT:
            raise FileNotFoundError(str(path)) from exc
        raise
    return bool(value)


def set_ignored(path: Path) -> None:
    """Mark ``path`` as ignored by Dropbox.

    Writes whichever attribute name Dropbox is watching on this host
    (legacy or File Provider — auto-detected).  ``path`` must be
    absolute (``ValueError`` otherwise).  On macOS, the NOFOLLOW path
    (``symlink=True`` on the xattr wrapper) allows marking symlinks
    directly — unlike Linux where the kernel raises ``EPERM``.
    """
    _require_absolute(path)
    xattr.setxattr(str(path), _detected_attr_name(), _MARKER_VALUE, symlink=True)


def clear_ignored(path: Path) -> None:
    """Remove the Dropbox ignore marker from ``path`` (no-op if absent or gone).

    Removes whichever attribute name Dropbox is watching on this host
    (legacy or File Provider — auto-detected).  ``path`` must be
    absolute (``ValueError`` otherwise).  Absent xattr (ENOATTR) or
    missing path (ENOENT) debug-logs and returns.  Other ``OSError``
    subclasses propagate.
    """
    _require_absolute(path)
    try:
        xattr.removexattr(str(path), _detected_attr_name(), symlink=True)
    except OSError as exc:
        if exc.errno == _NO_ATTR_ERRNO:
            logger.debug("clear_ignored: xattr absent on %s", path)
            return
        if exc.errno == errno.ENOENT:
            logger.debug("clear_ignored: path gone: %s", path)
            return
        raise

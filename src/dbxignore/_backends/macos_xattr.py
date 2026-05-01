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


def _detected_attr_name() -> str:
    """Return the xattr name Dropbox watches on this system.

    Detection strategy:

    **Primary (path-independent):** query Apple's PluginKit registry via
    ``pluginkit -m -A -i com.getdropbox.dropbox.fileprovider``. If the
    Dropbox File Provider extension is registered AND not user-disabled,
    the host is in File Provider mode regardless of where the Dropbox
    sync folder lives. Output format on macOS Tahoe 26 / Dropbox 250+::

        "     com.getdropbox.dropbox.fileprovider(250.4.3245)"   <- enabled
        "-    com.getdropbox.dropbox.fileprovider(250.4.3245)"   <- user-disabled

    The ``-`` prefix indicates the user toggled the extension off via
    ``pluginkit -e ignore`` or System Settings → Login Items &
    Extensions. In that state Dropbox falls back to legacy sync.

    **Fallback (when pluginkit is absent / hangs / errors):** check for
    the canonical File Provider Dropbox folder at
    ``~/Library/CloudStorage/Dropbox/``. Less robust than the primary
    path (misses users who relocated the File Provider folder via
    Finder) but covers the common case when subprocess I/O isn't
    available — e.g. test environments where pluginkit isn't on PATH.

    Cached on first call. The race between concurrent first-callers
    under the daemon's ``ThreadPoolExecutor`` is benign — both compute
    the same value from the same registry state, and Python's GIL makes
    the string assignment to the cache atomic.
    """
    global _attr_name_cache
    if _attr_name_cache is not None:
        return _attr_name_cache

    # Primary: pluginkit registry query
    try:
        result = subprocess.run(  # noqa: S603,S607 — hardcoded args, no user data
            ["pluginkit", "-m", "-A", "-i", _DROPBOX_FILEPROVIDER_BUNDLE_ID],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        for line in result.stdout.splitlines():
            if _DROPBOX_FILEPROVIDER_BUNDLE_ID not in line:
                continue
            bundle_idx = line.index(_DROPBOX_FILEPROVIDER_BUNDLE_ID)
            prefix = line[:bundle_idx]
            # `-` prefix = user explicitly disabled extension via System
            # Settings; Dropbox falls back to legacy sync (or doesn't sync
            # at all). Whitespace or `+` prefix = default-state or
            # explicitly-enabled — File Provider is active.
            _attr_name_cache = ATTR_LEGACY if "-" in prefix else ATTR_FILEPROVIDER
            logger.debug(
                "Detected Dropbox attr name via pluginkit: %s", _attr_name_cache
            )
            return _attr_name_cache
        # No matching line: extension not registered. Fall through.
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        # pluginkit absent (non-macOS test host), hung, or errored.
        pass

    # Fallback: default-path heuristic
    home = os.environ.get("HOME")
    if home and (Path(home) / "Library" / "CloudStorage" / "Dropbox").exists():
        _attr_name_cache = ATTR_FILEPROVIDER
    else:
        _attr_name_cache = ATTR_LEGACY
    logger.debug("Detected Dropbox attr name via fallback: %s", _attr_name_cache)
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

"""Read/write the Dropbox 'ignore' xattr on macOS.

Dropbox on macOS treats a path as ignored if it carries the extended
attribute ``com.dropbox.ignored`` with any non-empty value.  (Note: macOS
xattrs have no ``user.`` namespace prefix — that is a Linux convention.)

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
from pathlib import Path

import xattr

logger = logging.getLogger(__name__)

ATTR_NAME = "com.dropbox.ignored"
_MARKER_VALUE = b"1"

# errno.ENOATTR (93) is macOS/BSD-specific; Python on Linux omits it. The
# defensive getattr keeps this module importable on Linux (where the unit
# tests run), falling back to the raw value.
_NO_ATTR_ERRNO = getattr(errno, "ENOATTR", 93)


def _require_absolute(path: Path) -> None:
    if not path.is_absolute():
        raise ValueError(f"markers requires an absolute path; got {path!r}")


def is_ignored(path: Path) -> bool:
    """Return True if ``path`` bears a non-empty com.dropbox.ignored xattr.

    ``path`` must be absolute (``ValueError`` otherwise).  Returns False when
    the xattr is absent (ENOATTR).  Raises ``FileNotFoundError`` if the path
    itself does not exist (ENOENT).
    """
    _require_absolute(path)
    try:
        value = xattr.getxattr(str(path), ATTR_NAME, symlink=True)
    except OSError as exc:
        if exc.errno == _NO_ATTR_ERRNO:
            return False
        if exc.errno == errno.ENOENT:
            raise FileNotFoundError(str(path)) from exc
        raise
    return bool(value)


def set_ignored(path: Path) -> None:
    """Mark ``path`` as ignored by Dropbox.

    ``path`` must be absolute (``ValueError`` otherwise).  On macOS, the
    NOFOLLOW path (``symlink=True`` on the xattr wrapper) allows marking
    symlinks directly — unlike Linux where the kernel raises ``EPERM``.
    """
    _require_absolute(path)
    xattr.setxattr(str(path), ATTR_NAME, _MARKER_VALUE, symlink=True)


def clear_ignored(path: Path) -> None:
    """Remove the Dropbox ignore marker from ``path`` (no-op if absent or gone).

    ``path`` must be absolute (``ValueError`` otherwise).  Absent xattr
    (ENOATTR) or missing path (ENOENT) debug-logs and returns.  Other
    ``OSError`` subclasses propagate.
    """
    _require_absolute(path)
    try:
        xattr.removexattr(str(path), ATTR_NAME, symlink=True)
    except OSError as exc:
        if exc.errno == _NO_ATTR_ERRNO:
            logger.debug("clear_ignored: xattr absent on %s", path)
            return
        if exc.errno == errno.ENOENT:
            logger.debug("clear_ignored: path gone: %s", path)
            return
        raise

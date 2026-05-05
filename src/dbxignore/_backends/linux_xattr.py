"""Read/write the Dropbox 'ignore' user-namespace xattr on Linux.

Dropbox on Linux treats a path as ignored if it carries the extended
attribute ``user.com.dropbox.ignored`` with any non-empty value.
This module uses ``os.setxattr`` / ``getxattr`` / ``removexattr`` with
``follow_symlinks=False`` to mirror the ``os.walk(followlinks=False)``
walk discipline in ``reconcile_subtree``.

Symlink note: the Linux VFS refuses ``user.*`` xattrs on symlinks with
``EPERM``. A symlink matched by a rule therefore surfaces as a
``PermissionError`` from ``set_ignored``/``clear_ignored``, which
``_reconcile_path`` already catches (logs WARNING, appends to
``Report.errors``, continues). ``is_ignored`` on a symlink returns
``False`` cleanly because ``getxattr`` on a user.* xattr of a symlink
returns ``ENODATA``.
"""

from __future__ import annotations

import errno
import logging
import os
from pathlib import Path

from . import require_absolute as _require_absolute

logger = logging.getLogger(__name__)

ATTR_NAME = "user.com.dropbox.ignored"
_MARKER_VALUE = b"1"

# errno.ENOATTR is a BSD-ism (used on macOS). Linux uses ENODATA (61).
# Python does not expose ENOATTR on Linux, so getattr falls back to
# ENODATA; the set deduplicates naturally. The defensive form stays in
# case a future Python/platform adds ENOATTR as a distinct value.
_NO_ATTR_ERRNOS = {errno.ENODATA, getattr(errno, "ENOATTR", errno.ENODATA)}


def is_ignored(path: Path) -> bool:
    """Return True if ``path`` bears a non-empty user.com.dropbox.ignored xattr.

    ``path`` must be absolute (``ValueError`` otherwise). Returns False when
    the xattr is absent (ENODATA). Raises ``FileNotFoundError`` if the path
    itself does not exist (ENOENT).
    """
    _require_absolute(path)
    try:
        value = os.getxattr(os.fspath(path), ATTR_NAME, follow_symlinks=False)
    except OSError as exc:
        if exc.errno in _NO_ATTR_ERRNOS:
            return False
        if exc.errno == errno.ENOENT:
            raise FileNotFoundError(str(path)) from exc
        raise
    return bool(value)


def set_ignored(path: Path) -> None:
    """Mark ``path`` as ignored by Dropbox.

    ``path`` must be absolute (``ValueError`` otherwise). Raises
    ``PermissionError`` if the kernel rejects the operation — notably on
    symlinks, since Linux refuses ``user.*`` xattrs on symlinks. Callers
    (notably ``reconcile._reconcile_path``) catch both via the existing
    ``FileNotFoundError`` / ``PermissionError`` arms per the failure-mode
    contract.
    """
    _require_absolute(path)
    os.setxattr(os.fspath(path), ATTR_NAME, _MARKER_VALUE, follow_symlinks=False)


def clear_ignored(path: Path) -> None:
    """Remove the Dropbox ignore marker from ``path`` (no-op if absent or gone).

    ``path`` must be absolute (``ValueError`` otherwise). Absent xattr
    (ENODATA) or missing path (ENOENT) debug-logs and returns. Other OSError
    subclasses — including ``PermissionError`` on symlinks — propagate.
    """
    _require_absolute(path)
    try:
        os.removexattr(os.fspath(path), ATTR_NAME, follow_symlinks=False)
    except OSError as exc:
        if exc.errno in _NO_ATTR_ERRNOS:
            logger.debug("clear_ignored: xattr absent on %s", path)
            return
        if exc.errno == errno.ENOENT:
            logger.debug("clear_ignored: path gone: %s", path)
            return
        raise

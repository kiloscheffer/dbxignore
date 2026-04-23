"""Read/write the Dropbox 'ignore' NTFS alternate data stream.

Dropbox treats a file or directory as ignored if it has an NTFS alternate
data stream named ``com.dropbox.ignored`` containing any non-empty value.
This module exposes three operations via Python's built-in ``open()``,
which on Windows passes the ``path:streamname`` syntax through to
``CreateFileW`` at the kernel level — so no subprocess or pywin32 needed.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

STREAM_NAME = "com.dropbox.ignored"
_MARKER_VALUE = "1"
_LONG_PATH_PREFIX = "\\\\?\\"


def _stream_path(path: Path) -> str:
    """Return the ``\\\\?\\…:streamname`` path for ``path``.

    ``path`` must be absolute — the ``\\\\?\\`` long-path prefix is only
    meaningful before a full path. Callers normalize at the CLI/daemon
    boundary; relative paths here are a caller bug.
    """
    if not path.is_absolute():
        raise ValueError(f"markers requires an absolute path; got {path!r}")
    return f"{_LONG_PATH_PREFIX}{path}:{STREAM_NAME}"


def is_ignored(path: Path) -> bool:
    """Return True if ``path`` bears a non-empty com.dropbox.ignored stream."""
    try:
        with open(_stream_path(path), encoding="ascii") as f:
            return bool(f.read(1))
    except FileNotFoundError:
        return False


def set_ignored(path: Path) -> None:
    """Mark ``path`` as ignored by Dropbox.

    Raises ``FileNotFoundError`` if ``path`` vanished before the write;
    raises ``PermissionError`` if the stream cannot be written. Callers
    (notably ``reconcile_subtree``) catch and log both per the design's
    failure-mode contract.
    """
    with open(_stream_path(path), "w", encoding="ascii") as f:
        f.write(_MARKER_VALUE)


def clear_ignored(path: Path) -> None:
    """Remove the Dropbox ignore marker from ``path`` (no-op if absent)."""
    try:
        os.remove(_stream_path(path))
    except FileNotFoundError:
        logger.debug("clear_ignored: stream absent or path gone: %s", path)

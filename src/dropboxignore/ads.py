"""Read/write the Dropbox 'ignore' NTFS alternate data stream.

Dropbox treats a file or directory as ignored if it has an NTFS alternate
data stream named ``com.dropbox.ignored`` containing any non-empty value.
This module exposes three operations via Python's built-in ``open()``,
which on Windows passes the ``path:streamname`` syntax through to
``CreateFileW`` at the kernel level — so no subprocess or pywin32 needed.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

STREAM_NAME = "com.dropbox.ignored"
_MARKER_VALUE = "1"
_LONG_PATH_PREFIX = "\\\\?\\"


def _stream_path(path: Path) -> str:
    """Return the absolute ``\\\\?\\…:streamname`` path for ``path``."""
    absolute = path.resolve()
    return f"{_LONG_PATH_PREFIX}{absolute}:{STREAM_NAME}"


def is_ignored(path: Path) -> bool:
    """Return True if ``path`` currently bears the ignore marker."""
    try:
        with open(_stream_path(path), encoding="ascii") as f:
            return f.read(1) == _MARKER_VALUE
    except FileNotFoundError:
        return False


def set_ignored(path: Path) -> None:
    """Mark ``path`` as ignored by Dropbox."""
    with open(_stream_path(path), "w", encoding="ascii") as f:
        f.write(_MARKER_VALUE)


def clear_ignored(path: Path) -> None:
    """Remove the Dropbox ignore marker from ``path`` (no-op if absent)."""
    with contextlib.suppress(FileNotFoundError):
        os.remove(_stream_path(path))

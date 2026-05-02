"""Reconcile the filesystem's ignore markers with the current rule set."""

from __future__ import annotations

import errno
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from dbxignore import markers
from dbxignore.rules import IGNORE_FILENAME, RuleCache

logger = logging.getLogger(__name__)


@dataclass
class Report:
    marked: int = 0
    cleared: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)
    duration_s: float = 0.0


def reconcile_subtree(root: Path, subdir: Path, cache: RuleCache) -> Report:
    start = time.perf_counter()
    report = Report()

    root = root.resolve()
    subdir = subdir.resolve()
    if subdir != root and not subdir.is_relative_to(root):
        raise ValueError(f"subdir {subdir} is not under root {root}")

    # If subdir itself ends up ignored, don't descend.
    if _reconcile_path(subdir, cache, report):
        report.duration_s = time.perf_counter() - start
        return report

    for current, dirnames, filenames in os.walk(subdir, followlinks=False):
        current_path = Path(current)
        # Reconcile each subdirectory; if it ends up ignored, prune it from
        # the walk (os.walk honors in-place modification of dirnames).
        dirnames[:] = [
            name for name in dirnames
            if not _reconcile_path(current_path / name, cache, report)
        ]
        for name in filenames:
            _reconcile_path(current_path / name, cache, report)

    report.duration_s = time.perf_counter() - start
    return report


def _reconcile_path(path: Path, cache: RuleCache, report: Report) -> bool | None:
    """Reconcile one path's ignore marker with the current rule set.

    Returns the path's final ignored state (True/False), or None if it could
    not be determined (read error or vanished path). The return value drives
    subtree pruning in reconcile_subtree.
    """
    try:
        should_ignore = cache.match(path)
        currently_ignored = markers.is_ignored(path)
    except FileNotFoundError:
        logger.debug("Path vanished during reconcile: %s", path)
        return None
    except PermissionError as exc:
        logger.warning("Permission denied reading %s: %s", path, exc)
        report.errors.append((path, f"read: {exc}"))
        return None
    except OSError as exc:
        # Catch-all for read-side I/O errors that aren't FileNotFoundError or
        # PermissionError — e.g. EIO on a flaky network drive, or ENOTSUP
        # from getxattr on a filesystem that doesn't support xattrs at all.
        # Without this arm the error would escape `_reconcile_path` and kill
        # the per-root sweep worker silently (followup item 21). Mirrors the
        # write-side ENOTSUP/EOPNOTSUPP handling shape.
        logger.warning(
            "I/O error reading marker on %s: errno=%s %s", path, exc.errno, exc
        )
        report.errors.append((path, f"read: errno={exc.errno} {exc}"))
        return None

    try:
        if should_ignore and not currently_ignored:
            markers.set_ignored(path)
            report.marked += 1
            return True
        if currently_ignored and not should_ignore:
            if path.name == IGNORE_FILENAME:
                logger.warning(
                    ".dropboxignore at %s was marked ignored; overriding back to synced",
                    path,
                )
            markers.clear_ignored(path)
            report.cleared += 1
            return False
    except FileNotFoundError:
        logger.debug("Path vanished before marker write: %s", path)
        return None
    except PermissionError as exc:
        logger.warning("Permission denied writing marker on %s: %s", path, exc)
        report.errors.append((path, f"write: {exc}"))
        # Write failed: the marker state is still whatever we read.
        return currently_ignored
    except OSError as exc:
        if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            logger.warning(
                "Filesystem does not support ignore markers on %s: %s", path, exc
            )
            report.errors.append((path, f"unsupported: {exc}"))
            # Mirror PermissionError's return: preserve last-known marker
            # state so subtree pruning fires correctly when an already-
            # marked directory's clear fails (item #41).
            return currently_ignored
        raise

    return currently_ignored

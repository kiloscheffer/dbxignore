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
    # Populated only in dry-run mode. In normal mode `marked` and `cleared`
    # count actual mutations; in dry-run mode they count would-have-been
    # mutations and these lists carry the per-path detail for CLI output.
    # Steady-state daemon sweeps are always dry_run=False, so these stay
    # empty and the per-sweep memory footprint is unchanged (followup item 64).
    would_mark: list[Path] = field(default_factory=list)
    would_clear: list[Path] = field(default_factory=list)


def reconcile_subtree(
    root: Path, subdir: Path, cache: RuleCache, *, dry_run: bool = False
) -> Report:
    """Reconcile ``subdir`` under ``root`` with the current rule set.

    Both ``root`` and ``subdir`` MUST be absolute and pre-resolved by the
    caller — resolution is the CLI/daemon boundary's responsibility (see
    CLAUDE.md "Resolve at the CLI/daemon boundary, never inside the cache
    or markers layer"). ``Path.resolve()`` on Windows is a per-call
    syscall that dominated sweep wall-clock before being hoisted.

    When ``dry_run`` is True, marker mutations are skipped: ``markers.set_ignored``
    and ``markers.clear_ignored`` are NOT called. Subtree pruning still
    fires based on the would-be ignored state, so the dry-run preview is
    structurally identical to what a real reconcile would do. Counters
    (``report.marked`` / ``report.cleared``) reflect *would-have-been*
    mutations; per-path detail lives in ``report.would_mark`` /
    ``report.would_clear`` for CLI consumption.
    """
    start = time.perf_counter()
    report = Report()

    if subdir != root and not subdir.is_relative_to(root):
        raise ValueError(f"subdir {subdir} is not under root {root}")

    # If subdir itself ends up ignored, don't descend.
    if _reconcile_path(subdir, cache, report, dry_run=dry_run):
        report.duration_s = time.perf_counter() - start
        return report

    for current, dirnames, filenames in os.walk(subdir, followlinks=False):
        current_path = Path(current)
        # Reconcile each subdirectory; if it ends up ignored, prune it from
        # the walk (os.walk honors in-place modification of dirnames).
        dirnames[:] = [
            name
            for name in dirnames
            if not _reconcile_path(current_path / name, cache, report, dry_run=dry_run)
        ]
        for name in filenames:
            _reconcile_path(current_path / name, cache, report, dry_run=dry_run)

    report.duration_s = time.perf_counter() - start
    return report


def _reconcile_path(
    path: Path, cache: RuleCache, report: Report, *, dry_run: bool = False
) -> bool | None:
    """Reconcile one path's ignore marker with the current rule set.

    Returns the path's final ignored state (True/False), or None if it could
    not be determined (read error or vanished path). The return value drives
    subtree pruning in reconcile_subtree.

    When ``dry_run`` is True, the would-be marker mutation is recorded in
    ``report.would_mark`` / ``report.would_clear`` instead of being applied;
    the read side still runs (so we can decide what would change).
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
        logger.warning("I/O error reading marker on %s: errno=%s %s", path, exc.errno, exc)
        report.errors.append((path, f"read: errno={exc.errno} {exc}"))
        return None

    try:
        if should_ignore and not currently_ignored:
            if not dry_run:
                markers.set_ignored(path)
            else:
                report.would_mark.append(path)
            report.marked += 1
            return True
        if currently_ignored and not should_ignore:
            if path.name == IGNORE_FILENAME:
                logger.warning(
                    ".dropboxignore at %s was marked ignored; overriding back to synced",
                    path,
                )
            if not dry_run:
                markers.clear_ignored(path)
            else:
                report.would_clear.append(path)
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
        # Symmetric to the read-side broad-OSError arm (item #21). Tolerates
        # transient I/O errors (EIO on network drives, ENOSPC on quota-full
        # disks, etc.) without killing the per-root sweep worker. Other
        # exception types (real bugs, e.g. AttributeError, TypeError) still
        # propagate.
        if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            logger.warning("Filesystem does not support ignore markers on %s: %s", path, exc)
            report.errors.append((path, f"unsupported: {exc}"))
        else:
            logger.warning("I/O error writing marker on %s: errno=%s %s", path, exc.errno, exc)
            report.errors.append((path, f"write: errno={exc.errno} {exc}"))
        # Preserve last-known marker state so subtree pruning fires when an
        # already-marked directory's write fails. Mirrors PermissionError arm.
        return currently_ignored

    return currently_ignored

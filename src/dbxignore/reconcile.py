"""Reconcile the filesystem's ignore markers with the current rule set."""

from __future__ import annotations

import errno
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import threading

from dbxignore import markers
from dbxignore._logging import timed_debug
from dbxignore.rules import RuleCache, is_ignore_filename

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
    # empty and the per-sweep memory footprint is unchanged.
    would_mark: list[Path] = field(default_factory=list)
    would_clear: list[Path] = field(default_factory=list)


def reconcile_subtree(
    root: Path,
    subdir: Path,
    cache: RuleCache,
    *,
    dry_run: bool = False,
    descend: bool = True,
    stop_event: threading.Event | None = None,
) -> Report:
    """Reconcile ``subdir`` under ``root`` with the current rule set.

    Both ``root`` and ``subdir`` MUST be absolute and normalized at the
    CLI/daemon boundary — the daemon resolves roots upfront via
    ``_discover_roots()`` (avoiding a per-walk ``Path.resolve()`` syscall
    that previously dominated sweep wall-clock on Windows). The CLI's
    path-taking verbs may pass symlink-preserving normalized paths:
    containment check below is purely lexical and tolerates either
    form. The ``ValueError`` raised on out-of-root ``subdir`` is the
    caller's responsibility to avoid; misuse is a programming error.

    When ``dry_run`` is True, marker mutations are skipped: ``markers.set_ignored``
    and ``markers.clear_ignored`` are NOT called. Subtree pruning still
    fires based on the would-be ignored state, so the dry-run preview is
    structurally identical to what a real reconcile would do. Counters
    (``report.marked`` / ``report.cleared``) reflect *would-have-been*
    mutations; per-path detail lives in ``report.would_mark`` /
    ``report.would_clear`` for CLI consumption.

    When ``descend`` is False, only ``subdir`` itself is reconciled; the
    ``os.walk`` is skipped. Used by ``daemon._sweep_once`` to handle the
    root-path reconcile separately from the per-top-level-child fan-out;
    the caller submits one ``descend=False`` call
    per root plus one ``descend=True`` call per top-level child to a single
    ``ThreadPoolExecutor``, parallelizing the walk across subdirs.

    When ``stop_event`` is supplied and gets set during the walk, the walk
    breaks out at the next directory or file boundary. The ``Report``
    returned has accurate counts for what completed before the break;
    convergence (next sweep over the same paths) finishes the rest. Used
    by the daemon's initial-sweep worker to support cooperative
    cancellation on SIGTERM.
    """
    start = time.perf_counter()
    report = Report()
    # DEBUG-level timing log. Pairs with the `done` log below to measure
    # subtree-walk wall-clock. Under AV scanning, this can be the dominant
    # cost on Windows runners. No-op cost when DBXIGNORE_LOG_LEVEL != DEBUG.
    logger.debug(
        "reconcile_subtree start subdir=%s dry_run=%s descend=%s", subdir, dry_run, descend
    )

    if subdir != root and not subdir.is_relative_to(root):
        raise ValueError(f"subdir {subdir} is not under root {root}")

    # When subdir is itself a symlink, force descend=False. The
    # "symlinks are leaves" invariant says markers attach to the link
    # object, not the target — and `os.walk(top, followlinks=False)`
    # still follows the walk root when ``top`` IS a symlink (the flag
    # only gates subdirectory symlinks encountered during traversal).
    # Without this guard, a descend=True walk on a symlinked directory
    # would traverse the link target — potentially outside any
    # Dropbox tree. Mirrors `daemon._sweep_once`'s per-child symlink
    # guard at the dispatch site and `_walk_marked_paths`'s
    # explicit short-circuit in cli.py.
    if descend and subdir.is_symlink():
        descend = False

    # If subdir itself ends up ignored, don't descend. Also short-circuits
    # when descend=False — the caller has split the walk across siblings
    # and only wants the path-only reconcile here.
    subdir_ignored = _reconcile_path(subdir, cache, report, dry_run=dry_run)
    if subdir_ignored or not descend:
        report.duration_s = time.perf_counter() - start
        logger.debug(
            "reconcile_subtree done subdir=%s duration=%.4fs marked=%d cleared=%d errors=%d",
            subdir,
            report.duration_s,
            report.marked,
            report.cleared,
            len(report.errors),
        )
        return report

    for current, dirnames, filenames in os.walk(subdir, followlinks=False):
        if stop_event is not None and stop_event.is_set():
            break
        current_path = Path(current)
        # Reconcile each subdirectory; if it ends up ignored, prune it from
        # the walk (os.walk honors in-place modification of dirnames).
        # Use a loop rather than a comprehension so stop_event can interrupt
        # mid-list — a flat directory with thousands of children would otherwise
        # process every sibling before the next os.walk iteration check fired.
        keep: list[str] = []
        for name in dirnames:
            if stop_event is not None and stop_event.is_set():
                break
            if not _reconcile_path(current_path / name, cache, report, dry_run=dry_run):
                keep.append(name)
        dirnames[:] = keep
        if stop_event is not None and stop_event.is_set():
            break
        for name in filenames:
            if stop_event is not None and stop_event.is_set():
                break
            _reconcile_path(current_path / name, cache, report, dry_run=dry_run)

    report.duration_s = time.perf_counter() - start
    logger.debug(
        "reconcile_subtree done subdir=%s duration=%.4fs marked=%d cleared=%d errors=%d",
        subdir,
        report.duration_s,
        report.marked,
        report.cleared,
        len(report.errors),
    )
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
        # the per-root sweep worker silently. Mirrors the
        # write-side ENOTSUP/EOPNOTSUPP handling shape.
        logger.warning("I/O error reading marker on %s: errno=%s %s", path, exc.errno, exc)
        report.errors.append((path, f"read: errno={exc.errno} {exc}"))
        return None

    try:
        if should_ignore and not currently_ignored:
            if not dry_run:
                # DEBUG-level timing log. Measures NTFS ADS write latency per
                # path; on Windows this is the layer most likely to be slowed by
                # Defender real-time scanning. ``timed_debug`` gates the
                # ``time.perf_counter()`` calls on the logger level so the
                # per-mutation cost is zero in production INFO config.
                with timed_debug(logger, "set_ignored path=%s", path):
                    markers.set_ignored(path)
            else:
                report.would_mark.append(path)
            report.marked += 1
            return True
        if currently_ignored and not should_ignore:
            if is_ignore_filename(path.name):
                logger.warning(
                    ".dropboxignore at %s was marked ignored; overriding back to synced",
                    path,
                )
            if not dry_run:
                with timed_debug(logger, "clear_ignored path=%s", path):
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
        # Symmetric to the read-side broad-OSError arm. Tolerates
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

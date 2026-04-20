"""Reconcile the filesystem's ADS markers with the current rule set."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from dropboxignore import ads
from dropboxignore.rules import RuleCache

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

    _reconcile_path(subdir, cache, report)
    # If subdir itself is now ignored, don't descend.
    if _safe_is_ignored(subdir):
        report.duration_s = time.perf_counter() - start
        return report

    for current, dirnames, filenames in os.walk(subdir, followlinks=False):
        current_path = Path(current)
        # Reconcile each subdirectory; if it becomes ignored, prune it from
        # the walk (os.walk honors in-place modification of dirnames).
        kept_dirs: list[str] = []
        for name in dirnames:
            child = current_path / name
            _reconcile_path(child, cache, report)
            if not _safe_is_ignored(child):
                kept_dirs.append(name)
        dirnames[:] = kept_dirs

        for name in filenames:
            _reconcile_path(current_path / name, cache, report)

    report.duration_s = time.perf_counter() - start
    return report


def _reconcile_path(path: Path, cache: RuleCache, report: Report) -> None:
    try:
        should_ignore = cache.match(path)
        currently_ignored = ads.is_ignored(path)
    except FileNotFoundError:
        logger.debug("Path vanished during reconcile: %s", path)
        return
    except PermissionError as exc:
        logger.warning("Permission denied reading %s: %s", path, exc)
        report.errors.append((path, f"read: {exc}"))
        return

    try:
        if should_ignore and not currently_ignored:
            ads.set_ignored(path)
            report.marked += 1
        elif currently_ignored and not should_ignore:
            ads.clear_ignored(path)
            report.cleared += 1
    except FileNotFoundError:
        logger.debug("Path vanished before ADS write: %s", path)
    except PermissionError as exc:
        logger.warning("Permission denied writing ADS on %s: %s", path, exc)
        report.errors.append((path, f"write: {exc}"))


def _safe_is_ignored(path: Path) -> bool:
    try:
        return ads.is_ignored(path)
    except (FileNotFoundError, PermissionError):
        return False

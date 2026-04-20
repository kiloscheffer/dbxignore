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

    _reconcile_path(subdir, cache, report)
    for current, dirnames, filenames in os.walk(subdir, followlinks=False):
        current_path = Path(current)
        for name in filenames + dirnames:
            _reconcile_path(current_path / name, cache, report)

    report.duration_s = time.perf_counter() - start
    return report


def _reconcile_path(path: Path, cache: RuleCache, report: Report) -> None:
    should_ignore = cache.match(path)
    currently_ignored = ads.is_ignored(path)
    if should_ignore and not currently_ignored:
        ads.set_ignored(path)
        report.marked += 1
    elif currently_ignored and not should_ignore:
        ads.clear_ignored(path)
        report.cleared += 1

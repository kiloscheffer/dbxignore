"""Long-running daemon: watchdog observer + hourly sweep + event dispatch."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from dropboxignore.debounce import EventKind
from dropboxignore.reconcile import reconcile_subtree
from dropboxignore.rules import IGNORE_FILENAME, RuleCache

logger = logging.getLogger(__name__)


def _root_of(path: Path, roots: list[Path]) -> Path | None:
    for r in roots:
        try:
            path.relative_to(r)
            return r
        except ValueError:
            continue
    return None


def _classify(event: Any, roots: list[Path]) -> tuple[EventKind, str] | None:
    src = Path(event.src_path)
    if _root_of(src, roots) is None:
        return None
    if src.name == IGNORE_FILENAME:
        # any CRUD on a .dropboxignore is an EventKind.RULES event
        return EventKind.RULES, str(src).lower()
    if event.event_type == "created" and event.is_directory:
        return EventKind.DIR_CREATE, str(src).lower()
    if event.event_type in ("created", "moved"):
        return EventKind.OTHER, str(src).lower()
    # Everything else (modified non-rules file, deleted non-rules file) — skip.
    return None


def _dispatch(event: Any, cache: RuleCache, roots: list[Path]) -> None:
    classification = _classify(event, roots)
    if classification is None:
        return
    kind, _key = classification
    src = Path(event.src_path)
    root = _root_of(src, roots)
    if root is None:
        return

    if kind is EventKind.RULES:
        if event.event_type == "deleted":
            cache.remove_file(src)
        else:
            cache.reload_file(src)
        reconcile_subtree(root, src.parent, cache)
    elif kind is EventKind.DIR_CREATE:
        reconcile_subtree(root, src, cache)
    else:
        target = src.parent if src.is_file() or not src.exists() else src
        reconcile_subtree(root, target, cache)

"""Long-running daemon: watchdog observer + hourly sweep + event dispatch."""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import logging.handlers
import os
import signal
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from dropboxignore import roots as roots_module
from dropboxignore import state as state_module
from dropboxignore.debounce import Debouncer, EventKind
from dropboxignore.reconcile import reconcile_subtree
from dropboxignore.roots import find_containing
from dropboxignore.rules import IGNORE_FILENAME, RuleCache

logger = logging.getLogger(__name__)


def _classify(event: Any, roots: list[Path]) -> tuple[EventKind, str] | None:
    src = Path(event.src_path)
    if find_containing(src, roots) is None:
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
    root = find_containing(src, roots)
    if root is None:
        return

    if kind is EventKind.RULES:
        if event.event_type == "deleted":
            cache.remove_file(src)
            reconcile_subtree(root, src.parent, cache)
        elif event.event_type == "moved":
            cache.remove_file(src)
            reconcile_subtree(root, src.parent, cache)
            dest = Path(event.dest_path) if event.dest_path else None
            if dest is not None:
                dest_root = find_containing(dest, roots)
                if dest_root is not None:
                    cache.reload_file(dest)
                    if (dest_root, dest.parent) != (root, src.parent):
                        reconcile_subtree(dest_root, dest.parent, cache)
        else:
            cache.reload_file(src)
            reconcile_subtree(root, src.parent, cache)
    elif kind is EventKind.DIR_CREATE:
        reconcile_subtree(root, src, cache)
    else:
        target = src.parent
        reconcile_subtree(root, target, cache)
        if event.event_type == "moved" and event.dest_path:
            dest = Path(event.dest_path)
            dest_root = find_containing(dest, roots)
            if dest_root is not None:
                dest_target = dest if event.is_directory else dest.parent
                if (dest_root, dest_target) != (root, target):
                    reconcile_subtree(dest_root, dest_target, cache)


SWEEP_INTERVAL_S = 3600

DEFAULT_TIMEOUTS_MS = {
    EventKind.RULES: 100,
    EventKind.DIR_CREATE: 0,
    EventKind.OTHER: 500,
}

_TIMEOUT_ENV_VARS = {
    EventKind.RULES: "DROPBOXIGNORE_DEBOUNCE_RULES_MS",
    EventKind.DIR_CREATE: "DROPBOXIGNORE_DEBOUNCE_DIRS_MS",
    EventKind.OTHER: "DROPBOXIGNORE_DEBOUNCE_OTHER_MS",
}


def _timeouts_from_env() -> dict[EventKind, int]:
    return {
        kind: int(os.environ.get(_TIMEOUT_ENV_VARS[kind], str(default)))
        for kind, default in DEFAULT_TIMEOUTS_MS.items()
    }


def _log_dir() -> Path:
    return state_module.user_state_dir()


@contextlib.contextmanager
def _configured_logging() -> Iterator[None]:
    """Scope a rotating file handler to the block; restore prior logger state on exit."""
    level_name = os.environ.get("DROPBOXIGNORE_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "daemon.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=4,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))

    pkg_logger = logging.getLogger("dropboxignore")
    saved_handlers = list(pkg_logger.handlers)
    saved_propagate = pkg_logger.propagate
    saved_level = pkg_logger.level

    for h in list(pkg_logger.handlers):
        pkg_logger.removeHandler(h)
    pkg_logger.addHandler(handler)
    pkg_logger.propagate = False
    pkg_logger.setLevel(level)
    try:
        yield
    finally:
        for h in list(pkg_logger.handlers):
            pkg_logger.removeHandler(h)
            h.close()
        for h in saved_handlers:
            pkg_logger.addHandler(h)
        pkg_logger.propagate = saved_propagate
        pkg_logger.setLevel(saved_level)


def _is_other_live_daemon(pid: int | None) -> bool:
    if pid is None or pid == os.getpid():
        return False
    try:
        import psutil
    except ImportError:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False
    if not psutil.pid_exists(pid):
        return False
    try:
        proc = psutil.Process(pid)
        name = proc.name().lower()
        # Frozen PyInstaller build runs as dropboxignored.exe; source run as python.
        return "python" in name or "dropboxignored" in name
    except psutil.Error:
        return False


class _WatchdogHandler(FileSystemEventHandler):
    def __init__(self, debouncer: Debouncer, roots: list[Path]) -> None:
        self._debouncer = debouncer
        self._roots = roots

    def on_any_event(self, event):
        try:
            classification = _classify(event, self._roots)
            if classification is not None:
                kind, key = classification
                self._debouncer.submit(kind, key, event)
        except Exception:  # noqa: BLE001 — watcher must not die
            logger.exception("watchdog handler failed on event %r", event)


def run(stop_event: threading.Event | None = None) -> None:
    with _configured_logging():
        stop_event = stop_event or threading.Event()
        daemon_started = dt.datetime.now(dt.UTC)

        # Refuse to run if another daemon is already running.
        prior = state_module.read()
        if prior is not None and _is_other_live_daemon(prior.daemon_pid):
            logger.error(
                "daemon already running (pid=%d); refusing to start", prior.daemon_pid
            )
            return

        def _signal_handler(signum, _frame):
            logger.info("received signal %s, shutting down", signum)
            stop_event.set()
        for s in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(ValueError, AttributeError):
                signal.signal(s, _signal_handler)

        configured_roots = roots_module.discover()
        if not configured_roots:
            logger.error("no Dropbox roots discovered; exiting")
            return

        cache = RuleCache()
        for r in configured_roots:
            cache.load_root(r)

        _sweep_once(configured_roots, cache, daemon_started)

        debouncer = Debouncer(
            on_emit=lambda item: _dispatch(item[2], cache, configured_roots),
            timeouts_ms=_timeouts_from_env(),
        )
        handler = _WatchdogHandler(debouncer, configured_roots)
        observer = Observer()
        for r in configured_roots:
            observer.schedule(handler, str(r), recursive=True)

        debouncer.start()
        try:
            observer.start()
            logger.info("watching roots: %s", [str(r) for r in configured_roots])
            try:
                while not stop_event.is_set():
                    woke = stop_event.wait(SWEEP_INTERVAL_S)
                    if woke:
                        break
                    _sweep_once(configured_roots, cache, daemon_started)
            finally:
                observer.stop()
                observer.join()
        finally:
            debouncer.stop()
            logger.info("daemon stopped")


def _sweep_once(
    roots: list[Path], cache: RuleCache, daemon_started: dt.datetime
) -> None:
    sweep_start = time.perf_counter()

    # Phase 1: refresh the rule cache. Sequential — load_root mutates the
    # shared _rules dict and is cheap (only stats .dropboxignore files).
    for r in roots:
        cache.load_root(r)

    # Phase 2: reconcile each root. Reads cache (no writes) and writes
    # per-file ADS markers on disjoint paths, so threads across roots
    # don't contend. Single-root skips the pool to stay simple.
    if len(roots) > 1:
        with ThreadPoolExecutor(max_workers=len(roots)) as pool:
            reports = list(
                pool.map(lambda r: reconcile_subtree(r, r, cache), roots)
            )
    elif roots:
        reports = [reconcile_subtree(roots[0], roots[0], cache)]
    else:
        reports = []

    total_marked = sum(r.marked for r in reports)
    total_cleared = sum(r.cleared for r in reports)
    total_errors = sum(len(r.errors) for r in reports)
    wall_duration = time.perf_counter() - sweep_start

    logger.info(
        "sweep completed: marked=%d cleared=%d errors=%d duration=%.2fs",
        total_marked, total_cleared, total_errors, wall_duration,
    )

    now = dt.datetime.now(dt.UTC)
    last_err = next(
        (r.errors[-1] for r in reversed(reports) if r.errors),
        None,
    )

    s = state_module.State(
        daemon_pid=os.getpid(),
        daemon_started=daemon_started,
        last_sweep=now,
        last_sweep_duration_s=wall_duration,
        last_sweep_marked=total_marked,
        last_sweep_cleared=total_cleared,
        last_sweep_errors=total_errors,
        last_error=(
            state_module.LastError(time=now, path=last_err[0], message=last_err[1])
            if last_err is not None
            else None
        ),
        watched_roots=roots,
    )
    try:
        state_module.write(s)
    except OSError as exc:
        logger.warning("could not write state file: %s", exc)

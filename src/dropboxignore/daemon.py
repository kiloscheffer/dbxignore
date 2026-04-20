"""Long-running daemon: watchdog observer + hourly sweep + event dispatch."""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import logging.handlers
import os
import signal
import threading
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from dropboxignore import roots as roots_module
from dropboxignore import state as state_module
from dropboxignore.debounce import Debouncer, EventKind
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
            reconcile_subtree(root, src.parent, cache)
        elif event.event_type == "moved":
            cache.remove_file(src)
            reconcile_subtree(root, src.parent, cache)
            dest = Path(event.dest_path) if event.dest_path else None
            if dest is not None:
                dest_root = _root_of(dest, roots)
                if dest_root is not None:
                    cache.reload_file(dest)
                    reconcile_subtree(dest_root, dest.parent, cache)
        else:
            cache.reload_file(src)
            reconcile_subtree(root, src.parent, cache)
    elif kind is EventKind.DIR_CREATE:
        reconcile_subtree(root, src, cache)
    else:
        target = src.parent if src.is_file() or not src.exists() else src
        reconcile_subtree(root, target, cache)


SWEEP_INTERVAL_S = 3600

DEFAULT_TIMEOUTS_MS = {
    EventKind.RULES: 100,
    EventKind.DIR_CREATE: 0,
    EventKind.OTHER: 500,
}


def _timeouts_from_env() -> dict[EventKind, int]:
    return {
        EventKind.RULES: int(os.environ.get("DROPBOXIGNORE_DEBOUNCE_RULES_MS", "100")),
        EventKind.DIR_CREATE: int(os.environ.get("DROPBOXIGNORE_DEBOUNCE_DIRS_MS", "0")),
        EventKind.OTHER: int(os.environ.get("DROPBOXIGNORE_DEBOUNCE_OTHER_MS", "500")),
    }


def _log_dir() -> Path:
    localappdata = os.environ.get("LOCALAPPDATA")
    base = Path(localappdata) if localappdata else Path.home() / "AppData" / "Local"
    return base / "dropboxignore"


def _configure_logging() -> None:
    """Install a rotating file handler writing to LOCALAPPDATA/dropboxignore/daemon.log."""
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

    root_logger = logging.getLogger("dropboxignore")
    root_logger.setLevel(level)
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)
    root_logger.addHandler(handler)
    root_logger.propagate = False


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
        return "python" in proc.name().lower()
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
    _configure_logging()
    stop_event = stop_event or threading.Event()
    daemon_started = dt.datetime.now(dt.UTC)

    # Refuse to run if another daemon is already running.
    prior = state_module.read()
    if prior is not None and _is_other_live_daemon(prior.daemon_pid):
        logger.error("daemon already running (pid=%d); refusing to start", prior.daemon_pid)
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
    total_marked = 0
    total_cleared = 0
    total_errors = 0
    total_duration = 0.0

    for r in roots:
        cache.load_root(r)
        report = reconcile_subtree(r, r, cache)
        total_marked += report.marked
        total_cleared += report.cleared
        total_errors += len(report.errors)
        total_duration += report.duration_s

    logger.info(
        "sweep completed: marked=%d cleared=%d errors=%d duration=%.2fs",
        total_marked, total_cleared, total_errors, total_duration,
    )

    s = state_module.State(
        daemon_pid=os.getpid(),
        daemon_started=daemon_started,  # fixed: persists true start time across sweeps
        last_sweep=dt.datetime.now(dt.UTC),
        last_sweep_duration_s=total_duration,
        last_sweep_marked=total_marked,
        last_sweep_cleared=total_cleared,
        last_sweep_errors=total_errors,
        watched_roots=roots,
    )
    try:
        state_module.write(s)
    except OSError as exc:
        logger.warning("could not write state file: %s", exc)

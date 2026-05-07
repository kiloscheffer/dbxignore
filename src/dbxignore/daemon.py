"""Long-running daemon: watchdog observer + hourly sweep + event dispatch."""

from __future__ import annotations

import contextlib
import datetime as dt
import errno
import logging
import logging.handlers
import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from dbxignore import roots as roots_module
from dbxignore import state as state_module
from dbxignore.debounce import Debouncer, EventKind
from dbxignore.markers import detection_summary
from dbxignore.reconcile import reconcile_subtree
from dbxignore.roots import find_containing
from dbxignore.rules import IGNORE_FILENAME, RuleCache

if TYPE_CHECKING:
    from collections.abc import Iterator

    from watchdog.observers.api import BaseObserver

logger = logging.getLogger(__name__)

# Explicitly enumerated rather than `getattr(logging, name, default)` so that
# `DBXIGNORE_LOG_LEVEL=NOTSET` (a real logging constant but rarely what the user
# wants — means "use parent level", typically root which defaults to WARNING)
# also surfaces as "unknown" with the same fallback-to-INFO + WARNING treatment.
_VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _resolve_under_roots(raw_path: str | None, roots: list[Path]) -> tuple[Path, Path] | None:
    """Return ``(root, resolved_path)`` if ``raw_path`` is under a watched root.

    Resolution is deferred until after ``find_containing`` succeeds — the
    Path.resolve() syscall is the cost item #43 hoisted out of reconcile,
    so paying it for events outside any root would defeat the win.
    """
    if not raw_path:
        return None
    p = Path(raw_path)
    root = find_containing(p, roots)
    if root is None:
        return None
    return root, p.resolve()


def _moved_dest_under_root(event: Any, roots: list[Path]) -> tuple[Path, Path] | None:
    """Return ``(dest_root, resolved_dest)`` if ``event`` is a moved event
    whose ``dest_path`` is a ``.dropboxignore`` under a watched root.

    Used by ``_classify`` to recognize rule-file move-into events whether or
    not the event's ``src_path`` resolves under a watched root — atomic-save
    renames within a root and cross-watch moves from outside (where watchdog
    may emit empty/external ``src_path``) are both rule-file events.
    """
    if event.event_type != "moved" or not event.dest_path:
        return None
    dest_path = Path(event.dest_path)
    if dest_path.name != IGNORE_FILENAME:
        return None
    dest_root = find_containing(dest_path, roots)
    if dest_root is None:
        return None
    return dest_root, dest_path.resolve()


def _classify(
    event: Any, roots: list[Path]
) -> tuple[EventKind, str, Path, Path, tuple[Path, Path] | None] | None:
    """Classify a watchdog event and return
    ``(kind, key, root, resolved_src, dest_pair)``.

    ``roots`` MUST already be resolved (see ``_discover_roots`` in cli.py
    and ``run()`` below). ``resolved_src`` is hoisted out of ``_dispatch``
    so downstream consumers don't repeat the syscall.

    ``dest_pair`` is ``(dest_root, resolved_dest)`` for moved events whose
    dest is a `.dropboxignore` under a watched root, else ``None``. Threaded
    through so ``_dispatch`` doesn't re-run ``find_containing`` +
    ``Path.resolve()`` on the dest path that ``_moved_dest_under_root``
    already computed (the same cost optimization as item #43 for src).
    """
    dest_rule = _moved_dest_under_root(event, roots)
    located = _resolve_under_roots(event.src_path, roots)
    if located is None:
        # src is outside all watched roots, or unresolvable (watchdog emits
        # empty src_path for cross-watch moves: e.g. a `.dropboxignore` moved
        # in from `~/Downloads` or any non-watched directory). If dest is a
        # rule file inside a watched root, treat the event as a rule-file
        # event keyed at dest. Without this fallback, such a move is dropped
        # and the new rule file is invisible until the hourly sweep.
        if dest_rule is not None:
            dest_root, dest = dest_rule
            return (
                EventKind.RULES,
                f"moved-into:{str(dest).lower()}",
                dest_root,
                dest,
                dest_rule,
            )
        return None
    root, src = located
    if src.name == IGNORE_FILENAME:
        return EventKind.RULES, str(src).lower(), root, src, dest_rule
    # Moved event with src inside a root but not a rule file, dest is a rule
    # file (atomic-save: `.dropboxignore.tmp` -> `.dropboxignore`). Without
    # this branch the event lands in OTHER and the new rules don't load
    # until the next hourly sweep.
    if dest_rule is not None:
        # Key on the dest path (the rule file), not on src (the temp file
        # name). Atomic-save editors generate unique tmp filenames per
        # save; keying on src would defeat the RULES debounce window for
        # consecutive saves of the same `.dropboxignore`.
        #
        # Prefix with `moved-into:` so the key cannot collide with the
        # first branch's bare-path key (which uses the same path string
        # when src IS the rule file). Without the prefix, a move-out
        # `A/.dropboxignore` -> `B/...` (key `A/...`) and a move-into
        # `tmp` -> `A/.dropboxignore` (key would also be `A/...`) land
        # on the same debouncer token, and last-wins coalesce drops
        # one event's dest-side handling.
        _, dest = dest_rule
        return EventKind.RULES, f"moved-into:{str(dest).lower()}", root, src, dest_rule
    if event.event_type == "created" and event.is_directory:
        return EventKind.DIR_CREATE, str(src).lower(), root, src, None
    if event.event_type in ("created", "moved"):
        return EventKind.OTHER, str(src).lower(), root, src, None
    return None


def _dispatch(event: Any, cache: RuleCache, roots: list[Path]) -> None:
    classification = _classify(event, roots)
    if classification is None:
        return
    kind, _key, root, src, dest_pair = classification
    # DEBUG-level boundary log for backlog item #34 timing diagnostics.
    # Emitted on the debouncer worker thread (or synchronously from the
    # watchdog thread for fast-path DIR_CREATEs that route through here in
    # the future). Pairs with `submit` / `emit` timestamps to measure
    # queue-to-dispatch latency. No-op cost when DBXIGNORE_LOG_LEVEL != DEBUG.
    logger.debug(
        "dispatch kind=%s event_type=%s path=%s",
        kind.value,
        event.event_type,
        src,
    )

    if kind is EventKind.RULES:
        if event.event_type == "deleted":
            cache.remove_file(src)
            reconcile_subtree(root, src.parent, cache)
        elif event.event_type == "moved":
            # src and dest are handled independently: each side is a rule
            # file iff its basename is `.dropboxignore`. Possible shapes:
            #   rule -> rule           : remove src, reload dest
            #   rule -> non-rule       : remove src only (editor backup
            #                            rename `.dropboxignore` -> `.bak`)
            #   non-rule -> rule       : reload dest only (atomic save:
            #                            `.dropboxignore.tmp` -> rule file)
            #
            # All cache mutations run before any reconcile so the reconcile
            # sees the post-move rule state. Critical for the non-rule->rule
            # same-parent case (atomic save, e.g. `.dropboxignore.tmp` ->
            # `.dropboxignore`): the same-parent dedupe collapses to a single
            # reconcile call, which must run after the dest reload — otherwise
            # the new rules don't apply until the next event or hourly sweep.
            #
            # Cross-watch synthesis: when `event.src_path` was outside all
            # watched roots, `_classify` set `src` equal to the resolved
            # dest path so dispatch's `src.parent` reconcile targets the
            # correct directory. Detect that case via `src == dest_pair[1]`
            # and skip `cache.remove_file(src)` — there was never a real
            # cached entry on the src side to remove, and the redundant
            # call would fire `_recompute_conflicts` an extra time.
            src_is_rules = src.name == IGNORE_FILENAME and (
                dest_pair is None or src != dest_pair[1]
            )
            if src_is_rules:
                cache.remove_file(src)
            if dest_pair is not None:
                cache.reload_file(dest_pair[1])
            reconcile_subtree(root, src.parent, cache)
            # dest.parent reconcile: prefer the precomputed dest_pair (no
            # extra resolve syscall — the common atomic-save and rule->rule
            # cases). Fall back to a fresh resolve when src was a rule file
            # but dest is non-rule (rare rule->non-rule cross-parent case,
            # e.g. user renames `/A/.dropboxignore` to `/B/foo.bak`); the
            # moved file lands in /B and may match rules from B's tree, so
            # /B's reconcile must still fire.
            dest_for_reconcile: tuple[Path, Path] | None
            if dest_pair is not None:
                dest_for_reconcile = dest_pair
            elif src_is_rules:
                dest_for_reconcile = _resolve_under_roots(event.dest_path, roots)
            else:
                dest_for_reconcile = None
            if dest_for_reconcile is not None:
                dest_root, dest = dest_for_reconcile
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
        if event.event_type == "moved":
            dest_located = _resolve_under_roots(event.dest_path, roots)
            if dest_located is not None:
                dest_root, dest = dest_located
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
    EventKind.RULES: "DBXIGNORE_DEBOUNCE_RULES_MS",
    EventKind.DIR_CREATE: "DBXIGNORE_DEBOUNCE_DIRS_MS",
    EventKind.OTHER: "DBXIGNORE_DEBOUNCE_OTHER_MS",
}


def _timeouts_from_env() -> dict[EventKind, int]:
    """Return per-kind debounce timeouts honoring the ``DBXIGNORE_DEBOUNCE_*_MS``
    env-var overrides, with defensive parsing.

    A typo (`DBXIGNORE_DEBOUNCE_OTHER_MS=fast`) or a negative value would
    crash daemon startup if we used a bare `int(...)` — the daemon then
    stays unreachable until the user notices and corrects the env var.
    Validate per kind: log a WARNING naming the bad value, fall back to
    the default. Same shape as the `DBXIGNORE_LOG_LEVEL` validation.
    """
    timeouts: dict[EventKind, int] = {}
    for kind, default in DEFAULT_TIMEOUTS_MS.items():
        env_var = _TIMEOUT_ENV_VARS[kind]
        raw = os.environ.get(env_var)
        if raw is None:
            timeouts[kind] = default
            continue
        try:
            value = int(raw)
        except ValueError:
            logger.warning(
                "%s=%r is not an integer; falling back to default %d ms.",
                env_var,
                raw,
                default,
            )
            timeouts[kind] = default
            continue
        if value < 0:
            logger.warning(
                "%s=%d is negative; falling back to default %d ms.",
                env_var,
                value,
                default,
            )
            timeouts[kind] = default
            continue
        timeouts[kind] = value
    return timeouts


_ENOSPC_MESSAGE = (
    "inotify watch limit reached (errno ENOSPC). The kernel's "
    "fs.inotify.max_user_watches is exhausted; recursive watch on a Dropbox "
    "tree larger than the per-user limit fails at observer startup. To raise "
    "the limit, run as root:\n"
    "\n"
    "    sudo sysctl -w fs.inotify.max_user_watches=524288\n"
    "\n"
    "To make the change persist across reboots:\n"
    "\n"
    "    echo 'fs.inotify.max_user_watches=524288' | sudo tee "
    "/etc/sysctl.d/99-dbxignore.conf\n"
    "    sudo sysctl --system\n"
    "\n"
    "Alternatively, reduce the watched tree by adding rules to .dropboxignore. "
    "Daemon exiting with status 75."
)

_EMFILE_MESSAGE = (
    "inotify instance limit reached (errno EMFILE). The kernel's "
    "fs.inotify.max_user_instances is exhausted. To raise the limit, run as "
    "root:\n"
    "\n"
    "    sudo sysctl -w fs.inotify.max_user_instances=1024\n"
    "\n"
    "To make the change persist across reboots:\n"
    "\n"
    "    echo 'fs.inotify.max_user_instances=1024' | sudo tee "
    "/etc/sysctl.d/99-dbxignore.conf\n"
    "    sudo sysctl --system\n"
    "\n"
    "Daemon exiting with status 75."
)


def _log_dir() -> Path:
    return state_module.user_log_dir()


@contextlib.contextmanager
def _configured_logging() -> Iterator[None]:
    """Scope log handlers to the block; restore prior logger state on exit.

    Always installs a RotatingFileHandler at ``_log_dir()/daemon.log``. On
    Linux, additionally attaches a ``StreamHandler(sys.stderr)`` so that
    records flow to systemd-journald when the daemon runs as a user unit
    (``journalctl --user -u dbxignore.service`` surfaces them). The
    rotating file remains authoritative — identical records land in both
    sinks, so grabbing ``daemon.log`` still yields a complete debug record
    on Linux, matching the Windows workflow.
    """
    level_name_raw = os.environ.get("DBXIGNORE_LOG_LEVEL")
    if level_name_raw and level_name_raw.upper() in _VALID_LEVELS:
        level = getattr(logging, level_name_raw.upper())
        unknown_level = None
    else:
        level = logging.INFO
        # `unknown_level` is the (raw, original-cased) value the user supplied
        # if non-empty; preserved for the warning message below so a typo like
        # `DBXIGNORE_LOG_LEVEL=DEUG` shows up verbatim, not lower-cased.
        unknown_level = level_name_raw if level_name_raw else None

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "daemon.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=4,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    new_handlers: list[logging.Handler] = [file_handler]
    if sys.platform.startswith("linux"):
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(formatter)
        new_handlers.append(stderr_handler)

    pkg_logger = logging.getLogger("dbxignore")
    saved_handlers = list(pkg_logger.handlers)
    saved_propagate = pkg_logger.propagate
    saved_level = pkg_logger.level

    for h in list(pkg_logger.handlers):
        pkg_logger.removeHandler(h)
    for h in new_handlers:
        pkg_logger.addHandler(h)
    pkg_logger.propagate = False
    pkg_logger.setLevel(level)
    if unknown_level is not None:
        # Emit AFTER handlers are configured so the warning lands in
        # daemon.log (and stderr on Linux), where the user looks for daemon
        # output. WARNING is always >= INFO so it surfaces even though we
        # fell back to INFO.
        logger.warning(
            "DBXIGNORE_LOG_LEVEL=%r is not a recognized logging level; "
            "falling back to INFO. Accepted: %s.",
            unknown_level,
            ", ".join(_VALID_LEVELS),
        )
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


def _singleton_lock_path() -> Path:
    """Return the path of the daemon-singleton lock file.

    Sits next to ``state.json`` under the per-user state directory.
    Cleaned by ``cli._purge_local_state`` on ``uninstall --purge``.
    """
    return state_module.user_state_dir() / "daemon.lock"


def _acquire_singleton_lock() -> Any | None:
    """Try to acquire the daemon-singleton lock.

    Returns the open file handle on success — the caller MUST keep it
    open for the daemon's lifetime; closing it releases the lock. The
    OS releases the lock automatically on process exit (handles SIGKILL,
    power loss, and crashes that bypass the cleanup), so a stale lock
    file on disk is never a problem on subsequent restarts.

    Returns ``None`` on contention (another process holds the lock).

    Cross-platform via ``fcntl.flock`` on POSIX and ``msvcrt.locking``
    on Windows. Both use non-blocking exclusive semantics: a second
    acquisition fails immediately rather than waiting. This is the
    singleton gate that backlog item #78 introduces — the prior
    state-based check (read state.json → check is_daemon_alive(prior.pid))
    had a non-atomic window between read and the first state.write, so
    two concurrent ``dbxignored`` launches could both decide "no other
    daemon" and proceed.
    """
    lock_path = _singleton_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # Open in append+binary+read so the file is created if missing without
    # truncating an existing one. msvcrt.locking on Windows requires the
    # locked region to overlap actual file bytes, so write a placeholder
    # byte if the file is empty.
    fh = open(lock_path, "ab+")  # noqa: SIM115 — caller closes for daemon lifetime
    try:
        if os.fstat(fh.fileno()).st_size == 0:
            fh.write(b" ")
            fh.flush()
        # Seek to byte 0 before locking. ``msvcrt.locking`` on Windows
        # locks from the file's current cursor position; ``"ab+"`` leaves
        # the cursor at EOF after open, and the placeholder write above
        # advances it by one. Two concurrent fresh launches with different
        # write timings would end at different cursors and lock different
        # byte ranges — both succeed, singleton defeated. Forcing all
        # contenders to lock byte 0 closes the race. ``fcntl.flock`` on
        # POSIX is per-open-file (cursor-independent), so the seek is a
        # no-op there but keeps the cross-platform contract uniform.
        fh.seek(0)
        if sys.platform == "win32":
            import msvcrt  # type: ignore[import-not-found, unused-ignore]

            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # type: ignore[import-not-found, unused-ignore]

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def _start_observer_or_exit(observer: BaseObserver) -> None:
    """Start ``observer``; trap inotify watch/instance exhaustion and exit 75.

    Trapped errnos (ENOSPC, EMFILE) emit ERROR with a sysctl runbook then
    ``sys.exit(75)`` (POSIX ``EX_TEMPFAIL``) so systemd marks the unit
    ``failed``. Other ``OSError`` shapes propagate.

    MUST be called from inside ``_configured_logging()`` so the ERROR record
    reaches ``daemon.log`` (and journald on Linux).
    """
    try:
        observer.start()
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            logger.error(_ENOSPC_MESSAGE)
            sys.exit(75)
        if exc.errno == errno.EMFILE:
            logger.error(_EMFILE_MESSAGE)
            sys.exit(75)
        raise


class _WatchdogHandler(FileSystemEventHandler):
    def __init__(self, debouncer: Debouncer, roots: list[Path], cache: RuleCache) -> None:
        self._debouncer = debouncer
        self._roots = roots
        self._cache = cache

    def on_any_event(self, event: Any) -> None:
        # DEBUG-level boundary log for backlog item #34 timing diagnostics.
        # Emitted on the watchdog thread BEFORE classification so we can
        # measure kernel-event-delivery latency from the test-side write
        # timestamp. No-op cost when DBXIGNORE_LOG_LEVEL != DEBUG.
        logger.debug(
            "on_any_event type=%s path=%s is_dir=%s",
            event.event_type,
            event.src_path,
            event.is_directory,
        )
        try:
            classification = _classify(event, self._roots)
            if classification is None:
                return
            kind, key, root, src, _dest_pair = classification
            # Fast-path: a DIR_CREATE for a path already matching a cached
            # rule reconciles synchronously, skipping the debouncer queue.
            # Every millisecond of debounce widens the race window where
            # Dropbox sees the new directory and starts ingesting children
            # before the parent's marker lands. RULES still coalesce (rule
            # bursts deserve it); OTHER still batches. Trade-off: if the
            # matching rule was just deleted in a queued-but-not-yet-
            # processed RULES event, the bypass marks a path that the next
            # reconcile_subtree (driven by that RULES event) will clear —
            # bounded transient false-positive (followup item 57).
            if kind is EventKind.DIR_CREATE:
                matched = self._cache.match(src)
                logger.debug("fast-path DIR_CREATE: match=%s path=%s", matched, src)
                if matched:
                    reconcile_subtree(root, src, self._cache)
                    return
            self._debouncer.submit(kind, key, event)
        except Exception:  # noqa: BLE001 — watcher must not die
            logger.exception("watchdog handler failed on event %r", event)


def run(stop_event: threading.Event | None = None) -> None:
    with _configured_logging():
        stop_event = stop_event or threading.Event()
        daemon_started = dt.datetime.now(dt.UTC)

        # Singleton gate (backlog item #78). The OS-level lock is the
        # authoritative check: kernel-released on process exit so a stale
        # lock file is never a problem, and acquisition is atomic so two
        # concurrent ``dbxignored`` launches can't both proceed. The
        # prior state-based check (read state.json → is_daemon_alive)
        # had a non-atomic window between read and the first state.write
        # and is now removed in favor of this lock.
        singleton_lock = _acquire_singleton_lock()
        if singleton_lock is None:
            # Read state.json (best-effort) to recover the existing
            # daemon's PID for a more useful error. Falls back to a
            # generic "lock held" message if state is unreadable.
            prior = state_module.read()
            if prior is not None and prior.daemon_pid is not None:
                logger.error(
                    "daemon already running (pid=%d); refusing to start",
                    prior.daemon_pid,
                )
            else:
                logger.error("daemon already running (singleton lock held); refusing to start")
            return

        # Wrap the entire post-acquisition body so the lock is released on
        # any exit path — empty-roots return, detection_summary raise,
        # observer.start failure, etc. The kernel would also auto-release
        # on process exit, but explicit close keeps the contract crisp for
        # tests that bring the daemon up and down within a single process.
        # Without the wide try/finally the empty-configured_roots return
        # below would silently leak the handle for the rest of the process.
        try:
            # Defense-in-depth for the migration window: a legacy
            # (pre-#78) daemon wrote state.json but never created
            # daemon.lock, so the lock-acquire above succeeded against
            # nothing. Re-check state.json against the live process
            # table and refuse if a different live daemon is recorded.
            # Once everyone has run this version once, state.json's
            # daemon_pid matches the most recent (this-version) daemon
            # whose lock would have already blocked the second start,
            # so this branch becomes vacuous.
            prior = state_module.read()
            if (
                prior is not None
                and prior.daemon_pid is not None
                and prior.daemon_pid != os.getpid()
                and state_module.is_daemon_alive(prior.daemon_pid, prior.daemon_create_time)
            ):
                logger.error(
                    "daemon already running (pid=%d); refusing to start "
                    "(legacy daemon predates the singleton lock file)",
                    prior.daemon_pid,
                )
                return

            # Capture our own process create_time so the persisted state.json
            # carries it for future is_daemon_alive(create_time=...) checks
            # (backlog item #79). Lazy-imported because psutil is soft-required
            # and we want graceful degradation when it's missing.
            try:
                import psutil  # type: ignore[import-untyped, unused-ignore]

                daemon_create_time: float | None = psutil.Process(os.getpid()).create_time()
            except Exception:  # noqa: BLE001 — psutil missing or any per-platform quirk
                daemon_create_time = None

            def _signal_handler(signum: int, _frame: object) -> None:
                logger.info("received signal %s, shutting down", signum)
                stop_event.set()

            for s in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(ValueError, AttributeError):
                    signal.signal(s, _signal_handler)

            # Resolve at the daemon boundary; downstream layers must not re-pay.
            configured_roots = [r.resolve() for r in roots_module.discover()]
            if not configured_roots:
                logger.error("no Dropbox roots discovered; exiting")
                return

            # Surface the macOS sync-mode detection result so users can self-
            # diagnose without DBXIGNORE_LOG_LEVEL=DEBUG (followup item 37).
            # Returns None on Windows/Linux — single-attribute platforms have
            # no detection step to report.
            summary = detection_summary()
            if summary is not None:
                logger.info("sync mode detection: %s", summary)

            cache = RuleCache()
            for r in configured_roots:
                cache.load_root(r)

            _sweep_once(configured_roots, cache, daemon_started, daemon_create_time)

            debouncer = Debouncer(
                on_emit=lambda item: _dispatch(item[2], cache, configured_roots),
                timeouts_ms=_timeouts_from_env(),
            )
            handler = _WatchdogHandler(debouncer, configured_roots, cache)
            observer = Observer()
            for r in configured_roots:
                observer.schedule(handler, str(r), recursive=True)

            debouncer.start()
            try:
                _start_observer_or_exit(observer)
                logger.info("watching roots: %s", [str(r) for r in configured_roots])
                try:
                    while not stop_event.is_set():
                        woke = stop_event.wait(SWEEP_INTERVAL_S)
                        if woke:
                            break
                        _sweep_once(configured_roots, cache, daemon_started, daemon_create_time)
                finally:
                    observer.stop()
                    observer.join()
            finally:
                debouncer.stop()
                logger.info("daemon stopped")
        finally:
            singleton_lock.close()


def _sweep_once(
    roots: list[Path],
    cache: RuleCache,
    daemon_started: dt.datetime,
    daemon_create_time: float | None = None,
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
            reports = list(pool.map(lambda r: reconcile_subtree(r, r, cache), roots))
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
        total_marked,
        total_cleared,
        total_errors,
        wall_duration,
    )

    now = dt.datetime.now(dt.UTC)
    last_err = next(
        (r.errors[-1] for r in reversed(reports) if r.errors),
        None,
    )

    # Snapshot the conflict count so `cli.status --summary` can report it
    # without re-walking the rule cache on every poll (item #68). Free —
    # `cache.conflicts()` reads the already-computed `_conflicts` list that
    # `cache.load_root` populated above.
    total_conflicts = len(cache.conflicts())

    s = state_module.State(
        daemon_pid=os.getpid(),
        daemon_create_time=daemon_create_time,
        daemon_started=daemon_started,
        last_sweep=now,
        last_sweep_duration_s=wall_duration,
        last_sweep_marked=total_marked,
        last_sweep_cleared=total_cleared,
        last_sweep_errors=total_errors,
        last_sweep_conflicts=total_conflicts,
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

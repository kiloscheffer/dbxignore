"""Persist daemon state under the platform's per-user state directory.

Windows: ``%LOCALAPPDATA%\\dbxignore\\state.json``.
Linux: ``$XDG_STATE_HOME/dbxignore/state.json`` (fallback ``~/.local/state/...``).
macOS: ``~/Library/Application Support/dbxignore/state.json``
       (logs split off to ``~/Library/Logs/dbxignore/`` per Apple's app-data conventions).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Valid daemon process names for the is_daemon_alive name guard. The
# `"python"` substring check covers source runs (`python -m dbxignore daemon`
# and `pythonw -m dbxignore daemon`); this set covers the frozen-binary
# names: dbxignore.exe (CLI; foreground daemon launches), dbxignorew.exe
# (Task Scheduler default after PR #239), and their Linux/macOS analogs.
_DAEMON_PROCESS_NAMES: frozenset[str] = frozenset(
    {
        "dbxignore",
        "dbxignore.exe",
        "dbxignorew",
        "dbxignorew.exe",
    }
)


@dataclass
class LastError:
    time: datetime
    path: Path
    message: str


@dataclass
class State:
    daemon_pid: int | None = None
    # Per-process create timestamp (psutil.Process.create_time() value, a
    # Unix-epoch float). Persisted alongside daemon_pid so is_daemon_alive
    # can distinguish "the daemon is still that PID" from "the kernel
    # recycled that PID for an unrelated process". Optional for backwards-
    # compat with state.json files written before #79.
    daemon_create_time: float | None = None
    daemon_started: datetime | None = None
    last_sweep: datetime | None = None
    last_sweep_duration_s: float = 0.0
    last_sweep_marked: int = 0
    last_sweep_cleared: int = 0
    last_sweep_errors: int = 0
    # Count of rule conflicts detected at the last sweep's `cache.load_root`.
    # Persisted so `cli.status --summary` can report it without re-walking
    # the rule cache on every poll (item #68). Stale when the daemon isn't
    # running, same lineage as `last_sweep_*` above.
    last_sweep_conflicts: int = 0
    last_error: LastError | None = None
    watched_roots: list[Path] = field(default_factory=list)


def user_state_dir() -> Path:
    """Per-user directory where dbxignore persists state.

    On Windows + Linux, also where daemon.log lives. On macOS, daemon.log
    is split off to ~/Library/Logs/dbxignore/ — see user_log_dir().
    """
    if sys.platform == "win32":
        localappdata = os.environ.get("LOCALAPPDATA")
        base = Path(localappdata) if localappdata else Path.home() / "AppData" / "Local"
        return base / "dbxignore"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "dbxignore"
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "dbxignore"


def user_log_dir() -> Path:
    """Per-user directory where dbxignore writes daemon.log.

    Same as user_state_dir() on Windows + Linux. On macOS, splits off
    to ~/Library/Logs/dbxignore/ to match Apple's app-data conventions
    (state files live in Application Support/, log files in Logs/).
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "dbxignore"
    return user_state_dir()


def default_path() -> Path:
    return user_state_dir() / "state.json"


def daemon_is_running(state_obj: State | None) -> bool:
    """True if the recorded daemon PID corresponds to a live daemon.

    Convenience wrapper around ``is_daemon_alive`` for the common
    "state.json says PID X is the daemon — is X actually running?" check.
    Folds the None-state and None-pid edges into a single bool so callers
    don't have to repeat ``s is not None and s.daemon_pid is not None and
    is_daemon_alive(s.daemon_pid)``. Forwards ``state_obj.daemon_create_time``
    so a recycled PID at the same numeric value but with a different
    create_time is correctly rejected (followup item #79).
    """
    if state_obj is None or state_obj.daemon_pid is None:
        return False
    return is_daemon_alive(state_obj.daemon_pid, create_time=state_obj.daemon_create_time)


def is_daemon_alive(pid: int | None, create_time: float | None = None) -> bool:
    """Return True if ``pid`` is a live dbxignore daemon process.

    Two-stage check. The first stage verifies that the PID exists AND that
    the process at that PID is plausibly a dbxignore daemon by name: a
    recycled PID claimed by an unrelated process registers as alive under
    a bare existence check, which is the PID-reuse false positive we want
    to avoid. Frozen PyInstaller installs run as ``dbxignore.exe`` (terminal
    CLI) or ``dbxignorew.exe`` (Task Scheduler / shell-verb GUI helper);
    source runs are typically ``python -m dbxignore daemon`` or
    ``pythonw -m dbxignore daemon`` (or pytest under the test suite).

    The second stage, gated on a non-None ``create_time``, additionally
    requires the live process's ``psutil.Process.create_time()`` to match
    the caller-supplied value. This is the followup item #79 enhancement:
    a substring-name match is not enough when the recycled PID's new
    occupant happens to also be a python process (very common when the
    test suite or any python tooling runs after a daemon dies). Comparing
    the create_time disambiguates "still that daemon" from "PID was
    recycled by another python".

    Lazy-imports ``psutil``; falls back to ``os.kill(pid, 0)`` for the
    bare-existence check when ``psutil`` isn't installed. The fallback
    treats ``ProcessLookupError`` as the expected "no such process" path
    (silent — common post-daemon-death) and ``(OSError, SystemError)`` as
    indeterminate (logs WARNING, returns False). The ``SystemError`` catch
    covers CPython's exception-state-wrapping case where ``os.kill`` fires
    while another exception (e.g. a partially-initialized psutil import)
    is still being handled (item #118). With psutil unavailable, PID-reuse
    can't be detected and ``create_time`` is silently ignored — a known
    limitation, not a behavior bug. Used by ``cli.status`` to render the
    "running / not running / state may be stale" UI. The daemon's
    singleton gate has moved to a process-lifetime OS lock (see
    ``daemon._acquire_singleton_lock``), so this helper is no longer on
    that path.
    """
    if pid is None:
        return False
    try:
        import psutil  # type: ignore[import-untyped, unused-ignore]
    except ImportError:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except (OSError, SystemError) as exc:
            # SystemError can wrap an OSError when os.kill fires while
            # another exception is still being handled — e.g. a prior
            # ImportError on psutil left exception state dirty (item
            # #118, surfaced by Python 3.14 + the psutil partial-init
            # scenario from item #117). Either way the PID probe is
            # indeterminate; log so the cause surfaces, return False
            # so callers don't block on an opaque error.
            logger.warning(
                "os.kill(%d, 0) probe failed (%s: %s); treating daemon as not alive",
                pid,
                type(exc).__name__,
                exc,
            )
            return False
        return True
    if not psutil.pid_exists(pid):
        return False
    try:
        proc = psutil.Process(pid)
        name = proc.name().lower()
    except psutil.Error:
        return False
    if "python" not in name and name not in _DAEMON_PROCESS_NAMES:
        return False
    if create_time is None:
        return True
    # Strict-mode: caller supplied the create_time the daemon recorded
    # at startup. If it doesn't match the live process's create_time,
    # the PID was recycled.
    try:
        live_create_time = proc.create_time()
    except psutil.Error:
        return False
    # psutil reports create_time as a Unix-epoch float. Resolution varies
    # by platform (Windows is sub-second; Linux/macOS read from /proc or
    # equivalent). A strict equality check is too tight given float
    # round-trip through json; allow a millisecond of slack.
    # bool() narrowing because psutil is untyped — without it mypy infers
    # the comparison's result as Any.
    return bool(abs(live_create_time - create_time) < 0.001)


def is_any_daemon_running() -> bool:
    """Probe whether any dbxignore daemon is currently running, without a PID.

    Unlike ``is_daemon_alive`` (PID-anchored from a readable ``state.json``),
    this helper works when ``state.json`` is absent, unreadable, or malformed.
    Uses the daemon-singleton lock at ``user_state_dir()/daemon.lock`` as the
    liveness signal: the daemon holds an exclusive ``fcntl.flock`` (POSIX) /
    ``msvcrt.locking`` (Windows) on that file for its entire lifetime (see
    ``daemon._acquire_singleton_lock``). If we can briefly acquire-then-release
    the same lock, no daemon currently holds it.

    Returns ``False`` when:

    - the lock file doesn't exist (no daemon has ever run, or a prior
      ``--purge`` cleaned up the state directory), OR
    - the file exists and we acquired the lock (daemon is dead — OS released
      the lock on process exit, file lingers).

    Returns ``True`` when the lock acquisition fails with EWOULDBLOCK/EAGAIN
    (or the Windows ``msvcrt`` equivalent), which means a contender holds it.

    Indeterminate probes (open failure, lock primitive unimportable) log a
    WARNING and return ``False`` — matches the convention ``is_daemon_alive``
    follows for its no-psutil ``os.kill`` fallback (item #118): destructive
    verbs should not block on opaque errors. Used by ``cli.uninstall``'s
    Arm A (BACKLOG #122 fix, Codex P2 follow-up on PR #243) so that a
    corrupt ``state.json`` on a system with no live daemon doesn't block
    ``--purge`` from cleaning up the orphaned file.

    Cross-references the lock-path knowledge with ``daemon._singleton_lock_path``
    — both compute ``user_state_dir() / "daemon.lock"``. Kept duplicated rather
    than imported to avoid a ``state``→``daemon`` import cycle (``daemon``
    already imports ``state``).
    """
    lock_path = user_state_dir() / "daemon.lock"
    if not lock_path.exists():
        return False
    try:
        fh = open(lock_path, "ab+")  # noqa: SIM115 — closed in finally below
    except OSError as exc:
        logger.warning(
            "could not open %s for liveness probe (%s: %s); treating as no daemon",
            lock_path,
            type(exc).__name__,
            exc,
        )
        return False
    try:
        # An empty lock file means the daemon never wrote its placeholder
        # byte — no daemon has held the lock since the file was created
        # (the daemon writes the byte BEFORE locking; see
        # ``daemon._acquire_singleton_lock``). Short-circuit: no daemon.
        # Also avoids msvcrt.locking's "region must overlap actual bytes"
        # error on Windows.
        if os.fstat(fh.fileno()).st_size == 0:
            return False
        fh.seek(0)
        if sys.platform == "win32":
            import msvcrt  # type: ignore[import-not-found, unused-ignore]

            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            # Best-effort release; fh.close() in `finally` releases anyway.
            with contextlib.suppress(OSError):
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl  # type: ignore[import-not-found, unused-ignore]

            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            with contextlib.suppress(OSError):
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()
    return False


def write(state: State, path: Path | None = None) -> None:
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a sibling tmp file then os.replace into place. A SIGKILL or
    # power loss between truncate and write completion would otherwise leave
    # an empty / partial state.json — _read_at would log WARNING and return
    # None, and daemon.run's singleton check would then proceed and start a
    # second daemon while the first is still alive (followup item 20).
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(_encode(state), indent=2), encoding="utf-8")
    # Parse-back guard: a future serializer regression producing malformed JSON
    # would otherwise be committed by os.replace, and _read_at's JSONDecodeError
    # arm would silently fall through to "no prior daemon" — same singleton-
    # bypass mode item 20 already defended (followup item 55).
    try:
        json.loads(tmp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def read(path: Path | None = None) -> State | None:
    return _read_at(path or default_path())


def _read_at(path: Path) -> State | None:
    # OSError (locked / permission-denied / cloud-placeholder) warns and
    # returns None instead of propagating, so CLI verbs that consult state
    # best-effort (`status`, `clear`'s daemon-alive guard, daemon legacy-state
    # migration) don't crash on a stale-or-broken file. Followup items 24, 97.
    # The middle arm catches the JSON-syntax + shape errors that `_decode`
    # raises (KeyError on missing last_error sub-key; TypeError on non-dict
    # last_error; ValueError on a stored datetime that no longer parses).
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _decode(raw)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("State file %s corrupt or shape-mismatched: %s", path, exc)
        return None
    except OSError as exc:
        logger.warning("State file %s unreadable: %s", path, exc)
        return None


def _encode(state: State) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "daemon_pid": state.daemon_pid,
        "daemon_create_time": state.daemon_create_time,
        "daemon_started": state.daemon_started.isoformat() if state.daemon_started else None,
        "last_sweep": state.last_sweep.isoformat() if state.last_sweep else None,
        "last_sweep_duration_s": state.last_sweep_duration_s,
        "last_sweep_marked": state.last_sweep_marked,
        "last_sweep_cleared": state.last_sweep_cleared,
        "last_sweep_errors": state.last_sweep_errors,
        "last_sweep_conflicts": state.last_sweep_conflicts,
        "last_error": {
            "time": state.last_error.time.isoformat(),
            "path": str(state.last_error.path),
            "message": state.last_error.message,
        }
        if state.last_error
        else None,
        "watched_roots": [str(p) for p in state.watched_roots],
    }


def _decode(raw: dict[str, Any]) -> State:
    return State(
        daemon_pid=raw.get("daemon_pid"),
        # `daemon_create_time` is decode-tolerant: old state.json files
        # (pre-#79) lack the field and decode to None, which triggers the
        # legacy substring-name fallback in is_daemon_alive. But when the
        # field IS present, it MUST be a number — a hand-edited or shape-
        # mismatched state file with e.g. a string ``daemon_create_time``
        # would otherwise propagate to ``is_daemon_alive``'s
        # ``abs(live_create_time - create_time)`` arithmetic and raise
        # TypeError, breaking status / clear / the daemon's legacy-
        # startup guard. Raise ValueError here so ``_read_at``'s existing
        # corrupt-state arm catches it and ``read()`` returns None.
        # ``isinstance(True, int)`` is True (bool subclasses int) so
        # explicit bool exclusion is required to reject hand-edited
        # ``"daemon_create_time": true`` values.
        daemon_create_time=_validate_create_time(raw.get("daemon_create_time")),
        daemon_started=_parse_dt(raw.get("daemon_started")),
        last_sweep=_parse_dt(raw.get("last_sweep")),
        last_sweep_duration_s=raw.get("last_sweep_duration_s", 0.0),
        last_sweep_marked=raw.get("last_sweep_marked", 0),
        last_sweep_cleared=raw.get("last_sweep_cleared", 0),
        last_sweep_errors=raw.get("last_sweep_errors", 0),
        # Decode-tolerant: pre-#68 state.json files lack this field and
        # decode to 0, which keeps `status --summary conflicts=0` sane until
        # the next daemon sweep refreshes the count.
        last_sweep_conflicts=raw.get("last_sweep_conflicts", 0),
        last_error=LastError(
            time=datetime.fromisoformat(raw["last_error"]["time"]),
            path=Path(raw["last_error"]["path"]),
            message=raw["last_error"]["message"],
        )
        if raw.get("last_error")
        else None,
        watched_roots=[Path(p) for p in raw.get("watched_roots", [])],
    )


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _validate_create_time(value: Any) -> float | None:
    """Coerce a JSON-decoded ``daemon_create_time`` to a float or raise.

    Accepts None (field absent — pre-#79 state.json) and numeric values
    (int or float). Rejects bool (a Python int subclass), strings, lists,
    dicts, and anything else. ValueError surfaces through ``_read_at``'s
    corrupt-state arm so ``read()`` returns None on a hand-edited or
    shape-mismatched record.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"daemon_create_time must be numeric, got {type(value).__name__}")
    return float(value)

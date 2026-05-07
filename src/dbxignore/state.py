"""Persist daemon state under the platform's per-user state directory.

Windows: ``%LOCALAPPDATA%\\dbxignore\\state.json``.
Linux: ``$XDG_STATE_HOME/dbxignore/state.json`` (fallback ``~/.local/state/...``).
macOS: ``~/Library/Application Support/dbxignore/state.json``
       (logs split off to ``~/Library/Logs/dbxignore/`` per Apple's app-data conventions).
"""

from __future__ import annotations

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
    to avoid. Frozen PyInstaller installs run as ``dbxignored.exe``;
    source runs are typically ``python -m dbxignore daemon`` (or pytest
    under the test suite).

    The second stage, gated on a non-None ``create_time``, additionally
    requires the live process's ``psutil.Process.create_time()`` to match
    the caller-supplied value. This is the followup item #79 enhancement:
    a substring-name match is not enough when the recycled PID's new
    occupant happens to also be a python process (very common when the
    test suite or any python tooling runs after a daemon dies). Comparing
    the create_time disambiguates "still that daemon" from "PID was
    recycled by another python".

    Lazy-imports ``psutil``; falls back to ``os.kill(pid, 0)`` for the
    bare-existence check when ``psutil`` isn't installed (in which case
    PID-reuse can't be detected and ``create_time`` is silently ignored —
    a known limitation, not a behavior bug). Used by ``cli.status`` to
    render the "running / not running / state may be stale" UI and by
    ``daemon._is_other_live_daemon`` for the singleton check.
    """
    if pid is None:
        return False
    try:
        import psutil  # type: ignore[import-untyped, unused-ignore]
    except ImportError:
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False
        return True
    if not psutil.pid_exists(pid):
        return False
    try:
        proc = psutil.Process(pid)
        name = proc.name().lower()
    except psutil.Error:
        return False
    if "python" not in name and "dbxignored" not in name:
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
    if not path.exists():
        return None
    # Catch both JSON-syntax errors and shape errors raised by _decode (KeyError
    # if a nested last_error sub-key is missing; TypeError if last_error is
    # present but not a dict; ValueError if a stored datetime no longer parses).
    # Without _decode being inside the try, a hand-edited or schema-mismatched
    # state.json crashes the daemon on startup instead of falling back to
    # "no prior state" — followup item 24.
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _decode(raw)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("State file %s corrupt or shape-mismatched: %s", path, exc)
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
        # legacy substring-name fallback in is_daemon_alive.
        daemon_create_time=raw.get("daemon_create_time"),
        daemon_started=_parse_dt(raw.get("daemon_started")),
        last_sweep=_parse_dt(raw.get("last_sweep")),
        last_sweep_duration_s=raw.get("last_sweep_duration_s", 0.0),
        last_sweep_marked=raw.get("last_sweep_marked", 0),
        last_sweep_cleared=raw.get("last_sweep_cleared", 0),
        last_sweep_errors=raw.get("last_sweep_errors", 0),
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

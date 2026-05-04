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


def _encode(state: State) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "daemon_pid": state.daemon_pid,
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
        } if state.last_error else None,
        "watched_roots": [str(p) for p in state.watched_roots],
    }


def _decode(raw: dict) -> State:
    return State(
        daemon_pid=raw.get("daemon_pid"),
        daemon_started=_parse_dt(raw.get("daemon_started")),
        last_sweep=_parse_dt(raw.get("last_sweep")),
        last_sweep_duration_s=raw.get("last_sweep_duration_s", 0.0),
        last_sweep_marked=raw.get("last_sweep_marked", 0),
        last_sweep_cleared=raw.get("last_sweep_cleared", 0),
        last_sweep_errors=raw.get("last_sweep_errors", 0),
        last_error=LastError(
            time=_parse_dt(raw["last_error"]["time"]),
            path=Path(raw["last_error"]["path"]),
            message=raw["last_error"]["message"],
        ) if raw.get("last_error") else None,
        watched_roots=[Path(p) for p in raw.get("watched_roots", [])],
    )


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None

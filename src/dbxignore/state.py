"""Persist daemon state under the platform's per-user state directory.

Windows: ``%LOCALAPPDATA%\\dbxignore\\state.json``.
Linux: ``$XDG_STATE_HOME/dbxignore/state.json`` (fallback ``~/.local/state/...``).
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
    """Per-user directory where dbxignore persists state and log files."""
    if sys.platform == "win32":
        localappdata = os.environ.get("LOCALAPPDATA")
        base = Path(localappdata) if localappdata else Path.home() / "AppData" / "Local"
        return base / "dbxignore"
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "dbxignore"


def default_path() -> Path:
    return user_state_dir() / "state.json"


def _legacy_linux_path() -> Path:
    # Pre-XDG builds wrote to a Windows-shaped tree even on Linux. Keep one
    # release of read-time fallback so existing installs keep their sweep
    # stats after upgrade; the next write() persists to the XDG path.
    return Path.home() / "AppData" / "Local" / "dropboxignore" / "state.json"


def write(state: State, path: Path | None = None) -> None:
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_encode(state), indent=2), encoding="utf-8")


def read(path: Path | None = None) -> State | None:
    if path is not None:
        return _read_at(path)
    default = default_path()
    if default.exists():
        return _read_at(default)
    if sys.platform.startswith("linux"):
        legacy = _legacy_linux_path()
        if legacy.exists():
            logger.warning(
                "Reading state from legacy path %s; next write will persist to %s. "
                "Delete the legacy file after verifying the migration.",
                legacy, default,
            )
            return _read_at(legacy)
    return None


def _read_at(path: Path) -> State | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("State file %s corrupt: %s", path, exc)
        return None
    return _decode(raw)


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

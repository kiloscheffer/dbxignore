"""Discover configured Dropbox root paths from Dropbox's own info.json."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _read_dropbox_account_paths(info_path: Path) -> list[str]:
    """Parse a Dropbox ``info.json`` and return per-account ``path`` strings.

    Returns zero or more strings, one per dict-shaped account entry with a
    non-empty string ``path`` field. Iterates over ``data.values()`` rather
    than a hardcoded account-type allow-list, so any current or future
    Dropbox account type (today: ``personal`` / ``business``) is picked up
    automatically.

    Raises ``OSError`` (file missing or unreadable), ``UnicodeDecodeError``
    (file isn't valid UTF-8), ``json.JSONDecodeError`` (malformed JSON), or
    ``ValueError`` (top-level value is not an object). Callers wrap with
    their own try/except so they can choose between WARNING-with-detail
    (``roots.discover()``) and silent fallback (mode detection in the macOS
    backend on hosts where Dropbox isn't installed).
    """
    data = json.loads(info_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"top-level value is not an object: {type(data).__name__}")
    paths: list[str] = []
    for account in data.values():
        if isinstance(account, dict):
            p = account.get("path")
            if isinstance(p, str) and p:
                paths.append(p)
    return paths


def find_containing(path: Path, roots: list[Path]) -> Path | None:
    """Return the first root that contains ``path``, or ``None`` if none do."""
    for root in roots:
        try:
            path.relative_to(root)
            return root
        except ValueError:
            continue
    return None


def _info_json_paths() -> list[Path]:
    """Return candidate Dropbox info.json locations, in priority order.

    Windows: Dropbox's per-user installer writes ``%APPDATA%\\Dropbox\\info.json``;
    the per-machine installer (also called "install for all users") writes
    ``%LOCALAPPDATA%\\Dropbox\\info.json``. Check both, ``%APPDATA%`` first
    since the per-user installer is the more common shape.

    Linux + macOS: Dropbox desktop places ``info.json`` at
    ``~/.dropbox/info.json`` on both, so a single arm covers them.

    Empty list signals "no candidates derivable from environment" — caller
    treats it the same as "no info.json exists" and returns ``[]`` from
    ``discover()`` so the daemon's "no roots" path fires cleanly.
    """
    if sys.platform == "win32":
        candidates: list[Path] = []
        for env_var in ("APPDATA", "LOCALAPPDATA"):
            value = os.environ.get(env_var)
            if value:
                candidates.append(Path(value) / "Dropbox" / "info.json")
        if not candidates:
            logger.warning("Neither APPDATA nor LOCALAPPDATA set; cannot locate Dropbox info.json")
        return candidates
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        home = os.environ.get("HOME")
        if not home:
            logger.warning("HOME not set; cannot locate Dropbox info.json")
            return []
        return [Path(home) / ".dropbox" / "info.json"]
    logger.warning("Unsupported platform %s; cannot locate Dropbox info.json", sys.platform)
    return []


def discover() -> list[Path]:
    override = os.environ.get("DBXIGNORE_ROOT")
    if override:
        override_path = Path(override)
        # The override needs to be an absolute existing directory:
        # - relative paths drift with CWD, and Task Scheduler / systemd /
        #   launchd each pick their own daemon CWD at launch.
        # - a file path becomes a "root" silently producing no-op applies
        #   and breaks the watchdog observer's recursive schedule.
        if not override_path.is_absolute():
            logger.warning(
                "DBXIGNORE_ROOT=%s is not an absolute path; ignoring override",
                override_path,
            )
            return []
        if not override_path.exists():
            logger.warning(
                "DBXIGNORE_ROOT=%s does not exist; ignoring override",
                override_path,
            )
            return []
        if not override_path.is_dir():
            logger.warning(
                "DBXIGNORE_ROOT=%s is not a directory; ignoring override",
                override_path,
            )
            return []
        return [override_path]

    candidates = _info_json_paths()
    if not candidates:
        return []

    info_path: Path | None = None
    for candidate in candidates:
        if candidate.exists():
            info_path = candidate
            break

    if info_path is None:
        if len(candidates) == 1:
            logger.warning("Dropbox info.json not found at %s", candidates[0])
        else:
            joined = ", ".join(str(p) for p in candidates)
            logger.warning("Dropbox info.json not found at any of: %s", joined)
        return []

    try:
        account_paths = _read_dropbox_account_paths(info_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Cannot read Dropbox info.json at %s: %s", info_path, exc)
        return []
    return [Path(p) for p in account_paths]

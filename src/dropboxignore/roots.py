"""Discover configured Dropbox root paths from Dropbox's own info.json."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_ACCOUNT_TYPES = ("personal", "business")


def find_containing(path: Path, roots: list[Path]) -> Path | None:
    """Return the first root that contains ``path``, or ``None`` if none do."""
    for root in roots:
        try:
            path.relative_to(root)
            return root
        except ValueError:
            continue
    return None


def _info_json_path() -> Path | None:
    """Return the platform's Dropbox info.json location, or None if unknown."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            logger.warning("APPDATA not set; cannot locate Dropbox info.json")
            return None
        return Path(appdata) / "Dropbox" / "info.json"
    if sys.platform.startswith("linux"):
        home = os.environ.get("HOME")
        if not home:
            logger.warning("HOME not set; cannot locate Dropbox info.json")
            return None
        return Path(home) / ".dropbox" / "info.json"
    logger.warning("Unsupported platform %s; cannot locate Dropbox info.json", sys.platform)
    return None


def discover() -> list[Path]:
    info_path = _info_json_path()
    if info_path is None:
        return []

    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("Dropbox info.json not found at %s", info_path)
        return []
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Cannot read Dropbox info.json at %s: %s", info_path, exc)
        return []

    if not isinstance(data, dict):
        logger.warning(
            "Unexpected Dropbox info.json structure at %s (top-level is not an object)", info_path
        )
        return []

    roots: list[Path] = []
    for account_type in _ACCOUNT_TYPES:
        account = data.get(account_type)
        if isinstance(account, dict) and isinstance(account.get("path"), str):
            roots.append(Path(account["path"]))
    return roots

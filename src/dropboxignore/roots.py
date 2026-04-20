"""Discover configured Dropbox root paths from Dropbox's own info.json."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_ACCOUNT_TYPES = ("personal", "business")


def discover() -> list[Path]:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        logger.warning("APPDATA environment variable not set; cannot locate Dropbox info.json")
        return []

    info_path = Path(appdata) / "Dropbox" / "info.json"
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

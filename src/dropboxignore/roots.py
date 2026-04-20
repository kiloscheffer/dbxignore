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
    if not info_path.exists():
        logger.warning("Dropbox info.json not found at %s", info_path)
        return []

    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Malformed Dropbox info.json at %s: %s", info_path, exc)
        return []

    roots: list[Path] = []
    for account_type in _ACCOUNT_TYPES:
        account = data.get(account_type)
        if isinstance(account, dict) and isinstance(account.get("path"), str):
            roots.append(Path(account["path"]))
    return roots

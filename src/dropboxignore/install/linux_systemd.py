"""Generate and install a systemd user unit for the daemon on Linux."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

UNIT_NAME = "dropboxignore.service"


def _unit_path() -> Path:
    """Return ``~/.config/systemd/user/dropboxignore.service``."""
    import os
    home = os.environ.get("HOME")
    if not home:
        raise RuntimeError("HOME not set; cannot locate systemd user unit directory")
    return Path(home) / ".config" / "systemd" / "user" / UNIT_NAME


def _detect_invocation() -> tuple[Path, str]:
    """Return (executable, arguments) to run the daemon in the current install."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable), ""
    # uv tool install places a `dropboxignored` shim on PATH.
    exe = shutil.which("dropboxignored")
    if exe:
        return Path(exe), ""
    # Fallback: the current Python + `-m dropboxignore daemon`.
    python = shutil.which("python3") or sys.executable
    if not python:
        raise RuntimeError(
            "dropboxignored not on PATH and no python3 found; "
            "run `uv tool install .` from the dropboxignore checkout first"
        )
    return Path(python), "-m dropboxignore daemon"


def build_unit_content(exe_path: Path, arguments: str = "") -> str:
    """Return the full [Unit]/[Service]/[Install] text for the systemd user unit."""
    exec_start = f"{exe_path} {arguments}".strip()
    return f"""[Unit]
Description=dropboxignore daemon
Documentation=https://github.com/kiloscheffer/dropboxignore
After=default.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=60s

[Install]
WantedBy=default.target
"""


def install_unit() -> None:
    exe, args = _detect_invocation()
    content = build_unit_content(exe, args)
    path = _unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info("Wrote systemd user unit to %s", path)

    subprocess.run(  # noqa: S603 — hardcoded args, no user data
        ["systemctl", "--user", "daemon-reload"], check=True,
    )
    subprocess.run(  # noqa: S603 — hardcoded args, no user data
        ["systemctl", "--user", "enable", "--now", UNIT_NAME], check=True,
    )
    logger.info("Enabled and started %s", UNIT_NAME)


def uninstall_unit() -> None:
    path = _unit_path()
    # disable --now: stop and disable. Missing unit → non-zero exit, which we swallow.
    subprocess.run(  # noqa: S603 — hardcoded args, no user data
        ["systemctl", "--user", "disable", "--now", UNIT_NAME],
        check=False, capture_output=True, text=True,
    )
    if path.exists():
        path.unlink()
        logger.info("Removed %s", path)
    subprocess.run(  # noqa: S603 — hardcoded args, no user data
        ["systemctl", "--user", "daemon-reload"], check=True,
    )

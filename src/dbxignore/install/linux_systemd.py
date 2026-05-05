"""Generate and install a systemd user unit for the daemon on Linux."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from dbxignore.install._common import detect_invocation

logger = logging.getLogger(__name__)

UNIT_NAME = "dbxignore.service"

# Env vars that `install_unit()` forwards from the caller's shell into the
# generated unit's `[Service]` block. Scoped to DBXIGNORE_ROOT because
# without it the daemon silently falls back to `~/.dropbox/info.json`
# discovery, leaving non-stock-Dropbox users confused. Other DBXIGNORE_*
# vars are optional tuning with sensible defaults — users who want to adjust
# them can drop in their own override under `dbxignore.service.d/`.
_FORWARDED_ENV_VARS = ("DBXIGNORE_ROOT",)


def _unit_path() -> Path:
    """Return ``~/.config/systemd/user/dbxignore.service``."""
    home = os.environ.get("HOME")
    if not home:
        raise RuntimeError("HOME not set; cannot locate systemd user unit directory")
    return Path(home) / ".config" / "systemd" / "user" / UNIT_NAME


def _escape_systemd_env_value(value: str) -> str:
    """Escape backslash + double-quote for use inside a quoted Environment= line.

    systemd's unit-file parser treats ``Environment="KEY=VALUE"`` as one
    assignment; literal backslashes and double-quotes inside VALUE must be
    doubled and backslash-escaped respectively so the parser doesn't
    misinterpret them as escape sequences or a premature end of the quoted
    string.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _run_systemctl(cmd: list[str]) -> None:
    """Run a systemctl command; convert CalledProcessError → RuntimeError.

    Callers (notably cli.install / cli.uninstall) catch RuntimeError to
    surface failures cleanly. subprocess.CalledProcessError is not a
    RuntimeError, so without this wrapping a failed systemctl call would
    escape as a raw traceback.
    """
    try:
        subprocess.run(cmd, check=True)  # noqa: S603 — hardcoded args, no user data
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"{' '.join(cmd)} failed with exit code {exc.returncode}") from exc


def build_unit_content(
    exe_path: Path,
    arguments: str = "",
    environment: dict[str, str] | None = None,
) -> str:
    """Return the full [Unit]/[Service]/[Install] text for the systemd user unit.

    ``environment`` (if given) is emitted as one quoted ``Environment="KEY=VALUE"``
    line per entry, placed before ``ExecStart=`` in ``[Service]`` so the daemon
    process sees the variable by the time it runs.
    """
    exec_start = f"{exe_path.as_posix()} {arguments}".strip()
    env_lines = ""
    if environment:
        env_lines = (
            "\n".join(
                f'Environment="{key}={_escape_systemd_env_value(value)}"'
                for key, value in environment.items()
            )
            + "\n"
        )
    return f"""[Unit]
Description=dbxignore daemon
Documentation=https://github.com/kiloscheffer/dbxignore
After=default.target

[Service]
Type=simple
{env_lines}ExecStart={exec_start}
Restart=on-failure
RestartSec=60s

[Install]
WantedBy=default.target
"""


def install_unit() -> None:
    exe, args = detect_invocation()
    environment = {name: os.environ[name] for name in _FORWARDED_ENV_VARS if os.environ.get(name)}
    content = build_unit_content(exe, args, environment=environment or None)
    path = _unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info("Wrote systemd user unit to %s", path)
    if environment:
        logger.info(
            "Forwarded environment into unit: %s",
            ", ".join(sorted(environment.keys())),
        )

    _run_systemctl(["systemctl", "--user", "daemon-reload"])
    _run_systemctl(["systemctl", "--user", "enable", "--now", UNIT_NAME])
    logger.info("Enabled and started %s", UNIT_NAME)


def uninstall_unit() -> None:
    path = _unit_path()
    # disable --now: stop and disable. Missing unit → non-zero exit, which we swallow.
    subprocess.run(  # noqa: S603 — hardcoded args, no user data
        ["systemctl", "--user", "disable", "--now", UNIT_NAME],
        check=False,
        capture_output=True,
        text=True,
    )
    if path.exists():
        path.unlink()
        logger.info("Removed %s", path)
    _run_systemctl(["systemctl", "--user", "daemon-reload"])


def remove_dropin_directory() -> Path | None:
    """Remove the systemd drop-in directory for the unit, if it exists.

    Drop-in directories live at ``~/.config/systemd/user/<unit-name>.d/``
    and are where users put ``Environment=`` overrides (see the
    "Install (Linux)" section of the README). On a full `--purge`
    uninstall, we clean this up too so no dbxignore-related artifacts
    linger.

    Returns the path that was removed, or ``None`` if HOME is unset or
    the directory didn't exist.
    """
    home = os.environ.get("HOME")
    if not home:
        return None
    dropin_dir = Path(home) / ".config" / "systemd" / "user" / f"{UNIT_NAME}.d"
    if not dropin_dir.exists():
        return None
    shutil.rmtree(dropin_dir)
    return dropin_dir

"""Generate and install a launchd User Agent for the dbxignore daemon on macOS.

Plist is written to ~/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist
and bootstrapped into the user's GUI session domain (gui/<uid>) via the
modern launchctl bootstrap/bootout commands. Legacy `launchctl load -w`/
`unload -w` is intentionally not used — see the v0.4 spec for rationale.

GUI-domain prerequisite: `launchctl bootstrap gui/<uid>` requires the
user has logged into the macOS GUI at least once since the last reboot.
SSH-on-fresh-boot installs fail with "Bootstrap failed: 5: Input/output
error". Documented in the README.
"""
from __future__ import annotations

import logging
import os
import plistlib
import subprocess
from pathlib import Path

from dbxignore import state as state_module
from dbxignore.install._common import detect_invocation

logger = logging.getLogger(__name__)

LABEL = "com.kiloscheffer.dbxignore"
PLIST_FILENAME = f"{LABEL}.plist"

# Env vars `install_agent()` forwards from the caller's shell into the
# generated plist's EnvironmentVariables. Scoped to DBXIGNORE_ROOT for the
# same reason linux_systemd.py forwards it — without it, the daemon
# silently falls back to `~/.dropbox/info.json` discovery, leaving
# non-stock-Dropbox users confused. Other DBXIGNORE_* vars are tuning
# knobs with sensible defaults.
_FORWARDED_ENV_VARS = ("DBXIGNORE_ROOT",)


def _plist_path() -> Path:
    """Return ~/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist."""
    home = os.environ.get("HOME")
    if not home:
        raise RuntimeError("HOME not set; cannot locate ~/Library/LaunchAgents")
    return Path(home) / "Library" / "LaunchAgents" / PLIST_FILENAME


def _domain() -> str:
    """User's GUI session domain — required for LaunchAgents that need user env."""
    return f"gui/{os.getuid()}"


def _service_target(label: str = LABEL) -> str:
    """Full launchd service target for bootout/bootstrap calls."""
    return f"{_domain()}/{label}"


def _run_launchctl(cmd: list[str]) -> None:
    """Run a launchctl command; raise RuntimeError on non-zero exit.

    `subprocess.run(check=True)` raises CalledProcessError; we wrap it in
    RuntimeError because cli.install / cli.uninstall catch RuntimeError to
    produce clean error output rather than a raw traceback. Includes
    launchctl's stderr in the message — typical failure modes
    (`Bootstrap failed: 5: Input/output error` for missing GUI session)
    are self-explanatory.
    """
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)  # noqa: S603 — hardcoded
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"{' '.join(cmd)} failed (exit {exc.returncode}): {exc.stderr.strip()}"
        ) from exc


def build_plist_content(
    label: str,
    program_arguments: list[str],
    log_dir: Path,
    environment: dict[str, str] | None = None,
) -> bytes:
    """Generate the launchd User Agent plist as bytes (XML format).

    KeepAlive: {SuccessfulExit: false, Crashed: true} matches the spirit
    of systemd's Restart=on-failure — restart on non-zero exit or signal
    kill, but respect a clean `launchctl bootout` (clean shutdown).
    launchd's built-in 10s throttle covers what RestartSec=60s does
    explicitly on systemd.

    StandardOutPath/StandardErrorPath both point at log_dir/launchd.log
    so we capture any startup-time output the daemon emits before its
    own RotatingFileHandler initializes. In normal operation this file
    stays near-empty.
    """
    plist: dict = {
        "Label": label,
        "ProgramArguments": program_arguments,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False, "Crashed": True},
        "StandardOutPath": (log_dir / "launchd.log").as_posix(),
        "StandardErrorPath": (log_dir / "launchd.log").as_posix(),
    }
    if environment:
        plist["EnvironmentVariables"] = environment
    return plistlib.dumps(plist)


def install_agent() -> None:
    exe, args = detect_invocation()
    program_args = [str(exe)] + (args.split() if args else [])
    environment = {
        name: os.environ[name]
        for name in _FORWARDED_ENV_VARS
        if os.environ.get(name)
    }
    log_dir = state_module.user_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    content = build_plist_content(LABEL, program_args, log_dir, environment or None)
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    logger.info("Wrote launchd plist to %s", path)
    if environment:
        logger.info(
            "Forwarded environment into plist: %s",
            ", ".join(sorted(environment.keys())),
        )

    # Bootout an existing instance first; bootstrap fails on duplicate
    # label with EEXIST. Missing-service errors on first install are
    # expected — swallow.
    subprocess.run(  # noqa: S603 — hardcoded args, no user data
        ["launchctl", "bootout", _service_target()],
        check=False, capture_output=True,
    )
    _run_launchctl(["launchctl", "bootstrap", _domain(), str(path)])
    logger.info("Bootstrapped %s into %s", LABEL, _domain())


def uninstall_agent() -> None:
    # Bootout — missing service is fine, swallow.
    subprocess.run(  # noqa: S603 — hardcoded args, no user data
        ["launchctl", "bootout", _service_target()],
        check=False, capture_output=True,
    )
    path = _plist_path()
    if path.exists():
        path.unlink()
        logger.info("Removed %s", path)

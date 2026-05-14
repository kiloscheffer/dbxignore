"""Generate and install a launchd User Agent for the dbxignore daemon on macOS.

Plist is written to ~/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist
and bootstrapped into the user's GUI session domain (gui/<uid>) via the
launchctl bootstrap/bootout commands. The legacy `launchctl load -w` /
`unload -w` pair is intentionally not used: bootstrap/bootout target the
GUI session domain explicitly, whereas `load -w` is domain-ambiguous and
deprecated on current macOS.

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

from dbxignore import _testing
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
    return f"gui/{os.getuid()}"  # type: ignore[attr-defined, unused-ignore]


def _service_target(label: str = LABEL) -> str:
    """Full launchd service target for bootout/bootstrap calls."""
    return f"{_domain()}/{label}"


# Stderr substrings (case-insensitive) that launchctl emits when bootout's
# target service isn't currently loaded — the idempotent-uninstall case.
# A re-run after a successful uninstall, a manual `launchctl bootout`
# between install and uninstall, or a daemon crash all reach `uninstall_agent`
# with the service already gone; treating any of these as failure would
# regress the "uninstall is idempotent" contract Linux/Windows uphold via
# their respective service-managers. macOS phrasing varies by major version:
# "No such process" maps to errno-3 ESRCH (modern macOS); "Could not find
# service" / "Could not find specified service" are the Ventura/Sequoia
# wordings; "not loaded" appears in older 10.x logs. If a future macOS rev
# adds a new phrase, the user-visible symptom is "second uninstall fails
# noisily" — fix forward by extending this list.
_NOT_LOADED_STDERR_PATTERNS = (
    "no such process",
    "could not find service",
    "could not find specified service",
    "not loaded",
)


def _is_service_not_loaded(stderr: str) -> bool:
    """Return True if launchctl bootout's stderr indicates the target service
    wasn't loaded (idempotent uninstall), False otherwise. Matches the
    patterns in ``_NOT_LOADED_STDERR_PATTERNS`` case-insensitively."""
    lowered = stderr.lower()
    return any(pat in lowered for pat in _NOT_LOADED_STDERR_PATTERNS)


def _run_launchctl(cmd: list[str]) -> None:
    """Run a launchctl command; raise RuntimeError on non-zero exit or invocation failure.

    `subprocess.run(check=True)` raises CalledProcessError; we wrap it in
    RuntimeError because cli.install / cli.uninstall catch RuntimeError to
    produce clean error output rather than a raw traceback. Includes
    launchctl's stderr in the message — typical failure modes
    (`Bootstrap failed: 5: Input/output error` for missing GUI session)
    are self-explanatory. The OSError arm handles the rare case where
    `launchctl` itself can't be invoked (FileNotFoundError if the binary
    is missing — atypical on macOS but possible in stripped-down
    environments — or PermissionError).
    """
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)  # noqa: S603 — hardcoded
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"{' '.join(cmd)} failed (exit {exc.returncode}): {exc.stderr.strip()}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"{' '.join(cmd)} could not be invoked: {exc}") from exc


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
    plist: dict[str, object] = {
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
    environment = {name: os.environ[name] for name in _FORWARDED_ENV_VARS if os.environ.get(name)}
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
    # expected — swallow.  The OSError arm also tolerates `launchctl`
    # itself being missing (FileNotFoundError, subclass of OSError);
    # without it, the FNFE would escape before the bootstrap call
    # below has a chance to surface a clean RuntimeError via
    # `_run_launchctl`. Same shape as the equivalent windows_task.py
    # pre-call.
    try:
        subprocess.run(  # noqa: S603 — hardcoded args, no user data
            ["launchctl", "bootout", _service_target()],
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        logger.debug("launchctl bootout pre-call could not be invoked: %s", exc)
    _run_launchctl(["launchctl", "bootstrap", _domain(), str(path)])
    logger.info("Bootstrapped %s into %s", LABEL, _domain())


def uninstall_agent() -> None:
    # Bootout is the actual daemon-shutdown step on macOS (unlike Windows
    # where schtasks /End + /Delete are distinct, or Linux where systemctl
    # --user disable --now handles both). Two failure modes need different
    # handling:
    #
    # 1. OSError on invocation (launchctl missing / PermissionError) — raise
    #    RuntimeError, preserve plist. Silently proceeding to plist removal
    #    would leave an orphaned live daemon mutating state.json and markers
    #    while `dbxignore uninstall` reports success — and a subsequent
    #    `--purge` would clear state under the running daemon.
    #
    # 2. Non-zero rc — distinguish "service was already unloaded" (idempotent
    #    success, proceed to plist removal) from "service refused to die"
    #    (real failure, raise + preserve plist). The distinction uses
    #    stderr-pattern matching via `_is_service_not_loaded` because
    #    launchctl's rc isn't a stable indicator across macOS versions.
    #
    # The prior shape discarded both rc and stderr, leaving the tug-of-war
    # silent. The fix below mirrors the eventual-consistency contract
    # Linux/Windows uphold via their respective service-managers.
    try:
        result = subprocess.run(  # noqa: S603 — hardcoded args, no user data
            ["launchctl", "bootout", _service_target()],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise RuntimeError(f"launchctl bootout could not be invoked: {exc}") from exc
    if _testing.fail_point_active("BOOTOUT"):
        # Substitute a confirmed-failure result: non-zero rc with stderr
        # that `_is_service_not_loaded` does NOT match, so the arm below
        # raises RuntimeError instead of treating it as idempotent success.
        result = subprocess.CompletedProcess(
            result.args,
            returncode=5,
            stdout="",
            stderr="injected bootout failure (DBXIGNORE_TEST_FAIL_BOOTOUT)",
        )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if not _is_service_not_loaded(stderr):
            raise RuntimeError(
                f"launchctl bootout returned {result.returncode}: "
                f"{stderr or (result.stdout or '').strip() or '(no output)'}"
            )
        logger.info(
            "launchctl bootout reported service not loaded (rc=%s); proceeding to plist removal",
            result.returncode,
        )
    path = _plist_path()
    if path.exists():
        path.unlink()
        logger.info("Removed %s", path)

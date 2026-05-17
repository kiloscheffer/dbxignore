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
# generated unit's `[Service]` block.
#
# DBXIGNORE_ROOT — without this forwarded, the daemon silently falls back
# to `~/.dropbox/info.json` discovery, leaving non-stock-Dropbox users
# confused.
#
# XDG_STATE_HOME — `state.user_state_dir()` resolves
# `$XDG_STATE_HOME/dbxignore` if set, falling back to `~/.local/state/...`
# otherwise. Without forwarding, a user who sets `XDG_STATE_HOME` in their
# shell ends up with their own tools (`dbxignore status` etc.) probing one
# path and the systemd-launched daemon writing to another, producing
# silent "no state.json" reports. Forwarding aligns both sides.
#
# Other DBXIGNORE_* vars are optional tuning with sensible defaults — users
# who want to adjust them can drop in their own override under
# `dbxignore.service.d/`.
_FORWARDED_ENV_VARS = ("DBXIGNORE_ROOT", "XDG_STATE_HOME")


def _unit_path() -> Path:
    """Return ``~/.config/systemd/user/dbxignore.service``."""
    home = os.environ.get("HOME")
    if not home:
        raise RuntimeError("HOME not set; cannot locate systemd user unit directory")
    return Path(home) / ".config" / "systemd" / "user" / UNIT_NAME


def _escape_systemd_quoted_string(value: str) -> str:
    """Escape backslash + double-quote for use inside a systemd quoted string.

    Same C-style escape rules apply to both ``Environment="KEY=VALUE"`` and
    ``ExecStart="/path with space"`` — literal backslashes are doubled and
    literal double-quotes are backslash-escaped so systemd's parser does not
    misinterpret them as escape sequences or a premature end-of-string.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _quote_exec_start_path(exe_path: Path) -> str:
    """Return the path rendered for an ``ExecStart=`` token.

    Two systemd parser concerns:

    - ``ExecStart`` splits on whitespace, so a path containing a space
      (e.g. ``/home/user/My Tools/dbxignore``) tokenizes incorrectly when
      bare. Wrap such paths in double quotes and C-style-escape embedded
      ``"`` and ``\\``.
    - systemd expands ``%X`` specifiers in ``ExecStart`` at unit-load time
      (``%T`` → ``/tmp``, ``%h`` → home, etc.), so a literal ``%`` must
      be doubled to ``%%`` regardless of quoting. Otherwise an install path
      like ``/home/me/100% Tools/dbxignore`` is silently rewritten by the
      specifier expander and the unit points at the wrong binary.

    A ``$`` in the path is deliberately **not** escaped: systemd only
    expands the bare ``$VAR`` form when it is the entire argument (per
    ``man systemd.service`` "Command Lines"; the docs note the first
    argument may not be a variable). A literal ``$`` embedded in the
    executable path is passed through unchanged. Doubling it to ``$$``
    would write a literal ``$$`` into argv0 that systemd does not collapse
    back in the executable position. The ``${VAR}`` form IS expanded
    mid-string, but a path literally containing ``${...}`` is rare enough
    to defer — and the escape for that form would be a more careful
    ``${`` → ``$${`` substitution, not a global ``$`` → ``$$``.

    Bare paths without whitespace or escape-needing chars stay unquoted to
    match the on-disk shape stock distro installs have today.
    """
    posix = exe_path.as_posix().replace("%", "%%")
    if any(ch.isspace() or ch in '"\\' for ch in posix):
        return f'"{_escape_systemd_quoted_string(posix)}"'
    return posix


def _run_systemctl(cmd: list[str]) -> None:
    """Run a systemctl command; convert CalledProcessError / OSError → RuntimeError.

    Callers (notably cli.install / cli.uninstall) catch RuntimeError to
    surface failures cleanly. subprocess.CalledProcessError is not a
    RuntimeError, so without this wrapping a failed systemctl call would
    escape as a raw traceback. The OSError arm covers the "systemctl
    binary not on PATH" case (FileNotFoundError, subclass of OSError) —
    real on minimal containers / chroots without systemd installed —
    and other invocation-level failures (sandbox EACCES etc.).
    """
    try:
        subprocess.run(cmd, check=True)  # noqa: S603 — hardcoded args, no user data
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"{' '.join(cmd)} failed with exit code {exc.returncode}") from exc
    except OSError as exc:
        raise RuntimeError(f"{' '.join(cmd)} could not be invoked: {exc}") from exc


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
    exec_start = f"{_quote_exec_start_path(exe_path)} {arguments}".strip()
    env_lines = ""
    if environment:
        # Symmetric with `_quote_exec_start_path`'s `%` → `%%` doubling:
        # systemd's specifier expansion runs inside `Environment=` values
        # too (per systemd.exec(5) "specifier expansion is possible"), so
        # a literal `%` in a forwarded value (e.g. `XDG_STATE_HOME=
        # /home/me/100%state`) would be silently rewritten by the
        # specifier expander. Doubled before the C-style escape because
        # the two transformations are orthogonal — backslash + quote
        # escapes do not add or remove `%` characters.
        env_lines = (
            "\n".join(
                f'Environment="{key}={_escape_systemd_quoted_string(value.replace("%", "%%"))}"'
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
    # disable --now: stop and disable. Missing unit → non-zero exit, which
    # `check=False` swallows. But `check=False` does NOT suppress OSError —
    # a `FileNotFoundError` (systemctl absent on a minimal container /
    # chroot) or sandbox `EACCES` would escape as a raw traceback. Tolerate
    # it here: the unit-file removal below is the meaningful cleanup, and
    # the `daemon-reload` call (via `_run_systemctl`) surfaces a genuinely-
    # missing systemctl as RuntimeError, which cli.uninstall catches.
    try:
        subprocess.run(  # noqa: S603 — hardcoded args, no user data
            ["systemctl", "--user", "disable", "--now", UNIT_NAME],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        logger.warning("systemctl disable --now %s could not be invoked: %s", UNIT_NAME, exc)
    if path.exists():
        # Convert an unlink OSError (permission denied, etc.) to RuntimeError
        # so cli.uninstall's `except RuntimeError` handler surfaces it as a
        # clean "Failed to uninstall daemon service" + exit 2 rather than a
        # raw traceback after partial cleanup.
        try:
            path.unlink()
        except OSError as exc:
            raise RuntimeError(f"could not remove systemd unit file {path}: {exc}") from exc
        logger.info("Removed %s", path)
    _run_systemctl(["systemctl", "--user", "daemon-reload"])


def remove_dropin_directory(errors: list[tuple[Path, str]] | None = None) -> Path | None:
    """Remove the systemd drop-in directory for the unit, if it exists.

    Drop-in directories live at ``~/.config/systemd/user/<unit-name>.d/``
    and are where users put ``Environment=`` overrides (see the
    "Install (Linux)" section of the README). On a full `--purge`
    uninstall, we clean this up too so no dbxignore-related artifacts
    linger.

    Returns the path that was removed, or ``None`` if HOME is unset, the
    directory didn't exist, or removal failed. ``--purge`` calls this
    mid-cleanup, so an ``shutil.rmtree`` OSError must NOT escape as a raw
    traceback that strands the rest of the purge: when ``errors`` is
    supplied, a removal failure appends ``(dropin_dir, message)`` to it and
    returns ``None``; the caller surfaces it through the same exit-2-gated
    accumulator as the marker / state-file errors.
    """
    home = os.environ.get("HOME")
    if not home:
        return None
    dropin_dir = Path(home) / ".config" / "systemd" / "user" / f"{UNIT_NAME}.d"
    if not dropin_dir.exists():
        return None
    try:
        shutil.rmtree(dropin_dir)
    except OSError as exc:
        logger.warning("Could not remove systemd drop-in directory %s: %s", dropin_dir, exc)
        if errors is not None:
            errors.append((dropin_dir, str(exc)))
        return None
    return dropin_dir

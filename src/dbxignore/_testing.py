"""Failure-injection hooks for end-to-end manual tests.

The manual-test shell scripts (``scripts/manual-test-*.{sh,ps1}``) drive
the real ``dbxignore`` binary as a subprocess. Their only lever into that
subprocess is the environment, so exit-2 error paths whose underlying
failure mode cannot be forced on a healthy machine are exercised by
setting a ``DBXIGNORE_TEST_FAIL_<name>`` env var that production code
honors at a specific boundary.

Every hook is inert unless its env var is set to a non-empty value, and
logs a WARNING when it fires so a leaked env var is diagnosable rather
than a silent behavior change.

Fail points
-----------
- ``MARKER_READ``  — ``cli._walk_marked_paths`` raises ``OSError`` before
  every ``markers.is_ignored`` read. Drives the ``scan_errors`` exit-2
  path of ``clear`` / ``list``.
- ``STATE_PURGE``  — ``cli._purge_dir`` raises ``OSError`` before each
  ``f.unlink()``. Drives the ``state_errors`` exit-2 path of
  ``uninstall --purge``.
- ``BOOTOUT``      — ``install.macos_launchd.uninstall_agent`` treats the
  ``launchctl bootout`` result as a confirmed non-zero-rc failure. Drives
  the bootout exit-2 path of ``uninstall`` on macOS.
- ``DAEMON_ALIVE`` — ``cli.uninstall``'s ``--purge`` daemon-alive gate
  fires as if a daemon survived service removal. Drives the daemon-alive
  purge-refusal exit-2 path.

Test-only. Nothing outside the manual-test scripts and ``tests/`` should
set these env vars. New fail points are a one-liner here (a docstring
entry) plus a one-line hook at the boundary.
"""

from __future__ import annotations

import errno
import logging
import os

logger = logging.getLogger(__name__)

_ENV_PREFIX = "DBXIGNORE_TEST_FAIL_"


def _is_armed(name: str) -> bool:
    """True if ``DBXIGNORE_TEST_FAIL_<name>`` is set to a non-empty value."""
    return bool(os.environ.get(f"{_ENV_PREFIX}{name}"))


def fail_point_active(name: str) -> bool:
    """Return True if the ``name`` fail point is armed via the environment.

    For boundaries that inject failure by substituting a value (a
    subprocess return code, a boolean) rather than raising. Logs a
    WARNING when it returns True.
    """
    if _is_armed(name):
        logger.warning(
            "failure-injection fail point %r is active (%s%s is set)",
            name,
            _ENV_PREFIX,
            name,
        )
        return True
    return False


def raise_if_fail_point(name: str, exc: OSError | None = None) -> None:
    """Raise ``exc`` if the ``name`` fail point is armed via the environment.

    For boundaries that inject failure by raising ``OSError`` into an
    existing ``except OSError`` arm. The default exception is
    ``OSError(errno.ENOTSUP, ...)`` — the errno a filesystem without
    xattr/ADS support reports, which is the real-world failure mode the
    marker-read fail point simulates. Logs a WARNING before raising.
    """
    if not _is_armed(name):
        return
    logger.warning(
        "failure-injection fail point %r is active (%s%s is set); raising",
        name,
        _ENV_PREFIX,
        name,
    )
    if exc is None:
        exc = OSError(errno.ENOTSUP, f"injected failure ({_ENV_PREFIX}{name})")
    raise exc

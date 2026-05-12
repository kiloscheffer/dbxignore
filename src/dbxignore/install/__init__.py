"""Platform-dispatched install/uninstall for the dbxignore daemon."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path

    from . import linux_systemd as linux_systemd
    from . import macos_launchd as macos_launchd
    from . import windows_task as windows_task


def install_service() -> None:
    if sys.platform == "win32":
        from dbxignore.install.windows_task import install_task

        install_task()
    elif sys.platform.startswith("linux"):
        from dbxignore.install.linux_systemd import install_unit

        install_unit()
    elif sys.platform == "darwin":
        from dbxignore.install.macos_launchd import install_agent

        install_agent()
    else:
        raise NotImplementedError(
            f"install: no backend for platform {sys.platform!r}; "
            "supported: 'win32', 'linux', 'darwin'"
        )


def uninstall_service() -> None:
    if sys.platform == "win32":
        from dbxignore.install.windows_task import uninstall_task

        uninstall_task()
    elif sys.platform.startswith("linux"):
        from dbxignore.install.linux_systemd import uninstall_unit

        uninstall_unit()
    elif sys.platform == "darwin":
        from dbxignore.install.macos_launchd import uninstall_agent

        uninstall_agent()
    else:
        raise NotImplementedError(
            f"uninstall: no backend for platform {sys.platform!r}; "
            "supported: 'win32', 'linux', 'darwin'"
        )


logger = logging.getLogger(__name__)

InstallOutcome = Literal["installed", "skipped-no-roots", "skipped-bad-roots", "skipped-platform"]
UninstallOutcome = Literal["uninstalled", "skipped-platform"]


def install_shell_integration_if_supported(*, dropbox_roots: list[Path]) -> InstallOutcome:
    """Install Windows Explorer right-click verbs; no-op on Linux/macOS.

    Branches on ``sys.platform`` first — non-Windows returns ``"skipped-platform"``
    without referencing the windows_shell module. On Windows, an empty
    ``dropbox_roots`` returns ``"skipped-no-roots"`` with a WARNING; a
    ``RuntimeError`` from the platform module (typically a refused root
    containing ``"``) returns ``"skipped-bad-roots"`` with a WARNING.
    """
    if sys.platform != "win32":
        logger.debug("shell-integration install: no-op on platform %s", sys.platform)
        return "skipped-platform"
    if not dropbox_roots:
        logger.warning(
            "shell-integration install: no Dropbox roots discovered; skipping. "
            "Re-run `dbxignore install` after Dropbox is set up."
        )
        return "skipped-no-roots"
    from dbxignore.install.windows_shell import install_shell_integration

    try:
        install_shell_integration(dropbox_roots)
    except RuntimeError as exc:
        logger.warning("shell-integration install refused: %s", exc)
        return "skipped-bad-roots"
    return "installed"


def uninstall_shell_integration_if_supported(
    *, errors: list[tuple[str, str]] | None = None
) -> UninstallOutcome:
    """Remove Windows Explorer right-click verbs; no-op on Linux/macOS.

    The optional ``errors`` accumulator is threaded through to the platform
    module so CLI ``--purge`` can escalate registry failures into a non-zero
    exit. When ``errors=None`` (plain ``uninstall``), the platform module
    falls back to logging WARNINGs for each failed DeleteKey.
    """
    if sys.platform != "win32":
        logger.debug("shell-integration uninstall: no-op on platform %s", sys.platform)
        return "skipped-platform"
    from dbxignore.install.windows_shell import uninstall_shell_integration

    uninstall_shell_integration(errors=errors)
    return "uninstalled"

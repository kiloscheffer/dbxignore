"""Platform-dispatched install/uninstall for the dbxignore daemon."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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

"""Platform-dispatched install/uninstall for the dropboxignore daemon."""

from __future__ import annotations

import sys


def install_service() -> None:
    if sys.platform == "win32":
        from dropboxignore.install.windows_task import install_task
        install_task()
    elif sys.platform.startswith("linux"):
        from dropboxignore.install.linux_systemd import install_unit
        install_unit()
    else:
        raise NotImplementedError(
            f"install: no backend for platform {sys.platform!r}; "
            "supported: 'win32', 'linux'"
        )


def uninstall_service() -> None:
    if sys.platform == "win32":
        from dropboxignore.install.windows_task import uninstall_task
        uninstall_task()
    elif sys.platform.startswith("linux"):
        from dropboxignore.install.linux_systemd import uninstall_unit
        uninstall_unit()
    else:
        raise NotImplementedError(
            f"uninstall: no backend for platform {sys.platform!r}; "
            "supported: 'win32', 'linux'"
        )

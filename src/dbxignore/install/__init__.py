"""Platform-dispatched install/uninstall for the dbxignore daemon."""

from __future__ import annotations

import sys


def install_service() -> None:
    if sys.platform == "win32":
        from dbxignore.install.windows_task import install_task
        install_task()
    elif sys.platform.startswith("linux"):
        from dbxignore.install.linux_systemd import install_unit
        install_unit()
    elif sys.platform == "darwin":
        raise NotImplementedError(
            "macOS launchd installer is not yet wired (lands in v0.4 PR 4). "
            "Build dbxignore from a checkout that includes "
            "src/dbxignore/install/macos_launchd.py to get install/uninstall "
            "working on macOS."
        )
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
        raise NotImplementedError(
            "macOS launchd uninstaller is not yet wired (lands in v0.4 PR 4). "
            "Build dbxignore from a checkout that includes "
            "src/dbxignore/install/macos_launchd.py to get install/uninstall "
            "working on macOS."
        )
    else:
        raise NotImplementedError(
            f"uninstall: no backend for platform {sys.platform!r}; "
            "supported: 'win32', 'linux', 'darwin'"
        )

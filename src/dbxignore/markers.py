"""Platform-dispatched ignore-marker API.

Every module that needs to read or write the Dropbox ignore marker
imports this module. The concrete implementation is chosen at import
time based on ``sys.platform``.
"""
from __future__ import annotations

import sys

if sys.platform == "win32":
    from dbxignore._backends.windows_ads import (
        clear_ignored,
        is_ignored,
        set_ignored,
    )
elif sys.platform.startswith("linux"):
    from dbxignore._backends.linux_xattr import (
        clear_ignored,
        is_ignored,
        set_ignored,
    )
elif sys.platform == "darwin":
    from dbxignore._backends.macos_xattr import (
        clear_ignored,
        is_ignored,
        set_ignored,
    )
else:
    def _unsupported(*_args, **_kwargs):
        raise NotImplementedError(
            f"dbxignore has no ignore-marker backend for platform "
            f"{sys.platform!r}; supported: 'win32', 'linux', 'darwin'."
        )
    is_ignored = set_ignored = clear_ignored = _unsupported

__all__ = ["is_ignored", "set_ignored", "clear_ignored"]

"""Unit tests for the shared install detect_invocation helper."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def _daemon_name() -> str:
    """Platform-appropriate daemon binary name (drives the Windows .exe suffix)."""
    return "dbxignored.exe" if sys.platform == "win32" else "dbxignored"


def test_detect_invocation_returns_frozen_executable_when_already_dbxignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """User invoked `dbxignored install` directly: sys.executable IS the daemon shim."""
    daemon_exe = tmp_path / _daemon_name()
    daemon_exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(daemon_exe))
    from dbxignore.install import _common

    exe, args = _common.detect_invocation()
    assert exe == daemon_exe
    assert args == ""


def test_detect_invocation_finds_dbxignored_sibling_from_dbxignore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """User invoked `dbxignore install` (frozen): resolve to the `dbxignored` sibling.

    Common case for v0.4 macOS / Windows installs — both binaries ship together
    from a paired PyInstaller Analysis, the user runs the long-form CLI for
    install, and the service manager needs the daemon-shim binary as its
    invocation target.
    """
    cli_name = "dbxignore.exe" if sys.platform == "win32" else "dbxignore"
    cli_exe = tmp_path / cli_name
    cli_exe.write_text("")
    daemon_exe = tmp_path / _daemon_name()
    daemon_exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(cli_exe))
    from dbxignore.install import _common

    exe, args = _common.detect_invocation()
    assert exe == daemon_exe
    assert args == ""


def test_detect_invocation_falls_back_to_daemon_subcommand_when_sibling_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No `dbxignored` sibling: invoke ourselves with the `daemon` subcommand.

    Defensive case — the PyInstaller specs always emit both binaries, so this
    code path should not be reached in shipped releases. But if it is reached,
    `(dbxignore, "daemon")` is the correct fallback because Click can dispatch
    to the daemon subcommand from the long-form binary.
    """
    cli_name = "dbxignore.exe" if sys.platform == "win32" else "dbxignore"
    cli_exe = tmp_path / cli_name
    cli_exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(cli_exe))
    from dbxignore.install import _common

    exe, args = _common.detect_invocation()
    assert exe == cli_exe
    assert args == "daemon"


def test_detect_invocation_falls_back_to_python_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/python3" if name == "python3" else None
    )
    from dbxignore.install import _common

    exe, args = _common.detect_invocation()
    assert exe == Path("/usr/bin/python3")
    assert args == "-m dbxignore daemon"


def test_detect_invocation_uses_path_shim_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)

    def fake_which(name: str) -> str | None:
        if name == "dbxignored":
            return "/home/u/.local/bin/dbxignored"
        return None

    monkeypatch.setattr("shutil.which", fake_which)
    from dbxignore.install import _common

    exe, args = _common.detect_invocation()
    assert exe == Path("/home/u/.local/bin/dbxignored")
    assert args == ""

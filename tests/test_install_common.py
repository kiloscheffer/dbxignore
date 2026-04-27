"""Unit tests for the shared install detect_invocation helper."""
from __future__ import annotations

import sys
from pathlib import Path


def test_detect_invocation_returns_frozen_executable(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/path/to/dbxignored")
    from dbxignore.install import _common
    exe, args = _common.detect_invocation()
    assert exe == Path("/path/to/dbxignored")
    assert args == ""


def test_detect_invocation_falls_back_to_python_module(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/python3" if name == "python3" else None
    )
    from dbxignore.install import _common
    exe, args = _common.detect_invocation()
    assert exe == Path("/usr/bin/python3")
    assert args == "-m dbxignore daemon"


def test_detect_invocation_uses_path_shim_when_present(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    def fake_which(name):
        if name == "dbxignored":
            return "/home/u/.local/bin/dbxignored"
        return None
    monkeypatch.setattr("shutil.which", fake_which)
    from dbxignore.install import _common
    exe, args = _common.detect_invocation()
    assert exe == Path("/home/u/.local/bin/dbxignored")
    assert args == ""

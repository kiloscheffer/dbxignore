"""Cross-platform unit tests for state.user_log_dir().

The function returns different paths on darwin vs Windows + Linux. We
use monkeypatch to exercise each branch from any host OS, so the darwin
branch gets coverage in the portable test tier (not just in the
macos_only smoke).
"""
from __future__ import annotations

import sys
from pathlib import Path


def test_user_log_dir_equals_user_state_dir_on_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    from dbxignore import state
    assert state.user_log_dir() == state.user_state_dir()


def test_user_log_dir_equals_user_state_dir_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    from dbxignore import state
    assert state.user_log_dir() == state.user_state_dir()


def test_user_log_dir_under_library_logs_on_darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    from dbxignore import state
    assert Path(str(state.user_log_dir())).parts[-3:] == ("Library", "Logs", "dbxignore")

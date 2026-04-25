import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dbxignore import state


def test_roundtrip(tmp_path):
    s = state.State(
        daemon_pid=1234,
        daemon_started=datetime(2026, 4, 20, 9, 0, tzinfo=UTC),
        last_sweep=datetime(2026, 4, 20, 10, 0, tzinfo=UTC),
        last_sweep_duration_s=1.5,
        last_sweep_marked=5,
        last_sweep_cleared=2,
        last_sweep_errors=0,
        last_error=None,
        watched_roots=[Path(r"C:\Dropbox")],
    )
    path = tmp_path / "state.json"
    state.write(s, path)

    loaded = state.read(path)
    assert loaded == s


def test_read_missing_returns_none(tmp_path):
    assert state.read(tmp_path / "does_not_exist.json") is None


def test_read_corrupt_returns_none(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("not json", encoding="utf-8")
    assert state.read(p) is None


def test_write_leaves_no_tmp_file(tmp_path):
    """Atomic write: state.json.tmp must be renamed away on success."""
    p = tmp_path / "state.json"
    state.write(state.State(daemon_pid=1), p)
    assert p.exists()
    assert not (tmp_path / "state.json.tmp").exists()


def test_write_overwrites_stale_tmp(tmp_path):
    """A leaked tmp from a prior crash must not break the next write."""
    p = tmp_path / "state.json"
    (tmp_path / "state.json.tmp").write_text("garbage from crash", encoding="utf-8")
    state.write(state.State(daemon_pid=2), p)
    assert state.read(p).daemon_pid == 2
    assert not (tmp_path / "state.json.tmp").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path layout")
def test_default_path_windows_under_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert state.default_path() == tmp_path / "dbxignore" / "state.json"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux path layout")
def test_default_path_linux_uses_xdg_state_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert state.default_path() == tmp_path / "state" / "dbxignore" / "state.json"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux path layout")
def test_default_path_linux_falls_back_to_local_state(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert (
        state.default_path()
        == tmp_path / ".local" / "state" / "dbxignore" / "state.json"
    )

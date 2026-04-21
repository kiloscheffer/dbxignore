import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dropboxignore import state


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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path layout")
def test_default_path_windows_under_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert state.default_path() == tmp_path / "dropboxignore" / "state.json"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux path layout")
def test_default_path_linux_uses_xdg_state_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert state.default_path() == tmp_path / "state" / "dropboxignore" / "state.json"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux path layout")
def test_default_path_linux_falls_back_to_local_state(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert (
        state.default_path()
        == tmp_path / ".local" / "state" / "dropboxignore" / "state.json"
    )


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux migration")
def test_read_falls_back_to_legacy_linux_path_with_warning(
    monkeypatch, tmp_path, caplog
):
    """Pre-XDG installs persisted to ~/AppData/Local/dropboxignore/state.json.
    read() must transparently pick that up and warn so the user can clean up."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    legacy = tmp_path / "AppData" / "Local" / "dropboxignore" / "state.json"
    legacy.parent.mkdir(parents=True)
    state.write(state.State(daemon_pid=999), legacy)

    xdg = tmp_path / ".local" / "state" / "dropboxignore" / "state.json"
    assert not xdg.exists()

    with caplog.at_level(logging.WARNING, logger="dropboxignore.state"):
        loaded = state.read()

    assert loaded is not None
    assert loaded.daemon_pid == 999
    assert any(
        "legacy" in rec.message.lower() and str(legacy) in rec.message
        for rec in caplog.records
    ), [rec.message for rec in caplog.records]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux migration")
def test_read_prefers_xdg_when_both_exist(monkeypatch, tmp_path):
    """If the daemon has already written to the XDG path, that wins — an old
    legacy file must not clobber newer state."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    legacy = tmp_path / "AppData" / "Local" / "dropboxignore" / "state.json"
    legacy.parent.mkdir(parents=True)
    state.write(state.State(daemon_pid=111), legacy)

    xdg = tmp_path / ".local" / "state" / "dropboxignore" / "state.json"
    xdg.parent.mkdir(parents=True)
    state.write(state.State(daemon_pid=222), xdg)

    loaded = state.read()

    assert loaded is not None
    assert loaded.daemon_pid == 222


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux migration")
def test_read_explicit_path_does_not_trigger_legacy_fallback(monkeypatch, tmp_path):
    """An explicit path argument means 'read this file' — the legacy-fallback
    logic only kicks in for the zero-arg discovered-default call."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    legacy = tmp_path / "AppData" / "Local" / "dropboxignore" / "state.json"
    legacy.parent.mkdir(parents=True)
    state.write(state.State(daemon_pid=999), legacy)

    assert state.read(tmp_path / "nope.json") is None

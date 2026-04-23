import contextlib
import datetime as dt
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from dbxignore import daemon, state


@pytest.mark.parametrize("name,expected", [
    ("python.exe", True),
    ("python3", True),
    ("pythonw.exe", True),
    ("dbxignored.exe", True),
    ("dbxignored", True),
    ("notepad.exe", False),
    ("svchost.exe", False),
])
def test_is_other_live_daemon_accepts_python_and_frozen_exe(monkeypatch, name, expected):
    class _FakeProc:
        def __init__(self, _pid): pass
        def name(self): return name

    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(psutil, "Process", _FakeProc)

    # Use a pid that's not our own to bypass the self-check short-circuit.
    other_pid = 1 if daemon.os.getpid() != 1 else 2
    assert daemon._is_other_live_daemon(other_pid) is expected


def test_run_refuses_when_another_pid_is_alive(monkeypatch, tmp_path, caplog):
    # Spawn a sleeping Python subprocess; use its pid as the "other daemon".
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    try:
        s = state.State(
            daemon_pid=proc.pid,
            daemon_started=dt.datetime.now(dt.UTC),
            watched_roots=[Path(r"C:\Dropbox")],
        )
        state_path = tmp_path / "state.json"
        state.write(s, state_path)
        monkeypatch.setattr(state, "default_path", lambda: state_path)
        monkeypatch.setattr(daemon.roots_module, "discover", lambda: [tmp_path])
        monkeypatch.setattr(daemon, "_configured_logging", contextlib.nullcontext)

        caplog.set_level("ERROR", logger="dbxignore.daemon")
        daemon.run()
        assert any("already running" in rec.message.lower() for rec in caplog.records)
    finally:
        proc.kill()
        proc.wait(timeout=5)

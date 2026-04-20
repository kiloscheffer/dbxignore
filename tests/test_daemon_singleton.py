import datetime as dt
import subprocess
import sys
from pathlib import Path

from dropboxignore import daemon, state


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
        monkeypatch.setattr(daemon, "_configure_logging", lambda: None)

        caplog.set_level("ERROR", logger="dropboxignore.daemon")
        daemon.run()
        assert any("already running" in rec.message.lower() for rec in caplog.records)
    finally:
        proc.kill()
        proc.wait(timeout=5)

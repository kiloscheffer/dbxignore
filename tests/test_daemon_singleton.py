import contextlib
from pathlib import Path

import psutil  # type: ignore[import-untyped, unused-ignore]
import pytest

from dbxignore import daemon, state


@pytest.mark.parametrize(
    "name,expected",
    [
        ("python.exe", True),
        ("python3", True),
        ("pythonw.exe", True),
        ("dbxignored.exe", True),
        ("dbxignored", True),
        ("notepad.exe", False),
        ("svchost.exe", False),
    ],
)
def test_is_other_live_daemon_accepts_python_and_frozen_exe(
    monkeypatch: pytest.MonkeyPatch, name: str, expected: bool
) -> None:
    class _FakeProc:
        def __init__(self, _pid: int) -> None:
            pass

        def name(self) -> str:
            return name

    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(psutil, "Process", _FakeProc)

    # Use a pid that's not our own to bypass the self-check short-circuit.
    other_pid = 1 if daemon.os.getpid() != 1 else 2  # type: ignore[attr-defined]
    assert daemon._is_other_live_daemon(other_pid) is expected


# ---- singleton lock (followup item #78) -------------------------------------


def test_acquire_singleton_lock_succeeds_on_fresh_state_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """First acquisition on an empty state dir succeeds and returns a
    non-None file handle. The handle is what the caller holds for the
    daemon's lifetime; closing it releases the lock."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    fh = daemon._acquire_singleton_lock()
    assert fh is not None
    fh.close()


def test_acquire_singleton_lock_returns_none_when_already_held(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Second acquisition while the first is still held returns None.
    This is the singleton gate that backlog item #78 fixes — the prior
    state-based check had a non-atomic read-then-write window where two
    concurrent daemon launches could both proceed."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    first = daemon._acquire_singleton_lock()
    assert first is not None
    try:
        second = daemon._acquire_singleton_lock()
        assert second is None
    finally:
        first.close()


def test_acquire_singleton_lock_after_release_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After the first holder closes its handle, the lock is released
    and a fresh acquisition succeeds. Verifies the close-releases-lock
    contract that all the cross-platform locking primitives provide."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    first = daemon._acquire_singleton_lock()
    assert first is not None
    first.close()
    second = daemon._acquire_singleton_lock()
    assert second is not None
    second.close()


def test_run_refuses_when_singleton_lock_is_held(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """daemon.run() refuses to start when daemon.lock is already held.

    Replaces the prior subprocess-spawn-based test (a Windows-only timing
    flake — backlog item #14). The new shape: the test process itself
    acquires the lock, then calls daemon.run() — which sees contention
    and refuses with an ERROR log. Deterministic, no subprocess, no
    timing dependency.
    """
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [tmp_path])  # type: ignore[attr-defined]
    monkeypatch.setattr(daemon, "_configured_logging", contextlib.nullcontext)

    lock_holder = daemon._acquire_singleton_lock()
    assert lock_holder is not None, "test process should be able to acquire fresh lock"
    try:
        caplog.set_level("ERROR", logger="dbxignore.daemon")
        daemon.run()
        assert any("already running" in rec.message.lower() for rec in caplog.records)
    finally:
        lock_holder.close()

import contextlib
from pathlib import Path

import pytest

from dbxignore import daemon, state

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


def test_acquire_singleton_lock_seeks_to_byte_zero_before_locking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``msvcrt.locking`` on Windows locks from the file's current cursor
    position (Python docs: "The lock starts at the file's current
    position"). Without an explicit seek to 0, two concurrent fresh
    launches that both end at different cursors after the placeholder
    write can lock different byte ranges and both succeed — defeating
    the singleton. The implementation must seek to 0 before locking so
    every contender competes for byte 0 regardless of the file's
    pre-existing size or the cursor's post-write position.

    Test surface: pre-populate the lock file with 100 bytes (so the
    placeholder-write branch is skipped and the cursor would otherwise
    sit at EOF after the ``"ab+"`` open). Acquire the lock. Assert the
    cursor is at 0 — proving ``seek(0)`` ran before the lock primitive.
    Cross-platform: fcntl.flock doesn't care about cursor on POSIX, but
    the seek is harmless and the contract is consistent.
    """
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    lock_path = tmp_path / "daemon.lock"
    lock_path.write_bytes(b"x" * 100)

    fh = daemon._acquire_singleton_lock()
    assert fh is not None
    try:
        assert fh.tell() == 0, (
            f"expected cursor at byte 0 after lock, got {fh.tell()} — "
            "_acquire_singleton_lock must seek to 0 before locking so "
            "all contenders compete for byte 0 regardless of file size"
        )
    finally:
        fh.close()


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

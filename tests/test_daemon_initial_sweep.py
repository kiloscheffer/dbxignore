"""Tests for the worker-thread initial-sweep design (BACKLOG #53).

These tests bring up a real daemon thread with a ``BlockingMarkers`` gate
to deterministically pause the worker mid-sweep and observe behavior in
the ``state=starting`` window. The 10-second gate timeout keeps a
forgotten ``gate.set()`` from hanging the full pytest timeout.
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import TYPE_CHECKING

from dbxignore import cli, daemon, reconcile, state

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest

    from tests.conftest import BlockingMarkers, WriteFile


def _poll_until(fn: Callable[[], bool], timeout_s: float = 5.0, interval_s: float = 0.05) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(interval_s)
    return False


def _make_blocking_markers(gate: threading.Event) -> BlockingMarkers:
    from tests.conftest import BlockingMarkers

    return BlockingMarkers(gate)


def test_state_json_appears_before_sweep_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
) -> None:
    """state.json must appear with state=starting before the initial sweep
    completes — that's the user-visible value of item #53. The transition
    to state=running must be observable after the sweep finishes."""
    root = tmp_path / "root"
    write_file(root / ".dropboxignore", "build/\n")
    (root / "build").mkdir()
    (root / "src").mkdir()

    state_dir = tmp_path / "state"
    monkeypatch.setattr(state, "default_path", lambda: state_dir / "state.json")
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(state, "user_log_dir", lambda: state_dir)
    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [root])  # type: ignore[attr-defined, unused-ignore]

    gate = threading.Event()
    blocking = _make_blocking_markers(gate)
    monkeypatch.setattr(reconcile, "markers", blocking)
    monkeypatch.setattr(cli, "markers", blocking)

    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)
    t.start()
    try:
        # state.json should appear within ~5s, well before the gate is opened.
        appeared = _poll_until(
            lambda: (state_dir / "state.json").exists(),
            timeout_s=5.0,
        )
        assert appeared, "state.json did not appear within 5s of daemon start"

        # Read it: state should be 'starting' (last_sweep is None).
        s = state.read()
        assert s is not None
        assert s.daemon_pid is not None
        assert s.last_sweep is None, (
            f"expected last_sweep=None during starting window, got {s.last_sweep}"
        )

        # --summary output should reflect state=starting.
        summary = cli._format_summary(
            s,
            alive=True,
            conflicts_count=0,
        )
        assert summary == f"state=starting pid={s.daemon_pid}", (
            f"expected starting-form summary, got {summary!r}"
        )

        # Open the gate; worker proceeds and completes the sweep.
        gate.set()

        # state.json should transition to running (last_sweep != None).
        ran = _poll_until(
            lambda: (lambda x: x is not None and x.last_sweep is not None)(state.read()),
            timeout_s=10.0,
        )
        assert ran, "state.json never transitioned to state=running"
    finally:
        gate.set()
        stop.set()
        t.join(timeout=10.0)


def test_observer_up_before_initial_sweep_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
) -> None:
    """While the worker thread is paused on a closed gate, watchdog events
    arriving on the tree should still be classified and dispatched.
    Confirms the observer is genuinely up during the state=starting window
    and not blocked behind the initial sweep."""
    root = tmp_path / "root"
    write_file(root / ".dropboxignore", "")
    (root / "existing").mkdir()

    state_dir = tmp_path / "state"
    monkeypatch.setattr(state, "default_path", lambda: state_dir / "state.json")
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(state, "user_log_dir", lambda: state_dir)
    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [root])  # type: ignore[attr-defined, unused-ignore]

    gate = threading.Event()
    blocking = _make_blocking_markers(gate)
    monkeypatch.setattr(reconcile, "markers", blocking)
    monkeypatch.setattr(cli, "markers", blocking)

    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)
    t.start()
    try:
        # Wait for daemon to reach the "starting" window (state.json exists).
        assert _poll_until(
            lambda: (state_dir / "state.json").exists(),
            timeout_s=5.0,
        ), "daemon never wrote early state.json"

        # While the gate is still closed, create a new directory matching
        # a rule. The watchdog observer should pick this up; reconcile may
        # or may not run depending on rules, but the observer being alive
        # is the contract under test.
        new_dir = root / "newly_created"
        new_dir.mkdir()

        # Wait briefly for the observer to deliver the event. We can't
        # easily assert "event was received" without internal observer
        # state, but we can confirm the daemon thread didn't crash.
        time.sleep(0.5)
        assert t.is_alive(), "daemon thread died while observer should be running"

        # Open the gate; daemon completes initial sweep + remains alive.
        gate.set()
        assert _poll_until(
            lambda: (lambda x: x is not None and x.last_sweep is not None)(state.read()),
            timeout_s=10.0,
        )
    finally:
        gate.set()
        stop.set()
        t.join(timeout=10.0)


def test_worker_failure_shuts_down_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the initial sweep raises, the worker logs and sets stop_event,
    causing the main thread to exit. Daemon should be dead within ~5s,
    not lingering forever in state=starting."""
    root = tmp_path / "root"
    write_file(root / ".dropboxignore", "build/\n")
    (root / "build").mkdir()

    state_dir = tmp_path / "state"
    monkeypatch.setattr(state, "default_path", lambda: state_dir / "state.json")
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(state, "user_log_dir", lambda: state_dir)
    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [root])  # type: ignore[attr-defined, unused-ignore]

    # Force the initial-sweep call to raise.
    def _raising_sweep_once(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated sweep failure")

    monkeypatch.setattr(daemon, "_sweep_once", _raising_sweep_once)
    # Bypass _configured_logging so caplog can capture records (it disconnects
    # propagation to the root logger; same pattern as test_daemon_singleton.py).
    monkeypatch.setattr(daemon, "_configured_logging", contextlib.nullcontext)

    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)

    caplog.set_level("ERROR", logger="dbxignore.daemon")
    t.start()
    # Daemon should exit quickly via stop_event.set() from the worker.
    t.join(timeout=10.0)
    assert not t.is_alive(), "daemon did not exit within 10s after worker failure"

    # ERROR log should mention the worker failure with traceback.
    assert any("initial sweep worker failed" in rec.message for rec in caplog.records), (
        "expected ERROR log naming the worker failure"
    )


def test_cancelled_sweep_does_not_write_last_sweep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
) -> None:
    """Stopping the daemon mid-initial-sweep must leave last_sweep=None in
    state.json.  A cancelled sweep is partial; recording it as complete
    would misrepresent how many paths were actually reconciled."""
    root = tmp_path / "root"
    write_file(root / ".dropboxignore", "build/\n")
    (root / "build").mkdir()

    state_dir = tmp_path / "state"
    monkeypatch.setattr(state, "default_path", lambda: state_dir / "state.json")
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(state, "user_log_dir", lambda: state_dir)
    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [root])  # type: ignore[attr-defined, unused-ignore]

    gate = threading.Event()
    blocking = _make_blocking_markers(gate)
    monkeypatch.setattr(reconcile, "markers", blocking)
    monkeypatch.setattr(cli, "markers", blocking)

    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)
    t.start()
    try:
        # Wait for the daemon to write the early state.json (last_sweep=None).
        assert _poll_until(
            lambda: (state_dir / "state.json").exists(),
            timeout_s=5.0,
        ), "daemon never wrote early state.json"
        s_before = state.read()
        assert s_before is not None and s_before.last_sweep is None

        # Cancel the daemon while the worker is blocked on the gate, then
        # open the gate so the worker can observe stop_event and exit.
        stop.set()
        gate.set()

        t.join(timeout=10.0)
        assert not t.is_alive(), "daemon did not exit within 10s after stop_event"

        # last_sweep must still be None — the sweep was never completed.
        s_after = state.read()
        assert s_after is not None
        assert s_after.last_sweep is None, (
            f"cancelled sweep was recorded as completed: last_sweep={s_after.last_sweep}"
        )
    finally:
        gate.set()
        stop.set()
        t.join(timeout=10.0)


def test_cooperative_shutdown_during_initial_sweep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
) -> None:
    """Setting stop_event while the worker thread is mid-sweep must cause
    the daemon to exit promptly (not wait for the sweep to complete).
    Bound: ~5s, well under the 50s a real sweep on a large tree would
    take. Verifies cooperative cancellation in reconcile_subtree's walk."""
    root = tmp_path / "root"
    write_file(root / ".dropboxignore", "build/\n")
    # Create enough subdirectories that the walk takes meaningful time
    # under the BlockingMarkers gate without it being too slow without.
    for i in range(20):
        (root / f"dir_{i}").mkdir()

    state_dir = tmp_path / "state"
    monkeypatch.setattr(state, "default_path", lambda: state_dir / "state.json")
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(state, "user_log_dir", lambda: state_dir)
    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [root])  # type: ignore[attr-defined, unused-ignore]

    gate = threading.Event()
    blocking = _make_blocking_markers(gate)
    monkeypatch.setattr(reconcile, "markers", blocking)
    monkeypatch.setattr(cli, "markers", blocking)

    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)

    t.start()
    # Wait for daemon to reach the starting window.
    assert _poll_until(
        lambda: (state_dir / "state.json").exists(),
        timeout_s=5.0,
    )

    # Set stop_event while the worker is paused on the gate. Open the gate
    # AFTER setting stop so the worker proceeds past wait() but should see
    # stop_event.is_set() at the next reconcile_subtree check point.
    shutdown_start = time.time()
    stop.set()
    gate.set()

    t.join(timeout=10.0)
    shutdown_duration = time.time() - shutdown_start

    assert not t.is_alive(), "daemon did not exit within 10s after stop_event"
    assert shutdown_duration < 5.0, (
        f"shutdown took {shutdown_duration:.2f}s — cooperative cancellation likely "
        "not honored at reconcile_subtree boundaries"
    )

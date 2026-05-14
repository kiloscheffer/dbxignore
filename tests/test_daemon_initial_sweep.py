"""Tests for the worker-thread initial-sweep design.

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
from tests.conftest import BlockingMarkers, _poll_until, setup_daemon_state

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from tests.conftest import WriteFile


def test_state_json_appears_before_sweep_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
) -> None:
    """state.json must appear with state=starting before the initial sweep
    completes. The transition
    to state=running must be observable after the sweep finishes."""
    root = tmp_path / "root"
    write_file(root / ".dropboxignore", "build/\n")
    (root / "build").mkdir()
    (root / "src").mkdir()

    state_dir = setup_daemon_state(monkeypatch, tmp_path, root)

    gate = threading.Event()
    blocking = BlockingMarkers(gate)
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

    state_dir = setup_daemon_state(monkeypatch, tmp_path, root)

    gate = threading.Event()
    blocking = BlockingMarkers(gate)
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

    setup_daemon_state(monkeypatch, tmp_path, root)

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

    state_dir = setup_daemon_state(monkeypatch, tmp_path, root)

    gate = threading.Event()
    blocking = BlockingMarkers(gate)
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

    state_dir = setup_daemon_state(monkeypatch, tmp_path, root)

    gate = threading.Event()
    blocking = BlockingMarkers(gate)
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


def test_state_starting_appears_before_rule_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
) -> None:
    """`cache.load_root` must run only in the worker thread, so a slow
    rule-file scan on a large tree doesn't delay the early
    ``state.json``/``state=starting`` visibility. Verify by blocking
    ``RuleCache.load_root`` and asserting ``state.json`` still appears
    before the block is released."""
    from dbxignore.rules import RuleCache

    root = tmp_path / "root"
    write_file(root / ".dropboxignore", "")

    state_dir = setup_daemon_state(monkeypatch, tmp_path, root)

    load_root_gate = threading.Event()
    original_load_root = RuleCache.load_root

    def blocking_load_root(
        self: RuleCache,
        root_path: Path,
        *,
        log_warnings: bool = True,
        stop_event: threading.Event | None = None,
    ) -> None:
        # Forward the keyword arguments `_sweep_once` passes (`stop_event`
        # and `log_warnings`). Without the forward, the worker would TypeError
        # on the unexpected kwarg,
        # `_initial_sweep_worker`'s try/except would catch it, and the
        # test would pass vacuously — the early state.write fires in the
        # main thread before the worker spawns, so the `state.json
        # appears` and `last_sweep is None` assertions hold even when the
        # gate never blocked anything.
        load_root_gate.wait(timeout=10.0)
        original_load_root(self, root_path, log_warnings=log_warnings, stop_event=stop_event)

    monkeypatch.setattr(RuleCache, "load_root", blocking_load_root)

    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)
    t.start()
    try:
        # state.json should appear within a few seconds even though
        # `cache.load_root` is blocked. Without the fix, the main thread
        # would call `cache.load_root` before `state.write`, so the gate
        # would block state.json creation.
        appeared = _poll_until(
            lambda: (state_dir / "state.json").exists(),
            timeout_s=5.0,
        )
        assert appeared, (
            "state.json did not appear within 5s while cache.load_root was "
            "blocked — main thread may still be waiting on load_root before "
            "the early state.write"
        )

        s = state.read()
        assert s is not None
        assert s.last_sweep is None, (
            f"expected state=starting (last_sweep=None), got last_sweep={s.last_sweep}"
        )

        # Open the gate; the worker should now actually run load_root and
        # then proceed through reconcile + the post-sweep state.write.
        # Asserting this transition guards against the worker dying silently
        # (e.g., `blocking_load_root` missing a kwarg the production signature
        # gained later — exactly the vacuous-pass shape the wrapper's
        # docstring above warns about): a dead worker never writes
        # `last_sweep != None`, so this poll would time out.
        load_root_gate.set()
        ran = _poll_until(
            lambda: (lambda x: x is not None and x.last_sweep is not None)(state.read()),
            timeout_s=10.0,
        )
        assert ran, (
            "state.json never transitioned to last_sweep != None — the "
            "worker likely died silently rather than running load_root + "
            "reconcile after the gate opened"
        )
    finally:
        load_root_gate.set()
        stop.set()
        t.join(timeout=10.0)


def test_periodic_sweep_skipped_while_initial_worker_alive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
) -> None:
    """Periodic sweep loop must skip ticks while the initial-sweep worker
    is still running. Without this guard, an initial sweep that runs
    longer than ``SWEEP_INTERVAL_S`` would race against a periodic sweep
    on the same paths — operations are idempotent, but skipping the tick
    avoids the wasted concurrent traversal."""
    root = tmp_path / "root"
    write_file(root / ".dropboxignore", "build/\n")
    (root / "build").mkdir()

    state_dir = setup_daemon_state(monkeypatch, tmp_path, root)

    # Shorten SWEEP_INTERVAL_S so the periodic loop ticks within the test
    # window. The default 3600s would never fire during a 2-second test.
    monkeypatch.setattr(daemon, "SWEEP_INTERVAL_S", 0.3)

    # Count _sweep_once calls. Without the fix, a periodic tick would call
    # _sweep_once a second time (count=2) while the worker's _sweep_once
    # is still blocked on the gate. With the fix, the periodic tick sees
    # worker.is_alive() and skips, leaving count=1.
    sweep_calls: list[float] = []
    real_sweep_once = daemon._sweep_once

    def counting_sweep_once(*args: object, **kwargs: object) -> None:
        sweep_calls.append(time.time())
        real_sweep_once(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(daemon, "_sweep_once", counting_sweep_once)

    gate = threading.Event()
    blocking = BlockingMarkers(gate)
    monkeypatch.setattr(reconcile, "markers", blocking)
    monkeypatch.setattr(cli, "markers", blocking)

    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)
    t.start()
    try:
        # Wait for daemon to reach the starting window (state.json exists)
        # so we know the worker has called _sweep_once at least once.
        assert _poll_until(
            lambda: (state_dir / "state.json").exists(),
            timeout_s=5.0,
        ), "daemon never wrote early state.json"

        # Let the periodic loop tick a few times while the worker is still
        # blocked on the gate. SWEEP_INTERVAL_S=0.3 means roughly 4 ticks
        # in 1.2 seconds. Without the fix, sweep_calls grows; with the fix,
        # it stays at 1.
        time.sleep(1.2)

        # The worker called _sweep_once exactly once. The periodic loop
        # ticked but skipped each time because worker.is_alive() was True.
        assert len(sweep_calls) == 1, (
            f"periodic sweep ran while initial worker was alive: "
            f"{len(sweep_calls)} _sweep_once calls in 1.2s"
        )
    finally:
        gate.set()
        stop.set()
        t.join(timeout=10.0)


def test_hourly_tick_recovers_from_sweep_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-OSError exception from ``_sweep_once`` during an hourly tick
    must not kill the daemon — the next tick should still fire. Without
    the wrapper, an unexpected ``AttributeError`` / ``KeyError`` from a
    rules-edge-case would propagate out of the while loop, the daemon
    thread would exit, and the service manager would have to restart it
    once an hour. The initial-sweep worker is wrapped; this pins the
    symmetric wrap on the hourly tick."""
    root = tmp_path / "root"
    write_file(root / ".dropboxignore", "")

    setup_daemon_state(monkeypatch, tmp_path, root)
    monkeypatch.setattr(daemon, "_configured_logging", contextlib.nullcontext)
    monkeypatch.setattr(daemon, "SWEEP_INTERVAL_S", 0.3)

    real_sweep_once = daemon._sweep_once
    call_count = [0]
    third_call_event = threading.Event()

    def _sweep_succeed_then_raise_then_signal(*args: object, **kwargs: object) -> None:
        call_count[0] += 1
        n = call_count[0]
        if n == 1:
            # Initial-sweep worker — run the real sweep so worker exits
            # cleanly and the hourly loop starts ticking.
            real_sweep_once(*args, **kwargs)  # type: ignore[arg-type]
            return
        if n == 2:
            # First hourly tick — raise. Without the fix this propagates
            # out of the while loop and the daemon dies.
            raise RuntimeError("simulated hourly sweep failure")
        # Subsequent tick — confirms the loop kept ticking after recovery.
        third_call_event.set()

    monkeypatch.setattr(daemon, "_sweep_once", _sweep_succeed_then_raise_then_signal)

    caplog.set_level("ERROR", logger="dbxignore.daemon")
    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)
    t.start()
    try:
        assert third_call_event.wait(timeout=5.0), (
            "daemon did not survive _sweep_once exception during hourly tick"
        )
        assert t.is_alive(), "daemon thread died despite supposed recovery"
        assert any("hourly sweep" in rec.message.lower() for rec in caplog.records), (
            "expected ERROR log naming the hourly sweep failure"
        )
    finally:
        stop.set()
        t.join(timeout=10.0)

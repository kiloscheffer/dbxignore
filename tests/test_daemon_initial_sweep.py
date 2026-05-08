"""Tests for the worker-thread initial-sweep design (BACKLOG #53).

These tests bring up a real daemon thread with a ``BlockingMarkers`` gate
to deterministically pause the worker mid-sweep and observe behavior in
the ``state=starting`` window. The 10-second gate timeout keeps a
forgotten ``gate.set()`` from hanging the full pytest timeout.
"""

from __future__ import annotations

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

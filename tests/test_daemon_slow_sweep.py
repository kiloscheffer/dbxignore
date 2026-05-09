"""Tests for the slow-sweep marker hook (BACKLOG #89).

The marker file at ``state.user_state_dir() / "_test_slow_sweep"`` lets
manual-test scripts deterministically pad the daemon's initial sweep so
``state=starting`` is observable on small test trees. Unit tests cover the
helper's parsing branches; an integration test pins the worker call site.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from dbxignore import daemon, state

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write_marker(state_dir: Path, contents: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / daemon.SLOW_SWEEP_MARKER_NAME).write_text(contents, encoding="utf-8")


def test_pad_returns_zero_when_marker_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """No marker file → returns 0.0, no log records."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    caplog.set_level(logging.WARNING, logger="dbxignore.daemon")
    assert daemon._slow_sweep_pad_seconds() == 0.0
    assert caplog.records == []


def test_pad_returns_zero_when_marker_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Empty file → returns 0.0, no log records (file may have been touched
    by a script as a placeholder, not a directive)."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    _write_marker(tmp_path, "")
    caplog.set_level(logging.WARNING, logger="dbxignore.daemon")
    assert daemon._slow_sweep_pad_seconds() == 0.0
    assert caplog.records == []


def test_pad_returns_zero_when_marker_whitespace_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Whitespace/newline-only contents → returns 0.0, no log records.
    Defends against a script that did ``echo > file`` (which writes a
    bare newline) — that's effectively the same as empty."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    _write_marker(tmp_path, "  \n  \t\n")
    caplog.set_level(logging.WARNING, logger="dbxignore.daemon")
    assert daemon._slow_sweep_pad_seconds() == 0.0
    assert caplog.records == []


def test_pad_honors_positive_integer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``5`` → returns 5.0, WARNING logged so honored state is visible."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    _write_marker(tmp_path, "5\n")
    caplog.set_level(logging.WARNING, logger="dbxignore.daemon")
    assert daemon._slow_sweep_pad_seconds() == 5.0
    assert any("honored" in rec.message and "5.0s" in rec.message for rec in caplog.records)


def test_pad_honors_positive_float(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``2.5`` → returns 2.5, WARNING logged."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    _write_marker(tmp_path, "2.5")
    caplog.set_level(logging.WARNING, logger="dbxignore.daemon")
    assert daemon._slow_sweep_pad_seconds() == 2.5
    assert any("honored" in rec.message for rec in caplog.records)


def test_pad_returns_zero_for_explicit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``0`` → returns 0.0, no WARNING (a script that wants to disable
    the pad without removing the file shouldn't spam the log)."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    _write_marker(tmp_path, "0")
    caplog.set_level(logging.WARNING, logger="dbxignore.daemon")
    assert daemon._slow_sweep_pad_seconds() == 0.0
    assert caplog.records == []


def test_pad_warns_and_returns_zero_on_non_numeric(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``garbage`` → returns 0.0, WARNING names the bad value."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    _write_marker(tmp_path, "garbage")
    caplog.set_level(logging.WARNING, logger="dbxignore.daemon")
    assert daemon._slow_sweep_pad_seconds() == 0.0
    assert any("not a number" in rec.message for rec in caplog.records)


def test_pad_warns_and_returns_zero_on_negative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``-1`` → returns 0.0, WARNING names negativity (a typo could land
    a negative; refuse rather than passing it to ``stop_event.wait``,
    where a negative value silently wakes immediately on Python 3.12+)."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    _write_marker(tmp_path, "-1")
    caplog.set_level(logging.WARNING, logger="dbxignore.daemon")
    assert daemon._slow_sweep_pad_seconds() == 0.0
    assert any("negative" in rec.message for rec in caplog.records)


def test_pad_warns_and_returns_zero_on_inf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``inf`` → returns 0.0, WARNING names non-finite. Without this
    check ``stop_event.wait(inf)`` raises OverflowError, the worker
    thread dies before the sweep, and the daemon stays in
    ``state=starting`` forever (Codex P2 catch on PR #175)."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    _write_marker(tmp_path, "inf")
    caplog.set_level(logging.WARNING, logger="dbxignore.daemon")
    assert daemon._slow_sweep_pad_seconds() == 0.0
    assert any("non-finite" in rec.message for rec in caplog.records)


def test_pad_warns_and_returns_zero_on_nan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``nan`` → returns 0.0, WARNING names non-finite. NaN slips past
    both the ``< 0`` and ``> 0`` arms (NaN comparisons are False), so
    without the explicit isfinite check it would silently fall through
    to ``return value`` and the call site's ``if pad_s > 0`` would also
    skip — non-functional but the WARNING contract should hold."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    _write_marker(tmp_path, "nan")
    caplog.set_level(logging.WARNING, logger="dbxignore.daemon")
    assert daemon._slow_sweep_pad_seconds() == 0.0
    assert any("non-finite" in rec.message for rec in caplog.records)


def test_pad_warns_and_returns_zero_above_timeout_max(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A large finite value exceeding ``threading.TIMEOUT_MAX`` →
    returns 0.0, WARNING. ``TIMEOUT_MAX`` is much smaller on Windows
    (~4.3M seconds) than on POSIX, so a value picked from a Linux
    tester's environment can over-shoot Windows ``stop_event.wait``
    (raises OverflowError)."""
    import threading as threading_mod

    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    _write_marker(tmp_path, str(threading_mod.TIMEOUT_MAX + 1.0))
    caplog.set_level(logging.WARNING, logger="dbxignore.daemon")
    assert daemon._slow_sweep_pad_seconds() == 0.0
    assert any("TIMEOUT_MAX" in rec.message for rec in caplog.records)


def test_initial_sweep_worker_pads_before_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Marker present → ``_sweep_once`` is delayed by approximately the
    pad value. Pins the call-site wiring without exercising the full
    daemon thread (the helper wires the wait into the worker; the wait
    itself is well-tested by Python's stdlib)."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    # 0.5s pad with 0.4s lower-bound assertion gives ~100ms headroom for
    # thread-scheduling jitter on contended CI workers — Python's
    # threading.Event.wait honors the timeout as a minimum, but cross-
    # platform timer resolution can produce small under-shoots in practice.
    _write_marker(tmp_path, "0.5")

    sweep_called_at: list[float] = []

    def fake_sweep_once(*_args: object, **_kwargs: object) -> None:
        sweep_called_at.append(time.monotonic())

    monkeypatch.setattr(daemon, "_sweep_once", fake_sweep_once)

    stop = threading.Event()
    started = time.monotonic()
    daemon._initial_sweep_worker(
        roots=[],
        cache=None,  # type: ignore[arg-type]
        daemon_started=None,  # type: ignore[arg-type]
        daemon_create_time=None,
        stop_event=stop,
    )
    elapsed = sweep_called_at[0] - started

    assert sweep_called_at, "_sweep_once was never called"
    assert elapsed >= 0.4, f"sweep ran after only {elapsed:.3f}s; marker should have padded ~0.5s"


def test_initial_sweep_worker_returns_early_when_stopped_during_pad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Long marker pad + stop_event set during the wait → worker returns
    before ``_sweep_once`` runs. Pins the cooperative-cancellation contract
    so daemon shutdown stays prompt even when the slow-sweep marker is
    set to a large value."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    _write_marker(tmp_path, "30")  # 30s pad — far longer than test budget

    sweep_called: list[bool] = []

    def fake_sweep_once(*_args: object, **_kwargs: object) -> None:
        sweep_called.append(True)

    monkeypatch.setattr(daemon, "_sweep_once", fake_sweep_once)

    stop = threading.Event()

    # Set stop_event from a helper thread shortly after the worker enters
    # its pad wait, so the wait returns True (event set) and the worker
    # short-circuits before _sweep_once.
    def _signal_stop() -> None:
        time.sleep(0.2)
        stop.set()

    signaller = threading.Thread(target=_signal_stop)
    signaller.start()

    started = time.monotonic()
    daemon._initial_sweep_worker(
        roots=[],
        cache=None,  # type: ignore[arg-type]
        daemon_started=None,  # type: ignore[arg-type]
        daemon_create_time=None,
        stop_event=stop,
    )
    elapsed = time.monotonic() - started
    signaller.join(timeout=1.0)

    assert sweep_called == [], "_sweep_once should not run after stop during pad"
    assert elapsed < 5.0, (
        f"worker took {elapsed:.2f}s to return after stop; "
        "expected early-return well under the 30s pad"
    )


def test_initial_sweep_worker_no_pad_when_marker_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No marker → ``_sweep_once`` runs essentially immediately, with no
    measurable wait. Confirms the unset/zero path pays no cost."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)

    sweep_called_at: list[float] = []

    def fake_sweep_once(*_args: object, **_kwargs: object) -> None:
        sweep_called_at.append(time.monotonic())

    monkeypatch.setattr(daemon, "_sweep_once", fake_sweep_once)

    stop = threading.Event()
    started = time.monotonic()
    daemon._initial_sweep_worker(
        roots=[],
        cache=None,  # type: ignore[arg-type]
        daemon_started=None,  # type: ignore[arg-type]
        daemon_create_time=None,
        stop_event=stop,
    )
    elapsed = sweep_called_at[0] - started

    assert sweep_called_at, "_sweep_once was never called"
    assert elapsed < 0.1, (
        f"sweep delayed {elapsed:.3f}s without marker; expected near-zero overhead"
    )

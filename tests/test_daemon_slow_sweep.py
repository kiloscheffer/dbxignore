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


def test_pad_warns_and_returns_zero_on_undecodable_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Marker file with non-UTF-8 bytes → returns 0.0, WARNING.
    ``Path.read_text(encoding="utf-8")`` raises ``UnicodeDecodeError``
    (which derives from ``ValueError``, not ``OSError``), so it needs
    its own catch arm. Surfaces when a stale marker was written in a
    different encoding — Windows PS 5.1 ``Set-Content`` defaults to
    UTF-16, corrupt/binary edits, etc. (Codex P2 catch on PR #175)."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    marker = tmp_path / daemon.SLOW_SWEEP_MARKER_NAME
    # UTF-16 LE BOM + "15" — 0xff is not a valid UTF-8 start byte.
    marker.write_bytes(b"\xff\xfe1\x005\x00")
    caplog.set_level(logging.WARNING, logger="dbxignore.daemon")
    assert daemon._slow_sweep_pad_seconds() == 0.0
    assert any("could not read slow-sweep marker" in rec.message for rec in caplog.records)


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
    assert sweep_called_at, "_sweep_once was never called"
    elapsed = sweep_called_at[0] - started
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


def test_initial_sweep_worker_routes_helper_exception_to_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unhandled exception from `_slow_sweep_pad_seconds` (or the
    surrounding `stop_event.wait` call) must NOT escape the worker thread
    — it must route through the same try/except arm `_sweep_once`
    failures use, setting `stop_event` so the daemon shuts down cleanly
    instead of stranding in `state=starting` (item #91).

    Defense-in-depth against future helper additions that introduce an
    exception type the helper's parser doesn't catch. Codex caught two
    such bugs on PR #175 — at the parser level — but a third class
    would otherwise need a third parser-level fix; this test pins the
    structural fallback."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)

    def boom() -> float:
        raise RuntimeError("simulated helper failure")

    monkeypatch.setattr(daemon, "_slow_sweep_pad_seconds", boom)

    sweep_called: list[bool] = []

    def fake_sweep_once(*_args: object, **_kwargs: object) -> None:
        sweep_called.append(True)

    monkeypatch.setattr(daemon, "_sweep_once", fake_sweep_once)

    stop = threading.Event()
    caplog.set_level(logging.ERROR, logger="dbxignore.daemon")
    daemon._initial_sweep_worker(
        roots=[],
        cache=None,  # type: ignore[arg-type]
        daemon_started=None,  # type: ignore[arg-type]
        daemon_create_time=None,
        stop_event=stop,
    )

    assert stop.is_set(), "worker must set stop_event when helper raises"
    assert sweep_called == [], "_sweep_once must not run after helper raises"
    assert any("initial sweep worker failed" in rec.message for rec in caplog.records), (
        "ERROR log must name the worker failure (matches existing _sweep_once-failure shape)"
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
    assert sweep_called_at, "_sweep_once was never called"
    elapsed = sweep_called_at[0] - started
    assert elapsed < 0.1, (
        f"sweep delayed {elapsed:.3f}s without marker; expected near-zero overhead"
    )


def test_initial_sweep_worker_sets_cache_ready_before_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins the item-1 contract: the worker loads the rule cache and signals
    `cache_ready` BEFORE calling `_sweep_once`. The order matters — debounced
    events that arrived during the startup window can dispatch the moment
    `cache_ready` is set, even though Phase 2 (reconcile sweep) is still
    running. Without this ordering, gated events would queue until the full
    sweep completes, an unnecessary ~50s delay on big trees."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)

    cache_ready_was_set_when_sweep_started: list[bool] = []

    def fake_sweep_once(*_args: object, **_kwargs: object) -> None:
        cache_ready_was_set_when_sweep_started.append(cache_ready.is_set())

    monkeypatch.setattr(daemon, "_sweep_once", fake_sweep_once)

    stop = threading.Event()
    cache_ready = threading.Event()
    daemon._initial_sweep_worker(
        roots=[],  # empty roots → for-loop is a no-op; we test the set-before-sweep wiring
        cache=None,  # type: ignore[arg-type]
        daemon_started=None,  # type: ignore[arg-type]
        daemon_create_time=None,
        stop_event=stop,
        cache_ready=cache_ready,
    )
    assert cache_ready_was_set_when_sweep_started == [True], (
        "cache_ready must be set before _sweep_once is called"
    )


def test_initial_sweep_worker_does_not_set_cache_ready_when_stopped_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symmetric guard: if stop_event fires before the worker finishes
    loading the cache, `cache_ready` stays unset and gated events never
    dispatch against a partial cache."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    _write_marker(tmp_path, "10")  # long pad so we can stop mid-pad

    sweep_called: list[bool] = []
    monkeypatch.setattr(daemon, "_sweep_once", lambda *a, **kw: sweep_called.append(True))

    stop = threading.Event()
    cache_ready = threading.Event()

    def _signal_stop() -> None:
        time.sleep(0.1)
        stop.set()

    signaller = threading.Thread(target=_signal_stop)
    signaller.start()
    daemon._initial_sweep_worker(
        roots=[],
        cache=None,  # type: ignore[arg-type]
        daemon_started=None,  # type: ignore[arg-type]
        daemon_create_time=None,
        stop_event=stop,
        cache_ready=cache_ready,
    )
    signaller.join(timeout=1.0)

    assert sweep_called == [], "_sweep_once must not run after stop_event"
    assert not cache_ready.is_set(), "cache_ready must NOT be set when the worker returns early"


def test_initial_sweep_worker_does_not_set_cache_ready_when_load_root_returned_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 followup on PR #240: if `stop_event` fires mid-walk during
    the FINAL `cache.load_root`, the top-of-loop stop check above doesn't
    catch it (the loop has already exited), and `cache_ready.set()` used
    to fire against a partial cache. The post-loop re-check now refuses
    to signal ready in that state."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)

    sweep_called: list[bool] = []
    monkeypatch.setattr(daemon, "_sweep_once", lambda *a, **kw: sweep_called.append(True))

    # Fake cache whose load_root simulates the "saw stop_event mid-walk,
    # returned early with partial state" contract.
    class PartialLoadCache:
        def __init__(self) -> None:
            self.loaded: list[object] = []

        def load_root(self, r: object, stop_event: threading.Event) -> None:
            self.loaded.append(r)
            # Simulate load_root noticing stop mid-walk: caller set
            # stop_event before this call, and load_root cooperatively
            # bails after recording the call but BEFORE the cache is
            # fully populated. Same effect as a real partial walk.
            return

    fake_cache = PartialLoadCache()
    stop = threading.Event()
    cache_ready = threading.Event()

    # Set stop BEFORE the worker enters its load_root call. The first
    # iteration's top-of-loop check would catch this, so we want the
    # worker to start with stop unset, fire stop during load_root, then
    # exit the loop. Easiest: arrange one root, set stop INSIDE
    # load_root via a wrapper.
    real_load_root = fake_cache.load_root

    def stop_during_load(r: object, stop_event: threading.Event) -> None:
        real_load_root(r, stop_event)
        stop_event.set()  # mid-walk cancellation surface

    fake_cache.load_root = stop_during_load  # type: ignore[method-assign]

    daemon._initial_sweep_worker(
        roots=[tmp_path],  # one root → loop runs once → no top-of-loop catch
        cache=fake_cache,  # type: ignore[arg-type]
        daemon_started=None,  # type: ignore[arg-type]
        daemon_create_time=None,
        stop_event=stop,
        cache_ready=cache_ready,
    )

    assert fake_cache.loaded == [tmp_path], "load_root was called exactly once"
    assert stop.is_set(), "stop_event was set during the load_root call"
    assert not cache_ready.is_set(), (
        "cache_ready MUST stay unset when load_root returned with stop_event set"
    )
    assert sweep_called == [], "_sweep_once must not run after stop"


def test_initial_sweep_worker_drains_deferred_after_cache_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 followup on PR #240: events that arrived during the
    startup window MUST be re-dispatched after `cache_ready.set()` so a
    newly-created ignored directory is marked within ~cache-load-time
    rather than waiting for the sweep's wall-clock to reach it.

    The drain runs BEFORE `_sweep_once` (so the user-visible mark window
    shrinks) and AFTER `cache_ready.set()` (so the gate's atomic-check
    sends any concurrent appends down the direct-dispatch path)."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)

    sweep_called: list[bool] = []
    monkeypatch.setattr(daemon, "_sweep_once", lambda *a, **kw: sweep_called.append(True))

    # Pre-load the queue with three deferred "events" (opaque strings here —
    # the worker passes them through redispatch verbatim).
    deferred = daemon._DeferredEvents()
    deferred.append("ev1", threading.Event())  # unset event → append succeeds
    deferred.append("ev2", threading.Event())
    deferred.append("ev3", threading.Event())

    redispatched: list[object] = []
    cache_ready = threading.Event()
    drain_order: list[str] = []

    def fake_redispatch(event: object) -> None:
        # Worker must drain AFTER cache_ready.set() — pin that ordering.
        assert cache_ready.is_set(), (
            "cache_ready must be set before deferred events are re-dispatched"
        )
        drain_order.append("redispatch")
        redispatched.append(event)

    # _sweep_once should run AFTER all redispatches.
    def recording_sweep(*_a: object, **_kw: object) -> None:
        drain_order.append("sweep")
        sweep_called.append(True)

    monkeypatch.setattr(daemon, "_sweep_once", recording_sweep)

    stop = threading.Event()
    daemon._initial_sweep_worker(
        roots=[],
        cache=None,  # type: ignore[arg-type]
        daemon_started=None,  # type: ignore[arg-type]
        daemon_create_time=None,
        stop_event=stop,
        cache_ready=cache_ready,
        deferred=deferred,
        redispatch=fake_redispatch,
    )

    assert redispatched == ["ev1", "ev2", "ev3"], "drain must replay events in FIFO order"
    assert drain_order == ["redispatch", "redispatch", "redispatch", "sweep"], (
        "drain must complete before _sweep_once runs"
    )
    # Queue is empty after drain.
    assert deferred.drain() == []


def test_initial_sweep_worker_continues_drain_on_redispatch_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """One bad deferred event (or one whose path vanished between
    deferral and replay) must NOT abort the rest of the drain. The
    worker logs the exception and continues. Mirrors the watchdog
    handler's broad-except pattern at the dispatch entry — convergence
    is the goal, not strict per-event correctness."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)
    monkeypatch.setattr(daemon, "_sweep_once", lambda *a, **kw: None)

    deferred = daemon._DeferredEvents()
    cache_ready = threading.Event()
    # Append directly to the internal list to avoid the cache_ready atomic
    # check (we want these in the queue regardless).
    with deferred._lock:
        deferred._events.extend(["ev1", "boom", "ev3"])

    seen: list[object] = []

    def maybe_failing(event: object) -> None:
        if event == "boom":
            raise RuntimeError("simulated dispatch failure")
        seen.append(event)

    stop = threading.Event()
    caplog.set_level(logging.ERROR, logger="dbxignore.daemon")
    daemon._initial_sweep_worker(
        roots=[],
        cache=None,  # type: ignore[arg-type]
        daemon_started=None,  # type: ignore[arg-type]
        daemon_create_time=None,
        stop_event=stop,
        cache_ready=cache_ready,
        deferred=deferred,
        redispatch=maybe_failing,
    )

    assert seen == ["ev1", "ev3"], "drain must skip the failing event but continue"
    assert any("deferred event re-dispatch failed" in rec.message for rec in caplog.records), [
        rec.message for rec in caplog.records
    ]

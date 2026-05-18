"""Tests for the slow-sweep marker hook.

The marker file at ``state.user_state_dir() / "_test_slow_sweep"`` lets
manual-test scripts deterministically pad the daemon's initial sweep so
``state=starting`` is observable on small test trees. Unit tests cover the
helper's parsing branches; an integration test pins the worker call site.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from typing import TYPE_CHECKING

from dbxignore import daemon, reconcile, state
from dbxignore.rules import RuleCache

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
    ``state=starting`` forever."""
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
    UTF-16, corrupt/binary edits, etc."""
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
    instead of stranding in `state=starting`.

    Defense-in-depth against future helper additions that introduce an
    exception type the helper's parser doesn't catch. This test pins the
    structural fallback at the worker level."""
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


def test_sweep_once_sets_cache_ready_between_phase1_and_phase2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pins the item-1 contract: ``_sweep_once`` signals ``cache_ready``
    between Phase 1 (load) and Phase 2 (reconcile). The order matters —
    debounced events that arrived during the startup window can dispatch
    the moment ``cache_ready`` is set, even though Phase 2 is still
    running. Without this ordering, gated events would queue until the
    full sweep completes, an unnecessary ~50s delay on big trees.

    Logic was relocated from ``_initial_sweep_worker`` into ``_sweep_once``
    so that initial sweep walks the tree exactly once; this test exercises
    the ``_sweep_once`` cache_ready / deferred params directly."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)

    # Track when cache_ready was set relative to reconcile_subtree calls.
    cache_ready_state_at_reconcile: list[bool] = []

    def fake_reconcile(_root: Path, _sub: Path, _cache: object, **_kw: object) -> reconcile.Report:
        cache_ready_state_at_reconcile.append(cache_ready.is_set())
        return reconcile.Report()

    monkeypatch.setattr(daemon, "reconcile_subtree", fake_reconcile)

    cache = RuleCache()
    stop = threading.Event()
    cache_ready = threading.Event()

    daemon._sweep_once(
        roots=[tmp_path],
        cache=cache,
        daemon_started=dt.datetime.now(dt.UTC),
        daemon_create_time=None,
        stop_event=stop,
        cache_ready=cache_ready,
    )

    assert cache_ready.is_set(), "cache_ready must be set after Phase 1"
    assert cache_ready_state_at_reconcile, "reconcile_subtree was never called"
    assert all(cache_ready_state_at_reconcile), (
        "cache_ready must already be set when Phase 2's reconcile calls fire"
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


def test_sweep_once_does_not_set_cache_ready_when_load_root_returned_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``stop_event`` fires mid-walk during the FINAL ``cache.load_root``,
    the top-of-loop stop check inside ``_sweep_once`` doesn't catch it (loop
    has exited), so a naive ``cache_ready.set()`` after the loop would
    fire against a partial cache. The post-loop re-check refuses to
    signal ready in that state.

    Logic lives in ``_sweep_once`` so the initial sweep walks the tree
    exactly once; this test targets ``_sweep_once`` directly."""
    reconcile_calls: list[Path] = []

    def fake_reconcile(_root: Path, sub: Path, _cache: object, **_kw: object) -> reconcile.Report:
        reconcile_calls.append(sub)
        return reconcile.Report()

    monkeypatch.setattr(daemon, "reconcile_subtree", fake_reconcile)

    # Fake cache whose load_root sets stop_event after recording the call —
    # simulates RuleCache.load_root's cooperative-cancellation surface
    # (returns early with partial state when stop_event fires mid-walk).
    class PartialLoadCache:
        def __init__(self) -> None:
            self.loaded: list[object] = []

        def load_root(self, r: object, stop_event: threading.Event) -> None:
            self.loaded.append(r)
            stop_event.set()  # mid-walk cancellation surface

    fake_cache = PartialLoadCache()
    stop = threading.Event()
    cache_ready = threading.Event()

    daemon._sweep_once(
        roots=[tmp_path],  # one root → loop runs once → no top-of-loop catch
        cache=fake_cache,  # type: ignore[arg-type]
        daemon_started=dt.datetime.now(dt.UTC),
        daemon_create_time=None,
        stop_event=stop,
        cache_ready=cache_ready,
    )

    assert fake_cache.loaded == [tmp_path], "load_root was called exactly once"
    assert stop.is_set(), "stop_event was set during the load_root call"
    assert not cache_ready.is_set(), (
        "cache_ready MUST stay unset when load_root returned with stop_event set"
    )
    assert reconcile_calls == [], "Phase 2 reconcile must not run after stop"


def test_sweep_once_drains_deferred_between_phases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Events that arrived during the startup window MUST be re-dispatched
    after Phase 1 finishes so a newly-created ignored directory is marked
    within ~cache-load-time rather than waiting for Phase 2's wall-clock
    to reach it.

    The drain runs AFTER ``cache_ready.set()`` (so any concurrent
    append falls through to direct dispatch via the atomic-check) and
    BEFORE Phase 2 (so the marker decision uses the loaded cache)."""
    # Pre-load the queue with three deferred "events" (opaque strings here —
    # _sweep_once passes them through redispatch verbatim).
    deferred = daemon._DeferredEvents()
    deferred.append("ev1", threading.Event())  # unset event → append succeeds
    deferred.append("ev2", threading.Event())
    deferred.append("ev3", threading.Event())

    cache_ready = threading.Event()
    order: list[str] = []

    def fake_redispatch(event: object) -> None:
        # Drain must run AFTER cache_ready.set() — pin that ordering.
        assert cache_ready.is_set(), (
            "cache_ready must be set before deferred events are re-dispatched"
        )
        order.append(f"redispatch:{event}")

    def fake_reconcile(_root: Path, _sub: Path, _cache: object, **_kw: object) -> reconcile.Report:
        order.append("reconcile")
        return reconcile.Report()

    monkeypatch.setattr(daemon, "reconcile_subtree", fake_reconcile)

    daemon._sweep_once(
        roots=[tmp_path],
        cache=RuleCache(),
        daemon_started=dt.datetime.now(dt.UTC),
        daemon_create_time=None,
        stop_event=threading.Event(),
        cache_ready=cache_ready,
        deferred=deferred,
        redispatch=fake_redispatch,
    )

    # All three deferred events fire BEFORE Phase 2's first reconcile.
    redispatches = [s for s in order if s.startswith("redispatch:")]
    first_reconcile_idx = next((i for i, s in enumerate(order) if s == "reconcile"), -1)
    assert redispatches == ["redispatch:ev1", "redispatch:ev2", "redispatch:ev3"], (
        "drain must replay events in FIFO order"
    )
    assert first_reconcile_idx > 0, "Phase 2 reconcile must fire"
    assert order.index("redispatch:ev3") < first_reconcile_idx, (
        "drain must complete before Phase 2 reconcile"
    )
    # Queue is empty after drain.
    assert deferred.drain() == []


def test_sweep_once_continues_drain_on_redispatch_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """One bad deferred event (or one whose path vanished between
    deferral and replay) must NOT abort the rest of the drain. The
    drain logs the exception and continues. Mirrors the watchdog
    handler's broad-except pattern at the dispatch entry — convergence
    is the goal, not strict per-event correctness."""
    monkeypatch.setattr(
        daemon,
        "reconcile_subtree",
        lambda r, sub, c, **kw: reconcile.Report(),
    )

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

    caplog.set_level(logging.ERROR, logger="dbxignore.daemon")
    daemon._sweep_once(
        roots=[tmp_path],
        cache=RuleCache(),
        daemon_started=dt.datetime.now(dt.UTC),
        daemon_create_time=None,
        stop_event=threading.Event(),
        cache_ready=cache_ready,
        deferred=deferred,
        redispatch=maybe_failing,
    )

    assert seen == ["ev1", "ev3"], "drain must skip the failing event but continue"
    assert any("deferred event re-dispatch failed" in rec.message for rec in caplog.records), [
        rec.message for rec in caplog.records
    ]


def test_initial_sweep_worker_forwards_cache_ready_and_deferred_to_sweep_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Thin wrapper test: the worker now delegates ALL of cache_ready /
    deferred / redispatch to ``_sweep_once``. Pin the call-site wiring
    so a future refactor that drops one of the kwargs doesn't silently
    regress the initial-sweep contract."""
    monkeypatch.setattr(state, "user_state_dir", lambda: tmp_path)

    captured_kwargs: dict[str, object] = {}

    def fake_sweep_once(*_args: object, **kwargs: object) -> None:
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(daemon, "_sweep_once", fake_sweep_once)

    cache_ready = threading.Event()
    deferred = daemon._DeferredEvents()

    def redispatch(_event: object) -> None:
        pass

    daemon._initial_sweep_worker(
        roots=[],
        cache=None,  # type: ignore[arg-type]
        daemon_started=None,  # type: ignore[arg-type]
        daemon_create_time=None,
        stop_event=threading.Event(),
        cache_ready=cache_ready,
        deferred=deferred,
        redispatch=redispatch,
    )

    assert captured_kwargs["cache_ready"] is cache_ready
    assert captured_kwargs["deferred"] is deferred
    assert captured_kwargs["redispatch"] is redispatch

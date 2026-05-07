import threading
import time

from dbxignore.debounce import DebounceKey, Debouncer, EventKind

_DEFAULT_TIMEOUTS = {
    EventKind.DIR_CREATE: 0,
    EventKind.OTHER: 50,
    EventKind.RULES: 20,
}


def test_single_event_is_emitted_after_quiet_period() -> None:
    received: list[tuple[EventKind, DebounceKey, object]] = []
    d = Debouncer(
        on_emit=received.append,
        timeouts_ms={EventKind.DIR_CREATE: 10, EventKind.OTHER: 50, EventKind.RULES: 20},
    )
    d.start()
    try:
        d.submit(EventKind.OTHER, ("single", "key1"), "payload1")
        time.sleep(0.2)
        assert received == [(EventKind.OTHER, ("single", "key1"), "payload1")]
    finally:
        d.stop()


def test_coalesces_repeated_events_for_same_key() -> None:
    received: list[tuple[EventKind, DebounceKey, object]] = []
    d = Debouncer(
        on_emit=received.append,
        timeouts_ms={EventKind.DIR_CREATE: 10, EventKind.OTHER: 100, EventKind.RULES: 20},
    )
    d.start()
    try:
        # 2ms inter-submit gap (was 20ms) gives 50x headroom against the 100ms
        # debounce window, instead of 5x. Surfaced first on macos-latest CI when
        # PR adding macOS to the test matrix saw runner contention drift the
        # 20ms sleep past the debounce window — the first submit then fully
        # debounced and emitted before the second arrived (2 emits, expected 1).
        # Same family as BACKLOG #18 (Windows daemon-smoke poll widening).
        for _ in range(5):
            d.submit(EventKind.OTHER, ("single", "samekey"), "last")
            time.sleep(0.002)
        time.sleep(0.25)
        assert received == [(EventKind.OTHER, ("single", "samekey"), "last")]
    finally:
        d.stop()


def test_different_keys_emit_independently() -> None:
    received: list[tuple[EventKind, DebounceKey, object]] = []
    d = Debouncer(
        on_emit=received.append,
        timeouts_ms={EventKind.DIR_CREATE: 10, EventKind.OTHER: 50, EventKind.RULES: 20},
    )
    d.start()
    try:
        d.submit(EventKind.OTHER, ("single", "a"), "aa")
        d.submit(EventKind.OTHER, ("single", "b"), "bb")
        time.sleep(0.2)
        keys = sorted([r[1] for r in received])
        assert keys == [("single", "a"), ("single", "b")]
    finally:
        d.stop()


def test_dir_create_emits_immediately() -> None:
    received: list[tuple[EventKind, DebounceKey, object]] = []
    d = Debouncer(
        on_emit=received.append,
        timeouts_ms={EventKind.DIR_CREATE: 0, EventKind.OTHER: 500, EventKind.RULES: 100},
    )
    d.start()
    try:
        d.submit(EventKind.DIR_CREATE, ("single", "newdir"), "p")
        time.sleep(0.05)
        assert received == [(EventKind.DIR_CREATE, ("single", "newdir"), "p")]
    finally:
        d.stop()


def test_stop_wakes_idle_worker_quickly() -> None:
    """With nothing submitted, the worker waits indefinitely on its condition.
    stop() must notify so the worker exits promptly; a missed notify would
    leave the worker blocked on cond.wait() and stop() would hang forever
    (since join() has no timeout)."""
    d = Debouncer(on_emit=lambda _: None, timeouts_ms=_DEFAULT_TIMEOUTS)
    d.start()
    # Give the worker a moment to reach its wait() call on an empty queue.
    time.sleep(0.05)

    start = time.monotonic()
    d.stop()
    elapsed = time.monotonic() - start

    # If notify worked, stop() returns essentially immediately.
    assert elapsed < 0.2, f"stop() took {elapsed:.3f}s; worker was not woken"


def test_stop_waits_for_in_flight_emit() -> None:
    """If an emit is in flight when stop() is called, stop() must block until
    the emit completes — otherwise the daemon exits while reconcile_subtree
    is mid-write and ADS markers / state.json land half-written."""
    release = threading.Event()
    emit_finished = threading.Event()

    def slow_emit(_item: tuple[EventKind, DebounceKey, object]) -> None:
        release.wait(timeout=2.0)
        emit_finished.set()

    d = Debouncer(on_emit=slow_emit, timeouts_ms=_DEFAULT_TIMEOUTS)
    d.start()
    try:
        d.submit(EventKind.DIR_CREATE, ("single", "k"), "p")
        # Let the worker pick up the event and enter slow_emit.
        time.sleep(0.05)

        stop_thread = threading.Thread(target=d.stop)
        stop_thread.start()

        # stop() must not return while emit is still blocked on release.
        time.sleep(0.15)
        assert stop_thread.is_alive(), "stop() returned before emit completed"
        assert not emit_finished.is_set()

        release.set()
        stop_thread.join(timeout=2.0)
        assert not stop_thread.is_alive(), "stop() did not return after emit finished"
        assert emit_finished.is_set()
    finally:
        release.set()


def test_submit_from_within_emit_callback_is_processed() -> None:
    """Re-entrant submit (from inside on_emit) must not deadlock and must
    produce the follow-up event. Regression guard for the submit-while-
    emitting race the cond refactor has to preserve."""
    received: list[tuple[EventKind, DebounceKey, object]] = []
    done = threading.Event()

    d: Debouncer  # forward ref; captured by closure below

    def on_emit(item: tuple[EventKind, DebounceKey, object]) -> None:
        received.append(item)
        if item[1] == ("single", "first"):
            d.submit(EventKind.OTHER, ("single", "second"), "p2")
        else:
            done.set()

    d = Debouncer(on_emit=on_emit, timeouts_ms=_DEFAULT_TIMEOUTS)
    d.start()
    try:
        d.submit(EventKind.OTHER, ("single", "first"), "p1")
        assert done.wait(timeout=1.0), "re-entrant submit never emitted"
        assert [r[1] for r in received] == [("single", "first"), ("single", "second")]
    finally:
        d.stop()


def test_late_submit_after_emit_still_fires() -> None:
    """After emitting all pending items, the worker may wait indefinitely.
    A fresh submit must wake it; the new event must emit within its
    timeout window, not be stuck in the queue."""
    received: list[tuple[EventKind, DebounceKey, object]] = []
    d = Debouncer(on_emit=received.append, timeouts_ms=_DEFAULT_TIMEOUTS)
    d.start()
    try:
        d.submit(EventKind.OTHER, ("single", "first"), "a")
        time.sleep(0.2)  # wait for first to emit; worker goes idle
        assert received == [(EventKind.OTHER, ("single", "first"), "a")]

        d.submit(EventKind.OTHER, ("single", "second"), "b")
        time.sleep(0.2)
        assert received[-1] == (EventKind.OTHER, ("single", "second"), "b")
    finally:
        d.stop()

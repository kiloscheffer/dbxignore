import threading
import time

from dropboxignore.debounce import Debouncer, EventKind

_DEFAULT_TIMEOUTS = {
    EventKind.DIR_CREATE: 0,
    EventKind.OTHER: 50,
    EventKind.RULES: 20,
}


def test_single_event_is_emitted_after_quiet_period():
    received: list[tuple] = []
    d = Debouncer(on_emit=received.append,
                  timeouts_ms={EventKind.DIR_CREATE: 10, EventKind.OTHER: 50, EventKind.RULES: 20})
    d.start()
    try:
        d.submit(EventKind.OTHER, "key1", "payload1")
        time.sleep(0.2)
        assert received == [(EventKind.OTHER, "key1", "payload1")]
    finally:
        d.stop()


def test_coalesces_repeated_events_for_same_key():
    received: list[tuple] = []
    d = Debouncer(on_emit=received.append,
                  timeouts_ms={EventKind.DIR_CREATE: 10, EventKind.OTHER: 100, EventKind.RULES: 20})
    d.start()
    try:
        for _ in range(5):
            d.submit(EventKind.OTHER, "samekey", "last")
            time.sleep(0.02)
        time.sleep(0.25)
        assert received == [(EventKind.OTHER, "samekey", "last")]
    finally:
        d.stop()


def test_different_keys_emit_independently():
    received: list[tuple] = []
    d = Debouncer(on_emit=received.append,
                  timeouts_ms={EventKind.DIR_CREATE: 10, EventKind.OTHER: 50, EventKind.RULES: 20})
    d.start()
    try:
        d.submit(EventKind.OTHER, "a", "aa")
        d.submit(EventKind.OTHER, "b", "bb")
        time.sleep(0.2)
        keys = sorted([r[1] for r in received])
        assert keys == ["a", "b"]
    finally:
        d.stop()


def test_dir_create_emits_immediately():
    received: list[tuple] = []
    d = Debouncer(on_emit=received.append,
                  timeouts_ms={EventKind.DIR_CREATE: 0, EventKind.OTHER: 500, EventKind.RULES: 100})
    d.start()
    try:
        d.submit(EventKind.DIR_CREATE, "newdir", "p")
        time.sleep(0.05)
        assert received == [(EventKind.DIR_CREATE, "newdir", "p")]
    finally:
        d.stop()


def test_stop_wakes_idle_worker_quickly():
    """With nothing submitted, the worker waits indefinitely on its condition.
    stop() must notify so the worker exits promptly, not hang for the
    thread.join timeout."""
    d = Debouncer(on_emit=lambda _: None, timeouts_ms=_DEFAULT_TIMEOUTS)
    d.start()
    # Give the worker a moment to reach its wait() call on an empty queue.
    time.sleep(0.05)

    start = time.monotonic()
    d.stop()
    elapsed = time.monotonic() - start

    # Anything approaching join's 1.0s timeout means we failed to notify.
    assert elapsed < 0.2, f"stop() took {elapsed:.3f}s; worker was not woken"


def test_submit_from_within_emit_callback_is_processed():
    """Re-entrant submit (from inside on_emit) must not deadlock and must
    produce the follow-up event. Regression guard for the submit-while-
    emitting race the cond refactor has to preserve."""
    received: list[tuple] = []
    done = threading.Event()

    d: Debouncer  # forward ref; captured by closure below

    def on_emit(item):
        received.append(item)
        if item[1] == "first":
            d.submit(EventKind.OTHER, "second", "p2")
        else:
            done.set()

    d = Debouncer(on_emit=on_emit, timeouts_ms=_DEFAULT_TIMEOUTS)
    d.start()
    try:
        d.submit(EventKind.OTHER, "first", "p1")
        assert done.wait(timeout=1.0), "re-entrant submit never emitted"
        assert [r[1] for r in received] == ["first", "second"]
    finally:
        d.stop()


def test_late_submit_after_emit_still_fires():
    """After emitting all pending items, the worker may wait indefinitely.
    A fresh submit must wake it; the new event must emit within its
    timeout window, not be stuck in the queue."""
    received: list[tuple] = []
    d = Debouncer(on_emit=received.append, timeouts_ms=_DEFAULT_TIMEOUTS)
    d.start()
    try:
        d.submit(EventKind.OTHER, "first", "a")
        time.sleep(0.2)  # wait for first to emit; worker goes idle
        assert received == [(EventKind.OTHER, "first", "a")]

        d.submit(EventKind.OTHER, "second", "b")
        time.sleep(0.2)
        assert received[-1] == (EventKind.OTHER, "second", "b")
    finally:
        d.stop()

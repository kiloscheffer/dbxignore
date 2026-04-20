import time

from dropboxignore.debounce import Debouncer, EventKind


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

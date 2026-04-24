"""RuleCache mutations must be safe across the sweep thread and the
debouncer worker thread. Without internal locking, load_root's stale-purge
iterates self._rules while reload_file/remove_file can pop/insert — which
raises ``RuntimeError: dictionary changed size during iteration`` in CPython.

This test pins the invariant down without faking threads."""

from __future__ import annotations

import threading

from dbxignore.rules import RuleCache


def test_load_root_concurrent_with_reload_and_remove(tmp_path, write_file):
    for i in range(30):
        write_file(tmp_path / f"pkg{i}" / ".dropboxignore", f"build{i}/\n")

    cache = RuleCache()
    cache.load_root(tmp_path)

    stop = threading.Event()
    errors: list[BaseException] = []

    def sweep_loop() -> None:
        try:
            while not stop.is_set():
                cache.load_root(tmp_path)
        except BaseException as exc:
            errors.append(exc)

    def dispatch_loop() -> None:
        try:
            while not stop.is_set():
                for i in range(30):
                    ignore = tmp_path / f"pkg{i}" / ".dropboxignore"
                    cache.reload_file(ignore)
                    cache.remove_file(ignore)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=sweep_loop, name="sweep"),
        threading.Thread(target=dispatch_loop, name="dispatch"),
    ]
    for t in threads:
        t.start()
    # 150 ms is well over the millisecond-scale window needed for an
    # unprotected dict to trip the iteration-vs-mutation race.
    stop.wait(0.15)
    stop.set()
    for t in threads:
        t.join(timeout=2.0)

    assert not errors, errors

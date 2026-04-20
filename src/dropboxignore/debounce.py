"""Per-(kind, key) debouncing queue with a background worker."""

from __future__ import annotations

import enum
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class EventKind(enum.Enum):
    RULES = "rules"         # .dropboxignore create/modify/delete
    DIR_CREATE = "dir"      # directory creation (react immediately)
    OTHER = "other"         # everything else worth reconciling


@dataclass
class _Pending:
    payload: object
    deadline: float  # monotonic time when this should fire


class Debouncer:
    """Coalesce events per (kind, key) and emit after a quiet period."""

    def __init__(
        self,
        on_emit: Callable[[tuple[EventKind, str, object]], None],
        timeouts_ms: dict[EventKind, int],
    ) -> None:
        self._on_emit = on_emit
        self._timeouts = {k: v / 1000.0 for k, v in timeouts_ms.items()}
        self._pending: dict[tuple[EventKind, str], _Pending] = {}
        # Condition wraps its own lock; _pending is guarded by that lock.
        self._cond = threading.Condition()
        self._stopped = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="debouncer")
        self._thread.start()

    def stop(self) -> None:
        """Signal the worker to stop and block until it exits.

        Joins without a timeout: if an emit is in flight (reconcile_subtree
        can take seconds on a large Dropbox tree), the daemon's shutdown must
        wait for it to finish rather than abandon partially-written ADS or
        state. The worker is ``daemon=True``, so an unresponsive process-exit
        still terminates via the daemon-thread mechanism — but a well-behaved
        shutdown completes cleanly.
        """
        with self._cond:
            self._stopped = True
            self._cond.notify_all()
        if self._thread:
            self._thread.join()
            self._thread = None

    def submit(self, kind: EventKind, key: str, payload: object) -> None:
        deadline = time.monotonic() + self._timeouts[kind]
        with self._cond:
            self._pending[(kind, key)] = _Pending(payload=payload, deadline=deadline)
            # Always notify: the worker recomputes its wait-until on every
            # iteration anyway, so a spurious wakeup is just one no-op loop.
            self._cond.notify()

    def _run(self) -> None:
        while True:
            due: list[tuple[EventKind, str, object]] = []
            with self._cond:
                if self._stopped:
                    return
                now = time.monotonic()
                for key, pending in list(self._pending.items()):
                    if pending.deadline <= now:
                        due.append((key[0], key[1], pending.payload))
                        del self._pending[key]
                if not due:
                    # Wait until the soonest deadline, or indefinitely if no
                    # items are pending. submit() / stop() will notify.
                    if self._pending:
                        wait_s = max(
                            0.0,
                            min(p.deadline for p in self._pending.values())
                            - time.monotonic(),
                        )
                        self._cond.wait(timeout=wait_s)
                    else:
                        self._cond.wait()
                    continue
            # Emit outside the lock so on_emit can re-entrantly call submit().
            for item in due:
                try:
                    self._on_emit(item)
                except Exception:  # noqa: BLE001
                    logger.exception("debouncer emit handler failed")

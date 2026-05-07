"""Per-(kind, key) debouncing queue with a background worker."""

from __future__ import annotations

import enum
import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class EventKind(enum.Enum):
    RULES = "rules"  # .dropboxignore create/modify/delete
    DIR_CREATE = "dir"  # directory creation (react immediately)
    OTHER = "other"  # everything else worth reconciling


# Role discriminator on the debounce key. `"single"` is the default for any
# event whose dedupe identity is just the path (DIR_CREATE, OTHER, and most
# RULES events). The other two roles disambiguate the two RULES sub-shapes
# that previously collided under a single string-keyed model:
#
#   - `"moved-out"` — moved event whose src is a `.dropboxignore` (the
#     rule file is moving away from its parent directory).
#   - `"moved-into"` — moved event whose dest is a `.dropboxignore` (the
#     rule file is appearing at a new parent — atomic save or cross-watch
#     move-in).
#
# Without the role discriminator a move-out `A/.dropboxignore` -> `B/...`
# and a created/modified event for `A/.dropboxignore` within the 100ms
# RULES debounce window both keyed on `str(A/.dropboxignore).lower()`, and
# the Debouncer's last-wins overwrite would drop one event's dispatch.
# Surfaced in BACKLOG item #77; the previous string-prefix `"moved-into:"`
# scheme (PR #120) addressed only the moved-out vs moved-into half of the
# disambiguation.
DebounceRole = Literal["single", "moved-out", "moved-into"]
DebounceKey = tuple[DebounceRole, str]


@dataclass
class _Pending:
    payload: object
    deadline: float  # monotonic time when this should fire


class Debouncer:
    """Coalesce events per (kind, key) and emit after a quiet period."""

    def __init__(
        self,
        on_emit: Callable[[tuple[EventKind, DebounceKey, object]], None],
        timeouts_ms: dict[EventKind, int],
    ) -> None:
        self._on_emit = on_emit
        self._timeouts = {k: v / 1000.0 for k, v in timeouts_ms.items()}
        self._pending: dict[tuple[EventKind, DebounceKey], _Pending] = {}
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
        """Signal the worker and block until it exits; no join timeout so
        in-flight emits finish cleanly before shutdown."""
        with self._cond:
            self._stopped = True
            self._cond.notify_all()
        if self._thread:
            self._thread.join()
            self._thread = None

    def submit(self, kind: EventKind, key: DebounceKey, payload: object) -> None:
        timeout = self._timeouts[kind]
        deadline = time.monotonic() + timeout
        with self._cond:
            self._pending[(kind, key)] = _Pending(payload=payload, deadline=deadline)
            # DEBUG-level boundary log for backlog item #34 timing diagnostics.
            # Inside the lock + after the insert so `queue_depth` is the
            # post-insert size, not a racing pre-insert read. Pairs with the
            # `emit` log below to measure debouncer queue latency. No-op cost
            # when DBXIGNORE_LOG_LEVEL != DEBUG.
            logger.debug(
                "submit kind=%s role=%s path=%s timeout=%.3fs queue_depth=%d",
                kind.value,
                key[0],
                key[1],
                timeout,
                len(self._pending),
            )
            # Always notify: the worker recomputes its wait-until on every
            # iteration anyway, so a spurious wakeup is just one no-op loop.
            self._cond.notify()

    def _run(self) -> None:
        while True:
            due: list[tuple[EventKind, DebounceKey, object, float]] = []
            with self._cond:
                if self._stopped:
                    return
                now = time.monotonic()
                for key, pending in list(self._pending.items()):
                    if pending.deadline <= now:
                        # Carry deadline alongside the payload so the post-lock
                        # emit log can report queue dwell time (now - deadline).
                        # Deadline is the SCHEDULED fire time; if dwell > 0 the
                        # worker thread was starved between deadline and now.
                        due.append((key[0], key[1], pending.payload, pending.deadline))
                        del self._pending[key]
                if not due:
                    # Wait until the soonest deadline, or indefinitely if no
                    # items are pending. submit() / stop() will notify.
                    if self._pending:
                        wait_s = max(
                            0.0,
                            min(p.deadline for p in self._pending.values()) - time.monotonic(),
                        )
                        self._cond.wait(timeout=wait_s)
                    else:
                        self._cond.wait()
                    continue
            # Emit outside the lock so on_emit can re-entrantly call submit().
            for emit_kind, emit_key, payload, deadline in due:
                # DEBUG-level boundary log for backlog item #34 timing
                # diagnostics. `dwell` measures how long after the deadline
                # the worker actually got to the item — under GIL/CPU
                # starvation this can be much larger than the configured
                # timeout. No-op cost when DBXIGNORE_LOG_LEVEL != DEBUG.
                emit_at = time.monotonic()
                logger.debug(
                    "emit kind=%s role=%s path=%s dwell=%.3fs",
                    emit_kind.value,
                    emit_key[0],
                    emit_key[1],
                    emit_at - deadline,
                )
                try:
                    self._on_emit((emit_kind, emit_key, payload))
                except Exception:  # noqa: BLE001
                    logger.exception("debouncer emit handler failed")

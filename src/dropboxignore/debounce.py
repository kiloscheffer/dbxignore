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
        tick_ms: int = 20,
    ) -> None:
        self._on_emit = on_emit
        self._timeouts = {k: v / 1000.0 for k, v in timeouts_ms.items()}
        self._tick = tick_ms / 1000.0
        self._pending: dict[tuple[EventKind, str], _Pending] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="debouncer")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def submit(self, kind: EventKind, key: str, payload: object) -> None:
        timeout = self._timeouts[kind]
        deadline = time.monotonic() + timeout
        with self._lock:
            self._pending[(kind, key)] = _Pending(payload=payload, deadline=deadline)

    def _run(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            due: list[tuple[EventKind, str, object]] = []
            with self._lock:
                for key, pending in list(self._pending.items()):
                    if pending.deadline <= now:
                        due.append((key[0], key[1], pending.payload))
                        del self._pending[key]
            for item in due:
                try:
                    self._on_emit(item)
                except Exception:  # noqa: BLE001
                    logger.exception("debouncer emit handler failed")
            time.sleep(self._tick)

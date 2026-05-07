"""Tiny helpers for DEBUG-level instrumentation that need to be cheap when DEBUG is off."""

from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextlib.contextmanager
def timed_debug(logger: logging.Logger, msg_fmt: str, *args: object) -> Iterator[None]:
    """Time the wrapped block and emit ``msg_fmt + " duration=%.4fs"`` at DEBUG.

    Skips both the ``time.perf_counter()`` calls and the format-time work
    when DEBUG isn't enabled on the logger. The original
    ``t0 = time.perf_counter(); ...; logger.debug(... time.perf_counter() - t0)``
    shape paid the perf_counter cost on every call regardless of log level,
    which the per-mutation reconcile loop multiplied by the size of the
    swept tree (item #53 measured 49.62s on a 27k-dir tree). The
    ``isEnabledFor(DEBUG)`` gate makes the no-op-when-not-debug claim
    actually hold.
    """
    if not logger.isEnabledFor(logging.DEBUG):
        yield
        return
    t0 = time.perf_counter()
    yield
    logger.debug(msg_fmt + " duration=%.4fs", *args, time.perf_counter() - t0)

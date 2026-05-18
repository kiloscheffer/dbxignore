"""Tests for ``daemon._capture_create_time``.

The helper captures the daemon's own ``psutil.Process(os.getpid()).create_time()``
at startup so ``state.json`` carries it for future PID-reuse-race protection
in ``is_daemon_alive``. The wrapper exists so the failure modes (psutil
missing, OSError from low-level OS probes, ``psutil.Error`` subclasses like
NoSuchProcess on the platform-specific OpenProcess path on Windows) can be
tested without spinning up the full daemon.

This helper exists because Windows daemons occasionally write
``daemon_create_time: null`` to ``state.json`` (non-deterministic; observed
on Windows). Inlining the capture in ``daemon.run`` with a bare
``except Exception`` would swallow the exception silently — no log line, no
traceback, no diagnostic data when the null state appears. The narrow catch
+ WARNING ensures any null observation in the wild leaves forensic evidence
in ``daemon.log``; unanticipated exception types propagate up ``daemon.run``
(releasing the singleton lock via the outer ``try/finally`` and aborting
startup before the observer or initial-sweep worker are created) instead of
silently mis-initializing the daemon with a misleading None record.
"""

from __future__ import annotations

import logging
import os

import psutil  # type: ignore[import-untyped, unused-ignore]
import pytest

from dbxignore import daemon


def test_capture_create_time_returns_float_for_live_process(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Happy path: ``psutil.Process(os.getpid()).create_time()`` returns a
    non-None positive float for the running test process, and no WARNING
    is logged. Locks in the contract that the helper does not warn on the
    only path that's actually exercised in production almost all the time.
    """
    caplog.set_level(logging.WARNING, logger="dbxignore.daemon")
    result = daemon._capture_create_time()
    assert isinstance(result, float)
    assert result > 0
    assert not any("daemon_create_time" in rec.message for rec in caplog.records), (
        f"unexpected WARNING(s) on happy path: {[r.message for r in caplog.records]}"
    )


def test_capture_create_time_returns_none_and_warns_on_psutil_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When ``psutil.Process(...).create_time()`` raises a ``psutil.Error``
    subclass (e.g. ``NoSuchProcess`` in a hypothetical race, ``AccessDenied``
    in locked-down Windows configurations), the helper returns None and
    logs a WARNING that includes the exception type name.

    The exception-type-in-WARNING contract is the load-bearing part of #21:
    the next null observation in the wild needs the type name in
    ``daemon.log`` so we can pick among the candidate fixes (retry, reject,
    investigate). Without the type name the WARNING is decorative.
    """

    class _RaisingProc:
        def __init__(self, _pid: int) -> None: ...

        def create_time(self) -> float:
            raise psutil.NoSuchProcess(os.getpid())

    monkeypatch.setattr(psutil, "Process", _RaisingProc)

    caplog.set_level(logging.WARNING, logger="dbxignore.daemon")
    result = daemon._capture_create_time()
    assert result is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("daemon_create_time" in r.message for r in warnings), (
        "expected WARNING mentioning daemon_create_time; "
        f"got: {[r.message for r in caplog.records]}"
    )
    assert any("NoSuchProcess" in r.message for r in warnings), (
        "WARNING must include the exception type name so future debugging "
        f"knows what raised; got: {[r.message for r in caplog.records]}"
    )


def test_capture_create_time_returns_none_and_warns_on_oserror(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``OSError`` from the underlying ``psutil.Process`` construction (e.g.
    ``/proc`` read errors on Linux, ``OpenProcess`` permission errors on
    Windows) follows the same path as ``psutil.Error``: return None, log
    WARNING with type+message.
    """

    class _RaisingProc:
        def __init__(self, _pid: int) -> None:
            raise OSError("simulated /proc access failure")

    monkeypatch.setattr(psutil, "Process", _RaisingProc)

    caplog.set_level(logging.WARNING, logger="dbxignore.daemon")
    result = daemon._capture_create_time()
    assert result is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("daemon_create_time" in r.message for r in warnings)
    assert any("OSError" in r.message for r in warnings), (
        f"WARNING must include the exception type name; got: {[r.message for r in caplog.records]}"
    )


def test_capture_create_time_propagates_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare ``except Exception`` here would be a forensic dead-end: every
    exception type would be swallowed silently, including types nobody
    anticipated (TypeError from a malformed psutil shim, RuntimeError from
    a third-party hook, etc.). The catch handles only the anticipated set
    ((ImportError, psutil.Error, OSError, SystemError)); other exceptions
    propagate up ``daemon.run`` (releasing the singleton lock via the outer
    ``try/finally`` and aborting startup before the observer or initial-sweep
    worker are created) — preferable to silently mis-initializing the daemon
    with a misleading None record.
    """

    class _RaisingProc:
        def __init__(self, _pid: int) -> None: ...

        def create_time(self) -> float:
            raise RuntimeError("simulated unanticipated failure")

    monkeypatch.setattr(psutil, "Process", _RaisingProc)

    with pytest.raises(RuntimeError, match="simulated unanticipated failure"):
        daemon._capture_create_time()

"""Trap inotify resource exhaustion at observer startup (BACKLOG #52)."""

from __future__ import annotations

import contextlib
import errno
import logging
from typing import TYPE_CHECKING, Any

import pytest

from dbxignore import daemon, roots, state

if TYPE_CHECKING:
    from pathlib import Path


class _FakeObserver:
    """Stand-in for watchdog.Observer; .start() raises a configured OSError."""

    def __init__(self, *, start_error: OSError | None = None) -> None:
        self._start_error = start_error
        self.scheduled: list[tuple[Any, str, bool]] = []
        self.started = False
        self.stopped = False
        self.joined = False

    def schedule(self, handler: Any, path: str, recursive: bool = False) -> None:
        self.scheduled.append((handler, path, recursive))

    def start(self) -> None:
        if self._start_error is not None:
            raise self._start_error
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self) -> None:
        self.joined = True


class _FakeDebouncer:
    """Stand-in for Debouncer; records start/stop call ordering."""

    def __init__(self, **_kwargs: Any) -> None:
        self.calls: list[str] = []

    def start(self) -> None:
        self.calls.append("start")

    def stop(self) -> None:
        self.calls.append("stop")

    def submit(self, *_args: Any, **_kwargs: Any) -> None:  # pragma: no cover
        pass


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    start_error: OSError | None = None,
) -> tuple[_FakeObserver, _FakeDebouncer]:
    fake_observer = _FakeObserver(start_error=start_error)
    fake_debouncer = _FakeDebouncer()
    monkeypatch.setattr(daemon, "Observer", lambda: fake_observer)
    monkeypatch.setattr(daemon, "Debouncer", lambda **kw: fake_debouncer)
    monkeypatch.setattr(daemon, "_configured_logging", contextlib.nullcontext)
    monkeypatch.setattr(roots, "discover", lambda: [tmp_path])
    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")
    return fake_observer, fake_debouncer


def test_run_traps_enospc_and_exits_75(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ENOSPC at observer.start() → ERROR log with sysctl block + sys.exit(75)."""
    err = OSError(errno.ENOSPC, "inotify watch limit reached")
    _, fake_debouncer = _install_fakes(monkeypatch, tmp_path, start_error=err)

    caplog.set_level(logging.ERROR, logger="dbxignore.daemon")
    with pytest.raises(SystemExit) as exc_info:
        daemon.run()

    assert exc_info.value.code == 75
    messages = "\n".join(rec.message for rec in caplog.records)
    assert "fs.inotify.max_user_watches=524288" in messages
    assert "ENOSPC" in messages
    # Outer finally must run despite SystemExit so the debouncer thread is stopped.
    assert fake_debouncer.calls == ["start", "stop"]

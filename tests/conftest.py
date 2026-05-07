"""Shared fixtures and helpers for the dbxignore test suite."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest

from dbxignore import cli, reconcile

if TYPE_CHECKING:
    from pathlib import Path


class WriteFile(Protocol):
    """Callable shape for the `write_file` fixture: `(path[, content])` -> Path."""

    def __call__(self, path: Path, content: str = ...) -> Path: ...


class FakeMarkers:
    """In-memory stand-in for the ``markers`` module."""

    def __init__(self) -> None:
        self._ignored: set[Path] = set()
        self.set_calls: list[Path] = []
        self.clear_calls: list[Path] = []

    def is_ignored(self, path: Path) -> bool:
        return path.resolve() in self._ignored

    def set_ignored(self, path: Path) -> None:
        p = path.resolve()
        self._ignored.add(p)
        self.set_calls.append(p)

    def clear_ignored(self, path: Path) -> None:
        p = path.resolve()
        self._ignored.discard(p)
        self.clear_calls.append(p)


@pytest.fixture
def fake_markers(monkeypatch: pytest.MonkeyPatch) -> FakeMarkers:
    """Replace ``markers`` in both ``reconcile`` and ``cli`` with a shared FakeMarkers."""
    fake = FakeMarkers()
    monkeypatch.setattr(reconcile, "markers", fake)
    monkeypatch.setattr(cli, "markers", fake)
    return fake


@pytest.fixture
def write_file() -> WriteFile:
    """Write a file, creating parent dirs; returns a callable ``(path, content="")``."""

    def _write(path: Path, content: str = "") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    return _write


@pytest.fixture
def fake_psutil_process(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201 — factory-fixture
    """Factory: install a fake ``psutil.Process`` + ``pid_exists`` pair.

    Centralizes the per-test ``_FakeProc`` boilerplate that
    ``state.is_daemon_alive`` tests (and a couple of CLI / daemon-singleton
    callers) had been duplicating ~10 times. The factory returns an
    install-callable so a single test can configure the fake exactly once
    and let the rest of the test exercise the real code.

    Kwargs:

    - ``name`` — string returned by ``proc.name()``. Default ``"python.exe"``.
    - ``create_time`` — float returned by ``proc.create_time()``. Default
      ``None``; if ``None``, calling ``proc.create_time()`` raises an
      AssertionError so a test that doesn't expect the create_time path to
      fire can detect when it does.
    - ``pid_exists`` — bool returned by ``psutil.pid_exists``. Default
      ``True``.
    - ``name_raises`` — exception instance to raise from ``proc.name()``
      instead of returning the name. Default ``None`` (return the name).
      Useful for the ``psutil.NoSuchProcess`` race-window test.
    """
    import psutil  # type: ignore[import-untyped, unused-ignore]

    def _install(
        *,
        name: str = "python.exe",
        create_time: float | None = None,
        pid_exists: bool = True,
        name_raises: BaseException | None = None,
    ) -> None:
        class _FakeProc:
            def __init__(self, _pid: int) -> None:
                # Embed the short-circuit contract: production code MUST
                # consult pid_exists before constructing Process(pid). Any
                # caller that sets pid_exists=False but doesn't short-circuit
                # gets a clear failure here. Same assertion strength as the
                # prior inline sentinel-Process pattern, but now applies
                # uniformly to every pid_exists=False test.
                if not pid_exists:
                    raise AssertionError(
                        "fake_psutil_process: Process(pid) was constructed "
                        "even though pid_exists=False — production code must "
                        "short-circuit on pid_exists before calling Process()"
                    )

            def name(self) -> str:
                if name_raises is not None:
                    raise name_raises
                return name

            def create_time(self) -> float:
                if create_time is None:
                    raise AssertionError(
                        "fake_psutil_process: create_time() called but the "
                        "test didn't supply a value — pass create_time=... "
                        "to the factory if the strict-mode path is expected"
                    )
                return create_time

        monkeypatch.setattr(psutil, "pid_exists", lambda _pid: pid_exists)
        monkeypatch.setattr(psutil, "Process", _FakeProc)

    return _install

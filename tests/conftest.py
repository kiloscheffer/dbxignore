"""Shared fixtures and helpers for the dbxignore test suite."""

from __future__ import annotations

import ctypes
import os
import sys
import time
from typing import TYPE_CHECKING, Protocol
from unittest.mock import MagicMock

import pytest

from dbxignore import cli, daemon, reconcile, state

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable
    from pathlib import Path


def _poll_until(fn: Callable[[], bool], timeout_s: float = 5.0, interval_s: float = 0.05) -> bool:
    """Poll ``fn`` until it returns True or ``timeout_s`` elapses.

    Returns True if ``fn`` ever returned True before the deadline; False if
    the timeout fired first. Used by daemon-thread tests that need to wait
    for an asynchronous condition (state.json appearance, log line, marker
    write) without resorting to ``time.sleep`` of a fixed duration.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(interval_s)
    return False


def setup_daemon_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, root: Path) -> Path:
    """Install the standard daemon-test monkeypatches and return ``state_dir``.

    Most daemon-thread tests redirect ``state.{default_path,user_state_dir,
    user_log_dir}`` to a per-test directory and replace ``roots_module.discover``
    with a fixed-roots lambda. Bundling the four ``setattr`` calls behind one
    helper keeps the per-test setup focused on the behavior under test.
    """
    state_dir = tmp_path / "state"
    monkeypatch.setattr(state, "default_path", lambda: state_dir / "state.json")
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(state, "user_log_dir", lambda: state_dir)
    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [root])  # type: ignore[attr-defined, unused-ignore]
    return state_dir


def stub_event(
    kind: str,
    src_path: str,
    is_directory: bool = False,
    dest_path: str | None = None,
) -> MagicMock:
    """Build a watchdog-shaped event mock for direct ``daemon._dispatch`` tests.

    Returns a ``MagicMock`` with ``event_type``, ``src_path``, ``dest_path``,
    and ``is_directory`` attributes — the only fields ``_classify`` /
    ``_dispatch`` consume. Lets dispatch tests fire synthetic events at the
    daemon's classification + reconcile chain without bringing up a real
    ``Observer`` (whose ``ReadDirectoryChangesW`` events are unreliable on
    Windows CI runners — see backlog item #34).
    """
    e = MagicMock()
    e.event_type = kind
    e.src_path = src_path
    e.dest_path = dest_path
    e.is_directory = is_directory
    return e


class WriteFile(Protocol):
    """Callable shape for the `write_file` fixture: `(path[, content])` -> Path."""

    def __call__(self, path: Path, content: str = ...) -> Path: ...


class FakePsutilProcess(Protocol):
    """Callable shape for the `fake_psutil_process` fixture's install function."""

    def __call__(
        self,
        *,
        name: str = ...,
        create_time: float | None = ...,
        pid_exists: bool = ...,
        name_raises: BaseException | None = ...,
    ) -> None: ...


class FakeMarkers:
    """In-memory stand-in for the ``markers`` module."""

    def __init__(self) -> None:
        self._ignored: set[Path] = set()
        self.set_calls: list[Path] = []
        self.clear_calls: list[Path] = []
        # Records every is_ignored() query so pruning-contract tests can
        # assert that grandchildren of an already-marked subtree are NEVER
        # queried — see test_reconcile_basic.py:test_does_not_descend_into_marked_subtree.
        self.is_ignored_calls: list[Path] = []

    def is_ignored(self, path: Path) -> bool:
        self.is_ignored_calls.append(path.resolve())
        return path.resolve() in self._ignored

    def set_ignored(self, path: Path) -> None:
        p = path.resolve()
        self._ignored.add(p)
        self.set_calls.append(p)

    def clear_ignored(self, path: Path) -> None:
        p = path.resolve()
        self._ignored.discard(p)
        self.clear_calls.append(p)


class BlockingMarkers(FakeMarkers):
    """``FakeMarkers`` whose ``is_ignored`` waits on a caller-controlled gate.

    Used by `tests/test_daemon_initial_sweep.py` to deterministically pause
    the daemon's worker thread mid-sweep so tests can observe the
    ``state=starting`` window. The 10-second timeout on ``gate.wait()``
    bounds the failure mode: if a test forgets to open the gate, the
    daemon thread hangs but the wait returns False after 10s, the worker
    proceeds, and the test fails fast with a meaningful assertion error
    rather than blocking the full pytest timeout.
    """

    def __init__(self, gate: threading.Event) -> None:
        super().__init__()
        self._gate = gate

    def is_ignored(self, path: Path) -> bool:
        self._gate.wait(timeout=10.0)
        return super().is_ignored(path)


@pytest.fixture(autouse=True)
def _stub_get_console_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force GetConsoleWindow() to report a console attached (return non-zero).

    `GetConsoleWindow()` returns 0 in the pytest process when it runs without
    an attached console window (e.g. under `uv run python -m pytest` spawned
    from some terminals or IDE integrations on Windows). That would make
    `_windows_dialogs.should_use_gui_dialogs()` return True for the entire
    test run, causing every code path that calls `_error_or_messagebox` or
    `_confirm_or_messagebox` to invoke the real `MessageBoxW` — blocking the
    suite waiting for user input.

    This fixture stubs the kernel32 call to return 0x1 (a plausible HWND) so
    `should_use_gui_dialogs()` returns False by default across all tests.
    Tests that specifically want to exercise the no-console branch should
    monkeypatch `ctypes.windll` (or `_windows_dialogs.should_use_gui_dialogs`)
    directly — that monkeypatch wins over the module-level attribute set here
    because pytest monkeypatch restores in LIFO order.

    On non-Windows the stub is a no-op (`ctypes.windll` is absent, and
    `should_use_gui_dialogs()` already returns False via the platform guard).
    """
    if sys.platform != "win32":
        return

    class _FakeKernel32WithConsole:
        @staticmethod
        def GetConsoleWindow() -> int:  # noqa: N802
            return 0x1  # non-zero → console attached → should_use_gui_dialogs() → False

    class _FakeWindll:
        kernel32 = _FakeKernel32WithConsole()

    monkeypatch.setattr(ctypes, "windll", _FakeWindll(), raising=False)


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
def require_case_sensitive_fs(tmp_path: Path) -> None:
    """Skip the test if ``tmp_path`` lives on a case-insensitive filesystem.

    Probe via the alternate-name approach (write one casing, check whether
    the other "exists"): `PosixPath.resolve()` doesn't lowercase basenames
    on POSIX, so an equality check on resolved paths would falsely report
    distinct files on case-insensitive macOS APFS-default. Use this for
    tests that need both `.dropboxignore` and `.DropboxIgnore` to coexist
    as distinct on-disk entries.
    """
    probe = tmp_path / "_case_probe"
    probe.write_text("", encoding="utf-8")
    if (tmp_path / "_CASE_PROBE").exists():
        pytest.skip("case-insensitive FS — both names resolve to one file")
    probe.unlink()


@pytest.fixture
def symlink_capable(tmp_path: Path) -> None:
    """Skip the test if symlink creation isn't permitted (Windows without Dev Mode).

    A runtime probe rather than a static ``skipif(sys.platform == "win32")``:
    hosts that can create symlinks (Linux, macOS, Windows with Developer
    Mode — including CI's ``windows-latest`` runner) exercise the test; only
    hosts that genuinely can't (Windows without Dev Mode, locked-down
    containers) skip.
    """
    probe_target = tmp_path / "_symlink_probe_target"
    probe_target.touch()
    probe_link = tmp_path / "_symlink_probe_link"
    try:
        os.symlink(probe_target, probe_link)
    except OSError as exc:
        pytest.skip(f"symlink creation not permitted on this host: {exc}")
    finally:
        probe_link.unlink(missing_ok=True)
        probe_target.unlink(missing_ok=True)


@pytest.fixture
def fake_psutil_process(monkeypatch: pytest.MonkeyPatch) -> FakePsutilProcess:
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

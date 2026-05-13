"""Tests for src/dbxignore/_windows_console.py.

The orchestrator (`early_init`) is tested cross-platform via mocks of
the helpers. The ctypes helpers are tested Windows-only via smoke
tests that gate on `sys.platform == "win32"`.
"""

from __future__ import annotations

import sys
import types

import pytest

from dbxignore import _windows_console


def test_early_init_no_op_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Linux/macOS, early_init returns without touching anything."""
    monkeypatch.setattr(sys, "platform", "linux")
    # Sentinel: if attach helper were called, it'd raise (linux has no ctypes.windll)
    _windows_console.early_init()  # should not raise


def test_is_stream_connected_false_for_none() -> None:
    assert not _windows_console._is_stream_connected(None)


def test_is_stream_connected_false_when_fileno_raises() -> None:
    class BrokenStream:
        def fileno(self) -> int:
            raise OSError("no fd")

    assert not _windows_console._is_stream_connected(BrokenStream())


def test_is_stream_connected_true_for_real_stdio() -> None:
    """Under pytest, sys.stdout has a valid fileno (pytest's capture
    wrappers proxy through to a real FD). Verifies the happy-path
    detection."""
    assert _windows_console._is_stream_connected(sys.stdout)


def test_early_init_attach_success_redirects_and_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(_windows_console, "_has_console", lambda: False)
    monkeypatch.setattr(_windows_console, "_attach_parent_console", lambda: True)
    calls: list[str] = []
    monkeypatch.setattr(
        _windows_console,
        "_redirect_stdio_to_attached_console",
        lambda: calls.append("redirect"),
    )
    monkeypatch.setattr(
        _windows_console,
        "_show_help_message_box",
        lambda: calls.append("messagebox"),
    )
    _windows_console.early_init()  # should not exit
    assert calls == ["redirect"]


def test_early_init_attach_fail_with_argv_returns_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["dbxignore.exe", "daemon"])
    monkeypatch.setattr(_windows_console, "_has_console", lambda: False)
    monkeypatch.setattr(_windows_console, "_attach_parent_console", lambda: False)
    calls: list[str] = []
    monkeypatch.setattr(
        _windows_console,
        "_show_help_message_box",
        lambda: calls.append("messagebox"),
    )
    _windows_console.early_init()
    assert calls == []


def test_early_init_attach_fail_no_argv_shows_box_and_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["dbxignore.exe"])
    monkeypatch.setattr(_windows_console, "_has_console", lambda: False)
    monkeypatch.setattr(_windows_console, "_attach_parent_console", lambda: False)
    box_calls: list[str] = []
    monkeypatch.setattr(
        _windows_console,
        "_show_help_message_box",
        lambda: box_calls.append("shown"),
    )
    with pytest.raises(SystemExit) as exc_info:
        _windows_console.early_init()
    assert exc_info.value.code == 0
    assert box_calls == ["shown"]


def test_early_init_messagebox_oserror_still_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even if MessageBox itself fails (unusual session), we still exit
    cleanly. `_show_help_message_box` already swallows OSError internally;
    here we simulate that with a no-op stub."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["dbxignore.exe"])
    monkeypatch.setattr(_windows_console, "_has_console", lambda: False)
    monkeypatch.setattr(_windows_console, "_attach_parent_console", lambda: False)
    monkeypatch.setattr(_windows_console, "_show_help_message_box", lambda: None)
    with pytest.raises(SystemExit) as exc_info:
        _windows_console.early_init()
    assert exc_info.value.code == 0


@pytest.mark.parametrize("flag", ["--help", "-h", "--version"])
def test_early_init_help_or_version_does_not_take_messagebox_branch(
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
) -> None:
    """--help / --version are valid CLI usage that must NEVER pop the
    MessageBox even if AttachConsole fails (unusual edge: someone
    double-clicks a desktop shortcut with `--help` in the target).
    Argv with any non-program token always takes the silent-return
    branch; click handles --help / --version normally from there."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["dbxignore.exe", flag])
    monkeypatch.setattr(_windows_console, "_has_console", lambda: False)
    monkeypatch.setattr(_windows_console, "_attach_parent_console", lambda: False)
    box_calls: list[str] = []
    monkeypatch.setattr(
        _windows_console,
        "_show_help_message_box",
        lambda: box_calls.append("shown"),
    )
    _windows_console.early_init()
    assert box_calls == []


def test_early_init_no_op_when_process_already_has_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the process already has a console (CUI trampoline case), do
    nothing — let click handle argv normally. Specifically: even with
    empty argv, do NOT pop the MessageBox."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["dbxignore.exe"])  # empty
    monkeypatch.setattr(_windows_console, "_has_console", lambda: True)
    calls: list[str] = []
    monkeypatch.setattr(
        _windows_console,
        "_attach_parent_console",
        lambda: calls.append("attach"),
    )
    monkeypatch.setattr(
        _windows_console,
        "_show_help_message_box",
        lambda: calls.append("messagebox"),
    )
    _windows_console.early_init()  # should not raise SystemExit
    assert calls == []  # neither helper called


def test_has_console_returns_bool() -> None:
    """_has_console should always return a bool regardless of platform.

    On non-Windows, ctypes.windll is absent and the helper must return
    False via OSError/AttributeError suppression. On Windows under pytest
    (a CUI process) it should return True. Either way: a bool.
    """
    result = _windows_console._has_console()
    # On non-Windows: must be False (ctypes.windll missing)
    # On Windows under pytest: pytest is CUI, so True
    # Don't assert a specific value across platforms — assert it returns a bool
    assert isinstance(result, bool)


def test_redirect_preserves_valid_stdout_and_reopens_missing_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed case: stdout valid (already wired up), stderr None.
    Must NOT overwrite stdout; MUST reopen stderr against CONOUT$.
    Verifies the per-stream preservation contract from the spec.

    _restore_inherited_stdio is mocked to a no-op here — this test
    exercises the CONOUT$/CONIN$ fallback specifically, not the
    Win32-handle rehydration path."""
    monkeypatch.setattr(sys, "platform", "win32")
    fake_stdout = sys.stdout  # already valid under pytest
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "stdin", None)
    monkeypatch.setattr(_windows_console, "_restore_inherited_stdio", lambda: None)

    opened: list[tuple[str, str]] = []

    def fake_open(name: str, mode: str, **kwargs: object) -> object:
        opened.append((name, mode))
        return object()  # sentinel — not a real file object

    monkeypatch.setattr("builtins.open", fake_open)
    _windows_console._redirect_stdio_to_attached_console()
    # stdout untouched (was already valid)
    assert sys.stdout is fake_stdout
    # stderr and stdin reopened
    assert sys.stderr is not None
    assert sys.stdin is not None
    # Confirm the opens went to CONOUT$ (for stderr) and CONIN$ (for stdin) — NOT stdout
    assert opened == [("CONOUT$", "w"), ("CONIN$", "r")]


def test_restore_inherited_stdio_skips_already_valid_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a stream already has a valid fileno, _restore should leave it alone
    even when GetStdHandle would return a different handle. Verifies the
    "skip if already valid" guard."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(_windows_console, "_is_stream_connected", lambda s: s is not None)
    sentinel_stdout = sys.stdout
    sentinel_stderr = sys.stderr
    sentinel_stdin = sys.stdin
    # _restore_inherited_stdio starts with `import msvcrt` (Windows-only stdlib);
    # inject a fake so the test runs cross-platform (sys.modules-injection pattern).
    fake_msvcrt = types.ModuleType("msvcrt")
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    _windows_console._restore_inherited_stdio()
    assert sys.stdout is sentinel_stdout
    assert sys.stderr is sentinel_stderr
    assert sys.stdin is sentinel_stdin


def test_restore_inherited_stdio_recovers_valid_win32_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If sys.stdout is None but GetStdHandle returns a valid handle, the
    helper should wrap it as a Python file object. Verifies the
    rehydrate-before-fallback contract."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "stdin", None)

    # Mock the Win32 GetStdHandle to return a fake "valid" handle (any positive int).
    # Use __getattr__ dispatch to avoid ruff N802 (non-lowercase method name).
    class FakeKernel32:
        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            if name == "GetStdHandle":
                return lambda which: 42  # any non-zero non-(-1) value
            raise AttributeError(name)

    fake_windll = type("FakeWindll", (), {"kernel32": FakeKernel32()})()
    monkeypatch.setattr("ctypes.windll", fake_windll, raising=False)

    fake_streams = [object(), object(), object()]
    fake_idx = [0]

    def fake_open_osfhandle(handle: int, flags: int) -> int:
        return 100 + handle  # fake fd

    def fake_fdopen(fd: int, mode: str, **kwargs: object) -> object:
        stream = fake_streams[fake_idx[0]]
        fake_idx[0] += 1
        return stream

    # _restore_inherited_stdio starts with `import msvcrt` (Windows-only stdlib);
    # inject a fake so the test runs cross-platform (sys.modules-injection pattern).
    fake_msvcrt = types.ModuleType("msvcrt")
    fake_msvcrt.open_osfhandle = fake_open_osfhandle  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr("os.fdopen", fake_fdopen)

    _windows_console._restore_inherited_stdio()
    assert sys.stdin is fake_streams[0]
    assert sys.stdout is fake_streams[1]
    assert sys.stderr is fake_streams[2]


def test_restore_inherited_stdio_skips_invalid_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If GetStdHandle returns 0 or INVALID_HANDLE_VALUE (-1), the helper
    should leave sys.<stream> unchanged (still None) — caller's CONOUT$
    fallback fills it."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "stdin", None)

    class FakeKernel32:
        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            if name == "GetStdHandle":
                return lambda which: -1  # INVALID_HANDLE_VALUE
            raise AttributeError(name)

    fake_windll = type("FakeWindll", (), {"kernel32": FakeKernel32()})()
    monkeypatch.setattr("ctypes.windll", fake_windll, raising=False)

    # _restore_inherited_stdio starts with `import msvcrt` (Windows-only stdlib);
    # inject a fake so the test runs cross-platform (sys.modules-injection pattern).
    fake_msvcrt = types.ModuleType("msvcrt")
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)

    _windows_console._restore_inherited_stdio()
    assert sys.stdout is None
    assert sys.stderr is None
    assert sys.stdin is None

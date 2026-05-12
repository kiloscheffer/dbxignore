"""Tests for src/dbxignore/_windows_console.py.

The orchestrator (`early_init`) is tested cross-platform via mocks of
the helpers. The ctypes helpers are tested Windows-only via smoke
tests that gate on `sys.platform == "win32"`.
"""

from __future__ import annotations

import sys

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
    monkeypatch.setattr(_windows_console, "_attach_parent_console", lambda: False)
    box_calls: list[str] = []
    monkeypatch.setattr(
        _windows_console,
        "_show_help_message_box",
        lambda: box_calls.append("shown"),
    )
    _windows_console.early_init()
    assert box_calls == []


def test_redirect_preserves_valid_stdout_and_reopens_missing_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed case: stdout valid (already wired up), stderr None.
    Must NOT overwrite stdout; MUST reopen stderr against CONOUT$.
    Verifies the per-stream preservation contract from the spec."""
    monkeypatch.setattr(sys, "platform", "win32")
    fake_stdout = sys.stdout  # already valid under pytest
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "stdin", None)

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

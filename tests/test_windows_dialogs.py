"""Tests for src/dbxignore/_windows_dialogs.py.

All tests run cross-platform via sys.modules injection of a fake ctypes.windll
(same pattern as test_windows_console.py's msvcrt injection). The
platform-specific predicates are exercised by monkeypatching sys.platform and
ctypes.windll.
"""

from __future__ import annotations

import ctypes
import sys
import types

import pytest  # noqa: TC002

from dbxignore import _windows_dialogs

# ---------------------------------------------------------------------------
# should_use_gui_dialogs
# ---------------------------------------------------------------------------


def test_should_use_gui_dialogs_when_no_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """No console window attached (GetConsoleWindow returns 0) → True on Windows."""
    monkeypatch.setattr(sys, "platform", "win32")

    class FakeKernel32:
        @staticmethod
        def GetConsoleWindow() -> int:  # noqa: N802
            return 0

    class FakeWindll:
        kernel32 = FakeKernel32()

    monkeypatch.setattr(ctypes, "windll", FakeWindll(), raising=False)
    assert _windows_dialogs.should_use_gui_dialogs() is True


def test_should_use_gui_dialogs_when_console_attached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Console window attached (non-zero handle) → False on Windows."""
    monkeypatch.setattr(sys, "platform", "win32")

    class FakeKernel32:
        @staticmethod
        def GetConsoleWindow() -> int:  # noqa: N802
            return 0x12345

    class FakeWindll:
        kernel32 = FakeKernel32()

    monkeypatch.setattr(ctypes, "windll", FakeWindll(), raising=False)
    assert _windows_dialogs.should_use_gui_dialogs() is False


def test_should_use_gui_dialogs_returns_false_on_non_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert _windows_dialogs.should_use_gui_dialogs() is False


# ---------------------------------------------------------------------------
# confirm_destructive — helpers for fake user32
# ---------------------------------------------------------------------------


def _make_fake_windll(messagebox_return: int) -> types.SimpleNamespace:
    """Build a fake ctypes.windll with a user32.MessageBoxW stub."""
    calls: list[tuple[object, str, str, int]] = []

    def fake_messagebox(hwnd: object, text: str, caption: str, utype: int) -> int:
        calls.append((hwnd, text, caption, utype))
        return messagebox_return

    fake_user32 = types.SimpleNamespace(MessageBoxW=fake_messagebox)
    fake_windll = types.SimpleNamespace(user32=fake_user32)
    fake_windll._calls = calls
    return fake_windll


# ---------------------------------------------------------------------------
# confirm_destructive
# ---------------------------------------------------------------------------


def test_confirm_destructive_returns_true_on_idyes(monkeypatch: pytest.MonkeyPatch) -> None:
    """IDYES (6) → True."""
    fake_windll = _make_fake_windll(messagebox_return=6)  # _IDYES
    monkeypatch.setattr("ctypes.windll", fake_windll, raising=False)
    result = _windows_dialogs.confirm_destructive("Mark path?")
    assert result is True


def test_confirm_destructive_returns_false_on_idno(monkeypatch: pytest.MonkeyPatch) -> None:
    """IDNO (7) → False."""
    fake_windll = _make_fake_windll(messagebox_return=7)  # IDNO
    monkeypatch.setattr("ctypes.windll", fake_windll, raising=False)
    result = _windows_dialogs.confirm_destructive("Mark path?")
    assert result is False


def test_confirm_destructive_passes_yesno_and_warning_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MessageBoxW must be called with MB_YESNO | MB_ICONWARNING (0x34)."""
    fake_windll = _make_fake_windll(messagebox_return=6)
    monkeypatch.setattr("ctypes.windll", fake_windll, raising=False)
    _windows_dialogs.confirm_destructive("msg", title="T")
    calls = fake_windll._calls
    assert len(calls) == 1
    hwnd, text, caption, utype = calls[0]
    assert hwnd is None
    assert text == "msg"
    assert caption == "T"
    # MB_YESNO (0x4) | MB_ICONWARNING (0x30) == 0x34
    assert utype == (0x00000004 | 0x00000030)


def test_confirm_destructive_uses_default_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no title is supplied, the default 'dbxignore' title is used."""
    fake_windll = _make_fake_windll(messagebox_return=6)
    monkeypatch.setattr("ctypes.windll", fake_windll, raising=False)
    _windows_dialogs.confirm_destructive("msg")
    calls = fake_windll._calls
    assert calls[0][2] == "dbxignore"


def test_confirm_destructive_returns_false_on_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    """If MessageBoxW raises OSError (unusual session), returns False defensively."""

    def _raise_oserror(*args: object, **kwargs: object) -> int:
        raise OSError("no window station")

    fake_user32 = types.SimpleNamespace(MessageBoxW=_raise_oserror)
    fake_windll = types.SimpleNamespace(user32=fake_user32)
    monkeypatch.setattr("ctypes.windll", fake_windll, raising=False)
    result = _windows_dialogs.confirm_destructive("msg")
    assert result is False


def test_confirm_destructive_returns_false_on_attributeerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AttributeError (e.g. ctypes.windll absent on non-Windows) → False."""
    monkeypatch.setattr("ctypes.windll", None, raising=False)
    result = _windows_dialogs.confirm_destructive("msg")
    assert result is False


# ---------------------------------------------------------------------------
# show_error
# ---------------------------------------------------------------------------


def test_show_error_calls_messagebox_with_ok_and_error_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MessageBoxW must be called with MB_OK | MB_ICONERROR (0x10)."""
    fake_windll = _make_fake_windll(messagebox_return=1)  # IDOK
    monkeypatch.setattr("ctypes.windll", fake_windll, raising=False)
    _windows_dialogs.show_error("something failed", title="dbxignore")
    calls = fake_windll._calls
    assert len(calls) == 1
    hwnd, text, caption, utype = calls[0]
    assert hwnd is None
    assert text == "something failed"
    assert caption == "dbxignore"
    # MB_OK (0x0) | MB_ICONERROR (0x10) == 0x10
    assert utype == (0x00000000 | 0x00000010)


def test_show_error_uses_default_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no title is supplied, the default 'dbxignore' title is used."""
    fake_windll = _make_fake_windll(messagebox_return=1)
    monkeypatch.setattr("ctypes.windll", fake_windll, raising=False)
    _windows_dialogs.show_error("err")
    calls = fake_windll._calls
    assert calls[0][2] == "dbxignore"


def test_show_error_swallows_oserror_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    """OSError in MessageBoxW must not propagate — show_error returns None."""

    def _raise_oserror(*args: object, **kwargs: object) -> int:
        raise OSError("no window station")

    fake_user32 = types.SimpleNamespace(MessageBoxW=_raise_oserror)
    fake_windll = types.SimpleNamespace(user32=fake_user32)
    monkeypatch.setattr("ctypes.windll", fake_windll, raising=False)
    # Must not raise.
    _windows_dialogs.show_error("msg")


def test_show_error_swallows_attributeerror_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    """AttributeError (ctypes.windll absent) must not propagate."""
    monkeypatch.setattr("ctypes.windll", None, raising=False)
    _windows_dialogs.show_error("msg")  # must not raise

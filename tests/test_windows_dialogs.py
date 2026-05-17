"""Tests for src/dbxignore/_windows_dialogs.py.

All tests run cross-platform via sys.modules injection of a fake ctypes.windll
(see the FakeKernel32 / FakeUser32 helpers below). The platform-specific
predicates are exercised by monkeypatching sys.platform and ctypes.windll.
"""

from __future__ import annotations

import ctypes
import sys
import types

import pytest  # noqa: TC002

from dbxignore import _windows_dialogs

# ---------------------------------------------------------------------------
# should_use_gui_dialogs — fake-kernel32 factory + tests
# ---------------------------------------------------------------------------


def _make_fake_kernel32_windll(
    getconsolewindow_result: int | type[BaseException] | BaseException,
) -> object:
    """Build a fake ctypes.windll with a kernel32.GetConsoleWindow stub.

    `getconsolewindow_result` is either an int returned by the stub or an
    exception instance/class raised by it.
    """

    class FakeKernel32:
        @staticmethod
        def GetConsoleWindow() -> int:  # noqa: N802
            if isinstance(getconsolewindow_result, BaseException) or (
                isinstance(getconsolewindow_result, type)
                and issubclass(getconsolewindow_result, BaseException)
            ):
                raise getconsolewindow_result
            return getconsolewindow_result

    return types.SimpleNamespace(kernel32=FakeKernel32())


def test_should_use_gui_dialogs_when_no_console_and_no_stdio_backing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No console window AND stdio has no real fileno → True (dbxignorew.exe
    GUI helper invoked by Task Scheduler / Explorer / double-click)."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", _make_fake_kernel32_windll(0), raising=False)

    # PyInstaller noconsole bootloader stub: writable but no fileno.
    class NoFilenoStream:
        def fileno(self) -> int:
            raise OSError("no fileno on noconsole stub")

    monkeypatch.setattr(sys, "stdout", NoFilenoStream())
    assert _windows_dialogs.should_use_gui_dialogs() is True


def test_should_use_gui_dialogs_false_when_no_console_but_stdio_has_fileno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No console window BUT stdio has a real fileno (CREATE_NO_WINDOW
    invocation from automation) → False. Output should flow through the
    inherited pipe, not a MessageBox the parent script can't see.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", _make_fake_kernel32_windll(0), raising=False)

    class RealFilenoStream:
        def fileno(self) -> int:
            return 1

    monkeypatch.setattr(sys, "stdout", RealFilenoStream())
    assert _windows_dialogs.should_use_gui_dialogs() is False


def test_should_use_gui_dialogs_true_when_no_console_and_stdout_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No console window AND sys.stdout is None → True."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", _make_fake_kernel32_windll(0), raising=False)
    monkeypatch.setattr(sys, "stdout", None)
    assert _windows_dialogs.should_use_gui_dialogs() is True


def test_should_use_gui_dialogs_when_console_attached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Console window attached (non-zero handle) → False on Windows."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", _make_fake_kernel32_windll(0x12345), raising=False)
    assert _windows_dialogs.should_use_gui_dialogs() is False


def test_should_use_gui_dialogs_returns_false_on_non_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert _windows_dialogs.should_use_gui_dialogs() is False


def test_should_use_gui_dialogs_returns_true_on_getconsolewindow_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OSError from GetConsoleWindow() falls through to True (conservative: treat as GUI).

    This is the safety-sensitive branch: a probe failure on an unusual
    Windows session state must NOT cause destructive operations to silently
    auto-confirm. Returning True ensures the MessageBox confirmation fires.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        ctypes, "windll", _make_fake_kernel32_windll(OSError("no window station")), raising=False
    )
    assert _windows_dialogs.should_use_gui_dialogs() is True


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


# ---------------------------------------------------------------------------
# show_info
# ---------------------------------------------------------------------------


def test_show_info_calls_messagebox_with_ok_and_information_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MessageBoxW must be called with MB_OK | MB_ICONINFORMATION (0x40)."""
    fake_windll = _make_fake_windll(messagebox_return=1)  # IDOK
    monkeypatch.setattr("ctypes.windll", fake_windll, raising=False)
    _windows_dialogs.show_info("this is a cli tool", title="dbxignore")
    calls = fake_windll._calls
    assert len(calls) == 1
    hwnd, text, caption, utype = calls[0]
    assert hwnd is None
    assert text == "this is a cli tool"
    assert caption == "dbxignore"
    # MB_OK (0x0) | MB_ICONINFORMATION (0x40) == 0x40
    assert utype == (0x00000000 | 0x00000040)


def test_show_info_uses_default_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no title is supplied, the default 'dbxignore' title is used."""
    fake_windll = _make_fake_windll(messagebox_return=1)
    monkeypatch.setattr("ctypes.windll", fake_windll, raising=False)
    _windows_dialogs.show_info("msg")
    calls = fake_windll._calls
    assert calls[0][2] == "dbxignore"


def test_show_info_swallows_oserror_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    """OSError in MessageBoxW must not propagate — show_info returns None."""

    def _raise_oserror(*args: object, **kwargs: object) -> int:
        raise OSError("no window station")

    fake_user32 = types.SimpleNamespace(MessageBoxW=_raise_oserror)
    fake_windll = types.SimpleNamespace(user32=fake_user32)
    monkeypatch.setattr("ctypes.windll", fake_windll, raising=False)
    _windows_dialogs.show_info("msg")  # must not raise


def test_show_info_swallows_attributeerror_silently(monkeypatch: pytest.MonkeyPatch) -> None:
    """AttributeError (ctypes.windll absent) must not propagate."""
    monkeypatch.setattr("ctypes.windll", None, raising=False)
    _windows_dialogs.show_info("msg")  # must not raise


# ---------------------------------------------------------------------------
# _handle_explorer_double_click (via __main__)
# ---------------------------------------------------------------------------


def test_handle_explorer_double_click_no_op_when_argv_has_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """argv with 2+ elements → returns without calling show_info or sys.exit."""
    from dbxignore import __main__

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["dbxignorew.exe", "status"])
    show_info_calls: list[str] = []
    monkeypatch.setattr(
        _windows_dialogs, "show_info", lambda msg, **kw: show_info_calls.append(msg)
    )

    __main__._handle_explorer_double_click()

    assert show_info_calls == []


def test_handle_explorer_double_click_no_op_when_console_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """argv empty + console attached (should_use_gui_dialogs → False) → no dialog."""
    from dbxignore import __main__

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["dbxignorew.exe"])
    monkeypatch.setattr(_windows_dialogs, "should_use_gui_dialogs", lambda: False)
    show_info_calls: list[str] = []
    monkeypatch.setattr(
        _windows_dialogs, "show_info", lambda msg, **kw: show_info_calls.append(msg)
    )

    __main__._handle_explorer_double_click()

    assert show_info_calls == []


def test_handle_explorer_double_click_shows_info_and_exits_when_no_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """argv empty + no console (should_use_gui_dialogs → True) → show_info + sys.exit(0)."""
    from dbxignore import __main__

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["dbxignorew.exe"])
    monkeypatch.setattr(_windows_dialogs, "should_use_gui_dialogs", lambda: True)
    show_info_calls: list[str] = []
    monkeypatch.setattr(
        _windows_dialogs, "show_info", lambda msg, **kw: show_info_calls.append(msg)
    )

    with pytest.raises(SystemExit) as exc_info:
        __main__._handle_explorer_double_click()

    assert exc_info.value.code == 0
    assert len(show_info_calls) == 1
    assert "dbxignore --help" in show_info_calls[0]

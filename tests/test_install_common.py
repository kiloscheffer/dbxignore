"""Unit tests for the shared install detect_invocation helper."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def test_detect_invocation_frozen_returns_executable_with_daemon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Frozen (PyInstaller): always return (sys.executable, "daemon") unconditionally.

    After #30 unification there is no separate dbxignored binary — the single
    dbxignore[.exe] binary handles all subcommands. The pre-#30 three-step
    "find dbxignored shim" logic is gone.
    """
    cli_name = "dbxignore.exe" if sys.platform == "win32" else "dbxignore"
    cli_exe = tmp_path / cli_name
    cli_exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(cli_exe))
    from dbxignore.install import _common

    exe, args = _common.detect_invocation()
    assert exe == cli_exe
    assert args == "daemon"


def test_detect_invocation_falls_back_to_python_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    # Force the Linux/macOS branch — the Windows branch short-circuits to
    # pythonw.exe before reaching the shutil.which lookup.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    from dbxignore.install import _common

    exe, args = _common.detect_invocation()
    assert exe == Path("/usr/bin/python3")
    assert args == "-m dbxignore daemon"


def test_detect_invocation_uses_path_shim_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")

    def fake_which(name: str) -> str | None:
        if name == "dbxignore":
            return "/home/u/.local/bin/dbxignore"
        return None

    monkeypatch.setattr("shutil.which", fake_which)
    from dbxignore.install import _common

    exe, args = _common.detect_invocation()
    assert exe == Path("/home/u/.local/bin/dbxignore")
    assert args == "daemon"


def test_detect_invocation_returns_pythonw_on_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Windows non-frozen, pythonw.exe present: select ``pythonw.exe`` next to ``sys.executable``.

    Task Scheduler launches at logon and the daemon must not flash a
    console window or orphan a ``conhost.exe`` — ``pythonw.exe`` (the
    windowless Python interpreter) avoids both. The ``shutil.which("dbxignored")``
    PATH-shim lookup that the Linux/macOS branch uses is intentionally
    skipped on Windows; any shim would still launch ``python.exe`` with
    a console attached.

    Item #50 — collapsed `windows_task.detect_invocation` into a re-export
    of `_common.detect_invocation` once the Windows non-frozen branch was
    folded in here.

    Item #100 — the Windows branch now checks ``pythonw.exe`` actually
    exists before returning it. This test pins the happy path: both
    ``python.exe`` and ``pythonw.exe`` present, pythonw selected.

    Uses ``tmp_path`` for the executable rather than a hardcoded
    ``C:\\…\\python.exe`` literal: on POSIX hosts ``Path(r"C:\\…")`` parses
    the whole backslash string as a single filename (no path components),
    so ``Path.with_name("pythonw.exe")`` collapses to bare ``pythonw.exe``
    and the assertion fails on the cross-platform CI legs. Backslash
    handling is platform-specific to ``pathlib.PureWindowsPath``, which
    isn't what ``Path`` resolves to on POSIX. Using ``tmp_path / "..."``
    sidesteps the asymmetry — the parent-directory-vs-filename split is
    identical on every platform.
    """
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    python_exe = tmp_path / "Scripts" / "python.exe"
    python_exe.parent.mkdir()
    python_exe.write_text("")
    pythonw_exe = tmp_path / "Scripts" / "pythonw.exe"
    pythonw_exe.write_text("")
    monkeypatch.setattr(sys, "executable", str(python_exe))
    from dbxignore.install import _common

    exe, args = _common.detect_invocation()
    assert exe == pythonw_exe
    assert args == "-m dbxignore daemon"


def test_detect_invocation_falls_back_to_python_exe_on_windows_when_pythonw_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Windows non-frozen, pythonw.exe missing: fall back to python.exe with a WARNING.

    Item #100 — Microsoft Store Python, embedded interpreters, and pruned
    CPython installs may ship only ``python.exe``. Without a fallback, the
    install previously wrote a Task Scheduler entry pointing at a
    nonexistent ``pythonw.exe`` and the daemon silently never started at
    logon. New behavior: return ``sys.executable`` (``python.exe``) with
    a WARNING log explaining the console-flash trade-off.
    """
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    python_exe = tmp_path / "Scripts" / "python.exe"
    python_exe.parent.mkdir()
    python_exe.write_text("")
    # NB: pythonw.exe intentionally NOT created.
    monkeypatch.setattr(sys, "executable", str(python_exe))
    from dbxignore.install import _common

    with caplog.at_level("WARNING", logger="dbxignore.install._common"):
        exe, args = _common.detect_invocation()

    assert exe == python_exe
    assert args == "-m dbxignore daemon"
    assert any("pythonw.exe not found" in rec.message for rec in caplog.records)


def test_detect_cli_invocation_frozen_uses_sibling_exe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Frozen PyInstaller install: dbxignore.exe sibling is the registered target."""
    daemon_name = "dbxignored.exe" if sys.platform == "win32" else "dbxignored"
    daemon_exe = tmp_path / daemon_name
    daemon_exe.write_text("")
    cli_name = "dbxignore.exe" if sys.platform == "win32" else "dbxignore"
    cli_exe = tmp_path / cli_name
    cli_exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(daemon_exe))
    from dbxignore.install import _common

    result = _common.detect_cli_invocation()
    assert result == f'"{cli_exe}"'


def test_detect_cli_invocation_uses_shutil_which_shim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-frozen install: `dbxignore` PATH shim is the registered target."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    shim_path = "C:\\Users\\u\\.local\\bin\\dbxignore.exe"

    def fake_which(name: str) -> str | None:
        if name == "dbxignore":
            return shim_path
        return None

    monkeypatch.setattr("shutil.which", fake_which)
    from dbxignore.install import _common

    result = _common.detect_cli_invocation()
    assert result == f'"{shim_path}"'


def test_detect_cli_invocation_falls_back_to_python_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No frozen install, no `dbxignore` on PATH: use `<sys.executable> -m dbxignore`."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    python_exe = tmp_path / "Scripts" / "python.exe"
    python_exe.parent.mkdir()
    python_exe.write_text("")
    monkeypatch.setattr(sys, "executable", str(python_exe))
    monkeypatch.setattr("shutil.which", lambda _name: None)
    from dbxignore.install import _common

    result = _common.detect_cli_invocation()
    assert result == f'"{python_exe}" -m dbxignore'


def test_detect_cli_invocation_raises_when_no_python_and_no_sys_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive guard: empty sys.executable + no shim must raise, not return Path('.')."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", "")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    from dbxignore.install import _common

    with pytest.raises(RuntimeError, match="dbxignore not on PATH"):
        _common.detect_cli_invocation()


def test_detect_invocation_raises_when_sys_executable_empty_and_no_python3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In misconfigured embedded interpreters, sys.executable may be '' or None.

    If neither it nor python3 on PATH is discoverable, detect_invocation raises
    RuntimeError rather than writing a broken executable like Path('.') into the
    systemd unit / launchd plist.
    """
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "executable", "")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    from dbxignore.install import _common

    with pytest.raises(RuntimeError, match="Cannot determine Python interpreter"):
        _common.detect_invocation()


def test_detect_invocation_falls_back_to_python3_when_sys_executable_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When sys.executable is empty but python3 is on PATH, use python3 with -m dbxignore daemon."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "executable", "")

    def fake_which(name: str) -> str | None:
        if name == "python3":
            return "/usr/bin/python3"
        return None

    monkeypatch.setattr("shutil.which", fake_which)
    from dbxignore.install import _common

    exe, args = _common.detect_invocation()
    assert exe == Path("/usr/bin/python3")
    assert args == "-m dbxignore daemon"

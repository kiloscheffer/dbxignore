import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import psutil  # type: ignore[import-untyped, unused-ignore]
import pytest

from dbxignore import state


def test_roundtrip(tmp_path: Path) -> None:
    s = state.State(
        daemon_pid=1234,
        daemon_started=datetime(2026, 4, 20, 9, 0, tzinfo=UTC),
        daemon_create_time=1745140800.5,
        last_sweep=datetime(2026, 4, 20, 10, 0, tzinfo=UTC),
        last_sweep_duration_s=1.5,
        last_sweep_marked=5,
        last_sweep_cleared=2,
        last_sweep_errors=0,
        last_error=None,
        watched_roots=[Path(r"C:\Dropbox")],
    )
    path = tmp_path / "state.json"
    state.write(s, path)

    loaded = state.read(path)
    assert loaded == s


def test_decode_tolerates_missing_daemon_create_time(tmp_path: Path) -> None:
    """state.json files written before #79 lack daemon_create_time. Decode
    must default to None rather than KeyError-ing into the corrupt-state arm
    (which would silently drop the file's other fields). Backwards-compat
    with the v0.4.x state schema."""
    p = tmp_path / "state.json"
    p.write_text(
        '{"schema": 1, "daemon_pid": 4321, "daemon_started": null, '
        '"last_sweep": null, "last_sweep_duration_s": 0.0, '
        '"last_sweep_marked": 0, "last_sweep_cleared": 0, "last_sweep_errors": 0, '
        '"last_error": null, "watched_roots": []}',
        encoding="utf-8",
    )
    loaded = state.read(p)
    assert loaded is not None
    assert loaded.daemon_pid == 4321
    assert loaded.daemon_create_time is None


def test_read_missing_returns_none(tmp_path: Path) -> None:
    assert state.read(tmp_path / "does_not_exist.json") is None


def test_read_corrupt_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    p.write_text("not json", encoding="utf-8")
    assert state.read(p) is None


def test_write_leaves_no_tmp_file(tmp_path: Path) -> None:
    """Atomic write: state.json.tmp must be renamed away on success."""
    p = tmp_path / "state.json"
    state.write(state.State(daemon_pid=1), p)
    assert p.exists()
    assert not (tmp_path / "state.json.tmp").exists()


def test_write_overwrites_stale_tmp(tmp_path: Path) -> None:
    """A leaked tmp from a prior crash must not break the next write."""
    p = tmp_path / "state.json"
    (tmp_path / "state.json.tmp").write_text("garbage from crash", encoding="utf-8")
    state.write(state.State(daemon_pid=2), p)
    s = state.read(p)
    assert s is not None
    assert s.daemon_pid == 2
    assert not (tmp_path / "state.json.tmp").exists()


def test_write_parse_back_rejects_invalid_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A future serializer regression that produces malformed JSON must not
    reach state.json — parse-back validation should raise and unlink the tmp,
    leaving any prior state.json untouched."""
    p = tmp_path / "state.json"
    state.write(state.State(daemon_pid=1), p)
    prior = p.read_text(encoding="utf-8")

    monkeypatch.setattr(json, "dumps", lambda *a, **kw: "{not valid json")
    with pytest.raises(json.JSONDecodeError):
        state.write(state.State(daemon_pid=2), p)

    assert p.read_text(encoding="utf-8") == prior
    assert not (tmp_path / "state.json.tmp").exists()


def test_read_shape_mismatch_missing_subkey_returns_none(tmp_path: Path) -> None:
    """Valid JSON but last_error missing a required sub-key (KeyError arm)."""
    p = tmp_path / "state.json"
    p.write_text(
        '{"last_error": {"path": "/x"}}',  # missing "time" and "message"
        encoding="utf-8",
    )
    assert state.read(p) is None


def test_read_shape_mismatch_wrong_type_returns_none(tmp_path: Path) -> None:
    """Valid JSON but last_error is a string, not a dict (TypeError arm)."""
    p = tmp_path / "state.json"
    p.write_text('{"last_error": "oops"}', encoding="utf-8")
    assert state.read(p) is None


def test_read_shape_mismatch_bad_datetime_returns_none(tmp_path: Path) -> None:
    """Valid JSON but a stored datetime fails to parse (ValueError arm)."""
    p = tmp_path / "state.json"
    p.write_text('{"daemon_started": "not-a-datetime"}', encoding="utf-8")
    assert state.read(p) is None


def test_read_string_daemon_create_time_returns_none(tmp_path: Path) -> None:
    """A non-numeric daemon_create_time (e.g. hand-edited or shape-
    mismatched state file) must fail decode and route through the
    corrupt-state fallback. Without strict validation at decode time,
    the bad value would propagate to ``is_daemon_alive``'s
    ``abs(live_create_time - create_time)`` arithmetic and raise
    TypeError, breaking ``status`` / ``clear`` / the daemon's legacy-
    startup guard."""
    p = tmp_path / "state.json"
    p.write_text(
        '{"daemon_pid": 1234, "daemon_create_time": "not-a-number"}',
        encoding="utf-8",
    )
    assert state.read(p) is None


def test_read_bool_daemon_create_time_returns_none(tmp_path: Path) -> None:
    """``isinstance(True, int)`` is True in Python (bool subclasses int),
    so a naive ``isinstance(ct, (int, float))`` check would accept a bool
    create_time. Explicit bool exclusion keeps the validation tight."""
    p = tmp_path / "state.json"
    p.write_text(
        '{"daemon_pid": 1234, "daemon_create_time": true}',
        encoding="utf-8",
    )
    assert state.read(p) is None


# ---- is_daemon_alive (followup item 59) -------------------------------------


def test_is_daemon_alive_none_pid_returns_false() -> None:
    """No recorded pid → not alive (no state.json or pid never written)."""
    assert state.is_daemon_alive(None) is False


def test_is_daemon_alive_dead_pid_returns_false(fake_psutil_process) -> None:
    """psutil reports the PID doesn't exist → False, no Process construction."""
    fake_psutil_process(pid_exists=False)
    assert state.is_daemon_alive(99999) is False


def test_is_daemon_alive_recycled_pid_returns_false_for_unrelated_process(
    fake_psutil_process,
) -> None:
    """PID is alive but the process at that PID isn't a dbxignore daemon —
    the PID was reused by something else (firefox, svchost, etc.). The
    bare-existence check would say "alive"; the process-name guard catches
    the false positive (followup item 59)."""
    fake_psutil_process(name="firefox.exe")
    assert state.is_daemon_alive(12345) is False


def test_is_daemon_alive_python_process_returns_true(fake_psutil_process) -> None:
    """Source-run daemon: process is python (or python3, pythonw.exe, etc.).
    Match is case-insensitive and substring-based so all common variants pass."""
    fake_psutil_process(name="Python3.11")
    assert state.is_daemon_alive(12345) is True


def test_is_daemon_alive_dbxignored_process_returns_true(fake_psutil_process) -> None:
    """Frozen PyInstaller install: process is dbxignored.exe (or dbxignored
    on macOS/Linux). The 'd' suffix distinguishes the daemon binary from
    the dbxignore CLI binary."""
    fake_psutil_process(name="dbxignored.exe")
    assert state.is_daemon_alive(12345) is True


def test_is_daemon_alive_create_time_match_returns_true(fake_psutil_process) -> None:
    """Both pid_exists AND create_time matching → True. The strict-mode
    contract that backlog item #79 motivates: a recycled PID claimed by an
    unrelated python process would have a different create_time, so this
    branch shouldn't fire for the false-positive case."""
    fake_psutil_process(name="python.exe", create_time=1700000000.5)
    assert state.is_daemon_alive(12345, create_time=1700000000.5) is True


def test_is_daemon_alive_create_time_mismatch_returns_false(fake_psutil_process) -> None:
    """pid_exists True but create_time differs → False. This is the
    backlog item #79 fix: catches PID-reuse where the recycled process
    happens to have a name-substring match (another python instance).
    Without create_time disambiguation, the prior is_daemon_alive would
    return True and incorrectly block destructive verbs."""
    fake_psutil_process(name="python.exe", create_time=1700001000.0)  # mismatched
    assert state.is_daemon_alive(12345, create_time=1700000000.5) is False


def test_is_daemon_alive_create_time_none_falls_back_to_substring(fake_psutil_process) -> None:
    """When create_time is None (state.json predates #79 OR the daemon
    hasn't yet written its create_time), fall back to the substring-name
    check. Backwards-compat with v0.4.x state.json files. The fixture's
    default ``create_time=None`` makes ``proc.create_time()`` raise on
    call, so this also pins that the None-path must not invoke
    create_time()."""
    fake_psutil_process(name="python.exe")
    assert state.is_daemon_alive(12345, create_time=None) is True


def test_is_daemon_alive_psutil_error_returns_false(fake_psutil_process) -> None:
    """psutil.Process(pid).name() raises (NoSuchProcess if the PID died
    between pid_exists and the name call) → False. Race-window safety net."""
    fake_psutil_process(name_raises=psutil.NoSuchProcess(12345))
    assert state.is_daemon_alive(12345) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path layout")
def test_default_path_windows_under_localappdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert state.default_path() == tmp_path / "dbxignore" / "state.json"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux path layout")
def test_default_path_linux_uses_xdg_state_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert state.default_path() == tmp_path / "state" / "dbxignore" / "state.json"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux path layout")
def test_default_path_linux_falls_back_to_local_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert state.default_path() == tmp_path / ".local" / "state" / "dbxignore" / "state.json"

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import psutil  # type: ignore[import-untyped, unused-ignore]
import pytest

from dbxignore import state
from tests.conftest import FakePsutilProcess


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
        last_sweep_conflicts=3,
        last_error=None,
        watched_roots=[Path(r"C:\Dropbox")],
    )
    path = tmp_path / "state.json"
    state.write(s, path)

    loaded = state.read(path)
    assert loaded == s


def test_decode_tolerates_missing_last_sweep_conflicts(tmp_path: Path) -> None:
    """state.json files written before #68 lack last_sweep_conflicts. Decode
    must default to 0 rather than KeyError-ing into the corrupt-state arm.
    Backwards-compat with the schema as it shipped pre-#68."""
    p = tmp_path / "state.json"
    p.write_text(
        '{"schema": 1, "daemon_pid": 4321, "daemon_create_time": null, '
        '"daemon_started": null, "last_sweep": null, "last_sweep_duration_s": 0.0, '
        '"last_sweep_marked": 0, "last_sweep_cleared": 0, "last_sweep_errors": 0, '
        '"last_error": null, "watched_roots": []}',
        encoding="utf-8",
    )
    loaded = state.read(p)
    assert loaded is not None
    assert loaded.last_sweep_conflicts == 0


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


def test_read_unreadable_returns_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A locked / permission-denied / cloud-placeholder state.json must degrade
    to ``None`` with a WARNING, matching the corrupt-state contract. Without
    catching OSError, ``state.read()`` would propagate the error and crash
    every CLI verb that consults state (``status``, ``clear``'s daemon-alive
    guard, daemon legacy-state migration). Backlog item #97."""
    p = tmp_path / "state.json"
    p.write_text("{}", encoding="utf-8")

    def boom(self: Path, *args: object, **kwargs: object) -> str:
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "read_text", boom)

    with caplog.at_level("WARNING", logger="dbxignore.state"):
        assert state.read(p) is None
    assert any("unreadable" in rec.message for rec in caplog.records)


def test_read_missing_does_not_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Truly-absent state.json (the first-run common case) must not log a
    WARNING. Regression guard against accidentally promoting missing-file
    to the new ``OSError`` warning arm added in item #97."""
    with caplog.at_level("WARNING", logger="dbxignore.state"):
        assert state.read(tmp_path / "does_not_exist.json") is None
    assert not caplog.records


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


def test_is_daemon_alive_dead_pid_returns_false(fake_psutil_process: FakePsutilProcess) -> None:
    """psutil reports the PID doesn't exist → False, no Process construction."""
    fake_psutil_process(pid_exists=False)
    assert state.is_daemon_alive(99999) is False


def test_is_daemon_alive_recycled_pid_returns_false_for_unrelated_process(
    fake_psutil_process: FakePsutilProcess,
) -> None:
    """PID is alive but the process at that PID isn't a dbxignore daemon —
    the PID was reused by something else (firefox, svchost, etc.). The
    bare-existence check would say "alive"; the process-name guard catches
    the false positive (followup item 59)."""
    fake_psutil_process(name="firefox.exe")
    assert state.is_daemon_alive(12345) is False


def test_is_daemon_alive_python_process_returns_true(
    fake_psutil_process: FakePsutilProcess,
) -> None:
    """Source-run daemon: process is python (or python3, pythonw.exe, etc.).
    Match is case-insensitive and substring-based so all common variants pass."""
    fake_psutil_process(name="Python3.11")
    assert state.is_daemon_alive(12345) is True


def test_is_daemon_alive_dbxignored_process_returns_false(
    fake_psutil_process: FakePsutilProcess,
) -> None:
    """Pre-#30 frozen install: process is dbxignored.exe. After #30
    unification the guard no longer accepts this name — the daemon binary
    was renamed to dbxignore.exe and 'dbxignored' was dropped from the
    guard tuple."""
    fake_psutil_process(name="dbxignored.exe")
    assert state.is_daemon_alive(12345) is False


def test_is_daemon_alive_recognizes_dbxignore_process_name(
    fake_psutil_process: FakePsutilProcess,
) -> None:
    """After #30 unification, a frozen daemon is named `dbxignore.exe`,
    not `dbxignored.exe`. The process-name guard tuple must accept it."""
    fake_psutil_process(name="dbxignore.exe")
    assert state.is_daemon_alive(12345) is True


def test_is_daemon_alive_no_longer_recognizes_dbxignored(
    fake_psutil_process: FakePsutilProcess,
) -> None:
    """The pre-#30 `dbxignored` name is intentionally dropped from the
    guard. A surviving v0.5.x daemon process surfaces as not-alive,
    prompting the migration. Surfacing stale state is the desired
    behavior."""
    fake_psutil_process(name="dbxignored.exe")
    assert state.is_daemon_alive(12345) is False


def test_is_daemon_alive_accepts_dbxignorew_process_name(
    fake_psutil_process: FakePsutilProcess,
) -> None:
    """After dual-binary split, the daemon process name on Windows is
    typically dbxignorew.exe (launched by Task Scheduler with the GUI
    helper). is_daemon_alive must recognize it as a valid dbxignore daemon
    so destructive CLI verbs' daemon-alive guard works correctly.
    """
    fake_psutil_process(name="dbxignorew.exe")
    assert state.is_daemon_alive(12345) is True


def test_is_daemon_alive_accepts_dbxignorew_without_suffix(
    fake_psutil_process: FakePsutilProcess,
) -> None:
    """proc.name() may return either "dbxignorew" or "dbxignorew.exe"
    depending on psutil's Windows backend version — both must pass.
    """
    fake_psutil_process(name="dbxignorew")
    assert state.is_daemon_alive(12345) is True


def test_is_daemon_alive_create_time_match_returns_true(
    fake_psutil_process: FakePsutilProcess,
) -> None:
    """Both pid_exists AND create_time matching → True. The strict-mode
    contract that backlog item #79 motivates: a recycled PID claimed by an
    unrelated python process would have a different create_time, so this
    branch shouldn't fire for the false-positive case."""
    fake_psutil_process(name="python.exe", create_time=1700000000.5)
    assert state.is_daemon_alive(12345, create_time=1700000000.5) is True


def test_is_daemon_alive_create_time_mismatch_returns_false(
    fake_psutil_process: FakePsutilProcess,
) -> None:
    """pid_exists True but create_time differs → False. This is the
    backlog item #79 fix: catches PID-reuse where the recycled process
    happens to have a name-substring match (another python instance).
    Without create_time disambiguation, the prior is_daemon_alive would
    return True and incorrectly block destructive verbs."""
    fake_psutil_process(name="python.exe", create_time=1700001000.0)  # mismatched
    assert state.is_daemon_alive(12345, create_time=1700000000.5) is False


def test_is_daemon_alive_create_time_none_falls_back_to_substring(
    fake_psutil_process: FakePsutilProcess,
) -> None:
    """When create_time is None (state.json predates #79 OR the daemon
    hasn't yet written its create_time), fall back to the substring-name
    check. Backwards-compat with v0.4.x state.json files. The fixture's
    default ``create_time=None`` makes ``proc.create_time()`` raise on
    call, so this also pins that the None-path must not invoke
    create_time()."""
    fake_psutil_process(name="python.exe")
    assert state.is_daemon_alive(12345, create_time=None) is True


def test_is_daemon_alive_psutil_error_returns_false(fake_psutil_process: FakePsutilProcess) -> None:
    """psutil.Process(pid).name() raises (NoSuchProcess if the PID died
    between pid_exists and the name call) → False. Race-window safety net."""
    fake_psutil_process(name_raises=psutil.NoSuchProcess(12345))
    assert state.is_daemon_alive(12345) is False


# ---- is_daemon_alive psutil-unavailable fallback (item #118) ---------------


def test_is_daemon_alive_psutil_unavailable_os_kill_succeeds_returns_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """psutil import fails, os.kill bare-existence probe succeeds → True.

    The legacy fallback path for systems without psutil. Real psutil is
    installed in dev/CI, so simulate its absence via `sys.modules[None]`
    (the Python idiom for poisoning a module import).
    """
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr("os.kill", lambda _pid, _sig: None)
    assert state.is_daemon_alive(12345) is True


def test_is_daemon_alive_psutil_unavailable_process_lookup_error_returns_false_silent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """psutil unavailable, os.kill raises ProcessLookupError → False, NO warning.

    ProcessLookupError is the expected "no such process" case — the common
    path post-daemon-death. Should not generate log noise on every CLI
    invocation after the daemon stops. Item #118 split the catch arms to
    distinguish this routine case from the rare OSError/SystemError ones.
    """
    monkeypatch.setitem(sys.modules, "psutil", None)

    def fake_kill(_pid: int, _sig: int) -> None:
        raise ProcessLookupError("no such process")

    monkeypatch.setattr("os.kill", fake_kill)
    with caplog.at_level("WARNING", logger="dbxignore.state"):
        result = state.is_daemon_alive(12345)
    assert result is False
    assert not any("os.kill" in rec.message for rec in caplog.records)


def test_is_daemon_alive_psutil_unavailable_oserror_returns_false_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """psutil unavailable, os.kill raises generic OSError → False + WARNING.

    Rare path — Windows PermissionError on the kill probe, EINVAL on a
    non-PID-shaped pid, etc. The WARNING surfaces the underlying error so
    callers can see that probing failed for an unusual reason rather than
    just "daemon is not running". Item #118.
    """
    monkeypatch.setitem(sys.modules, "psutil", None)

    def fake_kill(_pid: int, _sig: int) -> None:
        raise OSError(87, "fake EINVAL")

    monkeypatch.setattr("os.kill", fake_kill)
    with caplog.at_level("WARNING", logger="dbxignore.state"):
        result = state.is_daemon_alive(12345)
    assert result is False
    assert any("os.kill" in rec.message and "OSError" in rec.message for rec in caplog.records)


def test_is_daemon_alive_psutil_unavailable_system_error_returns_false_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """psutil unavailable, os.kill raises SystemError → False + WARNING.

    Item #118: CPython wraps an os.kill OSError as SystemError when called
    while another exception is still being handled. Surfaced 2026-05-12
    under the Python 3.14 + psutil partial-init scenario from item #117
    — `dbxignore uninstall --purge` crashed with an opaque
    `SystemError: <built-in function kill> returned a result with an
    exception set` rather than treating the indeterminate PID probe as
    "not alive". The fix catches SystemError alongside OSError so callers
    get `False` + a diagnostic WARNING instead of an uncaught exception.
    """
    monkeypatch.setitem(sys.modules, "psutil", None)

    def fake_kill(_pid: int, _sig: int) -> None:
        raise SystemError("<built-in function kill> returned a result with an exception set")

    monkeypatch.setattr("os.kill", fake_kill)
    with caplog.at_level("WARNING", logger="dbxignore.state"):
        result = state.is_daemon_alive(12345)
    assert result is False
    assert any("os.kill" in rec.message and "SystemError" in rec.message for rec in caplog.records)


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


def test_is_any_daemon_running_no_lock_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No daemon has ever run on this system (or `--purge` cleaned up the
    state dir) — lock file doesn't exist, helper returns False."""
    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)

    assert state.is_any_daemon_running() is False


def test_is_any_daemon_running_empty_lock_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lock file exists but is empty — the daemon writes a placeholder byte
    BEFORE locking, so an empty file means the daemon never reached its
    lock-acquire step. Helper returns False (no daemon)."""
    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    (state_dir / "daemon.lock").touch()
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)

    assert state.is_any_daemon_running() is False


def test_is_any_daemon_running_acquirable_lock_returns_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lock file exists with a placeholder byte but no one holds the lock
    (e.g. daemon died ungracefully — OS released the lock, file lingers).
    Helper acquires and releases the lock, returns False (no daemon)."""
    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    lock_path = state_dir / "daemon.lock"
    lock_path.write_bytes(b" ")
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)

    assert state.is_any_daemon_running() is False


def test_is_any_daemon_running_held_lock_returns_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lock file is held by another process (or in this test, another
    file descriptor in the same process — fcntl.flock and msvcrt.locking
    are both per-open-file-description, so a second acquire fails with
    EWOULDBLOCK/EACCES). Helper returns True (daemon-equivalent contention
    detected)."""
    from dbxignore import daemon

    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)

    holder = daemon._acquire_singleton_lock()
    assert holder is not None, "test setup: could not acquire holder lock"
    try:
        assert state.is_any_daemon_running() is True
    finally:
        holder.close()


def test_is_any_daemon_running_non_contention_oserror_returns_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-contention OSError from the lock primitive (``ENOLCK`` on a
    filesystem without advisory-lock support, ``EINTR``, ``ENOTSUP``) is
    indeterminate, not "lock held" — the helper logs a WARNING and returns
    False so destructive verbs aren't blocked on opaque errors."""
    import errno as _errno

    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    lock_path = state_dir / "daemon.lock"
    lock_path.write_bytes(b" ")
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)

    if sys.platform == "win32":
        import msvcrt  # type: ignore[import-not-found, unused-ignore]

        def fake_locking(fd: int, mode: int, n: int) -> None:
            raise OSError(_errno.ENOLCK, "fake non-contention error")

        monkeypatch.setattr(msvcrt, "locking", fake_locking)
    else:
        import fcntl  # type: ignore[import-not-found, unused-ignore]

        def fake_flock(fd: int, op: int) -> None:
            raise OSError(_errno.ENOLCK, "fake non-contention error")

        monkeypatch.setattr(fcntl, "flock", fake_flock)

    with caplog.at_level("WARNING"):
        result = state.is_any_daemon_running()

    assert result is False, "non-contention OSError must NOT report a live daemon"
    assert any("probe" in r.message.lower() for r in caplog.records), (
        "should have logged a WARNING about the indeterminate probe"
    )

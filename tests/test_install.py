import getpass
import subprocess
import sys
from pathlib import Path

import pytest

from dbxignore.install import windows_task as install
from dbxignore.install.windows_shell import _format_applies_to_query
from tests.conftest import FakeMarkers


def test_build_xml_contains_logon_trigger_and_action() -> None:
    xml = install.build_task_xml(exe_path=Path(r"C:\bin\dbxignored.exe"))
    assert "<LogonTrigger>" in xml
    assert f"<UserId>{getpass.getuser()}</UserId>" in xml
    assert r"C:\bin\dbxignored.exe" in xml
    assert "<RestartOnFailure>" in xml


def test_build_xml_uses_pythonw_when_source_install(tmp_path: Path) -> None:
    pythonw = tmp_path / "pythonw.exe"
    xml = install.build_task_xml(exe_path=pythonw, arguments="-m dbxignore daemon")
    assert "pythonw.exe" in xml
    assert "-m dbxignore daemon" in xml


def test_build_xml_escapes_ampersand_in_exe_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install paths containing ``&`` (e.g. ``C:\\Users\\Tom & Jerry\\``) must
    not break the XML. Without escaping, ``schtasks /Create /XML`` rejects the
    document as not-well-formed."""
    import xml.etree.ElementTree as ET

    monkeypatch.setattr("getpass.getuser", lambda: "kilo")
    xml = install.build_task_xml(exe_path=Path(r"C:\Users\Tom & Jerry\dbxignored.exe"))
    ET.fromstring(xml)  # would raise ParseError on unescaped ``&``


def test_build_xml_escapes_special_chars_in_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Usernames containing ``&``, ``<``, or ``>`` are rare but legal. The XML
    must remain well-formed regardless."""
    import xml.etree.ElementTree as ET

    monkeypatch.setattr("getpass.getuser", lambda: "A&B<C>")
    xml = install.build_task_xml(exe_path=Path(r"C:\bin\dbxignored.exe"))
    root = ET.fromstring(xml)
    ns = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
    user_ids = root.findall(f".//{ns}UserId")
    assert user_ids, "expected at least one UserId element"
    for el in user_ids:
        assert el.text == "A&B<C>"


def test_build_xml_escapes_ampersand_in_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defensive: arguments are interpolated too, and a future caller passing
    ``&``-containing args should not silently produce malformed XML."""
    import xml.etree.ElementTree as ET

    monkeypatch.setattr("getpass.getuser", lambda: "kilo")
    xml = install.build_task_xml(
        exe_path=Path(r"C:\bin\dbxignored.exe"),
        arguments="--flag a&b",
    )
    root = ET.fromstring(xml)
    ns = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
    args_el = root.find(f".//{ns}Arguments")
    assert args_el is not None
    assert args_el.text == "--flag a&b"


# detect_invocation tests live in tests/test_install_common.py — windows_task
# now re-exports the shared helper (item #50 collapse). The non-frozen
# Windows-specific behavior (pythonw.exe selection) is exercised by
# test_detect_invocation_returns_pythonw_on_windows there.


def test_uninstall_task_raises_on_schtasks_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """schtasks /Delete's non-zero exit must surface as a RuntimeError so the
    CLI stops claiming "Uninstalled scheduled task" when the task still
    exists (e.g. missing elevation, task already gone, locale quirks)."""
    from dbxignore import state as state_module

    # No state.json -> uninstall_task skips the post-/End wait. Without
    # this mock the test would read whatever state.json exists on the
    # host (a real daemon_pid on a developer Windows machine) and hang
    # in the wait loop until the per-test pytest-timeout fires.
    monkeypatch.setattr(state_module, "read", lambda: None)

    fake_result = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="ERROR: Access is denied.\r\n",
    )
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **kw: fake_result)  # type: ignore[attr-defined]

    with pytest.raises(RuntimeError, match="Access is denied"):
        install.uninstall_task()


def test_uninstall_task_succeeds_silently_on_zero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    from dbxignore import state as state_module

    monkeypatch.setattr(state_module, "read", lambda: None)

    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **kw: fake_result)  # type: ignore[attr-defined]
    install.uninstall_task()  # must not raise


def test_uninstall_task_ends_task_before_deleting_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per item #87: uninstall_task must signal the running task to end
    BEFORE removing the task definition. schtasks /Delete /F is fire-and-
    forget on the running instance, so the daemon process can outlive
    `dbxignore uninstall` by several seconds — long enough to write
    state.json after _purge_local_state() removes it. /End first lets
    the daemon exit cleanly; the subsequent wait pins the exit before
    /Delete fires.

    Mirrors the Linux/macOS synchronous-shutdown contract that
    `systemctl --user disable --now` and `launchctl bootout` already
    provide.
    """
    from dbxignore import state as state_module

    monkeypatch.setattr(
        state_module,
        "read",
        lambda: state_module.State(daemon_pid=12345),
    )
    # Daemon "already gone" by the time we poll — no real wait.
    monkeypatch.setattr(
        state_module,
        "is_daemon_alive",
        lambda pid, create_time=None: False,
    )

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(install.subprocess, "run", fake_run)  # type: ignore[attr-defined]

    install.uninstall_task()

    assert calls == [
        ["schtasks", "/End", "/TN", install.TASK_NAME],
        ["schtasks", "/Delete", "/TN", install.TASK_NAME, "/F"],
    ], calls


def test_uninstall_task_polls_is_daemon_alive_until_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the synchronization invariant: between /End and /Delete,
    uninstall_task polls is_daemon_alive() until the recorded daemon
    process has actually exited. daemon_create_time is forwarded so
    PID reuse cases (a recycled PID claimed by an unrelated process)
    are rejected. Without this wait, /Delete fires while the daemon
    is still alive — the orphaned daemon then writes state.json after
    a subsequent --purge has removed it.
    """
    from dbxignore import state as state_module

    monkeypatch.setattr(
        state_module,
        "read",
        lambda: state_module.State(daemon_pid=12345, daemon_create_time=1234567.89),
    )
    alive_states = [True, True, False]
    is_alive_calls: list[tuple[int, float | None]] = []

    def fake_is_alive(pid: int, create_time: float | None = None) -> bool:
        is_alive_calls.append((pid, create_time))
        return alive_states.pop(0)

    monkeypatch.setattr(state_module, "is_daemon_alive", fake_is_alive)
    monkeypatch.setattr(install.time, "sleep", lambda _: None)  # type: ignore[attr-defined]

    schtasks_calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        schtasks_calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(install.subprocess, "run", fake_run)  # type: ignore[attr-defined]

    install.uninstall_task()

    # daemon_create_time is forwarded on every poll so PID-reuse is rejected.
    assert is_alive_calls == [
        (12345, 1234567.89),
        (12345, 1234567.89),
        (12345, 1234567.89),
    ]
    # /Delete fires only after the wait drains.
    assert schtasks_calls == [
        ["schtasks", "/End", "/TN", install.TASK_NAME],
        ["schtasks", "/Delete", "/TN", install.TASK_NAME, "/F"],
    ]


def test_uninstall_task_logs_warning_and_still_deletes_on_timeout(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """If the daemon never exits within the wait window, log WARNING and
    still proceed with /Delete. /Delete failing to run would leave the
    next `dbxignore install` blocked by 'task already exists' — so the
    timeout path explicitly trades the synchronization guarantee for
    forward progress.
    """
    import logging

    from dbxignore import state as state_module

    monkeypatch.setattr(
        state_module,
        "read",
        lambda: state_module.State(daemon_pid=12345),
    )
    monkeypatch.setattr(state_module, "is_daemon_alive", lambda *a, **kw: True)
    monkeypatch.setattr(install.time, "sleep", lambda _: None)  # type: ignore[attr-defined]
    # monotonic ticks: deadline calc, loop check 1 (in window), loop check 2 (past).
    ticks = iter([0.0, 0.0, 100.0])
    monkeypatch.setattr(install.time, "monotonic", lambda: next(ticks))  # type: ignore[attr-defined]

    schtasks_calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        schtasks_calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(install.subprocess, "run", fake_run)  # type: ignore[attr-defined]

    with caplog.at_level(logging.WARNING, logger="dbxignore.install.windows_task"):
        install.uninstall_task()

    assert any(
        "did not exit within" in rec.message and "pid=12345" in rec.message
        for rec in caplog.records
    ), [rec.message for rec in caplog.records]
    assert schtasks_calls[-1] == ["schtasks", "/Delete", "/TN", install.TASK_NAME, "/F"]


def test_uninstall_task_skips_wait_when_end_fails_and_daemon_pid_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """schtasks /End "Stops only the instances of a program started by a
    scheduled task" (Microsoft docs), so a non-zero /End cannot make
    a non-task-instance daemon exit — e.g. a manually-launched
    `dbxignored` or a stale state.json from a different install. The
    wait must be gated on /End succeeding; otherwise uninstall hangs
    for the full _END_WAIT_TIMEOUT_S window with no benefit.

    pytest-timeout (10s default) would force-fail if the wait engaged,
    so this test is self-protective even without mocking time.sleep.
    """
    from dbxignore import state as state_module

    monkeypatch.setattr(
        state_module,
        "read",
        lambda: state_module.State(daemon_pid=12345),
    )
    # Daemon stays alive — would block the wait loop indefinitely if
    # the gating logic regressed.
    is_alive_calls: list[int] = []

    def fake_is_alive(pid: int, create_time: float | None = None) -> bool:
        is_alive_calls.append(pid)
        return True

    monkeypatch.setattr(state_module, "is_daemon_alive", fake_is_alive)

    schtasks_calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        schtasks_calls.append(cmd)
        if cmd[1] == "/End":
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="ERROR: The system cannot find the path specified.\r\n",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(install.subprocess, "run", fake_run)  # type: ignore[attr-defined]

    install.uninstall_task()  # must not hang

    # Wait skipped: is_daemon_alive never called despite daemon_pid being set.
    assert is_alive_calls == []
    # /Delete still ran.
    assert [cmd[1] for cmd in schtasks_calls] == ["/End", "/Delete"]


def test_uninstall_task_tolerates_end_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """schtasks /End returning non-zero is non-fatal — typical failure
    modes (target task isn't running, locale quirks) are operationally
    benign and shouldn't prevent /Delete from cleaning up the task
    definition. Compare /Delete's failure, which DOES raise (covered by
    test_uninstall_task_raises_on_schtasks_failure)."""
    from dbxignore import state as state_module

    monkeypatch.setattr(state_module, "read", lambda: None)

    schtasks_calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        schtasks_calls.append(cmd)
        if cmd[1] == "/End":
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="ERROR: The system cannot find the path specified.\r\n",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(install.subprocess, "run", fake_run)  # type: ignore[attr-defined]

    install.uninstall_task()  # must not raise

    assert [cmd[1] for cmd in schtasks_calls] == ["/End", "/Delete"]


def test_install_task_runs_schtasks_create_then_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """install_task should both register the task (Create) and start it now (Run)
    so the daemon comes up without waiting for next logon. Mirrors what
    `systemctl --user enable --now` does on Linux and what
    `launchctl bootstrap` + RunAtLoad does on macOS."""
    monkeypatch.setattr(install, "detect_invocation", lambda: (Path(r"C:\bin\dbxignored.exe"), ""))
    calls = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(install.subprocess, "run", fake_run)  # type: ignore[attr-defined]

    install.install_task()

    assert len(calls) == 2, calls
    assert calls[0][0:2] == ["schtasks", "/Create"]
    assert "/TN" in calls[0] and install.TASK_NAME in calls[0]
    assert calls[1] == ["schtasks", "/Run", "/TN", install.TASK_NAME]


def test_install_task_warns_but_does_not_raise_when_run_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A schtasks /Run failure must NOT surface as an install error — the
    Create succeeded, the task is registered, and it'll start at next logon
    regardless. Suppressing the failure here avoids a confusing partial-
    success state where the user sees an exception but the task is in
    fact installed."""
    import logging

    monkeypatch.setattr(install, "detect_invocation", lambda: (Path(r"C:\bin\dbxignored.exe"), ""))

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0:2] == ["schtasks", "/Create"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        # /Run fails (e.g. task scheduler service unavailable mid-install).
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=1,
            stdout="",
            stderr="ERROR: The Task Scheduler service is not available.\r\n",
        )

    monkeypatch.setattr(install.subprocess, "run", fake_run)  # type: ignore[attr-defined]

    with caplog.at_level(logging.WARNING, logger="dbxignore.install.windows_task"):
        install.install_task()  # must not raise

    assert any(
        "schtasks /Run returned 1" in rec.message
        and "Task is registered and will start at next logon" in rec.message
        for rec in caplog.records
    ), [rec.message for rec in caplog.records]


def test_cli_uninstall_reports_schtasks_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """cli.uninstall must echo the failure to stderr and exit non-zero when
    uninstall_service raises — not print "Uninstalled" anyway."""
    from click.testing import CliRunner

    import dbxignore.install as install_pkg
    from dbxignore import cli

    def raising_uninstall() -> None:
        raise RuntimeError("schtasks /Delete returned 1: ERROR: Access is denied.")

    monkeypatch.setattr(install_pkg, "uninstall_service", raising_uninstall)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["uninstall"])

    assert result.exit_code != 0, result.output
    assert "Failed to uninstall daemon service" in result.output
    assert "Access is denied" in result.output
    assert "Uninstalled dbxignore daemon service" not in result.output


def test_cli_install_reports_backend_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """cli.install must echo the failure to stderr and exit non-zero when
    install_service raises — not surface a raw traceback and not print
    "Installed" anyway. Mirrors the uninstall contract."""
    from click.testing import CliRunner

    import dbxignore.install as install_pkg
    from dbxignore import cli

    def raising_install() -> None:
        raise RuntimeError("schtasks /Create returned 1: ERROR: Access is denied.")

    monkeypatch.setattr(install_pkg, "install_service", raising_install)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["install"])

    assert result.exit_code != 0, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"expected clean SystemExit, got: {result.exception!r}"
    )
    assert "Failed to install daemon service" in result.output
    assert "Access is denied" in result.output
    assert "Installed dbxignore daemon service" not in result.output


def test_purge_removes_state_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_markers: FakeMarkers
) -> None:
    """--purge deletes state.default_path()."""
    import click.testing

    from dbxignore import cli, state

    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    state_json = state_dir / "state.json"
    state_json.write_text('{"schema": 1}', encoding="utf-8")

    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    monkeypatch.setattr("dbxignore.install.uninstall_service", lambda: None)

    result = click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])
    assert result.exit_code == 0
    assert not state_json.exists()


def test_purge_clears_marker_on_discovered_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_markers: FakeMarkers
) -> None:
    """A marker on the Dropbox root itself is part of purge's cleanup surface."""
    import click.testing

    from dbxignore import cli, state

    root = tmp_path / "Dropbox"
    root.mkdir()
    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    fake_markers.set_ignored(root)

    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr("dbxignore.install.uninstall_service", lambda: None)

    result = click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])

    assert result.exit_code == 0, result.output
    assert "Cleared 1 ignore markers" in result.output
    assert not fake_markers.is_ignored(root)


def test_purge_reports_marker_clear_errors_and_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_markers: FakeMarkers
) -> None:
    """Backlog item #98: when `markers.clear_ignored` raises OSError on one
    path, --purge collects the error, prints a stderr report listing the
    failure, still purges every OTHER marker and the local state dir, and
    exits 2 so scripts can detect incomplete cleanup. The prior shape
    silently swallowed OSError and reported full success."""
    import click.testing

    from dbxignore import cli, state

    root = tmp_path / "Dropbox"
    root.mkdir()
    good = root / "good.tmp"
    good.touch()
    bad = root / "bad.tmp"
    bad.touch()
    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    state_json = state_dir / "state.json"
    state_json.write_text('{"schema": 1}', encoding="utf-8")
    fake_markers.set_ignored(good)
    fake_markers.set_ignored(bad)

    # Inject a clear-side failure for `bad` only.
    real_clear = fake_markers.clear_ignored

    def failing_clear(path: Path) -> None:
        if path.resolve() == bad.resolve():
            raise PermissionError(13, "Permission denied", str(path))
        real_clear(path)

    monkeypatch.setattr(fake_markers, "clear_ignored", failing_clear)
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr("dbxignore.install.uninstall_service", lambda: None)

    result = click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])

    assert result.exit_code == 2, result.output
    # The good marker was cleared; the bad one was not.
    assert "Cleared 1 ignore markers" in result.output
    assert not fake_markers.is_ignored(good)
    assert fake_markers.is_ignored(bad)
    # Stderr names the failure with its operation tag and the bad path.
    assert "Could not fully clear markers" in result.stderr
    assert "clear failed on" in result.stderr
    assert str(bad) in result.stderr
    # State cleanup ran despite the marker failure — exit-2 only after.
    assert not state_json.exists()


def test_purge_skips_vanished_paths_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_markers: FakeMarkers
) -> None:
    """A path that `os.walk` listed can vanish before `markers.is_ignored`
    is called (Dropbox sync deleting the path, IDE moving a temp file,
    concurrent user activity). `FileNotFoundError` is an `OSError`
    subclass, so without a specific arm the prior fix would report the
    vanished path as a read failure and exit 2 spuriously. Mirrors the
    reconcile read arm's vanished-path treatment."""
    import click.testing

    from dbxignore import cli, state

    root = tmp_path / "Dropbox"
    root.mkdir()
    vanished = root / "vanished.tmp"
    vanished.touch()
    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    # Set up the marker BEFORE injecting the FileNotFoundError so the
    # path is in fake_markers' set; the injection covers what would
    # happen if the file disappeared between `os.walk`'s listing and the
    # `is_ignored` call.
    fake_markers.set_ignored(vanished)
    real_is_ignored = fake_markers.is_ignored

    def vanishing_is_ignored(path: Path) -> bool:
        if path.resolve() == vanished.resolve():
            raise FileNotFoundError(2, "No such file or directory", str(path))
        return real_is_ignored(path)

    monkeypatch.setattr(fake_markers, "is_ignored", vanishing_is_ignored)
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr("dbxignore.install.uninstall_service", lambda: None)

    result = click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])

    assert result.exit_code == 0, (result.output, result.stderr)
    # Vanished path is not counted in cleared (we never confirmed the
    # marker) and not reported as an error.
    assert "Cleared 0 ignore markers" in result.output
    assert "Could not fully clear" not in result.stderr


def test_purge_no_stderr_report_when_no_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_markers: FakeMarkers
) -> None:
    """Regression guard for item #98: happy path stays exit 0 with no
    "Could not fully clear" stderr report. Pins that the new error accumulator
    doesn't false-positive on the no-error case (distinct from
    ``test_purge_clears_marker_on_discovered_root``, which doesn't read
    stderr separately or assert the report's absence)."""
    import click.testing

    from dbxignore import cli, state

    root = tmp_path / "Dropbox"
    root.mkdir()
    marked = root / "scratch.tmp"
    marked.touch()
    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    fake_markers.set_ignored(marked)

    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr("dbxignore.install.uninstall_service", lambda: None)

    result = click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])

    assert result.exit_code == 0, result.output
    assert "Cleared 1 ignore markers" in result.output
    assert "Could not fully clear" not in result.stderr


def test_purge_removes_daemon_log_and_rotations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_markers: FakeMarkers
) -> None:
    """--purge deletes daemon.log plus rotated daemon.log.1..4."""
    import click.testing

    from dbxignore import cli, state

    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    for name in ["daemon.log", "daemon.log.1", "daemon.log.2", "daemon.log.3", "daemon.log.4"]:
        (state_dir / name).write_text("entry\n", encoding="utf-8")

    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    monkeypatch.setattr("dbxignore.install.uninstall_service", lambda: None)

    result = click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])
    assert result.exit_code == 0
    for name in ["daemon.log", "daemon.log.1", "daemon.log.2", "daemon.log.3", "daemon.log.4"]:
        assert not (state_dir / name).exists(), f"{name} survived --purge"


def test_purge_removes_slow_sweep_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_markers: FakeMarkers
) -> None:
    """--purge deletes the ``_test_slow_sweep`` marker (BACKLOG #89).
    Defends against a manual-test run that crashes mid-Phase-5 before
    the script's own cleanup arm runs — the next ``uninstall --purge``
    leaves no stale marker behind to silently re-pad future installs."""
    import click.testing

    from dbxignore import cli, daemon, state

    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    (state_dir / daemon.SLOW_SWEEP_MARKER_NAME).write_text("15\n", encoding="utf-8")
    # Need at least one other file so the dir survives the rmdir step and
    # the assertion targets the marker specifically.
    (state_dir / "user-note.txt").write_text("keep me\n", encoding="utf-8")

    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    monkeypatch.setattr("dbxignore.install.uninstall_service", lambda: None)

    result = click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])
    assert result.exit_code == 0
    assert not (state_dir / daemon.SLOW_SWEEP_MARKER_NAME).exists()
    assert (state_dir / "user-note.txt").exists()


def test_purge_rmdirs_empty_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_markers: FakeMarkers
) -> None:
    """After files are deleted, if the state dir is empty, rmdir removes it."""
    import click.testing

    from dbxignore import cli, state

    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    (state_dir / "state.json").write_text('{"schema": 1}', encoding="utf-8")

    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    monkeypatch.setattr("dbxignore.install.uninstall_service", lambda: None)

    click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])
    assert not state_dir.exists()


def test_purge_preserves_state_dir_with_foreign_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_markers: FakeMarkers
) -> None:
    """If the user has dropped something else in the state dir, rmdir fails
    silently and we preserve their content."""
    import click.testing

    from dbxignore import cli, state

    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    (state_dir / "state.json").write_text('{"schema": 1}', encoding="utf-8")
    (state_dir / "user-authored-note.txt").write_text(
        "my notes on the ignore config\n", encoding="utf-8"
    )

    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    monkeypatch.setattr("dbxignore.install.uninstall_service", lambda: None)

    click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])
    # State dir survives because it's not empty.
    assert state_dir.exists()
    # Our file is gone.
    assert not (state_dir / "state.json").exists()
    # Their file survives.
    assert (state_dir / "user-authored-note.txt").exists()


def test_purge_handles_missing_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_markers: FakeMarkers
) -> None:
    """--purge on a fresh install (no state dir yet) succeeds cleanly."""
    import click.testing

    from dbxignore import cli, state

    state_dir = tmp_path / "never_created"

    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    monkeypatch.setattr("dbxignore.install.uninstall_service", lambda: None)

    result = click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])
    assert result.exit_code == 0


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux-only")
def test_purge_removes_systemd_dropin_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_markers: FakeMarkers
) -> None:
    """On Linux, --purge also removes ~/.config/systemd/user/<unit>.d/."""
    import click.testing

    from dbxignore import cli, state

    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()

    dropin_dir = tmp_path / ".config" / "systemd" / "user" / "dbxignore.service.d"
    dropin_dir.mkdir(parents=True)
    (dropin_dir / "scratch-root.conf").write_text(
        "[Service]\nEnvironment=DBXIGNORE_ROOT=/home/u/dbx\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    monkeypatch.setattr("dbxignore.install.uninstall_service", lambda: None)

    click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])
    assert not dropin_dir.exists()


def test_purge_preserves_files_not_matching_daemon_log_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_markers: FakeMarkers
) -> None:
    """RotatingFileHandler only creates daemon.log and daemon.log.<N>.
    Files like `daemon.log_backup` or `daemon.logger` are not our artifacts —
    even if they start with `daemon.log`. --purge must not touch them."""
    import click.testing

    from dbxignore import cli, state

    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    (state_dir / "daemon.log").write_text("entry\n", encoding="utf-8")
    (state_dir / "daemon.log.1").write_text("entry\n", encoding="utf-8")
    # These names start with "daemon.log" but aren't rotation files:
    (state_dir / "daemon.log_backup").write_text("user content\n", encoding="utf-8")
    (state_dir / "daemon.logger").write_text("user content\n", encoding="utf-8")

    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    monkeypatch.setattr("dbxignore.install.uninstall_service", lambda: None)

    click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])

    # Rotation files gone.
    assert not (state_dir / "daemon.log").exists()
    assert not (state_dir / "daemon.log.1").exists()
    # User content preserved.
    assert (state_dir / "daemon.log_backup").exists()
    assert (state_dir / "daemon.logger").exists()
    # State dir survives because user content remains.
    assert state_dir.exists()


def test_purge_cleans_separate_log_dir_on_darwin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When state.user_log_dir != state.user_state_dir, purge cleans both."""
    state_dir = tmp_path / "state"
    log_dir = tmp_path / "logs"
    state_dir.mkdir()
    log_dir.mkdir()
    (state_dir / "state.json").write_text("{}")
    (log_dir / "daemon.log").write_text("entry")
    (log_dir / "daemon.log.1").write_text("rotated")
    (log_dir / "launchd.log").write_text("oops")

    from dbxignore import cli, state

    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(state, "user_log_dir", lambda: log_dir)
    monkeypatch.setattr(sys, "platform", "darwin")

    cli._purge_local_state()

    assert not state_dir.exists()
    assert not log_dir.exists()


def test_format_applies_to_query_single_root() -> None:
    roots = [Path(r"C:\Users\kilo\Dropbox")]
    result = _format_applies_to_query(roots)
    assert result == (
        r'System.ItemPathDisplay:="C:\\Users\\kilo\\Dropbox" OR '
        r'System.ItemPathDisplay:~<"C:\\Users\\kilo\\Dropbox\\"'
    )


def test_format_applies_to_query_multiple_roots_or_joined() -> None:
    roots = [Path(r"C:\Users\kilo\Dropbox"), Path(r"D:\Dropbox (Personal)")]
    result = _format_applies_to_query(roots)
    # Each root contributes := + :~< ; four clauses total OR-joined.
    assert result.count(" OR ") == 3
    assert r'System.ItemPathDisplay:="C:\\Users\\kilo\\Dropbox"' in result
    assert r'System.ItemPathDisplay:~<"C:\\Users\\kilo\\Dropbox\\"' in result
    assert r'System.ItemPathDisplay:="D:\\Dropbox (Personal)"' in result
    assert r'System.ItemPathDisplay:~<"D:\\Dropbox (Personal)\\"' in result


def test_format_applies_to_query_refuses_root_with_quote() -> None:
    roots = [Path('C:\\bad"path')]
    with pytest.raises(RuntimeError, match="contains a quote character"):
        _format_applies_to_query(roots)


def test_format_applies_to_query_empty_roots_returns_empty_string() -> None:
    # The dispatcher guards against this case (skipped-no-roots), but
    # the pure helper itself handles it cleanly — empty list ⇒ empty string.
    assert _format_applies_to_query([]) == ""


def test_format_applies_to_query_drive_root() -> None:
    """Drive-root Dropbox mount (e.g. `D:\\`) — str(Path) already has a trailing
    backslash; the prefix clause must not double-append, otherwise it produces
    `D:\\\\` in stored AQS which parses to `D:\\` (two backslashes) and matches
    no real Windows path.
    """
    roots = [Path("D:\\")]
    result = _format_applies_to_query(roots)
    # Exact clause for the root itself: `D:` followed by one backslash, doubled
    # in stored AQS to `D:\\`.
    assert r'System.ItemPathDisplay:="D:\\"' in result
    # Prefix clause: same `D:\\` — the prefix-construction normalization
    # (`rstrip + re-append`) ensures we DON'T get `D:\\\\` here.
    assert r'System.ItemPathDisplay:~<"D:\\"' in result
    # And we should have exactly two clauses (no spurious extras).
    assert result.count(" OR ") == 1

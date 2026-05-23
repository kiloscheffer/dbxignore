"""Unit tests for the macOS launchd User Agent install backend.

Cross-platform — tests plist generation via plistlib round-trip and
launchctl command construction via subprocess argument capture. No
real launchctl required.
"""

from __future__ import annotations

import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_build_plist_content_has_required_keys() -> None:
    from dbxignore.install import macos_launchd

    content = macos_launchd.build_plist_content(
        label="com.kiloscheffer.dbxignore",
        program_arguments=["/usr/local/bin/dbxignore", "daemon"],
        log_dir=Path("/tmp/log"),
    )
    parsed = plistlib.loads(content)
    assert parsed["Label"] == "com.kiloscheffer.dbxignore"
    assert parsed["ProgramArguments"] == ["/usr/local/bin/dbxignore", "daemon"]
    assert parsed["RunAtLoad"] is True
    assert parsed["KeepAlive"] == {"SuccessfulExit": False, "Crashed": True}
    assert parsed["StandardOutPath"] == "/tmp/log/launchd.log"
    assert parsed["StandardErrorPath"] == "/tmp/log/launchd.log"
    assert "EnvironmentVariables" not in parsed


def test_build_plist_content_emits_environment_variables_when_provided() -> None:
    from dbxignore.install import macos_launchd

    content = macos_launchd.build_plist_content(
        label="com.kiloscheffer.dbxignore",
        program_arguments=["/usr/local/bin/dbxignore", "daemon"],
        log_dir=Path("/tmp/log"),
        environment={"DBXIGNORE_ROOT": "/Users/kilo/Dropbox"},
    )
    parsed = plistlib.loads(content)
    assert parsed["EnvironmentVariables"] == {"DBXIGNORE_ROOT": "/Users/kilo/Dropbox"}


def test_build_plist_content_with_arguments_in_program() -> None:
    """Args after the executable should land as additional ProgramArguments entries."""
    from dbxignore.install import macos_launchd

    content = macos_launchd.build_plist_content(
        label="com.kiloscheffer.dbxignore",
        program_arguments=["/usr/local/bin/python3", "-m", "dbxignore", "daemon"],
        log_dir=Path("/tmp/log"),
    )
    parsed = plistlib.loads(content)
    assert parsed["ProgramArguments"] == [
        "/usr/local/bin/python3",
        "-m",
        "dbxignore",
        "daemon",
    ]


def test_service_target_includes_uid_and_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)
    from dbxignore.install import macos_launchd

    assert macos_launchd._service_target() == "gui/501/com.kiloscheffer.dbxignore"


def test_install_agent_writes_plist_and_calls_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)
    monkeypatch.setattr(
        "dbxignore.install.macos_launchd.detect_invocation",
        lambda: (Path("/usr/local/bin/dbxignore"), "daemon"),
    )
    monkeypatch.setattr(
        "dbxignore.state.user_log_dir",
        lambda: tmp_path / "logs",
    )

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        calls.append(cmd)

        class R:
            returncode = 0
            stderr = ""

        return R()

    monkeypatch.setattr("subprocess.run", fake_run)

    from dbxignore.install import macos_launchd

    macos_launchd.install_agent()

    plist_path = tmp_path / "Library" / "LaunchAgents" / "com.kiloscheffer.dbxignore.plist"
    assert plist_path.exists()
    parsed = plistlib.loads(plist_path.read_bytes())
    assert parsed["Label"] == "com.kiloscheffer.dbxignore"
    assert parsed["ProgramArguments"] == [str(Path("/usr/local/bin/dbxignore")), "daemon"]

    # Should have called bootout (idempotent precaution) then bootstrap.
    assert any(c[:2] == ["launchctl", "bootout"] for c in calls)
    bootstrap_calls = [c for c in calls if c[:2] == ["launchctl", "bootstrap"]]
    assert len(bootstrap_calls) == 1
    assert bootstrap_calls[0][2] == "gui/501"
    assert bootstrap_calls[0][3] == str(plist_path)


def test_uninstall_agent_calls_bootout_and_removes_plist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)

    plist_dir = tmp_path / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    plist_path = plist_dir / "com.kiloscheffer.dbxignore.plist"
    plist_path.write_bytes(b"<plist></plist>")

    # state.read() ignores HOME on non-darwin hosts (platformdirs uses the
    # native config dir), so without this stub the bootout-exit wait inside
    # uninstall_agent would poll against whatever real state.json happens to
    # exist on the test runner. Stub to None: the wait is gated on a non-None
    # daemon_pid and short-circuits, which matches the intent of this test
    # (verify bootout is called and plist is removed; not the wait).
    from dbxignore import state as state_module

    monkeypatch.setattr(state_module, "read", lambda: None)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        calls.append(cmd)

        class R:
            returncode = 0
            stderr = ""

        return R()

    monkeypatch.setattr("subprocess.run", fake_run)

    from dbxignore.install import macos_launchd

    macos_launchd.uninstall_agent()

    assert not plist_path.exists()
    assert any(c[:2] == ["launchctl", "bootout"] for c in calls)


def test_install_agent_wraps_filenotfounderror_from_launchctl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When launchctl isn't available (atypical on macOS but possible in
    stripped sandboxes), the FNFE must be translated to RuntimeError so
    cli.install reports a clean error rather than emitting a raw
    traceback."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)
    monkeypatch.setattr(
        "dbxignore.install.macos_launchd.detect_invocation",
        lambda: (Path("/usr/local/bin/dbxignore"), "daemon"),
    )
    monkeypatch.setattr(
        "dbxignore.state.user_log_dir",
        lambda: tmp_path / "logs",
    )

    def fake_run_missing(*_a: object, **_kw: object) -> object:
        raise FileNotFoundError(2, "No such file or directory", "launchctl")

    monkeypatch.setattr("subprocess.run", fake_run_missing)

    from dbxignore.install import macos_launchd

    with pytest.raises(RuntimeError, match="could not be invoked"):
        macos_launchd.install_agent()


def test_uninstall_agent_raises_on_launchctl_filenotfound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``uninstall_agent`` MUST raise RuntimeError when ``launchctl`` itself
    can't be invoked. Logging a warning and proceeding to remove the plist
    would leave an orphaned daemon running while ``dbxignore uninstall``
    reported success — and a subsequent ``--purge`` would clear
    state.json/markers under the live daemon. The asymmetry with
    ``install_agent``'s bootout pre-call (which DOES swallow OSError) is
    intentional: install's bootout is idempotent pre-cleanup, uninstall's
    bootout IS the daemon-shutdown step."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)

    plist_dir = tmp_path / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    plist_path = plist_dir / "com.kiloscheffer.dbxignore.plist"
    plist_path.write_bytes(b"<plist></plist>")

    def fake_run_missing(*_a: object, **_kw: object) -> object:
        raise FileNotFoundError(2, "No such file or directory", "launchctl")

    monkeypatch.setattr("subprocess.run", fake_run_missing)

    from dbxignore.install import macos_launchd

    with pytest.raises(RuntimeError, match="bootout could not be invoked"):
        macos_launchd.uninstall_agent()

    # plist MUST still exist — uninstall failed before removal.
    assert plist_path.exists(), "plist must not be removed when bootout fails to invoke launchctl"


def test_uninstall_agent_raises_on_bootout_nonzero_rc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bootout returning non-zero rc with a non-'not loaded' stderr signals
    a real failure (e.g. ``Boot-out failed: 5: Input/output error``).
    Discarding rc and stderr would unlink the plist unconditionally and
    let ``dbxignore uninstall`` report success while the daemon survived.
    Contract: surface as RuntimeError and preserve plist so the user
    can investigate."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)

    plist_dir = tmp_path / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    plist_path = plist_dir / "com.kiloscheffer.dbxignore.plist"
    plist_path.write_bytes(b"<plist></plist>")

    def fake_run_failure(cmd: list[str], **kwargs: object) -> object:
        class R:
            returncode = 5
            stderr = "Boot-out failed: 5: Input/output error"
            stdout = ""

        return R()

    monkeypatch.setattr("subprocess.run", fake_run_failure)

    from dbxignore.install import macos_launchd

    with pytest.raises(RuntimeError, match="bootout"):
        macos_launchd.uninstall_agent()

    assert plist_path.exists(), "plist must not be removed when bootout returns non-zero rc"


@pytest.mark.parametrize(
    ("rc", "stderr"),
    [
        # Each case exercises a distinct entry in
        # macos_launchd._NOT_LOADED_STDERR_PATTERNS. Removing any of the
        # three tuple entries below ("no such process", "could not find
        # service", "not loaded") makes one of these parametrize cases
        # fail — that's the coverage role this test plays.
        # "could not find specified service" is intentionally NOT
        # separately tested: it's a strict suffix of "could not find
        # service" so any stderr matching the longer phrase also matches
        # the shorter one, making the longer entry redundant under
        # substring matching. Kept in the tuple as documentation of the
        # platform-emitted wording, not as load-bearing coverage.
        (3, "Boot-out failed: 3: No such process"),
        (113, "Boot-out failed: 113: Could not find service in domain for port"),
        (3, "Service not loaded"),
    ],
    ids=["no_such_process", "could_not_find_service", "not_loaded"],
)
def test_uninstall_agent_tolerates_not_loaded_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rc: int, stderr: str
) -> None:
    """bootout returning non-zero rc with a 'not loaded'-class stderr is
    the idempotent-uninstall case — service was already torn down (e.g.
    user ran ``launchctl bootout`` manually between install and uninstall,
    or a crash unloaded the service). Treat as success and proceed to plist
    removal so a second ``dbxignore uninstall`` doesn't leave the plist on
    disk."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)

    plist_dir = tmp_path / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    plist_path = plist_dir / "com.kiloscheffer.dbxignore.plist"
    plist_path.write_bytes(b"<plist></plist>")

    # See sibling test_uninstall_agent_calls_bootout_and_removes_plist:
    # the bootout-exit wait reads state.json, which on non-darwin hosts
    # bypasses the HOME monkeypatch. Stub to None so the wait short-circuits.
    from dbxignore import state as state_module

    monkeypatch.setattr(state_module, "read", lambda: None)

    def fake_run_not_loaded(cmd: list[str], **kwargs: object) -> object:
        return SimpleNamespace(returncode=rc, stderr=stderr, stdout="")

    monkeypatch.setattr("subprocess.run", fake_run_not_loaded)

    from dbxignore.install import macos_launchd

    macos_launchd.uninstall_agent()

    assert not plist_path.exists(), (
        "plist should be removed when bootout fails idempotently ('not loaded')"
    )


def test_uninstall_agent_polls_is_daemon_alive_until_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin the synchronization invariant: after launchctl bootout returns,
    uninstall_agent polls is_daemon_alive() until the recorded daemon
    process has actually exited, THEN removes the plist. Without this wait,
    cli.uninstall --purge's daemon-alive guard fires on a daemon that's
    about to exit but isn't quite gone yet — the SIGTERM handler may still
    be releasing the singleton lock and writing final state.
    daemon_create_time is forwarded so PID reuse cases are rejected. Mirrors
    windows_task.uninstall_task's wait-for-exit pattern.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)

    plist_dir = tmp_path / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    plist_path = plist_dir / "com.kiloscheffer.dbxignore.plist"
    plist_path.write_bytes(b"<plist></plist>")

    from dbxignore import state as state_module
    from dbxignore.install import macos_launchd

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
    monkeypatch.setattr(macos_launchd.time, "sleep", lambda _: None)  # type: ignore[attr-defined]

    bootout_call_order: list[str] = []

    def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        bootout_call_order.append(cmd[1])
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("subprocess.run", fake_run)

    macos_launchd.uninstall_agent()

    # daemon_create_time is forwarded on every poll so PID-reuse is rejected.
    assert is_alive_calls == [
        (12345, 1234567.89),
        (12345, 1234567.89),
        (12345, 1234567.89),
    ]
    # bootout fired exactly once; plist removed only after the wait drained.
    assert bootout_call_order == ["bootout"]
    assert not plist_path.exists()


def test_uninstall_agent_skips_wait_when_bootout_reports_not_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When bootout returns non-zero with a 'not loaded' stderr (idempotent
    uninstall — service was already gone), there is no daemon to wait for.
    A stale state.json with daemon_create_time=None and daemon_pid recycled
    to an unrelated python process would otherwise make is_daemon_alive
    return True on every poll and burn the 30s timeout for nothing. Gate
    matches windows_task's `end_result.returncode == 0` check.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)

    plist_dir = tmp_path / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    plist_path = plist_dir / "com.kiloscheffer.dbxignore.plist"
    plist_path.write_bytes(b"<plist></plist>")

    from dbxignore import state as state_module
    from dbxignore.install import macos_launchd

    # Stale state.json with a pid but no create_time — exactly the
    # regression case Codex flagged.
    monkeypatch.setattr(
        state_module,
        "read",
        lambda: state_module.State(daemon_pid=12345, daemon_create_time=None),
    )

    is_alive_calls: list[tuple[int, float | None]] = []

    def fake_is_alive(pid: int, create_time: float | None = None) -> bool:
        is_alive_calls.append((pid, create_time))
        return True  # simulate the recycled-python-PID false positive

    monkeypatch.setattr(state_module, "is_daemon_alive", fake_is_alive)

    def fake_run_not_loaded(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=3, stderr="Boot-out failed: 3: No such process", stdout=""
        )

    monkeypatch.setattr("subprocess.run", fake_run_not_loaded)

    macos_launchd.uninstall_agent()

    # Gate must skip the wait entirely. is_daemon_alive is never consulted
    # because the rc != 0 branch is the "not loaded" idempotent path.
    assert is_alive_calls == []
    # Plist still removed — idempotent uninstall still completes.
    assert not plist_path.exists()


def test_uninstall_agent_logs_warning_and_still_removes_plist_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """If the daemon never exits within the wait window, log a WARNING and
    still proceed with plist removal. Failing to remove the plist would leave
    the next `dbxignore install` blocked (bootstrap would fail with "service
    already loaded") — so the timeout path trades the synchronization
    guarantee for forward progress, same as windows_task does on /End timeout.
    """
    import logging

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)

    plist_dir = tmp_path / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True)
    plist_path = plist_dir / "com.kiloscheffer.dbxignore.plist"
    plist_path.write_bytes(b"<plist></plist>")

    from dbxignore import state as state_module
    from dbxignore.install import macos_launchd

    monkeypatch.setattr(
        state_module,
        "read",
        lambda: state_module.State(daemon_pid=12345),
    )
    monkeypatch.setattr(state_module, "is_daemon_alive", lambda *a, **kw: True)
    monkeypatch.setattr(macos_launchd.time, "sleep", lambda _: None)  # type: ignore[attr-defined]
    # monotonic ticks: deadline calc, loop check 1 (in window), loop check 2 (past).
    ticks = iter([0.0, 0.0, 100.0])
    monkeypatch.setattr(macos_launchd.time, "monotonic", lambda: next(ticks))  # type: ignore[attr-defined]

    def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("subprocess.run", fake_run)

    with caplog.at_level(logging.WARNING, logger="dbxignore.install.macos_launchd"):
        macos_launchd.uninstall_agent()

    assert any(
        "did not exit within" in rec.message and "pid=12345" in rec.message
        for rec in caplog.records
    ), [rec.message for rec in caplog.records]
    # Plist is still removed despite the timeout — forward progress wins.
    assert not plist_path.exists()

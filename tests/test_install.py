import getpass
import subprocess
import sys
from pathlib import Path

import pytest

from dbxignore.install import windows_task as install
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


def test_detect_invocation_returns_frozen_mode_when_already_dbxignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """User invoked `dbxignored.exe install` directly: sys.executable IS the daemon shim."""
    daemon_exe = tmp_path / "dbxignored.exe"
    daemon_exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(daemon_exe))
    exe, args = install.detect_invocation()
    assert exe == daemon_exe
    assert args == ""


def test_detect_invocation_finds_dbxignored_sibling_from_dbxignore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """User invoked `dbxignore.exe install` (frozen): resolve to `dbxignored.exe` sibling.

    Common case — both PyInstaller binaries ship as a paired set, the user runs
    the long-form CLI for install, and Task Scheduler needs the daemon-shim
    binary as its `<Command>` invocation target. Mirrors the macOS/Linux path
    in `tests/test_install_common.py`.
    """
    cli_exe = tmp_path / "dbxignore.exe"
    cli_exe.write_text("")
    daemon_exe = tmp_path / "dbxignored.exe"
    daemon_exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(cli_exe))
    exe, args = install.detect_invocation()
    assert exe == daemon_exe
    assert args == ""


def test_detect_invocation_falls_back_to_daemon_subcommand_when_sibling_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No `dbxignored.exe` sibling: invoke ourselves with the `daemon` subcommand.

    Defensive case — PyInstaller specs always emit both binaries, so this
    fallback is for unusual deployments only.
    """
    cli_exe = tmp_path / "dbxignore.exe"
    cli_exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(cli_exe))
    exe, args = install.detect_invocation()
    assert exe == cli_exe
    assert args == "daemon"


def test_detect_invocation_returns_source_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\uv\tools\dbxignore\Scripts\python.exe")
    exe, args = install.detect_invocation()
    assert exe.name == "pythonw.exe"
    assert args == "-m dbxignore daemon"


def test_uninstall_task_raises_on_schtasks_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """schtasks /Delete's non-zero exit must surface as a RuntimeError so the
    CLI stops claiming "Uninstalled scheduled task" when the task still
    exists (e.g. missing elevation, task already gone, locale quirks)."""
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
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **kw: fake_result)  # type: ignore[attr-defined]
    install.uninstall_task()  # must not raise


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

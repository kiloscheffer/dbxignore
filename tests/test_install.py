import getpass
import subprocess
import sys
from pathlib import Path

import pytest

from dropboxignore.install import windows_task as install


def test_build_xml_contains_logon_trigger_and_action():
    xml = install.build_task_xml(exe_path=Path(r"C:\bin\dropboxignored.exe"))
    assert "<LogonTrigger>" in xml
    assert f"<UserId>{getpass.getuser()}</UserId>" in xml
    assert r"C:\bin\dropboxignored.exe" in xml
    assert "<RestartOnFailure>" in xml


def test_build_xml_uses_pythonw_when_source_install(tmp_path):
    pythonw = tmp_path / "pythonw.exe"
    xml = install.build_task_xml(
        exe_path=pythonw, arguments="-m dropboxignore daemon"
    )
    assert "pythonw.exe" in xml
    assert "-m dropboxignore daemon" in xml


def test_detect_invocation_returns_frozen_mode(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\bin\dropboxignored.exe")
    exe, args = install.detect_invocation()
    assert exe == Path(r"C:\bin\dropboxignored.exe")
    assert args == ""


def test_detect_invocation_returns_source_mode(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\uv\tools\dropboxignore\Scripts\python.exe")
    exe, args = install.detect_invocation()
    assert exe.name == "pythonw.exe"
    assert args == "-m dropboxignore daemon"


def test_uninstall_task_raises_on_schtasks_failure(monkeypatch):
    """schtasks /Delete's non-zero exit must surface as a RuntimeError so the
    CLI stops claiming "Uninstalled scheduled task" when the task still
    exists (e.g. missing elevation, task already gone, locale quirks)."""
    fake_result = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="ERROR: Access is denied.\r\n",
    )
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **kw: fake_result)

    with pytest.raises(RuntimeError, match="Access is denied"):
        install.uninstall_task()


def test_uninstall_task_succeeds_silently_on_zero_exit(monkeypatch):
    fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **kw: fake_result)
    install.uninstall_task()  # must not raise


def test_cli_uninstall_reports_schtasks_failure(monkeypatch):
    """cli.uninstall must echo the failure to stderr and exit non-zero when
    uninstall_service raises — not print "Uninstalled" anyway."""
    from click.testing import CliRunner

    import dropboxignore.install as install_pkg
    from dropboxignore import cli

    def raising_uninstall():
        raise RuntimeError("schtasks /Delete returned 1: ERROR: Access is denied.")

    monkeypatch.setattr(install_pkg, "uninstall_service", raising_uninstall)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["uninstall"])

    assert result.exit_code != 0, result.output
    assert "Failed to uninstall daemon service" in result.output
    assert "Access is denied" in result.output
    assert "Uninstalled dropboxignore daemon service" not in result.output


def test_cli_install_reports_backend_failure(monkeypatch):
    """cli.install must echo the failure to stderr and exit non-zero when
    install_service raises — not surface a raw traceback and not print
    "Installed" anyway. Mirrors the uninstall contract."""
    from click.testing import CliRunner

    import dropboxignore.install as install_pkg
    from dropboxignore import cli

    def raising_install():
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
    assert "Installed dropboxignore daemon service" not in result.output


def test_purge_removes_state_json(tmp_path, monkeypatch, fake_markers):
    """--purge deletes state.default_path()."""
    import click.testing

    from dropboxignore import cli, state

    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    state_json = state_dir / "state.json"
    state_json.write_text('{"schema": 1}', encoding="utf-8")

    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    monkeypatch.setattr(
        "dropboxignore.install.uninstall_service", lambda: None
    )

    result = click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])
    assert result.exit_code == 0
    assert not state_json.exists()


def test_purge_removes_daemon_log_and_rotations(tmp_path, monkeypatch, fake_markers):
    """--purge deletes daemon.log plus rotated daemon.log.1..4."""
    import click.testing

    from dropboxignore import cli, state

    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    for name in ["daemon.log", "daemon.log.1", "daemon.log.2", "daemon.log.3", "daemon.log.4"]:
        (state_dir / name).write_text("entry\n", encoding="utf-8")

    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    monkeypatch.setattr(
        "dropboxignore.install.uninstall_service", lambda: None
    )

    result = click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])
    assert result.exit_code == 0
    for name in ["daemon.log", "daemon.log.1", "daemon.log.2", "daemon.log.3", "daemon.log.4"]:
        assert not (state_dir / name).exists(), f"{name} survived --purge"


def test_purge_rmdirs_empty_state_dir(tmp_path, monkeypatch, fake_markers):
    """After files are deleted, if the state dir is empty, rmdir removes it."""
    import click.testing

    from dropboxignore import cli, state

    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    (state_dir / "state.json").write_text('{"schema": 1}', encoding="utf-8")

    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    monkeypatch.setattr(
        "dropboxignore.install.uninstall_service", lambda: None
    )

    click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])
    assert not state_dir.exists()


def test_purge_preserves_state_dir_with_foreign_content(tmp_path, monkeypatch, fake_markers):
    """If the user has dropped something else in the state dir, rmdir fails
    silently and we preserve their content."""
    import click.testing

    from dropboxignore import cli, state

    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    (state_dir / "state.json").write_text('{"schema": 1}', encoding="utf-8")
    (state_dir / "user-authored-note.txt").write_text(
        "my notes on the ignore config\n", encoding="utf-8"
    )

    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    monkeypatch.setattr(
        "dropboxignore.install.uninstall_service", lambda: None
    )

    click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])
    # State dir survives because it's not empty.
    assert state_dir.exists()
    # Our file is gone.
    assert not (state_dir / "state.json").exists()
    # Their file survives.
    assert (state_dir / "user-authored-note.txt").exists()


def test_purge_handles_missing_state_dir(tmp_path, monkeypatch, fake_markers):
    """--purge on a fresh install (no state dir yet) succeeds cleanly."""
    import click.testing

    from dropboxignore import cli, state

    state_dir = tmp_path / "never_created"

    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    monkeypatch.setattr(
        "dropboxignore.install.uninstall_service", lambda: None
    )

    result = click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])
    assert result.exit_code == 0


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux-only")
def test_purge_removes_systemd_dropin_dir(tmp_path, monkeypatch, fake_markers):
    """On Linux, --purge also removes ~/.config/systemd/user/<unit>.d/."""
    import click.testing

    from dropboxignore import cli, state

    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()

    dropin_dir = tmp_path / ".config" / "systemd" / "user" / "dropboxignore.service.d"
    dropin_dir.mkdir(parents=True)
    (dropin_dir / "scratch-root.conf").write_text(
        "[Service]\nEnvironment=DROPBOXIGNORE_ROOT=/home/u/dbx\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    monkeypatch.setattr(
        "dropboxignore.install.uninstall_service", lambda: None
    )

    click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])
    assert not dropin_dir.exists()


def test_purge_preserves_files_not_matching_daemon_log_rotation(
    tmp_path, monkeypatch, fake_markers
):
    """RotatingFileHandler only creates daemon.log and daemon.log.<N>.
    Files like `daemon.log_backup` or `daemon.logger` are not our artifacts —
    even if they start with `daemon.log`. --purge must not touch them."""
    import click.testing

    from dropboxignore import cli, state

    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()
    (state_dir / "daemon.log").write_text("entry\n", encoding="utf-8")
    (state_dir / "daemon.log.1").write_text("entry\n", encoding="utf-8")
    # These names start with "daemon.log" but aren't rotation files:
    (state_dir / "daemon.log_backup").write_text("user content\n", encoding="utf-8")
    (state_dir / "daemon.logger").write_text("user content\n", encoding="utf-8")

    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    monkeypatch.setattr(
        "dropboxignore.install.uninstall_service", lambda: None
    )

    click.testing.CliRunner().invoke(cli.main, ["uninstall", "--purge"])

    # Rotation files gone.
    assert not (state_dir / "daemon.log").exists()
    assert not (state_dir / "daemon.log.1").exists()
    # User content preserved.
    assert (state_dir / "daemon.log_backup").exists()
    assert (state_dir / "daemon.logger").exists()
    # State dir survives because user content remains.
    assert state_dir.exists()

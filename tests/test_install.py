import getpass
import subprocess
import sys
from pathlib import Path

import pytest

from dropboxignore import install


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
    uninstall_task raises — not print "Uninstalled" anyway."""
    from click.testing import CliRunner

    from dropboxignore import cli

    def raising_uninstall():
        raise RuntimeError("schtasks /Delete returned 1: ERROR: Access is denied.")

    monkeypatch.setattr(install, "uninstall_task", raising_uninstall)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["uninstall"])

    assert result.exit_code != 0, result.output
    assert "Failed to uninstall scheduled task" in result.output
    assert "Access is denied" in result.output
    assert "Uninstalled scheduled task" not in result.output

import getpass
import sys
from pathlib import Path

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

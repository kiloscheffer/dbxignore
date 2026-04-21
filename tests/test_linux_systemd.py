"""Unit tests for the Linux systemd-user-unit install/uninstall backend.

Mocks all subprocess calls and the filesystem write. No real systemd
required, so this is a pure unit test running under ``not linux_only``
on every OS — the logic is pure-Python string manipulation + subprocess
argument assembly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def test_unit_file_content_has_exec_start_and_wanted_by(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "dropboxignore.install.linux_systemd._detect_invocation",
        lambda: (Path("/usr/local/bin/dropboxignored"), ""),
    )

    from dropboxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(Path("/usr/local/bin/dropboxignored"), "")
    assert "ExecStart=/usr/local/bin/dropboxignored" in content
    assert "Restart=on-failure" in content
    assert "WantedBy=default.target" in content


def test_unit_file_content_appends_arguments(tmp_path):
    from dropboxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/home/u/.local/bin/python"),
        "-m dropboxignore daemon",
    )
    assert (
        "ExecStart=/home/u/.local/bin/python -m dropboxignore daemon" in content
    )


def test_install_writes_unit_and_invokes_systemctl(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "dropboxignore.install.linux_systemd._detect_invocation",
        lambda: (Path("/usr/local/bin/dropboxignored"), ""),
    )

    calls: list[list[str]] = []

    def fake_run(cmd, check, capture_output=False, text=False):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    from dropboxignore.install import linux_systemd

    linux_systemd.install_unit()

    unit_path = tmp_path / ".config" / "systemd" / "user" / "dropboxignore.service"
    assert unit_path.exists()
    assert "ExecStart=/usr/local/bin/dropboxignored" in unit_path.read_text()

    assert calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "dropboxignore.service"],
    ]


def test_uninstall_disables_removes_unit_and_reloads(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    unit_path = tmp_path / ".config" / "systemd" / "user" / "dropboxignore.service"
    unit_path.parent.mkdir(parents=True)
    unit_path.write_text("[Unit]\nDescription=stub\n")

    calls: list[list[str]] = []

    def fake_run(cmd, check=False, capture_output=False, text=False):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    from dropboxignore.install import linux_systemd

    linux_systemd.uninstall_unit()

    assert not unit_path.exists()
    assert calls == [
        ["systemctl", "--user", "disable", "--now", "dropboxignore.service"],
        ["systemctl", "--user", "daemon-reload"],
    ]


def test_install_raises_when_executable_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    def _raise_not_found():
        raise RuntimeError("dropboxignored not on PATH; run `uv tool install .`")

    monkeypatch.setattr(
        "dropboxignore.install.linux_systemd._detect_invocation",
        _raise_not_found,
    )

    from dropboxignore.install import linux_systemd

    with pytest.raises(RuntimeError, match="dropboxignored not on PATH"):
        linux_systemd.install_unit()

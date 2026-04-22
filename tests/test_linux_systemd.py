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


def test_unit_content_has_no_environment_line_by_default():
    from dropboxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/usr/local/bin/dropboxignored"),
    )
    assert "Environment=" not in content


def test_unit_content_emits_environment_before_exec_start():
    """Environment= must appear inside [Service] and before ExecStart= so the
    daemon sees the variable when it launches."""
    from dropboxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/usr/local/bin/dropboxignored"),
        environment={"DROPBOXIGNORE_ROOT": "/home/kilo/dbx"},
    )
    assert 'Environment="DROPBOXIGNORE_ROOT=/home/kilo/dbx"' in content

    service_section = content.split("[Service]", 1)[1].split("[Install]", 1)[0]
    env_idx = service_section.index('Environment="DROPBOXIGNORE_ROOT=')
    exec_idx = service_section.index("ExecStart=")
    assert env_idx < exec_idx


def test_unit_content_quotes_environment_value_with_spaces():
    """Paths with spaces (e.g. ``/home/u/My Dropbox``) must survive intact —
    the outer-quoted Environment= form wraps the whole KEY=VALUE so the
    value can contain whitespace without systemd tokenizing on it."""
    from dropboxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/usr/local/bin/dropboxignored"),
        environment={"DROPBOXIGNORE_ROOT": "/home/u/My Dropbox"},
    )
    assert 'Environment="DROPBOXIGNORE_ROOT=/home/u/My Dropbox"' in content


def test_unit_content_escapes_backslash_and_quote_in_environment_value():
    """Backslash and double-quote must be escaped so systemd's parser
    doesn't misread them as escape sequences or an early end-of-string."""
    from dropboxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/usr/local/bin/dropboxignored"),
        environment={"DROPBOXIGNORE_ROOT": r'/path with "quote" and \slash'},
    )
    assert (
        r'Environment="DROPBOXIGNORE_ROOT=/path with \"quote\" and \\slash"'
        in content
    )


def test_unit_content_accepts_none_environment():
    """environment=None is equivalent to omitting the argument entirely."""
    from dropboxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/usr/local/bin/dropboxignored"),
        environment=None,
    )
    assert "Environment=" not in content


def test_install_propagates_dropboxignore_root_env(tmp_path, monkeypatch):
    """When DROPBOXIGNORE_ROOT is set in the install process's env, the
    generated unit must carry it forward — that's the fix for item 9."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DROPBOXIGNORE_ROOT", "/home/kilo/dbx-smoke")
    monkeypatch.setattr(
        "dropboxignore.install.linux_systemd._detect_invocation",
        lambda: (Path("/usr/local/bin/dropboxignored"), ""),
    )
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, check, capture_output=False, text=False:
            subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    from dropboxignore.install import linux_systemd

    linux_systemd.install_unit()

    unit_path = tmp_path / ".config" / "systemd" / "user" / "dropboxignore.service"
    assert 'Environment="DROPBOXIGNORE_ROOT=/home/kilo/dbx-smoke"' in unit_path.read_text()


def test_install_omits_environment_when_dropboxignore_root_unset(tmp_path, monkeypatch):
    """No env var → no Environment= line. Stock-Dropbox users shouldn't see
    boilerplate they don't need."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("DROPBOXIGNORE_ROOT", raising=False)
    monkeypatch.setattr(
        "dropboxignore.install.linux_systemd._detect_invocation",
        lambda: (Path("/usr/local/bin/dropboxignored"), ""),
    )
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, check, capture_output=False, text=False:
            subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    from dropboxignore.install import linux_systemd

    linux_systemd.install_unit()

    unit_path = tmp_path / ".config" / "systemd" / "user" / "dropboxignore.service"
    assert "Environment=" not in unit_path.read_text()


def test_install_ignores_empty_dropboxignore_root(tmp_path, monkeypatch):
    """Empty string means 'shell sourced a template with an unset placeholder' —
    treat as unset rather than forwarding a meaningless blank value that would
    cause ``roots.discover()`` to fall through to ``info.json`` anyway."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DROPBOXIGNORE_ROOT", "")
    monkeypatch.setattr(
        "dropboxignore.install.linux_systemd._detect_invocation",
        lambda: (Path("/usr/local/bin/dropboxignored"), ""),
    )
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, check, capture_output=False, text=False:
            subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    from dropboxignore.install import linux_systemd

    linux_systemd.install_unit()

    unit_path = tmp_path / ".config" / "systemd" / "user" / "dropboxignore.service"
    assert "Environment=" not in unit_path.read_text()


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


def test_install_wraps_calledprocesserror_from_systemctl(tmp_path, monkeypatch):
    """A failing systemctl must raise RuntimeError, not CalledProcessError.

    cli.install / cli.uninstall catch RuntimeError; a CalledProcessError
    would escape as a raw traceback.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "dropboxignore.install.linux_systemd._detect_invocation",
        lambda: (Path("/usr/local/bin/dropboxignored"), ""),
    )

    def fake_run_fails(cmd, check, capture_output=False, text=False):
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="no user session")

    monkeypatch.setattr(subprocess, "run", fake_run_fails)

    from dropboxignore.install import linux_systemd

    with pytest.raises(RuntimeError, match="daemon-reload"):
        linux_systemd.install_unit()


def test_remove_dropin_directory_removes_existing(tmp_path, monkeypatch):
    """Drop-in dir with a user-authored override file gets removed
    wholesale on --purge cleanup."""
    monkeypatch.setenv("HOME", str(tmp_path))
    dropin_dir = tmp_path / ".config" / "systemd" / "user" / "dropboxignore.service.d"
    dropin_dir.mkdir(parents=True)
    (dropin_dir / "scratch-root.conf").write_text(
        "[Service]\nEnvironment=DROPBOXIGNORE_ROOT=/home/u/dbx\n",
        encoding="utf-8",
    )

    from dropboxignore.install import linux_systemd
    result = linux_systemd.remove_dropin_directory()

    assert result == dropin_dir
    assert not dropin_dir.exists()


def test_remove_dropin_directory_absent_returns_none(tmp_path, monkeypatch):
    """Drop-in dir not present → return None, no error."""
    monkeypatch.setenv("HOME", str(tmp_path))

    from dropboxignore.install import linux_systemd
    assert linux_systemd.remove_dropin_directory() is None


def test_remove_dropin_directory_no_home_returns_none(monkeypatch):
    """HOME unset → return None (can't locate the dir; silent skip)."""
    monkeypatch.delenv("HOME", raising=False)

    from dropboxignore.install import linux_systemd
    assert linux_systemd.remove_dropin_directory() is None

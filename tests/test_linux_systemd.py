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


def test_unit_file_content_has_exec_start_and_wanted_by(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "dbxignore.install.linux_systemd.detect_invocation",
        lambda: (Path("/usr/local/bin/dbxignored"), ""),
    )

    from dbxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(Path("/usr/local/bin/dbxignored"), "")
    assert "ExecStart=/usr/local/bin/dbxignored" in content
    assert "Restart=on-failure" in content
    assert "WantedBy=default.target" in content


def test_unit_file_content_appends_arguments(tmp_path: Path) -> None:
    from dbxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/home/u/.local/bin/python"),
        "-m dbxignore daemon",
    )
    assert "ExecStart=/home/u/.local/bin/python -m dbxignore daemon" in content


def test_unit_content_has_no_environment_line_by_default() -> None:
    from dbxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/usr/local/bin/dbxignored"),
    )
    assert "Environment=" not in content


def test_unit_content_emits_environment_before_exec_start() -> None:
    """Environment= must appear inside [Service] and before ExecStart= so the
    daemon sees the variable when it launches."""
    from dbxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/usr/local/bin/dbxignored"),
        environment={"DBXIGNORE_ROOT": "/home/kilo/dbx"},
    )
    assert 'Environment="DBXIGNORE_ROOT=/home/kilo/dbx"' in content

    service_section = content.split("[Service]", 1)[1].split("[Install]", 1)[0]
    env_idx = service_section.index('Environment="DBXIGNORE_ROOT=')
    exec_idx = service_section.index("ExecStart=")
    assert env_idx < exec_idx


def test_unit_content_quotes_environment_value_with_spaces() -> None:
    """Paths with spaces (e.g. ``/home/u/My Dropbox``) must survive intact —
    the outer-quoted Environment= form wraps the whole KEY=VALUE so the
    value can contain whitespace without systemd tokenizing on it."""
    from dbxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/usr/local/bin/dbxignored"),
        environment={"DBXIGNORE_ROOT": "/home/u/My Dropbox"},
    )
    assert 'Environment="DBXIGNORE_ROOT=/home/u/My Dropbox"' in content


def test_unit_content_escapes_backslash_and_quote_in_environment_value() -> None:
    """Backslash and double-quote must be escaped so systemd's parser
    doesn't misread them as escape sequences or an early end-of-string."""
    from dbxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/usr/local/bin/dbxignored"),
        environment={"DBXIGNORE_ROOT": r'/path with "quote" and \slash'},
    )
    assert r'Environment="DBXIGNORE_ROOT=/path with \"quote\" and \\slash"' in content


def test_unit_content_quotes_exec_start_path_with_whitespace() -> None:
    """systemd splits ExecStart on whitespace; an executable path containing
    a space (e.g. ``/home/user/My Tools/dbxignored``) would be tokenized into
    two separate args, breaking the unit. Wrap the path in double quotes so
    systemd's parser treats it as one token."""
    from dbxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/home/user/My Tools/dbxignored"),
        "",
    )
    assert 'ExecStart="/home/user/My Tools/dbxignored"' in content


def test_unit_content_quotes_exec_start_path_with_whitespace_and_arguments() -> None:
    """Quoting wraps only the path, not the arguments — arguments stay
    whitespace-separated so systemd splits them into multiple argv entries
    as today (e.g. ``-m``, ``dbxignore``, ``daemon``)."""
    from dbxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/home/user/My Tools/python"),
        "-m dbxignore daemon",
    )
    assert 'ExecStart="/home/user/My Tools/python" -m dbxignore daemon' in content


def test_unit_content_escapes_backslash_and_quote_in_exec_start_path() -> None:
    """Defensive: paths containing ``"`` or ``\\`` must be C-style-escaped
    inside the double-quoted ExecStart. Linux paths with these chars are
    legal but extraordinarily rare."""
    from dbxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path(r'/home/user/odd "path"/dbxignored'),
        "",
    )
    assert r'ExecStart="/home/user/odd \"path\"/dbxignored"' in content


def test_unit_content_escapes_percent_in_quoted_exec_start_path() -> None:
    """systemd expands ``%X`` specifiers in ExecStart at unit-load time
    (``%T`` → ``/tmp``, ``%h`` → home, etc.). A literal ``%`` in the install
    path must be doubled to ``%%`` so the specifier expander does not
    rewrite the executable target. This applies whether the path is quoted
    or bare; the whitespace branch is exercised here."""
    from dbxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/home/me/100% Tools/dbxignored"),
        "",
    )
    assert 'ExecStart="/home/me/100%% Tools/dbxignored"' in content


def test_unit_content_escapes_percent_in_bare_exec_start_path() -> None:
    """Even without whitespace, a ``%`` must be doubled — systemd's
    specifier expansion happens regardless of whether the path is quoted."""
    from dbxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/home/me/100%Tools/dbxignored"),
        "",
    )
    assert "ExecStart=/home/me/100%%Tools/dbxignored\n" in content
    assert 'ExecStart="' not in content


def test_unit_content_escapes_dollar_in_quoted_exec_start_path() -> None:
    """systemd expands ``$VAR`` and ``${VAR}`` in ExecStart at unit-load time
    against the unit's environment, so a literal ``$`` in the install path
    must be doubled to ``$$``. Otherwise an install path like
    ``/home/me/$TOOLS folder/dbxignored`` is silently rewritten by systemd's
    variable expander and the unit points at the wrong binary."""
    from dbxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/home/me/$TOOLS folder/dbxignored"),
        "",
    )
    assert 'ExecStart="/home/me/$$TOOLS folder/dbxignored"' in content


def test_unit_content_escapes_dollar_in_bare_exec_start_path() -> None:
    """Even without whitespace, a ``$`` must be doubled — systemd's variable
    expansion happens regardless of whether the path is quoted."""
    from dbxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/home/me/$TOOLS/dbxignored"),
        "",
    )
    assert "ExecStart=/home/me/$$TOOLS/dbxignored\n" in content
    assert 'ExecStart="' not in content


def test_unit_content_leaves_simple_exec_start_path_unquoted() -> None:
    """Standard install paths (no whitespace, no escape chars) stay
    unquoted — matches the existing on-disk shape and avoids cosmetic
    churn for the common case."""
    from dbxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/usr/local/bin/dbxignored"),
        "",
    )
    assert "ExecStart=/usr/local/bin/dbxignored\n" in content
    assert 'ExecStart="' not in content


def test_unit_content_accepts_none_environment() -> None:
    """environment=None is equivalent to omitting the argument entirely."""
    from dbxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/usr/local/bin/dbxignored"),
        environment=None,
    )
    assert "Environment=" not in content


def test_install_propagates_dbxignore_root_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When DBXIGNORE_ROOT is set in the install process's env, the
    generated unit must carry it forward — that's the fix for item 9."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DBXIGNORE_ROOT", "/home/kilo/dbx-smoke")
    monkeypatch.setattr(
        "dbxignore.install.linux_systemd.detect_invocation",
        lambda: (Path("/usr/local/bin/dbxignored"), ""),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, check, capture_output=False, text=False: subprocess.CompletedProcess(
            cmd, 0, "", ""
        ),
    )

    from dbxignore.install import linux_systemd

    linux_systemd.install_unit()

    unit_path = tmp_path / ".config" / "systemd" / "user" / "dbxignore.service"
    assert 'Environment="DBXIGNORE_ROOT=/home/kilo/dbx-smoke"' in unit_path.read_text()


def test_install_omits_environment_when_dbxignore_root_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No env var → no Environment= line. Stock-Dropbox users shouldn't see
    boilerplate they don't need."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("DBXIGNORE_ROOT", raising=False)
    monkeypatch.setattr(
        "dbxignore.install.linux_systemd.detect_invocation",
        lambda: (Path("/usr/local/bin/dbxignored"), ""),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, check, capture_output=False, text=False: subprocess.CompletedProcess(
            cmd, 0, "", ""
        ),
    )

    from dbxignore.install import linux_systemd

    linux_systemd.install_unit()

    unit_path = tmp_path / ".config" / "systemd" / "user" / "dbxignore.service"
    assert "Environment=" not in unit_path.read_text()


def test_install_ignores_empty_dbxignore_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty string means 'shell sourced a template with an unset placeholder' —
    treat as unset rather than forwarding a meaningless blank value that would
    cause ``roots.discover()`` to fall through to ``info.json`` anyway."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DBXIGNORE_ROOT", "")
    monkeypatch.setattr(
        "dbxignore.install.linux_systemd.detect_invocation",
        lambda: (Path("/usr/local/bin/dbxignored"), ""),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, check, capture_output=False, text=False: subprocess.CompletedProcess(
            cmd, 0, "", ""
        ),
    )

    from dbxignore.install import linux_systemd

    linux_systemd.install_unit()

    unit_path = tmp_path / ".config" / "systemd" / "user" / "dbxignore.service"
    assert "Environment=" not in unit_path.read_text()


def test_install_writes_unit_and_invokes_systemctl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "dbxignore.install.linux_systemd.detect_invocation",
        lambda: (Path("/usr/local/bin/dbxignored"), ""),
    )

    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str], check: bool, capture_output: bool = False, text: bool = False
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    from dbxignore.install import linux_systemd

    linux_systemd.install_unit()

    unit_path = tmp_path / ".config" / "systemd" / "user" / "dbxignore.service"
    assert unit_path.exists()
    assert "ExecStart=/usr/local/bin/dbxignored" in unit_path.read_text()

    assert calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "dbxignore.service"],
    ]


def test_uninstall_disables_removes_unit_and_reloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    unit_path = tmp_path / ".config" / "systemd" / "user" / "dbxignore.service"
    unit_path.parent.mkdir(parents=True)
    unit_path.write_text("[Unit]\nDescription=stub\n")

    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str], check: bool = False, capture_output: bool = False, text: bool = False
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    from dbxignore.install import linux_systemd

    linux_systemd.uninstall_unit()

    assert not unit_path.exists()
    assert calls == [
        ["systemctl", "--user", "disable", "--now", "dbxignore.service"],
        ["systemctl", "--user", "daemon-reload"],
    ]


def test_install_raises_when_executable_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    def _raise_not_found() -> None:
        raise RuntimeError("dbxignored not on PATH; run `uv tool install .`")

    monkeypatch.setattr(
        "dbxignore.install.linux_systemd.detect_invocation",
        _raise_not_found,
    )

    from dbxignore.install import linux_systemd

    with pytest.raises(RuntimeError, match="dbxignored not on PATH"):
        linux_systemd.install_unit()


def test_install_wraps_calledprocesserror_from_systemctl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing systemctl must raise RuntimeError, not CalledProcessError.

    cli.install / cli.uninstall catch RuntimeError; a CalledProcessError
    would escape as a raw traceback.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "dbxignore.install.linux_systemd.detect_invocation",
        lambda: (Path("/usr/local/bin/dbxignored"), ""),
    )

    def fake_run_fails(
        cmd: list[str], check: bool, capture_output: bool = False, text: bool = False
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="no user session")

    monkeypatch.setattr(subprocess, "run", fake_run_fails)

    from dbxignore.install import linux_systemd

    with pytest.raises(RuntimeError, match="daemon-reload"):
        linux_systemd.install_unit()


def test_remove_dropin_directory_removes_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drop-in dir with a user-authored override file gets removed
    wholesale on --purge cleanup."""
    monkeypatch.setenv("HOME", str(tmp_path))
    dropin_dir = tmp_path / ".config" / "systemd" / "user" / "dbxignore.service.d"
    dropin_dir.mkdir(parents=True)
    (dropin_dir / "scratch-root.conf").write_text(
        "[Service]\nEnvironment=DBXIGNORE_ROOT=/home/u/dbx\n",
        encoding="utf-8",
    )

    from dbxignore.install import linux_systemd

    result = linux_systemd.remove_dropin_directory()

    assert result == dropin_dir
    assert not dropin_dir.exists()


def test_remove_dropin_directory_absent_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drop-in dir not present → return None, no error."""
    monkeypatch.setenv("HOME", str(tmp_path))

    from dbxignore.install import linux_systemd

    assert linux_systemd.remove_dropin_directory() is None


def test_remove_dropin_directory_no_home_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """HOME unset → return None (can't locate the dir; silent skip)."""
    monkeypatch.delenv("HOME", raising=False)

    from dbxignore.install import linux_systemd

    assert linux_systemd.remove_dropin_directory() is None

"""Unit tests for the macOS launchd User Agent install backend.

Cross-platform — tests plist generation via plistlib round-trip and
launchctl command construction via subprocess argument capture. No
real launchctl required.
"""

from __future__ import annotations

import plistlib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def test_build_plist_content_has_required_keys() -> None:
    from dbxignore.install import macos_launchd

    content = macos_launchd.build_plist_content(
        label="com.kiloscheffer.dbxignore",
        program_arguments=["/usr/local/bin/dbxignored"],
        log_dir=Path("/tmp/log"),
    )
    parsed = plistlib.loads(content)
    assert parsed["Label"] == "com.kiloscheffer.dbxignore"
    assert parsed["ProgramArguments"] == ["/usr/local/bin/dbxignored"]
    assert parsed["RunAtLoad"] is True
    assert parsed["KeepAlive"] == {"SuccessfulExit": False, "Crashed": True}
    assert parsed["StandardOutPath"] == "/tmp/log/launchd.log"
    assert parsed["StandardErrorPath"] == "/tmp/log/launchd.log"
    assert "EnvironmentVariables" not in parsed


def test_build_plist_content_emits_environment_variables_when_provided() -> None:
    from dbxignore.install import macos_launchd

    content = macos_launchd.build_plist_content(
        label="com.kiloscheffer.dbxignore",
        program_arguments=["/usr/local/bin/dbxignored"],
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
        lambda: (Path("/usr/local/bin/dbxignored"), ""),
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

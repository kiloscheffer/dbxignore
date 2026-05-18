"""Windows-only tests for HKCU shell-integration registry mechanics.

Module-level double guard mirrors the project's other Windows-only
integration test files (e.g. tests/test_windows_ads_integration.py).
Cross-platform dispatcher behavior is tested separately in
tests/test_install.py.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.windows_only
if sys.platform != "win32":
    pytest.skip("HKCU registry mechanics are Windows-only", allow_module_level=True)

import winreg  # noqa: E402  # safe — module-level skip above blocks import on non-Windows

from dbxignore import state  # noqa: E402
from dbxignore.install import windows_shell  # noqa: E402
from dbxignore.install.windows_shell import _IGNORE_VERB, _RESTORE_VERB  # noqa: E402


@pytest.fixture
def isolated_reg_base(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Redirect the module's _REG_BASE to a throwaway HKCU subkey.

    Avoids colliding with a real shell-integration install on the
    developer's machine. The throwaway subtree is deleted on teardown.
    """
    test_id = uuid.uuid4().hex[:8]
    base = f"Software\\Classes\\DbxignoreTest\\{test_id}\\shell"
    monkeypatch.setattr(windows_shell, "_REG_BASE", base)
    try:
        yield base
    finally:
        # Best-effort cleanup. Walk children and delete bottom-up because
        # winreg's DeleteKey only removes leaf keys.
        _delete_subtree_silently(
            winreg.HKEY_CURRENT_USER, f"Software\\Classes\\DbxignoreTest\\{test_id}"
        )


@pytest.fixture(autouse=True)
def isolated_state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect state.user_state_dir to a per-test tmp dir.

    Autouse because install_shell_integration copies an icon under
    ``state.user_state_dir() / "icons"`` and uninstall_shell_integration
    removes from the same place; without the redirect, every test in this
    module would touch the real ``%LOCALAPPDATA%\\dbxignore\\icons\\``.
    Tests that need to inspect the redirected path declare the fixture as
    a parameter; others receive it transparently as a side effect.
    """
    state_dir = tmp_path / "dbxignore-state"
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    return state_dir


@pytest.fixture
def stub_cli_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub detect_cli_invocation to a fixed quoted path for tests."""
    monkeypatch.setattr(
        windows_shell,
        "detect_cli_invocation",
        lambda: r'"C:\test\dbxignore.exe"',
    )


def _delete_subtree_silently(root: int, path: str) -> None:
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as key:
            # Enumerate subkeys first, then recurse.
            subkeys: list[str] = []
            i = 0
            while True:
                try:
                    subkeys.append(winreg.EnumKey(key, i))
                    i += 1
                except OSError:
                    break
        for sub in subkeys:
            _delete_subtree_silently(root, f"{path}\\{sub}")
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _read_value(base: str, verb: str, value_name: str) -> str:
    """Read a string value from HKCU\\<base>\\<verb> or its \\command subkey.

    Pass value_name="(default)" to read the command-subkey default value.
    """
    if value_name == "(default)":
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{base}\\{verb}\\command") as key:
            value, _ = winreg.QueryValueEx(key, "")
            return value  # type: ignore[no-any-return, unused-ignore]
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{base}\\{verb}") as key:
        value, _ = winreg.QueryValueEx(key, value_name)
        return value  # type: ignore[no-any-return, unused-ignore]


def test_install_writes_both_verb_keys(
    isolated_reg_base: str,
    stub_cli_invocation: None,
    tmp_path: Path,
) -> None:
    """Both DbxignoreIgnore and DbxignoreRestore keys present after install."""
    windows_shell.install_shell_integration([tmp_path])

    # Both verb keys should be openable.
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{isolated_reg_base}\\{_IGNORE_VERB}"):
        pass
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{isolated_reg_base}\\{_RESTORE_VERB}"):
        pass


def test_install_sets_mui_verb_labels(
    isolated_reg_base: str,
    stub_cli_invocation: None,
    tmp_path: Path,
) -> None:
    windows_shell.install_shell_integration([tmp_path])

    assert _read_value(isolated_reg_base, _IGNORE_VERB, "MUIVerb") == "Ignore from Dropbox"
    assert _read_value(isolated_reg_base, _RESTORE_VERB, "MUIVerb") == "Restore to Dropbox"


def test_install_sets_asymmetric_command_strings(
    isolated_reg_base: str,
    stub_cli_invocation: None,
    tmp_path: Path,
) -> None:
    """Ignore: no --yes (confirms in console). Restore: --yes (one-click safe)."""
    windows_shell.install_shell_integration([tmp_path])

    ignore_cmd = _read_value(isolated_reg_base, _IGNORE_VERB, "(default)")
    restore_cmd = _read_value(isolated_reg_base, _RESTORE_VERB, "(default)")
    assert ignore_cmd == r'"C:\test\dbxignore.exe" ignore "%1"'
    assert restore_cmd == r'"C:\test\dbxignore.exe" unignore --yes "%1"'


def test_install_applies_to_query_includes_each_root(
    isolated_reg_base: str,
    stub_cli_invocation: None,
) -> None:
    roots = [Path(r"C:\Users\u\Dropbox"), Path(r"D:\Dropbox (Personal)")]
    windows_shell.install_shell_integration(roots)

    applies_to = _read_value(isolated_reg_base, _IGNORE_VERB, "AppliesTo")
    assert r'System.ItemPathDisplay:="C:\Users\u\Dropbox"' in applies_to
    assert r'System.ItemPathDisplay:~<"C:\Users\u\Dropbox\"' in applies_to
    assert r'System.ItemPathDisplay:="D:\Dropbox (Personal)"' in applies_to
    assert r'System.ItemPathDisplay:~<"D:\Dropbox (Personal)\"' in applies_to


def test_install_applies_to_same_on_both_verbs(
    isolated_reg_base: str,
    stub_cli_invocation: None,
    tmp_path: Path,
) -> None:
    windows_shell.install_shell_integration([tmp_path])

    ignore_at = _read_value(isolated_reg_base, _IGNORE_VERB, "AppliesTo")
    restore_at = _read_value(isolated_reg_base, _RESTORE_VERB, "AppliesTo")
    assert ignore_at == restore_at


def test_install_overwrites_existing_keys(
    isolated_reg_base: str,
    stub_cli_invocation: None,
    tmp_path: Path,
) -> None:
    """Re-install with different roots: AppliesTo refreshed, no stale clauses."""
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    old_root.mkdir()
    new_root.mkdir()

    windows_shell.install_shell_integration([old_root])
    windows_shell.install_shell_integration([new_root])

    applies_to = _read_value(isolated_reg_base, _IGNORE_VERB, "AppliesTo")
    assert str(new_root) in applies_to
    assert str(old_root) not in applies_to


def test_install_partial_write_failure_cleans_up(
    isolated_reg_base: str,
    isolated_state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_cli_invocation: None,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """SetValueEx raising mid-install: no verb keys remain, icon file also cleaned up."""
    call_count = {"n": 0}
    real_set = winreg.SetValueEx

    def flaky_set(key: int, name: str, *args: object) -> None:
        call_count["n"] += 1
        # Fail on the 3rd SetValueEx — the Icon write inside the Ignore verb,
        # after MUIVerb + AppliesTo have already landed. Exercises the
        # mid-verb cleanup path: partial Ignore key + copied icon file both
        # need to disappear.
        if call_count["n"] == 3:
            raise OSError(13, "Access denied (simulated)")
        real_set(key, name, *args)  # type: ignore[call-overload, unused-ignore]

    monkeypatch.setattr(winreg, "SetValueEx", flaky_set)

    with caplog.at_level("WARNING"), pytest.raises(OSError, match="Access denied"):
        windows_shell.install_shell_integration([tmp_path])

    # Neither verb key should be present after the partial-write recovery.
    for verb in (_IGNORE_VERB, _RESTORE_VERB):
        with pytest.raises(FileNotFoundError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{isolated_reg_base}\\{verb}")

    # Icon file copied by _install_icon() before the registry loop should
    # be removed by the cleanup arm too — anything else would leave a
    # dangling file under %LOCALAPPDATA% that no registry key references.
    assert not (isolated_state_dir / "icons" / "context-menu.ico").exists()

    assert any("install failed mid-write" in r.message for r in caplog.records)


def test_uninstall_removes_both_verb_keys(
    isolated_reg_base: str,
    stub_cli_invocation: None,
    tmp_path: Path,
) -> None:
    """Clean install + clean uninstall: both verb keys are gone."""
    windows_shell.install_shell_integration([tmp_path])
    windows_shell.uninstall_shell_integration()

    for verb in (_IGNORE_VERB, _RESTORE_VERB):
        with pytest.raises(FileNotFoundError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{isolated_reg_base}\\{verb}")


def test_uninstall_idempotent_when_keys_missing(
    isolated_reg_base: str,
) -> None:
    """No error when uninstall is called against a clean registry."""
    # Should not raise — the FileNotFoundError arms swallow the missing-key case.
    windows_shell.uninstall_shell_integration()
    windows_shell.uninstall_shell_integration(errors=[])


@pytest.mark.parametrize("with_errors_list", [True, False])
def test_uninstall_other_oserror_routes_to_errors_or_warning(
    isolated_reg_base: str,
    monkeypatch: pytest.MonkeyPatch,
    stub_cli_invocation: None,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    with_errors_list: bool,
) -> None:
    """Non-FileNotFoundError OSError: routed to errors list OR logged as WARNING.

    Loop must always continue to the next key — never abort partway.
    """
    windows_shell.install_shell_integration([tmp_path])

    real_delete = winreg.DeleteKey
    call_count = {"n": 0}

    def flaky_delete(root: int, path: str) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:  # First call (DbxignoreIgnore's command subkey).
            raise OSError(5, "Access denied (simulated)")
        real_delete(root, path)

    monkeypatch.setattr(winreg, "DeleteKey", flaky_delete)

    errors: list[tuple[str, str]] | None = [] if with_errors_list else None
    with caplog.at_level("WARNING"):
        windows_shell.uninstall_shell_integration(errors=errors)

    # The second verb's keys (DbxignoreRestore) should still be removed —
    # the loop didn't abort.
    with pytest.raises(FileNotFoundError):
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{isolated_reg_base}\\{_RESTORE_VERB}")

    if with_errors_list:
        # The command subkey fails (call 1). Because winreg.DeleteKey refuses to
        # delete a non-leaf key, the verb key itself also fails — so >= 1 errors
        # land (typically 2 on Windows: command + verb key both rejected).
        assert errors is not None and len(errors) >= 1
        assert errors[0][0].endswith(f"{_IGNORE_VERB}\\command")
        assert "Access denied" in errors[0][1]
        # WARNING path is NOT taken when errors list provided.
        assert not any("shell-integration uninstall" in r.message for r in caplog.records)
    else:
        assert any(
            "shell-integration uninstall" in r.message and "Access denied" in r.message
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# Verb-icon delivery: copy to %LOCALAPPDATA%\dbxignore\icons\ + Icon REG_SZ
# ---------------------------------------------------------------------------


def test_install_copies_icon_to_per_user_state_dir(
    isolated_reg_base: str,
    isolated_state_dir: Path,
    stub_cli_invocation: None,
    tmp_path: Path,
) -> None:
    """install_shell_integration copies the bundled icon to <state>/icons/."""
    windows_shell.install_shell_integration([tmp_path])

    icon_path = isolated_state_dir / "icons" / "context-menu.ico"
    assert icon_path.exists(), f"expected icon at {icon_path}"
    # Sanity check: ICO container's reserved-zero + image-type fields are the
    # first 4 bytes; image-type is 1 for .ico (vs 2 for .cur). Skipping a
    # full ICO parse — this prefix check is enough to assert "an .ico landed
    # here, not random bytes."
    assert icon_path.read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_install_writes_icon_value_on_both_verbs(
    isolated_reg_base: str,
    isolated_state_dir: Path,
    stub_cli_invocation: None,
    tmp_path: Path,
) -> None:
    """Both verb keys have an Icon REG_SZ pointing at the copied file."""
    windows_shell.install_shell_integration([tmp_path])

    expected = str(isolated_state_dir / "icons" / "context-menu.ico")
    assert _read_value(isolated_reg_base, _IGNORE_VERB, "Icon") == expected
    assert _read_value(isolated_reg_base, _RESTORE_VERB, "Icon") == expected


def test_install_overwrites_existing_icon(
    isolated_reg_base: str,
    isolated_state_dir: Path,
    stub_cli_invocation: None,
    tmp_path: Path,
) -> None:
    """Re-install replaces the existing icon contents (version upgrade)."""
    icon_path = isolated_state_dir / "icons" / "context-menu.ico"
    icon_path.parent.mkdir(parents=True)
    icon_path.write_bytes(b"stale-icon-contents-from-prior-version")

    windows_shell.install_shell_integration([tmp_path])

    # Stale sentinel must be gone — the bundled .ico has the standard
    # ICO header, not our stale payload.
    assert icon_path.read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_uninstall_removes_icon_file_and_empty_dir(
    isolated_reg_base: str,
    isolated_state_dir: Path,
    stub_cli_invocation: None,
    tmp_path: Path,
) -> None:
    """Clean install + clean uninstall: icon file + icons/ dir both gone."""
    windows_shell.install_shell_integration([tmp_path])
    icon_path = isolated_state_dir / "icons" / "context-menu.ico"
    assert icon_path.exists()  # precondition

    windows_shell.uninstall_shell_integration()

    assert not icon_path.exists()
    assert not icon_path.parent.exists()  # empty dir removed too


def test_uninstall_preserves_nonempty_icons_dir(
    isolated_reg_base: str,
    isolated_state_dir: Path,
    stub_cli_invocation: None,
    tmp_path: Path,
) -> None:
    """A stray file alongside the icon prevents the icons/ dir removal."""
    windows_shell.install_shell_integration([tmp_path])
    stray = isolated_state_dir / "icons" / "user-dropped-this.txt"
    stray.write_text("not ours", encoding="utf-8")

    windows_shell.uninstall_shell_integration()

    # Icon itself removed; dir + stray file survive.
    assert not (isolated_state_dir / "icons" / "context-menu.ico").exists()
    assert stray.exists()
    assert (isolated_state_dir / "icons").exists()


def test_uninstall_idempotent_when_icon_missing(
    isolated_reg_base: str,
    isolated_state_dir: Path,
) -> None:
    """No error when uninstall runs against a state dir with no icon."""
    # Neither install nor manual setup — _uninstall_icon() must swallow
    # the missing-file case without raising.
    windows_shell.uninstall_shell_integration()
    windows_shell.uninstall_shell_integration(errors=[])


@pytest.mark.parametrize("with_errors_list", [True, False])
def test_uninstall_icon_oserror_routes_to_errors_or_warning(
    isolated_reg_base: str,
    isolated_state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_cli_invocation: None,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    with_errors_list: bool,
) -> None:
    """Non-FileNotFoundError OSError on icon unlink: routed to errors or WARNING.

    Mirrors the contract test_uninstall_other_oserror_routes_to_errors_or_warning
    enforces for the registry-key arm.
    """
    windows_shell.install_shell_integration([tmp_path])
    icon_path = isolated_state_dir / "icons" / "context-menu.ico"

    # Monkeypatch Path.unlink so the icon-removal arm raises a non-vanished
    # OSError. Patching the method (not the instance) is the cleanest way
    # to intercept the unlink call without involving real FS perms.
    real_unlink = Path.unlink

    def flaky_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self == icon_path:
            raise OSError(13, "Icon unlink denied (simulated)")
        real_unlink(self, *args, **kwargs)  # type: ignore[arg-type, unused-ignore]

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    errors: list[tuple[str, str]] | None = [] if with_errors_list else None
    with caplog.at_level("WARNING"):
        windows_shell.uninstall_shell_integration(errors=errors)

    # The icon file is still there (unlink failed) but the registry keys
    # were removed normally — the icon arm runs AFTER the registry sweep
    # and never short-circuits the earlier work.
    assert icon_path.exists()
    for verb in (_IGNORE_VERB, _RESTORE_VERB):
        with pytest.raises(FileNotFoundError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{isolated_reg_base}\\{verb}")

    if with_errors_list:
        assert errors is not None and len(errors) == 1
        assert errors[0][0] == str(icon_path)
        assert "Icon unlink denied" in errors[0][1]
        assert not any("shell-integration uninstall" in r.message for r in caplog.records)
    else:
        assert any(
            "shell-integration uninstall" in r.message and "Icon unlink denied" in r.message
            for r in caplog.records
        )

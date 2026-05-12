"""Windows-only tests for HKCU shell-integration registry mechanics (#65).

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

from dbxignore.install import windows_shell  # noqa: E402


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
            return value  # type: ignore[no-any-return]
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{base}\\{verb}") as key:
        value, _ = winreg.QueryValueEx(key, value_name)
        return value  # type: ignore[no-any-return]


def test_install_writes_both_verb_keys(
    isolated_reg_base: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Both DbxignoreIgnore and DbxignoreRestore keys present after install."""
    monkeypatch.setattr(
        windows_shell,
        "detect_cli_invocation",
        lambda: r'"C:\test\dbxignore.exe"',
    )
    windows_shell.install_shell_integration([tmp_path])

    # Both verb keys should be openable.
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{isolated_reg_base}\\DbxignoreIgnore"):
        pass
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{isolated_reg_base}\\DbxignoreRestore"):
        pass


def test_install_sets_mui_verb_labels(
    isolated_reg_base: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        windows_shell,
        "detect_cli_invocation",
        lambda: r'"C:\test\dbxignore.exe"',
    )
    windows_shell.install_shell_integration([tmp_path])

    assert _read_value(isolated_reg_base, "DbxignoreIgnore", "MUIVerb") == "Ignore from Dropbox"
    assert _read_value(isolated_reg_base, "DbxignoreRestore", "MUIVerb") == "Restore to Dropbox"


def test_install_sets_asymmetric_command_strings(
    isolated_reg_base: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Ignore: no --yes (confirms in console). Restore: --yes (one-click safe)."""
    monkeypatch.setattr(
        windows_shell,
        "detect_cli_invocation",
        lambda: r'"C:\test\dbxignore.exe"',
    )
    windows_shell.install_shell_integration([tmp_path])

    ignore_cmd = _read_value(isolated_reg_base, "DbxignoreIgnore", "(default)")
    restore_cmd = _read_value(isolated_reg_base, "DbxignoreRestore", "(default)")
    assert ignore_cmd == r'"C:\test\dbxignore.exe" ignore "%1"'
    assert restore_cmd == r'"C:\test\dbxignore.exe" unignore --yes "%1"'


def test_install_applies_to_query_includes_each_root(
    isolated_reg_base: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        windows_shell,
        "detect_cli_invocation",
        lambda: r'"C:\test\dbxignore.exe"',
    )
    roots = [Path(r"C:\Users\u\Dropbox"), Path(r"D:\Dropbox (Personal)")]
    windows_shell.install_shell_integration(roots)

    applies_to = _read_value(isolated_reg_base, "DbxignoreIgnore", "AppliesTo")
    assert r'System.ItemPathDisplay:="C:\\Users\\u\\Dropbox"' in applies_to
    assert r'System.ItemPathDisplay:~<"C:\\Users\\u\\Dropbox\\"' in applies_to
    assert r'System.ItemPathDisplay:="D:\\Dropbox (Personal)"' in applies_to
    assert r'System.ItemPathDisplay:~<"D:\\Dropbox (Personal)\\"' in applies_to


def test_install_applies_to_same_on_both_verbs(
    isolated_reg_base: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        windows_shell,
        "detect_cli_invocation",
        lambda: r'"C:\test\dbxignore.exe"',
    )
    windows_shell.install_shell_integration([tmp_path])

    ignore_at = _read_value(isolated_reg_base, "DbxignoreIgnore", "AppliesTo")
    restore_at = _read_value(isolated_reg_base, "DbxignoreRestore", "AppliesTo")
    assert ignore_at == restore_at


def test_install_overwrites_existing_keys(
    isolated_reg_base: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Re-install with different roots: AppliesTo refreshed, no stale clauses."""
    monkeypatch.setattr(
        windows_shell,
        "detect_cli_invocation",
        lambda: r'"C:\test\dbxignore.exe"',
    )
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    old_root.mkdir()
    new_root.mkdir()

    windows_shell.install_shell_integration([old_root])
    windows_shell.install_shell_integration([new_root])

    applies_to = _read_value(isolated_reg_base, "DbxignoreIgnore", "AppliesTo")
    assert str(new_root).replace("\\", "\\\\") in applies_to
    assert str(old_root).replace("\\", "\\\\") not in applies_to

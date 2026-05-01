"""Cross-platform unit tests for the macOS xattr backend.

These tests monkeypatch the ``xattr`` module so they run on Linux and macOS
(the ``xattr`` package is not installable on Windows — no C-extension wheel
exists). The mocking exercises the full errno-handling logic without touching
real extended attributes.
"""

from __future__ import annotations

import errno
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

if sys.platform == "win32":
    pytest.skip("xattr package not installable on Windows", allow_module_level=True)

import xattr  # noqa: E402  # after skip guard

from dbxignore._backends import macos_xattr as mod  # noqa: E402  # after skip guard

# ---- helpers ----------------------------------------------------------------

# ENOATTR is BSD/macOS-specific (errno 93). Python on Linux may not define it;
# fall back to 93 so the tests exercise the right numeric code everywhere.
_ENOATTR = getattr(errno, "ENOATTR", 93)


def _oserr(err_no: int) -> OSError:
    exc = OSError(err_no, f"mock OSError[{err_no}]")
    return exc


# ---- is_ignored -------------------------------------------------------------


def test_is_ignored_returns_false_when_attr_absent(tmp_path, monkeypatch):
    """ENOATTR from getxattr → False (attribute simply not set)."""
    p = tmp_path / "file.txt"
    p.touch()

    monkeypatch.setattr(xattr, "getxattr", MagicMock(side_effect=_oserr(_ENOATTR)))
    assert mod.is_ignored(p) is False


def test_is_ignored_returns_true_when_attr_present(tmp_path, monkeypatch):
    """Successful getxattr with non-empty bytes → True."""
    p = tmp_path / "file.txt"
    p.touch()

    monkeypatch.setattr(xattr, "getxattr", MagicMock(return_value=b"1"))
    assert mod.is_ignored(p) is True


def test_is_ignored_raises_filenotfound_on_enoent(tmp_path, monkeypatch):
    """ENOENT from getxattr → FileNotFoundError (path is gone)."""
    p = tmp_path / "gone.txt"

    monkeypatch.setattr(xattr, "getxattr", MagicMock(side_effect=_oserr(errno.ENOENT)))
    with pytest.raises(FileNotFoundError):
        mod.is_ignored(p)


def test_is_ignored_propagates_unexpected_oserror(tmp_path, monkeypatch):
    """Unexpected OSError (e.g. EACCES) propagates unchanged."""
    p = tmp_path / "file.txt"
    p.touch()

    monkeypatch.setattr(xattr, "getxattr", MagicMock(side_effect=_oserr(errno.EACCES)))
    with pytest.raises(OSError) as exc_info:
        mod.is_ignored(p)
    assert exc_info.value.errno == errno.EACCES


def test_is_ignored_rejects_relative_path():
    """Relative path → ValueError before any xattr call."""
    with pytest.raises(ValueError, match="absolute"):
        mod.is_ignored(Path("relative/path.txt"))


# ---- set_ignored ------------------------------------------------------------


def test_set_ignored_calls_setxattr_with_correct_args(tmp_path, monkeypatch):
    """set_ignored calls xattr.setxattr with the detected attr name and _MARKER_VALUE."""
    p = tmp_path / "file.txt"
    p.touch()

    mock_setxattr = MagicMock()
    monkeypatch.setattr(xattr, "setxattr", mock_setxattr)
    mod.set_ignored(p)

    mock_setxattr.assert_called_once_with(
        str(p), mod._detected_attr_name(), mod._MARKER_VALUE, symlink=True
    )


def test_set_ignored_propagates_unexpected_oserror(tmp_path, monkeypatch):
    """Unexpected OSError from setxattr propagates unchanged."""
    p = tmp_path / "file.txt"
    p.touch()

    monkeypatch.setattr(xattr, "setxattr", MagicMock(side_effect=_oserr(errno.EACCES)))
    with pytest.raises(OSError) as exc_info:
        mod.set_ignored(p)
    assert exc_info.value.errno == errno.EACCES


def test_set_ignored_rejects_relative_path():
    """Relative path → ValueError before any xattr call."""
    with pytest.raises(ValueError, match="absolute"):
        mod.set_ignored(Path("relative/path.txt"))


# ---- clear_ignored ----------------------------------------------------------


def test_clear_ignored_calls_removexattr_with_correct_args(tmp_path, monkeypatch):
    """clear_ignored calls xattr.removexattr with the detected attr name and symlink=True."""
    p = tmp_path / "file.txt"
    p.touch()

    mock_removexattr = MagicMock()
    monkeypatch.setattr(xattr, "removexattr", mock_removexattr)
    mod.clear_ignored(p)

    mock_removexattr.assert_called_once_with(
        str(p), mod._detected_attr_name(), symlink=True
    )


def test_clear_ignored_is_noop_when_attr_absent(tmp_path, monkeypatch):
    """ENOATTR from removexattr → no-op (already cleared)."""
    p = tmp_path / "file.txt"
    p.touch()

    monkeypatch.setattr(xattr, "removexattr", MagicMock(side_effect=_oserr(_ENOATTR)))
    mod.clear_ignored(p)  # must not raise


def test_clear_ignored_is_noop_when_path_gone(tmp_path, monkeypatch):
    """ENOENT from removexattr → no-op (path is already gone)."""
    p = tmp_path / "gone.txt"

    monkeypatch.setattr(xattr, "removexattr", MagicMock(side_effect=_oserr(errno.ENOENT)))
    mod.clear_ignored(p)  # must not raise


def test_clear_ignored_propagates_unexpected_oserror(tmp_path, monkeypatch):
    """Unexpected OSError from removexattr propagates unchanged."""
    p = tmp_path / "file.txt"
    p.touch()

    monkeypatch.setattr(
        xattr, "removexattr", MagicMock(side_effect=_oserr(errno.EACCES))
    )
    with pytest.raises(OSError) as exc_info:
        mod.clear_ignored(p)
    assert exc_info.value.errno == errno.EACCES


def test_clear_ignored_rejects_relative_path():
    """Relative path → ValueError before any xattr call."""
    with pytest.raises(ValueError, match="absolute"):
        mod.clear_ignored(Path("relative/path.txt"))


# ---- _detected_attr_name (Dropbox sync mode auto-detection) -----------------


@pytest.fixture
def reset_attr_cache(monkeypatch):
    """Reset `_attr_name_cache` to None for each test so detection re-runs.

    Without this, the first test to call `_detected_attr_name()` would
    populate the cache, and subsequent tests would see whatever value
    that test happened to monkeypatch — order-dependent flakiness.
    """
    monkeypatch.setattr(mod, "_attr_name_cache", None)
    yield
    monkeypatch.setattr(mod, "_attr_name_cache", None)


def _fake_pluginkit(stdout: str = "", side_effect: Exception | None = None):
    """Build a `subprocess.run` replacement that simulates `pluginkit` output.

    Returns a function suitable for `monkeypatch.setattr("subprocess.run", ...)`.
    If `side_effect` is given, the fake raises it instead of returning a result —
    used to test the fallback paths (FileNotFoundError on non-macOS hosts,
    TimeoutExpired on a hung pluginkit).
    """
    def fake(args, **kwargs):
        if side_effect is not None:
            raise side_effect
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout=stdout, stderr=""
        )
    return fake


# ---- Primary detection path (pluginkit registry query) ----------------------


def test_detected_attr_name_fileprovider_when_pluginkit_lists_extension_default_state(
    monkeypatch, reset_attr_cache
):
    """Default-state pluginkit output (whitespace prefix, no '+' or '-') →
    File Provider. This is the common case — most users never visit System
    Settings → Login Items & Extensions, so the extension stays in PluginKit's
    default-enabled state with no prefix character.
    """
    fake_stdout = "     com.getdropbox.dropbox.fileprovider(250.4.3245)\n"
    monkeypatch.setattr("subprocess.run", _fake_pluginkit(stdout=fake_stdout))
    assert mod._detected_attr_name() == mod.ATTR_FILEPROVIDER


def test_detected_attr_name_legacy_when_pluginkit_shows_extension_disabled(
    monkeypatch, reset_attr_cache
):
    """Disabled-state pluginkit output ('-' prefix) → legacy mode.

    User has explicitly disabled the Dropbox File Provider extension via
    `pluginkit -e ignore` or System Settings. In that state Dropbox falls
    back to legacy sync (or doesn't sync), so we want the legacy attr.
    """
    fake_stdout = "-    com.getdropbox.dropbox.fileprovider(250.4.3245)\n"
    monkeypatch.setattr("subprocess.run", _fake_pluginkit(stdout=fake_stdout))
    assert mod._detected_attr_name() == mod.ATTR_LEGACY


# ---- Fallback detection path (pluginkit unavailable / errored) --------------


def test_detected_attr_name_falls_back_to_path_when_pluginkit_returns_empty(
    tmp_path, monkeypatch, reset_attr_cache
):
    """Empty pluginkit output (extension not registered) → path heuristic →
    legacy if no CloudStorage folder.
    """
    monkeypatch.setattr("subprocess.run", _fake_pluginkit(stdout=""))
    monkeypatch.setenv("HOME", str(tmp_path))
    # Note: NOT creating Library/CloudStorage/Dropbox under tmp_path
    assert mod._detected_attr_name() == mod.ATTR_LEGACY


def test_detected_attr_name_falls_back_to_path_finds_fileprovider_via_cloudstorage(
    tmp_path, monkeypatch, reset_attr_cache
):
    """pluginkit empty + CloudStorage folder exists → File Provider via fallback.

    Covers the case where pluginkit's registry isn't reachable but the
    canonical default-path File Provider folder is present (e.g., test
    environments on Linux that ran pluginkit and got empty back).
    """
    monkeypatch.setattr(
        "subprocess.run",
        _fake_pluginkit(side_effect=FileNotFoundError("pluginkit not found")),
    )
    cloud_storage = tmp_path / "Library" / "CloudStorage" / "Dropbox"
    cloud_storage.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert mod._detected_attr_name() == mod.ATTR_FILEPROVIDER


def test_detected_attr_name_falls_back_when_pluginkit_times_out(
    tmp_path, monkeypatch, reset_attr_cache
):
    """TimeoutExpired (pluginkit hung) → fall through to path heuristic."""
    monkeypatch.setattr(
        "subprocess.run",
        _fake_pluginkit(side_effect=subprocess.TimeoutExpired(cmd="pluginkit", timeout=2)),
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    # No CloudStorage folder → legacy via fallback
    assert mod._detected_attr_name() == mod.ATTR_LEGACY


def test_detected_attr_name_falls_back_to_legacy_when_home_unset(
    monkeypatch, reset_attr_cache
):
    """No HOME and pluginkit empty → defensive legacy default."""
    monkeypatch.setattr("subprocess.run", _fake_pluginkit(stdout=""))
    monkeypatch.delenv("HOME", raising=False)
    assert mod._detected_attr_name() == mod.ATTR_LEGACY


# ---- Caching behavior -------------------------------------------------------


def test_detected_attr_name_caches_first_result(monkeypatch, reset_attr_cache):
    """First call invokes detection; subsequent calls hit the cache.

    Critical for the per-file reconcile loop's performance — the daemon
    processes thousands of paths per sweep, and re-invoking pluginkit on
    every marker call would add ~50ms × N to the sweep wall-clock.
    """
    call_count = 0

    def fake_run(args, **kwargs):
        nonlocal call_count
        call_count += 1
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="     com.getdropbox.dropbox.fileprovider(250.4.3245)\n",
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", fake_run)

    first = mod._detected_attr_name()
    second = mod._detected_attr_name()
    third = mod._detected_attr_name()

    assert first == second == third == mod.ATTR_FILEPROVIDER
    assert call_count == 1, "subprocess.run should only be called once"


# ---- File Provider mode end-to-end ------------------------------------------


@pytest.fixture
def fileprovider_mode(monkeypatch):
    """Force `_detected_attr_name()` to return ATTR_FILEPROVIDER for one test.

    Sets the cache directly so the tests don't depend on filesystem state —
    cleaner than constructing the full ~/Library/CloudStorage/Dropbox path
    via tmp_path. Each test in this section asserts the xattr backend
    routes the correct attribute name through to the underlying xattr call.
    """
    monkeypatch.setattr(mod, "_attr_name_cache", mod.ATTR_FILEPROVIDER)
    yield
    monkeypatch.setattr(mod, "_attr_name_cache", None)


def test_is_ignored_uses_fileprovider_attr_in_fileprovider_mode(
    tmp_path, monkeypatch, fileprovider_mode
):
    """In File Provider mode, is_ignored reads `com.apple.fileprovider.ignore#P`."""
    p = tmp_path / "file.txt"
    p.touch()

    mock_getxattr = MagicMock(return_value=b"1")
    monkeypatch.setattr(xattr, "getxattr", mock_getxattr)

    assert mod.is_ignored(p) is True
    mock_getxattr.assert_called_once_with(str(p), mod.ATTR_FILEPROVIDER, symlink=True)


def test_set_ignored_writes_fileprovider_attr_in_fileprovider_mode(
    tmp_path, monkeypatch, fileprovider_mode
):
    """In File Provider mode, set_ignored writes `com.apple.fileprovider.ignore#P`."""
    p = tmp_path / "file.txt"
    p.touch()

    mock_setxattr = MagicMock()
    monkeypatch.setattr(xattr, "setxattr", mock_setxattr)
    mod.set_ignored(p)

    mock_setxattr.assert_called_once_with(
        str(p), mod.ATTR_FILEPROVIDER, mod._MARKER_VALUE, symlink=True
    )


def test_clear_ignored_removes_fileprovider_attr_in_fileprovider_mode(
    tmp_path, monkeypatch, fileprovider_mode
):
    """In File Provider mode, clear_ignored removes `com.apple.fileprovider.ignore#P`."""
    p = tmp_path / "file.txt"
    p.touch()

    mock_removexattr = MagicMock()
    monkeypatch.setattr(xattr, "removexattr", mock_removexattr)
    mod.clear_ignored(p)

    mock_removexattr.assert_called_once_with(
        str(p), mod.ATTR_FILEPROVIDER, symlink=True
    )

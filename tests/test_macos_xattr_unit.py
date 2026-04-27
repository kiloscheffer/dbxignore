"""Cross-platform unit tests for the macOS xattr backend.

These tests monkeypatch the ``xattr`` module so they run on Linux and macOS
(the ``xattr`` package is not installable on Windows — no C-extension wheel
exists). The mocking exercises the full errno-handling logic without touching
real extended attributes.
"""

from __future__ import annotations

import errno
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
    """set_ignored calls xattr.setxattr with ATTR_NAME and _MARKER_VALUE."""
    p = tmp_path / "file.txt"
    p.touch()

    mock_setxattr = MagicMock()
    monkeypatch.setattr(xattr, "setxattr", mock_setxattr)
    mod.set_ignored(p)

    mock_setxattr.assert_called_once_with(
        str(p), mod.ATTR_NAME, mod._MARKER_VALUE, symlink=True
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
    """clear_ignored calls xattr.removexattr with ATTR_NAME and symlink=True."""
    p = tmp_path / "file.txt"
    p.touch()

    mock_removexattr = MagicMock()
    monkeypatch.setattr(xattr, "removexattr", mock_removexattr)
    mod.clear_ignored(p)

    mock_removexattr.assert_called_once_with(
        str(p), mod.ATTR_NAME, symlink=True
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

"""Integration tests for the Linux user.com.dropbox.ignored xattr backend."""

from __future__ import annotations

import os
import sys

import pytest

pytestmark = pytest.mark.linux_only

if not sys.platform.startswith("linux"):
    pytest.skip("user.* xattrs are Linux-only in v0.2", allow_module_level=True)

from dbxignore._backends import linux_xattr  # noqa: E402, I001  # must come after sys.platform skip guard
from typing import TYPE_CHECKING  # noqa: E402  # after skip guard

if TYPE_CHECKING:
    from pathlib import Path


def _xattr_supported(path) -> bool:
    """Probe whether the filesystem under `path` accepts user.* xattrs."""
    probe = path / ".xattr_probe"
    probe.touch()
    try:
        os.setxattr(os.fspath(probe), "user.dropboxignore.probe", b"1")
    except OSError:
        return False
    finally:
        probe.unlink(missing_ok=True)
    return True


@pytest.fixture(autouse=True)
def _require_xattr_fs(tmp_path: Path) -> None:
    if not _xattr_supported(tmp_path):
        pytest.skip(f"tmp_path {tmp_path} rejects user.* xattrs — cannot test")


def test_roundtrip_on_file(tmp_path: Path) -> None:
    p = tmp_path / "file.txt"
    p.touch()
    assert linux_xattr.is_ignored(p) is False
    linux_xattr.set_ignored(p)
    assert linux_xattr.is_ignored(p) is True
    linux_xattr.clear_ignored(p)
    assert linux_xattr.is_ignored(p) is False


def test_roundtrip_on_directory(tmp_path: Path) -> None:
    d = tmp_path / "subdir"
    d.mkdir()
    assert linux_xattr.is_ignored(d) is False
    linux_xattr.set_ignored(d)
    assert linux_xattr.is_ignored(d) is True
    linux_xattr.clear_ignored(d)
    assert linux_xattr.is_ignored(d) is False


def test_clear_is_idempotent_on_unmarked_path(tmp_path: Path) -> None:
    p = tmp_path / "unmarked.txt"
    p.touch()
    linux_xattr.clear_ignored(p)
    assert linux_xattr.is_ignored(p) is False


def test_is_ignored_on_nonexistent_path_raises_filenotfound(tmp_path: Path) -> None:
    p = tmp_path / "does-not-exist.txt"
    with pytest.raises(FileNotFoundError):
        linux_xattr.is_ignored(p)


def test_requires_absolute_path(tmp_path: Path) -> None:
    from pathlib import Path

    rel = Path("relative/path.txt")
    with pytest.raises(ValueError, match="absolute"):
        linux_xattr.is_ignored(rel)
    with pytest.raises(ValueError, match="absolute"):
        linux_xattr.set_ignored(rel)
    with pytest.raises(ValueError, match="absolute"):
        linux_xattr.clear_ignored(rel)


def test_set_on_symlink_raises_permission_error(tmp_path: Path) -> None:
    """Linux refuses user.* xattrs on symlinks (EPERM).

    set_ignored(symlink) must raise PermissionError so reconcile's existing
    PermissionError arm can log + skip. The symlink's target must remain
    untouched — the set call fails before any side effect on the target.
    """
    target = tmp_path / "target.txt"
    target.touch()
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    with pytest.raises(PermissionError):
        linux_xattr.set_ignored(link)

    with pytest.raises(PermissionError):
        linux_xattr.clear_ignored(link)

    # Read on a symlink returns ENODATA -> False (no xattr set).
    assert linux_xattr.is_ignored(link) is False
    # Target was never marked — the set call aborted on the link.
    assert linux_xattr.is_ignored(target) is False

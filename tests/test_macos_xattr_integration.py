"""Integration tests for the macOS com.dropbox.ignored xattr backend.

These tests exercise real xattr I/O on APFS via the ``xattr`` PyPI package.
They require a macOS host — on all other platforms the module is skipped at
collection time.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.macos_only

if sys.platform != "darwin":
    pytest.skip("requires macOS xattrs", allow_module_level=True)

from dbxignore._backends import macos_xattr  # noqa: E402, I001  # must come after skip guard


def test_roundtrip_on_file(tmp_path: Path) -> None:
    p = tmp_path / "file.txt"
    p.touch()
    assert macos_xattr.is_ignored(p) is False
    macos_xattr.set_ignored(p)
    assert macos_xattr.is_ignored(p) is True
    macos_xattr.clear_ignored(p)
    assert macos_xattr.is_ignored(p) is False


def test_roundtrip_on_directory(tmp_path: Path) -> None:
    d = tmp_path / "subdir"
    d.mkdir()
    assert macos_xattr.is_ignored(d) is False
    macos_xattr.set_ignored(d)
    assert macos_xattr.is_ignored(d) is True
    macos_xattr.clear_ignored(d)
    assert macos_xattr.is_ignored(d) is False


def test_clear_is_idempotent_on_unmarked_path(tmp_path: Path) -> None:
    """clear_ignored on a path with no xattr set is a no-op (no exception)."""
    p = tmp_path / "unmarked.txt"
    p.touch()
    macos_xattr.clear_ignored(p)  # must not raise
    assert macos_xattr.is_ignored(p) is False


def test_is_ignored_on_nonexistent_path_raises_filenotfound(tmp_path: Path) -> None:
    """is_ignored on a path that does not exist raises FileNotFoundError."""
    p = tmp_path / "does-not-exist.txt"
    with pytest.raises(FileNotFoundError):
        macos_xattr.is_ignored(p)


def test_symlink_marks_link_not_target(tmp_path: Path) -> None:
    """macOS XATTR_NOFOLLOW marks the symlink itself, not the target.

    This is the intentional macOS-vs-Linux behavioral divergence: Linux
    refuses ``user.*`` xattrs on symlinks (EPERM), but macOS allows marking
    symlinks directly via XATTR_NOFOLLOW. The symlink gets marked; the target
    remains unmarked.
    """
    target = tmp_path / "target.txt"
    target.touch()
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    # Neither is marked before we start.
    assert macos_xattr.is_ignored(target) is False

    # Mark the symlink — should succeed on macOS (unlike Linux).
    macos_xattr.set_ignored(link)

    # The link itself is now marked.
    assert macos_xattr.is_ignored(link) is True
    # The target is NOT marked — XATTR_NOFOLLOW prevents following the link.
    assert macos_xattr.is_ignored(target) is False

    # Clear the mark on the link — should succeed.
    macos_xattr.clear_ignored(link)
    assert macos_xattr.is_ignored(link) is False


def test_requires_absolute_path(tmp_path: Path) -> None:
    """All three functions reject relative paths with ValueError."""
    rel = Path("relative/path.txt")
    with pytest.raises(ValueError, match="absolute"):
        macos_xattr.is_ignored(rel)
    with pytest.raises(ValueError, match="absolute"):
        macos_xattr.set_ignored(rel)
    with pytest.raises(ValueError, match="absolute"):
        macos_xattr.clear_ignored(rel)

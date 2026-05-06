"""End-to-end: real xattrs + real reconcile_subtree on a Linux tmp tree."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import WriteFile

pytestmark = pytest.mark.linux_only

if not sys.platform.startswith("linux"):
    pytest.skip("Linux-only smoke test", allow_module_level=True)


def _xattr_supported(path) -> bool:
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
        pytest.skip(f"tmp_path {tmp_path} rejects user.* xattrs")


def test_apply_marks_and_clears_via_real_xattrs(tmp_path: Path, write_file: WriteFile) -> None:
    from dbxignore import markers
    from dbxignore.reconcile import reconcile_subtree
    from dbxignore.rules import RuleCache

    root = tmp_path
    write_file(root / ".dropboxignore", "build/\nsecrets.env\n")
    build_dir = root / "build"
    build_dir.mkdir()
    write_file(build_dir / "artifact.bin", "x")
    write_file(root / "secrets.env", "TOKEN=...")
    keeper = write_file(root / "src" / "keep.py", "print('hi')")

    cache = RuleCache()
    cache.load_root(root)

    report = reconcile_subtree(root, root, cache)

    # build/ itself is marked; descent into it is pruned, so its contents
    # are not individually marked (same contract as Windows).
    assert markers.is_ignored(build_dir) is True
    assert markers.is_ignored(root / "secrets.env") is True
    assert markers.is_ignored(keeper) is False
    assert report.marked >= 2
    assert report.errors == []

    # Drop the rule and re-sweep. The marker should be cleared.
    (root / ".dropboxignore").write_text("", encoding="utf-8")
    cache = RuleCache()
    cache.load_root(root)
    report2 = reconcile_subtree(root, root, cache)

    assert markers.is_ignored(build_dir) is False
    assert markers.is_ignored(root / "secrets.env") is False
    assert report2.cleared >= 2


def test_dropboxignore_itself_never_marked(tmp_path: Path, write_file: WriteFile) -> None:
    from dbxignore import markers
    from dbxignore.reconcile import reconcile_subtree
    from dbxignore.rules import RuleCache

    root = tmp_path
    # Rule tries to ignore .dropboxignore itself; must be overridden.
    write_file(root / ".dropboxignore", ".dropboxignore\n")

    cache = RuleCache()
    cache.load_root(root)
    reconcile_subtree(root, root, cache)

    assert markers.is_ignored(root / ".dropboxignore") is False

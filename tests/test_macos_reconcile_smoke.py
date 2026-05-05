"""End-to-end mark/clear smoke for macOS — exercises the dispatch + backend
through the real reconcile path on real xattrs.

Runs only on macOS — uses real APFS xattrs against tmp_path.
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.macos_only

if sys.platform != "darwin":
    pytest.skip("requires macOS xattrs", allow_module_level=True)

from dbxignore import markers, reconcile, rules  # noqa: E402, I001  # after skip guard


def test_apply_marks_directory_matching_rule(tmp_path):
    root = tmp_path
    (root / ".dropboxignore").write_text("build/\n")
    (root / "build").mkdir()
    (root / "build" / "child.txt").write_text("hi")

    cache = rules.RuleCache()
    cache.load_root(root)

    report = reconcile.reconcile_subtree(root, root, cache)

    assert markers.is_ignored(root / "build") is True
    assert report.errors == []


def test_apply_clears_marker_when_rule_removed(tmp_path):
    root = tmp_path
    rule_file = root / ".dropboxignore"
    rule_file.write_text("build/\n")
    (root / "build").mkdir()

    cache = rules.RuleCache()
    cache.load_root(root)
    reconcile.reconcile_subtree(root, root, cache)
    assert markers.is_ignored(root / "build") is True

    # Remove the rule
    rule_file.write_text("")
    cache.reload_file(rule_file)
    reconcile.reconcile_subtree(root, root, cache)
    assert markers.is_ignored(root / "build") is False

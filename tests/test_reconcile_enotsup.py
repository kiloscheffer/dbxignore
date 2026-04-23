"""Reconcile must log + skip paths on filesystems that reject the ignore marker."""

from __future__ import annotations

import errno
import logging

from dbxignore import reconcile
from dbxignore.rules import RuleCache


def _raise_enotsup(*_args, **_kwargs):
    raise OSError(errno.ENOTSUP, "Operation not supported")


def test_enotsup_on_set_is_reported_not_raised(
    fake_markers, tmp_path, write_file, monkeypatch, caplog
):
    root = tmp_path
    write_file(root / ".dropboxignore", "ignoreme.txt\n")
    target = write_file(root / "ignoreme.txt")

    # fake_markers starts clean; override set_ignored to raise ENOTSUP.
    monkeypatch.setattr(fake_markers, "set_ignored", _raise_enotsup)

    cache = RuleCache()
    cache.load_root(root)

    with caplog.at_level(logging.WARNING, logger="dbxignore.reconcile"):
        report = reconcile.reconcile_subtree(root, root, cache)

    assert report.marked == 0
    assert len(report.errors) == 1
    errored_path, message = report.errors[0]
    assert errored_path.resolve() == target.resolve()
    assert "unsupported" in message.lower()
    assert any("does not support ignore markers" in r.message for r in caplog.records)


def test_enotsup_on_clear_is_reported_not_raised(
    fake_markers, tmp_path, write_file, monkeypatch, caplog
):
    root = tmp_path
    # Pre-mark a path that no rule covers, so reconcile would clear it.
    target = write_file(root / "manually_marked.txt")
    fake_markers.set_ignored(target)
    # Sanity: no rules → reconcile will try to clear.
    (root / ".dropboxignore").write_text("", encoding="utf-8")

    monkeypatch.setattr(fake_markers, "clear_ignored", _raise_enotsup)

    cache = RuleCache()
    cache.load_root(root)

    with caplog.at_level(logging.WARNING, logger="dbxignore.reconcile"):
        report = reconcile.reconcile_subtree(root, root, cache)

    assert report.cleared == 0
    assert any(p.resolve() == target.resolve() for p, _ in report.errors)

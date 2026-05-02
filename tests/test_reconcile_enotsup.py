"""Reconcile must log + skip paths on filesystems that reject the ignore marker."""

from __future__ import annotations

import errno
import logging
from pathlib import Path

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


def _raise_eio(*_args, **_kwargs):
    raise OSError(errno.EIO, "Input/output error")


def test_oserror_on_read_is_reported_not_raised(
    fake_markers, tmp_path, write_file, monkeypatch, caplog
):
    """Read-side OSError (e.g. EIO on a flaky drive) must not kill the sweep."""
    root = tmp_path
    target = write_file(root / "file.txt")
    (root / ".dropboxignore").write_text("", encoding="utf-8")

    monkeypatch.setattr(fake_markers, "is_ignored", _raise_eio)

    cache = RuleCache()
    cache.load_root(root)

    with caplog.at_level(logging.WARNING, logger="dbxignore.reconcile"):
        report = reconcile.reconcile_subtree(root, root, cache)

    assert any(p.resolve() == target.resolve() for p, _ in report.errors)
    assert any(f"errno={errno.EIO}" in msg for _, msg in report.errors)
    assert any("I/O error reading marker" in r.message for r in caplog.records)


def test_enotsup_on_read_is_reported_not_raised(
    fake_markers, tmp_path, write_file, monkeypatch, caplog
):
    """Read-side ENOTSUP must also be handled — not just the write-side."""
    root = tmp_path
    target = write_file(root / "file.txt")
    (root / ".dropboxignore").write_text("", encoding="utf-8")

    monkeypatch.setattr(fake_markers, "is_ignored", _raise_enotsup)

    cache = RuleCache()
    cache.load_root(root)

    with caplog.at_level(logging.WARNING, logger="dbxignore.reconcile"):
        report = reconcile.reconcile_subtree(root, root, cache)

    assert any(p.resolve() == target.resolve() for p, _ in report.errors)
    assert any(f"errno={errno.ENOTSUP}" in msg for _, msg in report.errors)


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


def test_enotsup_on_directory_clear_prunes_subtree(
    fake_markers, tmp_path, write_file, monkeypatch
):
    """ENOTSUP when clearing an already-marked directory must still prune.

    Item #41: the write-side ENOTSUP arm previously returned ``None``,
    which is falsy → the dirnames-pruning filter (``not _reconcile_path
    (...)``) kept the directory and the walk descended into a subtree
    whose marker state we couldn't change. Each child then re-failed
    with ENOTSUP, spamming the log. The fix returns ``currently_ignored``
    (matching ``PermissionError``'s behavior) so a still-marked directory
    correctly prunes its descendants from the walk.

    Setup: pre-mark both a directory and a file inside it; have no rules
    so reconcile wants to clear both; intercept ``clear_ignored`` so it
    records each attempt and raises ENOTSUP. Assertion: only the
    directory's clear is attempted; the child's clear isn't reached
    because the still-marked directory pruned the walk.
    """
    root = tmp_path
    marked_dir = root / "marked_dir"
    marked_dir.mkdir()
    child_file = write_file(marked_dir / "child.txt")
    fake_markers.set_ignored(marked_dir)
    fake_markers.set_ignored(child_file)
    (root / ".dropboxignore").write_text("", encoding="utf-8")

    clear_attempts: list[Path] = []

    def recording_failing_clear(path):
        clear_attempts.append(path.resolve())
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr(fake_markers, "clear_ignored", recording_failing_clear)

    cache = RuleCache()
    cache.load_root(root)
    reconcile.reconcile_subtree(root, root, cache)

    assert marked_dir.resolve() in clear_attempts
    assert child_file.resolve() not in clear_attempts, (
        "subtree pruning failed: walk descended into the still-marked "
        "directory and attempted to clear its child"
    )

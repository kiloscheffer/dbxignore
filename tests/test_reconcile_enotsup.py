"""Reconcile must log + skip paths on filesystems that reject the ignore marker."""

from __future__ import annotations

import errno
import logging
from typing import TYPE_CHECKING

import pytest

from dbxignore import reconcile
from dbxignore.rules import RuleCache

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import FakeMarkers, WriteFile


def _raise_enotsup(*_args: object, **_kwargs: object) -> None:
    raise OSError(errno.ENOTSUP, "Operation not supported")


def test_enotsup_on_set_is_reported_not_raised(
    fake_markers: FakeMarkers,
    tmp_path: Path,
    write_file: WriteFile,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
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


def _raise_eio(*_args: object, **_kwargs: object) -> None:
    raise OSError(errno.EIO, "Input/output error")


def test_oserror_on_read_is_reported_not_raised(
    fake_markers: FakeMarkers,
    tmp_path: Path,
    write_file: WriteFile,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
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
    fake_markers: FakeMarkers,
    tmp_path: Path,
    write_file: WriteFile,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
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
    fake_markers: FakeMarkers,
    tmp_path: Path,
    write_file: WriteFile,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
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
    fake_markers: FakeMarkers,
    tmp_path: Path,
    write_file: WriteFile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A still-marked directory whose clear fails with ENOTSUP must prune.

    The walk-pruning filter treats truthy returns as "ignored, prune".
    Returning the pre-existing ``currently_ignored`` (True here) keeps
    descendants out of the walk; returning ``None`` (the bug) descended
    into them and re-failed for each child.
    """
    root = tmp_path
    marked_dir = root / "marked_dir"
    marked_dir.mkdir()
    child_file = write_file(marked_dir / "child.txt")
    fake_markers.set_ignored(marked_dir)
    fake_markers.set_ignored(child_file)
    (root / ".dropboxignore").write_text("", encoding="utf-8")

    clear_attempts: list[Path] = []

    def recording_failing_clear(path: Path) -> None:
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


def test_eio_on_set_is_reported_not_raised(
    fake_markers: FakeMarkers,
    tmp_path: Path,
    write_file: WriteFile,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Write-side EIO (e.g. transient network-drive failure) must not kill the sweep.

    Symmetric to `test_oserror_on_read_is_reported_not_raised` on the read side.
    """
    root = tmp_path
    write_file(root / ".dropboxignore", "ignoreme.txt\n")
    target = write_file(root / "ignoreme.txt")

    monkeypatch.setattr(fake_markers, "set_ignored", _raise_eio)

    cache = RuleCache()
    cache.load_root(root)

    with caplog.at_level(logging.WARNING, logger="dbxignore.reconcile"):
        report = reconcile.reconcile_subtree(root, root, cache)

    assert report.marked == 0
    assert any(p.resolve() == target.resolve() for p, _ in report.errors)
    assert any(f"errno={errno.EIO}" in msg for _, msg in report.errors)
    assert any("I/O error writing marker" in r.message for r in caplog.records)


def test_eio_on_clear_is_reported_not_raised(
    fake_markers: FakeMarkers,
    tmp_path: Path,
    write_file: WriteFile,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Write-side EIO during clear must not kill the sweep."""
    root = tmp_path
    target = write_file(root / "manually_marked.txt")
    fake_markers.set_ignored(target)
    (root / ".dropboxignore").write_text("", encoding="utf-8")

    monkeypatch.setattr(fake_markers, "clear_ignored", _raise_eio)

    cache = RuleCache()
    cache.load_root(root)

    with caplog.at_level(logging.WARNING, logger="dbxignore.reconcile"):
        report = reconcile.reconcile_subtree(root, root, cache)

    assert report.cleared == 0
    assert any(p.resolve() == target.resolve() for p, _ in report.errors)
    assert any(f"errno={errno.EIO}" in msg for _, msg in report.errors)


def test_typeerror_on_set_propagates(
    fake_markers: FakeMarkers,
    tmp_path: Path,
    write_file: WriteFile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-OSError write failures (real code bugs) still propagate.

    Pins the "we don't suppress unknown causes" contract: the broad-OSError
    arm must be limited to OSError, not bare Exception. A future refactor
    that widened to `except Exception` would fail this test.
    """
    root = tmp_path
    write_file(root / ".dropboxignore", "ignoreme.txt\n")
    write_file(root / "ignoreme.txt")

    def _raise_typeerror(*_args: object, **_kwargs: object) -> None:
        raise TypeError("synthetic bug")

    monkeypatch.setattr(fake_markers, "set_ignored", _raise_typeerror)

    cache = RuleCache()
    cache.load_root(root)

    with pytest.raises(TypeError, match="synthetic bug"):
        reconcile.reconcile_subtree(root, root, cache)

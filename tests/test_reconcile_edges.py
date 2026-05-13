from pathlib import Path

import pytest

from dbxignore import reconcile
from dbxignore.rules import RuleCache
from tests.conftest import FakeMarkers, WriteFile


def test_skips_descendants_of_already_ignored_directory(
    tmp_path: Path, fake_markers: FakeMarkers, write_file: WriteFile
) -> None:
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "deep").mkdir()
    (tmp_path / "build" / "a.o").touch()
    fake_markers.set_ignored(tmp_path / "build")  # pre-ignored

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    # 'deep' and 'a.o' must not be touched (we skipped into build/).
    assert (tmp_path / "build" / "deep").resolve() not in fake_markers._ignored
    # Report counts no new marks/clears — build/ was already correct.
    assert report.marked == 0
    assert report.cleared == 0


def test_permission_error_is_logged_and_counted_not_raised(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    write_file: WriteFile,
) -> None:
    import logging

    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "other").mkdir()

    class FailingADS:
        def __init__(self) -> None:
            self._ignored: set[Path] = set()

        def is_ignored(self, path: Path) -> bool:
            return False

        def set_ignored(self, path: Path) -> None:
            if path.name == "build":
                raise PermissionError("locked")
            self._ignored.add(path.resolve())

        def clear_ignored(self, path: Path) -> None:
            self._ignored.discard(path.resolve())

    failing = FailingADS()
    monkeypatch.setattr(reconcile, "markers", failing)

    cache = RuleCache()
    cache.load_root(tmp_path)

    with caplog.at_level(logging.WARNING, logger="dbxignore.reconcile"):
        report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert len(report.errors) == 1
    err_path, err_msg = report.errors[0]
    assert err_path.name == "build"
    assert "locked" in err_msg
    assert any(r.levelname == "WARNING" and "locked" in r.message for r in caplog.records)


def test_file_not_found_during_walk_is_silently_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_file: WriteFile
) -> None:
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()

    class DisappearingADS:
        def is_ignored(self, path: Path) -> bool:
            raise FileNotFoundError("gone")

        def set_ignored(self, path: Path) -> None:
            pass

        def clear_ignored(self, path: Path) -> None:
            pass

    monkeypatch.setattr(reconcile, "markers", DisappearingADS())

    cache = RuleCache()
    cache.load_root(tmp_path)

    # Must not raise.
    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)
    # FileNotFoundError is expected traffic, not an error.
    assert report.errors == []


def test_sweep_clears_markers_when_dropboxignore_was_deleted_offline(
    tmp_path: Path, fake_markers: FakeMarkers
) -> None:
    """Offline-recovery integration: if a .dropboxignore was deleted while
    the daemon was down, the next startup sweep must clear every ADS marker
    it used to justify. No rules in cache + marker on disk = clear."""
    # Prior-daemon-run state: build/ is ignored; deep/ inside it is ignored
    # too (descendant-of-ignored is skipped during normal reconcile, but if
    # an old sweep marked it directly, the marker is on disk).
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "deep").mkdir()
    fake_markers.set_ignored(tmp_path / "build")
    fake_markers.set_ignored(tmp_path / "build" / "deep")

    # Fresh daemon startup: no .dropboxignore on disk, empty cache, sweep.
    cache = RuleCache()
    cache.load_root(tmp_path)
    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert not fake_markers.is_ignored(tmp_path / "build")
    assert not fake_markers.is_ignored(tmp_path / "build" / "deep")
    assert report.cleared == 2


def test_symlinked_walk_root_is_treated_as_leaf(
    tmp_path: Path, fake_markers: FakeMarkers, write_file: WriteFile
) -> None:
    """A symlinked directory passed as the walk root must NOT have its
    target traversed. ``os.walk(top, followlinks=False)`` still follows
    the walk root itself when ``top`` is a symlink — only subdirectory
    symlinks are gated. Without the in-reconcile guard, a DIR_CREATE
    event for a symlink inside a watched root would mutate markers on
    paths under the link target, potentially outside any Dropbox tree
    (regression surfaced by Codex on PR #240 against the lexical-first
    containment change in `_resolve_under_roots`)."""
    root = tmp_path
    write_file(root / ".dropboxignore", "build/\n")
    (root / "build").mkdir()
    # Target tree OUTSIDE root — files here must NOT be walked or marked.
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    sentinel = outside / "must_not_be_touched.txt"
    sentinel.write_text("", encoding="utf-8")
    # Symlink inside root pointing OUTSIDE.
    link_inside_root = root / "escape_link"
    try:
        link_inside_root.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create symlink in this environment: {exc}")

    cache = RuleCache()
    cache.load_root(root)

    # Reconcile the symlink as the walk root, mirroring the daemon's
    # _dispatch DIR_CREATE call for a watchdog-reported symlinked dir.
    report = reconcile.reconcile_subtree(root, link_inside_root, cache)

    # No markers should land on paths under the link target.
    assert sentinel.resolve() not in fake_markers._ignored, (
        "reconcile traversed the symlink target — leaf invariant violated"
    )
    # Report must not list paths under the target either (would_mark/clear
    # are dry-run-only; the marked/cleared counters cover real walks).
    for p, _msg in report.errors:
        assert outside not in p.parents, (
            f"reconcile attempted I/O on {p}, which is under the link target"
        )


def test_overridden_dropboxignore_logs_warning(
    tmp_path: Path,
    fake_markers: FakeMarkers,
    caplog: pytest.LogCaptureFixture,
    write_file: WriteFile,
) -> None:
    """Spec: `.dropboxignore is never itself ignored` — violations are logged
    at WARNING on every reconcile and continue to be overridden."""
    import logging

    write_file(tmp_path / ".dropboxignore", "build/\n")
    fake_markers.set_ignored(tmp_path / ".dropboxignore")  # something else marked it

    cache = RuleCache()
    cache.load_root(tmp_path)

    with caplog.at_level(logging.WARNING, logger="dbxignore.reconcile"):
        report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert not fake_markers.is_ignored(tmp_path / ".dropboxignore")
    assert report.cleared >= 1
    assert any(
        r.levelname == "WARNING" and ".dropboxignore" in r.message and "overriding" in r.message
        for r in caplog.records
    ), caplog.records


def test_rejects_subdir_outside_root(tmp_path: Path, fake_markers: FakeMarkers) -> None:
    other = tmp_path / "other"
    other.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    cache = RuleCache()
    cache.load_root(root)

    with pytest.raises(ValueError, match="not under root"):
        reconcile.reconcile_subtree(root, other, cache)

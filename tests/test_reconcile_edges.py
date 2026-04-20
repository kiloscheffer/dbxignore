import pytest

from dropboxignore import reconcile
from dropboxignore.rules import RuleCache


def test_skips_descendants_of_already_ignored_directory(tmp_path, fake_ads, write_file):
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "deep").mkdir()
    (tmp_path / "build" / "a.o").touch()
    fake_ads.set_ignored(tmp_path / "build")  # pre-ignored

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    # 'deep' and 'a.o' must not be touched (we skipped into build/).
    assert (tmp_path / "build" / "deep").resolve() not in fake_ads._ignored
    # Report counts no new marks/clears — build/ was already correct.
    assert report.marked == 0
    assert report.cleared == 0


def test_permission_error_is_logged_and_counted_not_raised(
    tmp_path, monkeypatch, caplog, write_file,
):
    import logging

    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "other").mkdir()

    class FailingADS:
        def __init__(self):
            self._ignored = set()
        def is_ignored(self, path): return False
        def set_ignored(self, path):
            if path.name == "build":
                raise PermissionError("locked")
            self._ignored.add(path.resolve())
        def clear_ignored(self, path): self._ignored.discard(path.resolve())

    failing = FailingADS()
    monkeypatch.setattr(reconcile, "ads", failing)

    cache = RuleCache()
    cache.load_root(tmp_path)

    with caplog.at_level(logging.WARNING, logger="dropboxignore.reconcile"):
        report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert len(report.errors) == 1
    err_path, err_msg = report.errors[0]
    assert err_path.name == "build"
    assert "locked" in err_msg
    assert any(
        r.levelname == "WARNING" and "locked" in r.message
        for r in caplog.records
    )


def test_file_not_found_during_walk_is_silently_skipped(tmp_path, monkeypatch, write_file):
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()

    class DisappearingADS:
        def is_ignored(self, path):
            raise FileNotFoundError("gone")
        def set_ignored(self, path): pass
        def clear_ignored(self, path): pass

    monkeypatch.setattr(reconcile, "ads", DisappearingADS())

    cache = RuleCache()
    cache.load_root(tmp_path)

    # Must not raise.
    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)
    # FileNotFoundError is expected traffic, not an error.
    assert report.errors == []


def test_sweep_clears_markers_when_dropboxignore_was_deleted_offline(
    tmp_path, fake_ads
):
    """Offline-recovery integration: if a .dropboxignore was deleted while
    the daemon was down, the next startup sweep must clear every ADS marker
    it used to justify. No rules in cache + marker on disk = clear."""
    # Prior-daemon-run state: build/ is ignored; deep/ inside it is ignored
    # too (descendant-of-ignored is skipped during normal reconcile, but if
    # an old sweep marked it directly, the marker is on disk).
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "deep").mkdir()
    fake_ads.set_ignored(tmp_path / "build")
    fake_ads.set_ignored(tmp_path / "build" / "deep")

    # Fresh daemon startup: no .dropboxignore on disk, empty cache, sweep.
    cache = RuleCache()
    cache.load_root(tmp_path)
    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert not fake_ads.is_ignored(tmp_path / "build")
    assert not fake_ads.is_ignored(tmp_path / "build" / "deep")
    assert report.cleared == 2


def test_overridden_dropboxignore_logs_warning(tmp_path, fake_ads, caplog, write_file):
    """Spec: `.dropboxignore is never itself ignored` — violations are logged
    at WARNING on every reconcile and continue to be overridden."""
    import logging

    write_file(tmp_path / ".dropboxignore", "build/\n")
    fake_ads.set_ignored(tmp_path / ".dropboxignore")  # something else marked it

    cache = RuleCache()
    cache.load_root(tmp_path)

    with caplog.at_level(logging.WARNING, logger="dropboxignore.reconcile"):
        report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert not fake_ads.is_ignored(tmp_path / ".dropboxignore")
    assert report.cleared >= 1
    assert any(
        r.levelname == "WARNING" and ".dropboxignore" in r.message
        and "overriding" in r.message
        for r in caplog.records
    ), caplog.records


def test_rejects_subdir_outside_root(tmp_path, fake_ads):
    other = tmp_path / "other"
    other.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    cache = RuleCache()
    cache.load_root(root)

    with pytest.raises(ValueError, match="not under root"):
        reconcile.reconcile_subtree(root, other, cache)

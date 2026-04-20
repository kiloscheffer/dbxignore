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


def test_rejects_subdir_outside_root(tmp_path, fake_ads):
    import pytest

    other = tmp_path / "other"
    other.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    cache = RuleCache()
    cache.load_root(root)

    with pytest.raises(ValueError, match="not under root"):
        reconcile.reconcile_subtree(root, other, cache)

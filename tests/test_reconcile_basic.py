from dropboxignore import reconcile
from dropboxignore.rules import RuleCache


def test_sets_ads_on_matching_directory(tmp_path, fake_markers, write_file):
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "src").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert (tmp_path / "build").resolve() in fake_markers._ignored
    assert (tmp_path / "src").resolve() not in fake_markers._ignored
    assert report.marked == 1
    assert report.cleared == 0
    assert report.errors == []


def test_clears_ads_when_no_longer_matching(tmp_path, fake_markers, write_file):
    (tmp_path / "build").mkdir()
    fake_markers.set_ignored(tmp_path / "build")  # pre-existing marker
    write_file(tmp_path / ".dropboxignore", "")  # no rules

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert (tmp_path / "build").resolve() not in fake_markers._ignored
    assert report.cleared == 1


def test_no_ops_when_state_already_correct(tmp_path, fake_markers, write_file):
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    fake_markers.set_ignored(tmp_path / "build")

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    # No extra set or clear calls beyond the pre-seed.
    assert fake_markers.set_calls == [(tmp_path / "build").resolve()]
    assert fake_markers.clear_calls == []
    assert report.marked == 0
    assert report.cleared == 0


def test_matches_files_not_just_directories(tmp_path, fake_markers, write_file):
    write_file(tmp_path / ".dropboxignore", "*.log\n")
    (tmp_path / "a.log").touch()
    (tmp_path / "b.txt").touch()

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert (tmp_path / "a.log").resolve() in fake_markers._ignored
    assert (tmp_path / "b.txt").resolve() not in fake_markers._ignored
    assert report.marked == 1

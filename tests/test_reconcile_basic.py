from pathlib import Path

import pytest

from dbxignore import reconcile
from dbxignore.rules import RuleCache
from tests.conftest import FakeMarkers, WriteFile


def test_sets_ads_on_matching_directory(
    tmp_path: Path, fake_markers: FakeMarkers, write_file: WriteFile
) -> None:
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


def test_clears_ads_when_no_longer_matching(
    tmp_path: Path, fake_markers: FakeMarkers, write_file: WriteFile
) -> None:
    (tmp_path / "build").mkdir()
    fake_markers.set_ignored(tmp_path / "build")  # pre-existing marker
    write_file(tmp_path / ".dropboxignore", "")  # no rules

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert (tmp_path / "build").resolve() not in fake_markers._ignored
    assert report.cleared == 1


def test_no_ops_when_state_already_correct(
    tmp_path: Path, fake_markers: FakeMarkers, write_file: WriteFile
) -> None:
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


def test_matches_files_not_just_directories(
    tmp_path: Path, fake_markers: FakeMarkers, write_file: WriteFile
) -> None:
    write_file(tmp_path / ".dropboxignore", "*.log\n")
    (tmp_path / "a.log").touch()
    (tmp_path / "b.txt").touch()

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert (tmp_path / "a.log").resolve() in fake_markers._ignored
    assert (tmp_path / "b.txt").resolve() not in fake_markers._ignored
    assert report.marked == 1


def test_does_not_descend_into_marked_subtree(
    tmp_path: Path, fake_markers: FakeMarkers, write_file: WriteFile
) -> None:
    # Steady-state pruning contract: when a child directory
    # is already marked AND match() still confirms it should be ignored,
    # _reconcile_path returns currently_ignored=True and the dirnames[:] filter
    # drops it from the walk. Descendants are NEVER queried.
    #
    # If a future refactor breaks this contract, the per-tick cost of the
    # hourly recovery sweep regresses from O(unmarked dirs) to O(all dirs).
    write_file(tmp_path / ".dropboxignore", "big_dir/\n")
    big_dir = tmp_path / "big_dir"
    grand_a = big_dir / "grand_a"
    grand_b = big_dir / "grand_b"
    grand_a.mkdir(parents=True)
    grand_b.mkdir()
    deep_file = grand_a / "deep.txt"
    deep_file.touch()
    fake_markers.set_ignored(big_dir)
    fake_markers.set_calls.clear()  # Pre-seed shouldn't count as a sweep mutation.

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    queried = set(fake_markers.is_ignored_calls)
    assert big_dir.resolve() in queried, "subtree root must be queried"
    assert grand_a.resolve() not in queried, "grandchild directory must be pruned"
    assert grand_b.resolve() not in queried, "grandchild directory must be pruned"
    assert deep_file.resolve() not in queried, "great-grandchild file must be pruned"
    assert fake_markers.set_calls == [], "no new markers should be written"
    assert fake_markers.clear_calls == [], "marker on big_dir must NOT be cleared"
    assert report.marked == 0
    assert report.cleared == 0
    assert report.errors == []


def test_reconcile_subtree_honors_stop_event(
    tmp_path: Path, fake_markers: FakeMarkers, write_file: WriteFile
) -> None:
    # Cooperative cancellation contract: when stop_event is set
    # before reconcile_subtree starts, the walk must break out without
    # processing additional directories. Convergence guarantees the next
    # sweep finishes the rest.
    import threading

    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "deep").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    stop = threading.Event()
    stop.set()  # Already cancelled before reconcile starts.

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache, stop_event=stop)

    # The top-level _reconcile_path(subdir, ...) call still ran (it's the
    # pre-walk path; cheap, single syscall). The os.walk loop never began.
    # No descendant directories were visited.
    assert (tmp_path / "src" / "deep").resolve() not in fake_markers.is_ignored_calls
    assert report.errors == []


def test_reconcile_subtree_descend_false_skips_walk(
    tmp_path: Path, fake_markers: FakeMarkers, write_file: WriteFile
) -> None:
    # descend=False contract: reconcile only the
    # subdir's own marker; do NOT descend. _sweep_once relies on this to
    # split the root-path reconcile from the per-top-level-child fan-out.
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "deep").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache, descend=False)

    queried = set(fake_markers.is_ignored_calls)
    assert tmp_path.resolve() in queried, "subdir itself must be queried"
    assert (tmp_path / "build").resolve() not in queried, "child must NOT be queried"
    assert (tmp_path / "src").resolve() not in queried, "child must NOT be queried"
    assert (tmp_path / "src" / "deep").resolve() not in queried, "grandchild must NOT be queried"
    # No mutations expected — tmp_path itself doesn't match any rule.
    assert report.marked == 0
    assert report.cleared == 0


def test_reconcile_subtree_stops_mid_dirnames_on_stop_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_file: WriteFile
) -> None:
    # Guards the dirnames list-comprehension window: checking stop_event
    # only at os.walk iteration boundaries would process every sibling
    # directory in one level before the next check could fire.
    import threading

    write_file(tmp_path / ".dropboxignore", "")
    for i in range(5):
        (tmp_path / f"dir_{i}").mkdir()

    stop = threading.Event()
    queries: list[Path] = []

    class StopOnSecondQueryMarkers(FakeMarkers):
        def is_ignored(self, path: Path) -> bool:
            queries.append(path.resolve())
            if len(queries) == 2:  # root is call 1; first subdir is call 2
                stop.set()
            return super().is_ignored(path)

    monkeypatch.setattr(reconcile, "markers", StopOnSecondQueryMarkers())

    cache = RuleCache()
    cache.load_root(tmp_path)
    reconcile.reconcile_subtree(tmp_path, tmp_path, cache, stop_event=stop)

    # Only 2 is_ignored calls should occur: root + 1 subdir (which set the
    # event). The remaining 4 siblings must be skipped.
    assert len(queries) == 2, (
        f"expected 2 is_ignored calls (root + 1 subdir), got {len(queries)}: {queries}"
    )

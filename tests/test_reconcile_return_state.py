"""_reconcile_path returns the final ignored state so reconcile_subtree
can prune subtrees without a second ADS read."""

from __future__ import annotations

from pathlib import Path

from dropboxignore import reconcile
from dropboxignore.rules import RuleCache


def test_reconcile_path_returns_true_after_newly_marking(
    tmp_path, fake_markers, write_file
):
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.Report()
    result = reconcile._reconcile_path(tmp_path / "build", cache, report)

    assert result is True


def test_reconcile_path_returns_false_after_clearing(
    tmp_path, fake_markers, write_file
):
    (tmp_path / "build").mkdir()
    fake_markers.set_ignored(tmp_path / "build")
    write_file(tmp_path / ".dropboxignore", "")  # no rules
    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.Report()
    result = reconcile._reconcile_path(tmp_path / "build", cache, report)

    assert result is False


def test_reconcile_path_returns_current_state_when_no_mutation_needed(
    tmp_path, fake_markers, write_file
):
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    fake_markers.set_ignored(tmp_path / "build")
    (tmp_path / "src").mkdir()
    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.Report()
    assert reconcile._reconcile_path(tmp_path / "build", cache, report) is True
    assert reconcile._reconcile_path(tmp_path / "src", cache, report) is False


def test_reconcile_path_returns_none_on_read_permission_error(
    tmp_path, monkeypatch, write_file
):
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()

    class FailingADS:
        def is_ignored(self, path: Path) -> bool:
            raise PermissionError("locked")
        def set_ignored(self, path: Path) -> None: pass
        def clear_ignored(self, path: Path) -> None: pass

    monkeypatch.setattr(reconcile, "markers", FailingADS())

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.Report()
    result = reconcile._reconcile_path(tmp_path / "build", cache, report)

    assert result is None
    assert len(report.errors) == 1


def test_reconcile_path_returns_none_on_vanished_path(
    tmp_path, monkeypatch, write_file
):
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()

    class DisappearingADS:
        def is_ignored(self, path: Path) -> bool:
            raise FileNotFoundError("gone")
        def set_ignored(self, path: Path) -> None: pass
        def clear_ignored(self, path: Path) -> None: pass

    monkeypatch.setattr(reconcile, "markers", DisappearingADS())

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.Report()
    result = reconcile._reconcile_path(tmp_path / "build", cache, report)

    assert result is None


def test_reconcile_path_returns_unchanged_state_when_write_fails(
    tmp_path, monkeypatch, write_file
):
    """If the ADS write raises, the marker's actual state is unchanged —
    the returned value must reflect that, not the intended state."""
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()

    class WriteFailingADS:
        def __init__(self) -> None:
            self._ignored: set[Path] = set()
        def is_ignored(self, path: Path) -> bool:
            return path.resolve() in self._ignored
        def set_ignored(self, path: Path) -> None:
            raise PermissionError("locked")
        def clear_ignored(self, path: Path) -> None:
            raise PermissionError("locked")

    monkeypatch.setattr(reconcile, "markers", WriteFailingADS())

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.Report()
    # build/ matches, but set_ignored raises; actual ADS state still False.
    result = reconcile._reconcile_path(tmp_path / "build", cache, report)

    assert result is False
    assert len(report.errors) == 1


def test_reconcile_subtree_does_not_reread_ads_after_reconcile(
    tmp_path, monkeypatch, write_file
):
    """Regression guard: reconcile_subtree must call markers.is_ignored at most
    once per visited path. The final ignored state threads out of
    _reconcile_path; a second read purely to decide pruning is the bug."""
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "deep").mkdir()
    (tmp_path / "src").mkdir()

    class CountingADS:
        def __init__(self) -> None:
            self._ignored: set[Path] = set()
            self.is_ignored_calls: list[Path] = []
        def is_ignored(self, path: Path) -> bool:
            self.is_ignored_calls.append(path.resolve())
            return path.resolve() in self._ignored
        def set_ignored(self, path: Path) -> None:
            self._ignored.add(path.resolve())
        def clear_ignored(self, path: Path) -> None:
            self._ignored.discard(path.resolve())

    counting = CountingADS()
    monkeypatch.setattr(reconcile, "markers", counting)

    cache = RuleCache()
    cache.load_root(tmp_path)

    reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    # Each visited path should appear at most once in is_ignored_calls.
    # (build/ is pruned so deep/ shouldn't be visited at all.)
    from collections import Counter
    counts = Counter(counting.is_ignored_calls)
    duplicates = {p: c for p, c in counts.items() if c > 1}
    assert not duplicates, f"paths read twice: {duplicates}"

    # And build/deep must not have been visited (parent was pruned).
    assert (tmp_path / "build" / "deep").resolve() not in counting.is_ignored_calls

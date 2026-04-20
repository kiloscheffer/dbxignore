from pathlib import Path

import pytest

from dropboxignore import reconcile
from dropboxignore.rules import RuleCache


class FakeADS:
    """In-memory stand-in for the ads module."""

    def __init__(self) -> None:
        self._ignored: set[Path] = set()
        self.set_calls: list[Path] = []
        self.clear_calls: list[Path] = []

    def is_ignored(self, path: Path) -> bool:
        return path.resolve() in self._ignored

    def set_ignored(self, path: Path) -> None:
        p = path.resolve()
        self._ignored.add(p)
        self.set_calls.append(p)

    def clear_ignored(self, path: Path) -> None:
        p = path.resolve()
        self._ignored.discard(p)
        self.clear_calls.append(p)


@pytest.fixture
def fake_ads(monkeypatch):
    fake = FakeADS()
    monkeypatch.setattr(reconcile, "ads", fake)
    return fake


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_sets_ads_on_matching_directory(tmp_path, fake_ads):
    _write(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "src").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert (tmp_path / "build").resolve() in fake_ads._ignored
    assert (tmp_path / "src").resolve() not in fake_ads._ignored
    assert report.marked == 1
    assert report.cleared == 0
    assert report.errors == []


def test_clears_ads_when_no_longer_matching(tmp_path, fake_ads):
    (tmp_path / "build").mkdir()
    fake_ads.set_ignored(tmp_path / "build")  # pre-existing marker
    _write(tmp_path / ".dropboxignore", "")  # no rules

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert (tmp_path / "build").resolve() not in fake_ads._ignored
    assert report.cleared == 1


def test_no_ops_when_state_already_correct(tmp_path, fake_ads):
    _write(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    fake_ads.set_ignored(tmp_path / "build")

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    # No extra set or clear calls beyond the pre-seed.
    assert fake_ads.set_calls == [(tmp_path / "build").resolve()]
    assert fake_ads.clear_calls == []
    assert report.marked == 0
    assert report.cleared == 0


def test_matches_files_not_just_directories(tmp_path, fake_ads):
    _write(tmp_path / ".dropboxignore", "*.log\n")
    (tmp_path / "a.log").touch()
    (tmp_path / "b.txt").touch()

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert (tmp_path / "a.log").resolve() in fake_ads._ignored
    assert (tmp_path / "b.txt").resolve() not in fake_ads._ignored
    assert report.marked == 1

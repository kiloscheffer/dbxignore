"""Tests for daemon._sweep_once across one and multiple roots."""

from __future__ import annotations

import datetime as dt

from dropboxignore import daemon, state
from dropboxignore.rules import RuleCache


def _utc_now():
    return dt.datetime.now(dt.UTC)


def test_sweep_applies_rules_across_multiple_roots(
    tmp_path, fake_markers, monkeypatch, write_file
):
    """Multi-root sweep must reconcile every root independently. Regression
    guard for the phase-split (sequential load, parallel reconcile) — both
    roots' markers must land on exactly the paths their own rule file names."""
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    write_file(root_a / ".dropboxignore", "build/\n")
    (root_a / "build").mkdir()
    (root_a / "src").mkdir()
    write_file(root_b / ".dropboxignore", "dist/\n")
    (root_b / "dist").mkdir()
    (root_b / "lib").mkdir()

    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")

    cache = RuleCache()
    daemon._sweep_once([root_a, root_b], cache, _utc_now())

    assert (root_a / "build").resolve() in fake_markers._ignored
    assert (root_a / "src").resolve() not in fake_markers._ignored
    assert (root_b / "dist").resolve() in fake_markers._ignored
    assert (root_b / "lib").resolve() not in fake_markers._ignored


def test_sweep_writes_aggregated_report_to_state(
    tmp_path, fake_markers, monkeypatch, write_file
):
    """Aggregation: marked/cleared counts in the persisted state should
    sum across roots (not drop one root's report on the floor)."""
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    write_file(root_a / ".dropboxignore", "build/\n")
    (root_a / "build").mkdir()
    write_file(root_b / ".dropboxignore", "dist/\n")
    (root_b / "dist").mkdir()

    state_path = tmp_path / "state.json"
    monkeypatch.setattr(state, "default_path", lambda: state_path)

    cache = RuleCache()
    daemon._sweep_once([root_a, root_b], cache, _utc_now())

    s = state.read()
    assert s is not None
    # One marker per root — sum must be 2.
    assert s.last_sweep_marked == 2
    assert s.last_sweep_cleared == 0
    assert s.last_sweep_errors == 0


def test_sweep_populates_last_error_when_reconcile_fails(
    tmp_path, monkeypatch, write_file
):
    """Sweep errors must populate state.last_error so `status` can surface them."""
    from dropboxignore import reconcile

    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()

    class FailingADS:
        def is_ignored(self, path):
            return False
        def set_ignored(self, path):
            raise PermissionError("locked by Dropbox")
        def clear_ignored(self, path):
            pass

    monkeypatch.setattr(reconcile, "markers", FailingADS())
    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")

    cache = RuleCache()
    daemon._sweep_once([tmp_path], cache, _utc_now())

    s = state.read()
    assert s is not None
    assert s.last_sweep_errors == 1
    assert s.last_error is not None
    assert s.last_error.path.name == "build"
    assert "locked by Dropbox" in s.last_error.message


def test_sweep_leaves_last_error_none_on_clean_sweep(
    tmp_path, fake_markers, monkeypatch, write_file
):
    """Per-sweep semantics: a clean sweep writes last_error=None."""
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()

    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")

    cache = RuleCache()
    daemon._sweep_once([tmp_path], cache, _utc_now())

    s = state.read()
    assert s is not None
    assert s.last_sweep_errors == 0
    assert s.last_error is None


def test_sweep_single_root_still_works(
    tmp_path, fake_markers, monkeypatch, write_file
):
    """Regression guard: the single-root path (the common case) bypasses the
    ThreadPoolExecutor and stays simple."""
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()

    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")

    cache = RuleCache()
    daemon._sweep_once([tmp_path], cache, _utc_now())

    assert (tmp_path / "build").resolve() in fake_markers._ignored

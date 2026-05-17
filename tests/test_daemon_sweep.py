"""Tests for daemon._sweep_once across one and multiple roots."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import pytest

from dbxignore import daemon, state
from dbxignore.rules import RuleCache

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import FakeMarkers, WriteFile


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def test_sweep_applies_rules_across_multiple_roots(
    tmp_path: Path,
    fake_markers: FakeMarkers,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
) -> None:
    """Multi-root sweep must reconcile every root independently. Pins the
    phase-split (sequential load, parallel reconcile) — both roots'
    markers must land on exactly the paths their own rule file names."""
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
    tmp_path: Path,
    fake_markers: FakeMarkers,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
) -> None:
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_file: WriteFile
) -> None:
    """Sweep errors must populate state.last_error so `status` can surface them."""
    from dbxignore import reconcile

    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()

    class FailingADS:
        def is_ignored(self, path: Path) -> bool:
            return False

        def set_ignored(self, path: Path) -> None:
            raise PermissionError("locked by Dropbox")

        def clear_ignored(self, path: Path) -> None:
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
    tmp_path: Path,
    fake_markers: FakeMarkers,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
) -> None:
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
    tmp_path: Path,
    fake_markers: FakeMarkers,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
) -> None:
    """The single-root path (the common case) bypasses the
    ThreadPoolExecutor and stays simple."""
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()

    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")

    cache = RuleCache()
    daemon._sweep_once([tmp_path], cache, _utc_now())

    assert (tmp_path / "build").resolve() in fake_markers._ignored


def test_sweep_fans_out_per_top_level_child(
    tmp_path: Path,
    fake_markers: FakeMarkers,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
) -> None:
    """Per-subdir fan-out: each top-level child of a root becomes its own
    work item submitted to the ThreadPoolExecutor, plus one descend=False
    item for the root itself."""
    from concurrent.futures import ThreadPoolExecutor

    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    # `.dropboxignore` is also a top-level child — it counts as work.

    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")

    submitted: list[tuple[Path, Path, bool]] = []
    real_map = ThreadPoolExecutor.map

    def spy_map(self: ThreadPoolExecutor, fn, *iterables, **kwargs):  # type: ignore[no-untyped-def]
        # Record the work-item iterable so the test can assert what was
        # fanned out without depending on internal task counts.
        submitted.extend(list(iterables[0]))
        return real_map(self, fn, submitted, *iterables[1:], **kwargs)

    monkeypatch.setattr(ThreadPoolExecutor, "map", spy_map)

    cache = RuleCache()
    daemon._sweep_once([tmp_path], cache, _utc_now())

    # Expected: 1 (root descend=False) + 4 (.dropboxignore, build, docs, src as descend=True)
    assert len(submitted) == 5, f"expected 5 work items, got {len(submitted)}: {submitted}"
    descend_flags = [w[2] for w in submitted]
    assert descend_flags.count(False) == 1, "exactly one descend=False entry per root"
    assert descend_flags.count(True) == 4, "one descend=True entry per top-level child"

    # End state still correct — `build` marked, `src`/`docs` unmarked.
    assert (tmp_path / "build").resolve() in fake_markers._ignored
    assert (tmp_path / "src").resolve() not in fake_markers._ignored
    assert (tmp_path / "docs").resolve() not in fake_markers._ignored


def test_sweep_does_not_descend_into_top_level_symlink(
    tmp_path: Path,
    fake_markers: FakeMarkers,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
) -> None:
    """A top-level symlink-to-directory must not have its target traversed.

    ``os.walk(path, followlinks=False)`` follows the symlink when the
    symlink is the *starting* path — ``followlinks`` only applies to
    symlinks encountered as subdirectories during the walk. The previous
    sweep shape (one ``os.walk`` covering the whole root) saw symlinks
    only as ``dirnames`` entries, where ``followlinks=False`` did protect
    against descent. The per-subdir fan-out has to recover that protection
    at the work-list build by submitting symlink children with
    ``descend=False``.
    """
    root = tmp_path / "root"
    root.mkdir()
    write_file(root / ".dropboxignore", "*.leak\n")  # any rule; matters only if walked
    (root / "src").mkdir()  # a real (non-symlink) child for sanity

    target = tmp_path / "outside_target"
    target.mkdir()
    leaked = target / "secret.leak"
    leaked.touch()

    link = root / "link_into_target"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        # Windows non-admin runs without SeCreateSymbolicLink; skip.
        pytest.skip("symlink creation not supported in this environment")

    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")

    cache = RuleCache()
    daemon._sweep_once([root], cache, _utc_now())

    # The leaked file under the symlink target must NEVER have been
    # queried. The walk into the link target would have surfaced it via
    # the `*.leak` rule.
    assert leaked.resolve() not in fake_markers.is_ignored_calls, (
        f"sweep descended into symlink target — queried {leaked} "
        f"(out of {len(fake_markers.is_ignored_calls)} queries)"
    )
    # Sanity: the regular 'src' subdir was reconciled.
    assert (root / "src").resolve() in fake_markers.is_ignored_calls, (
        "regular subdir must still be reconciled"
    )


def test_sweep_handles_unreadable_root(
    tmp_path: Path,
    fake_markers: FakeMarkers,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When iterdir() raises OSError on a root, the sweep logs a warning,
    skips fanning out that root's children, but still reconciles the root
    itself (descend=False entry stays in the work list). Other roots are
    unaffected."""
    import logging

    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    write_file(root_a / ".dropboxignore", "build/\n")
    (root_a / "build").mkdir()
    write_file(root_b / ".dropboxignore", "dist/\n")
    (root_b / "dist").mkdir()

    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")

    real_iterdir = type(root_a).iterdir

    def selective_iterdir(self):  # type: ignore[no-untyped-def]
        if self == root_a:
            raise OSError("simulated read failure")
        return real_iterdir(self)

    monkeypatch.setattr(type(root_a), "iterdir", selective_iterdir)

    cache = RuleCache()
    with caplog.at_level(logging.WARNING, logger="dbxignore.daemon"):
        daemon._sweep_once([root_a, root_b], cache, _utc_now())

    # root_b's children were still reconciled despite root_a's failure.
    assert (root_b / "dist").resolve() in fake_markers._ignored
    # The warning about root_a was logged.
    assert any("could not enumerate root" in rec.message for rec in caplog.records), (
        "expected enumerate-failure warning"
    )


def test_sweep_once_forwards_stop_event_to_reconcile(
    tmp_path: Path,
    fake_markers: FakeMarkers,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
) -> None:
    # When _sweep_once is called with stop_event already set, no path under
    # any root should have its marker queried beyond the top-level
    # _reconcile_path call. Confirms the parameter threads through to
    # reconcile_subtree.
    import threading

    root = tmp_path / "root"
    write_file(root / ".dropboxignore", "build/\n")
    (root / "build").mkdir()
    (root / "src").mkdir()
    (root / "src" / "deep").mkdir()

    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")

    cache = RuleCache()
    stop = threading.Event()
    stop.set()

    daemon._sweep_once([root], cache, _utc_now(), stop_event=stop)

    # The deeply-nested directory should NOT have been queried — the walk
    # broke out before descending.
    assert (root / "src" / "deep").resolve() not in fake_markers.is_ignored_calls

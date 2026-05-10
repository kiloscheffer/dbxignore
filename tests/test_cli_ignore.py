"""CLI integration tests for ``dbxignore ignore`` and ``dbxignore unignore`` (item #93).

Covers helper unit tests, command happy paths, idempotence + redundancy
branches, --yes / --dry-run flags, error paths, and daemon-coexistence smoke.
"""

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from dbxignore import cli, reconcile, state
from dbxignore.rules import IGNORE_FILENAME, RuleCache
from tests.conftest import FakeMarkers


def test_select_rule_file_finds_target_parent_dropboxignore(tmp_path: Path) -> None:
    root = tmp_path
    proj = root / "proj"
    proj.mkdir()
    (proj / IGNORE_FILENAME).touch()
    target = proj / "build"
    target.mkdir()
    selected = cli._select_rule_file(target, root)
    assert selected == proj / IGNORE_FILENAME


def test_select_rule_file_walks_to_higher_ancestor(tmp_path: Path) -> None:
    root = tmp_path
    (root / IGNORE_FILENAME).touch()
    deep = root / "a" / "b" / "c"
    deep.mkdir(parents=True)
    selected = cli._select_rule_file(deep, root)
    assert selected == root / IGNORE_FILENAME


def test_select_rule_file_falls_back_to_root_when_no_ancestor(tmp_path: Path) -> None:
    root = tmp_path
    deep = root / "a" / "b"
    deep.mkdir(parents=True)
    selected = cli._select_rule_file(deep, root)
    # Returns the canonical root file path even if it doesn't exist yet —
    # ``append_rule`` will create it on first invocation.
    assert selected == root / IGNORE_FILENAME
    assert not selected.exists()


def test_select_rule_file_prefers_closer_ancestor(tmp_path: Path) -> None:
    root = tmp_path
    (root / IGNORE_FILENAME).touch()
    proj = root / "proj"
    proj.mkdir()
    (proj / IGNORE_FILENAME).touch()
    target = proj / "build"
    target.mkdir()
    selected = cli._select_rule_file(target, root)
    # Closer ancestor wins.
    assert selected == proj / IGNORE_FILENAME


def test_select_rule_file_target_equals_root(tmp_path: Path) -> None:
    """Edge: target is the root itself (technically `find_containing` rejects
    this for ignore/unignore in practice, but the helper should still terminate
    cleanly without infinite-looping). Returns root/IGNORE_FILENAME."""
    root = tmp_path
    selected = cli._select_rule_file(root, root)
    assert selected == root / IGNORE_FILENAME


def _setup_dropbox_root(
    tmp_path: Path,
    fake_markers: FakeMarkers,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Stage a Dropbox root with no existing rule files, no daemon alive."""
    root = tmp_path
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr(state, "default_path", lambda: root / "_state.json")
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid, create_time=None: False)
    return root


def test_ignore_happy_path_creates_root_file(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    rule_file = root / IGNORE_FILENAME
    assert rule_file.exists()
    assert "build/" in rule_file.read_text(encoding="utf-8")
    assert fake_markers.is_ignored(target)


def test_ignore_lands_in_nearest_ancestor(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    proj = root / "proj"
    proj.mkdir()
    (proj / IGNORE_FILENAME).touch()
    target = proj / "foo" / "bar"
    target.mkdir(parents=True)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    # Rule landed in proj/.dropboxignore, not root/.dropboxignore.
    assert "foo/bar/" in (proj / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert not (root / IGNORE_FILENAME).exists()
    assert fake_markers.is_ignored(target)


def test_ignore_idempotent_on_recall(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    runner = CliRunner()
    runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    rule_file_content_first = (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    assert "already ignored" in result.output
    # File unchanged on second call.
    assert (root / IGNORE_FILENAME).read_text(encoding="utf-8") == rule_file_content_first


def test_ignore_half_state_marker_missing(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule on disk, but marker not set (e.g. daemon was down on previous call)."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    (root / IGNORE_FILENAME).write_text("build/\n", encoding="utf-8")
    assert not fake_markers.is_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    # Already-ignored detected; marker set as half-state recovery.
    assert "already ignored" in result.output
    assert fake_markers.is_ignored(target)


def test_ignore_redundant_when_wildcard_already_matches(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    proj = root / "proj"
    proj.mkdir()
    (proj / IGNORE_FILENAME).write_text("**/build/\n", encoding="utf-8")
    target = proj / "build"
    target.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    # No redundant literal rule appended; informational message printed.
    assert "already covered" in result.output
    content = (proj / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert content == "**/build/\n"  # unchanged
    assert fake_markers.is_ignored(target)


def test_ignore_rejects_nonexistent_path(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(root / "ghost"), "--yes"])
    assert result.exit_code == 2
    assert "does not exist" in result.output


def test_ignore_rejects_path_outside_roots(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    elsewhere = tmp_path.parent / "not_dropbox"
    elsewhere.mkdir(exist_ok=True)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(elsewhere), "--yes"])
    assert result.exit_code == 2
    assert "not under any Dropbox root" in result.output


def test_ignore_rejects_no_dropbox_roots(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    target = tmp_path / "build"
    target.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 2
    assert "No Dropbox roots" in result.output


def test_ignore_dry_run_does_not_mutate(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would append" in result.output
    assert "would set marker" in result.output
    assert not (root / IGNORE_FILENAME).exists()
    assert not fake_markers.is_ignored(target)


def test_ignore_file_target_has_no_trailing_slash(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "notes.txt"
    target.touch()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    content = (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert "notes.txt\n" in content
    assert "notes.txt/\n" not in content


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows filesystems forbid '*' in filenames",
)
def test_ignore_meta_char_escaping_in_dir_name(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "foo*bar"
    target.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    content = (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert r"foo\*bar/" in content


def test_ignore_default_prompts_then_aborts_on_no(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target)], input="n\n")
    assert result.exit_code == 0, result.output
    assert "Aborted" in result.output
    # No mutation occurred.
    assert not (root / IGNORE_FILENAME).exists()
    assert not fake_markers.is_ignored(target)


def test_unignore_happy_path(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    # Pre-state: rule + marker.
    (root / IGNORE_FILENAME).write_text("build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    # Rule removed (file may be empty / header-only).
    content = (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert "build/" not in content
    # Marker cleared.
    assert not fake_markers.is_ignored(target)


def test_unignore_already_not_ignored_is_noop(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    assert "not ignored" in result.output


def test_unignore_removes_from_multiple_files(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    proj = root / "proj"
    proj.mkdir()
    target = proj / "build"
    target.mkdir()
    # Same target literal rule in TWO ancestor files (edge case Q4 case 5).
    (root / IGNORE_FILENAME).write_text("proj/build/\n", encoding="utf-8")
    (proj / IGNORE_FILENAME).write_text("build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    # Both rules removed.
    assert "proj/build/" not in (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert "build/" not in (proj / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert not fake_markers.is_ignored(target)


def test_unignore_rejects_nonexistent_path(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(root / "ghost"), "--yes"])
    assert result.exit_code == 2
    assert "does not exist" in result.output


def test_unignore_fails_loud_on_wildcard_collision(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Q4 case 2: literal rule + wildcard. Removing literal would still leave
    the path matched by the wildcard, so refuse to mutate."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    proj = root / "proj"
    proj.mkdir()
    target = proj / "build"
    target.mkdir()
    # Literal rule we wrote + wildcard rule the user added separately.
    (proj / IGNORE_FILENAME).write_text("build/\n**/build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 2
    assert "is also matched by" in result.output
    assert "**/build/" in result.output
    # Neither rule mutated; marker still set.
    content = (proj / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert "build/\n" in content
    assert "**/build/\n" in content
    assert fake_markers.is_ignored(target)


def test_unignore_fails_loud_when_only_wildcard_matches(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Q4 case 3: only a wildcard rule matches; no literal rule to remove.
    Same fail-loud message — the user has to remove the wildcard manually."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    proj = root / "proj"
    proj.mkdir()
    target = proj / "build"
    target.mkdir()
    (proj / IGNORE_FILENAME).write_text("**/build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 2
    assert "is also matched by" in result.output
    # Rule file unchanged; marker still set.
    assert (proj / IGNORE_FILENAME).read_text(encoding="utf-8") == "**/build/\n"
    assert fake_markers.is_ignored(target)


def test_unignore_filters_dropped_negation_matches(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """is_dropped matches must be filtered before computing blockers/removable.
    A dropped negation under an ignored ancestor is inert per the conflict
    detector and should not appear in unignore's blocker enumeration."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    proj = root / "proj"
    proj.mkdir()
    target = proj / "foo"
    target.mkdir()
    # Root rule ignores all of proj/. Sub-rule attempts a negation but it's
    # dropped because proj/ is itself ignored at the ancestor level.
    (root / IGNORE_FILENAME).write_text("proj/\n", encoding="utf-8")
    (proj / IGNORE_FILENAME).write_text("!foo/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    # The dropped negation should not appear in the blocker list. Only `proj/`
    # is a real matching rule. Verify the error message names `proj/` not `!foo/`.
    assert result.exit_code == 2
    assert "is also matched by" in result.output
    assert "proj/" in result.output
    # The dropped negation must NOT appear in the blocker list.
    assert "!foo/" not in result.output


def test_unignore_dry_run_does_not_mutate(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    (root / IGNORE_FILENAME).write_text("build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would remove" in result.output
    assert "would clear marker" in result.output
    # No mutation.
    assert "build/" in (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert fake_markers.is_ignored(target)


def test_unignore_tolerates_trailing_whitespace_in_rule_line(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manually-edited rule with trailing spaces — rstrip-equality matches
    the canonical form, rule is removable not blocking."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    (root / IGNORE_FILENAME).write_text("build/   \n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    assert "build/" not in (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert not fake_markers.is_ignored(target)


def test_unignore_default_prompts_then_aborts_on_no(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    (root / IGNORE_FILENAME).write_text("build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target)], input="n\n")
    assert result.exit_code == 0, result.output
    assert "Aborted" in result.output
    # No mutation.
    assert "build/" in (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert fake_markers.is_ignored(target)


def test_ignore_dry_run_does_not_mutate_in_half_state(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Half-state (rule on disk, marker missing) + --dry-run: must NOT mutate
    the marker. Caught by Final Review Finding 1 — the half-state branch's
    early return previously bypassed the --dry-run check."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    (root / IGNORE_FILENAME).write_text("build/\n", encoding="utf-8")
    assert not fake_markers.is_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would set marker" in result.output
    # Marker still not set — the dry-run was honored.
    assert not fake_markers.is_ignored(target)


def test_ignore_marker_oserror_exits_2_with_message(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ENOTSUP / FAT32 case (Final Review Finding 2): marker write fails;
    rule is on disk; verb exits 2 with a user-friendly message that names
    both the failure cause and the daemon's eventual recovery path."""
    import errno

    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()

    def _raise_enotsup(path: Path) -> None:
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr(fake_markers, "set_ignored", _raise_enotsup)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 2
    assert "Marker write failed" in result.output
    # The rule should still be on disk (rule-first ordering).
    assert "build/" in (root / IGNORE_FILENAME).read_text(encoding="utf-8")


def test_unignore_marker_oserror_exits_2_with_message(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symmetric to the ignore case: clear_ignored OSError exits 2 with
    a user-friendly message; the rule is already removed from disk."""
    import errno

    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    (root / IGNORE_FILENAME).write_text("build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)

    def _raise_enotsup(path: Path) -> None:
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr(fake_markers, "clear_ignored", _raise_enotsup)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 2
    assert "Marker clear failed" in result.output
    # The rule should be removed even though the marker clear failed.
    assert "build/" not in (root / IGNORE_FILENAME).read_text(encoding="utf-8")


def test_ignore_then_synthetic_rules_event_no_spurious_mutation(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Order-of-ops invariant (spec § Order of operations): after ``ignore``
    completes, a synthetic RULES event reconciling the watched root
    must not trigger a mark-or-clear. State should already be consistent."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    runner = CliRunner()
    runner.invoke(cli.main, ["ignore", str(target), "--yes"])

    # Snapshot post-verb state.
    set_calls_before = list(fake_markers.set_calls)
    clear_calls_before = list(fake_markers.clear_calls)

    # Build a fresh cache from the now-mutated rule file (mirroring what
    # the daemon does on RULES event).
    cache = RuleCache()
    cache.load_root(root)
    # Run reconcile_subtree directly (skipping debouncer) on the watched
    # root — this is what the daemon's _dispatch does for a RULES event.
    reconcile.reconcile_subtree(root, root, cache)

    # No additional set_ignored or clear_ignored calls should have happened
    # — the marker is already correct.
    assert fake_markers.set_calls == set_calls_before
    assert fake_markers.clear_calls == clear_calls_before
    # Final state still correct.
    assert fake_markers.is_ignored(target)


def test_unignore_then_synthetic_rules_event_no_spurious_mutation(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symmetric to test_ignore_then_..._no_spurious_mutation: after `unignore`
    completes, a synthetic RULES event reconciling the watched root
    must not trigger a mark-or-clear. Validates rule-first-then-marker order
    in the inverse direction (Final Review test gap, filed by the PR review)."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    # Pre-state: rule + marker.
    (root / IGNORE_FILENAME).write_text("build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    runner.invoke(cli.main, ["unignore", str(target), "--yes"])

    # Snapshot post-verb state.
    set_calls_before = list(fake_markers.set_calls)
    clear_calls_before = list(fake_markers.clear_calls)

    # Build a fresh cache from the now-mutated rule file.
    cache = RuleCache()
    cache.load_root(root)
    reconcile.reconcile_subtree(root, root, cache)

    # No additional set/clear calls — final state is consistent.
    assert fake_markers.set_calls == set_calls_before
    assert fake_markers.clear_calls == clear_calls_before
    assert not fake_markers.is_ignored(target)

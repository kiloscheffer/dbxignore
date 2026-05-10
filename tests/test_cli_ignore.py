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
    (root / IGNORE_FILENAME).write_text("/build/\n", encoding="utf-8")
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
    # Ancestor-coverage match only (via_us_match=None): no child marker written.
    # The daemon prunes below ignored ancestors and never creates child-level
    # markers, so the verb follows the same convention (Fix 2 / Codex P2).
    assert not fake_markers.is_ignored(target)


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
    (root / IGNORE_FILENAME).write_text("/build/\n", encoding="utf-8")
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
    (root / IGNORE_FILENAME).write_text("/proj/build/\n", encoding="utf-8")
    (proj / IGNORE_FILENAME).write_text("/build/\n", encoding="utf-8")
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
    (proj / IGNORE_FILENAME).write_text("/build/\n**/build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 2
    assert "is also matched by" in result.output
    assert "**/build/" in result.output
    # Neither rule mutated; marker still set.
    content = (proj / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert "/build/\n" in content
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


def test_unignore_succeeds_when_only_blocker_is_a_negation(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 regression: a negation rule preceding the literal rule that
    currently ignores the path is NOT a blocker. Removing the literal rule
    leaves the negation, which un-ignores the path."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    # Rules order matters: !build/ first, then /build/. Last match wins → ignored.
    (root / IGNORE_FILENAME).write_text("!build/\n/build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    # The literal rule was removed; the negation stays.
    content = (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert "/build/" not in content
    assert "!build/" in content
    assert not fake_markers.is_ignored(target)


def test_unignore_blocks_when_positive_rule_remains_after_negation(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Companion test: a wildcard positive rule AFTER a negation IS a blocker
    (last-match-wins; the wildcard wins). Removing the literal canonical rule
    leaves wildcard + negation in original order, last is positive → blocker."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    # !build/ first, /build/ (canonical), then **/build/ (wildcard, last).
    # Removing /build/ leaves !build/ then **/build/. Last = positive blocker.
    (root / IGNORE_FILENAME).write_text("!build/\n/build/\n**/build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 2
    assert "is also matched by" in result.output
    # File unchanged.
    content = (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert "!build/" in content
    assert "/build/" in content
    assert "**/build/" in content


def test_unignore_dry_run_does_not_mutate(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    (root / IGNORE_FILENAME).write_text("/build/\n", encoding="utf-8")
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
    (root / IGNORE_FILENAME).write_text("/build/   \n", encoding="utf-8")
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
    (root / IGNORE_FILENAME).write_text("/build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target)], input="n\n")
    assert result.exit_code == 0, result.output
    assert "Aborted" in result.output
    # No mutation.
    assert "build/" in (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert fake_markers.is_ignored(target)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows filesystems forbid newline in filenames",
)
def test_ignore_rejects_newline_in_path_component(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 regression: ensure the CLI verb propagates the format_literal_rule
    rejection to a user-friendly exit-2 error, with no rule file created and no
    marker set."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "foo\n*.tmp"
    target.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 2
    assert "non-space whitespace" in result.output
    # No mutation occurred.
    assert not (root / IGNORE_FILENAME).exists()
    assert not fake_markers.is_ignored(target)


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="symlinks-as-target only supported on macOS (Linux refuses user.* xattrs on symlinks; Windows symlink creation requires admin)",
)
def test_ignore_preserves_symlink_path(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 regression: when the target is a symlink, the verb must operate
    on the LINK (markers apply to the link per CLAUDE.md's symlink invariant),
    not the symlink's target. Specifically: a symlink under Dropbox pointing
    outside Dropbox must NOT be rejected as "not under any Dropbox root"."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    # Set up: a directory outside the Dropbox root that the symlink targets.
    outside = tmp_path.parent / "outside_dropbox"
    outside.mkdir(exist_ok=True)
    # Symlink under Dropbox pointing outside.
    link = root / "external_link"
    link.symlink_to(outside)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(link), "--yes"])
    # Primary regression assertion: the link itself is under Dropbox, so the
    # command must succeed (previously failed with "not under any Dropbox root").
    assert result.exit_code == 0, result.output
    # set_ignored was called exactly once (for the link, not the target).
    assert len(fake_markers.set_calls) == 1
    # Rule file references the link's name (`external_link`), not the target.
    rule_content = (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert "external_link" in rule_content
    assert "outside_dropbox" not in rule_content


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason="symlinks-as-target only supported on macOS (Linux refuses user.* xattrs on symlinks; Windows symlink creation requires admin)",
)
def test_ignore_symlink_target_inside_dropbox_marks_link_not_target(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the link points within Dropbox, the command accepts the link and
    calls set_ignored exactly once — the operation is link-scoped, not recursive
    into target."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    inner = root / "inner"
    inner.mkdir()
    link = root / "link_to_inner"
    link.symlink_to(inner)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(link), "--yes"])
    assert result.exit_code == 0, result.output
    # set_ignored called exactly once — the link, not the target separately.
    assert len(fake_markers.set_calls) == 1
    # Rule file references the link's name, not `inner`.
    rule_content = (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert "link_to_inner" in rule_content


def test_ignore_dry_run_does_not_mutate_in_half_state(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Half-state (rule on disk, marker missing) + --dry-run: must NOT mutate
    the marker."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    (root / IGNORE_FILENAME).write_text("/build/\n", encoding="utf-8")
    assert not fake_markers.is_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would set marker" in result.output
    # Marker still not set — the dry-run was honored.
    assert not fake_markers.is_ignored(target)


def test_ignore_half_state_prompts_when_yes_omitted(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P1 regression: half-state recovery (rule on disk, marker missing)
    must show the destructive-action confirmation when --yes is not passed.
    Setting the marker has the same Dropbox-side consequences as the main
    mutation path; the user must have the same opportunity to abort."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    # Pre-state: rule on disk, marker not set (half-state).
    (root / IGNORE_FILENAME).write_text("/build/\n", encoding="utf-8")
    assert not fake_markers.is_ignored(target)
    runner = CliRunner()
    # No --yes; user types "n" to abort.
    result = runner.invoke(cli.main, ["ignore", str(target)], input="n\n")
    assert result.exit_code == 0, result.output
    assert "Aborted" in result.output
    # Marker MUST still be unset — abort honored.
    assert not fake_markers.is_ignored(target)


def test_ignore_half_state_prompts_then_proceeds_on_yes(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confirmation flow with user typing 'y' completes the half-state recovery."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    (root / IGNORE_FILENAME).write_text("/build/\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target)], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Set marker on" in result.output
    assert fake_markers.is_ignored(target)


def test_ignore_marker_oserror_exits_2_with_message(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ENOTSUP / FAT32 case: marker write fails; rule is on disk; verb exits 2
    with a user-friendly message that names both the failure cause and the
    daemon's eventual recovery path."""
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
    (root / IGNORE_FILENAME).write_text("/build/\n", encoding="utf-8")
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
    in the inverse direction."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    # Pre-state: rule + marker.
    (root / IGNORE_FILENAME).write_text("/build/\n", encoding="utf-8")
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


# ---------------------------------------------------------------------------
# Fix 1 (Codex P1) regression: anchored rule must not match unrelated subtrees
# ---------------------------------------------------------------------------


def test_ignore_rule_does_not_match_unrelated_subtree(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P1 regression: a rule generated for ``root/build`` must NOT match
    ``root/proj/build``. Without leading-``/`` anchoring, gitignore treats
    ``build/`` as matching every ``build/`` anywhere under the rule file's
    mount, causing Dropbox to mark unrelated subtrees ignored."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    # An unrelated `build/` that must NOT be matched after the ignore.
    other_build = root / "proj" / "build"
    other_build.mkdir(parents=True)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    # Verify match scope via the cache, not just rule text.
    cache = RuleCache()
    cache.load_root(root)
    assert cache.match(target)
    assert not cache.match(other_build), (
        "rule must be anchored — should not match unrelated `proj/build/`"
    )


# ---------------------------------------------------------------------------
# Fix 2 (Codex P2) regression: _select_rule_file must find mixed-case ancestor
# ---------------------------------------------------------------------------


def test_select_rule_file_finds_mixed_case_ancestor(
    tmp_path: Path, require_case_sensitive_fs: None
) -> None:
    """Codex P2 regression: existing ``.DropboxIgnore`` ancestor must be reused
    by ``_select_rule_file``, not silently bypassed (creating a duplicate
    canonical-cased file would cause the ancestor's rules to stop applying)."""
    root = tmp_path
    proj = root / "proj"
    proj.mkdir()
    mixed_case_file = proj / ".DropboxIgnore"
    mixed_case_file.touch()
    target = proj / "build"
    target.mkdir()
    selected = cli._select_rule_file(target, root)
    assert selected == mixed_case_file


# ---------------------------------------------------------------------------
# Fix 3 regression: partial-disappear TOCTOU — exit 2 and preserve marker
# ---------------------------------------------------------------------------


def test_unignore_partial_disappearance_exits_2(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review-finding regression: when removable spans 2 files and one file's
    ``remove_rule`` returns 0 (rule already vanished — TOCTOU) while another
    succeeded, the verb must exit 2 — NOT silently clear the marker, which
    would let the daemon re-set it from the residual rule."""
    from dbxignore import rules

    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    proj = root / "proj"
    proj.mkdir()
    target = proj / "build"
    target.mkdir()
    (root / IGNORE_FILENAME).write_text("/proj/build/\n", encoding="utf-8")
    (proj / IGNORE_FILENAME).write_text("/build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)

    # Simulate file2's rule vanishing between cache load and unignore.
    proj_ignore = proj / IGNORE_FILENAME
    real_remove_rule = rules.remove_rule

    def fake_remove_rule(rule_file: Path, rule_line: str) -> int:
        if rule_file == proj_ignore:
            return 0  # TOCTOU: file or rule already disappeared.
        return real_remove_rule(rule_file, rule_line)

    monkeypatch.setattr(rules, "remove_rule", fake_remove_rule)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 2
    assert "disappeared between read and write" in result.output
    # Marker MUST still be set — we refused to clear it because residual rule remains.
    assert fake_markers.is_ignored(target)


# ---------------------------------------------------------------------------
# Fix 4 regression: Dropbox root guard in ignore and unignore (item #93)
# ---------------------------------------------------------------------------


def test_ignore_refuses_dropbox_root_target(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P1 regression: `dbxignore ignore <root>` would otherwise produce
    a degenerate `//` rule AND mark the entire Dropbox root as ignored —
    catastrophic since Dropbox would remove the root from cloud and propagate
    the deletion to every linked device."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(root), "--yes"])
    assert result.exit_code == 2
    assert "Dropbox root" in result.output
    # No mutation: marker not set on root, no .dropboxignore created.
    assert not fake_markers.is_ignored(root)
    assert not (root / IGNORE_FILENAME).exists()


def test_unignore_refuses_dropbox_root_target(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symmetric refusal for unignore — root-as-target is rejected in both
    directions."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(root), "--yes"])
    assert result.exit_code == 2
    assert "Dropbox root" in result.output


# ---------------------------------------------------------------------------
# Fix 5 regression: .dropboxignore filename guard in ignore and unignore
# ---------------------------------------------------------------------------


def test_ignore_refuses_dropboxignore_filename(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: ``.dropboxignore`` files are never marked ignored per the
    project invariant. The verb must refuse before writing a self-referential
    rule that the daemon would then have to clean up."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / IGNORE_FILENAME
    target.touch()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 2
    assert ".dropboxignore" in result.output
    assert not target.read_text(encoding="utf-8")  # no self-referential rule was written
    assert not fake_markers.is_ignored(target)


def test_unignore_refuses_dropboxignore_filename(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symmetric refusal for unignore — the project invariant rules out
    .dropboxignore-as-target regardless of direction."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / IGNORE_FILENAME
    target.touch()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 2
    assert ".dropboxignore" in result.output


# ---------------------------------------------------------------------------
# Codex P2 Fix 2 regression: unignore clears orphan markers with no rule
# ---------------------------------------------------------------------------


def test_unignore_clears_orphan_marker_with_no_rule(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 regression: marker set but no matching rule (e.g., user
    manually edited .dropboxignore while daemon was stopped). unignore must
    clear the orphan marker, not exit silent — symmetric to ignore's
    half-state recovery for the inverse direction."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    # No .dropboxignore on disk. But marker IS set (orphan).
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    assert "marker cleared" in result.output
    assert not fake_markers.is_ignored(target)


def test_unignore_dry_run_orphan_marker_does_not_mutate(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The orphan-marker recovery path honors --dry-run."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would clear marker" in result.output
    # Marker still set — dry-run honored.
    assert fake_markers.is_ignored(target)


# ---------------------------------------------------------------------------
# Codex P2 Fix 1 regression: unignore resolves canonical cache key to disk
# ---------------------------------------------------------------------------


def test_unignore_handles_mixed_case_rule_file_on_disk(
    tmp_path: Path,
    fake_markers: FakeMarkers,
    monkeypatch: pytest.MonkeyPatch,
    require_case_sensitive_fs: None,
) -> None:
    """Codex P2 regression: on a case-sensitive FS with only ``.DropboxIgnore``
    on disk, ``RuleCache`` stores it under the canonical lowercase cache key,
    so ``Match.ignore_file`` refers to a non-existent path.  The verb must
    resolve canonical-to-disk before calling ``remove_rule``, otherwise
    ``unignore`` cannot undo paths whose rule lives in a mixed-case file."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    mixed_case_rule_file = root / ".DropboxIgnore"
    mixed_case_rule_file.write_text("/build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    # Rule removed from the actual on-disk file (.DropboxIgnore), not a phantom canonical.
    assert "/build/" not in mixed_case_rule_file.read_text(encoding="utf-8")
    # Marker cleared.
    assert not fake_markers.is_ignored(target)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows filesystems forbid trailing space in filenames",
)
def test_ignore_then_unignore_round_trip_for_trailing_space_filename(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 regression: a file named `foo ` (trailing space) goes through
    `format_literal_rule`'s trailing-space escape (commit 88a7cee). The /simplify
    pass (d0ac827) had dropped .rstrip() from the canonical side of the
    comparison, breaking the round-trip — the canonical `/foo\\ ` failed to
    equal m.pattern.rstrip() (`/foo\\`). Verify ignore-then-unignore works."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "foo "  # file with trailing space
    target.touch()
    runner = CliRunner()
    # ignore
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    assert fake_markers.is_ignored(target)
    # The rule on disk should have the escaped trailing space.
    rule_content = (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert r"/foo\ " in rule_content
    # unignore — must round-trip cleanly, NOT exit 2 with "is also matched by"
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    assert "rule removed" in result.output
    assert not fake_markers.is_ignored(target)


# ---------------------------------------------------------------------------
# Codex P2 Fix: symlinked-ancestor rejection + case-insensitive rule match
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows symlink creation requires admin privileges",
)
def test_ignore_rejects_path_under_symlinked_ancestor(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 regression: a symlinked ancestor between target and Dropbox
    root means the daemon (followlinks=False) would never reconcile the path,
    leaving the marker permanently orphaned. Reject at validation time."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    # Create an external dir + a child within it.
    external = tmp_path.parent / "external_dir"
    external.mkdir(exist_ok=True)
    child_in_external = external / "deep_child"
    child_in_external.mkdir()
    # Symlink the external dir into Dropbox.
    link = root / "link_into_external"
    link.symlink_to(external)
    target_via_link = link / "deep_child"
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target_via_link), "--yes"])
    assert result.exit_code == 2
    assert "symlinked ancestor" in result.output
    # No mutation: marker not set, no rule file mutated.
    assert not fake_markers.is_ignored(target_via_link)


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="Linux-only: kernel refuses user.* xattrs on symlinks",
)
def test_ignore_rejects_symlink_target_on_linux(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 regression: on Linux, symlink targets must be rejected at
    validation time. Otherwise the verb would write the rule successfully but
    fail the marker write (EPERM), and the daemon (followlinks=False) would
    never recover the orphan rule."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    inner = root / "inner"
    inner.mkdir()
    link = root / "link_to_inner"
    link.symlink_to(inner)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(link), "--yes"])
    assert result.exit_code == 2
    assert "is a symlink" in result.output
    assert "EPERM" in result.output
    # No mutation: rule file not created, marker not set on link or target.
    assert not (root / IGNORE_FILENAME).exists()
    assert not fake_markers.is_ignored(link)
    assert not fake_markers.is_ignored(inner)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows symlink creation requires admin privileges",
)
def test_ignore_accepts_path_via_outside_dropbox_alias(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 regression: when the user supplies a path through an
    out-of-Dropbox symlink alias to a file that's actually inside Dropbox,
    the verb must accept and operate on the canonical path. The unresolved
    path fails lexical containment (alias is outside Dropbox); the verb
    falls back to the resolved path which is canonically inside Dropbox."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    # Create an out-of-Dropbox symlink that aliases the Dropbox root.
    alias_root = tmp_path.parent / "alias_to_dropbox"
    if alias_root.exists() or alias_root.is_symlink():
        alias_root.unlink()
    try:
        alias_root.symlink_to(root)
    except OSError as e:
        # Symlink creation failed; skip the test (e.g., older Windows without admin).
        pytest.skip(f"cannot create symlink: {e}")
    # User types the path via the alias.
    aliased_target = alias_root / "build"
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(aliased_target), "--yes"])
    assert result.exit_code == 0, result.output
    # The rule + marker landed on the canonical path (root / "build"), not
    # the alias path. Verify by checking the rule file content references
    # the canonical relative path.
    content = (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert "/build/" in content
    # FakeMarkers normalizes paths via resolve, so set_calls should record
    # the resolved canonical path. The verb's set_ignored argument was the
    # canonical target.
    assert fake_markers.is_ignored(target)
    # Cleanup the alias.
    alias_root.unlink()


def test_ignore_then_unignore_case_insensitive_match(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 regression: on case-insensitive FS, ignore writes a rule with
    the on-disk casing, but a subsequent unignore with different user-typed
    casing must still match the rule (mirrors pathspec's case-insensitive
    matching at the pattern layer). Use string casing only — no symlink or
    actual case-insensitive FS needed since we're testing the comparison
    logic, not filesystem behavior."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    # Pre-state: rule with capital 'B' already in the file (different casing
    # from what canonical would produce for `build` target).
    (root / IGNORE_FILENAME).write_text("/Build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    assert "rule removed" in result.output
    # Rule removed (case-insensitive comparison classified it as removable).
    assert "/Build/" not in (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert not fake_markers.is_ignored(target)


# ---------------------------------------------------------------------------
# Codex P2 fixes (PR #191): Linux-symlink scope + ancestor-only marker write
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="Linux-only: tests symlink target acceptance for unignore (rejected for ignore)",
)
def test_unignore_accepts_symlink_target_on_linux(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 regression: round-9 added a shared Linux-symlink-target
    rejection in _validate_target_under_root, but it's only justified for
    `ignore` (marker-write fails). `unignore` should be allowed to remove a
    stale rule for a symlink target — clear_ignored is a no-op when no xattr
    exists, no orphan-rule risk."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    inner = root / "inner"
    inner.mkdir()
    link = root / "link_to_inner"
    link.symlink_to(inner)
    # Pre-state: rule on disk for the symlink (could be stale, manually-added).
    (root / IGNORE_FILENAME).write_text("/link_to_inner\n", encoding="utf-8")
    fake_markers.set_ignored(link)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(link), "--yes"])
    assert result.exit_code == 0, result.output
    assert "rule removed" in result.output
    # Rule cleaned up.
    content = (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert "/link_to_inner" not in content


def test_ignore_does_not_set_child_marker_under_ancestor_rule(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 regression: when cache.match is True only because an ancestor
    rule covers the subtree (e.g., `parent/` covers `parent/child`), the
    half-state recovery should NOT write a marker on the child. The daemon
    prunes below ignored ancestors and never creates child-level markers,
    so writing one creates an artifact the daemon wouldn't produce, which
    would survive `unignore parent/` until the next reconcile."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    parent = root / "parent"
    parent.mkdir()
    child = parent / "child"
    child.mkdir()
    # Ancestor rule covers the subtree; no rule on child specifically.
    (root / IGNORE_FILENAME).write_text("/parent/\n", encoding="utf-8")
    # Marker on parent (would be set by daemon's reconcile).
    fake_markers.set_ignored(parent)
    # No marker on child (daemon prunes below).
    assert not fake_markers.is_ignored(child)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(child), "--yes"])
    assert result.exit_code == 0, result.output
    # Verb noted ancestor coverage. Did NOT add a child marker.
    assert "already covered" in result.output
    assert not fake_markers.is_ignored(child), (
        "must not write a child marker — daemon doesn't create one under ignored ancestors"
    )
    # No new rule added either.
    assert (root / IGNORE_FILENAME).read_text(encoding="utf-8") == "/parent/\n"

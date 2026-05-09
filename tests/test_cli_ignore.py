"""CLI integration tests for ``dbxignore ignore`` and ``dbxignore unignore`` (item #93).

Covers helper unit tests, command happy paths, idempotence + redundancy
branches, --yes / --dry-run flags, error paths, and daemon-coexistence smoke.
"""

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from dbxignore import cli, state
from dbxignore.rules import IGNORE_FILENAME
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

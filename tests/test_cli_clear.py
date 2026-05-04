"""Tests for ``dbxignore clear`` (followup item 61).

Covers: marker clearing, daemon-alive refusal + --force override, --dry-run
preview, --yes skipping the confirmation, path scoping, .dropboxignore and
state.json left untouched, and the no-markers / no-roots / out-of-root error
paths.
"""

import os
from pathlib import Path

from click.testing import CliRunner

from dbxignore import cli, state


def _setup_marked_tree(tmp_path: Path, fake_markers, monkeypatch) -> dict[str, Path]:
    """Build a small tree with one marked dir, one marked file, and a
    .dropboxignore + state.json. Returns the relevant paths for assertions.

    Returns dict with keys:
      root, marked_dir, marked_file, dropboxignore, state_json
    """
    root = tmp_path
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr(state, "default_path", lambda: root / "_state.json")
    # Ensure no daemon-alive false positive blocks the test.
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid: False)

    marked_dir = root / "build"
    marked_dir.mkdir()
    marked_file = root / "scratch.tmp"
    marked_file.touch()
    dropboxignore = root / ".dropboxignore"
    dropboxignore.write_text("build/\n*.tmp\n", encoding="utf-8")
    state_json = root / "_state.json"
    state_json.write_text("{}", encoding="utf-8")  # opaque-but-existing

    fake_markers.set_ignored(marked_dir)
    fake_markers.set_ignored(marked_file)

    return {
        "root": root,
        "marked_dir": marked_dir,
        "marked_file": marked_file,
        "dropboxignore": dropboxignore,
        "state_json": state_json,
    }


def test_clear_clears_markers(tmp_path, fake_markers, monkeypatch):
    """Happy path: --yes skips confirmation; markers on disk are cleared."""
    paths = _setup_marked_tree(tmp_path, fake_markers, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", "--yes"])

    assert result.exit_code == 0, result.output
    assert "cleared=2" in result.output
    assert not fake_markers.is_ignored(paths["marked_dir"])
    assert not fake_markers.is_ignored(paths["marked_file"])


def test_clear_leaves_dropboxignore_and_state_json_untouched(tmp_path, fake_markers, monkeypatch):
    """Inverse-of-apply: rule files and state.json survive a clear."""
    paths = _setup_marked_tree(tmp_path, fake_markers, monkeypatch)
    di_content = paths["dropboxignore"].read_text(encoding="utf-8")
    sj_content = paths["state_json"].read_text(encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", "--yes"])

    assert result.exit_code == 0, result.output
    assert paths["dropboxignore"].read_text(encoding="utf-8") == di_content
    assert paths["state_json"].read_text(encoding="utf-8") == sj_content


def test_clear_refuses_when_daemon_alive(tmp_path, fake_markers, monkeypatch):
    """The daemon would re-apply markers — refuse with exit 2 + guidance."""
    paths = _setup_marked_tree(tmp_path, fake_markers, monkeypatch)
    # Set up a state.json with a pid; pin is_daemon_alive=True.
    s = state.State(daemon_pid=os.getpid())
    state.write(s, paths["root"] / "_state.json")
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid: True)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", "--yes"])

    assert result.exit_code == 2
    assert "daemon is running" in result.output
    assert "--force" in result.output
    # Markers must still be set.
    assert fake_markers.is_ignored(paths["marked_dir"])


def test_clear_force_overrides_daemon_alive(tmp_path, fake_markers, monkeypatch):
    """--force lets the user override the daemon-alive guard knowingly."""
    paths = _setup_marked_tree(tmp_path, fake_markers, monkeypatch)
    s = state.State(daemon_pid=os.getpid())
    state.write(s, paths["root"] / "_state.json")
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid: True)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", "--force", "--yes"])

    assert result.exit_code == 0, result.output
    assert "cleared=2" in result.output
    assert not fake_markers.is_ignored(paths["marked_dir"])


def test_clear_dry_run_prints_but_does_not_clear(tmp_path, fake_markers, monkeypatch):
    """--dry-run lists candidates without touching markers; --yes is implied
    not needed because nothing actually changes."""
    paths = _setup_marked_tree(tmp_path, fake_markers, monkeypatch)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "would clear:" in result.output
    assert "would_clear=2" in result.output
    assert "(dry-run)" in result.output
    assert fake_markers.is_ignored(paths["marked_dir"])
    assert fake_markers.is_ignored(paths["marked_file"])


def test_clear_path_arg_scopes_to_subtree(tmp_path, fake_markers, monkeypatch):
    """Optional PATH arg restricts the walk to that subtree, parallel to apply."""
    root = tmp_path
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr(state, "default_path", lambda: root / "_state.json")
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid: False)

    sub_a = root / "a"
    sub_a.mkdir()
    file_a = sub_a / "thing.tmp"
    file_a.touch()
    sub_b = root / "b"
    sub_b.mkdir()
    file_b = sub_b / "thing.tmp"
    file_b.touch()
    fake_markers.set_ignored(file_a)
    fake_markers.set_ignored(file_b)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", str(sub_a), "--yes"])

    assert result.exit_code == 0, result.output
    assert "cleared=1" in result.output
    assert not fake_markers.is_ignored(file_a)
    assert fake_markers.is_ignored(file_b)


def test_clear_path_outside_roots_errors(tmp_path, fake_markers, monkeypatch):
    """A path not under any root is a CLI error (exit 2), not a silent no-op."""
    root = tmp_path / "dropbox"
    root.mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr(state, "default_path", lambda: root / "_state.json")
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid: False)

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", str(elsewhere), "--yes"])

    assert result.exit_code == 2
    assert "not under any Dropbox root" in result.output


def test_clear_no_markers_prints_message(tmp_path, fake_markers, monkeypatch):
    """Clean tree: distinct message instead of cleared=0 (more readable)."""
    root = tmp_path
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr(state, "default_path", lambda: root / "_state.json")
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid: False)
    (root / "file.txt").touch()

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", "--yes"])

    assert result.exit_code == 0, result.output
    assert "No markers to clear" in result.output


def test_clear_confirmation_aborts_on_no(tmp_path, fake_markers, monkeypatch):
    """Without --yes, the prompt fires; saying 'n' aborts without clearing."""
    paths = _setup_marked_tree(tmp_path, fake_markers, monkeypatch)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear"], input="n\n")

    assert result.exit_code == 0, result.output
    assert "Aborted" in result.output
    assert fake_markers.is_ignored(paths["marked_dir"])
    assert fake_markers.is_ignored(paths["marked_file"])


def test_clear_confirmation_proceeds_on_yes(tmp_path, fake_markers, monkeypatch):
    """Without --yes, saying 'y' to the prompt clears the markers."""
    paths = _setup_marked_tree(tmp_path, fake_markers, monkeypatch)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear"], input="y\n")

    assert result.exit_code == 0, result.output
    assert "cleared=2" in result.output
    assert not fake_markers.is_ignored(paths["marked_dir"])

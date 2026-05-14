"""Tests for ``dbxignore clear``.

Covers: marker clearing, daemon-alive refusal + --force override, --dry-run
preview, --yes skipping the confirmation, path scoping, .dropboxignore and
state.json left untouched, and the no-markers / no-roots / out-of-root error
paths.
"""

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from dbxignore import cli, state
from tests.conftest import FakeMarkers


def _setup_marked_tree(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    """Build a small tree with one marked dir, one marked file, and a
    .dropboxignore + state.json. Returns the relevant paths for assertions.

    Returns dict with keys:
      root, marked_dir, marked_file, dropboxignore, state_json
    """
    root = tmp_path
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr(state, "default_path", lambda: root / "_state.json")
    # Ensure no daemon-alive false positive blocks the test.
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid, create_time=None: False)

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


def test_clear_clears_markers(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: --yes skips confirmation; markers on disk are cleared."""
    paths = _setup_marked_tree(tmp_path, fake_markers, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", "--yes"])

    assert result.exit_code == 0, result.output
    assert "cleared=2" in result.output
    assert not fake_markers.is_ignored(paths["marked_dir"])
    assert not fake_markers.is_ignored(paths["marked_file"])


def test_clear_leaves_dropboxignore_and_state_json_untouched(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inverse-of-apply: rule files and state.json survive a clear."""
    paths = _setup_marked_tree(tmp_path, fake_markers, monkeypatch)
    di_content = paths["dropboxignore"].read_text(encoding="utf-8")
    sj_content = paths["state_json"].read_text(encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", "--yes"])

    assert result.exit_code == 0, result.output
    assert paths["dropboxignore"].read_text(encoding="utf-8") == di_content
    assert paths["state_json"].read_text(encoding="utf-8") == sj_content


def test_clear_refuses_when_daemon_alive(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The daemon would re-apply markers — refuse with exit 2 + guidance."""
    paths = _setup_marked_tree(tmp_path, fake_markers, monkeypatch)
    # Set up a state.json with a pid; pin is_daemon_alive=True.
    s = state.State(daemon_pid=os.getpid())
    state.write(s, paths["root"] / "_state.json")
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid, create_time=None: True)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", "--yes"])

    assert result.exit_code == 2
    assert "daemon is running" in result.output
    assert "--force" in result.output
    # Markers must still be set.
    assert fake_markers.is_ignored(paths["marked_dir"])


def test_clear_force_overrides_daemon_alive(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--force lets the user override the daemon-alive guard knowingly."""
    paths = _setup_marked_tree(tmp_path, fake_markers, monkeypatch)
    s = state.State(daemon_pid=os.getpid())
    state.write(s, paths["root"] / "_state.json")
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid, create_time=None: True)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", "--force", "--yes"])

    assert result.exit_code == 0, result.output
    assert "cleared=2" in result.output
    assert not fake_markers.is_ignored(paths["marked_dir"])


def test_clear_refuses_when_state_json_unreadable(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed when state.json is present but `state.read()` returns None
    (locked, permission-denied, cloud-placeholder). Daemon liveness is unknown,
    so treating it as "no daemon" would let `clear` race a live daemon that
    re-marks within seconds."""
    paths = _setup_marked_tree(tmp_path, fake_markers, monkeypatch)
    # state.json was created by the fixture; simulate unreadable by stubbing
    # state.read to return None (the OSError fallback).
    monkeypatch.setattr(state, "read", lambda: None)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", "--yes"])

    assert result.exit_code == 2
    assert "unreadable" in result.output
    assert "--force" in result.output
    # Markers must still be set — fail-closed means no destructive action.
    assert fake_markers.is_ignored(paths["marked_dir"])


def test_clear_force_overrides_unreadable_state(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--force lets the user override the unreadable-state fail-closed arm
    when they know no daemon is running (mirrors the daemon-alive override)."""
    paths = _setup_marked_tree(tmp_path, fake_markers, monkeypatch)
    monkeypatch.setattr(state, "read", lambda: None)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", "--force", "--yes"])

    assert result.exit_code == 0, result.output
    assert "cleared=2" in result.output
    assert not fake_markers.is_ignored(paths["marked_dir"])


def test_clear_dry_run_prints_but_does_not_clear(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_clear_path_arg_scopes_to_subtree(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Optional PATH arg restricts the walk to that subtree, parallel to apply."""
    root = tmp_path
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr(state, "default_path", lambda: root / "_state.json")
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid, create_time=None: False)

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


def test_clear_path_arg_clears_marked_file_target(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PATH that is itself marked must be cleared, not treated as an empty walk."""
    root = tmp_path
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr(state, "default_path", lambda: root / "_state.json")
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid, create_time=None: False)

    target = root / "scratch.tmp"
    target.touch()
    fake_markers.set_ignored(target)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", str(target), "--yes"])

    assert result.exit_code == 0, result.output
    assert "cleared=1" in result.output
    assert not fake_markers.is_ignored(target)


def test_clear_path_arg_clears_marked_directory_target_without_descending(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A marked directory target is the marker to clear; descendants stay pruned."""
    root = tmp_path
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr(state, "default_path", lambda: root / "_state.json")
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid, create_time=None: False)

    target = root / "build"
    target.mkdir()
    child = target / "redundant-child-marker"
    child.touch()
    fake_markers.set_ignored(target)
    fake_markers.set_ignored(child)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", str(target), "--yes"])

    assert result.exit_code == 0, result.output
    assert "cleared=1" in result.output
    assert not fake_markers.is_ignored(target)
    assert fake_markers.is_ignored(child)


def test_clear_surfaces_scan_errors_and_exits_two(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 7 from external review: when the marker-scan walk hits an
    OSError on a path (e.g. ENOTSUP on a filesystem that doesn't support
    extended attributes), the count is surfaced via stderr and the command
    exits 2 — previously the read errors were swallowed silently and the
    user saw "No markers to clear" while the scan had actually failed."""
    import errno

    root = tmp_path
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr(state, "default_path", lambda: root / "_state.json")
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid, create_time=None: False)

    good = root / "good.tmp"
    good.touch()
    bad = root / "bad.tmp"
    bad.touch()
    fake_markers.set_ignored(good)

    real_is_ignored = fake_markers.is_ignored

    def selective_raise(path: Path) -> bool:
        if path.resolve() == bad.resolve():
            raise OSError(errno.ENOTSUP, "Operation not supported")
        return real_is_ignored(path)

    monkeypatch.setattr(fake_markers, "is_ignored", selective_raise)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", "--yes"])

    assert result.exit_code == 2, result.output
    assert "cleared=1" in result.output
    assert "scan_errors=1" in result.output
    assert "scan errors: 1" in result.output
    assert "bad.tmp" in result.output


def test_clear_surfaces_scan_errors_when_no_markers_found(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the scan errored on every walked path AND no markers were
    found, the previous shape fell through into either the "No markers
    to clear" message (in the `not to_clear and not scan_errors` arm)
    or the confirmation prompt, both swallowing the partial-scan-failure
    surface. The scan-error report and exit 2 must fire regardless of
    how many markers landed."""
    import errno

    root = tmp_path
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr(state, "default_path", lambda: root / "_state.json")
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid, create_time=None: False)

    bad = root / "bad.tmp"
    bad.touch()

    # No markers set. The walk will hit ENOTSUP on `bad`.
    def always_raise(path: Path) -> bool:
        raise OSError(errno.ENOTSUP, "Operation not supported")

    monkeypatch.setattr(fake_markers, "is_ignored", always_raise)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", "--yes"])

    assert result.exit_code == 2, result.output
    # `_report_scan_errors` emits to stderr; CliRunner merges by default.
    # The walk short-circuits on the first `is_ignored` failure (on the
    # target directory itself, before descending into children), so we
    # assert on the count + errno rather than the leaf-file name.
    assert "scan errors: 1" in result.output
    assert "Operation not supported" in result.output
    # No misleading "No markers to clear" success message.
    assert "No markers to clear" not in result.output


def test_clear_surfaces_scan_errors_when_user_aborts_prompt(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even when the user declines the confirmation prompt, scan errors
    that fired during the walk must still surface and the command must
    exit 2. The prior shape returned cleanly with "Aborted." and exit 0,
    hiding the partial-scan failure."""
    import errno

    root = tmp_path
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr(state, "default_path", lambda: root / "_state.json")
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid, create_time=None: False)

    good = root / "good.tmp"
    good.touch()
    bad = root / "bad.tmp"
    bad.touch()
    fake_markers.set_ignored(good)

    real_is_ignored = fake_markers.is_ignored

    def selective_raise(path: Path) -> bool:
        if path.resolve() == bad.resolve():
            raise OSError(errno.ENOTSUP, "Operation not supported")
        return real_is_ignored(path)

    monkeypatch.setattr(fake_markers, "is_ignored", selective_raise)

    runner = CliRunner()
    # No --yes: prompt fires; "n" declines.
    result = runner.invoke(cli.main, ["clear"], input="n\n")

    assert result.exit_code == 2, result.output
    assert "Aborted" in result.output
    # Scan errors STILL surface even on the abort path.
    assert "scan errors: 1" in result.output
    assert "bad.tmp" in result.output
    # Marker was not cleared — user aborted.
    assert fake_markers.is_ignored(good)


def test_clear_path_outside_roots_errors(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path not under any root is a CLI error (exit 2), not a silent no-op."""
    root = tmp_path / "dropbox"
    root.mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr(state, "default_path", lambda: root / "_state.json")
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid, create_time=None: False)

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", str(elsewhere), "--yes"])

    assert result.exit_code == 2
    assert "not under any Dropbox root" in result.output


def test_clear_no_markers_prints_message(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clean tree: distinct message instead of cleared=0 (more readable)."""
    root = tmp_path
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr(state, "default_path", lambda: root / "_state.json")
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid, create_time=None: False)
    (root / "file.txt").touch()

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear", "--yes"])

    assert result.exit_code == 0, result.output
    assert "No markers to clear" in result.output


def test_clear_confirmation_aborts_on_no(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --yes, the prompt fires; saying 'n' aborts without clearing."""
    paths = _setup_marked_tree(tmp_path, fake_markers, monkeypatch)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear"], input="n\n")

    assert result.exit_code == 0, result.output
    assert "Aborted" in result.output
    assert fake_markers.is_ignored(paths["marked_dir"])
    assert fake_markers.is_ignored(paths["marked_file"])


def test_clear_confirmation_proceeds_on_yes(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --yes, saying 'y' to the prompt clears the markers."""
    paths = _setup_marked_tree(tmp_path, fake_markers, monkeypatch)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["clear"], input="y\n")

    assert result.exit_code == 0, result.output
    assert "cleared=2" in result.output
    assert not fake_markers.is_ignored(paths["marked_dir"])

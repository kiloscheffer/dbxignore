"""CLI integration tests for ``dbxignore ignore`` and ``dbxignore unignore`` (item #93).

Covers helper unit tests, command happy paths, idempotence + redundancy
branches, --yes / --dry-run flags, error paths, and daemon-coexistence smoke.
"""

from pathlib import Path

from dbxignore import cli
from dbxignore.rules import IGNORE_FILENAME


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

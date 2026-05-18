from pathlib import Path

from dbxignore.rules import RuleCache
from tests.conftest import WriteFile


def test_nested_dropboxignore_adds_rules(tmp_path: Path, write_file: WriteFile) -> None:
    # Root-level ignores nothing; nested ignores 'build/'.
    write_file(tmp_path / ".dropboxignore", "")
    (tmp_path / "proj").mkdir()
    write_file(tmp_path / "proj" / ".dropboxignore", "build/\n")
    (tmp_path / "proj" / "build").mkdir()
    (tmp_path / "proj" / "src").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "proj" / "build") is True
    assert cache.match(tmp_path / "proj" / "src") is False


def test_child_can_negate_ancestor_match(tmp_path: Path, write_file: WriteFile) -> None:
    write_file(tmp_path / ".dropboxignore", "*.log\n")
    (tmp_path / "proj").mkdir()
    write_file(tmp_path / "proj" / ".dropboxignore", "!important.log\n")
    (tmp_path / "proj" / "a.log").touch()
    (tmp_path / "proj" / "important.log").touch()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "proj" / "a.log") is True
    assert cache.match(tmp_path / "proj" / "important.log") is False


def test_ancestor_rule_applies_to_deep_descendant(tmp_path: Path, write_file: WriteFile) -> None:
    write_file(tmp_path / ".dropboxignore", "**/node_modules/\n")
    (tmp_path / "a" / "b" / "c" / "node_modules").mkdir(parents=True)

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "a" / "b" / "c" / "node_modules") is True


def test_same_file_negation(tmp_path: Path, write_file: WriteFile) -> None:
    # Single .dropboxignore with *.log ignored but !important.log as exception.
    write_file(tmp_path / ".dropboxignore", "*.log\n!important.log\n")
    (tmp_path / "a.log").touch()
    (tmp_path / "important.log").touch()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "a.log") is True
    assert cache.match(tmp_path / "important.log") is False


def test_three_level_reignore(tmp_path: Path, write_file: WriteFile) -> None:
    # root ignores *.log; proj un-ignores important.log; proj/deep re-ignores it.
    write_file(tmp_path / ".dropboxignore", "*.log\n")
    (tmp_path / "proj").mkdir()
    write_file(tmp_path / "proj" / ".dropboxignore", "!important.log\n")
    (tmp_path / "proj" / "deep").mkdir()
    write_file(tmp_path / "proj" / "deep" / ".dropboxignore", "important.log\n")
    (tmp_path / "proj" / "deep" / "important.log").touch()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "proj" / "deep" / "important.log") is True


def test_shallow_match_preserved_when_deeper_spec_unrelated(
    tmp_path: Path, write_file: WriteFile
) -> None:
    """If an ancestor matched, a deeper .dropboxignore with UNRELATED rules
    must not clobber the match. Regression guard: naive spec-level verdict
    (e.g. bare PathSpec.match_file) would return False for a spec whose
    patterns don't match and wrongly overwrite the ancestor's True."""
    write_file(tmp_path / ".dropboxignore", "*.log\n")
    (tmp_path / "proj").mkdir()
    write_file(tmp_path / "proj" / ".dropboxignore", "foo.txt\n")  # unrelated
    (tmp_path / "proj" / "a.log").touch()

    cache = RuleCache()
    cache.load_root(tmp_path)

    # a.log matched *.log at root; proj/.dropboxignore doesn't touch it.
    assert cache.match(tmp_path / "proj" / "a.log") is True

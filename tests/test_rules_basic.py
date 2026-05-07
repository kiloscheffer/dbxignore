from pathlib import Path

import pytest

from dbxignore.rules import RuleCache
from tests.conftest import WriteFile


def test_match_rejects_relative_path(tmp_path: Path, write_file: WriteFile) -> None:
    """Caller contract: match()/explain() require absolute paths. The internal
    resolve() used to mask relative-path bugs by silently normalizing; now
    they raise loudly so the bug surfaces at the call site instead."""
    write_file(tmp_path / ".dropboxignore", "build/\n")
    cache = RuleCache()
    cache.load_root(tmp_path)

    with pytest.raises(ValueError, match="absolute"):
        cache.match(Path("build"))
    with pytest.raises(ValueError, match="absolute"):
        cache.explain(Path("build"))


def test_flat_match_sets_true_for_matching_directory(tmp_path: Path, write_file: WriteFile) -> None:
    write_file(tmp_path / ".dropboxignore", "node_modules/\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "src").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "node_modules") is True
    assert cache.match(tmp_path / "src") is False


def test_empty_dropboxignore_matches_nothing(tmp_path: Path, write_file: WriteFile) -> None:
    write_file(tmp_path / ".dropboxignore", "")
    (tmp_path / "foo").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "foo") is False


def test_comment_and_blank_lines_ignored(tmp_path: Path, write_file: WriteFile) -> None:
    write_file(tmp_path / ".dropboxignore", "# comment\n\nbuild/\n")
    (tmp_path / "build").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "build") is True


def test_no_dropboxignore_files_matches_nothing(tmp_path: Path) -> None:
    (tmp_path / "anything").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "anything") is False


def test_indented_hash_line_is_active_pattern(tmp_path: Path) -> None:
    """Lines like `   #literal` are active patterns per gitignore semantics, not comments.

    Pins the comment-filter fix in `_build_entries`: the filter checks
    `raw.startswith("#")` (not `raw.strip().startswith("#")`) so leading
    whitespace before `#` keeps the line in the active-pattern set.
    """
    rules_path = tmp_path / ".dropboxignore"
    rules_path.write_text("   #literal\n", encoding="utf-8")
    cache = RuleCache()
    cache.load_root(tmp_path)

    loaded = cache._rules[rules_path]
    assert len(loaded.entries) == 1
    assert loaded.entries[0][0] == 0

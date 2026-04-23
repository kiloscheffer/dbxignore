from pathlib import Path

import pytest

from dbxignore.rules import RuleCache


def test_match_rejects_relative_path(tmp_path, write_file):
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


def test_flat_match_sets_true_for_matching_directory(tmp_path, write_file):
    write_file(tmp_path / ".dropboxignore", "node_modules/\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "src").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "node_modules") is True
    assert cache.match(tmp_path / "src") is False


def test_empty_dropboxignore_matches_nothing(tmp_path, write_file):
    write_file(tmp_path / ".dropboxignore", "")
    (tmp_path / "foo").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "foo") is False


def test_comment_and_blank_lines_ignored(tmp_path, write_file):
    write_file(tmp_path / ".dropboxignore", "# comment\n\nbuild/\n")
    (tmp_path / "build").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "build") is True


def test_no_dropboxignore_files_matches_nothing(tmp_path):
    (tmp_path / "anything").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "anything") is False

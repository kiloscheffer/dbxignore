from pathlib import Path

from dropboxignore.rules import RuleCache


def _write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_flat_match_sets_true_for_matching_directory(tmp_path):
    _write(tmp_path / ".dropboxignore", "node_modules/\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "src").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "node_modules") is True
    assert cache.match(tmp_path / "src") is False


def test_empty_dropboxignore_matches_nothing(tmp_path):
    _write(tmp_path / ".dropboxignore", "")
    (tmp_path / "foo").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "foo") is False


def test_comment_and_blank_lines_ignored(tmp_path):
    _write(tmp_path / ".dropboxignore", "# comment\n\nbuild/\n")
    (tmp_path / "build").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "build") is True


def test_no_dropboxignore_files_matches_nothing(tmp_path):
    (tmp_path / "anything").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "anything") is False

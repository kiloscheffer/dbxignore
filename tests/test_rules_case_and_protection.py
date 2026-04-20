from pathlib import Path

from dropboxignore.rules import RuleCache


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_case_insensitive_match(tmp_path):
    _write(tmp_path / ".dropboxignore", "node_modules/\n")
    (tmp_path / "Node_Modules").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "Node_Modules") is True


def test_dropboxignore_file_itself_never_matches(tmp_path):
    # A greedy rule at root that would otherwise sweep up the .dropboxignore file.
    _write(tmp_path / ".dropboxignore", "*\n")
    (tmp_path / "proj").mkdir()
    _write(tmp_path / "proj" / ".dropboxignore", "build/\n")

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / ".dropboxignore") is False
    assert cache.match(tmp_path / "proj" / ".dropboxignore") is False

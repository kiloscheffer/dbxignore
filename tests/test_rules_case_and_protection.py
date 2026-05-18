from pathlib import Path

from dbxignore.rules import RuleCache
from tests.conftest import WriteFile


def test_case_insensitive_match(tmp_path: Path, write_file: WriteFile) -> None:
    write_file(tmp_path / ".dropboxignore", "node_modules/\n")
    (tmp_path / "Node_Modules").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "Node_Modules") is True


def test_dropboxignore_file_itself_never_matches(tmp_path: Path, write_file: WriteFile) -> None:
    # A greedy rule at root that would otherwise sweep up the .dropboxignore file.
    write_file(tmp_path / ".dropboxignore", "*\n")
    (tmp_path / "proj").mkdir()
    write_file(tmp_path / "proj" / ".dropboxignore", "build/\n")

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / ".dropboxignore") is False
    assert cache.match(tmp_path / "proj" / ".dropboxignore") is False

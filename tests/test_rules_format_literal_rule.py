"""Unit tests for ``rules.format_literal_rule`` (item #93).

Pure-function tests — no fixtures, no tmp_path needed, no monkeypatching.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dbxignore.rules import format_literal_rule


def test_dir_target_gets_trailing_slash(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = tmp_path / "build"
    target.mkdir()
    assert format_literal_rule(target, rule_file) == "/build/"


def test_file_target_no_trailing_slash(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = tmp_path / "notes.txt"
    target.touch()
    assert format_literal_rule(target, rule_file) == "/notes.txt"


def test_multi_segment_relative_path(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = tmp_path / "proj" / "foo" / "bar"
    target.mkdir(parents=True)
    assert format_literal_rule(target, rule_file) == "/proj/foo/bar/"


def test_meta_char_escaping_in_segment(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    # Windows filesystems reject `*` in filenames, so we use MagicMock
    # to create a Path-like object with relative_to and is_dir methods.
    target = MagicMock(spec=Path)
    target.relative_to.return_value = Path("foo*bar")
    target.is_dir.return_value = True
    assert format_literal_rule(target, rule_file) == r"/foo\*bar/"


def test_question_mark_and_brackets_escaped(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    # Windows filesystems reject `?` and `[` in filenames; use MagicMock
    # to test escaping logic independently.
    target = MagicMock(spec=Path)
    target.relative_to.return_value = Path("weird?name[ish]")
    target.is_dir.return_value = True
    assert format_literal_rule(target, rule_file) == r"/weird\?name\[ish\]/"


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows filesystems forbid backslash in filenames"
)
def test_literal_backslash_escaped_in_segment(tmp_path: Path) -> None:
    r"""Backslash is in `_META_CHARS_INLINE`. A literal directory named `back\slash`
    should produce the rule `back\\slash/`."""
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = MagicMock(spec=Path)
    target.relative_to.return_value = Path(r"back\slash")
    target.is_dir.return_value = True
    assert format_literal_rule(target, rule_file) == r"/back\\slash/"


def test_leading_bang_in_first_segment_escaped(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = tmp_path / "!important"
    target.mkdir()
    # Leading `!` would make pathspec treat the line as a negation; escape.
    assert format_literal_rule(target, rule_file) == r"/\!important/"


def test_leading_hash_in_first_segment_escaped(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = tmp_path / "#literal"
    target.mkdir()
    # Leading `#` at column 0 makes pathspec treat the line as a comment;
    # escape it so the rule stays an active pattern.
    assert format_literal_rule(target, rule_file) == r"/\#literal/"


def test_leading_bang_only_escaped_at_first_segment(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    # `!` in a non-leading segment is just a literal character to gitignore.
    target = tmp_path / "proj" / "!subdir"
    target.mkdir(parents=True)
    assert format_literal_rule(target, rule_file) == "/proj/!subdir/"


def test_target_must_be_under_rule_file_parent(tmp_path: Path) -> None:
    # If target is not relative_to(rule_file.parent), this is a programming
    # error in the caller (rule selection should always pick an ancestor).
    rule_file = tmp_path / "a" / ".dropboxignore"
    rule_file.parent.mkdir()
    rule_file.touch()
    target = tmp_path / "b" / "elsewhere"
    target.mkdir(parents=True)
    with pytest.raises(ValueError):
        format_literal_rule(target, rule_file)

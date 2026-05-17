"""Unit tests for ``rules.format_literal_rule``.

Pure-function tests — no fixtures, no tmp_path needed, no monkeypatching.
"""

import stat
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
    target.lstat.return_value.st_mode = stat.S_IFDIR
    assert format_literal_rule(target, rule_file) == r"/foo\*bar/"


def test_question_mark_and_brackets_escaped(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    # Windows filesystems reject `?` and `[` in filenames; use MagicMock
    # to test escaping logic independently.
    target = MagicMock(spec=Path)
    target.relative_to.return_value = Path("weird?name[ish]")
    target.lstat.return_value.st_mode = stat.S_IFDIR
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
    target.lstat.return_value.st_mode = stat.S_IFDIR
    assert format_literal_rule(target, rule_file) == r"/back\\slash/"


def test_leading_bang_in_first_segment_escaped(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = tmp_path / "!important"
    target.mkdir()
    # Leading `!` would make pathspec treat the line as a negation; escape.
    # The directory is real, not a symlink, so it gets the trailing slash.
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


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows filesystems forbid newline in filenames",
)
def test_newline_in_segment_raises_value_error(tmp_path: Path) -> None:
    """A path component containing ``\\n`` or ``\\r`` must raise
    ValueError, not silently produce a rule line that splits into two
    (with the suffix becoming an injected rule that could match unrelated
    files — Dropbox would remove them from cloud)."""
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = MagicMock(spec=Path)
    target.relative_to.return_value = Path("foo\n*.tmp")
    with pytest.raises(ValueError, match="non-space whitespace"):
        format_literal_rule(target, rule_file)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows filesystems forbid trailing spaces in filenames",
)
def test_file_target_with_trailing_space_escaped(tmp_path: Path) -> None:
    """A file named ``foo `` (trailing space) must produce rule
    ``/foo\\ `` (escaped) so pathspec does not strip the space and fail
    to match the literal target.  Without escaping, the marker is set on
    ``foo `` but the rule matches ``foo`` only — the daemon clears the
    marker on next sweep."""
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = tmp_path / "foo "
    target.touch()
    assert format_literal_rule(target, rule_file) == r"/foo\ "


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows filesystems forbid tab in filenames",
)
def test_trailing_tab_in_segment_raises_value_error(tmp_path: Path) -> None:
    """A path component ending in a tab can't be encoded as a gitignore
    rule — pathspec strips the trailing tab regardless of backslash
    escape, so the rule would match the wrong path while the marker is
    set on the user-supplied path."""
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = MagicMock(spec=Path)
    target.relative_to.return_value = Path("foo\t")
    with pytest.raises(ValueError, match="non-space whitespace"):
        format_literal_rule(target, rule_file)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows symlink creation requires admin privileges",
)
def test_symlink_target_does_not_get_trailing_slash(tmp_path: Path) -> None:
    """A symlink to a directory must produce a rule WITHOUT trailing
    slash (treat the link as the link object, not its target).
    Otherwise round-trip ignore/unignore breaks for symlinks because
    format_literal_rule disagrees with how the rule was written/stored."""
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    # Real directory (target of the symlink).
    inner = tmp_path / "inner"
    inner.mkdir()
    # Symlink at the same level as rule file, pointing to inner/.
    link = tmp_path / "link_to_inner"
    link.symlink_to(inner)
    # canonical for the symlink should be `/link_to_inner` (no trailing slash).
    assert format_literal_rule(link, rule_file) == "/link_to_inner"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows filesystems forbid Unicode line separators in filenames",
)
def test_unicode_line_separator_in_segment_raises_value_error(tmp_path: Path) -> None:
    """U+2028 (LINE SEPARATOR) is a `str.splitlines()` separator, so a
    filename containing it would split into multiple rule lines on
    read-back. The rejection check must use `c.isspace() and c != ' '`
    to catch Unicode line separators beyond the ASCII set."""
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = MagicMock(spec=Path)
    target.relative_to.return_value = Path("foo bar")
    with pytest.raises(ValueError, match="non-space whitespace"):
        format_literal_rule(target, rule_file)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows symlink creation requires admin privileges",
)
def test_real_directory_still_gets_trailing_slash(tmp_path: Path) -> None:
    """Companion test: real directories (not symlinks) still get the trailing
    slash. The fix only suppresses the slash for symlinks."""
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    assert format_literal_rule(real_dir, rule_file) == "/real_dir/"

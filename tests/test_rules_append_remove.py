"""Unit tests for ``rules.append_rule`` and ``rules.remove_rule`` (item #93).

Both helpers use atomic temp-then-replace writes to avoid torn state under
SIGKILL or power loss. Tests verify idempotence, rstrip-equality semantics
(matching pathspec's gitignore-trailing-whitespace behavior), and the
file-creation-with-header path.
"""

from pathlib import Path

from dbxignore.rules import append_rule, remove_rule  # noqa: F401

# Header written when append_rule creates a new file.
HEADER = "# .dropboxignore — managed by dbxignore\n"


def test_append_creates_file_with_header_when_missing(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    appended = append_rule(rule_file, "build/")
    assert appended is True
    content = rule_file.read_text(encoding="utf-8")
    assert content == HEADER + "build/\n"


def test_append_to_existing_file_adds_line(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("node_modules/\n", encoding="utf-8")
    appended = append_rule(rule_file, "build/")
    assert appended is True
    assert rule_file.read_text(encoding="utf-8") == "node_modules/\nbuild/\n"


def test_append_idempotent_when_line_already_present(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("build/\n", encoding="utf-8")
    appended = append_rule(rule_file, "build/")
    assert appended is False
    assert rule_file.read_text(encoding="utf-8") == "build/\n"


def test_append_idempotent_with_trailing_whitespace_on_existing_line(tmp_path: Path) -> None:
    # Manually-typed rule with trailing whitespace — pathspec ignores trailing
    # whitespace when matching, so we treat it as equivalent to our canonical
    # form and do NOT add a redundant line.
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("build/   \n", encoding="utf-8")
    appended = append_rule(rule_file, "build/")
    assert appended is False
    assert rule_file.read_text(encoding="utf-8") == "build/   \n"


def test_append_handles_existing_file_without_trailing_newline(tmp_path: Path) -> None:
    # Rare but possible (manual edit, last line without final \n).
    # Our append must not produce ``last_line<newrule>``.
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("node_modules/", encoding="utf-8")  # no trailing \n
    appended = append_rule(rule_file, "build/")
    assert appended is True
    # Should be normalized so each rule is on its own line.
    assert rule_file.read_text(encoding="utf-8") == "node_modules/\nbuild/\n"

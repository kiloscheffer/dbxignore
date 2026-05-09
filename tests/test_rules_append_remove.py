"""Unit tests for ``rules.append_rule`` and ``rules.remove_rule`` (item #93).

Both helpers use atomic temp-then-replace writes to avoid torn state under
SIGKILL or power loss. Tests verify idempotence, rstrip-equality semantics
(matching pathspec's gitignore-trailing-whitespace behavior), and the
file-creation-with-header path.
"""

from pathlib import Path

from dbxignore.rules import append_rule, remove_rule

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


def test_remove_existing_line_returns_one(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("node_modules/\nbuild/\n.venv/\n", encoding="utf-8")
    removed = remove_rule(rule_file, "build/")
    assert removed == 1
    assert rule_file.read_text(encoding="utf-8") == "node_modules/\n.venv/\n"


def test_remove_with_trailing_whitespace_in_file(tmp_path: Path) -> None:
    # Manually-edited rule with trailing whitespace: rstrip-equality matches.
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("node_modules/\nbuild/   \n.venv/\n", encoding="utf-8")
    removed = remove_rule(rule_file, "build/")
    assert removed == 1
    assert rule_file.read_text(encoding="utf-8") == "node_modules/\n.venv/\n"


def test_remove_missing_line_returns_zero(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("node_modules/\n.venv/\n", encoding="utf-8")
    removed = remove_rule(rule_file, "build/")
    assert removed == 0
    assert rule_file.read_text(encoding="utf-8") == "node_modules/\n.venv/\n"


def test_remove_multiple_occurrences(tmp_path: Path) -> None:
    # Edge case from manual editing — same line written twice.
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("build/\nnode_modules/\nbuild/\n", encoding="utf-8")
    removed = remove_rule(rule_file, "build/")
    assert removed == 2
    assert rule_file.read_text(encoding="utf-8") == "node_modules/\n"


def test_remove_preserves_comments_and_blank_lines(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text(
        "# header\n\n# group: build artifacts\nbuild/\n# group: deps\nnode_modules/\n",
        encoding="utf-8",
    )
    removed = remove_rule(rule_file, "build/")
    assert removed == 1
    assert rule_file.read_text(encoding="utf-8") == (
        "# header\n\n# group: build artifacts\n# group: deps\nnode_modules/\n"
    )


def test_remove_when_file_does_not_exist_returns_zero(tmp_path: Path) -> None:
    # Defensive: caller should validate, but we shouldn't crash.
    rule_file = tmp_path / "nonexistent.dropboxignore"
    removed = remove_rule(rule_file, "build/")
    assert removed == 0
    assert not rule_file.exists()

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


def test_append_does_not_deduplicate(tmp_path: Path) -> None:
    """append_rule always appends — does NOT deduplicate against existing
    identical lines. The CLI gates calls via cache.match upstream, so a
    re-call here is intentional (e.g., to override a later negation that
    masked an earlier identical rule). gitignore's last-match-wins makes
    the duplicate effective."""
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("build/\n", encoding="utf-8")
    appended = append_rule(rule_file, "build/")
    assert appended is True
    # File now has TWO build/ lines.
    assert rule_file.read_text(encoding="utf-8") == "build/\nbuild/\n"


def test_append_appends_after_trailing_whitespace_existing_line(tmp_path: Path) -> None:
    """Trailing-whitespace tolerance no longer matters for append (we always
    append). The existing manually-typed `build/   ` stays untouched; the new
    canonical `build/` is appended verbatim."""
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("build/   \n", encoding="utf-8")
    appended = append_rule(rule_file, "build/")
    assert appended is True
    # Both lines present.
    assert rule_file.read_text(encoding="utf-8") == "build/   \nbuild/\n"


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


def test_append_to_existing_empty_file_starts_on_line_1(tmp_path: Path) -> None:
    """Regression: empty existing rule file (e.g., touched by user) was producing
    a leading blank line on first append. The empty-existing case must be
    treated like a missing file — write header + rule, no leading blank."""
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()  # empty file exists
    appended = append_rule(rule_file, "build/")
    assert appended is True
    assert rule_file.read_text(encoding="utf-8") == HEADER + "build/\n"


def test_append_does_not_clobber_preexisting_dropboxignore_tmp(tmp_path: Path) -> None:
    """A pre-existing ``.dropboxignore.tmp`` sibling — created by a concurrent
    CLI mutation in flight, an editor's atomic-save backup, or a stray user
    file — must survive the append. Previously ``append_rule`` wrote through
    the fixed name ``<rule_file>.tmp`` and would have clobbered it
    (item #101). The new ``mkstemp``-based shape picks a unique temp name."""
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("node_modules/\n", encoding="utf-8")
    sentinel = tmp_path / ".dropboxignore.tmp"
    sentinel.write_text("sentinel: in-flight concurrent write\n", encoding="utf-8")

    appended = append_rule(rule_file, "build/")

    assert appended is True
    assert rule_file.read_text(encoding="utf-8") == "node_modules/\nbuild/\n"
    assert sentinel.read_text(encoding="utf-8") == "sentinel: in-flight concurrent write\n", (
        "Pre-existing .dropboxignore.tmp was clobbered. The mkstemp-based "
        "temp-name picker must not collide with the fixed legacy name."
    )


def test_remove_does_not_clobber_preexisting_dropboxignore_tmp(tmp_path: Path) -> None:
    """Same collision concern as the append case, mirrored for ``remove_rule``."""
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("build/\nnode_modules/\n", encoding="utf-8")
    sentinel = tmp_path / ".dropboxignore.tmp"
    sentinel.write_text("sentinel: in-flight concurrent write\n", encoding="utf-8")

    removed = remove_rule(rule_file, "build/")

    assert removed == 1
    assert rule_file.read_text(encoding="utf-8") == "node_modules/\n"
    assert sentinel.read_text(encoding="utf-8") == "sentinel: in-flight concurrent write\n", (
        "Pre-existing .dropboxignore.tmp was clobbered by remove_rule."
    )


def test_append_does_not_leave_temp_files_behind(tmp_path: Path) -> None:
    """Happy path: after a successful append, no ``.dropboxignore.*.tmp``
    temp files should remain in the rule file's directory. Pins that the
    ``os.replace`` step moves the unique temp into place rather than leaving
    it as a sibling — would otherwise accumulate over many CLI invocations."""
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("build/\n", encoding="utf-8")

    append_rule(rule_file, "dist/")

    leftovers = list(tmp_path.glob(".dropboxignore.*.tmp"))
    assert leftovers == [], f"Leftover temp files after append: {leftovers}"

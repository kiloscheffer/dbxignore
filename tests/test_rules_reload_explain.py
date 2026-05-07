from pathlib import Path

import pytest

from dbxignore.rules import RuleCache
from tests.conftest import WriteFile


def test_reload_file_picks_up_new_pattern(tmp_path: Path, write_file: WriteFile) -> None:
    write_file(tmp_path / ".dropboxignore", "")
    (tmp_path / "build").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)
    assert cache.match(tmp_path / "build") is False

    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    cache.reload_file(tmp_path / ".dropboxignore")

    assert cache.match(tmp_path / "build") is True


def test_remove_file_drops_its_rules(tmp_path: Path, write_file: WriteFile) -> None:
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)
    assert cache.match(tmp_path / "build") is True

    cache.remove_file(tmp_path / ".dropboxignore")
    assert cache.match(tmp_path / "build") is False


def test_explain_returns_matching_rule(tmp_path: Path, write_file: WriteFile) -> None:
    write_file(tmp_path / ".dropboxignore", "# header\nbuild/\n*.log\n")
    (tmp_path / "build").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    matches = cache.explain(tmp_path / "build")
    assert len(matches) == 1
    assert matches[0].ignore_file == (tmp_path / ".dropboxignore").resolve()
    assert matches[0].pattern == "build/"
    assert matches[0].line == 2
    assert matches[0].negation is False


def test_explain_empty_for_non_matching_path(tmp_path: Path, write_file: WriteFile) -> None:
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "src").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.explain(tmp_path / "src") == []


def test_explain_line_numbers_with_interleaved_blank_and_comment_lines(
    tmp_path: Path, write_file: WriteFile
) -> None:
    """explain() must report the source line number from the file, not the
    pattern's index in pathspec's internal list. Regression guard for the
    one-pass pattern-entry build — a count-mismatch between active source
    lines and spec.patterns (e.g. indented '#' lines pathspec treats as
    patterns) must not shift the reported line number."""
    write_file(
        tmp_path / ".dropboxignore",
        "# header\n"  # line 1 — top-level comment
        "\n"  # line 2 — blank
        "build/\n"  # line 3 — target rule
        "   # indented\n"  # line 4 — pathspec treats this as an active pattern
        "*.log\n",  # line 5 — another target rule
    )
    (tmp_path / "build").mkdir()
    (tmp_path / "a.log").touch()

    cache = RuleCache()
    cache.load_root(tmp_path)

    build_matches = cache.explain(tmp_path / "build")
    assert len(build_matches) == 1
    assert build_matches[0].line == 3
    assert build_matches[0].pattern == "build/"

    log_matches = cache.explain(tmp_path / "a.log")
    assert len(log_matches) == 1
    assert log_matches[0].line == 5
    assert log_matches[0].pattern == "*.log"


def test_load_file_survives_malformed_pattern(
    tmp_path: Path, write_file: WriteFile, caplog: pytest.LogCaptureFixture
) -> None:
    """A .dropboxignore with a line pathspec can't compile must log a
    warning and leave the cache in a sane state, not raise."""
    import logging

    # '[z-a]' is a reverse-order character range; pathspec compiles it to a
    # regex that raises re.error at build time.
    write_file(tmp_path / ".dropboxignore", "[z-a]\n")

    cache = RuleCache()
    with caplog.at_level(logging.WARNING, logger="dbxignore.rules"):
        cache.load_root(tmp_path)

    # No rules loaded; match is defensively False.
    assert cache.match(tmp_path / "anything") is False
    assert any(
        r.levelname == "WARNING" and "Invalid .dropboxignore" in r.message for r in caplog.records
    )


def test_rulecache_populates_conflicts_on_load(tmp_path: Path) -> None:
    root = tmp_path
    (root / ".dropboxignore").write_text("build/\n!build/keep/\n", encoding="utf-8")
    cache = RuleCache()
    cache.load_root(root)

    conflicts = cache.conflicts()
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.dropped_pattern == "!build/keep/"
    assert c.masking_pattern == "build/"


def test_rulecache_no_conflict_for_children_only_pattern(tmp_path: Path) -> None:
    """`build/*` matches children of build/, not build/ itself. So
    `!build/keep/` is effective via pathspec last-match-wins — build/keep
    is in the include's match set, but the negation overrides for that
    specific path. This is the canonical git pattern for "exclude all of
    build/ except build/keep" and should NOT be flagged.
    """
    root = tmp_path
    (root / ".dropboxignore").write_text("build/*\n!build/keep/\n", encoding="utf-8")
    cache = RuleCache()
    cache.load_root(root)

    assert cache.conflicts() == []


def test_rulecache_no_conflict_three_rule_git_canonical(tmp_path: Path) -> None:
    """Three-rule git-canonical pattern: `build/*` + `!build/keep/` +
    `!build/keep/**`. All three should be effective. Rule 3's effect
    depends on rule 2 keeping build/keep unmarked; the detector must
    do last-match-wins on ancestors to see that.
    """
    root = tmp_path
    (root / ".dropboxignore").write_text(
        "build/*\n!build/keep/\n!build/keep/**\n", encoding="utf-8"
    )
    cache = RuleCache()
    cache.load_root(root)

    assert cache.conflicts() == []


def test_rulecache_still_flags_directory_rule_negation(tmp_path: Path) -> None:
    """Regression guard: `build/` + `!build/keep/` is the case where Dropbox
    inheritance makes the negation truly inert (build/ marks the dir; all
    descendants inherit). Must continue to flag as conflict.
    """
    root = tmp_path
    (root / ".dropboxignore").write_text("build/\n!build/keep/\n", encoding="utf-8")
    cache = RuleCache()
    cache.load_root(root)

    conflicts = cache.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].dropped_pattern == "!build/keep/"


def test_rulecache_flags_glob_prefix_negation_under_dir_marking_glob_include(
    tmp_path: Path,
) -> None:
    """Real-pathspec counterpart to the post-#76 detector behavior.

    Pre-#76: the conflict detector skipped any negation whose
    ``literal_prefix()`` returned None — including directory-targeting
    glob-prefix negations like ``!**/foo/bar/``. The diagnostic surface
    (``status``, ``explain``, the conflict WARNING) misled users by
    reporting no conflict even though Dropbox's ancestor inheritance
    made the negation inert wherever the `**` glob landed under the
    earlier ``**/foo/`` directory-marking include.

    Post-#76: ``_detect_conflicts`` adds a glob-prefix arm that flags
    such negations conservatively — any earlier include whose raw text
    ends in ``/`` triggers the drop. The detector still bypasses the
    literal-prefix ``is_directory_negation`` / strict-ancestor branch
    for glob-prefix patterns (``literal_prefix() is None`` early-exit
    at ``rules_conflicts.py:237-238`` is preserved); the new branch
    runs alongside it for the directory-targeting case.

    This test is the real-pathspec lock-down for the new behavior; the
    synthetic-shim counterpart lives at
    ``test_rules_conflicts.py::test_detect_glob_prefix_negation_under_directory_marking_glob_include``.
    """
    root = tmp_path
    (root / ".dropboxignore").write_text("**/foo/\n!**/foo/bar/\n", encoding="utf-8")
    cache = RuleCache()
    cache.load_root(root)

    conflicts = cache.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].dropped_pattern == "!**/foo/bar/"
    assert conflicts[0].masking_pattern == "**/foo/"


def test_rulecache_flags_descendant_negation_under_children_pattern(tmp_path: Path) -> None:
    """`build/*` + `!build/keep/foo.txt`: foo.txt is a strict descendant of
    build/keep, which gets marked by build/*. The file negation can't reach
    foo.txt due to Dropbox's inheritance. Conflict expected.
    """
    root = tmp_path
    (root / ".dropboxignore").write_text("build/*\n!build/keep/foo.txt\n", encoding="utf-8")
    cache = RuleCache()
    cache.load_root(root)

    conflicts = cache.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].dropped_pattern == "!build/keep/foo.txt"


def test_rulecache_flags_double_star_alone_under_children_pattern(tmp_path: Path) -> None:
    """`build/*` + `!build/keep/**` (without an earlier `!build/keep/` to
    save it): build/keep gets marked by build/*, so descendants can't be
    re-included. Conflict expected. Contrast with the three-rule version
    above where rule 2 keeps build/keep unmarked.
    """
    root = tmp_path
    (root / ".dropboxignore").write_text("build/*\n!build/keep/**\n", encoding="utf-8")
    cache = RuleCache()
    cache.load_root(root)

    conflicts = cache.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].dropped_pattern == "!build/keep/**"


def test_rulecache_no_cross_file_conflict_for_children_only_pattern(tmp_path: Path) -> None:
    """Cross-file analogue of the children-only pattern: parent
    `.dropboxignore` with `build/*`, child `.dropboxignore` inside
    `build/` with `!keep/`. Mirrors the within-file fix — no conflict.
    Pins the runtime cross-file path with the new strict-ancestor logic.
    """
    root = tmp_path
    (root / "build").mkdir()
    (root / ".dropboxignore").write_text("build/*\n", encoding="utf-8")
    (root / "build" / ".dropboxignore").write_text("!keep/\n", encoding="utf-8")

    cache = RuleCache()
    cache.load_root(root)

    assert cache.conflicts() == []


def test_rulecache_cross_file_conflict_for_directory_rule(tmp_path: Path) -> None:
    """Cross-file regression guard: parent `.dropboxignore` with `build/`,
    child `.dropboxignore` inside `build/` with `!keep/`. The directory-rule
    form still flags as a true cross-file conflict — Dropbox inheritance
    via the marked `build/` overrides the nested negation.
    """
    root = tmp_path
    (root / "build").mkdir()
    (root / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (root / "build" / ".dropboxignore").write_text("!keep/\n", encoding="utf-8")

    cache = RuleCache()
    cache.load_root(root)

    conflicts = cache.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].dropped_pattern == "!keep/"
    assert conflicts[0].masking_pattern == "build/"


def test_rulecache_sandwich_revives_conflict(tmp_path: Path) -> None:
    """Sandwich: include → negation → include → negation-target. The middle
    negation un-masks the ancestor, but a later include re-masks it, so
    the last-match-wins scan should report a conflict for the final
    negation. This pins the load-bearing semantic of the new
    `_find_masking_include` (last match per ancestor, not first include).
    """
    root = tmp_path
    (root / ".dropboxignore").write_text(
        "build/*\n!build/keep/\nbuild/\n!build/keep/foo.txt\n",
        encoding="utf-8",
    )
    cache = RuleCache()
    cache.load_root(root)

    # The last earlier rule matching `build/keep` is `build/` (line 3,
    # include); that re-marks the ancestor, so `!build/keep/foo.txt`
    # (line 4) is dropped.
    conflicts = cache.conflicts()
    dropped = {c.dropped_pattern for c in conflicts}
    assert "!build/keep/foo.txt" in dropped


def test_rulecache_clears_conflicts_on_reload_without_conflict(tmp_path: Path) -> None:
    root = tmp_path
    ignore_file = root / ".dropboxignore"
    ignore_file.write_text("build/\n!build/keep/\n", encoding="utf-8")
    cache = RuleCache()
    cache.load_root(root)
    assert len(cache.conflicts()) == 1

    # Fix the rules: drop the negation.
    ignore_file.write_text("build/\n", encoding="utf-8")
    cache.reload_file(ignore_file)

    assert cache.conflicts() == []


def test_rulecache_conflicts_removed_when_file_removed(tmp_path: Path) -> None:
    root = tmp_path
    ignore_file = root / ".dropboxignore"
    ignore_file.write_text("build/\n!build/keep/\n", encoding="utf-8")
    cache = RuleCache()
    cache.load_root(root)
    assert len(cache.conflicts()) == 1

    cache.remove_file(ignore_file)
    assert cache.conflicts() == []


def test_rulecache_conflicts_do_not_leak_across_roots(tmp_path: Path) -> None:
    """A conflict in root A must not appear in root B's conflicts list.
    The is_relative_to(root) filter in _build_sequence is what prevents
    this leakage; this test guards that filter."""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / ".dropboxignore").write_text("build/\n!build/keep/\n", encoding="utf-8")
    (root_b / ".dropboxignore").write_text("build/\n", encoding="utf-8")

    cache = RuleCache()
    cache.load_root(root_a)
    cache.load_root(root_b)

    conflicts = cache.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].dropped_source.is_relative_to(root_a)


def test_rulecache_detects_cross_file_conflict(tmp_path: Path) -> None:
    """Root .dropboxignore ignores build/; a nested .dropboxignore inside
    build/ tries to re-include keep/. The conflict spans two files —
    _build_sequence must order the root file before the nested one so
    the negation in the nested file sees `build/` as an earlier include."""
    root = tmp_path
    (root / "build").mkdir()
    (root / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (root / "build" / ".dropboxignore").write_text("!keep/\n", encoding="utf-8")

    cache = RuleCache()
    cache.load_root(root)

    conflicts = cache.conflicts()
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.dropped_source == (root / "build" / ".dropboxignore").resolve()
    assert c.masking_source == (root / ".dropboxignore").resolve()
    assert c.dropped_pattern == "!keep/"
    assert c.masking_pattern == "build/"


def test_match_treats_dropped_negation_as_absent(tmp_path: Path) -> None:
    """With `build/` + `!build/keep/`, the negation is dropped, so
    build/keep/ is matched via the include (gitignore semantics with the
    negation absent)."""
    root = tmp_path
    (root / ".dropboxignore").write_text("build/\n!build/keep/\n", encoding="utf-8")
    (root / "build").mkdir()
    (root / "build" / "keep").mkdir()
    cache = RuleCache()
    cache.load_root(root)

    assert cache.match(root / "build") is True
    # The negation is dropped — build/keep/ still matches the `build/` rule.
    assert cache.match(root / "build" / "keep") is True


def test_match_honors_non_conflicted_negation(tmp_path: Path) -> None:
    """*.log + !important.log: the negation is NOT dropped (no ignored
    ancestor), so important.log is excluded and others are included."""
    root = tmp_path
    (root / ".dropboxignore").write_text("*.log\n!important.log\n", encoding="utf-8")
    (root / "important.log").touch()
    (root / "debug.log").touch()
    cache = RuleCache()
    cache.load_root(root)

    assert cache.conflicts() == []  # guard: no conflict here
    assert cache.match(root / "important.log") is False
    assert cache.match(root / "debug.log") is True


def test_recompute_logs_warning_per_conflict(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    root = tmp_path
    (root / ".dropboxignore").write_text("build/\n!build/keep/\n", encoding="utf-8")
    cache = RuleCache()

    with caplog.at_level(logging.WARNING, logger="dbxignore.rules"):
        cache.load_root(root)

    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name == "dbxignore.rules" and "negation" in r.message
    ]
    assert len(warnings) == 1
    msg = warnings[0].message
    assert "!build/keep/" in msg
    assert "build/" in msg
    assert "Dropping the negation" in msg


def test_explain_includes_dropped_negation_with_flag(tmp_path: Path) -> None:
    root = tmp_path
    (root / ".dropboxignore").write_text("build/\n!build/keep/\n", encoding="utf-8")
    (root / "build").mkdir()
    (root / "build" / "keep").mkdir()
    cache = RuleCache()
    cache.load_root(root)

    results = cache.explain(root / "build" / "keep")
    by_pattern = {m.pattern.strip(): m for m in results}

    assert "build/" in by_pattern
    assert by_pattern["build/"].is_dropped is False

    assert "!build/keep/" in by_pattern
    assert by_pattern["!build/keep/"].is_dropped is True
    # Dropped matches still carry their source + line info so the CLI can
    # format "[dropped] ... (masked by ...)".
    assert by_pattern["!build/keep/"].line == 2


def test_explain_is_dropped_false_for_non_conflicted_negation(tmp_path: Path) -> None:
    """*.log + !important.log has no conflict — the negation should appear
    in explain() with is_dropped=False."""
    root = tmp_path
    (root / ".dropboxignore").write_text("*.log\n!important.log\n", encoding="utf-8")
    (root / "important.log").touch()
    cache = RuleCache()
    cache.load_root(root)

    assert cache.conflicts() == []  # guard: no conflict in this setup
    results = cache.explain(root / "important.log")
    by_pattern = {m.pattern.strip(): m for m in results}

    assert "!important.log" in by_pattern
    assert by_pattern["!important.log"].is_dropped is False
    assert by_pattern["!important.log"].negation is True

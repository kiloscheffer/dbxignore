from dropboxignore.rules import RuleCache


def test_reload_file_picks_up_new_pattern(tmp_path, write_file):
    write_file(tmp_path / ".dropboxignore", "")
    (tmp_path / "build").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)
    assert cache.match(tmp_path / "build") is False

    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    cache.reload_file(tmp_path / ".dropboxignore")

    assert cache.match(tmp_path / "build") is True


def test_remove_file_drops_its_rules(tmp_path, write_file):
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)
    assert cache.match(tmp_path / "build") is True

    cache.remove_file(tmp_path / ".dropboxignore")
    assert cache.match(tmp_path / "build") is False


def test_explain_returns_matching_rule(tmp_path, write_file):
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


def test_explain_empty_for_non_matching_path(tmp_path, write_file):
    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "src").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.explain(tmp_path / "src") == []


def test_explain_line_numbers_with_interleaved_blank_and_comment_lines(
    tmp_path, write_file
):
    """explain() must report the source line number from the file, not the
    pattern's index in pathspec's internal list. Regression guard for the
    one-pass pattern-entry build — a count-mismatch between active source
    lines and spec.patterns (e.g. indented '#' lines pathspec treats as
    patterns) must not shift the reported line number."""
    write_file(
        tmp_path / ".dropboxignore",
        "# header\n"            # line 1 — top-level comment
        "\n"                    # line 2 — blank
        "build/\n"              # line 3 — target rule
        "   # indented\n"       # line 4 — pathspec treats this as an active pattern
        "*.log\n"               # line 5 — another target rule
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


def test_load_file_survives_malformed_pattern(tmp_path, write_file, caplog):
    """A .dropboxignore with a line pathspec can't compile must log a
    warning and leave the cache in a sane state, not raise."""
    import logging

    # '[z-a]' is a reverse-order character range; pathspec compiles it to a
    # regex that raises re.error at build time.
    write_file(tmp_path / ".dropboxignore", "[z-a]\n")

    cache = RuleCache()
    with caplog.at_level(logging.WARNING, logger="dropboxignore.rules"):
        cache.load_root(tmp_path)

    # No rules loaded; match is defensively False.
    assert cache.match(tmp_path / "anything") is False
    assert any(
        r.levelname == "WARNING" and "Invalid .dropboxignore" in r.message
        for r in caplog.records
    )


def test_rulecache_populates_conflicts_on_load(tmp_path):
    from dropboxignore.rules import RuleCache

    root = tmp_path
    (root / ".dropboxignore").write_text(
        "build/\n!build/keep/\n", encoding="utf-8"
    )
    cache = RuleCache()
    cache.load_root(root)

    conflicts = cache.conflicts()
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.dropped_pattern == "!build/keep/"
    assert c.masking_pattern == "build/"


def test_rulecache_clears_conflicts_on_reload_without_conflict(tmp_path):
    from dropboxignore.rules import RuleCache

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


def test_rulecache_conflicts_removed_when_file_removed(tmp_path):
    from dropboxignore.rules import RuleCache

    root = tmp_path
    ignore_file = root / ".dropboxignore"
    ignore_file.write_text("build/\n!build/keep/\n", encoding="utf-8")
    cache = RuleCache()
    cache.load_root(root)
    assert len(cache.conflicts()) == 1

    cache.remove_file(ignore_file)
    assert cache.conflicts() == []


def test_rulecache_conflicts_do_not_leak_across_roots(tmp_path):
    """A conflict in root A must not appear in root B's conflicts list.
    The is_relative_to(root) filter in _build_sequence is what prevents
    this leakage; this test guards that filter."""
    from dropboxignore.rules import RuleCache

    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / ".dropboxignore").write_text(
        "build/\n!build/keep/\n", encoding="utf-8"
    )
    (root_b / ".dropboxignore").write_text("build/\n", encoding="utf-8")

    cache = RuleCache()
    cache.load_root(root_a)
    cache.load_root(root_b)

    conflicts = cache.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].dropped_source.is_relative_to(root_a)


def test_rulecache_detects_cross_file_conflict(tmp_path):
    """Root .dropboxignore ignores build/; a nested .dropboxignore inside
    build/ tries to re-include keep/. The conflict spans two files —
    _build_sequence must order the root file before the nested one so
    the negation in the nested file sees `build/` as an earlier include."""
    from dropboxignore.rules import RuleCache

    root = tmp_path
    (root / "build").mkdir()
    (root / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (root / "build" / ".dropboxignore").write_text(
        "!keep/\n", encoding="utf-8"
    )

    cache = RuleCache()
    cache.load_root(root)

    conflicts = cache.conflicts()
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.dropped_source == (root / "build" / ".dropboxignore").resolve()
    assert c.masking_source == (root / ".dropboxignore").resolve()
    assert c.dropped_pattern == "!keep/"
    assert c.masking_pattern == "build/"


def test_match_treats_dropped_negation_as_absent(tmp_path):
    """With `build/` + `!build/keep/`, the negation is dropped, so
    build/keep/ is matched via the include (gitignore semantics with the
    negation absent)."""
    from dropboxignore.rules import RuleCache

    root = tmp_path
    (root / ".dropboxignore").write_text(
        "build/\n!build/keep/\n", encoding="utf-8"
    )
    (root / "build").mkdir()
    (root / "build" / "keep").mkdir()
    cache = RuleCache()
    cache.load_root(root)

    assert cache.match(root / "build") is True
    # The negation is dropped — build/keep/ still matches the `build/` rule.
    assert cache.match(root / "build" / "keep") is True


def test_match_honors_non_conflicted_negation(tmp_path):
    """*.log + !important.log: the negation is NOT dropped (no ignored
    ancestor), so important.log is excluded and others are included."""
    from dropboxignore.rules import RuleCache

    root = tmp_path
    (root / ".dropboxignore").write_text(
        "*.log\n!important.log\n", encoding="utf-8"
    )
    (root / "important.log").touch()
    (root / "debug.log").touch()
    cache = RuleCache()
    cache.load_root(root)

    assert cache.conflicts() == []  # guard: no conflict here
    assert cache.match(root / "important.log") is False
    assert cache.match(root / "debug.log") is True


def test_recompute_logs_warning_per_conflict(tmp_path, caplog):
    import logging

    from dropboxignore.rules import RuleCache

    root = tmp_path
    (root / ".dropboxignore").write_text(
        "build/\n!build/keep/\n", encoding="utf-8"
    )
    cache = RuleCache()

    with caplog.at_level(logging.WARNING, logger="dropboxignore.rules"):
        cache.load_root(root)

    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and r.name == "dropboxignore.rules"
        and "negation" in r.message
    ]
    assert len(warnings) == 1
    msg = warnings[0].message
    assert "!build/keep/" in msg
    assert "build/" in msg
    assert "Dropping the negation" in msg

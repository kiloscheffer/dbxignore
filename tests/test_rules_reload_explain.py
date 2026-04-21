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

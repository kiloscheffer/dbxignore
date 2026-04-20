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

from dropboxignore.rules import RuleCache


def test_nested_dropboxignore_adds_rules(tmp_path, write_file):
    # Root-level ignores nothing; nested ignores 'build/'.
    write_file(tmp_path / ".dropboxignore", "")
    (tmp_path / "proj").mkdir()
    write_file(tmp_path / "proj" / ".dropboxignore", "build/\n")
    (tmp_path / "proj" / "build").mkdir()
    (tmp_path / "proj" / "src").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "proj" / "build") is True
    assert cache.match(tmp_path / "proj" / "src") is False


def test_child_can_negate_ancestor_match(tmp_path, write_file):
    write_file(tmp_path / ".dropboxignore", "*.log\n")
    (tmp_path / "proj").mkdir()
    write_file(tmp_path / "proj" / ".dropboxignore", "!important.log\n")
    (tmp_path / "proj" / "a.log").touch()
    (tmp_path / "proj" / "important.log").touch()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "proj" / "a.log") is True
    assert cache.match(tmp_path / "proj" / "important.log") is False


def test_ancestor_rule_applies_to_deep_descendant(tmp_path, write_file):
    write_file(tmp_path / ".dropboxignore", "**/node_modules/\n")
    (tmp_path / "a" / "b" / "c" / "node_modules").mkdir(parents=True)

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "a" / "b" / "c" / "node_modules") is True


def test_same_file_negation(tmp_path, write_file):
    # Single .dropboxignore with *.log ignored but !important.log as exception.
    write_file(tmp_path / ".dropboxignore", "*.log\n!important.log\n")
    (tmp_path / "a.log").touch()
    (tmp_path / "important.log").touch()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "a.log") is True
    assert cache.match(tmp_path / "important.log") is False


def test_three_level_reignore(tmp_path, write_file):
    # root ignores *.log; proj un-ignores important.log; proj/deep re-ignores it.
    write_file(tmp_path / ".dropboxignore", "*.log\n")
    (tmp_path / "proj").mkdir()
    write_file(tmp_path / "proj" / ".dropboxignore", "!important.log\n")
    (tmp_path / "proj" / "deep").mkdir()
    write_file(tmp_path / "proj" / "deep" / ".dropboxignore", "important.log\n")
    (tmp_path / "proj" / "deep" / "important.log").touch()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "proj" / "deep" / "important.log") is True

from click.testing import CliRunner

from dbxignore import cli


def test_apply_marks_matching_paths(tmp_path, fake_markers, monkeypatch):
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "src").mkdir()

    # Force roots.discover() to return tmp_path.
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "build").resolve() in fake_markers._ignored
    assert (tmp_path / "src").resolve() not in fake_markers._ignored
    assert "marked=1" in result.output or "1 marked" in result.output


def test_apply_with_path_argument_scopes_reconcile(tmp_path, fake_markers, monkeypatch):
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "build").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "build").mkdir()

    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", str(tmp_path / "a")])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "a" / "build").resolve() in fake_markers._ignored
    assert (tmp_path / "b" / "build").resolve() not in fake_markers._ignored


def test_apply_from_gitignore_mounts_at_dirname(tmp_path, fake_markers, monkeypatch):
    sub = tmp_path / "sub"
    sub.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (sub / "build").mkdir()
    (other / "build").mkdir()

    gitignore = sub / ".gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--from-gitignore", str(gitignore)])

    assert result.exit_code == 0, result.output
    assert (sub / "build").resolve() in fake_markers._ignored
    assert (other / "build").resolve() not in fake_markers._ignored


def test_apply_from_gitignore_ignores_existing_dropboxignore(
    tmp_path, fake_markers, monkeypatch
):
    (tmp_path / ".dropboxignore").write_text("other/\n", encoding="utf-8")
    (tmp_path / "other").mkdir()
    (tmp_path / "build").mkdir()

    gitignore = tmp_path / "my.gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--from-gitignore", str(gitignore)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "build").resolve() in fake_markers._ignored
    # 'other' rule from existing .dropboxignore is NOT applied
    assert (tmp_path / "other").resolve() not in fake_markers._ignored


def test_apply_from_gitignore_out_of_root_errors(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    gitignore = outside / ".gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")

    inside = tmp_path / "dropbox"
    inside.mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [inside])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--from-gitignore", str(gitignore)])

    assert result.exit_code == 2
    assert "not under any Dropbox root" in result.output


def test_apply_from_gitignore_mutex_with_positional_path(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["apply", str(tmp_path), "--from-gitignore", str(gitignore)],
    )

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_apply_from_gitignore_directory_arg_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--from-gitignore", str(tmp_path)])

    assert result.exit_code == 2
    assert "file path, not a directory" in result.output

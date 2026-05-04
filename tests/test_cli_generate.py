"""Tests for `dbxignore generate` — translates a .gitignore to a
.dropboxignore."""
from __future__ import annotations

from click.testing import CliRunner

from dbxignore import cli
from dbxignore import rules as rules_module


def test_generate_file_arg_writes_sibling(tmp_path):
    source = tmp_path / ".gitignore"
    source.write_text("build/\n*.log\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 0, result.output
    target = tmp_path / ".dropboxignore"
    assert target.read_text(encoding="utf-8") == "build/\n*.log\n"
    assert "wrote 2 rules" in result.output


def test_generate_directory_arg_finds_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("dist/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".dropboxignore").read_text(encoding="utf-8") == "dist/\n"


def test_generate_non_gitignore_filename_works(tmp_path):
    """File arg with a non-.gitignore name is accepted (e.g. .npmignore)."""
    source = tmp_path / ".npmignore"
    source.write_text("dist/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".dropboxignore").exists()


def test_generate_stdout_writes_no_file(tmp_path):
    source = tmp_path / ".gitignore"
    source.write_text("node_modules/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source), "--stdout"])

    assert result.exit_code == 0, result.output
    assert "node_modules/" in result.output
    assert not (tmp_path / ".dropboxignore").exists()


def test_generate_output_path_redirects(tmp_path):
    source = tmp_path / ".gitignore"
    source.write_text("target/\n", encoding="utf-8")
    custom = tmp_path / "custom" / ".dropboxignore"
    custom.parent.mkdir()

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source), "-o", str(custom)])

    assert result.exit_code == 0, result.output
    assert custom.read_text(encoding="utf-8") == "target/\n"
    assert not (tmp_path / ".dropboxignore").exists()


def test_generate_mutex_stdout_and_o_errors(tmp_path):
    source = tmp_path / ".gitignore"
    source.write_text("*.tmp\n", encoding="utf-8")
    bogus_out = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(
        cli.main, ["generate", str(source), "-o", str(bogus_out), "--stdout"]
    )

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_generate_collision_without_force_refuses(tmp_path):
    source = tmp_path / ".gitignore"
    source.write_text("new/\n", encoding="utf-8")
    target = tmp_path / ".dropboxignore"
    target.write_text("existing/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 2
    assert target.read_text(encoding="utf-8") == "existing/\n"  # unchanged
    assert "--force" in result.output


def test_generate_collision_with_force_overwrites(tmp_path):
    source = tmp_path / ".gitignore"
    source.write_text("new/\n", encoding="utf-8")
    target = tmp_path / ".dropboxignore"
    target.write_text("existing/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source), "--force"])

    assert result.exit_code == 0, result.output
    assert target.read_text(encoding="utf-8") == "new/\n"


def test_generate_invalid_pattern_writes_nothing(tmp_path, monkeypatch):
    """If the parser rejects the source, the target file is not created."""
    source = tmp_path / ".gitignore"
    source.write_text("anything\n", encoding="utf-8")

    def fail_build(_lines):
        raise ValueError("test-induced parse failure")

    monkeypatch.setattr(rules_module, "_build_spec", fail_build)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 2
    assert not (tmp_path / ".dropboxignore").exists()
    assert "invalid pattern" in result.output


def test_generate_missing_source_errors(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        cli.main, ["generate", str(tmp_path / "nonexistent.gitignore")]
    )

    assert result.exit_code == 2
    assert "not found" in result.output


def test_generate_target_outside_roots_warns_but_writes(tmp_path, monkeypatch):
    """Resolved target outside any Dropbox root → stderr warning, write proceeds."""
    inside = tmp_path / "dropbox"
    inside.mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [inside])

    outside = tmp_path / "outside"
    outside.mkdir()
    source = outside / ".gitignore"
    source.write_text("build/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 0, result.output
    assert (outside / ".dropboxignore").exists()
    assert "not under any discovered Dropbox root" in result.output

"""Tests for `dbxignore generate` — translates a .gitignore to a
.dropboxignore."""
from __future__ import annotations

from click.testing import CliRunner

from dbxignore import cli


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

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

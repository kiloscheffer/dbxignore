"""Tests for `dbxignore generate` — translates a .gitignore to a
.dropboxignore."""

from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner

from dbxignore import cli
from dbxignore import rules as rules_module

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_generate_file_arg_writes_sibling(tmp_path: Path) -> None:
    source = tmp_path / ".gitignore"
    source.write_text("build/\n*.log\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 0, result.output
    target = tmp_path / ".dropboxignore"
    assert target.read_text(encoding="utf-8") == "build/\n*.log\n"
    assert "wrote 2 rules" in result.output


def test_generate_directory_arg_finds_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("dist/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".dropboxignore").read_text(encoding="utf-8") == "dist/\n"


def test_generate_non_gitignore_filename_works(tmp_path: Path) -> None:
    """File arg with a non-.gitignore name is accepted (e.g. .npmignore)."""
    source = tmp_path / ".npmignore"
    source.write_text("dist/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".dropboxignore").exists()


def test_generate_stdout_writes_no_file(tmp_path: Path) -> None:
    source = tmp_path / ".gitignore"
    source.write_text("node_modules/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source), "--stdout"])

    assert result.exit_code == 0, result.output
    assert "node_modules/" in result.output
    assert not (tmp_path / ".dropboxignore").exists()


def test_generate_output_path_redirects(tmp_path: Path) -> None:
    source = tmp_path / ".gitignore"
    source.write_text("target/\n", encoding="utf-8")
    custom = tmp_path / "custom" / ".dropboxignore"
    custom.parent.mkdir()

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source), "-o", str(custom)])

    assert result.exit_code == 0, result.output
    assert custom.read_text(encoding="utf-8") == "target/\n"
    assert not (tmp_path / ".dropboxignore").exists()


def test_generate_mutex_stdout_and_o_errors(tmp_path: Path) -> None:
    source = tmp_path / ".gitignore"
    source.write_text("*.tmp\n", encoding="utf-8")
    bogus_out = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source), "-o", str(bogus_out), "--stdout"])

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_generate_collision_without_force_refuses(tmp_path: Path) -> None:
    source = tmp_path / ".gitignore"
    source.write_text("new/\n", encoding="utf-8")
    target = tmp_path / ".dropboxignore"
    target.write_text("existing/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 2
    assert target.read_text(encoding="utf-8") == "existing/\n"  # unchanged
    assert "--force" in result.output


def test_generate_collision_with_force_overwrites(tmp_path: Path) -> None:
    source = tmp_path / ".gitignore"
    source.write_text("new/\n", encoding="utf-8")
    target = tmp_path / ".dropboxignore"
    target.write_text("existing/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source), "--force"])

    assert result.exit_code == 0, result.output
    assert target.read_text(encoding="utf-8") == "new/\n"


def test_generate_invalid_pattern_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the parser rejects the source, the target file is not created."""
    source = tmp_path / ".gitignore"
    source.write_text("anything\n", encoding="utf-8")

    def fail_build(_lines: list[str]) -> object:
        raise ValueError("test-induced parse failure")

    monkeypatch.setattr(rules_module, "_build_spec", fail_build)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 2
    assert not (tmp_path / ".dropboxignore").exists()
    assert "invalid pattern" in result.output


def test_generate_missing_source_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(tmp_path / "nonexistent.gitignore")])

    assert result.exit_code == 2
    assert "not found" in result.output


def test_generate_target_outside_roots_warns_but_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_generate_warns_when_no_roots_discovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No Dropbox roots at all → out-of-root warning must still fire.

    Sibling of ``test_generate_target_outside_roots_warns_but_writes``: the
    earlier guard short-circuited the warning when ``_discover_roots()``
    returned ``[]``, which is precisely when the warning is most important
    ("your file won't be observed" is true both when target is outside
    discovered roots AND when no roots were found at all).
    """
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])

    source = tmp_path / ".gitignore"
    source.write_text("build/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".dropboxignore").exists()
    assert "not under any discovered Dropbox root" in result.output


# ---- generate-time conflict warning -----------------------------------------


def test_generate_no_conflicts_no_warning(tmp_path: Path) -> None:
    """Clean source: no conflict warning, no stderr noise."""
    source = tmp_path / ".gitignore"
    source.write_text("build/\nnode_modules/\n*.log\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 0, result.output
    assert "dropped negation" not in result.output
    assert "masked by" not in result.output


def test_generate_warns_on_dropped_negation(tmp_path: Path) -> None:
    """`build/` + `!build/keep/` is a true conflict (dir rule + descendant
    negation, Dropbox inheritance makes the negation inert). Warn at
    generate time so the user sees it before reconcile runs."""
    source = tmp_path / ".gitignore"
    source.write_text("build/\n!build/keep/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 0, result.output
    assert "!build/keep/" in result.output
    assert "build/" in result.output
    assert "masked by" in result.output


def test_generate_warning_does_not_alter_file(tmp_path: Path) -> None:
    """The byte-for-byte invariant survives the warning — the warning is
    informational, the file content is unchanged."""
    text = "build/\n!build/keep/\n"
    source = tmp_path / ".gitignore"
    source.write_text(text, encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 0, result.output
    target = tmp_path / ".dropboxignore"
    assert target.read_text(encoding="utf-8") == text


def test_generate_stdout_warning_to_stderr_only(tmp_path: Path) -> None:
    """--stdout mode: stdout carries the verbatim content; the warning
    goes to stderr so consumers piping the output downstream don't get
    a polluted file. Click 8.3+ keeps result.stdout / result.stderr
    separate by default."""
    source = tmp_path / ".gitignore"
    source.write_text("build/\n!build/keep/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source), "--stdout"])

    assert result.exit_code == 0, result.output
    assert result.stdout == "build/\n!build/keep/\n"
    assert "masked by" in result.stderr


def test_generate_no_warning_for_children_only_pattern(tmp_path: Path) -> None:
    """`build/*` + `!build/keep/` is the canonical git pattern for "exclude
    all of build/ except build/keep" and now (post detector fix) takes
    effect in dbxignore too — generate must not warn."""
    source = tmp_path / ".gitignore"
    source.write_text("build/*\n!build/keep/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 0, result.output
    assert "masked by" not in result.output
    assert "dropped negation" not in result.output

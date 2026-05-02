"""CLI surface tests for the ``dbxignore`` and ``dbxignored`` entry points."""
from __future__ import annotations

import re

from click.testing import CliRunner

from dbxignore import cli


def test_main_version_flag_emits_package_version():
    result = CliRunner().invoke(cli.main, ["--version"], prog_name="dbxignore")
    assert result.exit_code == 0, result.output
    assert re.match(r"^dbxignore, version \S+", result.output)


def test_daemon_main_version_flag_emits_package_version():
    result = CliRunner().invoke(cli.daemon_main, ["--version"], prog_name="dbxignored")
    assert result.exit_code == 0, result.output
    assert re.match(r"^dbxignored, version \S+", result.output)


def test_daemon_main_help_has_no_subcommand_token():
    result = CliRunner().invoke(cli.daemon_main, ["--help"], prog_name="dbxignored")
    assert result.exit_code == 0, result.output
    usage_line = result.output.splitlines()[0]
    assert usage_line == "Usage: dbxignored [OPTIONS]", usage_line


def test_daemon_main_verbose_flag_is_reachable(monkeypatch):
    called = []
    monkeypatch.setattr(cli, "_run_daemon", lambda: called.append(True))
    result = CliRunner().invoke(cli.daemon_main, ["--verbose"], prog_name="dbxignored")
    assert result.exit_code == 0, result.output
    assert called == [True]

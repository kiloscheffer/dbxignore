"""CLI surface tests for the ``dbxignore`` and ``dbxignored`` entry points."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from click.testing import CliRunner

from dbxignore import cli

if TYPE_CHECKING:
    import pytest


def test_main_version_flag_emits_package_version() -> None:
    result = CliRunner().invoke(cli.main, ["--version"], prog_name="dbxignore")
    assert result.exit_code == 0, result.output
    assert re.match(r"^dbxignore, version \S+", result.output)


def test_daemon_main_version_flag_emits_package_version() -> None:
    result = CliRunner().invoke(cli.daemon_main, ["--version"], prog_name="dbxignored")
    assert result.exit_code == 0, result.output
    assert re.match(r"^dbxignored, version \S+", result.output)


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def test_daemon_main_help_has_no_subcommand_token() -> None:
    result = CliRunner().invoke(cli.daemon_main, ["--help"], prog_name="dbxignored")
    assert result.exit_code == 0, result.output
    # Strip ANSI: rich-click colorizes captured stdout on POSIX CI runners
    # (TERM is set) but not on Windows runners — assertion must tolerate both.
    plain = _ANSI_ESCAPE_RE.sub("", result.output)
    usage_line = next(line for line in plain.splitlines() if "Usage:" in line)
    assert "dbxignored" in usage_line
    assert "[OPTIONS]" in usage_line
    assert "COMMAND" not in usage_line, usage_line
    assert "[ARGS]" not in usage_line, usage_line


def test_daemon_main_verbose_flag_is_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    called = []
    monkeypatch.setattr(cli, "_run_daemon", lambda: called.append(True))
    result = CliRunner().invoke(cli.daemon_main, ["--verbose"], prog_name="dbxignored")
    assert result.exit_code == 0, result.output
    assert called == [True]

"""CLI surface tests for the ``dbxignore`` and ``dbxignored`` entry points."""

from __future__ import annotations

import logging
import re

from click.testing import CliRunner

from dbxignore import cli


def test_main_version_flag_emits_package_version() -> None:
    result = CliRunner().invoke(cli.main, ["--version"], prog_name="dbxignore")
    assert result.exit_code == 0, result.output
    assert re.match(r"^dbxignore, version \S+", result.output)


def test_verbosity_to_level_default_is_warning() -> None:
    """Default (no `-v` flag) lands at WARNING so `logger.info` calls in
    install backends and other CLI-reachable modules stay off the user's
    terminal by default."""
    assert cli._verbosity_to_level(0) == logging.WARNING


def test_verbosity_to_level_one_v_is_info() -> None:
    assert cli._verbosity_to_level(1) == logging.INFO


def test_verbosity_to_level_two_v_is_debug() -> None:
    assert cli._verbosity_to_level(2) == logging.DEBUG


def test_verbosity_to_level_clamps_to_debug_for_higher_counts() -> None:
    """`-vvv` and beyond stay at DEBUG — no level deeper than DEBUG exists."""
    assert cli._verbosity_to_level(3) == logging.DEBUG
    assert cli._verbosity_to_level(10) == logging.DEBUG

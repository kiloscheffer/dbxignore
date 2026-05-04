"""Unit tests for RuleCache.load_external — the seam used by
``dbxignore apply --from-gitignore``. The seam loads an arbitrary file's
lines as if it were a .dropboxignore at a specified mount directory.
"""
from __future__ import annotations

import logging

from dbxignore.rules import IGNORE_FILENAME, RuleCache


def test_load_external_match_succeeds(tmp_path):
    """Rules from a non-.dropboxignore source still drive match() correctly."""
    source = tmp_path / "my.gitignore"
    source.write_text("build/\n", encoding="utf-8")
    mount_at = tmp_path
    (mount_at / "build").mkdir()

    cache = RuleCache()
    cache.load_external(source, mount_at)

    assert cache.match((mount_at / "build").resolve()) is True


def test_load_external_cache_key_is_mount_path(tmp_path):
    """The cache stores rules under <mount_at>/.dropboxignore, not the source path."""
    source = tmp_path / "elsewhere.gitignore"
    source.write_text("*.log\n", encoding="utf-8")

    mount_at = tmp_path / "project"
    mount_at.mkdir()

    cache = RuleCache()
    cache.load_external(source, mount_at)

    expected_key = (mount_at / IGNORE_FILENAME).resolve()
    assert expected_key in cache._rules
    assert source.resolve() not in cache._rules


def test_load_external_unreadable_source_logs_warning_no_raise(tmp_path, caplog):
    """Missing/unreadable source surfaces as a logged warning per
    _load_file's existing contract — load_external itself does not raise."""
    source = tmp_path / "does-not-exist"  # never created
    mount_at = tmp_path

    cache = RuleCache()
    with caplog.at_level(logging.WARNING, logger="dbxignore.rules"):
        cache.load_external(source, mount_at)  # must not raise

    assert "Could not read" in caplog.text

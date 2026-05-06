"""Unit tests for RuleCache.load_external — the seam used by
``dbxignore apply --from-gitignore``. The seam loads an arbitrary file's
lines as if it were a .dropboxignore at a specified mount directory.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from dbxignore.rules import RuleCache

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_load_external_match_succeeds(tmp_path: Path) -> None:
    """Rules from a non-.dropboxignore source still drive match() correctly."""
    source = tmp_path / "my.gitignore"
    source.write_text("build/\n", encoding="utf-8")
    mount_at = tmp_path
    (mount_at / "build").mkdir()

    cache = RuleCache()
    cache.load_external(source, mount_at)

    assert cache.match((mount_at / "build").resolve()) is True


def test_load_external_rules_apply_under_mount_not_source(tmp_path: Path) -> None:
    """Rules apply to paths under mount_at, not under source.parent —
    pins that load_external mounts the synthesized rules at mount_at
    rather than wherever the source happens to live."""
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    source = source_dir / "rules.gitignore"
    source.write_text("*.log\n", encoding="utf-8")

    mount_at = tmp_path / "project"
    mount_at.mkdir()

    cache = RuleCache()
    cache.load_external(source, mount_at)

    log_in_mount = mount_at / "test.log"
    log_in_mount.touch()
    assert cache.match(log_in_mount.resolve()) is True

    log_in_source_dir = source_dir / "test.log"
    log_in_source_dir.touch()
    assert cache.match(log_in_source_dir.resolve()) is False


def test_load_external_unreadable_source_logs_warning_no_raise(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Missing/unreadable source surfaces as a logged warning per
    _load_file's existing contract — load_external itself does not raise."""
    source = tmp_path / "does-not-exist"  # never created
    mount_at = tmp_path

    cache = RuleCache()
    with caplog.at_level(logging.WARNING, logger="dbxignore.rules"):
        cache.load_external(source, mount_at)  # must not raise

    assert "Could not read" in caplog.text

"""RuleCache.load_root skips reparsing .dropboxignore files whose content
(mtime + size) hasn't changed since the last load. The sweep is still the
safety net — rglob finds new files — but already-cached files stay put."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from dbxignore.rules import RuleCache

if TYPE_CHECKING:
    from tests.conftest import WriteFile


def _cached(cache: RuleCache, ignore_file_path: Path) -> object:
    return cache._rules[ignore_file_path.resolve()]


def test_load_root_skips_unchanged_file(tmp_path: Path, write_file: WriteFile) -> None:
    ignore = write_file(tmp_path / ".dropboxignore", "build/\n")

    cache = RuleCache()
    cache.load_root(tmp_path)
    first = _cached(cache, ignore)

    cache.load_root(tmp_path)
    second = _cached(cache, ignore)

    # Same _LoadedRules instance -> no reparse happened.
    assert first is second


def test_load_root_reloads_when_size_changes(tmp_path: Path, write_file: WriteFile) -> None:
    ignore = write_file(tmp_path / ".dropboxignore", "build/\n")

    cache = RuleCache()
    cache.load_root(tmp_path)
    first = _cached(cache, ignore)

    # Different content + different size.
    write_file(tmp_path / ".dropboxignore", "build/\ndist/\n")
    (tmp_path / "dist").mkdir()

    cache.load_root(tmp_path)
    second = _cached(cache, ignore)

    assert first is not second
    assert cache.match(tmp_path / "dist") is True


def test_load_root_reloads_when_mtime_changes_but_size_matches(
    tmp_path: Path, write_file: WriteFile
) -> None:
    """Same byte count, different content — size check alone wouldn't
    catch this. mtime_ns must be part of the stat tuple."""
    ignore = write_file(tmp_path / ".dropboxignore", "build/\n")  # 7 bytes

    cache = RuleCache()
    cache.load_root(tmp_path)
    first = _cached(cache, ignore)
    baseline_mtime_ns = ignore.stat().st_mtime_ns

    # Overwrite with same-length content; force a strictly-later mtime.
    ignore.write_text("cache/\n", encoding="utf-8")  # also 7 bytes
    new_mtime_ns = baseline_mtime_ns + 10_000_000  # +10ms
    os.utime(ignore, ns=(new_mtime_ns, new_mtime_ns))
    (tmp_path / "cache").mkdir()
    (tmp_path / "build").mkdir()

    cache.load_root(tmp_path)
    second = _cached(cache, ignore)

    assert first is not second
    assert cache.match(tmp_path / "cache") is True
    assert cache.match(tmp_path / "build") is False


def test_load_root_prunes_entries_for_deleted_files(tmp_path: Path, write_file: WriteFile) -> None:
    """If a .dropboxignore is deleted while the daemon is down (or the
    watchdog missed the delete), the next sweep's rglob won't find it.
    load_root must drop the stale cache entry so its rules don't keep
    silently applying."""
    ignore = write_file(tmp_path / ".dropboxignore", "build/\n")
    cache = RuleCache()
    cache.load_root(tmp_path)
    assert ignore.resolve() in cache._rules

    ignore.unlink()
    cache.load_root(tmp_path)

    assert ignore.resolve() not in cache._rules


def test_load_root_prune_leaves_other_roots_intact(tmp_path: Path, write_file: WriteFile) -> None:
    """Pruning under one root must not touch cached entries under others."""
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    ignore_a = write_file(root_a / ".dropboxignore", "build/\n")
    ignore_b = write_file(root_b / ".dropboxignore", "dist/\n")

    cache = RuleCache()
    cache.load_root(root_a)
    cache.load_root(root_b)

    ignore_a.unlink()
    cache.load_root(root_a)

    assert ignore_a.resolve() not in cache._rules
    assert ignore_b.resolve() in cache._rules


def test_load_root_picks_up_newly_created_file(tmp_path: Path, write_file: WriteFile) -> None:
    """Regression guard: the stat-check optimization must not break the
    rglob sweep's job of discovering files the cache doesn't know about."""
    write_file(tmp_path / ".dropboxignore", "build/\n")
    cache = RuleCache()
    cache.load_root(tmp_path)

    # A new .dropboxignore appears deeper in the tree (simulating a file
    # created during a watchdog-event-dropped window).
    (tmp_path / "proj").mkdir()
    new_ignore = write_file(tmp_path / "proj" / ".dropboxignore", "tmp/\n")

    cache.load_root(tmp_path)

    assert new_ignore.resolve() in cache._rules
    (tmp_path / "proj" / "tmp").mkdir()
    assert cache.match(tmp_path / "proj" / "tmp") is True


def test_load_root_drops_cached_entry_when_file_becomes_invalid(
    tmp_path: Path, write_file: WriteFile
) -> None:
    """A previously valid .dropboxignore that's later edited into an
    unparseable state must NOT keep its old rules active. Without this,
    the daemon would continue applying stale ignore markers to paths
    the user already changed their mind about, propagating cloud-sync
    deletions for paths the rules no longer cover."""
    ignore = write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)
    assert cache.match(tmp_path / "build") is True

    # Overwrite with valid bytes (so the read succeeds) but monkeypatch
    # `_build_spec` to raise — pathspec is too liberal to reliably reject
    # arbitrary text, so injecting the failure at the parser is the
    # robust way to exercise the parse-error arm. Bump mtime so
    # `_load_if_changed` notices and reparses.
    ignore.write_text("changed\n", encoding="utf-8")
    os.utime(ignore, (ignore.stat().st_atime, ignore.stat().st_mtime + 60))
    # Force-trip the bulk-parse path via a simulated parse error.
    from dbxignore import rules as rules_module

    with pytest.MonkeyPatch.context() as monkeypatch:

        def _raise(_lines: list[str]) -> object:
            raise ValueError("test-induced parse failure")

        monkeypatch.setattr(rules_module, "_build_spec", _raise)
        cache.load_root(tmp_path)

    assert cache.match(tmp_path / "build") is False, (
        "stale rules should not survive a parse failure on the cached file"
    )


def test_load_root_drops_cached_entry_when_file_becomes_unreadable(
    tmp_path: Path, write_file: WriteFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same shape as the parse-failure test but for the read-side OSError
    arm: a cached `.dropboxignore` whose later read fails must drop
    its entry, not keep applying stale rules."""
    import errno

    ignore = write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)
    assert cache.match(tmp_path / "build") is True

    # Bump the file's mtime so `_load_if_changed` reparses, then patch
    # `Path.read_text` to raise EIO on the rule file specifically.
    os.utime(ignore, (ignore.stat().st_atime, ignore.stat().st_mtime + 60))
    real_read_text = Path.read_text

    def _read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.resolve() == ignore.resolve():
            raise OSError(errno.EIO, "Input/output error")
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", _read_text)
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "build") is False, (
        "stale rules should not survive a read failure on the cached file"
    )

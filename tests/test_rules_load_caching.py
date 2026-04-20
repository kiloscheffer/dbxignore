"""RuleCache.load_root skips reparsing .dropboxignore files whose content
(mtime + size) hasn't changed since the last load. The sweep is still the
safety net — rglob finds new files — but already-cached files stay put."""

from __future__ import annotations

import os

from dropboxignore.rules import RuleCache


def _cached(cache: RuleCache, ignore_file_path):
    return cache._rules[ignore_file_path.resolve()]


def test_load_root_skips_unchanged_file(tmp_path, write_file):
    ignore = write_file(tmp_path / ".dropboxignore", "build/\n")

    cache = RuleCache()
    cache.load_root(tmp_path)
    first = _cached(cache, ignore)

    cache.load_root(tmp_path)
    second = _cached(cache, ignore)

    # Same _LoadedRules instance -> no reparse happened.
    assert first is second


def test_load_root_reloads_when_size_changes(tmp_path, write_file):
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
    tmp_path, write_file
):
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


def test_load_root_picks_up_newly_created_file(tmp_path, write_file):
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

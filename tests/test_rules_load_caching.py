"""RuleCache.load_root skips reparsing .dropboxignore files whose content
hash matches the cached digest. The sweep is still the safety net — rglob
finds new files — but already-cached files stay put.

A ``(mtime_ns, size)`` gate from stat would miss same-size edits with
preserved mtimes — a real edge case when editors or ``touch -r``
restore the timestamp — so the content hash is authoritative."""

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
    """Same byte count, different content, later mtime — the content-hash
    gate catches this. (A ``(mtime_ns, size)`` gate would also catch
    it because mtime differs; this test pins the common editor-save
    case.)"""
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


def test_load_root_drops_cache_on_non_utf8_overwrite(tmp_path: Path, write_file: WriteFile) -> None:
    """A .dropboxignore that turns into invalid UTF-8 (e.g. an editor
    saved as cp1252) is treated the same way as a pathspec parse error:
    the read succeeded but the content is broken, so the cached entry is
    dropped and the daemon treats the file as empty until the next valid
    edit. Keeping stale rules would let reconcile keep marking paths the
    user already changed their mind about.

    Using ``read_text("utf-8")`` directly would raise an uncaught
    ``UnicodeDecodeError`` and crash the sweep — strictly worse than
    either drop-cache or keep-cache."""
    ignore = write_file(tmp_path / ".dropboxignore", "build/\n")

    cache = RuleCache()
    cache.load_root(tmp_path)
    cache_key = ignore.resolve()
    assert cache_key in cache._rules

    # Overwrite with bytes that are not valid UTF-8.
    ignore.write_bytes(b"\xff\xfe\x00invalid\n")

    cache.load_root(tmp_path)

    assert cache_key not in cache._rules, (
        "Cached rules survived a non-UTF-8 overwrite; daemon would keep "
        "applying stale rules. Decode error should drop the entry, "
        "matching the parse-error arm's semantics."
    )


def test_load_root_reloads_when_size_and_mtime_match_but_content_differs(
    tmp_path: Path, write_file: WriteFile
) -> None:
    """Same byte count, content swap, AND mtime restored to its original
    value. An ``(mtime_ns, size)``-only gate would silently skip the reparse
    and leave stale rules active; the content-hash gate catches the swap.

    This is a real edge case — editors that preserve mtime on save, or
    explicit ``touch -r`` restoring a timestamp after edit. The daemon
    sweep is the recovery path for missed watchdog events, so a stale
    rule-cache entry that survives the sweep would survive indefinitely
    until the next size/mtime change."""
    ignore = write_file(tmp_path / ".dropboxignore", "build/\n")  # 7 bytes

    cache = RuleCache()
    cache.load_root(tmp_path)
    first = _cached(cache, ignore)
    baseline_mtime_ns = ignore.stat().st_mtime_ns

    # Overwrite with same-length content AND restore the original mtime.
    ignore.write_text("cache/\n", encoding="utf-8")  # also 7 bytes
    os.utime(ignore, ns=(baseline_mtime_ns, baseline_mtime_ns))
    (tmp_path / "cache").mkdir()
    (tmp_path / "build").mkdir()

    cache.load_root(tmp_path)
    second = _cached(cache, ignore)

    assert first is not second, (
        "Cache kept the stale entry despite a content swap with preserved "
        "mtime+size. The content-hash gate must catch this case; "
        "The (mtime_ns, size) gate alone would miss this case."
    )
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
    """The stat-check optimization must not break the rglob sweep's job
    of discovering files the cache doesn't know about."""
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
    """A valid .dropboxignore that's later edited into an unparseable
    state must NOT keep its old rules active. Without this, the daemon
    would continue applying stale ignore markers to paths the user
    already changed their mind about, propagating cloud-sync deletions
    for paths the rules no longer cover."""
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


def test_load_file_does_not_crash_on_resolve_failure(
    tmp_path: Path, write_file: WriteFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Path.resolve()` raises on symlink loops — `OSError(ELOOP)` on POSIX,
    `RuntimeError` on Windows / older POSIX. The cache-key resolve at the
    top of `_load_file` must catch both so a `.dropboxignore` that later
    turns into a symlink loop doesn't crash the sweep before the read /
    parse error arms can run.

    Symlink loops are awkward to create cross-platform; mock the resolve
    to raise instead. Same shape under the hood."""
    write_file(tmp_path / ".dropboxignore", "build/\n")
    real_resolve = Path.resolve

    def _raising_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        if self.name == ".dropboxignore":
            raise RuntimeError("Symlink loop")
        return real_resolve(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "resolve", _raising_resolve)

    cache = RuleCache()
    # Must not raise. `load_root` calls `.resolve()` directly too, so we
    # exercise `_load_file` via `_load_if_changed` from a separate seam.
    cache._load_file(tmp_path / ".dropboxignore")


def test_load_root_preserves_cached_entry_on_transient_read_error(
    tmp_path: Path, write_file: WriteFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient read error (editor lock, antivirus scan, brief EIO on a
    network drive) must NOT drop the cached entry. Dropping would treat
    every flap as confirmed corruption, the next reconcile would see the
    rule file as empty, and Dropbox would upload already-ignored paths
    to cloud before the read recovered. Recovery happens naturally on
    the next sweep when the read succeeds again — convergent design.
    A drop-on-OSError shape would be worse than the staleness it would
    try to fix."""
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

    assert cache.match(tmp_path / "build") is True, (
        "transient read error should not drop the last-known-good rule cache"
    )

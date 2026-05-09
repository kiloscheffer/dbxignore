from pathlib import Path

import pytest

from dbxignore.rules import RuleCache, is_ignore_filename
from tests.conftest import FakeMarkers, WriteFile


def test_match_rejects_relative_path(tmp_path: Path, write_file: WriteFile) -> None:
    """Caller contract: match()/explain() require absolute paths. The internal
    resolve() used to mask relative-path bugs by silently normalizing; now
    they raise loudly so the bug surfaces at the call site instead."""
    write_file(tmp_path / ".dropboxignore", "build/\n")
    cache = RuleCache()
    cache.load_root(tmp_path)

    with pytest.raises(ValueError, match="absolute"):
        cache.match(Path("build"))
    with pytest.raises(ValueError, match="absolute"):
        cache.explain(Path("build"))


def test_flat_match_sets_true_for_matching_directory(tmp_path: Path, write_file: WriteFile) -> None:
    write_file(tmp_path / ".dropboxignore", "node_modules/\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "src").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "node_modules") is True
    assert cache.match(tmp_path / "src") is False


def test_empty_dropboxignore_matches_nothing(tmp_path: Path, write_file: WriteFile) -> None:
    write_file(tmp_path / ".dropboxignore", "")
    (tmp_path / "foo").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "foo") is False


def test_comment_and_blank_lines_ignored(tmp_path: Path, write_file: WriteFile) -> None:
    write_file(tmp_path / ".dropboxignore", "# comment\n\nbuild/\n")
    (tmp_path / "build").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "build") is True


def test_no_dropboxignore_files_matches_nothing(tmp_path: Path) -> None:
    (tmp_path / "anything").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "anything") is False


def test_indented_hash_line_is_active_pattern(tmp_path: Path) -> None:
    """Lines like `   #literal` are active patterns per gitignore semantics, not comments.

    Pins the comment-filter fix in `_build_entries`: the filter checks
    `raw.startswith("#")` (not `raw.strip().startswith("#")`) so leading
    whitespace before `#` keeps the line in the active-pattern set.
    """
    rules_path = tmp_path / ".dropboxignore"
    rules_path.write_text("   #literal\n", encoding="utf-8")
    cache = RuleCache()
    cache.load_root(tmp_path)

    loaded = cache._rules[rules_path]
    assert len(loaded.entries) == 1
    assert loaded.entries[0][0] == 0


def test_load_root_honors_stop_event_between_directory_visits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`RuleCache.load_root` must check stop_event between directory visits
    so SIGTERM during phase 1 of `_sweep_once` is observed without
    scanning every `.dropboxignore` in a large tree. Surfaced by Codex P2
    #6 on PR #162; reframed in PR #184 around per-directory granularity
    (item #86) when the rglob loop was replaced by `os.walk`."""
    import os
    import threading

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    file_a = dir_a / ".dropboxignore"
    file_b = dir_b / ".dropboxignore"
    dir_a.mkdir()
    dir_b.mkdir()
    file_a.write_text("a/\n", encoding="utf-8")
    file_b.write_text("b/\n", encoding="utf-8")

    stop = threading.Event()
    real_walk = os.walk

    def fake_walk(top, **kwargs):  # type: ignore[no-untyped-def]
        # Yield root + dir_a, then set stop_event before yielding dir_b.
        # load_root observes the set event at the top of its next iteration
        # and returns early; dir_b's `.dropboxignore` is never processed.
        for yielded, entry in enumerate(real_walk(top, **kwargs), start=1):
            yield entry
            if yielded == 2:  # root + first child consumed
                stop.set()

    monkeypatch.setattr(os, "walk", fake_walk)

    cache = RuleCache()
    cache.load_root(tmp_path, stop_event=stop)

    # Exactly one of the two rule files should be loaded — the one in the
    # directory visited before stop fired. Which one depends on os.walk's
    # iteration order (alphabetical on most platforms; not guaranteed by
    # contract). Assert the disjunction so the test stays portable.
    loaded_a = file_a.resolve() in cache._rules
    loaded_b = file_b.resolve() in cache._rules
    assert loaded_a != loaded_b, (
        f"expected exactly one of file_a, file_b loaded; got loaded_a={loaded_a}, "
        f"loaded_b={loaded_b}"
    )


def test_load_root_preserves_cache_when_pre_stat_would_flap(
    tmp_path: Path, write_file: WriteFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item #86 / Codex P2 catch on PR #184: load_root must NOT pre-stat
    rule files in a way that lets a transient stat failure silently skip
    the file. If it did, the file would be missing from `seen` and the
    stale-purge would drop the cached entry — letting Dropbox upload
    previously-ignored paths before the next sweep recovers. The fix is
    to use os.walk's already-materialized filenames list rather than a
    separate `Path.is_file()` call.

    Pin contract: monkeypatching `Path.is_file` to always return False
    must NOT prevent load_root from finding the rule file or preserving
    its cached entry. (If a future refactor reintroduces a pre-stat
    gate via is_file, this test fails.)"""
    rule_file = tmp_path / "sub" / ".dropboxignore"
    write_file(rule_file, "build/\n")

    # Phase 1: warm the cache.
    cache = RuleCache()
    cache.load_root(tmp_path)
    cache_key = rule_file.resolve()
    assert cache_key in cache._rules, "cache should be warm after first load"

    # Phase 2: monkeypatch Path.is_file to ALWAYS return False, simulating
    # a transient stat flap that affects every subsequent stat call. If
    # load_root pre-stats via is_file, this would cause the file to be
    # silently skipped → stale-purge drops the cache entry. The fix
    # avoids the pre-stat entirely; the file is detected via os.walk's
    # filenames list (which derives from a single scandir, not per-file
    # stat).
    monkeypatch.setattr(Path, "is_file", lambda self: False)

    cache.load_root(tmp_path)

    assert cache_key in cache._rules, (
        "is_file()-flap must NOT drop the cached entry — "
        "load_root must use os.walk's filenames list, not a pre-stat gate"
    )


def test_load_root_finds_and_applies_mixed_case_dropboxignore(
    tmp_path: Path, write_file: WriteFile
) -> None:
    """Item #86 / Codex P2 catches on PR #184: load_root must find rule
    files whose on-disk filename has mixed casing (e.g. `.DropboxIgnore`
    vs `.dropboxignore`) AND the rules must actually apply via
    `cache.match()`. The prior `rglob` shape found mixed-case files on
    Windows NTFS and default macOS APFS/HFS+ because glob matching is
    case-insensitive there. The os.walk swap must preserve that behavior
    AND ensure the cache key is canonical (lowercase) so `match()`'s
    lookup hits — without normalization, `PosixPath` equality is
    case-sensitive on Linux/macOS and the cached entry is unreachable.

    Portable: on case-sensitive Linux, this creates a file named exactly
    `.DropboxIgnore` (a different file from `.dropboxignore` — but the
    fix loads it anyway, consistent with the project's
    case-insensitive-everywhere pattern-matching posture)."""
    sub = tmp_path / "sub"
    sub.mkdir()
    rule_file = sub / ".DropboxIgnore"  # mixed case
    rule_file.write_text("build/\n", encoding="utf-8")
    target = sub / "build"
    target.mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    # Cache key must end in `.dropboxignore` (lowercase) regardless of
    # on-disk casing — otherwise PosixPath case-sensitive lookup in
    # match() / _applicable would silently fail.
    canonical_key = (sub / ".dropboxignore").resolve()
    assert canonical_key in cache._rules, (
        f"expected canonical lowercase cache key {canonical_key}, got {list(cache._rules.keys())}"
    )

    # End-to-end: rules from the mixed-case file must apply via match().
    # The pre-fix shape (cache key with on-disk casing) silently failed
    # this assertion on Linux/macOS — file was loaded but match() lookup
    # via `ancestor / IGNORE_FILENAME` (lowercase) missed.
    assert cache.match(target.resolve()), (
        "rules from the mixed-case file must apply to a matching path"
    )


def test_load_root_prefers_exact_dropboxignore_over_mixed_case(
    tmp_path: Path, write_file: WriteFile, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item #86 / fourth Codex P2 catch on PR #184: when a directory contains
    both `.dropboxignore` (canonical) and `.DropboxIgnore` (mixed case), the
    canonical lowercase file MUST be selected. `os.walk`'s filename order is
    not guaranteed, so without an exact-match preference the selection is
    order-dependent and the canonical file's rules can be silently shadowed.

    Mocks `os.walk` to inject a mixed-case filename ahead of the real
    `.dropboxignore` in the filenames list. Only `.dropboxignore` exists on
    disk (creating both is impossible on case-insensitive filesystems
    anyway). The fix's exact-match preference ensures the read targets
    the file that actually exists; without it, on case-sensitive Linux
    the read would attempt `.DropboxIgnore` (nonexistent), fail, and
    leave the cache empty for that directory."""
    import os

    sub = tmp_path / "sub"
    sub.mkdir()
    write_file(sub / ".dropboxignore", "lower_marker/\n")

    real_walk = os.walk

    def fake_walk(top, **kwargs):  # type: ignore[no-untyped-def]
        for current, dirs, files in real_walk(top, **kwargs):
            if current == str(sub):
                # Force the mixed-case name to appear first.
                files = [".DropboxIgnore", *files]
            yield (current, dirs, files)

    monkeypatch.setattr(os, "walk", fake_walk)

    cache = RuleCache()
    cache.load_root(tmp_path)

    canonical = (sub / ".dropboxignore").resolve()
    assert canonical in cache._rules, (
        "exact-match `.dropboxignore` must be selected even when a mixed-case "
        "name appears earlier in os.walk's filenames"
    )
    cached = cache._rules[canonical]
    assert "lower_marker/" in cached.lines, (
        f"expected the canonical .dropboxignore's content to be cached; got {cached.lines}"
    )


def test_load_root_force_reloads_when_fallback_to_mixed_case(
    tmp_path: Path, write_file: WriteFile
) -> None:
    """Item #86 / Codex P3 catch on PR #184: when load_root falls back to
    a mixed-case filename (because the canonical `.dropboxignore` is
    absent from the filenames list), reload unconditionally — don't trust
    the cached entry's mtime/size shortcut. The cached entry under the
    canonical key may have been populated from a *different* source file
    earlier in the cache's life (e.g., the canonical file existed then,
    was deleted, and only the mixed-case file remains). Without forcing
    a reload, an unlucky stat-value coincidence between the two files
    would let stale rules from the deleted canonical file persist.

    Test exercises the deterministic case (different content) by setting
    the new file's mtime to match the prior cache's mtime exactly via
    `os.utime` — without `force`, the mtime+size shortcut would fire and
    the test would fail."""
    import os

    sub = tmp_path / "sub"
    sub.mkdir()
    canonical = sub / ".dropboxignore"
    canonical.write_text("first_rule/\n", encoding="utf-8")

    # Phase 1: warm the cache from the canonical file.
    cache = RuleCache()
    cache.load_root(tmp_path)
    cache_key = canonical.resolve()
    assert cache_key in cache._rules
    assert "first_rule/" in cache._rules[cache_key].lines
    cached_mtime = cache._rules[cache_key].mtime_ns
    cached_size = cache._rules[cache_key].size

    # Phase 2: delete the canonical file, replace with a mixed-case file
    # whose content differs but whose size happens to be identical (and
    # whose mtime we force-set to match the cached value).
    canonical.unlink()
    mixed = sub / ".DropboxIgnore"
    # Same byte length as "first_rule/\n" but different content.
    mixed.write_text("other_rule/\n", encoding="utf-8")
    assert mixed.stat().st_size == cached_size, (
        "test setup error: replacement file size must match cached size"
    )
    # Force-set mtime to match the prior cache exactly. Without the
    # `as_path.name != ignore_file.name` skip-shortcut check inside
    # `_load_if_changed`, the mtime+size match would skip reloading and
    # the test would see stale "first_rule/" content.
    os.utime(mixed, ns=(cached_mtime, cached_mtime))

    # Phase 3: re-sweep. The fallback selects `.DropboxIgnore` (canonical
    # is gone). The reload must fire even though the stat values match
    # the cached entry's.
    cache.load_root(tmp_path)

    cached_after = cache._rules[cache_key]
    assert "other_rule/" in cached_after.lines, (
        f"expected stale-rules invalidation; got {cached_after.lines}"
    )
    assert "first_rule/" not in cached_after.lines, (
        "stale rules from the deleted canonical file must NOT persist"
    )


def test_load_root_observes_stop_event_in_dropboxignore_free_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item #86: per-directory cancellation is the load-bearing improvement
    over the prior rglob-based check. Pin the case the new shape exists to
    fix — a tree with NO `.dropboxignore` files but multiple directories
    must still observe `stop_event` mid-traversal. The prior `rglob`-based
    check would have visited every directory before returning (zero yields
    for a no-rules tree), blocking SIGTERM observation."""
    import os
    import threading

    # Six directories at root, no .dropboxignore anywhere.
    for i in range(6):
        (tmp_path / f"dir_{i}").mkdir()

    stop = threading.Event()
    visited: list[str] = []
    real_walk = os.walk

    def counting_walk(top, **kwargs):  # type: ignore[no-untyped-def]
        for entry in real_walk(top, **kwargs):
            visited.append(entry[0])
            if len(visited) >= 2:
                stop.set()
            yield entry

    monkeypatch.setattr(os, "walk", counting_walk)

    cache = RuleCache()
    cache.load_root(tmp_path, stop_event=stop)

    # load_root must have returned without consuming all 7 dirs (root + 6).
    # The exact count depends on the for-loop's stop check timing relative
    # to the generator's yield: with stop set after visit 2, load_root sees
    # the set flag at the top of iteration 3 and returns. So `visited`
    # should be at most 3 (one over the trigger to account for the
    # post-yield set position).
    assert len(visited) <= 3, (
        f"expected early cancellation (≤3 visits), got {len(visited)}: {visited}"
    )


def test_is_ignore_filename_predicate() -> None:
    """``is_ignore_filename`` is the canonical case-insensitive predicate
    for `.dropboxignore` filenames. Used by match/explain/_classify/
    _moved_dest_under_root/_reconcile_path/_walk_marked_paths so the
    project's "case-insensitive everywhere" posture holds end-to-end
    (item #92)."""
    assert is_ignore_filename(".dropboxignore")
    assert is_ignore_filename(".DropboxIgnore")
    assert is_ignore_filename(".DROPBOXIGNORE")
    assert is_ignore_filename(".DroPboXigNoRe")
    assert not is_ignore_filename("dropboxignore")  # missing leading dot
    assert not is_ignore_filename(".dropboxignor")  # truncated
    assert not is_ignore_filename(".gitignore")
    assert not is_ignore_filename("")


def test_match_returns_false_for_mixed_case_dropboxignore_path(
    tmp_path: Path, write_file: WriteFile
) -> None:
    """match() must short-circuit to False for any case-equivalent
    `.dropboxignore` path. Without case-insensitive recognition, a
    mixed-case rule file could match an `.dropboxignore`-style rule
    against itself and end up marked, violating the project invariant
    that rule files are never ignored (item #92)."""
    write_file(tmp_path / ".dropboxignore", ".*ignore\n")  # would match self literally

    cache = RuleCache()
    cache.load_root(tmp_path)

    # The canonical rule file is never matched against its own rules.
    assert not cache.match((tmp_path / ".dropboxignore").resolve())
    # A mixed-case rule file is also recognized and short-circuits.
    # (Construct the path directly; we don't need it to exist on disk
    # because match() short-circuits before any FS access.)
    assert not cache.match((tmp_path / ".DropboxIgnore").resolve())
    assert not cache.match((tmp_path / ".DROPBOXIGNORE").resolve())


def test_explain_returns_empty_for_mixed_case_dropboxignore_path(
    tmp_path: Path, write_file: WriteFile
) -> None:
    """explain() mirrors match()'s short-circuit on rule-file paths
    case-insensitively (item #92)."""
    write_file(tmp_path / ".dropboxignore", "*.log\n")

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.explain((tmp_path / ".dropboxignore").resolve()) == []
    assert cache.explain((tmp_path / ".DropboxIgnore").resolve()) == []


def test_reload_file_with_mixed_case_path_updates_canonical_entry(
    tmp_path: Path, write_file: WriteFile
) -> None:
    """A watchdog reload event delivered with mixed-case path must update
    the canonical-key cache entry (not create a separate mixed-case-keyed
    entry that match() would never read). Codex-flagged latent bug from
    PR #184; fixed in item #92's comprehensive case-insensitive predicate.

    Pre-#92, reload_file did `self._rules.pop(ignore_file.resolve(), None)`
    which on POSIX uses the on-disk casing. Mixed-case path → mixed-case
    cache key → match()'s lookup of canonical key misses the fresh entry
    and continues reading the stale canonical-key entry.

    The reload-triggering edit goes to the canonical file (which is what
    actually drives the cache content per `load_root`'s prefer-exact-match
    precedence). The watchdog event happens to deliver the mixed-case
    path; the precedence guard in reload_file redirects the read to the
    canonical sibling so the cache entry reflects the canonical file's
    rules."""
    rule_file = tmp_path / ".dropboxignore"
    write_file(rule_file, "first/\n")

    cache = RuleCache()
    cache.load_root(tmp_path)
    canonical_key = rule_file.resolve()
    assert canonical_key in cache._rules
    assert "first/" in cache._rules[canonical_key].lines

    # User edits the (canonical) rule file. Watchdog could fire for either
    # casing depending on FS quirks; passing the mixed-case path here
    # exercises the case-insensitive cache-key normalization.
    rule_file.write_text("second/\n", encoding="utf-8")

    cache.reload_file(tmp_path / ".DropboxIgnore")  # <- mixed-case path

    # Cache must have ONE entry (canonical key) with the updated content.
    matching_keys = [k for k in cache._rules if k.name.lower() == ".dropboxignore"]
    assert len(matching_keys) == 1, f"expected exactly one cache entry; got {matching_keys}"
    assert canonical_key in cache._rules
    assert "second/" in cache._rules[canonical_key].lines, (
        "reload_file with mixed-case path must update the canonical-key entry"
    )


def test_reload_file_preserves_canonical_precedence_when_both_files_exist(
    tmp_path: Path, write_file: WriteFile, require_case_sensitive_fs: None
) -> None:
    """When both `.dropboxignore` (canonical) and `.DropboxIgnore` (mixed
    case) exist on disk (only possible on case-sensitive Linux/macOS-APFS
    -strict), a watchdog event for the mixed-case sibling must NOT
    overwrite the cache entry with the mixed-case file's content.
    `load_root`'s prefer-exact-match selection means the canonical file
    drives the active cache; an edit to the shadowed sibling is a no-op
    from the cache's perspective. Codex P2 catch on PR #185."""
    sub = tmp_path / "sub"
    sub.mkdir()
    canonical = sub / ".dropboxignore"
    mixed = sub / ".DropboxIgnore"
    canonical.write_text("canonical_rule/\n", encoding="utf-8")

    cache = RuleCache()
    cache.load_root(tmp_path)
    cached_lines_before = list(cache._rules[canonical.resolve()].lines)

    mixed.write_text("mixed_rule/\n", encoding="utf-8")

    # Watchdog fires for the mixed-case sibling. The reload must redirect
    # to the canonical file (which is unchanged) and keep its rules in
    # the cache; the mixed-case file's content must NOT replace them.
    cache.reload_file(mixed)

    cached_lines_after = list(cache._rules[canonical.resolve()].lines)
    assert "canonical_rule/" in cached_lines_after, (
        "canonical file's rules must remain in the cache"
    )
    assert "mixed_rule/" not in cached_lines_after, (
        "mixed-case sibling's rules must NOT shadow the canonical file"
    )
    assert cached_lines_after == cached_lines_before, (
        "cache content should be unchanged when reload redirects to "
        f"unchanged canonical sibling; got {cached_lines_after}"
    )


def test_remove_file_with_mixed_case_path_removes_canonical_entry(
    tmp_path: Path, write_file: WriteFile
) -> None:
    """A watchdog deletion event delivered with mixed-case path must
    remove the canonical-key cache entry when the rule file is genuinely
    gone (no canonical sibling on disk). Without canonical-key
    normalization the pop misses on POSIX and the cache stays stale
    (item #92)."""
    # Only `.DropboxIgnore` ever existed (no canonical sibling).
    rule_file = tmp_path / ".DropboxIgnore"
    rule_file.write_text("deleted/\n", encoding="utf-8")

    cache = RuleCache()
    cache.load_root(tmp_path)
    canonical_key = (tmp_path / ".dropboxignore").resolve()
    assert canonical_key in cache._rules

    # File is genuinely deleted before the watchdog event fires.
    rule_file.unlink()
    cache.remove_file(rule_file)  # mixed-case path

    assert canonical_key not in cache._rules, (
        "remove_file with mixed-case path must drop the canonical-key entry"
    )


def test_remove_file_preserves_cache_when_canonical_sibling_exists(
    tmp_path: Path, write_file: WriteFile, require_case_sensitive_fs: None
) -> None:
    """A deletion event for `.DropboxIgnore` while a canonical
    `.dropboxignore` sibling still exists must NOT drop the cache
    entry — the canonical file's rules are still authoritative.
    Mirrors `load_root`'s prefer-exact-match precedence on the
    deletion side (Codex P2 catch on PR #185).

    Only meaningful on case-sensitive Linux/macOS-APFS-strict where
    both files are distinct entries (the fixture skips otherwise)."""
    sub = tmp_path / "sub"
    sub.mkdir()
    canonical = sub / ".dropboxignore"
    mixed = sub / ".DropboxIgnore"
    canonical.write_text("canonical/\n", encoding="utf-8")

    mixed.write_text("mixed/\n", encoding="utf-8")

    cache = RuleCache()
    cache.load_root(tmp_path)
    canonical_key = canonical.resolve()
    assert canonical_key in cache._rules

    # User deletes only the mixed-case sibling. Watchdog fires for
    # `.DropboxIgnore` deletion.
    mixed.unlink()
    cache.remove_file(mixed)

    assert canonical_key in cache._rules, (
        "canonical sibling's cache entry must NOT be dropped when only the "
        "mixed-case sibling was deleted"
    )


def test_reconcile_path_recognizes_mixed_case_dropboxignore(
    tmp_path: Path,
    fake_markers: FakeMarkers,
    write_file: WriteFile,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_reconcile_path's "rule file marked ignored" warning + override
    must fire for mixed-case names too. Without case-insensitive
    recognition, a `.DropboxIgnore` that ended up marked would be
    silently cleared without the warning (item #92)."""
    import logging

    from dbxignore import reconcile

    rule_file = tmp_path / ".DropboxIgnore"
    rule_file.write_text("build/\n", encoding="utf-8")
    # Pre-seed a marker on the rule file (project invariant: rule files
    # are never marked; if they are, reconcile clears them with a
    # WARNING). The case-insensitive recognition is what makes the
    # warning fire on mixed-case names too.
    fake_markers.set_ignored(rule_file)

    cache = RuleCache()
    cache.load_root(tmp_path)

    with caplog.at_level(logging.WARNING, logger="dbxignore.reconcile"):
        reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert any("was marked ignored" in rec.message for rec in caplog.records), (
        f"expected rule-file warning; got {[r.message for r in caplog.records]}"
    )
    # Marker cleared regardless (the invariant restoration always fires).
    assert rule_file.resolve() not in fake_markers._ignored

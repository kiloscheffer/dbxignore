from pathlib import Path

import pytest

from dbxignore.rules import RuleCache
from tests.conftest import WriteFile


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

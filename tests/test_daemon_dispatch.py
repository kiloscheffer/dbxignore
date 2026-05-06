from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dbxignore import daemon
from dbxignore.debounce import EventKind


def _stub_event(
    kind: str, src_path: str, is_directory: bool = False, dest_path: str | None = None
) -> MagicMock:
    e = MagicMock()
    e.event_type = kind
    e.src_path = src_path
    e.dest_path = dest_path
    e.is_directory = is_directory
    return e


def test_classify_rules_file_created(tmp_path: Path) -> None:
    # Resolve at the boundary, mirroring run()'s contract — keeps the test
    # robust on macOS where tmp_path lives under /tmp -> /private/tmp.
    root = tmp_path.resolve()
    src = root / "proj" / ".dropboxignore"
    src.parent.mkdir(parents=True)
    src.write_text("", encoding="utf-8")
    ev = _stub_event("created", str(src))
    classification = daemon._classify(ev, roots=[root])
    assert classification is not None
    kind, key, classified_root, classified_src, _ = classification
    assert kind == EventKind.RULES
    assert key == str(src.resolve()).lower()
    assert classified_root == root
    assert classified_src == src.resolve()


def test_classify_directory_created(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    src = root / "proj" / "node_modules"
    src.mkdir(parents=True)
    ev = _stub_event("created", str(src), is_directory=True)
    classification = daemon._classify(ev, roots=[root])
    assert classification is not None
    kind, _key, classified_root, classified_src, _ = classification
    assert kind == EventKind.DIR_CREATE
    assert classified_root == root
    assert classified_src == src.resolve()


def test_classify_file_modified_is_ignored() -> None:
    ev = _stub_event("modified", r"C:\Dropbox\proj\foo.txt", is_directory=False)
    assert daemon._classify(ev, roots=[Path(r"C:\Dropbox")]) is None


def test_classify_delete_is_ignored_for_non_rules_file() -> None:
    ev = _stub_event("deleted", r"C:\Dropbox\proj\foo.txt")
    assert daemon._classify(ev, roots=[Path(r"C:\Dropbox")]) is None


def test_classify_event_outside_any_root_is_ignored() -> None:
    ev = _stub_event("created", r"D:\Other\foo", is_directory=True)
    assert daemon._classify(ev, roots=[Path(r"C:\Dropbox")]) is None


def test_dispatch_rules_reloads_and_reconciles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pre-resolve to mirror run()'s boundary contract — keeps assertions
    # comparing resolved-vs-resolved on macOS (/tmp -> /private/tmp).
    root = tmp_path.resolve()
    cache = MagicMock()
    reconcile_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        daemon, "reconcile_subtree", lambda r, sub, c: reconcile_calls.append((r, sub))
    )

    ignore_file = root / "proj" / ".dropboxignore"
    ignore_file.parent.mkdir()
    ignore_file.write_text("build/\n", encoding="utf-8")

    ev = _stub_event("modified", str(ignore_file))
    daemon._dispatch(ev, cache, roots=[root])

    cache.reload_file.assert_called_once_with(ignore_file)
    assert reconcile_calls == [(root, ignore_file.parent)]


def test_dispatch_dir_create_reconciles_that_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    cache = MagicMock()
    reconcile_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        daemon, "reconcile_subtree", lambda r, sub, c: reconcile_calls.append((r, sub))
    )

    new_dir = root / "proj" / "node_modules"
    new_dir.mkdir(parents=True)

    ev = _stub_event("created", str(new_dir), is_directory=True)
    daemon._dispatch(ev, cache, roots=[root])

    cache.reload_file.assert_not_called()
    assert reconcile_calls == [(root, new_dir)]


def test_dispatch_deleted_rules_file_removes_from_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    cache = MagicMock()
    reconcile_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        daemon, "reconcile_subtree", lambda r, sub, c: reconcile_calls.append((r, sub))
    )

    ignore_file = root / "proj" / ".dropboxignore"
    ignore_file.parent.mkdir()
    # File doesn't exist — simulates post-delete event.

    ev = _stub_event("deleted", str(ignore_file))
    daemon._dispatch(ev, cache, roots=[root])

    cache.remove_file.assert_called_once_with(ignore_file)
    assert reconcile_calls == [(root, ignore_file.parent)]


def test_dispatch_moved_non_rules_reconciles_both_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    cache = MagicMock()
    reconcile_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        daemon, "reconcile_subtree", lambda r, sub, c: reconcile_calls.append((r, sub))
    )

    (root / "old_dir").mkdir()
    (root / "new_dir").mkdir()
    old_file = root / "old_dir" / "foo.txt"
    new_file = root / "new_dir" / "foo.txt"
    # Only the destination exists on disk after a move.
    new_file.write_text("x", encoding="utf-8")

    ev = _stub_event("moved", str(old_file), dest_path=str(new_file))
    daemon._dispatch(ev, cache, roots=[root])

    # Both parents reconciled; cache is untouched for non-rules files.
    cache.reload_file.assert_not_called()
    cache.remove_file.assert_not_called()
    assert sorted(reconcile_calls, key=lambda rc: str(rc[1])) == sorted(
        [(root, old_file.parent), (root, new_file.parent)],
        key=lambda rc: str(rc[1]),
    )


def test_dispatch_moved_non_rules_dest_outside_any_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    cache = MagicMock()
    reconcile_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        daemon, "reconcile_subtree", lambda r, sub, c: reconcile_calls.append((r, sub))
    )

    (root / "old_dir").mkdir()
    old_file = root / "old_dir" / "foo.txt"
    # Dest is outside any watched root — should not be reconciled.
    dest_outside = Path(r"D:\Elsewhere\foo.txt")

    ev = _stub_event("moved", str(old_file), dest_path=str(dest_outside))
    daemon._dispatch(ev, cache, roots=[root])

    assert reconcile_calls == [(root, old_file.parent)]


def test_handler_bypasses_debouncer_for_matched_dir_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DIR_CREATE for a path that already matches a cached rule fast-paths
    to reconcile_subtree synchronously, skipping the debouncer queue (item 57)."""
    root = tmp_path.resolve()
    cache = MagicMock()
    cache.match.return_value = True
    debouncer = MagicMock()
    reconcile_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        daemon, "reconcile_subtree", lambda r, sub, c: reconcile_calls.append((r, sub))
    )

    new_dir = root / "node_modules"
    new_dir.mkdir()

    handler = daemon._WatchdogHandler(debouncer, [root], cache)
    handler.on_any_event(_stub_event("created", str(new_dir), is_directory=True))

    debouncer.submit.assert_not_called()
    assert reconcile_calls == [(root, new_dir.resolve())]


def test_handler_uses_debouncer_for_unmatched_dir_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DIR_CREATE for a path that doesn't match any cached rule still goes
    through the debouncer — the bypass is conditional on a positive match."""
    root = tmp_path.resolve()
    cache = MagicMock()
    cache.match.return_value = False
    debouncer = MagicMock()
    reconcile_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        daemon, "reconcile_subtree", lambda r, sub, c: reconcile_calls.append((r, sub))
    )

    new_dir = root / "src"
    new_dir.mkdir()

    handler = daemon._WatchdogHandler(debouncer, [root], cache)
    handler.on_any_event(_stub_event("created", str(new_dir), is_directory=True))

    debouncer.submit.assert_called_once()
    assert reconcile_calls == []


def test_handler_uses_debouncer_for_rules_events(tmp_path: Path) -> None:
    """RULES events keep their debounce — only matched DIR_CREATE bypasses.
    cache.match shouldn't even be consulted for non-DIR_CREATE kinds."""
    root = tmp_path.resolve()
    cache = MagicMock()
    debouncer = MagicMock()

    rules_file = root / "proj" / ".dropboxignore"
    rules_file.parent.mkdir()
    rules_file.write_text("build/\n", encoding="utf-8")

    handler = daemon._WatchdogHandler(debouncer, [root], cache)
    handler.on_any_event(_stub_event("modified", str(rules_file)))

    debouncer.submit.assert_called_once()
    cache.match.assert_not_called()


def test_dispatch_moved_rules_reloads_at_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    cache = MagicMock()
    reconcile_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        daemon, "reconcile_subtree", lambda r, sub, c: reconcile_calls.append((r, sub))
    )

    (root / "old_proj").mkdir()
    (root / "new_proj").mkdir()
    old_file = root / "old_proj" / ".dropboxignore"
    new_file = root / "new_proj" / ".dropboxignore"
    # Only the destination exists on disk after a move.
    new_file.write_text("build/\n", encoding="utf-8")

    ev = _stub_event("moved", str(old_file), dest_path=str(new_file))
    daemon._dispatch(ev, cache, roots=[root])

    cache.remove_file.assert_called_once_with(old_file)
    cache.reload_file.assert_called_once_with(new_file)
    # Both parents reconciled.
    assert sorted(reconcile_calls, key=lambda rc: str(rc[1])) == sorted(
        [(root, old_file.parent), (root, new_file.parent)],
        key=lambda rc: str(rc[1]),
    )


def test_classify_moved_dest_is_rule_file_classifies_as_rules(tmp_path: Path) -> None:
    """A moved event whose dest_path is .dropboxignore must classify as RULES
    even when src_path is not — atomic-save editors rename a temp file into
    place, so the rule cache only sees the rename event and would otherwise
    miss the new rules until the next hourly sweep."""
    root = tmp_path.resolve()
    proj = root / "proj"
    proj.mkdir()
    src = proj / ".dropboxignore.tmp"
    dest = proj / ".dropboxignore"
    dest.write_text("build/\n", encoding="utf-8")

    ev = _stub_event("moved", str(src), dest_path=str(dest))
    classification = daemon._classify(ev, roots=[root])

    assert classification is not None
    kind, _key, classified_root, _src, _ = classification
    assert kind == EventKind.RULES
    assert classified_root == root


def test_classify_moved_into_rules_keys_on_dest_for_debounce_coalesce(tmp_path: Path) -> None:
    """Atomic-save editors generate a unique tmp filename per save (vim's
    `4913`, mktemp randomness, kakoune's `<pid>`-suffixed tmp). The classify
    key must be derived from the destination rule-file path so a burst of
    saves of the same `.dropboxignore` coalesces in the RULES debounce
    window; keying on src would assign every save a distinct token."""
    root = tmp_path.resolve()
    proj = root / "proj"
    proj.mkdir()
    dest = proj / ".dropboxignore"
    dest.write_text("build/\n", encoding="utf-8")

    save_a = _stub_event("moved", str(proj / "4913"), dest_path=str(dest))
    save_b = _stub_event("moved", str(proj / "8231"), dest_path=str(dest))

    classification_a = daemon._classify(save_a, roots=[root])
    classification_b = daemon._classify(save_b, roots=[root])
    assert classification_a is not None
    assert classification_b is not None
    _, key_a, _, _, _ = classification_a
    _, key_b, _, _, _ = classification_b

    assert key_a == key_b
    assert str(dest).lower() in key_a


def test_classify_moved_with_empty_src_to_rule_dest_classifies_as_rules(tmp_path: Path) -> None:
    """Cross-watch move shape: watchdog emits a moved event with empty
    `src_path` when the source side was in a non-watched directory (the
    kernel's IN_MOVED_TO without a matching IN_MOVED_FROM, similar shapes
    on Windows / macOS). The early `located is None` guard would drop the
    event; this branch must re-check the dest before discarding."""
    root = tmp_path.resolve()
    proj = root / "proj"
    proj.mkdir()
    dest = proj / ".dropboxignore"
    dest.write_text("build/\n", encoding="utf-8")

    ev = _stub_event("moved", "", dest_path=str(dest))
    classification = daemon._classify(ev, roots=[root])

    assert classification is not None
    kind, _key, classified_root, classified_src, _ = classification
    assert kind == EventKind.RULES
    assert classified_root == root
    assert classified_src == dest.resolve()


def test_classify_moved_with_external_src_to_rule_dest_classifies_as_rules(tmp_path: Path) -> None:
    """Same cross-watch case but with a non-empty src_path that's outside
    every watched root (e.g., a download directory or system temp location).
    `_resolve_under_roots` returns None for the src; the dest-path fallback
    must still classify as RULES."""
    watched = (tmp_path / "watched").resolve()
    watched.mkdir()
    external = tmp_path / "external"
    external.mkdir()

    proj = watched / "proj"
    proj.mkdir()
    dest = proj / ".dropboxignore"
    dest.write_text("build/\n", encoding="utf-8")

    src_outside = external / "downloaded.dropboxignore"

    ev = _stub_event("moved", str(src_outside), dest_path=str(dest))
    classification = daemon._classify(ev, roots=[watched])

    assert classification is not None
    kind, _key, classified_root, classified_src, _ = classification
    assert kind == EventKind.RULES
    assert classified_root == watched
    assert classified_src == dest.resolve()


def test_dispatch_moved_rules_to_non_rules_cross_parent_reconciles_dest_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule -> non-rule across different parents (e.g. user renames
    `/A/.dropboxignore` to `/B/foo.bak`): src.parent must be reconciled
    (rule cache lost a file there), AND dest.parent must be reconciled
    too — the file lands in /B where rules from /B's tree may now apply
    to it. Without the dest.parent reconcile, the moved file goes
    unmarked until the next event or hourly sweep."""
    root = tmp_path.resolve()
    cache = MagicMock()
    reconcile_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        daemon, "reconcile_subtree", lambda r, sub, c: reconcile_calls.append((r, sub))
    )

    src_dir = root / "A"
    src_dir.mkdir()
    dest_dir = root / "B"
    dest_dir.mkdir()
    src = src_dir / ".dropboxignore"
    dest = dest_dir / "foo.bak"
    # Only the destination exists on disk after a move.
    dest.write_text("", encoding="utf-8")

    ev = _stub_event("moved", str(src), dest_path=str(dest))
    daemon._dispatch(ev, cache, roots=[root])

    cache.remove_file.assert_called_once_with(src)
    cache.reload_file.assert_not_called()
    assert sorted(reconcile_calls, key=lambda rc: str(rc[1])) == sorted(
        [(root, src.parent), (root, dest.parent)],
        key=lambda rc: str(rc[1]),
    )


def test_dispatch_moved_with_external_src_reloads_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: dispatch on a cross-watch move (src outside, dest is rule)
    must call `cache.reload_file(dest)` and reconcile dest.parent. Without
    this, a rule file moved in from an external location is invisible until
    the hourly sweep."""
    watched = (tmp_path / "watched").resolve()
    watched.mkdir()
    external = tmp_path / "external"
    external.mkdir()

    cache = MagicMock()
    reconcile_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        daemon, "reconcile_subtree", lambda r, sub, c: reconcile_calls.append((r, sub))
    )

    proj = watched / "proj"
    proj.mkdir()
    dest = proj / ".dropboxignore"
    dest.write_text("build/\n", encoding="utf-8")

    src_outside = external / "downloaded.dropboxignore"
    ev = _stub_event("moved", str(src_outside), dest_path=str(dest))

    daemon._dispatch(ev, cache, roots=[watched])

    cache.reload_file.assert_called_once_with(dest.resolve())
    # No phantom remove_file: src was outside any watched root, so there
    # was never a cached entry to remove. Calling it would fire
    # `_recompute_conflicts` an extra time on every cross-watch event.
    cache.remove_file.assert_not_called()
    assert reconcile_calls == [(watched, dest.parent.resolve())]


def test_classify_moved_out_and_moved_into_same_path_have_distinct_keys(tmp_path: Path) -> None:
    """A move-out (`A/.dropboxignore` -> `B/.dropboxignore`, src is rule)
    keys via the src-path branch; a move-into (`tmp` -> `A/.dropboxignore`,
    dest is rule) keys via the dest-path branch. Both events touching the
    same `A/.dropboxignore` path must produce DIFFERENT debouncer tokens —
    they're semantically distinct (move-out's dest-side reload of B would
    be lost if last-wins coalescing collapsed them). Pin the disambiguation
    so a future refactor can't reintroduce the collision."""
    root = tmp_path.resolve()
    a = root / "A"
    b = root / "B"
    a.mkdir()
    b.mkdir()
    a_rule = a / ".dropboxignore"
    b_rule = b / ".dropboxignore"
    b_rule.write_text("build/\n", encoding="utf-8")
    tmp_at_a = a / "tmp.4913"

    move_out = _stub_event("moved", str(a_rule), dest_path=str(b_rule))
    move_in = _stub_event("moved", str(tmp_at_a), dest_path=str(a_rule))

    classification_out = daemon._classify(move_out, roots=[root])
    classification_in = daemon._classify(move_in, roots=[root])
    assert classification_out is not None
    assert classification_in is not None
    _, key_out, _, _, _ = classification_out
    _, key_in, _, _, _ = classification_in

    assert key_out != key_in


def test_dispatch_moved_non_rules_to_rules_reloads_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomic-save: rename `.dropboxignore.tmp` -> `.dropboxignore`. Cache
    must reload at the dest; src was never cached so remove_file is a no-op."""
    root = tmp_path.resolve()
    cache = MagicMock()
    reconcile_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        daemon, "reconcile_subtree", lambda r, sub, c: reconcile_calls.append((r, sub))
    )

    proj = root / "proj"
    proj.mkdir()
    src = proj / ".dropboxignore.tmp"
    dest = proj / ".dropboxignore"
    dest.write_text("build/\n", encoding="utf-8")

    ev = _stub_event("moved", str(src), dest_path=str(dest))
    daemon._dispatch(ev, cache, roots=[root])

    cache.reload_file.assert_called_once_with(dest)
    cache.remove_file.assert_not_called()
    assert reconcile_calls == [(root, proj)]


def test_dispatch_moved_non_rules_to_rules_reloads_before_reconciling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomic-save same-parent: the cache reload MUST happen before the
    reconcile, otherwise the same-parent dedupe collapses to a single
    reconcile call running against the stale cache and the new rules
    don't take effect until another event or the hourly sweep."""
    root = tmp_path.resolve()
    cache = MagicMock()
    call_order: list[str] = []
    cache.reload_file = MagicMock(side_effect=lambda *a, **k: call_order.append("reload"))
    monkeypatch.setattr(
        daemon, "reconcile_subtree", lambda r, sub, c: call_order.append("reconcile")
    )

    proj = root / "proj"
    proj.mkdir()
    src = proj / ".dropboxignore.tmp"
    dest = proj / ".dropboxignore"
    dest.write_text("build/\n", encoding="utf-8")

    ev = _stub_event("moved", str(src), dest_path=str(dest))
    daemon._dispatch(ev, cache, roots=[root])

    assert call_order == ["reload", "reconcile"]


def test_dispatch_moved_rules_to_non_rules_does_not_reload_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Editor save-via-rename step: `.dropboxignore` -> `.dropboxignore~`.
    Dispatch must drop the old rule file from the cache and must NOT call
    `cache.reload_file` on the backup. Pins the dispatch contract only;
    the downstream `_build_sequence` cleanliness is a consequence covered
    by the rule-cache layer."""
    root = tmp_path.resolve()
    cache = MagicMock()
    reconcile_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        daemon, "reconcile_subtree", lambda r, sub, c: reconcile_calls.append((r, sub))
    )

    proj = root / "proj"
    proj.mkdir()
    src = proj / ".dropboxignore"
    dest = proj / ".dropboxignore~"
    dest.write_text("build/\n", encoding="utf-8")

    ev = _stub_event("moved", str(src), dest_path=str(dest))
    daemon._dispatch(ev, cache, roots=[root])

    cache.remove_file.assert_called_once_with(src)
    cache.reload_file.assert_not_called()
    assert reconcile_calls == [(root, proj)]


def test_timeouts_from_env_falls_back_on_invalid_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A typo'd env value (`DBXIGNORE_DEBOUNCE_OTHER_MS=fast`) must not
    crash daemon startup. Parse defensively, log a WARNING naming the
    bad value, and fall back to the default. Mirrors the existing
    `DBXIGNORE_LOG_LEVEL` validation pattern."""
    import logging

    monkeypatch.setenv("DBXIGNORE_DEBOUNCE_OTHER_MS", "fast")
    monkeypatch.setenv("DBXIGNORE_DEBOUNCE_RULES_MS", "150")

    with caplog.at_level(logging.WARNING, logger="dbxignore.daemon"):
        timeouts = daemon._timeouts_from_env()

    assert timeouts[EventKind.OTHER] == daemon.DEFAULT_TIMEOUTS_MS[EventKind.OTHER]
    assert timeouts[EventKind.RULES] == 150
    assert any(
        "DBXIGNORE_DEBOUNCE_OTHER_MS" in r.message and "fast" in r.message
        for r in caplog.records
    ), [r.message for r in caplog.records]


def test_timeouts_from_env_rejects_negative_values(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Negative debounce timeouts make no sense and would underflow the
    monotonic-deadline arithmetic in the Debouncer; warn and fall back."""
    import logging

    monkeypatch.setenv("DBXIGNORE_DEBOUNCE_RULES_MS", "-50")

    with caplog.at_level(logging.WARNING, logger="dbxignore.daemon"):
        timeouts = daemon._timeouts_from_env()

    assert timeouts[EventKind.RULES] == daemon.DEFAULT_TIMEOUTS_MS[EventKind.RULES]
    assert any("-50" in r.message for r in caplog.records)

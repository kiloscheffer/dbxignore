from pathlib import Path
from unittest.mock import MagicMock

from dbxignore import daemon
from dbxignore.debounce import EventKind


def _stub_event(kind: str, src_path: str, is_directory: bool = False, dest_path: str | None = None):
    e = MagicMock()
    e.event_type = kind
    e.src_path = src_path
    e.dest_path = dest_path
    e.is_directory = is_directory
    return e


def test_classify_rules_file_created(tmp_path):
    # Resolve at the boundary, mirroring run()'s contract — keeps the test
    # robust on macOS where tmp_path lives under /tmp -> /private/tmp.
    root = tmp_path.resolve()
    src = root / "proj" / ".dropboxignore"
    src.parent.mkdir(parents=True)
    src.write_text("", encoding="utf-8")
    ev = _stub_event("created", str(src))
    kind, key, classified_root, classified_src = daemon._classify(ev, roots=[root])
    assert kind == EventKind.RULES
    assert key == str(src.resolve()).lower()
    assert classified_root == root
    assert classified_src == src.resolve()


def test_classify_directory_created(tmp_path):
    root = tmp_path.resolve()
    src = root / "proj" / "node_modules"
    src.mkdir(parents=True)
    ev = _stub_event("created", str(src), is_directory=True)
    kind, _key, classified_root, classified_src = daemon._classify(ev, roots=[root])
    assert kind == EventKind.DIR_CREATE
    assert classified_root == root
    assert classified_src == src.resolve()


def test_classify_file_modified_is_ignored():
    ev = _stub_event("modified", r"C:\Dropbox\proj\foo.txt", is_directory=False)
    assert daemon._classify(ev, roots=[Path(r"C:\Dropbox")]) is None


def test_classify_delete_is_ignored_for_non_rules_file():
    ev = _stub_event("deleted", r"C:\Dropbox\proj\foo.txt")
    assert daemon._classify(ev, roots=[Path(r"C:\Dropbox")]) is None


def test_classify_event_outside_any_root_is_ignored():
    ev = _stub_event("created", r"D:\Other\foo", is_directory=True)
    assert daemon._classify(ev, roots=[Path(r"C:\Dropbox")]) is None


def test_dispatch_rules_reloads_and_reconciles(tmp_path, monkeypatch):
    # Pre-resolve to mirror run()'s boundary contract — keeps assertions
    # comparing resolved-vs-resolved on macOS (/tmp -> /private/tmp).
    root = tmp_path.resolve()
    cache = MagicMock()
    reconcile_calls: list = []
    monkeypatch.setattr(daemon, "reconcile_subtree",
                        lambda r, sub, c: reconcile_calls.append((r, sub)))

    ignore_file = root / "proj" / ".dropboxignore"
    ignore_file.parent.mkdir()
    ignore_file.write_text("build/\n", encoding="utf-8")

    ev = _stub_event("modified", str(ignore_file))
    daemon._dispatch(ev, cache, roots=[root])

    cache.reload_file.assert_called_once_with(ignore_file)
    assert reconcile_calls == [(root, ignore_file.parent)]


def test_dispatch_dir_create_reconciles_that_dir(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    cache = MagicMock()
    reconcile_calls: list = []
    monkeypatch.setattr(daemon, "reconcile_subtree",
                        lambda r, sub, c: reconcile_calls.append((r, sub)))

    new_dir = root / "proj" / "node_modules"
    new_dir.mkdir(parents=True)

    ev = _stub_event("created", str(new_dir), is_directory=True)
    daemon._dispatch(ev, cache, roots=[root])

    cache.reload_file.assert_not_called()
    assert reconcile_calls == [(root, new_dir)]


def test_dispatch_deleted_rules_file_removes_from_cache(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    cache = MagicMock()
    reconcile_calls: list = []
    monkeypatch.setattr(daemon, "reconcile_subtree",
                        lambda r, sub, c: reconcile_calls.append((r, sub)))

    ignore_file = root / "proj" / ".dropboxignore"
    ignore_file.parent.mkdir()
    # File doesn't exist — simulates post-delete event.

    ev = _stub_event("deleted", str(ignore_file))
    daemon._dispatch(ev, cache, roots=[root])

    cache.remove_file.assert_called_once_with(ignore_file)
    assert reconcile_calls == [(root, ignore_file.parent)]


def test_dispatch_moved_non_rules_reconciles_both_parents(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    cache = MagicMock()
    reconcile_calls: list = []
    monkeypatch.setattr(daemon, "reconcile_subtree",
                        lambda r, sub, c: reconcile_calls.append((r, sub)))

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


def test_dispatch_moved_non_rules_dest_outside_any_root(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    cache = MagicMock()
    reconcile_calls: list = []
    monkeypatch.setattr(daemon, "reconcile_subtree",
                        lambda r, sub, c: reconcile_calls.append((r, sub)))

    (root / "old_dir").mkdir()
    old_file = root / "old_dir" / "foo.txt"
    # Dest is outside any watched root — should not be reconciled.
    dest_outside = Path(r"D:\Elsewhere\foo.txt")

    ev = _stub_event("moved", str(old_file), dest_path=str(dest_outside))
    daemon._dispatch(ev, cache, roots=[root])

    assert reconcile_calls == [(root, old_file.parent)]


def test_dispatch_moved_rules_reloads_at_dest(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    cache = MagicMock()
    reconcile_calls: list = []
    monkeypatch.setattr(daemon, "reconcile_subtree",
                        lambda r, sub, c: reconcile_calls.append((r, sub)))

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

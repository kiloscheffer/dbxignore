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
    root = tmp_path
    src = root / "proj" / ".dropboxignore"
    ev = _stub_event("created", str(src))
    kind, key = daemon._classify(ev, roots=[root])
    assert kind == EventKind.RULES
    assert key == str(src).lower()


def test_classify_directory_created(tmp_path):
    root = tmp_path
    src = root / "proj" / "node_modules"
    ev = _stub_event("created", str(src), is_directory=True)
    kind, _key = daemon._classify(ev, roots=[root])
    assert kind == EventKind.DIR_CREATE


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
    cache = MagicMock()
    reconcile_calls: list = []
    monkeypatch.setattr(daemon, "reconcile_subtree",
                        lambda root, sub, c: reconcile_calls.append((root, sub)))

    ignore_file = tmp_path / "proj" / ".dropboxignore"
    ignore_file.parent.mkdir()
    ignore_file.write_text("build/\n", encoding="utf-8")

    ev = _stub_event("modified", str(ignore_file))
    daemon._dispatch(ev, cache, roots=[tmp_path])

    cache.reload_file.assert_called_once_with(ignore_file)
    assert reconcile_calls == [(tmp_path, ignore_file.parent)]


def test_dispatch_dir_create_reconciles_that_dir(tmp_path, monkeypatch):
    cache = MagicMock()
    reconcile_calls: list = []
    monkeypatch.setattr(daemon, "reconcile_subtree",
                        lambda root, sub, c: reconcile_calls.append((root, sub)))

    new_dir = tmp_path / "proj" / "node_modules"
    new_dir.mkdir(parents=True)

    ev = _stub_event("created", str(new_dir), is_directory=True)
    daemon._dispatch(ev, cache, roots=[tmp_path])

    cache.reload_file.assert_not_called()
    assert reconcile_calls == [(tmp_path, new_dir)]


def test_dispatch_deleted_rules_file_removes_from_cache(tmp_path, monkeypatch):
    cache = MagicMock()
    reconcile_calls: list = []
    monkeypatch.setattr(daemon, "reconcile_subtree",
                        lambda root, sub, c: reconcile_calls.append((root, sub)))

    ignore_file = tmp_path / "proj" / ".dropboxignore"
    ignore_file.parent.mkdir()
    # File doesn't exist — simulates post-delete event.

    ev = _stub_event("deleted", str(ignore_file))
    daemon._dispatch(ev, cache, roots=[tmp_path])

    cache.remove_file.assert_called_once_with(ignore_file)
    assert reconcile_calls == [(tmp_path, ignore_file.parent)]


def test_dispatch_moved_non_rules_reconciles_both_parents(tmp_path, monkeypatch):
    cache = MagicMock()
    reconcile_calls: list = []
    monkeypatch.setattr(daemon, "reconcile_subtree",
                        lambda root, sub, c: reconcile_calls.append((root, sub)))

    (tmp_path / "old_dir").mkdir()
    (tmp_path / "new_dir").mkdir()
    old_file = tmp_path / "old_dir" / "foo.txt"
    new_file = tmp_path / "new_dir" / "foo.txt"
    # Only the destination exists on disk after a move.
    new_file.write_text("x", encoding="utf-8")

    ev = _stub_event("moved", str(old_file), dest_path=str(new_file))
    daemon._dispatch(ev, cache, roots=[tmp_path])

    # Both parents reconciled; cache is untouched for non-rules files.
    cache.reload_file.assert_not_called()
    cache.remove_file.assert_not_called()
    assert sorted(reconcile_calls, key=lambda rc: str(rc[1])) == sorted(
        [(tmp_path, old_file.parent), (tmp_path, new_file.parent)],
        key=lambda rc: str(rc[1]),
    )


def test_dispatch_moved_non_rules_dest_outside_any_root(tmp_path, monkeypatch):
    cache = MagicMock()
    reconcile_calls: list = []
    monkeypatch.setattr(daemon, "reconcile_subtree",
                        lambda root, sub, c: reconcile_calls.append((root, sub)))

    (tmp_path / "old_dir").mkdir()
    old_file = tmp_path / "old_dir" / "foo.txt"
    # Dest is outside any watched root — should not be reconciled.
    dest_outside = Path(r"D:\Elsewhere\foo.txt")

    ev = _stub_event("moved", str(old_file), dest_path=str(dest_outside))
    daemon._dispatch(ev, cache, roots=[tmp_path])

    assert reconcile_calls == [(tmp_path, old_file.parent)]


def test_dispatch_moved_rules_reloads_at_dest(tmp_path, monkeypatch):
    cache = MagicMock()
    reconcile_calls: list = []
    monkeypatch.setattr(daemon, "reconcile_subtree",
                        lambda root, sub, c: reconcile_calls.append((root, sub)))

    (tmp_path / "old_proj").mkdir()
    (tmp_path / "new_proj").mkdir()
    old_file = tmp_path / "old_proj" / ".dropboxignore"
    new_file = tmp_path / "new_proj" / ".dropboxignore"
    # Only the destination exists on disk after a move.
    new_file.write_text("build/\n", encoding="utf-8")

    ev = _stub_event("moved", str(old_file), dest_path=str(new_file))
    daemon._dispatch(ev, cache, roots=[tmp_path])

    cache.remove_file.assert_called_once_with(old_file)
    cache.reload_file.assert_called_once_with(new_file)
    # Both parents reconciled.
    assert sorted(reconcile_calls, key=lambda rc: str(rc[1])) == sorted(
        [(tmp_path, old_file.parent), (tmp_path, new_file.parent)],
        key=lambda rc: str(rc[1]),
    )

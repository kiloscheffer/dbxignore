"""Synthetic-event end-to-end dispatch tests.

These exercise the full rule-load + reconcile + marker-write chain by firing
stub watchdog events directly into ``daemon._dispatch``, with a real
``RuleCache`` against an in-memory ``FakeMarkers`` backend. They are the
deterministic counterpart to ``tests/test_daemon_smoke.py``: that test
brings up a real daemon thread + ``Observer`` and is Windows-only and
flake-prone (PR #135 instrumentation in PR #136 captured a trace showing
``ReadDirectoryChangesW`` events silently dropped on CI runners — see
backlog item #34). Bypassing the watchdog event loop here removes the
kernel-event-delivery dependency while preserving coverage of every layer
the smoke test was uniquely covering: rule-cache reload, conflict
detection, reconcile traversal, and the ``set_ignored`` / ``clear_ignored``
calls reconcile drives.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from dbxignore import daemon
from dbxignore.rules import RuleCache

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from tests.conftest import FakeMarkers, WriteFile

from tests.conftest import stub_event


def test_rule_create_event_marks_matched_directory(
    tmp_path: Path, fake_markers: FakeMarkers, write_file: WriteFile
) -> None:
    root = tmp_path.resolve()
    (root / "build").mkdir()
    rule_file = write_file(root / ".dropboxignore", "build/\n")

    cache = RuleCache()
    cache.load_root(root)

    daemon._dispatch(stub_event("created", str(rule_file)), cache, roots=[root])

    assert (root / "build") in fake_markers.set_calls


def test_rule_modify_event_dropping_negation_emits_warning_and_keeps_parent(
    tmp_path: Path,
    fake_markers: FakeMarkers,
    write_file: WriteFile,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Append a negation the conflict detector drops at rule-load time.

    Under PR #33's negation-conflict semantics, ``!build/keep/`` underneath
    an earlier ``build/`` include is masked at rule-load time, dropped from
    the active rule set, and a WARNING emits. After the rule-modify event
    flows through ``_dispatch``: the parent ``build/`` stays marked (the
    re-walk doesn't clear it), the cache's match for the negated path still
    reports ``True`` (the dropped negation is silenced), and the conflict
    WARNING reaches ``dbxignore.rules`` at WARNING level.
    """
    root = tmp_path.resolve()
    (root / "build" / "keep").mkdir(parents=True)
    rule_file = write_file(root / ".dropboxignore", "build/\n")

    cache = RuleCache()
    cache.load_root(root)

    # Initial mark via the rule-create event.
    daemon._dispatch(stub_event("created", str(rule_file)), cache, roots=[root])
    assert (root / "build") in fake_markers.set_calls

    # Append the negation; replay as a rule-modify event.
    rule_file.write_text("build/\n!build/keep/\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="dbxignore.rules"):
        daemon._dispatch(stub_event("modified", str(rule_file)), cache, roots=[root])

    # Parent stays marked; the rule-modify reconcile didn't clear it.
    assert fake_markers.is_ignored(root / "build")
    # The dropped negation is silenced — cache.match treats the negated path
    # as still matched (Dropbox's inheritance-from-marked-ancestor model).
    assert cache.match(root / "build" / "keep")
    assert any(
        "!build/keep/" in record.message and "masked by" in record.message
        for record in caplog.records
    ), f"expected conflict WARNING in caplog, got: {[r.message for r in caplog.records]}"


def test_dir_create_event_marks_directory_when_rule_exists(
    tmp_path: Path, fake_markers: FakeMarkers, write_file: WriteFile
) -> None:
    """The non-fast-path arm of DIR_CREATE dispatch.

    ``_WatchdogHandler.on_any_event`` short-circuits matched DIR_CREATEs
    before the debouncer (daemon.py:519). Once an event reaches
    ``_dispatch`` itself, it falls through to the unconditional
    reconcile-the-new-dir arm. Both arms produce the same outcome — this
    test pins the latter.
    """
    root = tmp_path.resolve()
    write_file(root / ".dropboxignore", "build/\n")
    (root / "build").mkdir()

    cache = RuleCache()
    cache.load_root(root)

    daemon._dispatch(
        stub_event("created", str(root / "build"), is_directory=True),
        cache,
        roots=[root],
    )

    assert fake_markers.is_ignored(root / "build")

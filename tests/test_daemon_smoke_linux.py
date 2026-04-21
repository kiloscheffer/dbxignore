"""End-to-end: real xattrs + real daemon event loop on a Linux tmp tree.

Mirrors ``test_daemon_smoke.py`` (Windows) — isolates the
watchdog+debouncer+reconcile pipeline by monkey-patching root discovery
to a ``tmp_path`` scratch dir, then asserts that real ``user.com.dropbox.ignored``
xattrs land as rules change. The discovery layer has its own unit tests;
this smoke validates that all the pieces run together.
"""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.linux_only

if not sys.platform.startswith("linux"):
    pytest.skip(
        "Daemon smoke test exercises real user.* xattrs; Linux-only",
        allow_module_level=True,
    )


def _xattr_supported(path) -> bool:
    probe = path / ".xattr_probe"
    probe.touch()
    try:
        os.setxattr(os.fspath(probe), "user.dropboxignore.probe", b"1")
    except OSError:
        return False
    finally:
        probe.unlink(missing_ok=True)
    return True


@pytest.fixture(autouse=True)
def _require_xattr_fs(tmp_path):
    if not _xattr_supported(tmp_path):
        pytest.skip(f"tmp_path {tmp_path} rejects user.* xattrs")


def _poll_until(fn, timeout_s: float = 2.0, interval_s: float = 0.05) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(interval_s)
    return False


def _wait_for_daemon_watching(log_path, timeout_s: float = 3.0) -> bool:
    """Block until the daemon emits its ``watching roots: …`` line.

    On Linux, watchdog's inotify watches only observe events that fire
    *after* ``observer.schedule()`` registers the watch. Creating files
    before then is a silent miss — the next hourly sweep is 1h away.
    The ``watching roots`` log line is emitted immediately after
    ``observer.start()`` returns, so polling for it in ``daemon.log``
    gives the test a deterministic readiness signal without touching
    the daemon's public API.
    """
    return _poll_until(
        lambda: log_path.exists() and "watching roots:" in log_path.read_text(),
        timeout_s=timeout_s,
    )


def test_daemon_reacts_to_dropboxignore_add_and_remove(tmp_path, monkeypatch):
    """Adding a rule marks a matching path; removing the rule clears it.

    Deliberately avoids the Windows smoke's negation case (``!build/keep/``)
    because Linux's inotify fires DIR_CREATE fast enough to reach dispatch
    with the *old* rule cache, marking the child; the subsequent RULES
    reload then prunes descent at the still-ignored parent and the
    negation never unmarks the child. That prune/negation interaction is
    a product-behavior question tracked separately in the v0.2 follow-ups
    plan — out of scope for a pipeline smoke test.
    """
    from dropboxignore import daemon, markers

    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [tmp_path])
    # Route state.json + daemon.log off the real per-user XDG dir.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    log_path = tmp_path / "state" / "dropboxignore" / "daemon.log"

    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)
    t.start()
    try:
        assert _wait_for_daemon_watching(log_path), \
            "daemon never logged 'watching roots:' within 3s"

        # Phase 1: rule + directory → marker set.
        (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
        (tmp_path / "build").mkdir()

        assert _poll_until(lambda: markers.is_ignored(tmp_path / "build")), \
            "build/ was not marked ignored within 2s"

        # Phase 2: rule removed → marker cleared.
        (tmp_path / ".dropboxignore").write_text("", encoding="utf-8")

        assert _poll_until(
            lambda: not markers.is_ignored(tmp_path / "build"),
            timeout_s=3.0,
        ), "build/ marker was not cleared after .dropboxignore emptied"
    finally:
        stop.set()
        t.join(timeout=5.0)

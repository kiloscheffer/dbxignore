import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from dbxignore import daemon, markers

pytestmark = pytest.mark.windows_only

if sys.platform != "win32":
    pytest.skip(
        "Daemon smoke test exercises real NTFS ADS; Windows-only",
        allow_module_level=True,
    )


def _poll_until(fn: Callable[[], bool], timeout_s: float = 2.0, interval_s: float = 0.05) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(interval_s)
    return False


def _trace(msg: str) -> None:
    """Emit a timestamped trace line on stderr for the #34 timing investigation.

    Pairs with the DEBUG-level boundary logs in the daemon (which write to
    daemon.log under the test's LOCALAPPDATA redirect) so a CI-captured
    failure transcript shows the test's wall-clock anchors interleaved
    with the daemon's internal stage timings. ``time.monotonic()`` is
    process-wide, so timestamps from this function are directly comparable
    with ``time.perf_counter()``-derived durations in the daemon logs.
    """
    sys.stderr.write(f"TRACE {time.monotonic():.4f} {msg}\n")
    sys.stderr.flush()


def test_daemon_reacts_to_dropboxignore_and_directory_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Redirect roots.discover() to our fake dropbox root.
    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [tmp_path])  # type: ignore[attr-defined, unused-ignore]
    # Ensure the singleton check reads a fresh state path under tmp_path.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    # Capture per-stage timing for the backlog item #34 investigation: the
    # daemon emits DEBUG-level boundary logs at on_any_event, debouncer
    # submit/emit, _dispatch, reload_file, reconcile_subtree, and per-path
    # set_ignored. With DEBUG enabled they land in daemon.log under
    # LOCALAPPDATA. The on-failure dump below surfaces them in CI.
    monkeypatch.setenv("DBXIGNORE_LOG_LEVEL", "DEBUG")

    log_path = tmp_path / "LocalAppData" / "dbxignore" / "daemon.log"

    _trace("test: daemon thread starting")
    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)
    t.start()
    try:
        try:
            _trace("test: writing initial .dropboxignore (build/)")
            (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
            _trace("test: mkdir build/")
            (tmp_path / "build").mkdir()

            _trace("test: poll begin — markers.is_ignored(build)")
            assert _poll_until(lambda: markers.is_ignored(tmp_path / "build")), (
                "build/ was not marked ignored within 2s"
            )
            _trace("test: poll done — build is marked")

            # Append a negation; create the child. Under the new semantics
            # (v0.2 item 10 resolution) the negation is detected as conflicted
            # at rule-load time and dropped from the active rule set — so the
            # child stays marked, just like its parent. The daemon log should
            # carry the conflict WARNING.
            _trace("test: writing updated .dropboxignore (+!build/keep/)")
            (tmp_path / ".dropboxignore").write_text("build/\n!build/keep/\n", encoding="utf-8")
            _trace("test: mkdir build/keep/")
            (tmp_path / "build" / "keep").mkdir()
            _trace("test: poll begin — markers.is_ignored(build/keep)")

            # Wider timeout (5.0s vs the 3.0s used elsewhere in this test) to
            # absorb the watchdog event-ordering race documented in followup
            # item 18. See backlog item #34 for the recurrence history.
            assert _poll_until(
                lambda: markers.is_ignored(tmp_path / "build" / "keep"),
                timeout_s=5.0,
            ), "build/keep/ should stay marked — the negation is dropped"
            _trace("test: poll done — build/keep is marked")

            # Verify the WARNING made it into daemon.log. The log lives under
            # the test's LOCALAPPDATA redirect.
            assert _poll_until(
                lambda: (
                    log_path.exists()
                    and "!build/keep/" in log_path.read_text()
                    and "masked by" in log_path.read_text()
                ),
                timeout_s=3.0,
            ), "daemon.log should contain the conflict WARNING"
            _trace("test: poll done — conflict WARNING present in daemon.log")
        except Exception:
            # Item #34 investigation: dump the daemon's DEBUG-level boundary
            # log to stderr so CI captures the per-stage trace alongside the
            # AssertionError. The pairing with TRACE lines above lets a
            # post-hoc reader correlate test-side wall-clock anchors with
            # daemon-internal stage timings to identify which boundary
            # exceeded its budget under runner load.
            _trace("test: assertion failed — dumping daemon.log")
            sys.stderr.write("\n=== daemon.log on failure ===\n")
            if log_path.exists():
                sys.stderr.write(log_path.read_text())
            else:
                sys.stderr.write(f"(log_path {log_path} does not exist)\n")
            sys.stderr.write("\n=== end daemon.log ===\n")
            sys.stderr.flush()
            raise
    finally:
        stop.set()
        t.join(timeout=5.0)

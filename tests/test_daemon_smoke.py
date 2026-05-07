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


def test_daemon_starts_and_responds_to_one_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single-event watchdog canary: daemon thread + Observer wiring is alive.

    Reduced from a multi-event scenario that flaked weekly under Windows CI
    runner load (backlog item #34, six observations 2026-04-24 .. 2026-05-07).
    PR #135's DEBUG-level instrumentation captured a trace in PR #136 showing
    ``ReadDirectoryChangesW`` events silently dropped on the runner — not
    delayed. Ruling out timeout-widening as a fix shape.

    Multi-event coverage (rule-load + reconcile + conflict-detector WARNING)
    moved to ``tests/test_daemon_synthetic_events.py``, which fires synthetic
    events into ``daemon._dispatch`` directly and runs deterministically on
    every platform. This test stays Windows-only as the live-watchdog smoke:
    one event with a generous budget, so on the days the runner does deliver
    events the test passes; on the days it doesn't, the dropped-event
    behavior is documented and the assertion message points at the cause.
    """
    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [tmp_path])  # type: ignore[attr-defined, unused-ignore]
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    # DEBUG instrumentation stays installed: cheap (gated on logger level)
    # and useful if this canary ever flakes too — the daemon.log dump below
    # surfaces the per-stage trace the same way the multi-event variant did.
    monkeypatch.setenv("DBXIGNORE_LOG_LEVEL", "DEBUG")

    log_path = tmp_path / "LocalAppData" / "dbxignore" / "daemon.log"

    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)
    t.start()
    try:
        try:
            (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
            (tmp_path / "build").mkdir()

            # 10s budget: one event in flight, one reconcile to perform.
            # Generous relative to ~30ms reconcile + sub-second event delivery
            # under load. If this still flakes, candidate H8 (Windows kernel
            # silently dropping events — see CLAUDE.md gotcha) is the cause
            # and the test should be deleted, not widened further.
            assert _poll_until(lambda: markers.is_ignored(tmp_path / "build"), timeout_s=10.0), (
                "build/ was not marked ignored within 10s — likely a dropped "
                "ReadDirectoryChangesW event (see backlog item #34)."
            )
        except Exception:
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

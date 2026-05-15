import sys
import threading
from pathlib import Path

import pytest

from dbxignore import daemon, markers
from tests.conftest import _poll_until

pytestmark = pytest.mark.windows_only

if sys.platform != "win32":
    pytest.skip(
        "Daemon smoke test exercises real NTFS ADS; Windows-only",
        allow_module_level=True,
    )


def test_daemon_starts_and_responds_to_one_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single-event watchdog canary: daemon thread + Observer wiring is alive.

    Reduced from a multi-event scenario that flaked under Windows CI runner
    load. DEBUG-level instrumentation captured a trace showing
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
            # under load. If this still flakes, the cause is the Windows kernel
            # silently dropping events (see docs/internals/long-form-gotchas.md,
            # "Windows watchdog mystery") and the test should be deleted, not
            # widened further.
            assert _poll_until(lambda: markers.is_ignored(tmp_path / "build"), timeout_s=10.0), (
                "build/ was not marked ignored within 10s — likely a dropped "
                "ReadDirectoryChangesW event (Windows can drop these under load)."
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

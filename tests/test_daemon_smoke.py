import sys
import threading
import time

import pytest

from dropboxignore import daemon, markers

pytestmark = pytest.mark.windows_only

if sys.platform != "win32":
    pytest.skip(
        "Daemon smoke test exercises real NTFS ADS; Windows-only",
        allow_module_level=True,
    )


def _poll_until(fn, timeout_s: float = 2.0, interval_s: float = 0.05) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(interval_s)
    return False


def test_daemon_reacts_to_dropboxignore_and_directory_creation(tmp_path, monkeypatch):
    # Redirect roots.discover() to our fake dropbox root.
    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [tmp_path])
    # Ensure the singleton check reads a fresh state path under tmp_path.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))

    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)
    t.start()
    try:
        # Create .dropboxignore and matching directory; expect marker set.
        (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
        (tmp_path / "build").mkdir()

        assert _poll_until(lambda: markers.is_ignored(tmp_path / "build")), \
            "build/ was not marked ignored within 2s"

        # Append a negation; create the child. Under the new semantics
        # (v0.2 item 10 resolution) the negation is detected as conflicted
        # at rule-load time and dropped from the active rule set — so the
        # child stays marked, just like its parent. The daemon log should
        # carry the conflict WARNING.
        (tmp_path / ".dropboxignore").write_text(
            "build/\n!build/keep/\n", encoding="utf-8"
        )
        (tmp_path / "build" / "keep").mkdir()

        assert _poll_until(
            lambda: markers.is_ignored(tmp_path / "build" / "keep"),
            timeout_s=3.0,
        ), "build/keep/ should stay marked — the negation is dropped"

        # Verify the WARNING made it into daemon.log. The log lives under
        # the test's LOCALAPPDATA redirect.
        log_path = tmp_path / "LocalAppData" / "dropboxignore" / "daemon.log"
        assert _poll_until(
            lambda: log_path.exists()
            and "!build/keep/" in log_path.read_text()
            and "masked by" in log_path.read_text(),
            timeout_s=3.0,
        ), "daemon.log should contain the conflict WARNING"
    finally:
        stop.set()
        t.join(timeout=5.0)

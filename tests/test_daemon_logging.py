"""Tests for the `_configured_logging` context manager in daemon.py."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import pytest

from dbxignore import daemon


@pytest.fixture
def isolated_pkg_logger():
    """Install a known sentinel handler/propagate/level on the dbxignore
    package logger so tests can assert the context manager restored them on
    exit. Snapshots any pre-existing state and restores it after."""
    pkg_logger = logging.getLogger("dbxignore")
    saved_handlers = list(pkg_logger.handlers)
    saved_propagate = pkg_logger.propagate
    saved_level = pkg_logger.level
    sentinel = logging.NullHandler()
    pkg_logger.handlers = [sentinel]
    pkg_logger.propagate = True
    pkg_logger.setLevel(logging.WARNING)
    try:
        yield sentinel
    finally:
        for h in list(pkg_logger.handlers):
            pkg_logger.removeHandler(h)
            if h is not sentinel:
                h.close()
        for h in saved_handlers:
            pkg_logger.addHandler(h)
        pkg_logger.propagate = saved_propagate
        pkg_logger.level = saved_level


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    if sys.platform == "win32":
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
        return tmp_path / "LocalAppData" / "dbxignore"
    if sys.platform == "darwin":
        # daemon._log_dir() reads from state.user_log_dir() which on darwin
        # uses Path.home() / "Library" / "Logs" / "dbxignore". Redirect HOME
        # so the log lands in tmp rather than the runner's ~/Library/Logs/.
        monkeypatch.setenv("HOME", str(tmp_path))
        return tmp_path / "Library" / "Logs" / "dbxignore"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path / "state" / "dbxignore"


def test_configured_logging_installs_rotating_handler(
    isolated_pkg_logger, log_dir, monkeypatch
):
    monkeypatch.delenv("DBXIGNORE_LOG_LEVEL", raising=False)
    pkg_logger = logging.getLogger("dbxignore")

    with daemon._configured_logging():
        handlers = pkg_logger.handlers

        rotating = [
            h for h in handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(rotating) == 1
        assert Path(rotating[0].baseFilename) == log_dir / "daemon.log"

        stderr_handlers = [
            h for h in handlers
            if type(h) is logging.StreamHandler and h.stream is sys.stderr
        ]
        if sys.platform.startswith("linux"):
            assert len(stderr_handlers) == 1, (
                "Linux should dual-sink to stderr so systemd-journald captures "
                "the same records the rotating file holds"
            )
            assert len(handlers) == 2
        else:
            assert stderr_handlers == []
            assert len(handlers) == 1

        assert pkg_logger.propagate is False
        assert pkg_logger.level == logging.INFO


def test_configured_logging_does_not_close_stderr_on_exit(
    isolated_pkg_logger, log_dir
):
    """The Linux dual-sink attaches a StreamHandler wrapping sys.stderr.
    The cleanup loop calls .close() on every handler; that must not close
    sys.stderr itself, or subsequent test output (and real daemon restart)
    would write into a dead fd."""
    if not sys.platform.startswith("linux"):
        pytest.skip("stderr dual-sink only installed on Linux")

    with daemon._configured_logging():
        pass

    assert not sys.stderr.closed


def test_configured_logging_respects_log_level_env(
    isolated_pkg_logger, log_dir, monkeypatch
):
    monkeypatch.setenv("DBXIGNORE_LOG_LEVEL", "DEBUG")
    pkg_logger = logging.getLogger("dbxignore")

    with daemon._configured_logging():
        assert pkg_logger.level == logging.DEBUG


def test_configured_logging_accepts_lowercase_level(
    isolated_pkg_logger, log_dir, monkeypatch
):
    """The env var is case-insensitive: `debug` should resolve to logging.DEBUG."""
    monkeypatch.setenv("DBXIGNORE_LOG_LEVEL", "debug")
    pkg_logger = logging.getLogger("dbxignore")

    with daemon._configured_logging():
        assert pkg_logger.level == logging.DEBUG


def test_configured_logging_warns_and_falls_back_on_unknown_level(
    isolated_pkg_logger, log_dir, monkeypatch
):
    """A typo'd level (e.g. `DEUG`) silently degraded to INFO before the fix —
    user lost the level they wanted, no signal that anything went wrong.
    Now the daemon falls back to INFO AND emits a WARNING naming the bad
    value, so the user sees the misconfiguration in daemon.log."""
    monkeypatch.setenv("DBXIGNORE_LOG_LEVEL", "DEUG")
    pkg_logger = logging.getLogger("dbxignore")

    # The WARNING must surface through the daemon's configured handlers, not
    # be lost during early startup. Sniff the file handler's RotatingFileHandler
    # output by reading daemon.log after the context exits.
    with daemon._configured_logging():
        assert pkg_logger.level == logging.INFO  # fell back

    # Read what was written. log_dir fixture redirects %APPDATA%/HOME/etc.
    # to tmp_path, so daemon.log lands inside the test's tmp tree.
    daemon_log = log_dir / "daemon.log"
    assert daemon_log.exists(), f"expected log at {daemon_log}"
    log_text = daemon_log.read_text(encoding="utf-8")
    assert "DBXIGNORE_LOG_LEVEL='DEUG'" in log_text, log_text
    assert "not a recognized logging level" in log_text, log_text
    assert "falling back to INFO" in log_text, log_text


def test_configured_logging_unset_env_is_silent(
    isolated_pkg_logger, log_dir, monkeypatch
):
    """When DBXIGNORE_LOG_LEVEL is unset, no warning fires — the default-INFO
    path is the common case and should not emit a misconfiguration warning."""
    monkeypatch.delenv("DBXIGNORE_LOG_LEVEL", raising=False)

    with daemon._configured_logging():
        pass

    daemon_log = log_dir / "daemon.log"
    if daemon_log.exists():  # may be empty / not created if no records flowed
        log_text = daemon_log.read_text(encoding="utf-8")
        assert "not a recognized logging level" not in log_text, log_text


def test_configured_logging_empty_string_env_is_silent(
    isolated_pkg_logger, log_dir, monkeypatch
):
    """DBXIGNORE_LOG_LEVEL="" is shell-quirk-equivalent to unset — fall back
    to INFO without a warning. Mirrors the DBXIGNORE_ROOT="" → fall back to
    info.json discovery treatment in roots.discover()."""
    monkeypatch.setenv("DBXIGNORE_LOG_LEVEL", "")
    pkg_logger = logging.getLogger("dbxignore")

    with daemon._configured_logging():
        assert pkg_logger.level == logging.INFO

    daemon_log = log_dir / "daemon.log"
    if daemon_log.exists():
        log_text = daemon_log.read_text(encoding="utf-8")
        assert "not a recognized logging level" not in log_text, log_text


def test_configured_logging_restores_logger_state_on_exit(
    isolated_pkg_logger, log_dir
):
    sentinel = isolated_pkg_logger
    pkg_logger = logging.getLogger("dbxignore")

    with daemon._configured_logging():
        pass

    assert pkg_logger.handlers == [sentinel]
    assert pkg_logger.propagate is True
    assert pkg_logger.level == logging.WARNING


def test_configured_logging_restores_on_exception(isolated_pkg_logger, log_dir):
    sentinel = isolated_pkg_logger
    pkg_logger = logging.getLogger("dbxignore")

    with pytest.raises(RuntimeError, match="boom"), daemon._configured_logging():
        raise RuntimeError("boom")

    assert pkg_logger.handlers == [sentinel]
    assert pkg_logger.propagate is True
    assert pkg_logger.level == logging.WARNING


def test_configured_logging_closes_installed_handler_on_exit(
    isolated_pkg_logger, log_dir
):
    """Rotating file handler must be closed on exit so Windows releases the log file."""
    installed: list[logging.Handler] = []
    pkg_logger = logging.getLogger("dbxignore")

    with daemon._configured_logging():
        installed.extend(
            h for h in pkg_logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        )
        assert installed, "expected a RotatingFileHandler inside the context"

    for h in installed:
        assert h.stream is None or h.stream.closed, (
            f"handler {h!r} was not closed on context exit"
        )

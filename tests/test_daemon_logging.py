"""Tests for the `_configured_logging` context manager in daemon.py."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

import pytest

from dropboxignore import daemon


@pytest.fixture
def isolated_pkg_logger():
    """Install a known sentinel handler/propagate/level on the dropboxignore
    package logger so tests can assert the context manager restored them on
    exit. Snapshots any pre-existing state and restores it after."""
    pkg_logger = logging.getLogger("dropboxignore")
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
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    return tmp_path / "LocalAppData" / "dropboxignore"


def test_configured_logging_installs_rotating_handler(
    isolated_pkg_logger, log_dir, monkeypatch
):
    monkeypatch.delenv("DROPBOXIGNORE_LOG_LEVEL", raising=False)
    pkg_logger = logging.getLogger("dropboxignore")

    with daemon._configured_logging():
        handlers = pkg_logger.handlers
        assert len(handlers) == 1
        assert isinstance(handlers[0], logging.handlers.RotatingFileHandler)
        assert Path(handlers[0].baseFilename) == log_dir / "daemon.log"
        assert pkg_logger.propagate is False
        assert pkg_logger.level == logging.INFO


def test_configured_logging_respects_log_level_env(
    isolated_pkg_logger, log_dir, monkeypatch
):
    monkeypatch.setenv("DROPBOXIGNORE_LOG_LEVEL", "DEBUG")
    pkg_logger = logging.getLogger("dropboxignore")

    with daemon._configured_logging():
        assert pkg_logger.level == logging.DEBUG


def test_configured_logging_restores_logger_state_on_exit(
    isolated_pkg_logger, log_dir
):
    sentinel = isolated_pkg_logger
    pkg_logger = logging.getLogger("dropboxignore")

    with daemon._configured_logging():
        pass

    assert pkg_logger.handlers == [sentinel]
    assert pkg_logger.propagate is True
    assert pkg_logger.level == logging.WARNING


def test_configured_logging_restores_on_exception(isolated_pkg_logger, log_dir):
    sentinel = isolated_pkg_logger
    pkg_logger = logging.getLogger("dropboxignore")

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
    pkg_logger = logging.getLogger("dropboxignore")

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

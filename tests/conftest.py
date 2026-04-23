"""Shared fixtures and helpers for the dbxignore test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from dbxignore import cli, reconcile


class FakeMarkers:
    """In-memory stand-in for the ``markers`` module."""

    def __init__(self) -> None:
        self._ignored: set[Path] = set()
        self.set_calls: list[Path] = []
        self.clear_calls: list[Path] = []

    def is_ignored(self, path: Path) -> bool:
        return path.resolve() in self._ignored

    def set_ignored(self, path: Path) -> None:
        p = path.resolve()
        self._ignored.add(p)
        self.set_calls.append(p)

    def clear_ignored(self, path: Path) -> None:
        p = path.resolve()
        self._ignored.discard(p)
        self.clear_calls.append(p)


@pytest.fixture
def fake_markers(monkeypatch):
    """Replace ``markers`` in both ``reconcile`` and ``cli`` with a shared FakeMarkers."""
    fake = FakeMarkers()
    monkeypatch.setattr(reconcile, "markers", fake)
    monkeypatch.setattr(cli, "markers", fake)
    return fake


@pytest.fixture
def write_file():
    """Write a file, creating parent dirs; returns a callable ``(path, content="")``."""
    def _write(path: Path, content: str = "") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path
    return _write

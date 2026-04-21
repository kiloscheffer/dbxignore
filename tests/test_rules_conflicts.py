"""Unit tests for the gitignore conflict-detection helpers in rules.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from dropboxignore.rules import Conflict, literal_prefix


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("build/keep/", "build/keep/"),
        ("build/keep", "build/"),        # no trailing slash → cut at last /
        ("src/**/test.py", "src/"),
        ("foo*/bar/", None),             # glob in first segment
        ("**/cache/", None),             # starts with glob
        ("/anchored/path/", "anchored/path/"),   # leading-/ normalized
        ("", None),                      # empty
        ("plain", "plain"),              # single segment, no slash, no glob
        ("a/b/c/d/", "a/b/c/d/"),
        ("?single-char-glob", None),
        ("[abc]/charset", None),
    ],
)
def test_literal_prefix(pattern: str, expected: str | None) -> None:
    assert literal_prefix(pattern) == expected


def test_conflict_dataclass_shape() -> None:
    c = Conflict(
        dropped_source=Path("/root/.dropboxignore"),
        dropped_line=3,
        dropped_pattern="!build/keep/",
        masking_source=Path("/root/.dropboxignore"),
        masking_line=1,
        masking_pattern="build/",
    )
    assert c.dropped_line == 3
    assert c.masking_pattern == "build/"
    # Frozen — assignment should raise.
    with pytest.raises((AttributeError, TypeError)):
        c.dropped_line = 99  # type: ignore[misc]

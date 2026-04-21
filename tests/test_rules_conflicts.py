"""Unit tests for the gitignore conflict-detection helpers in rules.py."""

from __future__ import annotations

import pytest

from dropboxignore.rules import literal_prefix


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

"""Unit tests for the gitignore conflict-detection helpers in rules.py.

These tests use a `_FakePattern` shim that models trailing-slash
directory-rule semantics and exact non-directory matches. It does NOT
model gitwildmatch features like `build/*` (children-only). For coverage
of the children-only patterns and the post-PR-#108 strict-ancestor /
last-match-wins semantics, see the higher-fidelity tests against real
pathspec in `tests/test_rules_reload_explain.py` (the
`test_rulecache_*conflict*` block).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from dbxignore.rules_conflicts import Conflict, _detect_conflicts, literal_prefix


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


# ---------------------------------------------------------------------------
# _detect_conflicts tests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeEntry:
    """Test shim for the sequence entries _detect_conflicts consumes."""

    source: Path
    line: int           # 1-based
    raw: str            # source line text
    ancestor_dir: Path  # directory the pattern is scoped to
    pattern: Any        # object with .include and .match_file


class _FakePattern:
    def __init__(self, raw: str) -> None:
        # Strip leading `!` to decide include bit; strip the `/` anchor for
        # matching via simple prefix check (sufficient for these unit tests;
        # real pathspec handles the full gitignore grammar).
        self.include = not raw.startswith("!")
        self._target = raw.lstrip("!").lstrip("/")

    def match_file(self, path: str) -> bool | None:
        # Directory-only rules (trailing /) match the dir itself AND
        # descendants (prefix semantics); non-directory rules match exactly.
        tgt = self._target
        if tgt.endswith("/"):
            # For simplicity: path matches if it equals tgt or starts with tgt.
            if path == tgt or path.startswith(tgt):
                return True
        else:
            if path == tgt or path.rstrip("/") == tgt:
                return True
        return None


def _entry(source: str, line: int, raw: str, ancestor_dir: str) -> _FakeEntry:
    return _FakeEntry(
        source=Path(source),
        line=line,
        raw=raw,
        ancestor_dir=Path(ancestor_dir),
        pattern=_FakePattern(raw),
    )


def test_detect_conflict_flags_negation_under_ignored_ancestor(tmp_path: Path) -> None:
    root = tmp_path
    sequence = [
        _entry(str(root / ".dropboxignore"), 1, "build/", str(root)),
        _entry(str(root / ".dropboxignore"), 3, "!build/keep/", str(root)),
    ]
    conflicts = _detect_conflicts(sequence, root=root)

    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.dropped_pattern == "!build/keep/"
    assert c.masking_pattern == "build/"
    assert c.dropped_line == 3
    assert c.masking_line == 1


def test_detect_no_conflict_for_file_pattern_negation(tmp_path: Path) -> None:
    """*.log + !important.log does NOT flag — no directory is ignored."""
    root = tmp_path
    sequence = [
        _entry(str(root / ".dropboxignore"), 1, "*.log", str(root)),
        _entry(str(root / ".dropboxignore"), 2, "!important.log", str(root)),
    ]
    conflicts = _detect_conflicts(sequence, root=root)
    assert conflicts == []


def test_detect_no_conflict_for_unrelated_paths(tmp_path: Path) -> None:
    root = tmp_path
    sequence = [
        _entry(str(root / ".dropboxignore"), 1, "build/", str(root)),
        _entry(str(root / ".dropboxignore"), 2, "!unrelated/path/", str(root)),
    ]
    assert _detect_conflicts(sequence, root=root) == []


def test_detect_no_conflict_when_negation_precedes_include(tmp_path: Path) -> None:
    """Order matters: a negation can only be masked by a rule that appears
    before it in evaluation order."""
    root = tmp_path
    sequence = [
        _entry(str(root / ".dropboxignore"), 1, "!build/keep/", str(root)),
        _entry(str(root / ".dropboxignore"), 2, "build/", str(root)),
    ]
    assert _detect_conflicts(sequence, root=root) == []


def test_detect_cross_file_conflict(tmp_path: Path) -> None:
    """Root .dropboxignore ignores build/; nested .dropboxignore inside
    build/ tries to re-include keep/. Same conflict as intra-file, just
    with sources in two different files."""
    root = tmp_path
    root_file = root / ".dropboxignore"
    nested_file = root / "build" / ".dropboxignore"
    sequence = [
        _entry(str(root_file), 1, "build/", str(root)),
        _entry(str(nested_file), 1, "!keep/", str(root / "build")),
    ]
    conflicts = _detect_conflicts(sequence, root=root)

    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.dropped_source == nested_file
    assert c.masking_source == root_file


def test_detect_skips_glob_prefix_negation(tmp_path: Path) -> None:
    """Negations with no literal prefix (**/foo/bar/ starts with glob) are
    intentionally skipped — documented limitation."""
    root = tmp_path
    sequence = [
        _entry(str(root / ".dropboxignore"), 1, "**/foo/", str(root)),
        _entry(str(root / ".dropboxignore"), 2, "!**/foo/bar/", str(root)),
    ]
    assert _detect_conflicts(sequence, root=root) == []


def test_detect_multiple_independent_conflicts(tmp_path: Path) -> None:
    root = tmp_path
    sequence = [
        _entry(str(root / ".dropboxignore"), 1, "build/", str(root)),
        _entry(str(root / ".dropboxignore"), 2, "node_modules/", str(root)),
        _entry(str(root / ".dropboxignore"), 4, "!build/keep/", str(root)),
        _entry(str(root / ".dropboxignore"), 5, "!node_modules/patched/", str(root)),
    ]
    conflicts = _detect_conflicts(sequence, root=root)
    patterns = {c.dropped_pattern for c in conflicts}
    assert patterns == {"!build/keep/", "!node_modules/patched/"}


def test_detect_later_include_does_not_affect_earlier_negation(tmp_path: Path) -> None:
    """Sandwich (include → negation → include): the trailing include is invisible
    to the detector because it iterates ``sequence[:i]``. Pins the slice
    invariant so a refactor widening it to the full sequence would fail."""
    root = tmp_path
    sequence = [
        _entry(str(root / ".dropboxignore"), 1, "build/", str(root)),
        _entry(str(root / ".dropboxignore"), 2, "!build/keep/", str(root)),
        _entry(str(root / ".dropboxignore"), 3, "src/", str(root)),
    ]
    conflicts = _detect_conflicts(sequence, root=root)

    assert len(conflicts) == 1
    assert conflicts[0].dropped_pattern == "!build/keep/"
    assert conflicts[0].masking_pattern == "build/"

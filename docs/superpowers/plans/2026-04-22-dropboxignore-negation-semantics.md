# Negation-semantics conflict detection — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect `.dropboxignore` negation rules whose target lives under a directory matched by an earlier include rule; drop those negations from the active rule set and surface them through the daemon log, `dropboxignore status`, and `dropboxignore explain`.

**Architecture:** Three small pure functions added to `rules.py` (`literal_prefix`, `_ancestors_of`, `_detect_conflicts`), a `Conflict` dataclass, and two new `RuleCache` attributes (`_dropped` set, `_conflicts` list). `match()` filters against `_dropped`; `explain()` surfaces dropped entries with a new `is_dropped` flag on `Match`. CLI `status` gains a conflicts section; CLI `explain` annotates dropped rows.

**Tech Stack:** Python 3.11+, pathspec 1.0.4 (`GitIgnoreSpecPattern`), pytest, click. Follow existing `cchk.toml` commit/branch rules.

**Spec:** [`../specs/2026-04-21-dropboxignore-negation-semantics.md`](../specs/2026-04-21-dropboxignore-negation-semantics.md)

---

## File map

**Created:**
- `tests/test_rules_conflicts.py` — pure-function unit tests for `literal_prefix` and `_detect_conflicts`.

**Modified:**
- `src/dropboxignore/rules.py` — add `literal_prefix`, `_ancestors_of`, `_detect_conflicts`; add `Conflict` dataclass; extend `Match` with `is_dropped`; extend `RuleCache` with `_dropped`, `_conflicts`, `_recompute_conflicts`; filter `match()` and enrich `explain()`.
- `src/dropboxignore/cli.py` — `status` gains a conflicts section; `explain` annotates dropped rows.
- `tests/test_rules_reload_explain.py` — integration tests for the new `RuleCache` surfaces.
- `tests/test_cli_status_list_explain.py` — CLI tests for the new `status` section and `explain` annotation.
- `tests/test_daemon_smoke.py` — flip the Windows smoke's negation-phase assertions (deliberate behavior change).
- `tests/test_daemon_smoke_linux.py` — optional: add a negation sub-test for cross-platform parity.
- `README.md` — new "Negations and Dropbox's ignore inheritance" subsection.
- `CLAUDE.md` — new gotcha bullet for `RuleCache._dropped` / `_conflicts`.
- `docs/superpowers/plans/2026-04-21-dropboxignore-v0.2-linux-followups.md` — item 10 RESOLVED.

---

## Task 1: `literal_prefix` helper + unit tests

**Files:**
- Create: `tests/test_rules_conflicts.py`
- Modify: `src/dropboxignore/rules.py`

- [ ] **Step 1: Write the failing test.**

Create `tests/test_rules_conflicts.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails.**

```bash
uv run pytest tests/test_rules_conflicts.py -v
```

Expected: `ImportError: cannot import name 'literal_prefix' from 'dropboxignore.rules'`.

- [ ] **Step 3: Implement `literal_prefix` in `src/dropboxignore/rules.py`.**

Add at module scope (after imports, before the class definitions):

```python
def literal_prefix(pattern: str) -> str | None:
    """Return the leading literal path segments of a gitignore pattern.

    The returned value is the prefix up to (and including) the last ``/``
    before the first glob metacharacter (``*``, ``?``, ``[``), or the whole
    pattern if it contains no glob. A leading ``/`` anchor is stripped.

    Returns ``None`` when there is no literal anchor — e.g. patterns that
    begin with a glob (``**/cache/``), or that place a glob inside the first
    segment (``foo*/bar/``). The detection layer uses ``None`` to skip
    conflict analysis for that pattern (documented limitation).

    Input is the path portion of a gitignore pattern. Callers should pass
    the raw line with any leading ``!`` already stripped — pathspec
    already tracks include vs. negation via ``pattern.include``.
    """
    if not pattern:
        return None
    p = pattern.lstrip("/")
    if not p:
        return None
    boundary = next(
        (i for i, c in enumerate(p) if c in "*?["),
        len(p),
    )
    if boundary < len(p):
        last_sep = p[:boundary].rfind("/")
        if last_sep == -1:
            return None
        return p[:last_sep + 1]
    # No glob present: return whole pattern. If it ends in `/`, we keep the
    # trailing slash; otherwise we cut at the last `/` so the prefix is a
    # directory-shaped string (the detector walks directory ancestors).
    if "/" not in p:
        return p
    if p.endswith("/"):
        return p
    last_sep = p.rfind("/")
    return p[:last_sep + 1]
```

- [ ] **Step 4: Run the test to verify it passes.**

```bash
uv run pytest tests/test_rules_conflicts.py -v
```

Expected: all 11 parametrized cases pass.

- [ ] **Step 5: Commit.**

```bash
git add tests/test_rules_conflicts.py src/dropboxignore/rules.py
git commit -m "feat(rules): add literal_prefix helper for conflict detection"
```

---

## Task 2: `Conflict` dataclass

**Files:**
- Modify: `src/dropboxignore/rules.py`
- Modify: `tests/test_rules_conflicts.py`

- [ ] **Step 1: Write the failing test.**

Append to `tests/test_rules_conflicts.py`:

```python
from pathlib import Path

from dropboxignore.rules import Conflict


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
```

- [ ] **Step 2: Run the test to verify it fails.**

```bash
uv run pytest tests/test_rules_conflicts.py::test_conflict_dataclass_shape -v
```

Expected: `ImportError: cannot import name 'Conflict'`.

- [ ] **Step 3: Add the `Conflict` dataclass.**

In `src/dropboxignore/rules.py`, after the existing `Match` dataclass:

```python
@dataclass(frozen=True)
class Conflict:
    """A dropped negation rule and the earlier include rule that masks it.

    Emitted by ``RuleCache._recompute_conflicts`` when a negation's literal
    prefix lives under a directory matched by an earlier include rule —
    Dropbox's ignored-folder inheritance makes such negations inert. Used
    for the WARNING log, ``dropboxignore status`` reporting, and the
    ``[dropped]`` annotation in ``explain()`` output.
    """

    dropped_source: Path      # the .dropboxignore file containing the negation
    dropped_line: int         # 1-based source line of the negation
    dropped_pattern: str      # raw pattern text (e.g. "!build/keep/")
    masking_source: Path      # the .dropboxignore file containing the include
    masking_line: int         # 1-based source line of the masking include
    masking_pattern: str      # raw pattern text (e.g. "build/")
```

- [ ] **Step 4: Run the test to verify it passes.**

```bash
uv run pytest tests/test_rules_conflicts.py::test_conflict_dataclass_shape -v
```

Expected: PASS.

- [ ] **Step 5: Commit.**

```bash
git add src/dropboxignore/rules.py tests/test_rules_conflicts.py
git commit -m "feat(rules): add Conflict dataclass for dropped-negation diagnostics"
```

---

## Task 3: `_detect_conflicts` function + unit tests

**Files:**
- Modify: `src/dropboxignore/rules.py`
- Modify: `tests/test_rules_conflicts.py`

- [ ] **Step 1: Write the failing tests.**

Append to `tests/test_rules_conflicts.py`:

```python
from dataclasses import dataclass
from typing import Any

from dropboxignore.rules import _detect_conflicts


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
```

- [ ] **Step 2: Run the tests to verify they fail.**

```bash
uv run pytest tests/test_rules_conflicts.py -v
```

Expected: 7 failures with `ImportError: cannot import name '_detect_conflicts'`.

- [ ] **Step 3: Implement `_detect_conflicts` and supporting helpers.**

Add to `src/dropboxignore/rules.py`, after `literal_prefix`:

```python
def _ancestors_of(prefix: str, ancestor_dir: Path, root: Path) -> list[Path]:
    """Yield absolute ancestor directory paths for a negation's literal prefix.

    The negation's literal prefix is relative to its own ``.dropboxignore``
    file's directory (``ancestor_dir``). We produce absolute directory paths
    starting from the prefix itself (if it's a directory shape) and walking
    up to ``root``, inclusive.

    Example: prefix=``build/keep/``, ancestor_dir=``/root``, root=``/root``
    yields ``[/root/build/keep, /root/build, /root]``.
    """
    # Resolve the prefix against its scoping directory and strip the trailing
    # slash so we can navigate via Path.parent.
    target = (ancestor_dir / prefix.rstrip("/")).resolve()
    results: list[Path] = []
    current = target
    while True:
        results.append(current)
        if current == root:
            break
        if not current.is_relative_to(root):
            # Target escapes the root (unusual; likely malformed rule). Stop.
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return results


def _detect_conflicts(
    sequence: list, *, root: Path
) -> list[Conflict]:
    """Static rule-conflict detection.

    Input ``sequence`` is a list of entries in evaluation order. Each entry
    must expose ``source`` (Path), ``line`` (int, 1-based), ``raw`` (str,
    the source-line text), ``ancestor_dir`` (Path, the scoping directory
    of the pattern), and ``pattern`` (a pathspec pattern with ``.include``
    and ``.match_file``).

    Returns one ``Conflict`` per negation entry whose literal prefix is
    matched-as-ignored by any earlier include rule in the sequence.
    Skips negations whose pattern has no extractable literal prefix
    (documented limitation for glob-prefix patterns).
    """
    conflicts: list[Conflict] = []
    for i, entry in enumerate(sequence):
        if entry.pattern.include:
            continue  # include rules are potential masks, not subjects
        # Strip the leading `!` before extracting the literal prefix.
        raw = entry.raw.lstrip()
        if raw.startswith("!"):
            raw = raw[1:]
        prefix = literal_prefix(raw)
        if prefix is None:
            continue
        ancestors = _ancestors_of(prefix, entry.ancestor_dir, root)

        masking = _find_masking_include(sequence[:i], ancestors)
        if masking is None:
            continue
        conflicts.append(Conflict(
            dropped_source=entry.source,
            dropped_line=entry.line,
            dropped_pattern=entry.raw.strip(),
            masking_source=masking.source,
            masking_line=masking.line,
            masking_pattern=masking.raw.strip(),
        ))
    return conflicts


def _find_masking_include(
    earlier_entries: list, ancestors: list[Path]
) -> object | None:
    """Return the first earlier include whose pattern matches any ancestor.

    The ancestor is expressed as a path relative to each include's
    ``ancestor_dir`` (directory-shaped, with trailing slash), so pathspec's
    directory-rule matching fires.
    """
    for earlier in earlier_entries:
        if not earlier.pattern.include:
            continue
        for anc in ancestors:
            try:
                rel = anc.relative_to(earlier.ancestor_dir).as_posix() + "/"
            except ValueError:
                # This ancestor isn't under the earlier rule's scope.
                continue
            if earlier.pattern.match_file(rel):
                return earlier
    return None
```

- [ ] **Step 4: Run the tests to verify they pass.**

```bash
uv run pytest tests/test_rules_conflicts.py -v
```

Expected: all parametrized + dataclass + 7 detection tests pass.

- [ ] **Step 5: Commit.**

```bash
git add src/dropboxignore/rules.py tests/test_rules_conflicts.py
git commit -m "feat(rules): add _detect_conflicts static analysis for negations"
```

---

## Task 4: `RuleCache._dropped` / `_conflicts` + `_recompute_conflicts`

**Files:**
- Modify: `src/dropboxignore/rules.py`
- Modify: `tests/test_rules_reload_explain.py`

- [ ] **Step 1: Write the failing test.**

Append to `tests/test_rules_reload_explain.py`:

```python
def test_rulecache_populates_conflicts_on_load(tmp_path):
    from dropboxignore.rules import RuleCache

    root = tmp_path
    (root / ".dropboxignore").write_text(
        "build/\n!build/keep/\n", encoding="utf-8"
    )
    cache = RuleCache()
    cache.load_root(root)

    conflicts = cache.conflicts()
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.dropped_pattern == "!build/keep/"
    assert c.masking_pattern == "build/"


def test_rulecache_clears_conflicts_on_reload_without_conflict(tmp_path):
    from dropboxignore.rules import RuleCache

    root = tmp_path
    ignore_file = root / ".dropboxignore"
    ignore_file.write_text("build/\n!build/keep/\n", encoding="utf-8")
    cache = RuleCache()
    cache.load_root(root)
    assert len(cache.conflicts()) == 1

    # Fix the rules: drop the negation.
    ignore_file.write_text("build/\n", encoding="utf-8")
    cache.reload_file(ignore_file)

    assert cache.conflicts() == []


def test_rulecache_conflicts_removed_when_file_removed(tmp_path):
    from dropboxignore.rules import RuleCache

    root = tmp_path
    ignore_file = root / ".dropboxignore"
    ignore_file.write_text("build/\n!build/keep/\n", encoding="utf-8")
    cache = RuleCache()
    cache.load_root(root)
    assert len(cache.conflicts()) == 1

    cache.remove_file(ignore_file)
    assert cache.conflicts() == []
```

- [ ] **Step 2: Run the tests to verify they fail.**

```bash
uv run pytest tests/test_rules_reload_explain.py -v -k rulecache_populates
```

Expected: `AttributeError: 'RuleCache' object has no attribute 'conflicts'`.

- [ ] **Step 3: Extend `RuleCache` with conflict state.**

In `src/dropboxignore/rules.py`:

1. Add a dataclass for the internal sequence entries near the other dataclasses:

```python
@dataclass(frozen=True)
class _SequenceEntry:
    """One rule in the flattened evaluation-order sequence used by
    conflict detection. Internal to RuleCache."""

    source: Path           # the .dropboxignore file this rule came from
    line: int              # 1-based source line number
    raw: str               # source-line text (without trailing newline)
    ancestor_dir: Path     # directory the pattern is scoped to
    pattern: object        # GitIgnoreSpecPattern; duck-typed (.include, .match_file)
```

2. In `RuleCache.__init__`, add the new attributes:

```python
    def __init__(self) -> None:
        self._rules: dict[Path, _LoadedRules] = {}
        self._roots: list[Path] = []
        self._lock = threading.RLock()
        # Detection state — recomputed on every mutation. Keyed by
        # (ignore_file_path, line_idx) so match()/explain() can filter
        # without rebuilding per call.
        self._dropped: set[tuple[Path, int]] = set()
        self._conflicts: list[Conflict] = []
```

3. Add `_recompute_conflicts` and a public `conflicts()` getter:

```python
    def conflicts(self) -> list[Conflict]:
        """Current conflicts across all loaded roots, in detection order."""
        with self._lock:
            return list(self._conflicts)

    def _recompute_conflicts(self) -> None:
        """Rebuild _dropped and _conflicts from the current _rules.

        Called after any mutation (load_root, reload_file, remove_file).
        Caller must hold self._lock.
        """
        self._dropped.clear()
        self._conflicts.clear()
        for root in self._roots:
            sequence = self._build_sequence(root)
            for c in _detect_conflicts(sequence, root=root):
                self._conflicts.append(c)
                self._dropped.add((c.dropped_source, c.dropped_line - 1))
                logger.warning(
                    "negation `%s` at %s:%d is masked by include `%s` at %s:%d "
                    "(Dropbox inherits ignored state from ancestor directories). "
                    "Dropping the negation from the active rule set. "
                    "See README §Gotchas.",
                    c.dropped_pattern, c.dropped_source, c.dropped_line,
                    c.masking_pattern, c.masking_source, c.masking_line,
                )

    def _build_sequence(self, root: Path) -> list[_SequenceEntry]:
        """Flatten all .dropboxignore rules under root into evaluation order.

        Shallower files first; within a file, source-line order.
        """
        files_under_root = sorted(
            (p for p in self._rules if p.is_relative_to(root)),
            key=lambda p: (len(p.parts), p.as_posix()),
        )
        sequence: list[_SequenceEntry] = []
        for ignore_file in files_under_root:
            loaded = self._rules[ignore_file]
            ancestor_dir = ignore_file.parent
            for line_idx, pattern in loaded.entries:
                raw = (
                    loaded.lines[line_idx]
                    if line_idx < len(loaded.lines) else ""
                )
                sequence.append(_SequenceEntry(
                    source=ignore_file,
                    line=line_idx + 1,
                    raw=raw,
                    ancestor_dir=ancestor_dir,
                    pattern=pattern,
                ))
        return sequence
```

4. Wire `_recompute_conflicts` into the mutating methods. Modify `load_root`, `reload_file`, `remove_file` to call it at the end of their lock-held block:

```python
    def load_root(self, root: Path) -> None:
        root = root.resolve()
        with self._lock:
            if root not in self._roots:
                self._roots.append(root)
            seen: set[Path] = set()
            for ignore_file in root.rglob(IGNORE_FILENAME):
                seen.add(ignore_file.resolve())
                self._load_if_changed(ignore_file)
            for stale in [
                p for p in self._rules
                if p not in seen and p.is_relative_to(root)
            ]:
                del self._rules[stale]
            self._recompute_conflicts()

    def reload_file(self, ignore_file: Path) -> None:
        with self._lock:
            self._rules.pop(ignore_file.resolve(), None)
            self._load_file(ignore_file)
            self._recompute_conflicts()

    def remove_file(self, ignore_file: Path) -> None:
        with self._lock:
            self._rules.pop(ignore_file.resolve(), None)
            self._recompute_conflicts()
```

- [ ] **Step 4: Run the tests to verify they pass.**

```bash
uv run pytest tests/test_rules_reload_explain.py -v -k rulecache
```

Expected: 3 new tests pass, plus all existing tests in the file still pass.

- [ ] **Step 5: Run the full suite to confirm no regressions.**

```bash
uv run pytest -q
```

Expected: all previously-passing tests still pass.

- [ ] **Step 6: Commit.**

```bash
git add src/dropboxignore/rules.py tests/test_rules_reload_explain.py
git commit -m "feat(rules): recompute conflicts on RuleCache mutations"
```

---

## Task 5: `match()` skips dropped entries

**Files:**
- Modify: `src/dropboxignore/rules.py`
- Modify: `tests/test_rules_reload_explain.py`

- [ ] **Step 1: Write the failing test.**

Append to `tests/test_rules_reload_explain.py`:

```python
def test_match_treats_dropped_negation_as_absent(tmp_path):
    """With `build/` + `!build/keep/`, the negation is dropped, so
    build/keep/ is matched via the include (gitignore semantics with the
    negation absent)."""
    from dropboxignore.rules import RuleCache

    root = tmp_path
    (root / ".dropboxignore").write_text(
        "build/\n!build/keep/\n", encoding="utf-8"
    )
    (root / "build").mkdir()
    (root / "build" / "keep").mkdir()
    cache = RuleCache()
    cache.load_root(root)

    assert cache.match(root / "build") is True
    # The negation is dropped — build/keep/ still matches the `build/` rule.
    assert cache.match(root / "build" / "keep") is True


def test_match_honors_non_conflicted_negation(tmp_path):
    """*.log + !important.log: the negation is NOT dropped (no ignored
    ancestor), so important.log is excluded and others are included."""
    from dropboxignore.rules import RuleCache

    root = tmp_path
    (root / ".dropboxignore").write_text(
        "*.log\n!important.log\n", encoding="utf-8"
    )
    (root / "important.log").touch()
    (root / "debug.log").touch()
    cache = RuleCache()
    cache.load_root(root)

    assert cache.conflicts() == []  # guard: no conflict here
    assert cache.match(root / "important.log") is False
    assert cache.match(root / "debug.log") is True
```

- [ ] **Step 2: Run the tests to verify they fail.**

```bash
uv run pytest tests/test_rules_reload_explain.py -v \
  -k "match_treats_dropped or match_honors_non_conflicted"
```

Expected: `test_match_treats_dropped_negation_as_absent` FAILS because the current `match()` still honors the negation and returns `False` for `build/keep`. The non-conflicted case should already pass (no change in behavior).

- [ ] **Step 3: Filter `_dropped` entries in `match()`.**

Modify the `match()` method in `src/dropboxignore/rules.py` (the inner loop):

```python
    def match(self, path: Path) -> bool:
        if not path.is_absolute():
            raise ValueError(f"match() requires an absolute path; got {path!r}")
        if path.name == IGNORE_FILENAME:
            return False
        root = find_containing(path, self._roots)
        if root is None:
            return False

        matched = False
        for ancestor, loaded in self._applicable(root, path):
            rel_str = self._rel_path_str(ancestor, path)
            ignore_file = ancestor / IGNORE_FILENAME
            for line_idx, pattern in loaded.entries:
                if (ignore_file, line_idx) in self._dropped:
                    continue
                if pattern.match_file(rel_str) is not None:
                    matched = bool(pattern.include)
        return matched
```

- [ ] **Step 4: Run the tests to verify they pass.**

```bash
uv run pytest tests/test_rules_reload_explain.py -v \
  -k "match_treats_dropped or match_honors_non_conflicted"
```

Expected: both tests pass.

- [ ] **Step 5: Run full suite.**

```bash
uv run pytest -q
```

Expected: all previously-passing tests still pass.

- [ ] **Step 6: Commit.**

```bash
git add src/dropboxignore/rules.py tests/test_rules_reload_explain.py
git commit -m "feat(rules): skip dropped negations in RuleCache.match"
```

---

## Task 6: Conflict WARNING log is covered by a test

**Files:**
- Modify: `tests/test_rules_reload_explain.py`

The WARNING was emitted in Task 4's `_recompute_conflicts`. This task adds the log-contract test that pins its shape.

- [ ] **Step 1: Write the failing test.**

Append to `tests/test_rules_reload_explain.py`:

```python
def test_recompute_logs_warning_per_conflict(tmp_path, caplog):
    import logging

    from dropboxignore.rules import RuleCache

    root = tmp_path
    (root / ".dropboxignore").write_text(
        "build/\n!build/keep/\n", encoding="utf-8"
    )
    cache = RuleCache()

    with caplog.at_level(logging.WARNING, logger="dropboxignore.rules"):
        cache.load_root(root)

    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and r.name == "dropboxignore.rules"
        and "negation" in r.message
    ]
    assert len(warnings) == 1
    msg = warnings[0].message
    assert "!build/keep/" in msg
    assert "build/" in msg
    assert "Dropping the negation" in msg
```

- [ ] **Step 2: Run the test to verify it passes.**

```bash
uv run pytest tests/test_rules_reload_explain.py::test_recompute_logs_warning_per_conflict -v
```

Expected: PASS (Task 4's implementation already emits the log — the test pins the contract going forward).

- [ ] **Step 3: Commit.**

```bash
git add tests/test_rules_reload_explain.py
git commit -m "test(rules): pin WARNING log contract for dropped negations"
```

---

## Task 7: `Match.is_dropped` + `explain()` surfaces dropped negations

**Files:**
- Modify: `src/dropboxignore/rules.py`
- Modify: `tests/test_rules_reload_explain.py`

- [ ] **Step 1: Write the failing test.**

Append to `tests/test_rules_reload_explain.py`:

```python
def test_explain_includes_dropped_negation_with_flag(tmp_path):
    from dropboxignore.rules import RuleCache

    root = tmp_path
    (root / ".dropboxignore").write_text(
        "build/\n!build/keep/\n", encoding="utf-8"
    )
    (root / "build").mkdir()
    (root / "build" / "keep").mkdir()
    cache = RuleCache()
    cache.load_root(root)

    results = cache.explain(root / "build" / "keep")
    by_pattern = {m.pattern.strip(): m for m in results}

    assert "build/" in by_pattern
    assert by_pattern["build/"].is_dropped is False

    assert "!build/keep/" in by_pattern
    assert by_pattern["!build/keep/"].is_dropped is True
    # Dropped matches still carry their source + line info so the CLI can
    # format "[dropped] ... (masked by ...)".
    assert by_pattern["!build/keep/"].line == 2
```

- [ ] **Step 2: Run the test to verify it fails.**

```bash
uv run pytest tests/test_rules_reload_explain.py::test_explain_includes_dropped_negation_with_flag -v
```

Expected: `TypeError: Match.__init__() got an unexpected keyword argument 'is_dropped'` or `AttributeError: 'Match' object has no attribute 'is_dropped'`.

- [ ] **Step 3: Extend `Match` and `explain()`.**

In `src/dropboxignore/rules.py`:

1. Add `is_dropped` to `Match`:

```python
@dataclass(frozen=True)
class Match:
    """A single matching rule for the ``explain`` diagnostic."""

    ignore_file: Path
    line: int
    pattern: str
    negation: bool
    is_dropped: bool = False
```

2. Update `explain()` to include dropped entries with the flag set:

```python
    def explain(self, path: Path) -> list[Match]:
        if not path.is_absolute():
            raise ValueError(f"explain() requires an absolute path; got {path!r}")
        if path.name == IGNORE_FILENAME:
            return []
        root = find_containing(path, self._roots)
        if root is None:
            return []

        results: list[Match] = []
        for ancestor, loaded in self._applicable(root, path):
            rel_str = self._rel_path_str(ancestor, path)
            ignore_file = ancestor / IGNORE_FILENAME
            for line_idx, pattern in loaded.entries:
                if pattern.match_file(rel_str) is None:
                    continue
                raw_line = (
                    loaded.lines[line_idx]
                    if line_idx < len(loaded.lines) else ""
                )
                results.append(Match(
                    ignore_file=ignore_file,
                    line=line_idx + 1,
                    pattern=raw_line,
                    negation=not bool(pattern.include),
                    is_dropped=(ignore_file, line_idx) in self._dropped,
                ))
        return results
```

- [ ] **Step 4: Run the test to verify it passes.**

```bash
uv run pytest tests/test_rules_reload_explain.py::test_explain_includes_dropped_negation_with_flag -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite.**

```bash
uv run pytest -q
```

Expected: all previously-passing tests still pass (existing `explain()` callers get `is_dropped=False` by default — backward-compatible).

- [ ] **Step 6: Commit.**

```bash
git add src/dropboxignore/rules.py tests/test_rules_reload_explain.py
git commit -m "feat(rules): flag dropped negations in RuleCache.explain output"
```

---

## Task 8: CLI `status` shows conflicts section

**Files:**
- Modify: `src/dropboxignore/cli.py`
- Modify: `tests/test_cli_status_list_explain.py`

- [ ] **Step 1: Read the current `status` command.**

Skim `src/dropboxignore/cli.py` to locate the `status` command (around line 80-ish based on earlier exploration). The existing `status` prints daemon pid, last sweep, and last error. Locate the point after the last-error block where the new section inserts.

- [ ] **Step 2: Write the failing test.**

Append to `tests/test_cli_status_list_explain.py`:

```python
def test_status_lists_rule_conflicts(tmp_path, monkeypatch):
    """`status` surfaces RuleCache conflicts alongside daemon pid / sweep info."""
    import click.testing

    from dropboxignore import cli, state

    root = tmp_path
    (root / ".dropboxignore").write_text(
        "build/\n!build/keep/\n", encoding="utf-8"
    )

    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])

    result = click.testing.CliRunner().invoke(cli.main, ["status"])
    assert result.exit_code == 0
    assert "rule conflicts (1):" in result.output
    assert "!build/keep/" in result.output
    assert "build/" in result.output
    assert "masked by" in result.output


def test_status_omits_conflicts_section_when_empty(tmp_path, monkeypatch):
    import click.testing

    from dropboxignore import cli, state

    root = tmp_path
    (root / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])

    result = click.testing.CliRunner().invoke(cli.main, ["status"])
    assert result.exit_code == 0
    assert "rule conflicts" not in result.output
```

- [ ] **Step 3: Run the tests to verify they fail.**

```bash
uv run pytest tests/test_cli_status_list_explain.py -v \
  -k "status_lists_rule_conflicts or status_omits_conflicts"
```

Expected: `test_status_lists_rule_conflicts` fails (no "rule conflicts" line in output).

- [ ] **Step 4: Extend the `status` command.**

In `src/dropboxignore/cli.py`, the `status` command builds a `RuleCache` to resolve roots; extend it to print conflicts. Locate the existing `status` function and add at the end of its body, after any existing output:

```python
    # Conflicts section — present only when RuleCache has any.
    cache = rules.RuleCache()
    for r in roots:
        cache.load_root(r)
    conflicts = cache.conflicts()
    if conflicts:
        click.echo(f"rule conflicts ({len(conflicts)}):")
        for c in conflicts:
            # Show source paths relative to their dropbox root where possible.
            dropped_loc = _format_ignore_file_loc(c.dropped_source, roots)
            masking_loc = _format_ignore_file_loc(c.masking_source, roots)
            click.echo(
                f"  {dropped_loc}:{c.dropped_line}  {c.dropped_pattern}  "
                f"masked by {masking_loc}:{c.masking_line}  {c.masking_pattern}"
            )
```

And add the helper (module scope):

```python
def _format_ignore_file_loc(path: Path, roots: list[Path]) -> str:
    """Return path relative to the nearest root (short display), or absolute.

    Drops the trailing ``/.dropboxignore`` filename when the parent is a
    root — users already know the filename; the distinguishing info is the
    directory.
    """
    for r in roots:
        try:
            rel = path.relative_to(r)
            return str(rel)
        except ValueError:
            continue
    return str(path)
```

Make sure `from dropboxignore import rules` (or equivalent) is imported at the top of cli.py — if it isn't already, add it.

- [ ] **Step 5: Run the tests to verify they pass.**

```bash
uv run pytest tests/test_cli_status_list_explain.py -v \
  -k "status_lists_rule_conflicts or status_omits_conflicts"
```

Expected: both tests pass.

- [ ] **Step 6: Run full suite.**

```bash
uv run pytest -q
```

Expected: all pass.

- [ ] **Step 7: Commit.**

```bash
git add src/dropboxignore/cli.py tests/test_cli_status_list_explain.py
git commit -m "feat(cli): surface RuleCache conflicts in status output"
```

---

## Task 9: CLI `explain` annotates `[dropped]` rows

**Files:**
- Modify: `src/dropboxignore/cli.py`
- Modify: `tests/test_cli_status_list_explain.py`

- [ ] **Step 1: Write the failing test.**

Append to `tests/test_cli_status_list_explain.py`:

```python
def test_explain_annotates_dropped_negations(tmp_path, monkeypatch):
    import click.testing

    from dropboxignore import cli

    root = tmp_path
    (root / ".dropboxignore").write_text(
        "build/\n!build/keep/\n", encoding="utf-8"
    )
    (root / "build").mkdir()
    (root / "build" / "keep").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])

    result = click.testing.CliRunner().invoke(
        cli.main, ["explain", str(root / "build" / "keep")],
    )
    assert result.exit_code == 0
    assert "build/" in result.output
    assert "[dropped]" in result.output
    assert "!build/keep/" in result.output
    assert "masked by" in result.output
```

- [ ] **Step 2: Run the test to verify it fails.**

```bash
uv run pytest tests/test_cli_status_list_explain.py::test_explain_annotates_dropped_negations -v
```

Expected: no `[dropped]` in output (explain prints matches but without the annotation).

- [ ] **Step 3: Extend the `explain` command.**

In `src/dropboxignore/cli.py`, locate the `explain` command. Its current loop over `cache.explain(path)` should be extended to annotate dropped matches and include a pointer to the masking rule. The masking info isn't on `Match` itself — it's on `Conflict`. Build a lookup on demand:

```python
@main.command()
@click.argument("path", type=click.Path(exists=True, resolve_path=True))
def explain(path: str) -> None:
    """Show which rules match a given path and how (including dropped negations)."""
    from dropboxignore import rules
    roots = _discover_roots()
    cache = rules.RuleCache()
    for r in roots:
        cache.load_root(r)

    path_obj = Path(path)
    matches = cache.explain(path_obj)

    # Build a lookup: (source, line) -> Conflict, so we can print the
    # "masked by ..." tail for dropped rows.
    conflicts_by_drop = {
        (c.dropped_source, c.dropped_line): c
        for c in cache.conflicts()
    }

    if not matches:
        click.echo("No rules match this path.")
        return

    for m in matches:
        prefix = "[dropped]  " if m.is_dropped else ""
        raw = m.pattern.strip()
        suffix = ""
        if m.is_dropped:
            c = conflicts_by_drop.get((m.ignore_file, m.line))
            if c is not None:
                masking_loc = _format_ignore_file_loc(c.masking_source, roots)
                suffix = f"  (masked by {masking_loc}:{c.masking_line})"
        loc = _format_ignore_file_loc(m.ignore_file, roots)
        click.echo(f"{loc}:{m.line}  {prefix}{raw}{suffix}")
```

If the `explain` command already has a different body, preserve its flow and graft the `[dropped]`-aware formatting into the match-printing loop.

- [ ] **Step 4: Run the test to verify it passes.**

```bash
uv run pytest tests/test_cli_status_list_explain.py::test_explain_annotates_dropped_negations -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite.**

```bash
uv run pytest -q
```

Expected: all pass.

- [ ] **Step 6: Commit.**

```bash
git add src/dropboxignore/cli.py tests/test_cli_status_list_explain.py
git commit -m "feat(cli): annotate dropped negations in explain output"
```

---

## Task 10: Flip Windows daemon smoke assertions

**Files:**
- Modify: `tests/test_daemon_smoke.py`

The existing Windows smoke asserts `not markers.is_ignored(build/keep/)` after a negation is introduced. Under the new design the negation is dropped, so `build/keep/` remains marked (either directly, or inherited). This task deliberately flips that assertion.

- [ ] **Step 1: Read the current assertion.**

```bash
sed -n '/negation/,$p' tests/test_daemon_smoke.py
```

Note the existing phase-2 assertion block (the `not markers.is_ignored(...)` poll).

- [ ] **Step 2: Flip the assertion.**

In `tests/test_daemon_smoke.py`, replace the phase-2 block with:

```python
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

        log_path = tmp_path / "LocalAppData" / "dropboxignore" / "daemon.log"
        assert _poll_until(
            lambda: log_path.exists()
            and "!build/keep/" in log_path.read_text()
            and "masked by" in log_path.read_text(),
            timeout_s=3.0,
        ), "daemon.log should contain the conflict WARNING"
```

Note: the exact `log_path` for the Windows smoke matches the existing fixture — preserve whatever path the test file already uses. The point is to assert the WARNING landed.

- [ ] **Step 3: Run the test.**

On a Linux box you can't run `windows_only` tests. Skip locally; CI's Windows leg is the authoritative check. But you can confirm the test still *parses*:

```bash
uv run pytest tests/test_daemon_smoke.py --collect-only -q
```

Expected: test collected without errors.

- [ ] **Step 4: Commit.**

```bash
git add tests/test_daemon_smoke.py
git commit -m "test(daemon): flip Windows smoke negation assertions for item 10"
```

---

## Task 11: Add Linux negation sub-test

**Files:**
- Modify: `tests/test_daemon_smoke_linux.py`

Adds cross-platform parity: under the new design, the Linux smoke can now exercise the negation case deterministically (because the conflicted negation is detected and dropped at load time, removing the race).

- [ ] **Step 1: Append a new test function.**

Add to `tests/test_daemon_smoke_linux.py`:

```python
def test_daemon_drops_conflicted_negation(tmp_path, monkeypatch):
    """Adding `!build/keep/` after `build/` triggers the conflict-detection
    layer: the negation is dropped from the active rule set at rule-load
    time. `build/keep/` stays marked (either directly or via inheritance),
    and daemon.log records the WARNING.

    This is the Linux counterpart to the Windows smoke's negation phase.
    """
    from dropboxignore import daemon, markers

    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [tmp_path])
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    log_path = tmp_path / "state" / "dropboxignore" / "daemon.log"

    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)
    t.start()
    try:
        assert _wait_for_daemon_watching(log_path)

        # Phase 1 — rule + directory → marker.
        (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
        (tmp_path / "build").mkdir()
        assert _poll_until(lambda: markers.is_ignored(tmp_path / "build"))

        # Phase 2 — add a conflicted negation; child must NOT un-ignore.
        (tmp_path / ".dropboxignore").write_text(
            "build/\n!build/keep/\n", encoding="utf-8"
        )
        (tmp_path / "build" / "keep").mkdir()
        assert _poll_until(
            lambda: markers.is_ignored(tmp_path / "build" / "keep"),
            timeout_s=3.0,
        ), "conflicted negation should not un-ignore build/keep/"

        assert _poll_until(
            lambda: "!build/keep/" in log_path.read_text()
            and "masked by" in log_path.read_text(),
            timeout_s=3.0,
        ), "daemon.log should contain the conflict WARNING"
    finally:
        stop.set()
        t.join(timeout=5.0)
```

- [ ] **Step 2: Run the new test several times to check for flakiness.**

```bash
for i in 1 2 3 4 5 6 7 8 9 10; do \
  uv run pytest tests/test_daemon_smoke_linux.py::test_daemon_drops_conflicted_negation -q | tail -1; \
done
```

Expected: 10/10 pass. The detection-drops-at-load-time approach removes the race that made the original negation test flaky.

- [ ] **Step 3: Commit.**

```bash
git add tests/test_daemon_smoke_linux.py
git commit -m "test(daemon): Linux negation sub-test for cross-platform parity"
```

---

## Task 12: README subsection on inheritance + negation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Locate the insertion point.**

The spec places the new content "under 'Behaviour' or as its own top-level." Look at the existing README structure and choose the most natural location.

```bash
grep -n '^##' README.md | head -10
```

- [ ] **Step 2: Insert the new subsection.**

Add the following content at the chosen location:

```markdown
### Negations and Dropbox's ignore inheritance

Dropbox marks files and folders as ignored using xattrs. When a folder carries the ignore marker, Dropbox does not sync that folder or anything inside it — children inherit the ignored state regardless of whether they individually carry the marker. This matters for gitignore-style negation rules in your `.dropboxignore`.

If you write a negation whose target lives under a directory ignored by an earlier rule — the canonical case is `build/` followed by `!build/keep/` — the negation cannot take effect. Dropbox will ignore `build/keep/` because `build/` is ignored, no matter what xattr we put on the child. dropboxignore detects this at the moment you save the `.dropboxignore`, logs a WARNING naming both rules, and drops the conflicted negation from the active rule set.

Negations that don't conflict with an ignored ancestor work normally. For example:

```
*.log
!important.log
```

Here nothing marks a parent directory as ignored (`*.log` matches files, not dirs), so the negation works — `important.log` gets synced, the other `.log` files don't.

**Limitation.** Detection uses static analysis on the rule's literal path prefix. Negations that begin with a glob (`!**/keep/`, `!*/cache/`) have no literal anchor to analyze and are accepted without conflict-check — if they land under an ignored ancestor at runtime, they silently fail to take effect. If you need guaranteed semantics, prefer negations with a literal prefix.
```

- [ ] **Step 3: Commit.**

```bash
git add README.md
git commit -m "docs(readme): document inheritance-vs-negation interaction"
```

---

## Task 13: CLAUDE.md gotcha

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Locate the Gotchas section.**

```bash
grep -n '^## Gotchas' CLAUDE.md
```

- [ ] **Step 2: Insert a new bullet in the Gotchas section.**

Add:

```markdown
- `RuleCache` runs a static conflict detector at every mutation (`load_root`, `reload_file`, `remove_file`). Negations whose literal path prefix lives under a directory matched by an earlier include rule are recorded in `_conflicts` and their `(source, line_idx)` tuple is added to `_dropped`. `match()` and reconcile ignore anything in `_dropped`; `explain()` still returns dropped matches but sets `Match.is_dropped=True` so CLI/log formatters can annotate them. Semantic reason: Dropbox's ignored-folder inheritance makes negations inert under ignored ancestors, so we don't pretend they're in effect.
```

Place it alongside the other rule-cache-related gotchas for locality.

- [ ] **Step 3: Commit.**

```bash
git add CLAUDE.md
git commit -m "docs(claude): note RuleCache conflict-detection invariant"
```

---

## Task 14: Follow-ups plan — mark item 10 RESOLVED

**Files:**
- Modify: `docs/superpowers/plans/2026-04-21-dropboxignore-v0.2-linux-followups.md`

- [ ] **Step 1: Replace the item 10 entry.**

Find the `## 10. Prune + negation leaves stale markers on children of ignored directories` section and replace its body with:

```markdown
## 10. Prune + negation leaves stale markers — **RESOLVED**

The prune optimization is correct under Dropbox's inheritance model (once a directory is marked, Dropbox skips all descendants). What we actually had was a semantic mismatch: `.dropboxignore` accepted gitignore-style negations whose targets live under ignored ancestors, but Dropbox's inheritance makes those negations inert regardless of xattr state.

**Fix:** detect such conflicts statically at rule-load time, drop the conflicted negation from the active rule set, and surface the conflict via the daemon log (WARNING), `dropboxignore status`, and `dropboxignore explain`. Prune stays. See the [negation-semantics design doc](../specs/2026-04-21-dropboxignore-negation-semantics.md) for the full rationale and algorithm.

Touched: `src/dropboxignore/rules.py`, `src/dropboxignore/cli.py`, `tests/test_rules_conflicts.py` (new), `tests/test_rules_reload_explain.py`, `tests/test_cli_status_list_explain.py`, `tests/test_daemon_smoke.py` (Windows assertions flipped), `tests/test_daemon_smoke_linux.py` (negation sub-test added), `README.md`, `CLAUDE.md`.
```

- [ ] **Step 2: Update the Status block at the bottom of the file.**

Remove item 10 from the "Remaining open" list. The remaining items after this PR should be just 6 and 8:

```markdown
Remaining open after v0.2 follow-ups:
- Item 6 — Retire legacy Linux state-path fallback (v0.4 branch).
- Item 8 — `uninstall --purge` state/log cleanup (design decision: broaden `--purge` vs add `--purge-state`). Empirically confirmed by item 4's VPS run.
```

- [ ] **Step 3: Commit.**

```bash
git add docs/superpowers/plans/2026-04-21-dropboxignore-v0.2-linux-followups.md
git commit -m "docs(followups): mark item 10 RESOLVED"
```

---

## Task 15: Full pre-flight, push, open PR

**Files:** none (wrap-up).

- [ ] **Step 1: Run ruff.**

```bash
uv run ruff check
```

Expected: `All checks passed!`.

If any line-length violations come from the implementation or test code, fix them — prefer splitting long log-format calls across multiple lines or extracting intermediate variables.

- [ ] **Step 2: Run the full test suite.**

```bash
uv run pytest -q
```

Expected: all passing. The new tests added: roughly `+11` for literal_prefix parametrize cases, `+1` for Conflict dataclass, `+7` for detection, `+3-4` for RuleCache integration, `+1` for the WARNING log contract, `+1` for explain-with-is_dropped, `+2` for CLI status, `+1` for CLI explain annotation, `+1` for Linux negation smoke. Total net additions ~28. The existing Windows smoke is modified (not additive).

- [ ] **Step 3: Pre-flight commit-check against the planned PR subject.**

```bash
printf '%s\n' "feat(rules): drop negations that conflict with inherited ignores" > /tmp/msg.txt
commit-check --message --no-banner /tmp/msg.txt
commit-check --branch --no-banner
```

Expected: both exit 0. Subject is 63 chars — under the 72-char cap from `cchk.toml`.

- [ ] **Step 4: Push the branch.**

```bash
git push -u origin feature/v0.2-followup-10-negation-semantics
```

- [ ] **Step 5: Open the PR.**

```bash
gh pr create --title "feat(rules): drop negations that conflict with inherited ignores" --body "$(cat <<'EOF'
## Summary

Resolves v0.2 follow-up item 10. `.dropboxignore` negation rules whose target lives under a directory already ignored by an earlier rule are now detected at rule-load time, dropped from the active rule set, and surfaced to the user via three channels: daemon log WARNING, `dropboxignore status`, and `dropboxignore explain`.

Design doc: [`docs/superpowers/specs/2026-04-21-dropboxignore-negation-semantics.md`](docs/superpowers/specs/2026-04-21-dropboxignore-negation-semantics.md).

## Why

Dropbox's [ignored-files documentation](https://help.dropbox.com/sync/ignored-files) confirms that when a folder is ignored, files and subfolders within it are automatically ignored too. That inheritance can't be overridden by setting xattrs on descendants — so `.dropboxignore` patterns like `build/\n!build/keep/\n` can't be translated faithfully to Dropbox's model. Rather than let users write semantically-broken rules and discover the failure via sync surprise hours later, we detect the mismatch at rule-load time.

## Behavior change

- **Windows smoke test** — the existing negation-phase assertion (expecting the child to be un-ignored) is deliberately flipped: the negation is now dropped, so the child stays marked. See the test file's updated comments.
- **`dropboxignore list` output** may grow for users with existing conflicted-negation rules, because paths that were previously inconsistently marked will now be consistently marked. **Dropbox's sync behavior does not change** — those paths were already being ignored via inheritance.

## Test plan
- [x] `uv run ruff check` clean
- [x] `uv run pytest` — all pass
- [x] Linux negation smoke runs 10/10 deterministically (no flake)
- [x] `commit-check --message --branch` passes
- [ ] CI Linux + Windows + check legs all green
- [ ] Manual: run \`dropboxignore status\` on a repo with a conflicted rule and confirm the new "rule conflicts" section formatting
- [ ] Manual: run \`dropboxignore explain\` on a path matched by a dropped negation and confirm the \`[dropped]\` annotation

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Monitor CI.**

```bash
gh pr checks
```

Expected: three checks (the `check` workflow for commit-check, plus Linux and Windows test legs) all green or pending.

# #93 path-taking ignore/unignore verbs — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two new CLI commands `dbxignore ignore <path>` and `dbxignore unignore <path>` that mutate `.dropboxignore` (append / remove a literal-path rule) and set / clear the corresponding marker. Unblocks #65 (Windows Explorer right-click integration), which wires the registry verb to `dbxignore.exe ignore --yes "%1"`.

**Architecture:** Three layers, mirroring the project's CLI / rules / markers split. `cli.py` gains two `@main.command()` blocks plus a `_select_rule_file` helper. `rules.py` gains three pure helpers — `format_literal_rule` (path → canonical rule string), `append_rule` (atomic append-iff-missing), `remove_rule` (atomic rstrip-equality remove). The marker layer is unchanged; verbs call `markers.set_ignored` / `markers.clear_ignored` directly because the rule-line is path-anchored 1:1 to the literal target. Order of operations is rule-first-then-marker to avoid a daemon-race where a marker without a rule would trigger a spurious clear in the OTHER debouncer's 500ms window.

**Spec:** `docs/superpowers/specs/2026-05-10-93-path-taking-ignore-verbs-design.md` — read it before starting; this plan operationalizes that design.

**Tech Stack:** Python 3.11+, `click` / `rich_click`, `pathspec`, `pytest` (with `CliRunner`), `uv` for env management. Existing fixtures: `FakeMarkers` + `fake_markers` + `write_file` in `tests/conftest.py`. Existing reference: `tests/test_cli_clear.py` (similar verb shape — path-arg, --yes, --dry-run, confirmation).

---

## File structure

**Create:**
- `tests/test_cli_ignore.py` — CLI integration tests for both verbs.

**Modify:**
- `src/dbxignore/rules.py` — three new helpers (`format_literal_rule`, `append_rule`, `remove_rule`).
- `src/dbxignore/cli.py` — `_select_rule_file` helper plus two new `@main.command()` blocks.
- `tests/test_rules.py` *(if it exists; otherwise tests of `format_literal_rule` go in a new module — see Task 1)*.
- `README.md` — `## Commands` table gains `ignore` / `unignore` rows; new §"Ad-hoc ignore" subsection if appropriate.
- `BACKLOG.md` — `Status: RESOLVED` marker on item #93; remove #93 from Open list; update #65's "Blocked by #93" text to a "Resolved by PR #N" cross-reference; bump count back from "Ten items" to "Nine items."
- `scripts/_phase_extended_cli.sh` — three new Phase 4.5 cases (Linux + macOS shared helper).
- `scripts/manual-test-windows.ps1` — same three cases mirrored for PowerShell 7+.

**Total estimated change:** ~140 LOC of code in `rules.py` + `cli.py`; ~250 LOC of tests; ~80 LOC of docs / manual-test scripts.

---

## Task 1: `format_literal_rule` helper

Pure function that turns a resolved target path + a rule-file path into a gitignore-anchored literal-path rule string. No I/O. TDD with multiple unit-test cases.

**Files:**
- Modify: `src/dbxignore/rules.py` (add new function near other module-level helpers, e.g. after `_canonical_cache_key` at line 57)
- Test: `tests/test_rules_format_literal_rule.py` (new module)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rules_format_literal_rule.py`:

```python
"""Unit tests for ``rules.format_literal_rule`` (item #93).

Pure-function tests — no fixtures, no tmp_path needed, no monkeypatching.
"""

from pathlib import Path

import pytest

from dbxignore.rules import format_literal_rule


def test_dir_target_gets_trailing_slash(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = tmp_path / "build"
    target.mkdir()
    assert format_literal_rule(target, rule_file) == "build/"


def test_file_target_no_trailing_slash(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = tmp_path / "notes.txt"
    target.touch()
    assert format_literal_rule(target, rule_file) == "notes.txt"


def test_multi_segment_relative_path(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = tmp_path / "proj" / "foo" / "bar"
    target.mkdir(parents=True)
    assert format_literal_rule(target, rule_file) == "proj/foo/bar/"


def test_meta_char_escaping_in_segment(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = tmp_path / "foo*bar"
    target.mkdir()
    assert format_literal_rule(target, rule_file) == r"foo\*bar/"


def test_question_mark_and_brackets_escaped(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = tmp_path / "weird?name[ish]"
    target.mkdir()
    assert format_literal_rule(target, rule_file) == r"weird\?name\[ish\]/"


def test_leading_bang_in_first_segment_escaped(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = tmp_path / "!important"
    target.mkdir()
    # Leading `!` would make pathspec treat the line as a negation; escape.
    assert format_literal_rule(target, rule_file) == r"\!important/"


def test_leading_hash_in_first_segment_escaped(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    target = tmp_path / "#literal"
    target.mkdir()
    # Leading `#` at column 0 makes pathspec treat the line as a comment;
    # escape it so the rule stays an active pattern.
    assert format_literal_rule(target, rule_file) == r"\#literal/"


def test_leading_bang_only_escaped_at_first_segment(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.touch()
    # `!` in a non-leading segment is just a literal character to gitignore.
    target = tmp_path / "proj" / "!subdir"
    target.mkdir(parents=True)
    assert format_literal_rule(target, rule_file) == "proj/!subdir/"


def test_target_must_be_under_rule_file_parent(tmp_path: Path) -> None:
    # If target is not relative_to(rule_file.parent), this is a programming
    # error in the caller (rule selection should always pick an ancestor).
    rule_file = tmp_path / "a" / ".dropboxignore"
    rule_file.parent.mkdir()
    rule_file.touch()
    target = tmp_path / "b" / "elsewhere"
    target.mkdir(parents=True)
    with pytest.raises(ValueError):
        format_literal_rule(target, rule_file)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_rules_format_literal_rule.py -v
```

Expected: ImportError on `from dbxignore.rules import format_literal_rule`.

- [ ] **Step 3: Implement `format_literal_rule` in `src/dbxignore/rules.py`**

Insert after the `_resolve_to_canonical_sibling` function (around line 80, before `class _CaseInsensitiveGitIgnorePattern`):

```python
# gitignore meta-chars that need backslash-escaping when our rule generator
# encounters them as literal directory-name characters. The set tracks
# pathspec.GitIgnoreSpec's interpretation: `*` and `?` are wildcards, `[`
# starts a character class, `\` is the escape char itself. `!` and `#`
# only matter when they're the first non-whitespace character of the line
# (negation marker / comment marker), so they're handled separately below.
_META_CHARS_INLINE = frozenset("*?[\\")


def format_literal_rule(target: Path, rule_file: Path) -> str:
    """Return a gitignore-anchored literal-path rule for ``target``.

    The result is the rule line that, when written to ``rule_file``, matches
    exactly ``target`` and no other path. Used by ``cli.ignore`` to compute
    the rule to append, and by ``cli.unignore`` to compute the canonical
    rule to compare against existing rules for removal.

    Construction:

    1. Compute ``target.relative_to(rule_file.parent)`` — raises ``ValueError``
       if ``target`` is not under the rule file's directory (a caller bug;
       rule-file selection should always pick an ancestor).
    2. Escape gitignore inline meta-chars (``*``, ``?``, ``[``, ``\\``) per
       segment with a leading backslash.
    3. If the FIRST segment starts with ``!`` (negation marker) or ``#``
       (column-0 comment marker), prepend a backslash so pathspec parses
       the line as an active pattern instead of a negation or comment.
    4. Re-join segments with ``/`` (gitignore separator, regardless of
       host OS).
    5. If ``target.is_dir()``, append ``/`` to make the rule directory-only
       (matches the directory itself, not all paths whose basename equals
       the directory name).
    """
    relative = target.relative_to(rule_file.parent)
    parts = relative.parts
    escaped = [_escape_segment(p) for p in parts]
    if escaped and escaped[0].startswith(("!", "#")):
        escaped[0] = "\\" + escaped[0]
    line = "/".join(escaped)
    if target.is_dir():
        line += "/"
    return line


def _escape_segment(segment: str) -> str:
    """Backslash-escape gitignore inline meta-chars in one path segment."""
    return "".join("\\" + c if c in _META_CHARS_INLINE else c for c in segment)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_rules_format_literal_rule.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Run lint + format**

```bash
uv run ruff check src/dbxignore/rules.py tests/test_rules_format_literal_rule.py --fix
uv run ruff format src/dbxignore/rules.py tests/test_rules_format_literal_rule.py
```

- [ ] **Step 6: Commit**

```bash
git add src/dbxignore/rules.py tests/test_rules_format_literal_rule.py
git commit -m "feat(rules): format_literal_rule helper for #93"
```

---

## Task 2: `append_rule` helper

Atomic append-iff-missing helper with file-creation-with-header and `rstrip()`-tolerant idempotence detection.

**Files:**
- Modify: `src/dbxignore/rules.py` (add after `format_literal_rule`)
- Test: `tests/test_rules_append_remove.py` (new module)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rules_append_remove.py`:

```python
"""Unit tests for ``rules.append_rule`` and ``rules.remove_rule`` (item #93).

Both helpers use atomic temp-then-replace writes to avoid torn state under
SIGKILL or power loss. Tests verify idempotence, rstrip-equality semantics
(matching pathspec's gitignore-trailing-whitespace behavior), and the
file-creation-with-header path.
"""

from pathlib import Path

from dbxignore.rules import append_rule, remove_rule

# Header written when append_rule creates a new file.
HEADER = "# .dropboxignore — managed by dbxignore\n"


def test_append_creates_file_with_header_when_missing(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    appended = append_rule(rule_file, "build/")
    assert appended is True
    content = rule_file.read_text(encoding="utf-8")
    assert content == HEADER + "build/\n"


def test_append_to_existing_file_adds_line(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("node_modules/\n", encoding="utf-8")
    appended = append_rule(rule_file, "build/")
    assert appended is True
    assert rule_file.read_text(encoding="utf-8") == "node_modules/\nbuild/\n"


def test_append_idempotent_when_line_already_present(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("build/\n", encoding="utf-8")
    appended = append_rule(rule_file, "build/")
    assert appended is False
    assert rule_file.read_text(encoding="utf-8") == "build/\n"


def test_append_idempotent_with_trailing_whitespace_on_existing_line(tmp_path: Path) -> None:
    # Manually-typed rule with trailing whitespace — pathspec ignores trailing
    # whitespace when matching, so we treat it as equivalent to our canonical
    # form and do NOT add a redundant line.
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("build/   \n", encoding="utf-8")
    appended = append_rule(rule_file, "build/")
    assert appended is False
    assert rule_file.read_text(encoding="utf-8") == "build/   \n"


def test_append_handles_existing_file_without_trailing_newline(tmp_path: Path) -> None:
    # Rare but possible (manual edit, last line without final \n).
    # Our append must not produce ``last_line<newrule>``.
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("node_modules/", encoding="utf-8")  # no trailing \n
    appended = append_rule(rule_file, "build/")
    assert appended is True
    # Should be normalized so each rule is on its own line.
    assert rule_file.read_text(encoding="utf-8") == "node_modules/\nbuild/\n"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_rules_append_remove.py -v
```

Expected: ImportError on `from dbxignore.rules import append_rule, remove_rule`.

- [ ] **Step 3: Implement `append_rule` in `src/dbxignore/rules.py`**

Insert after the `_escape_segment` helper from Task 1:

```python
_FILE_HEADER = "# .dropboxignore — managed by dbxignore\n"


def append_rule(rule_file: Path, rule_line: str) -> bool:
    """Atomic append-iff-missing of ``rule_line`` to ``rule_file``.

    Returns True if the line was appended, False if an equivalent line
    (after ``rstrip()``) was already present. Creates the file with a
    leading comment header if it doesn't exist.

    Atomic via temp-then-replace, mirroring ``state.write()``: writes to
    ``<rule_file>.tmp``, then ``os.replace`` into place. Survives SIGKILL
    or power loss mid-write — the file is either fully updated or unchanged.
    Not safe against concurrent writers; intended for serial CLI invocation.
    """
    target_norm = rule_line.rstrip()
    if rule_file.exists():
        content = rule_file.read_text(encoding="utf-8")
        existing_lines = content.splitlines()
        if any(line.rstrip() == target_norm for line in existing_lines):
            return False
        # Ensure the existing content ends with a newline so our appended
        # line lands on its own line. ``splitlines()`` already ate a trailing
        # newline if present, so we always rebuild with explicit \n joins.
        new_content = "\n".join(existing_lines) + "\n" + rule_line + "\n"
    else:
        new_content = _FILE_HEADER + rule_line + "\n"

    tmp = rule_file.with_suffix(rule_file.suffix + ".tmp")
    tmp.write_text(new_content, encoding="utf-8")
    import os
    os.replace(tmp, rule_file)
    return True
```

- [ ] **Step 4: Run the append tests to verify they pass**

```bash
uv run python -m pytest tests/test_rules_append_remove.py -v -k append
```

Expected: 5 passed.

- [ ] **Step 5: Move the `import os` to the top of the file**

The implementation in Step 3 used `import os` inline for clarity in the patch. Hoist it to the top of `src/dbxignore/rules.py` if not already imported there. Check the existing `import` block and add `import os` if missing.

- [ ] **Step 6: Run lint**

```bash
uv run ruff check src/dbxignore/rules.py tests/test_rules_append_remove.py --fix
uv run ruff format src/dbxignore/rules.py tests/test_rules_append_remove.py
```

- [ ] **Step 7: Commit**

```bash
git add src/dbxignore/rules.py tests/test_rules_append_remove.py
git commit -m "feat(rules): append_rule helper for #93"
```

---

## Task 3: `remove_rule` helper

Atomic rewrite that removes all lines whose `rstrip()` matches the target. Returns the count of removed lines.

**Files:**
- Modify: `src/dbxignore/rules.py` (add after `append_rule`)
- Test: `tests/test_rules_append_remove.py` (extend with remove cases)

- [ ] **Step 1: Add the failing tests to `tests/test_rules_append_remove.py`**

Append to the test module from Task 2:

```python
def test_remove_existing_line_returns_one(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("node_modules/\nbuild/\n.venv/\n", encoding="utf-8")
    removed = remove_rule(rule_file, "build/")
    assert removed == 1
    assert rule_file.read_text(encoding="utf-8") == "node_modules/\n.venv/\n"


def test_remove_with_trailing_whitespace_in_file(tmp_path: Path) -> None:
    # Manually-edited rule with trailing whitespace: rstrip-equality matches.
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("node_modules/\nbuild/   \n.venv/\n", encoding="utf-8")
    removed = remove_rule(rule_file, "build/")
    assert removed == 1
    assert rule_file.read_text(encoding="utf-8") == "node_modules/\n.venv/\n"


def test_remove_missing_line_returns_zero(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("node_modules/\n.venv/\n", encoding="utf-8")
    removed = remove_rule(rule_file, "build/")
    assert removed == 0
    assert rule_file.read_text(encoding="utf-8") == "node_modules/\n.venv/\n"


def test_remove_multiple_occurrences(tmp_path: Path) -> None:
    # Edge case from manual editing — same line written twice.
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text("build/\nnode_modules/\nbuild/\n", encoding="utf-8")
    removed = remove_rule(rule_file, "build/")
    assert removed == 2
    assert rule_file.read_text(encoding="utf-8") == "node_modules/\n"


def test_remove_preserves_comments_and_blank_lines(tmp_path: Path) -> None:
    rule_file = tmp_path / ".dropboxignore"
    rule_file.write_text(
        "# header\n\n# group: build artifacts\nbuild/\n# group: deps\nnode_modules/\n",
        encoding="utf-8",
    )
    removed = remove_rule(rule_file, "build/")
    assert removed == 1
    assert rule_file.read_text(encoding="utf-8") == (
        "# header\n\n# group: build artifacts\n# group: deps\nnode_modules/\n"
    )


def test_remove_when_file_does_not_exist_returns_zero(tmp_path: Path) -> None:
    # Defensive: caller should validate, but we shouldn't crash.
    rule_file = tmp_path / "nonexistent.dropboxignore"
    removed = remove_rule(rule_file, "build/")
    assert removed == 0
    assert not rule_file.exists()
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
uv run python -m pytest tests/test_rules_append_remove.py -v -k remove
```

Expected: ImportError or NameError — `remove_rule` not defined.

- [ ] **Step 3: Implement `remove_rule` in `src/dbxignore/rules.py`**

Insert after `append_rule`:

```python
def remove_rule(rule_file: Path, rule_line: str) -> int:
    """Atomic remove-all-rstrip-matches of ``rule_line`` from ``rule_file``.

    Returns the count of removed lines. Returns 0 (and does not error) if
    the file doesn't exist or the line is not present. Atomic via
    temp-then-replace; the file is either fully rewritten or untouched.

    rstrip-equality (rather than exact-string equality) tolerates manually-
    typed rules with trailing whitespace, mirroring pathspec's
    gitignore-trailing-whitespace semantics.
    """
    if not rule_file.exists():
        return 0
    target_norm = rule_line.rstrip()
    content = rule_file.read_text(encoding="utf-8")
    existing_lines = content.splitlines()
    kept = [line for line in existing_lines if line.rstrip() != target_norm]
    removed_count = len(existing_lines) - len(kept)
    if removed_count == 0:
        return 0
    new_content = "\n".join(kept) + ("\n" if kept else "")
    tmp = rule_file.with_suffix(rule_file.suffix + ".tmp")
    tmp.write_text(new_content, encoding="utf-8")
    os.replace(tmp, rule_file)
    return removed_count
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_rules_append_remove.py -v
```

Expected: 11 passed (5 from Task 2 + 6 new).

- [ ] **Step 5: Run lint**

```bash
uv run ruff check src/dbxignore/rules.py tests/test_rules_append_remove.py --fix
uv run ruff format src/dbxignore/rules.py tests/test_rules_append_remove.py
```

- [ ] **Step 6: Commit**

```bash
git add src/dbxignore/rules.py tests/test_rules_append_remove.py
git commit -m "feat(rules): remove_rule helper for #93"
```

---

## Task 4: `_select_rule_file` helper

Walks from `target.parent` toward `root`, returning the closest existing `.dropboxignore` ancestor, or `root / IGNORE_FILENAME` if none. Pure function plus stat checks.

**Files:**
- Modify: `src/dbxignore/cli.py` (add helper near `_load_cache` at line 93)
- Test: `tests/test_cli_ignore.py` (new module — first usage)

- [ ] **Step 1: Create `tests/test_cli_ignore.py` with the failing test**

```python
"""CLI integration tests for ``dbxignore ignore`` and ``dbxignore unignore`` (item #93).

Covers helper unit tests, command happy paths, idempotence + redundancy
branches, --yes / --dry-run flags, error paths, and daemon-coexistence smoke.
"""

from pathlib import Path

import pytest

from dbxignore import cli
from dbxignore.rules import IGNORE_FILENAME


def test_select_rule_file_finds_target_parent_dropboxignore(tmp_path: Path) -> None:
    root = tmp_path
    proj = root / "proj"
    proj.mkdir()
    (proj / IGNORE_FILENAME).touch()
    target = proj / "build"
    target.mkdir()
    selected = cli._select_rule_file(target, root)
    assert selected == proj / IGNORE_FILENAME


def test_select_rule_file_walks_to_higher_ancestor(tmp_path: Path) -> None:
    root = tmp_path
    (root / IGNORE_FILENAME).touch()
    deep = root / "a" / "b" / "c"
    deep.mkdir(parents=True)
    selected = cli._select_rule_file(deep, root)
    assert selected == root / IGNORE_FILENAME


def test_select_rule_file_falls_back_to_root_when_no_ancestor(tmp_path: Path) -> None:
    root = tmp_path
    deep = root / "a" / "b"
    deep.mkdir(parents=True)
    selected = cli._select_rule_file(deep, root)
    # Returns the canonical root file path even if it doesn't exist yet —
    # ``append_rule`` will create it on first invocation.
    assert selected == root / IGNORE_FILENAME
    assert not selected.exists()


def test_select_rule_file_prefers_closer_ancestor(tmp_path: Path) -> None:
    root = tmp_path
    (root / IGNORE_FILENAME).touch()
    proj = root / "proj"
    proj.mkdir()
    (proj / IGNORE_FILENAME).touch()
    target = proj / "build"
    target.mkdir()
    selected = cli._select_rule_file(target, root)
    # Closer ancestor wins.
    assert selected == proj / IGNORE_FILENAME
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_cli_ignore.py -v
```

Expected: AttributeError on `cli._select_rule_file`.

- [ ] **Step 3: Implement `_select_rule_file` in `src/dbxignore/cli.py`**

Insert after `_load_cache` (around line 130 — find the function's closing line and insert below):

```python
def _select_rule_file(target: Path, root: Path) -> Path:
    """Return the closest ``.dropboxignore`` ancestor of ``target`` under ``root``.

    Walks from ``target.parent`` up to (and including) ``root``. Returns the
    first existing ``.dropboxignore`` found, or ``root / IGNORE_FILENAME``
    if no ancestor file exists. The returned path may not exist on disk —
    ``append_rule`` creates it on first invocation.

    ``root`` is assumed to be a Dropbox root (under which ``target`` lives).
    Caller is responsible for verifying ``target`` is under ``root`` before
    calling.
    """
    current = target.parent
    while current != root.parent:  # walk up to and including root
        candidate = current / IGNORE_FILENAME
        if candidate.is_file():
            return candidate
        if current == root:
            break
        current = current.parent
    return root / IGNORE_FILENAME
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_cli_ignore.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Run lint**

```bash
uv run ruff check src/dbxignore/cli.py tests/test_cli_ignore.py --fix
uv run ruff format src/dbxignore/cli.py tests/test_cli_ignore.py
```

- [ ] **Step 6: Commit**

```bash
git add src/dbxignore/cli.py tests/test_cli_ignore.py
git commit -m "feat(cli): _select_rule_file helper for #93"
```

---

## Task 5: `dbxignore ignore <path>` — happy path + idempotence

Wire up the `ignore` command using the helpers from Tasks 1-4. Cover: happy path, ancestor file selection, idempotence on re-call, half-state recovery.

**Files:**
- Modify: `src/dbxignore/cli.py` (add new `@main.command()` block at end of file, before any final helpers)
- Test: `tests/test_cli_ignore.py` (extend)

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_cli_ignore.py`:

```python
from click.testing import CliRunner

from dbxignore import state
from tests.conftest import FakeMarkers


def _setup_dropbox_root(
    tmp_path: Path,
    fake_markers: FakeMarkers,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Stage a Dropbox root with no existing rule files, no daemon alive."""
    root = tmp_path
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    monkeypatch.setattr(state, "default_path", lambda: root / "_state.json")
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid, create_time=None: False)
    return root


def test_ignore_happy_path_creates_root_file(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    rule_file = root / IGNORE_FILENAME
    assert rule_file.exists()
    assert "build/" in rule_file.read_text(encoding="utf-8")
    assert fake_markers.is_ignored(target)


def test_ignore_lands_in_nearest_ancestor(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    proj = root / "proj"
    proj.mkdir()
    (proj / IGNORE_FILENAME).touch()
    target = proj / "foo" / "bar"
    target.mkdir(parents=True)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    # Rule landed in proj/.dropboxignore, not root/.dropboxignore.
    assert "foo/bar/" in (proj / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert not (root / IGNORE_FILENAME).exists()
    assert fake_markers.is_ignored(target)


def test_ignore_idempotent_on_recall(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    runner = CliRunner()
    runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    rule_file_content_first = (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    assert "already ignored" in result.output
    # File unchanged on second call.
    assert (root / IGNORE_FILENAME).read_text(encoding="utf-8") == rule_file_content_first


def test_ignore_half_state_marker_missing(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule on disk, but marker not set (e.g. daemon was down on previous call)."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    (root / IGNORE_FILENAME).write_text("build/\n", encoding="utf-8")
    assert not fake_markers.is_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    # Already-ignored detected; marker set as half-state recovery.
    assert "already ignored" in result.output
    assert fake_markers.is_ignored(target)


def test_ignore_redundant_when_wildcard_already_matches(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    proj = root / "proj"
    proj.mkdir()
    (proj / IGNORE_FILENAME).write_text("**/build/\n", encoding="utf-8")
    target = proj / "build"
    target.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    # No redundant literal rule appended; informational message printed.
    assert "already covered" in result.output
    content = (proj / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert content == "**/build/\n"  # unchanged
    assert fake_markers.is_ignored(target)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_cli_ignore.py -v -k "ignore_"
```

Expected: errors — `ignore` command not registered.

- [ ] **Step 3: Implement the `ignore` command in `src/dbxignore/cli.py`**

Add this command block. Place it after the `clear` command (around line 700, before `explain`). The exact insertion line will depend on current cli.py state — look for the `clear` command block's closing and append after it.

```python
@main.command()
@click.argument("path", type=click.Path(exists=False, path_type=Path))
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be added/marked without changing anything.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Skip the confirmation prompt (for scripted use). Without --yes "
    "and outside --dry-run, ignore previews changes and asks before "
    "mutating — marking a previously-synced path causes Dropbox to "
    "remove it from cloud and from every linked device.",
)
def ignore(path: Path, dry_run: bool, yes: bool) -> None:
    """Mark <PATH> ignored persistently.

    Appends a literal-path rule to the nearest ancestor .dropboxignore
    (creating one at the Dropbox root if no ancestor exists) AND sets the
    ignore marker on <PATH> in one synchronous invocation. Idempotent —
    safe to re-call.
    """
    target = path.resolve()
    if not target.exists():
        click.echo(f"error: {path} does not exist", err=True)
        sys.exit(2)

    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found. Is Dropbox installed?", err=True)
        sys.exit(2)

    root = find_containing(target, discovered)
    if root is None:
        click.echo(f"error: {path} is not under any Dropbox root", err=True)
        sys.exit(2)

    cache = _load_cache(discovered)
    rule_file = _select_rule_file(target, root)
    canonical = rules.format_literal_rule(target, rule_file)

    # Idempotence + redundancy guards
    if cache.match(target):
        matches = cache.explain(target)
        via_us = any(
            m.pattern.rstrip() == canonical.rstrip() for m in matches if not m.is_dropped
        )
        if via_us:
            click.echo(f"{path} is already ignored.")
        else:
            blocker = next(m for m in matches if not m.is_dropped)
            click.echo(
                f"{path} is already covered by {blocker.pattern.rstrip()!r} "
                f"in {blocker.ignore_file}; not adding redundant rule."
            )
        # Half-state recovery: ensure marker is set even if rule was already on disk.
        if not markers.is_ignored(target):
            markers.set_ignored(target)
            click.echo(f"Set marker on {target}.")
        return

    # Confirmation
    if dry_run:
        click.echo(f"would append {canonical!r} to {rule_file}")
        click.echo(f"would set marker on {target}")
        return

    if not yes:
        click.echo(f"This will mark {target} ignored.")
        click.echo(
            "Dropbox will remove it from cloud Dropbox and from every "
            "other linked device. Local copies on this device are preserved."
        )
        if not click.confirm("Continue?"):
            click.echo("Aborted.")
            return

    # Mutation: rule first, then marker (avoids the daemon-race documented
    # in the spec § Order of operations).
    rules.append_rule(rule_file, canonical)
    markers.set_ignored(target)
    click.echo(f"ignore: rule added to {rule_file}; marker set on {target}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_cli_ignore.py -v -k "ignore_"
```

Expected: 5 passed (the 5 ignore_* tests added in Step 1).

- [ ] **Step 5: Run the full test_cli_ignore module**

```bash
uv run python -m pytest tests/test_cli_ignore.py -v
```

Expected: 9 passed (4 from Task 4 + 5 here).

- [ ] **Step 6: Run lint**

```bash
uv run ruff check src/dbxignore/cli.py tests/test_cli_ignore.py --fix
uv run ruff format src/dbxignore/cli.py tests/test_cli_ignore.py
```

- [ ] **Step 7: Commit**

```bash
git add src/dbxignore/cli.py tests/test_cli_ignore.py
git commit -m "feat(cli): ignore <path> command for #93"
```

---

## Task 6: `dbxignore ignore <path>` — error paths + meta-char escaping

Cover: path doesn't exist, path outside roots, no Dropbox roots, --dry-run preview, file target (no trailing /), meta-char escaping in directory name.

**Files:**
- Test: `tests/test_cli_ignore.py` (extend)

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_cli_ignore.py`:

```python
def test_ignore_rejects_nonexistent_path(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(root / "ghost"), "--yes"])
    assert result.exit_code == 2
    assert "does not exist" in result.output


def test_ignore_rejects_path_outside_roots(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    elsewhere = tmp_path.parent / "not_dropbox"
    elsewhere.mkdir(exist_ok=True)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(elsewhere), "--yes"])
    assert result.exit_code == 2
    assert "not under any Dropbox root" in result.output


def test_ignore_rejects_no_dropbox_roots(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    target = tmp_path / "build"
    target.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 2
    assert "No Dropbox roots" in result.output


def test_ignore_dry_run_does_not_mutate(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would append" in result.output
    assert "would set marker" in result.output
    assert not (root / IGNORE_FILENAME).exists()
    assert not fake_markers.is_ignored(target)


def test_ignore_file_target_has_no_trailing_slash(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "notes.txt"
    target.touch()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    content = (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert "notes.txt\n" in content
    assert "notes.txt/\n" not in content


def test_ignore_meta_char_escaping_in_dir_name(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "foo*bar"
    target.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    content = (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert r"foo\*bar/" in content


def test_ignore_default_prompts_then_aborts_on_no(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["ignore", str(target)], input="n\n")
    assert result.exit_code == 0, result.output
    assert "Aborted" in result.output
    # No mutation occurred.
    assert not (root / IGNORE_FILENAME).exists()
    assert not fake_markers.is_ignored(target)
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_cli_ignore.py -v
```

Expected: 16 passed (9 prior + 7 here). The implementation from Task 5 already handles all these cases — these tests just confirm.

- [ ] **Step 3: Run lint**

```bash
uv run ruff check tests/test_cli_ignore.py --fix
uv run ruff format tests/test_cli_ignore.py
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli_ignore.py
git commit -m "test(cli): error paths + meta-char escaping for ignore (#93)"
```

---

## Task 7: `dbxignore unignore <path>` — happy path + multi-file removal

Wire up the `unignore` command. Cover: happy path (rule + marker removed), multi-file rule removal (Q4 case 5), already-not-ignored no-op (Q4 case 4).

**Files:**
- Modify: `src/dbxignore/cli.py` (add `@main.command()` block after `ignore`)
- Test: `tests/test_cli_ignore.py` (extend)

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_cli_ignore.py`:

```python
def test_unignore_happy_path(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    # Pre-state: rule + marker.
    (root / IGNORE_FILENAME).write_text("build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    # Rule removed (file may be empty / header-only).
    content = (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert "build/" not in content
    # Marker cleared.
    assert not fake_markers.is_ignored(target)


def test_unignore_already_not_ignored_is_noop(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    assert "not ignored" in result.output


def test_unignore_removes_from_multiple_files(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    proj = root / "proj"
    proj.mkdir()
    target = proj / "build"
    target.mkdir()
    # Same target literal rule in TWO ancestor files (edge case Q4 case 5).
    (root / IGNORE_FILENAME).write_text("proj/build/\n", encoding="utf-8")
    (proj / IGNORE_FILENAME).write_text("build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    # Both rules removed.
    assert "proj/build/" not in (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert "build/" not in (proj / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert not fake_markers.is_ignored(target)


def test_unignore_rejects_nonexistent_path(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(root / "ghost"), "--yes"])
    assert result.exit_code == 2
    assert "does not exist" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_cli_ignore.py -v -k "unignore"
```

Expected: errors — `unignore` command not registered.

- [ ] **Step 3: Implement the `unignore` command in `src/dbxignore/cli.py`**

Add immediately after the `ignore` command:

```python
@main.command()
@click.argument("path", type=click.Path(exists=False, path_type=Path))
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be removed/cleared without changing anything.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Skip the confirmation prompt (for scripted use). Without --yes "
    "and outside --dry-run, unignore previews changes and asks before "
    "mutating — clearing a marker causes Dropbox to start syncing the "
    "path again and re-upload its contents to cloud.",
)
def unignore(path: Path, dry_run: bool, yes: bool) -> None:
    """Remove the ignore marker and rule for <PATH>.

    Inverse of ``ignore``. Removes all literal-path rules in the relevant
    .dropboxignore file(s) that match <PATH> AND clears the marker. If
    <PATH> is also matched by a wildcard or non-literal rule, refuses
    to mutate and names the blocking rule.
    """
    target = path.resolve()
    if not target.exists():
        click.echo(f"error: {path} does not exist", err=True)
        sys.exit(2)

    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found. Is Dropbox installed?", err=True)
        sys.exit(2)

    root = find_containing(target, discovered)
    if root is None:
        click.echo(f"error: {path} is not under any Dropbox root", err=True)
        sys.exit(2)

    cache = _load_cache(discovered)

    if not cache.match(target):
        click.echo(f"{path} is not ignored; nothing to do.")
        return

    # Find all rules that match the target. Each Match has ignore_file +
    # line + pattern. is_dropped matches are inert (under an ignored ancestor),
    # so we only consider non-dropped matches as blockers/removable.
    matches = [m for m in cache.explain(target) if not m.is_dropped]

    # Compute canonical rule for each candidate ancestor file.
    canonical_per_file: dict[Path, str] = {}
    for m in matches:
        if m.ignore_file not in canonical_per_file:
            canonical_per_file[m.ignore_file] = rules.format_literal_rule(target, m.ignore_file)

    removable = [
        m for m in matches if m.pattern.rstrip() == canonical_per_file[m.ignore_file].rstrip()
    ]
    blockers = [m for m in matches if m not in removable]

    if blockers:
        click.echo(f"error: {path} is also matched by:", err=True)
        for m in blockers:
            click.echo(f"  line {m.line} of {m.ignore_file}: {m.pattern.rstrip()}", err=True)
        click.echo("Remove these manually if you want to unignore this path.", err=True)
        sys.exit(2)

    # Confirmation
    if dry_run:
        for m in removable:
            click.echo(f"would remove {m.pattern.rstrip()!r} from {m.ignore_file}")
        click.echo(f"would clear marker on {target}")
        return

    if not yes:
        click.echo(f"This will unignore {target}.")
        click.echo("Dropbox will start syncing it again and upload local contents to cloud.")
        if not click.confirm("Continue?"):
            click.echo("Aborted.")
            return

    # Mutation: rules first, then marker.
    affected_files: set[Path] = set()
    for m in removable:
        rules.remove_rule(m.ignore_file, m.pattern)
        affected_files.add(m.ignore_file)
    markers.clear_ignored(target)
    files_str = ", ".join(str(f) for f in sorted(affected_files))
    click.echo(f"unignore: rule removed from {files_str}; marker cleared on {target}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_cli_ignore.py -v -k "unignore"
```

Expected: 4 passed.

- [ ] **Step 5: Run lint**

```bash
uv run ruff check src/dbxignore/cli.py tests/test_cli_ignore.py --fix
uv run ruff format src/dbxignore/cli.py tests/test_cli_ignore.py
```

- [ ] **Step 6: Commit**

```bash
git add src/dbxignore/cli.py tests/test_cli_ignore.py
git commit -m "feat(cli): unignore <path> command for #93"
```

---

## Task 8: `dbxignore unignore <path>` — wildcard collision + flags

Cover: wildcard-collision blockers (Q4 cases 2/3), --dry-run preview, trailing-whitespace tolerance on rule-line equality.

**Files:**
- Test: `tests/test_cli_ignore.py` (extend)

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_cli_ignore.py`:

```python
def test_unignore_fails_loud_on_wildcard_collision(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Q4 case 2: literal rule + wildcard. Removing literal would still leave
    the path matched by the wildcard, so refuse to mutate."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    proj = root / "proj"
    proj.mkdir()
    target = proj / "build"
    target.mkdir()
    # Literal rule we wrote + wildcard rule the user added separately.
    (proj / IGNORE_FILENAME).write_text("build/\n**/build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 2
    assert "is also matched by" in result.output
    assert "**/build/" in result.output
    # Neither rule mutated; marker still set.
    content = (proj / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert "build/\n" in content
    assert "**/build/\n" in content
    assert fake_markers.is_ignored(target)


def test_unignore_fails_loud_when_only_wildcard_matches(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Q4 case 3: only a wildcard rule matches; no literal rule to remove.
    Same fail-loud message — the user has to remove the wildcard manually."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    proj = root / "proj"
    proj.mkdir()
    target = proj / "build"
    target.mkdir()
    (proj / IGNORE_FILENAME).write_text("**/build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 2
    assert "is also matched by" in result.output


def test_unignore_dry_run_does_not_mutate(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    (root / IGNORE_FILENAME).write_text("build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would remove" in result.output
    assert "would clear marker" in result.output
    # No mutation.
    assert "build/" in (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert fake_markers.is_ignored(target)


def test_unignore_tolerates_trailing_whitespace_in_rule_line(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manually-edited rule with trailing spaces — rstrip-equality matches
    the canonical form, rule is removable not blocking."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    (root / IGNORE_FILENAME).write_text("build/   \n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target), "--yes"])
    assert result.exit_code == 0, result.output
    assert "build/" not in (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert not fake_markers.is_ignored(target)


def test_unignore_default_prompts_then_aborts_on_no(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    (root / IGNORE_FILENAME).write_text("build/\n", encoding="utf-8")
    fake_markers.set_ignored(target)
    runner = CliRunner()
    result = runner.invoke(cli.main, ["unignore", str(target)], input="n\n")
    assert result.exit_code == 0, result.output
    assert "Aborted" in result.output
    # No mutation.
    assert "build/" in (root / IGNORE_FILENAME).read_text(encoding="utf-8")
    assert fake_markers.is_ignored(target)
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
uv run python -m pytest tests/test_cli_ignore.py -v
```

Expected: 25 passed (Task 4 added 4 + Task 5 added 5 + Task 6 added 7 + Task 7 added 4 + this task added 5 = 25).

- [ ] **Step 3: Run lint**

```bash
uv run ruff check tests/test_cli_ignore.py --fix
uv run ruff format tests/test_cli_ignore.py
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli_ignore.py
git commit -m "test(cli): wildcard-collision + dry-run for unignore (#93)"
```

---

## Task 9: Daemon-coexistence smoke test

A test that fires a synthetic RULES event into `daemon._dispatch` after the verb runs, asserting reconcile sees a consistent state and doesn't spurious-clear or spurious-mark. Validates the order-of-operations decision in the spec.

**Files:**
- Test: `tests/test_cli_ignore.py` (extend)

- [ ] **Step 1: Add the failing test**

Append to `tests/test_cli_ignore.py`:

```python
from dbxignore import daemon, reconcile
from dbxignore.rules import RuleCache
from tests.conftest import stub_event


def test_ignore_then_synthetic_rules_event_no_spurious_mutation(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Order-of-ops invariant (spec § Order of operations): after ``ignore``
    completes, a synthetic RULES event reconciling the rule-file's mount
    must not trigger a mark-or-clear. State should already be consistent."""
    root = _setup_dropbox_root(tmp_path, fake_markers, monkeypatch)
    target = root / "build"
    target.mkdir()
    runner = CliRunner()
    runner.invoke(cli.main, ["ignore", str(target), "--yes"])

    # Snapshot post-verb state.
    set_calls_before = list(fake_markers.set_calls)
    clear_calls_before = list(fake_markers.clear_calls)

    # Build a fresh cache from the now-mutated rule file (mirroring what
    # the daemon does on RULES event).
    cache = RuleCache()
    cache.load_root(root)
    # Run reconcile_subtree directly (skipping debouncer) on the rule
    # file's mount — this is what the daemon's _dispatch does.
    reconcile.reconcile_subtree(root, root, cache)

    # No additional set_ignored or clear_ignored calls should have happened
    # — the marker is already correct.
    assert fake_markers.set_calls == set_calls_before
    assert fake_markers.clear_calls == clear_calls_before
    # Final state still correct.
    assert fake_markers.is_ignored(target)
```

- [ ] **Step 2: Run the test to verify it passes**

```bash
uv run python -m pytest tests/test_cli_ignore.py::test_ignore_then_synthetic_rules_event_no_spurious_mutation -v
```

Expected: PASS. The test should pass without any new code changes — the order-of-ops decision in the spec is already implemented in Task 5's `ignore` command. If it fails, investigate: most likely the `_select_rule_file` is returning a path under which `reconcile_subtree` doesn't see consistent state.

- [ ] **Step 3: Run the full test_cli_ignore module**

```bash
uv run python -m pytest tests/test_cli_ignore.py -v
```

Expected: 26 passed.

- [ ] **Step 4: Run lint**

```bash
uv run ruff check tests/test_cli_ignore.py --fix
uv run ruff format tests/test_cli_ignore.py
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_cli_ignore.py
git commit -m "test(cli): daemon-coexistence smoke for ignore (#93)"
```

---

## Task 10: README CLI reference

Add `ignore` and `unignore` to the README's `## Commands` table (or wherever the CLI surface is documented).

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Find the existing Commands table**

```bash
grep -n "## Commands\|^| .apply" README.md
```

Note the line range of the existing table. The conventions there will dictate cell width and column ordering.

- [ ] **Step 2: Insert new rows for `ignore` and `unignore`**

Add rows for `ignore` and `unignore` immediately after the `clear` row (since both are mutation verbs). Match the existing column layout. Example phrasing — adapt to existing column conventions:

```markdown
| `ignore <path>` | Append a literal-path rule to the nearest ancestor `.dropboxignore` and set the ignore marker on `<path>`. |
| `unignore <path>` | Remove that rule and clear the marker. Refuses if `<path>` is also matched by a wildcard rule. |
```

If a §"Ad-hoc ignore" subsection makes sense in the README structure, add a short paragraph linking the two verbs to the daemon-coexistence story (the rule lands on disk, the daemon reconciles, no race). Otherwise, the table entries are sufficient.

- [ ] **Step 3: Verify the README still renders cleanly**

```bash
# A quick sanity grep that table syntax is still balanced.
grep -c "^| " README.md
```

Should be the previous count + 2.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(readme): document ignore + unignore verbs (#93)"
```

---

## Task 11: Manual-test scripts Phase 4.5

Add three new Phase 4.5 cases (4o, 4p, 4q) to the shared bash helper AND the Windows PowerShell script. Per CLAUDE.md, this is required for any user-visible CLI surface change.

**Files:**
- Modify: `scripts/_phase_extended_cli.sh` (Linux + macOS shared helper)
- Modify: `scripts/manual-test-windows.ps1` (Windows PowerShell)

- [ ] **Step 1: Read the existing Phase 4.5 cases**

```bash
sed -n '1,180p' scripts/_phase_extended_cli.sh
```

Note the function-per-case pattern (e.g. `_phase_4n_clear_basic`). Find the calling function (typically `phase_extended_cli` or similar) where the cases are dispatched.

- [ ] **Step 2: Add three new functions to `scripts/_phase_extended_cli.sh`**

After the existing `_phase_4n_clear_basic` function (or whatever the last numbered case is), insert:

```bash
# 4o — dbxignore ignore <path> happy path (PR #<N>)
_phase_4o_ignore_basic() {
    echo "--- Phase 4o: ignore basic ---"
    local target="$DROPBOX_ROOT/dbxignore_test_4o"
    mkdir -p "$target"
    dbxignore ignore "$target" --yes
    grep -q "dbxignore_test_4o/" "$DROPBOX_ROOT/.dropboxignore" \
        || { echo "FAIL: rule not appended"; return 1; }
    # Marker check (xattr on Linux/macOS).
    case "$(uname)" in
        Linux)
            getfattr -d "$target" 2>/dev/null | grep -q "user.com.dropbox.ignored" \
                || { echo "FAIL: marker not set"; return 1; } ;;
        Darwin)
            xattr -p com.dropbox.ignored "$target" >/dev/null 2>&1 \
                || xattr -p "com.apple.fileprovider.ignore#P" "$target" >/dev/null 2>&1 \
                || { echo "FAIL: marker not set"; return 1; } ;;
    esac
    echo "PASS"
}

# 4p — dbxignore unignore <path> happy path (PR #<N>)
_phase_4p_unignore_basic() {
    echo "--- Phase 4p: unignore basic ---"
    local target="$DROPBOX_ROOT/dbxignore_test_4o"  # reuse 4o's target
    dbxignore unignore "$target" --yes
    grep -q "dbxignore_test_4o/" "$DROPBOX_ROOT/.dropboxignore" \
        && { echo "FAIL: rule still in file"; return 1; }
    rm -rf "$target"
    echo "PASS"
}

# 4q — dbxignore unignore wildcard collision (PR #<N>)
_phase_4q_unignore_wildcard_collision() {
    echo "--- Phase 4q: unignore wildcard collision ---"
    local target="$DROPBOX_ROOT/dbxignore_test_4q"
    mkdir -p "$target"
    # Add both literal + wildcard rules.
    echo "dbxignore_test_4q/" >> "$DROPBOX_ROOT/.dropboxignore"
    echo "**/dbxignore_test_4q/" >> "$DROPBOX_ROOT/.dropboxignore"
    sleep 0.5  # let daemon's RULES debouncer process the edit
    if dbxignore unignore "$target" --yes; then
        echo "FAIL: unignore should have refused due to wildcard"
        return 1
    fi
    # Cleanup.
    sed -i '/dbxignore_test_4q\//d' "$DROPBOX_ROOT/.dropboxignore"
    rm -rf "$target"
    echo "PASS"
}
```

- [ ] **Step 3: Wire the new functions into the Phase 4.5 dispatcher**

Find the calling function (search for `_phase_4n_clear_basic`'s call site). Add three calls:

```bash
_phase_4o_ignore_basic
_phase_4p_unignore_basic
_phase_4q_unignore_wildcard_collision
```

- [ ] **Step 4: Mirror the cases in `scripts/manual-test-windows.ps1`**

Find the `Test-ExtendedCli` function (or wherever Phase 4.5 lives in the PowerShell script). Add equivalent PowerShell test cases. Example shape (adapt to the script's existing helper conventions):

```powershell
# 4o — dbxignore ignore <path> happy path (PR #<N>)
function Test-Phase4o-Ignore {
    Write-Host "--- Phase 4o: ignore basic ---"
    $target = Join-Path $DropboxRoot "dbxignore_test_4o"
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    & dbxignore ignore $target --yes
    if (-not (Select-String -Path (Join-Path $DropboxRoot ".dropboxignore") -Pattern "dbxignore_test_4o/" -Quiet)) {
        throw "FAIL: rule not appended"
    }
    # ADS marker check.
    $ads = Get-Item -Path "${target}:com.dropbox.ignored" -ErrorAction SilentlyContinue
    if ($null -eq $ads) { throw "FAIL: ADS marker not set" }
    Write-Host "PASS"
}

# 4p — dbxignore unignore <path> happy path (PR #<N>)
function Test-Phase4p-Unignore {
    Write-Host "--- Phase 4p: unignore basic ---"
    $target = Join-Path $DropboxRoot "dbxignore_test_4o"
    & dbxignore unignore $target --yes
    if (Select-String -Path (Join-Path $DropboxRoot ".dropboxignore") -Pattern "dbxignore_test_4o/" -Quiet) {
        throw "FAIL: rule still in file"
    }
    Remove-Item -Recurse -Force $target
    Write-Host "PASS"
}

# 4q — dbxignore unignore wildcard collision (PR #<N>)
function Test-Phase4q-WildcardCollision {
    Write-Host "--- Phase 4q: unignore wildcard collision ---"
    $target = Join-Path $DropboxRoot "dbxignore_test_4q"
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Add-Content -Path (Join-Path $DropboxRoot ".dropboxignore") -Value "dbxignore_test_4q/"
    Add-Content -Path (Join-Path $DropboxRoot ".dropboxignore") -Value "**/dbxignore_test_4q/"
    Start-Sleep -Milliseconds 500
    & dbxignore unignore $target --yes
    if ($LASTEXITCODE -eq 0) { throw "FAIL: unignore should have refused" }
    # Cleanup.
    $content = Get-Content (Join-Path $DropboxRoot ".dropboxignore") | Where-Object { $_ -notmatch "dbxignore_test_4q/" }
    Set-Content -Path (Join-Path $DropboxRoot ".dropboxignore") -Value $content
    Remove-Item -Recurse -Force $target
    Write-Host "PASS"
}
```

Wire these into the PowerShell Phase 4.5 dispatcher (look for the existing `Test-Phase4n-*` invocation).

- [ ] **Step 5: Replace `<N>` placeholders with the actual PR number**

This task can't predict the PR number, so the provenance comments use `<N>`. Just before pushing the PR (Task 15), grep for `# 4[opq] —.*PR #<N>` in both scripts and replace with the actual PR number from `gh pr list --state all --limit 1` + 1.

- [ ] **Step 6: Commit**

```bash
git add scripts/_phase_extended_cli.sh scripts/manual-test-windows.ps1
git commit -m "test(scripts): Phase 4.5 cases 4o-4q for ignore/unignore (#93)"
```

---

## Task 12: BACKLOG.md update — RESOLVED marker + Open list + #65 cross-ref

Mark item #93 as RESOLVED with the actual PR number, remove from Open list, update #65's "Blocked by #93" cross-reference to a "Resolved by PR #N" form, drop the count from "Ten items" back to "Nine items."

**Files:**
- Modify: `BACKLOG.md`

- [ ] **Step 1: Predict the next PR number**

```bash
gh pr list --state all --limit 1 --json number --jq '.[0].number'
gh issue list --state all --limit 1 --json number --jq '.[0].number'
```

Take `max(numbers) + 1` as the predicted PR number for the inline RESOLVED marker. (Note: in this PR the spec + plan + implementation all land together, so all references can use the same predicted number.)

- [ ] **Step 2: Add inline RESOLVED marker to item #93's body**

Edit the heading + paragraph at item #93. The first line of the body becomes:

```markdown
**Status: RESOLVED <YYYY-MM-DD> (PR #<N>).** Took fix candidate (1) — append-rule-and-mark verbs. Implementation matches the design spec (see `docs/superpowers/specs/2026-05-10-93-path-taking-ignore-verbs-design.md`); see `docs/superpowers/plans/2026-05-10-93-path-taking-ignore-verbs-implementation.md` for the realized task decomposition.
```

Replace `<YYYY-MM-DD>` with today's date and `<N>` with the predicted PR number.

- [ ] **Step 3: Remove #93 from the Open list, fix the count, update #65's cross-reference**

In the `## Status > ### Open` section:

- Change `Ten items.` back to `Nine items.`
- Remove the `**#93** —` bullet entirely.
- Update the `**#65** — ...` bullet's tail from `**Blocked by #93** — registry verb has nothing to invoke until the path-taking ignore/unignore CLI verbs land.` to `Path-taking verbs landed in PR #<N> (item #93); spec/plan/PR cycle for #65 can resume.`

- [ ] **Step 4: Add #93 to the Resolved section**

In `## Status > ### Resolved (reverse chronological) > #### <YYYY-MM-DD>`, add the new entry at the top:

```markdown
- **#93** in PR #<N> — path-taking `ignore` / `unignore` CLI verbs. Took fix candidate (1) — append-rule-and-mark. New helpers in `rules.py` (`format_literal_rule`, `append_rule`, `remove_rule`); two new `@main.command()` blocks in `cli.py` plus `_select_rule_file` helper. Order of operations is rule-first-then-marker (avoids the daemon-race where marker-first could trigger a spurious clear in the OTHER debouncer's 500ms window). `unignore` fails loud on wildcard collisions naming the blocking rule and file. rstrip-equality on rule-line comparisons tolerates manually-typed rules with trailing whitespace, mirroring pathspec's gitignore-trailing-whitespace semantics. Unblocks item #65 (Windows Explorer right-click integration).
```

- [ ] **Step 5: Run a sanity check on the Open list**

```bash
grep -c "^- \*\*#" BACKLOG.md
```

Should be the previous count - 1 (since #93 was added in PR #190 then now removed).

- [ ] **Step 6: Commit**

```bash
git add BACKLOG.md
git commit -m "docs(backlog): mark #93 RESOLVED; #65 unblocked"
```

---

## Task 13: Final lint + typecheck + full test suite

Run all the gates the project documents in CLAUDE.md's "How to run checks."

**Files:** None (verification step).

- [ ] **Step 1: Run the canonical check sequence**

```bash
uv run mypy .
uv run ruff check . --fix
uv run ruff check .
uv run ruff format .
uv run python -m pytest
```

Expected outputs:
- `mypy`: same number of errors as before (the existing `tests/conftest.py:9 [attr-defined]` errors are pre-existing per CLAUDE.md gotcha; no new errors).
- `ruff check`: clean.
- `ruff format`: nothing to reformat.
- `pytest`: all tests pass; the platform-gated `windows_only` / `linux_only` / `macos_only` markers are CI-only.

- [ ] **Step 2: Re-run commit-check against every commit on the branch**

```bash
for sha in $(git log origin/main..HEAD --format='%h'); do
    git log -1 --format='%s' $sha > /tmp/subj-$sha.txt
    echo "--- $sha ---"
    commit-check -m /tmp/subj-$sha.txt
done
```

Expected: every commit returns exit 0.

- [ ] **Step 3: Verify subject byte lengths are under 72**

```bash
for sha in $(git log origin/main..HEAD --format='%h'); do
    SUBJ=$(git log -1 --format='%s' $sha)
    BYTES=$(echo -n "$SUBJ" | wc -c)
    echo "$BYTES bytes: $SUBJ"
done
```

Each line should report fewer than 72 bytes.

- [ ] **Step 4: If anything is wrong, fix it inline and amend the relevant commit**

Use `git rebase -i` (or fix forward via new commits) per project preferences. Per CLAUDE.md: prefer creating new commits over amending unless the user explicitly requests amend.

- [ ] **Step 5: No commit needed for this task** (verification-only)

---

## Task 14: Push branch + open PR

The full work (spec + plan + implementation) lands as one PR per project precedent (#53's `feat/53-ready-before-sweep`).

**Files:** None.

- [ ] **Step 1: Push the branch**

```bash
git push -u origin chore/issue-93-spec
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "feat: path-taking ignore/unignore verbs (#93)" --body "$(cat <<'EOF'
## Summary

- Adds two new CLI commands `dbxignore ignore <path>` and `dbxignore unignore <path>` that mutate `.dropboxignore` (append / remove a literal-path rule) AND set / clear the marker, in one synchronous invocation.
- Order of operations is rule-first-then-marker to avoid a daemon-race where marker-first could trigger a spurious clear in the OTHER debouncer's 500ms window.
- `unignore` fails loud on wildcard collisions, naming the blocking rule and file. rstrip-equality on rule-line comparisons tolerates manually-typed rules with trailing whitespace.
- Unblocks item #65 (Windows Explorer right-click integration), which can now wire its registry verb to `dbxignore.exe ignore --yes "%1"`.

## Spec & plan

- Design spec: `docs/superpowers/specs/2026-05-10-93-path-taking-ignore-verbs-design.md`
- Implementation plan: `docs/superpowers/plans/2026-05-10-93-path-taking-ignore-verbs-implementation.md`

## Test plan

- [x] Unit tests for new helpers (`format_literal_rule`, `append_rule`, `remove_rule`, `_select_rule_file`).
- [x] CLI integration tests for `ignore` (happy path, ancestor selection, idempotence, half-state recovery, wildcard-already-matches, error paths, --yes / --dry-run).
- [x] CLI integration tests for `unignore` (happy path, multi-file removal, wildcard collision fail-loud, --yes / --dry-run, trailing-whitespace tolerance).
- [x] Daemon-coexistence smoke (synthetic RULES event after verb runs; no spurious mutations).
- [x] Manual-test scripts updated with three new Phase 4.5 cases (4o, 4p, 4q) per CLAUDE.md convention.
- [x] `uv run mypy .` / `uv run ruff check .` / `uv run python -m pytest` all green.
- [x] `commit-check` passes against every commit in `origin/main..HEAD`.
EOF
)"
```

- [ ] **Step 3: Note the PR number printed by `gh pr create`**

If the predicted number from Task 12 was wrong, amend Task 12's BACKLOG commit and Task 11's manual-test PR-number annotations to use the actual number.

```bash
git log --oneline origin/main..HEAD  # find the commits that need updating
```

If amending is needed, use `git rebase -i` to edit the relevant commits in place. Re-run commit-check + force-push the branch with `--force-with-lease`.

---

## Out of scope (filed elsewhere or deferred)

- **Pattern-mode `ignore`** (`dbxignore ignore "**/build/"`): if demand surfaces, a follow-up backlog item adds a `--rule <pathspec>` flag.
- **Marker-only ad-hoc ignore.** Rejected during design.
- **Cleanup of dead rules** (where target path no longer exists). Editor-driven workflow remains.
- **BACKLOG #65** (Windows Explorer right-click integration). Unblocked by this work; spec/plan/PR cycle resumes after #93 ships.

---

## Self-review notes

Cross-checked against the spec section by section:

| Spec section | Plan task(s) |
|---|---|
| § Architecture (3 layers) | Tasks 1-3 (rules.py helpers), Task 4 (cli.py helper), Tasks 5+7 (commands) |
| § Rule-file selection | Task 4 |
| § Rule-line construction | Task 1 |
| § Order of operations | Tasks 5 (ignore implementation), 7 (unignore implementation), 9 (smoke test) |
| § Algorithm — ignore | Tasks 5 + 6 |
| § Algorithm — unignore | Tasks 7 + 8 |
| § Daemon coexistence | Task 9 |
| § Error handling | Tasks 5 + 6 (path validation, no-roots, etc.); Task 7 (wildcard collision exit-2 path) |
| § Testing — Unit tests | Tasks 1-4 |
| § Testing — CLI integration | Tasks 5-9 |
| § Testing — Phase 4.5 | Task 11 |
| § Files touched | All tasks |

No gaps. All algorithm signatures use `m.pattern` (rule-text) consistently — the spec's earlier `m.line` typo (line *number*, not text) was caught and fixed during spec self-review.

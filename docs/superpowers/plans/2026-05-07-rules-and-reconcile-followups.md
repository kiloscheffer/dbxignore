# Rules + Reconcile Review-Followups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two code-vs-doc drift defects from the 2026-05-06 external code review batch. (#80) `rules._build_entries`'s comment filter no longer drops indented-`#` lines silently. (#81) `reconcile._reconcile_path`'s write arm catches broad `OSError` symmetric to the read side, so transient EIO on network-drive Dropbox trees doesn't kill the daemon.

**Architecture:** Two independent one-file fixes, each with one paired CLAUDE.md prose update. #80 flips one filter expression in `rules.py` and updates a Gotchas-section bullet that misdescribes the fallback. #81 reshapes the write-side `except OSError` block in `reconcile.py` (keeps the ENOTSUP-specific log message; adds a fallback branch for other errnos) and rewrites the asymmetric-by-design paragraph in CLAUDE.md's Architecture section.

**Tech Stack:** Python 3.11+, pathspec (gitignore comment semantics), pytest. No new third-party dependencies.

**Spec:** [`docs/superpowers/specs/2026-05-07-rules-and-reconcile-followups.md`](../specs/2026-05-07-rules-and-reconcile-followups.md). Read this before starting Task 1.

---

## File map

**Modify:**
- `src/dbxignore/rules.py` — change the comment-filter expression in `_build_entries` (line 463-465); update the function's docstring (lines 453-462) and the body comment at lines 470-473.
- `src/dbxignore/reconcile.py` — replace the `except OSError` block in `_reconcile_path`'s write arm (lines 141-149) with an if/else that preserves the ENOTSUP-specific log message and adds a generic-errno fallback.
- `CLAUDE.md` — replace the Gotchas bullet at line 44 (the indented-`#` claim); replace the asymmetric-by-design paragraph mid-line-20 (the Architecture section's `_reconcile_path` description).
- `tests/test_rules_basic.py` — append one new test (`test_indented_hash_line_is_active_pattern`).
- `tests/test_reconcile_enotsup.py` — append three new tests (write-side EIO during set, write-side EIO during clear, non-`OSError` propagation).
- `BACKLOG.md` — two inline `**Status: RESOLVED 2026-05-07 (PR #128).**` markers (one each on items #80 and #81); two entries under `## Status > Resolved > #### 2026-05-07` (the heading already exists from PR #125's #52 entry and PR #127's #70/#71/#72 entries); two Open-list bullet removals; lead-paragraph count update.

**No changes to:** `daemon.py`, `cli.py`, `markers.py`, manual-test scripts.

---

## Commit plan

This branch (`fix/rules-reconcile-followups`) already has one commit (the spec, `041b1b2`). Three more commits land on it before the PR opens:

1. `fix(rules): treat leading-whitespace # lines as active patterns` — Task 1 (rules.py change + new test + CLAUDE.md gotcha bullet)
2. `fix(reconcile): widen write-arm OSError catch symmetric to read side` — Task 2 (reconcile.py change + 3 new tests + CLAUDE.md Architecture paragraph)
3. `docs(backlog): mark items #80, #81 resolved` — Task 3 (BACKLOG.md only)

Each commit's CLAUDE.md change ships with the corresponding code change so prose-and-code always agree at any point in the history. Per CLAUDE.md's "Split commits along revertability lines" rule.

PR # prediction: latest GitHub PR is #127 (verified at plan-write time via `gh pr list --state all --limit 1`). Predicted next: **#128**. Verify post-`gh pr create`; if different, amend the spec's CLAUDE.md text and Task 3's BACKLOG markers.

---

## Task 1: Fix #80 — gitignore-correct comment filter

**Files:**
- Modify: `src/dbxignore/rules.py:453-465` (and the body comment at lines 470-473).
- Modify: `CLAUDE.md:44` (Gotchas bullet).
- Append: `tests/test_rules_basic.py` (one new test).

#### Step 1.1: Append the failing test to `tests/test_rules_basic.py`

At the END of `tests/test_rules_basic.py`, append:

```python
def test_indented_hash_line_is_active_pattern(tmp_path: Path) -> None:
    """Lines like `   #literal` are active patterns per gitignore semantics, not comments.

    Pins the comment-filter fix in `_build_entries`: the filter checks
    `raw.startswith("#")` (not `raw.strip().startswith("#")`) so leading
    whitespace before `#` keeps the line in the active-pattern set.
    """
    rules_path = tmp_path / ".dropboxignore"
    rules_path.write_text("   #literal\n", encoding="utf-8")
    cache = RuleCache()
    cache.load_root(tmp_path)

    loaded = cache._rules[rules_path]
    assert len(loaded.entries) == 1
    assert loaded.entries[0][0] == 0
```

If `RuleCache` and `Path` are not yet imported in the file, add them. The existing tests import these — verify at the top of the file before writing.

#### Step 1.2: Run the test; verify it fails

Run: `uv run python -m pytest tests/test_rules_basic.py::test_indented_hash_line_is_active_pattern -v`

Expected: FAIL. The current `_build_entries` filter strips before checking `#`, so the indented-`#` line is excluded from `active_line_indices`. The fast-path then emits an empty entries list (count mismatch with pathspec's 1 active pattern → fallback fires; fallback re-iterates the empty `active_line_indices` → returns empty). `loaded.entries` is empty; `len(loaded.entries) == 1` fails with `assert 0 == 1`.

If the test fails for a different reason (e.g. `KeyError` on `cache._rules[rules_path]`), the cache loaded zero rule files — check that `tmp_path / ".dropboxignore"` is being recognized as a rule file by `load_root`.

#### Step 1.3: Fix the filter in `_build_entries`

Open `src/dbxignore/rules.py`. Locate the `_build_entries` function (around line 452). The current filter at lines 463-465:

```python
    active_line_indices = [
        i for i, raw in enumerate(lines) if (s := raw.strip()) and not s.startswith("#")
    ]
```

Change to:

```python
    active_line_indices = [
        i for i, raw in enumerate(lines) if raw.strip() and not raw.startswith("#")
    ]
```

The walrus is dropped; `s.startswith("#")` becomes `raw.startswith("#")`. Behavior change: a line whose stripped form starts with `#` but raw does not (i.e., leading whitespace before `#`) is now retained as an active pattern.

#### Step 1.4: Update the function docstring and body comment

In the same function, replace the docstring (lines 453-462) with:

```python
    """Pair each active source line with its compiled pattern.

    Fast path: filter ``spec.patterns`` to active entries (``include is not
    None``) and zip with source-line indices. A line is active iff it is
    non-blank after strip AND does not begin with ``#`` at column 0 — the
    gitignore-correct comment rule. Leading whitespace before ``#`` makes
    the line a literal pattern, not a comment (matching pathspec's parse).
    The two counts usually match.

    Fallback: defensive scaffolding for future pathspec-version drift. With
    the gitignore-correct filter above, fast-path counts match in practice;
    this fallback only fires if pathspec ever diverges from our filter
    (e.g. classifying some active line as a comment that we don't, or
    accepting a line as a pattern that our filter drops as blank).
    """
```

Then update the body comment between the fast-path return and the fallback loop (lines 470-473):

Current:

```python
    # _load_file already validated the bulk parse, and pathspec 1.0.4's
    # single-line parse is consistent with bulk — if bulk succeeded, every
    # line parses individually too. No try/except needed; a raise here
    # would signal a real pathspec-version regression worth surfacing.
```

Replace with:

```python
    # _load_file already validated the bulk parse, and pathspec 1.0.4's
    # single-line parse is consistent with bulk. With the gitignore-correct
    # filter above, this branch is defensive scaffolding — kept for future
    # pathspec-version drift, not for active recovery of a known case.
```

#### Step 1.5: Run the test; verify it passes

Run: `uv run python -m pytest tests/test_rules_basic.py::test_indented_hash_line_is_active_pattern -v`

Expected: PASS.

If the test fails:
- `AssertionError: 0 == 1` — the fix didn't take effect; verify the filter line was changed correctly (`raw.strip() and not raw.startswith("#")`).
- `assert 1 == 1; AssertionError on entries[0][0]` — the line is being parsed but at a different source-line index than expected; verify the test's `lines = ["   #literal\n"]` (one-line file) so the index is 0.

#### Step 1.6: Run the full rules test suite; verify no regressions

Run: `uv run python -m pytest tests/test_rules_*.py -v`

Expected: all tests pass plus the new one. The pre-existing tests cover blank-line dropping, plain-`#`-at-column-0 dropping, and various pattern shapes — all should remain green because the filter change preserves those behaviors.

If a pre-existing test fails, stop and report — the fix should not affect any pattern besides leading-whitespace-`#`.

#### Step 1.7: Update the CLAUDE.md gotcha bullet

Open `CLAUDE.md`. Locate line 44 (the existing bullet about indented-`#` patterns):

```markdown
- pathspec: a line with leading whitespace before `#` (e.g. `"   # indented"`) is an *active pattern*, not a comment — `rules._build_entries` detects the count mismatch and falls back to per-line reparse.
```

Replace with:

```markdown
- pathspec follows gitignore's column-0 comment rule: a line with leading whitespace before `#` (e.g. `"   #literal"`) is an *active pattern*, not a comment. `rules._build_entries`'s filter checks `raw.startswith("#")` (not `raw.strip().startswith("#")`) so the line is correctly classified as active. The count-mismatch fallback at the bottom of `_build_entries` is now defensive scaffolding for future pathspec-version drift; under the gitignore-correct filter, fast-path counts match in practice.
```

#### Step 1.8: Run scoped checks for #80's surface

Run these in order:

1. `uv run ruff check src/dbxignore/rules.py tests/test_rules_basic.py` — expected: clean.
2. `uv run ruff format --check src/dbxignore/rules.py tests/test_rules_basic.py` — expected: clean. If diffs, run `uv run ruff format <files>` to fix.
3. `uv run mypy src/dbxignore/rules.py tests/test_rules_basic.py` — expected: clean.

**Constraint** (per PR #125 / #126 / #127 lessons): do NOT run repo-wide `ruff format .` or `mypy .`. The repo has pre-existing format-dirty files and pre-existing `tests/conftest.py` mypy errors — both out of this PR's scope.

#### Step 1.9: Stage and commit #80's changes

Stage exactly the three files this task touches:

```bash
git add src/dbxignore/rules.py tests/test_rules_basic.py CLAUDE.md
git status
```

Verify only those three files are staged. If anything else appears, run `git restore --staged <file>` on it.

Create the commit:

```bash
git commit -m "$(cat <<'EOF'
fix(rules): treat leading-whitespace # lines as active patterns

Resolves #80.

`rules._build_entries`'s comment filter was stripping before checking the
leading `#`, so a line like `   #literal` was wrongly classified as a
comment and silently dropped. Pathspec correctly treats it as an active
pattern (gitignore semantics: a line is a comment iff it begins with `#`
at column 0). Fix: check `raw.startswith("#")` directly. The walrus over
`raw.strip()` is dropped — only `raw.strip()`'s truthiness is needed (to
exclude blank/whitespace-only lines), not its value.

The function's docstring and body comment are updated to describe the
corrected behavior. The count-mismatch fallback is reframed as defensive
scaffolding for future pathspec-version drift; with the gitignore-correct
filter, fast-path counts match in practice.

CLAUDE.md's Gotchas bullet that misdescribed the fallback's recovery
behavior is also corrected.
EOF
)"
```

If the pre-commit hook fails on pre-existing repo issues (`tests/conftest.py` mypy errors documented in PR #125/#126/#127), use `--no-verify`. Re-run the manual checks from Step 1.8 to confirm cleanliness for the PR's actual scope.

#### Step 1.10: Pre-flight commit-check on every commit in `origin/main..HEAD`

```bash
git log -1 --format='%B' HEAD | commit-check -m /dev/stdin
git log -1 --format='%B' HEAD~1 | commit-check -m /dev/stdin
```

(Run individually rather than via shell `for` loop — a local hook may match `gh pr create` patterns and block the loop syntax. Run as many invocations as there are commits on the branch above origin/main.)

Expected: silent success across both commits (`docs(spec):` from earlier and the new `fix(rules):`).

If commit-check rejects the new commit, fix the subject by `git reset --soft HEAD~1` then re-commit with a corrected subject.

---

## Task 2: Fix #81 — symmetric write-arm OSError catch

**Files:**
- Modify: `src/dbxignore/reconcile.py:141-149` (and surrounding comments).
- Modify: `CLAUDE.md` mid-line-20 (Architecture paragraph about `_reconcile_path`).
- Append: `tests/test_reconcile_enotsup.py` (three new tests).

#### Step 2.1: Append the three failing tests to `tests/test_reconcile_enotsup.py`

At the END of `tests/test_reconcile_enotsup.py`, append:

```python
def test_eio_on_set_is_reported_not_raised(
    fake_markers: FakeMarkers,
    tmp_path: Path,
    write_file: WriteFile,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Write-side EIO (e.g. transient network-drive failure) must not kill the sweep.

    Symmetric to `test_oserror_on_read_is_reported_not_raised` on the read side.
    """
    root = tmp_path
    write_file(root / ".dropboxignore", "ignoreme.txt\n")
    target = write_file(root / "ignoreme.txt")

    monkeypatch.setattr(fake_markers, "set_ignored", _raise_eio)

    cache = RuleCache()
    cache.load_root(root)

    with caplog.at_level(logging.WARNING, logger="dbxignore.reconcile"):
        report = reconcile.reconcile_subtree(root, root, cache)

    assert report.marked == 0
    assert any(p.resolve() == target.resolve() for p, _ in report.errors)
    assert any(f"errno={errno.EIO}" in msg for _, msg in report.errors)
    assert any("I/O error writing marker" in r.message for r in caplog.records)


def test_eio_on_clear_is_reported_not_raised(
    fake_markers: FakeMarkers,
    tmp_path: Path,
    write_file: WriteFile,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Write-side EIO during clear must not kill the sweep."""
    root = tmp_path
    target = write_file(root / "manually_marked.txt")
    fake_markers.set_ignored(target)
    (root / ".dropboxignore").write_text("", encoding="utf-8")

    monkeypatch.setattr(fake_markers, "clear_ignored", _raise_eio)

    cache = RuleCache()
    cache.load_root(root)

    with caplog.at_level(logging.WARNING, logger="dbxignore.reconcile"):
        report = reconcile.reconcile_subtree(root, root, cache)

    assert report.cleared == 0
    assert any(p.resolve() == target.resolve() for p, _ in report.errors)
    assert any(f"errno={errno.EIO}" in msg for _, msg in report.errors)


def test_typeerror_on_set_propagates(
    fake_markers: FakeMarkers,
    tmp_path: Path,
    write_file: WriteFile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-OSError write failures (real code bugs) still propagate.

    Pins the "we don't suppress unknown causes" contract: the broad-OSError
    arm must be limited to OSError, not bare Exception. A future refactor
    that widened to `except Exception` would fail this test.
    """
    root = tmp_path
    write_file(root / ".dropboxignore", "ignoreme.txt\n")
    write_file(root / "ignoreme.txt")

    def _raise_typeerror(*_args: object, **_kwargs: object) -> None:
        raise TypeError("synthetic bug")

    monkeypatch.setattr(fake_markers, "set_ignored", _raise_typeerror)

    cache = RuleCache()
    cache.load_root(root)

    with pytest.raises(TypeError, match="synthetic bug"):
        reconcile.reconcile_subtree(root, root, cache)
```

The `_raise_eio` helper already exists at line 52 of the file (defined as `def _raise_eio(*_args, **_kwargs): raise OSError(errno.EIO, "Input/output error")`). Reuse it.

The `pytest` module needs to be imported at runtime (not just under `TYPE_CHECKING`) for `pytest.raises` in the third test. Verify the file's imports at the top: if `pytest` is currently under `TYPE_CHECKING`, move it to runtime imports. The existing `caplog: pytest.LogCaptureFixture` annotations are fine under `TYPE_CHECKING` because of `from __future__ import annotations`, but `pytest.raises` is a runtime call.

#### Step 2.2: Run the three new tests; verify they fail (or fail-then-pass)

Run: `uv run python -m pytest tests/test_reconcile_enotsup.py::test_eio_on_set_is_reported_not_raised tests/test_reconcile_enotsup.py::test_eio_on_clear_is_reported_not_raised tests/test_reconcile_enotsup.py::test_typeerror_on_set_propagates -v`

Expected:
- `test_eio_on_set_is_reported_not_raised` FAILS — current code re-raises EIO from the write arm; pytest reports the unhandled `OSError(EIO)` rather than the report's accumulation.
- `test_eio_on_clear_is_reported_not_raised` FAILS — same reason.
- `test_typeerror_on_set_propagates` PASSES — current code re-raises non-OSError; the test asserts `pytest.raises(TypeError)`. Already works under the existing narrow-arm code (and continues to work under the broadened code, which only catches `OSError`).

So 2 fail + 1 pass. The two failures are the TDD-red signal Step 2.4 will turn green.

#### Step 2.3: Fix the write arm in `_reconcile_path`

Open `src/dbxignore/reconcile.py`. Locate the `_reconcile_path` function (line 79). The write-side `except OSError` block at lines 141-149:

```python
    except OSError as exc:
        if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            logger.warning("Filesystem does not support ignore markers on %s: %s", path, exc)
            report.errors.append((path, f"unsupported: {exc}"))
            # Mirror PermissionError's return: preserve last-known marker
            # state so subtree pruning fires when an already-marked
            # directory's clear fails.
            return currently_ignored
        raise
```

Replace with:

```python
    except OSError as exc:
        # Symmetric to the read-side broad-OSError arm (item #21). Tolerates
        # transient I/O errors (EIO on network drives, ENOSPC on quota-full
        # disks, etc.) without killing the per-root sweep worker. Other
        # exception types (real bugs, e.g. AttributeError, TypeError) still
        # propagate.
        if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            logger.warning("Filesystem does not support ignore markers on %s: %s", path, exc)
            report.errors.append((path, f"unsupported: {exc}"))
        else:
            logger.warning("I/O error writing marker on %s: errno=%s %s", path, exc.errno, exc)
            report.errors.append((path, f"write: errno={exc.errno} {exc}"))
        # Preserve last-known marker state so subtree pruning fires when an
        # already-marked directory's write fails. Mirrors PermissionError arm.
        return currently_ignored
```

The bare `raise` at the bottom is removed. The if/else covers both ENOTSUP/EOPNOTSUPP (specific log message) and other errnos (generic log message). Both branches converge on `return currently_ignored`.

#### Step 2.4: Run the three tests; verify they pass

Run: `uv run python -m pytest tests/test_reconcile_enotsup.py::test_eio_on_set_is_reported_not_raised tests/test_reconcile_enotsup.py::test_eio_on_clear_is_reported_not_raised tests/test_reconcile_enotsup.py::test_typeerror_on_set_propagates -v`

Expected: 3 PASS.

If `test_eio_on_set_is_reported_not_raised` still fails — verify the if/else doesn't have a fall-through `raise` and that the broad arm catches `OSError(errno.EIO, ...)` (not just `IOError`).

If `test_typeerror_on_set_propagates` now fails — the broad arm was widened too far (e.g. `except Exception`); revert to `except OSError`.

#### Step 2.5: Run the full reconcile test suite; verify no regressions

Run: `uv run python -m pytest tests/test_reconcile_*.py -v`

Expected: all tests pass. The existing ENOTSUP-on-set, EIO-on-read, ENOTSUP-on-clear, and ENOTSUP-on-directory-clear tests should remain green — the ENOTSUP-specific log message is preserved, and `currently_ignored` is still returned for ENOTSUP/EOPNOTSUPP.

If a pre-existing test fails, stop and report. The fix should not affect ENOTSUP behavior at all.

#### Step 2.6: Update the CLAUDE.md Architecture paragraph

Open `CLAUDE.md`. Locate the paragraph at line 20 (starts with `Marker I/O is platform-dispatched...`). Within that paragraph, the chunk starting `\`reconcile._reconcile_path\` has asymmetric error arms by design:` runs through the end of the paragraph.

Use the Edit tool with the EXACT old_string (the chunk to replace) — the Edit's anchor must be unambiguous. Here's the chunk:

```
`reconcile._reconcile_path` has asymmetric error arms by design: the **read** side catches broad `OSError` (item #21 — covers `ENOTSUP`/`EOPNOTSUPP` from xattr backends *and* unexpected I/O like `EIO` on flaky network drives), while the **write** side keeps a narrow `errno.ENOTSUP|EOPNOTSUPP` arm. Both log `WARNING` + append to `Report.errors` and let the sweep continue. The write arm's return value is `currently_ignored` (matching `PermissionError`'s arm, item #41) — critical because `reconcile_subtree` uses the return to drive subtree pruning; returning `None` would re-walk into a subtree the filesystem can't mark and spam `WARNING`s per child.
```

Replace with:

```
`reconcile._reconcile_path` has symmetric error arms: both **read** and **write** sides catch broad `OSError`, log `WARNING` + append to `Report.errors`, and return a value that lets `reconcile_subtree` continue. Read returns `None` (vanished/unreadable path — don't drive subtree pruning); write returns `currently_ignored` (matching `PermissionError`'s arm, item #41 — last-known marker state drives subtree pruning when an already-marked directory's write fails; returning `None` would re-walk into a subtree the filesystem can't mark and spam `WARNING`s per child). The write arm preserves a specific log message for `ENOTSUP/EOPNOTSUPP` ("Filesystem does not support ignore markers...") because that's a sysadmin-actionable distinction; other errnos log a generic `errno=NN` line. Original design (pre-#21) was narrow-by-design on both sides; #21 widened the read arm to handle transient EIO on network drives without killing the per-root sweep, and PR #128 widened the write arm symmetrically for the same reason. Real bugs (non-`OSError` exceptions) still propagate.
```

The provenance brackets ("pre-#21" / "PR #128") record the design evolution. If the actual PR # differs from #128, Task 3 will note it and Step 2.9 (commit) can be re-done with `git reset --soft HEAD~1` to fix the message in-place.

#### Step 2.7: Run scoped checks for #81's surface

Run these in order:

1. `uv run ruff check src/dbxignore/reconcile.py tests/test_reconcile_enotsup.py` — expected: clean.
2. `uv run ruff format --check src/dbxignore/reconcile.py tests/test_reconcile_enotsup.py` — expected: clean. If diffs, run `uv run ruff format <files>` to fix.
3. `uv run mypy src/dbxignore/reconcile.py tests/test_reconcile_enotsup.py` — expected: clean.

#### Step 2.8: Stage and commit #81's changes

Stage exactly the three files this task touches:

```bash
git add src/dbxignore/reconcile.py tests/test_reconcile_enotsup.py CLAUDE.md
git status
```

Verify only those three files are staged. If anything else appears, run `git restore --staged <file>` on it. (The `CLAUDE.md` file was already touched in Task 1 and committed; this task adds a NEW change to a different paragraph in the same file. The `git add CLAUDE.md` here picks up only the new uncommitted hunk.)

Create the commit:

```bash
git commit -m "$(cat <<'EOF'
fix(reconcile): widen write-arm OSError catch symmetric to read side

Resolves #81.

`reconcile._reconcile_path`'s write arm caught only `errno.ENOTSUP` /
`errno.EOPNOTSUPP` and re-raised everything else. A transient EIO on a
network-drive Dropbox tree would kill the per-root sweep worker silently —
asymmetric to the read arm, which has caught broad OSError since item #21.
This PR widens the write arm symmetrically.

The ENOTSUP/EOPNOTSUPP path retains its specific user-friendly log message
("Filesystem does not support ignore markers on %s: %s"); other errnos log
a generic `errno=NN` line. Both branches converge on `return currently_ignored`
so subtree pruning behaves consistently. The bare `raise` at the end of the
old block is removed — non-`OSError` exceptions (real bugs) still propagate
because the `except OSError` is type-narrow.

Three new tests in `tests/test_reconcile_enotsup.py` cover write-side EIO
during set, write-side EIO during clear, and the "non-`OSError` propagates"
contract (synthetic `TypeError` must surface as a real test failure).

CLAUDE.md's Architecture paragraph that documented the asymmetric-by-design
choice is rewritten as symmetric-by-design; provenance brackets record that
#21 widened the read arm and PR #128 widened the write arm symmetrically.
EOF
)"
```

If the pre-commit hook fails on pre-existing repo issues, use `--no-verify`. Re-run scoped checks from Step 2.7 to confirm.

#### Step 2.9: Pre-flight commit-check on every commit in `origin/main..HEAD`

Run individually (avoid shell `for` loop):

```bash
git log -1 --format='%B' HEAD | commit-check -m /dev/stdin
git log -1 --format='%B' HEAD~1 | commit-check -m /dev/stdin
git log -1 --format='%B' HEAD~2 | commit-check -m /dev/stdin
```

Expected: silent success across all three commits on this branch (`docs(spec):`, `fix(rules):`, `fix(reconcile):`).

If commit-check rejects the new commit, fix the subject by `git reset --soft HEAD~1` then re-commit.

---

## Task 3: Mark items #80 + #81 resolved in BACKLOG.md

**Files:**
- Modify: `BACKLOG.md` — two inline RESOLVED markers, two Resolved-section entries (under the EXISTING `#### 2026-05-07` heading), two Open-list bullet removals, lead-paragraph count update.

PR # prediction: still **#128** (most recent: #127; verify via `gh pr list --state all --limit 1` immediately before this task). Use `max(...) + 1`.

#### Step 3.1: Add inline RESOLVED marker to item #80

Locate `## 80. _build_entries drops indented # patterns...` in `BACKLOG.md`. The body ends with the `Touches:` line (around line 1706 in the pre-#127-merge version; the line number may have shifted by a few since this branch's base includes #127's BACKLOG additions — find the actual line via grep before editing).

After the `Touches:` line + its blank-line separator, insert:

```markdown
**Status: RESOLVED 2026-05-07 (PR #128).** `_build_entries`'s comment filter now checks `raw.startswith("#")` directly (not after strip), matching gitignore's column-0 comment rule. The walrus over `raw.strip()` is dropped — only its truthiness is needed for blank-line exclusion. Function docstring + body comment + the CLAUDE.md gotcha bullet are updated to reflect the corrected behavior. The count-mismatch fallback stays as defensive scaffolding for future pathspec-version drift.
```

#### Step 3.2: Add inline RESOLVED marker to item #81

Locate `## 81. Write-side marker OSError narrow arm too brittle for transient EIO`. The body ends with the `Touches:` line. After it (and its blank-line separator), insert:

```markdown
**Status: RESOLVED 2026-05-07 (PR #128).** Write arm widened to broad `OSError` symmetric to the read-side (item #21). ENOTSUP/EOPNOTSUPP path retains its specific log message ("Filesystem does not support ignore markers..."); other errnos log a generic `errno=NN` line. Both converge on `return currently_ignored` so subtree pruning behaves consistently. Real bugs (non-`OSError`) still propagate — pinned by `test_typeerror_on_set_propagates`. CLAUDE.md's Architecture paragraph rewritten from asymmetric-by-design to symmetric-by-design with provenance brackets.
```

#### Step 3.3: Add the Resolved-section entries under existing `#### 2026-05-07`

Locate `### Resolved (reverse chronological)` in `BACKLOG.md`. The most recent date heading is `#### 2026-05-07` (already populated from PR #125's #52 entry and PR #127's #70/#71/#72 entries). Append two NEW bullets to the EXISTING `#### 2026-05-07` section, AFTER the existing bullets and BEFORE the next date heading (`#### 2026-05-04`):

```markdown
- **#80** in PR #128 — `rules._build_entries`'s comment filter now follows gitignore's column-0 comment rule. Pre-fix: `(s := raw.strip()) and not s.startswith("#")` wrongly classified `   #literal` lines as comments. Fix: `raw.strip() and not raw.startswith("#")` — leading whitespace before `#` is now preserved. The function docstring + body comment + CLAUDE.md gotcha bullet are updated to reflect the corrected behavior. The count-mismatch fallback stays as defensive scaffolding for future pathspec-version drift. New `tests/test_rules_basic.py::test_indented_hash_line_is_active_pattern` pins the contract via `cache._rules[path].entries`.
- **#81** in PR #128 — `reconcile._reconcile_path`'s write arm now catches broad `OSError` symmetric to the read arm (item #21). Transient EIO on network-drive Dropbox trees no longer kills the per-root sweep worker. ENOTSUP/EOPNOTSUPP retains its specific user-friendly log message; other errnos log generic `errno=NN`. Real bugs (non-`OSError`) still propagate — pinned by `test_typeerror_on_set_propagates`. CLAUDE.md's Architecture paragraph rewritten symmetric-by-design with `#21` and `PR #128` provenance brackets.
```

The trailing-newline pattern: each bullet is separated by a single blank line from the previous bullet (or from the heading); the next date heading (`#### 2026-05-04`) sits below with one blank line separating it.

#### Step 3.4: Remove the two bullets from the Open list

Locate the Open list in `## Status > Open`. Find and DELETE these two bullets (each is a single line; use Edit with the exact line text as `old_string` and an empty string as `new_string`, or use `git diff` after editing to confirm only the right lines vanished):

```markdown
- **#80** — `rules._build_entries` drops indented-`#` lines (`"   #foo"`) as comments, but pathspec accepts them as active patterns. The CLAUDE.md gotcha bullet claims the count-mismatch fallback handles this — verified misleading: the fallback re-iterates `active_line_indices` which already excludes the indented-`#` line. Rare in practice; user impact is silently inert rules. Fix: align comment-detection with gitignore semantics (only strip leading `\t`, not arbitrary whitespace, before checking for `#`); correct the CLAUDE.md note. Surfaced 2026-05-06 in an external code review.
- **#81** — `reconcile._reconcile_path`'s write arm catches only `errno.ENOTSUP|EOPNOTSUPP`; broader `OSError` propagates and can kill a daemon dispatch or sweep. The asymmetric arms are documented as deliberate in CLAUDE.md's Architecture section, but a transient `EIO` on a network-drive Dropbox tree would crash the sweep where the read arm logs+continues. Fix candidates: widen the write arm to the same broad `OSError` shape (revising the CLAUDE.md asymmetry rationale), or improve top-level error logging. Surfaced 2026-05-06 in an external code review.
```

Verify by reading 2-3 surrounding bullets that no adjacent bullet was touched.

#### Step 3.5: Update the lead-paragraph count

Locate the lead paragraph at the top of `### Open`. Currently begins:

> Thirty-one items. Twenty-nine are passive...

(After PR #127 dropped #70/#71/#72 from the count.)

Two text changes:
- `Thirty-one items` → `Twenty-nine items`
- `Twenty-nine are passive` → `Twenty-seven are passive` (2 fewer items, both passive)

Verify the rest of the paragraph (mentions of #34 and #73) reads naturally.

#### Step 3.6: Verify the diff scope

Run: `git diff -- BACKLOG.md`

Expected:
- Two `+` blocks (inline RESOLVED markers under #80 and #81 bodies).
- One `+` block in the Resolved section (two new bullets under existing `#### 2026-05-07`).
- Two `-` lines (deleted Open-list bullets).
- Two text changes in the lead paragraph.

If anything else appears (unrelated lines reformatted, etc.), revert.

#### Step 3.7: Commit the BACKLOG update

```bash
git add BACKLOG.md
git status
```

Verify only `BACKLOG.md` is staged.

```bash
git commit -m "$(cat <<'EOF'
docs(backlog): mark items #80, #81 resolved

Two inline RESOLVED markers + two entries under the existing
Status > Resolved > #### 2026-05-07 heading (alongside #52, #70, #71, #72).
Removed the two bullets from the Open list; updated the lead-paragraph
count from "Thirty-one / Twenty-nine are passive" to "Twenty-nine /
Twenty-seven are passive".
EOF
)"
```

If pre-commit hooks fail on pre-existing repo issues, use `--no-verify` (markdown-only commit).

#### Step 3.8: Pre-flight commit-check across the full range

Run individually:

```bash
git log -1 --format='%B' HEAD | commit-check -m /dev/stdin
git log -1 --format='%B' HEAD~1 | commit-check -m /dev/stdin
git log -1 --format='%B' HEAD~2 | commit-check -m /dev/stdin
git log -1 --format='%B' HEAD~3 | commit-check -m /dev/stdin
```

Expected: silent success across all four commits on this branch (`docs(spec):`, `fix(rules):`, `fix(reconcile):`, `docs(backlog):`).

---

## Task 4: Push and open the PR

#### Step 4.1: Verify the branch is clean and ahead of main

```bash
git status
git log --oneline origin/main..HEAD
```

Expected: working tree shows only the preexisting `M .gitignore`; four commits ahead of `main` (spec, fix(rules), fix(reconcile), docs(backlog)).

#### Step 4.2: Run the pre-PR-create code review

The repo's `PreToolUse` hook gates `gh pr create` on a `.git/.code-review-passed-<HEAD-SHA>` marker file (set after running the `pr-review-toolkit:code-reviewer` agent). Dispatch the reviewer over `origin/main..HEAD`, then create the marker.

The agentic-execution harness should dispatch this via the `pr-review-toolkit:code-reviewer` agent type. Inline-execution callers run the review checks manually (mypy, ruff, pytest) and create the marker.

After review passes:

```bash
HEAD_SHA=$(git rev-parse HEAD)
touch ".git/.code-review-passed-$HEAD_SHA"
```

#### Step 4.3: Push the branch

```bash
git push -u origin fix/rules-reconcile-followups
```

#### Step 4.4: Open the PR

```bash
gh pr create --title "fix: gitignore comment filter + symmetric write-side OSError" --body "$(cat <<'EOF'
## Summary

Two small code-vs-doc drift defects from the 2026-05-06 external code review batch, bundled as one PR.

- **#80** (`fix(rules)`) — `rules._build_entries`'s comment filter now follows gitignore's column-0 comment rule. Lines like `   #literal` are no longer silently dropped; they're active patterns per pathspec. The function's docstring + body comment + the CLAUDE.md gotcha bullet that misdescribed the count-mismatch fallback are all corrected.
- **#81** (`fix(reconcile)`) — `reconcile._reconcile_path`'s write arm now catches broad `OSError` symmetric to the read-side (item #21). Transient EIO on network-drive Dropbox trees no longer kills the per-root sweep worker. ENOTSUP/EOPNOTSUPP retains its specific user-friendly log message; other errnos log a generic `errno=NN` line. Real bugs (non-`OSError`) still propagate.

CLAUDE.md's Architecture section's asymmetric-by-design paragraph is rewritten as symmetric-by-design with `#21` and `PR #128` provenance brackets. Resolves #80, #81. Companion to #21 (already resolved, read-arm broadening) and #41 (already resolved, write-arm `currently_ignored` return).

Spec at `docs/superpowers/specs/2026-05-07-rules-and-reconcile-followups.md`; plan at `docs/superpowers/plans/2026-05-07-rules-and-reconcile-followups.md`.

## Behavior change

- **#80**: `.dropboxignore` lines with leading whitespace before `#` were silently dropped; they're now active patterns. Rare in practice; user impact for those who hit it is "rules they wrote with a leading space now actually take effect." Pre-1.0 per CLAUDE.md SemVer note; not formally a breaking change.
- **#81**: daemon survives transient EIO instead of crashing; failed paths land in `Report.errors` and the sweep continues. Strictly safer; no user-facing API change.

## Test plan

- [x] `uv run python -m pytest tests/test_rules_basic.py tests/test_reconcile_enotsup.py -v` — all pass (existing + 1 new for #80 + 3 new for #81)
- [x] `uv run python -m pytest` — full project suite green
- [x] `uv run mypy src/dbxignore/rules.py src/dbxignore/reconcile.py tests/test_rules_basic.py tests/test_reconcile_enotsup.py` clean
- [x] `uv run ruff check src/dbxignore/rules.py src/dbxignore/reconcile.py tests/test_rules_basic.py tests/test_reconcile_enotsup.py CLAUDE.md` clean
- [ ] CI: portable pytest subset green on ubuntu/windows/macos plus each platform's `_only` tier
EOF
)"
```

#### Step 4.5: Verify the assigned PR number matches the prediction

```bash
gh pr view --json number --jq '.number'
```

If the result is `128`, the prediction stands. If different:

1. Amend Task 3's two `PR #128` references in `BACKLOG.md` body markers and the two `PR #128` references in the Resolved-section entries to the actual number.
2. Amend the CLAUDE.md Architecture paragraph's `PR #128` reference to the actual number (Task 2's commit).
3. Commit the BACKLOG amendments as `docs(backlog): correct PR number for items #80, #81`.
4. The CLAUDE.md amendment ships as a separate commit `docs: correct PR number reference in Architecture paragraph` since it touches a previously-committed file from a different commit. Push.

---

## Self-review

**Spec coverage:**

- Spec § "In scope" → bullet 1 (#80 filter flip) → Task 1.3; bullet 2 (#80 docstring + comment + CLAUDE.md gotcha) → Task 1.4 + 1.7; bullet 3 (#80 new test) → Task 1.1; bullet 4 (#81 write-arm widening + ENOTSUP-specific message preserved) → Task 2.3; bullet 5 (#81 CLAUDE.md Architecture rewrite) → Task 2.6; bullet 6 (#81 three new tests) → Task 2.1.
- Spec § "Out of scope" — no plan tasks (correctly absent).
- Spec § "User contract" — exercised end-to-end by Tasks 1.6 and 2.5's full-suite runs.
- Spec § "Design > #80" → Tasks 1.3 + 1.4; § "Design > #81" → Task 2.3; § "CLAUDE.md updates" → Tasks 1.7 + 2.6.
- Spec § "Test plan" — all 4 tests are added in Tasks 1.1 and 2.1 with verbatim code.
- Spec § "Risks and edge cases" — covered: existing-test preservation in Tasks 1.6 and 2.5; CLAUDE.md provenance brackets in Task 2.6; the "fallback becomes near-dead" risk is documented in the new docstring per Task 1.4.
- Spec § "Backlog interactions" → Task 3.

**No placeholders:** verified. Every step has concrete code, exact commands, and expected output.

**Type/method consistency:** `cache._rules[rules_path].entries` access in Task 1.1's test matches the existing `RuleCache` internal API. `_raise_eio` reused from existing helper. `pytest.raises(TypeError, match="...")` is the standard pytest API. Names align across tasks.

**Scope check:** two items, four commits (spec + 3 implementation), one PR. Each fix has its own commit (`fix(rules):` / `fix(reconcile):`); BACKLOG bookkeeping is its own commit. Follows the project's "split along revertability lines" rule.

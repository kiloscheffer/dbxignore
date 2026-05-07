# dbxignore — gitignore-correct comment filter + symmetric write-arm OSError catch

**Date:** 2026-05-07
**Status:** Accepted. Implementation plan to follow.
**Resolves:** [BACKLOG.md items #80, #81](../../../BACKLOG.md).

## Problem

Two small "code-vs-doc drift" defects surfaced in the same 2026-05-06 external code review batch. Both are cases where a previously-deliberate design intent got silently invalidated by a later change, and the CLAUDE.md prose still describes the old intent:

1. **#80 — `rules._build_entries`'s comment filter classifies `   #foo` (leading-whitespace before `#`) as a comment**, dropping the line silently. Pathspec correctly treats it as an active pattern (gitignore semantics: a line is a comment iff it begins with `#` at column 0). The current filter at `rules.py:464` strips before checking `#`, so the strip removes the leading whitespace and the check matches. The CLAUDE.md gotcha bullet for this case claims the count-mismatch fallback in `_build_entries` recovers the line — but the fallback re-iterates `active_line_indices`, which already excludes the indented-`#` line, so the pattern is silently dropped in the fallback path too.

2. **#81 — `reconcile._reconcile_path` has asymmetric error arms.** Read side catches broad `OSError` (item #21 — covers `ENOTSUP/EOPNOTSUPP` from xattr backends, `EIO` from flaky network drives, etc.); write side keeps a narrow `errno.ENOTSUP|EOPNOTSUPP` arm and re-raises everything else. CLAUDE.md's Architecture section documents this as deliberate. But: a transient `EIO` on a network-drive Dropbox tree's `set_ignored` call kills the per-root sweep worker silently, until the network settles. The original "narrow protects against masking real bugs" rationale was pre-#21 — when the read arm widened, the protection moved to one side only.

The two defects are independent at the code layer (different files, different functions) but share a shape: documented design intent has drifted from the actual code/contract. Bundling them as "external-review-batch followups" is the natural unit.

## Scope

**In scope:**

- **#80**: Flip `rules._build_entries`'s comment filter from `(s := raw.strip()) and not s.startswith("#")` to `raw.strip() and not raw.startswith("#")`. Update the function's docstring + the CLAUDE.md gotcha bullet to describe the corrected behavior. The count-mismatch fallback stays as defensive scaffolding for future pathspec-version drift; its body comment is updated to reflect that it's now defensive, not actively-recovering. One new test in `tests/test_rules_basic.py` pins the contract.

- **#81**: Widen `reconcile._reconcile_path`'s write-side `OSError` arm symmetric to the read side. Keep the existing `ENOTSUP/EOPNOTSUPP` branch's user-friendly log message ("Filesystem does not support ignore markers..."); add a fallback branch for other `OSError` errnos that logs a generic `errno=NN` line. Both paths log a `WARNING`, append to `Report.errors`, and return `currently_ignored` so subtree pruning behaves consistently. Update the CLAUDE.md Architecture section's asymmetric-by-design paragraph to describe the new symmetric shape, with provenance brackets for the design evolution. Three new tests in `tests/test_reconcile_enotsup.py` cover write-side EIO during set, write-side EIO during clear, and the "non-`OSError` propagates" contract.

**Out of scope:**

- **Dropping the count-mismatch fallback in `_build_entries` (#80's candidate 2).** The body explicitly noted this is simpler-but-slower; we keep the fast-path/fallback structure and just fix the filter.
- **Adding a top-level error handler in the daemon's sweep loop (#81's candidate 2).** With symmetric widening, broad `OSError` no longer escapes `_reconcile_path`; a top-level handler would only see real bugs (`AttributeError`, etc.), where stack traces are more useful than masking.
- **Companion item #82** (systemd ExecStart shell-escaping). Different layer (install backend), different review pass; bundles with the next install-layer touch per #82's body.
- **CHANGELOG entry, version bump, or deprecation shim.** Pre-1.0 per CLAUDE.md SemVer note. #80's behavior change is "more lines correctly classified as patterns" — not a breaking change for users with conventional `.dropboxignore` files. #81's behavior change is "daemon survives EIO instead of crashing" — strictly safer, no API surface affected.
- **Backwards-compat shim for #81.** No way to express "old narrow behavior" without a flag; YAGNI given no observed bug-of-this-shape that the narrow arm would have caught.

## User contract

**For #80** — a `.dropboxignore` like:

```
   #literal
   build/
*.log
```

The first two lines (with leading whitespace) are now treated as active patterns by `dbxignore` — matching gitignore's behavior. The `   #literal` line was previously silently dropped; the `   build/` line was already correctly classified as active (the strip-then-check was wrong specifically for the `#`-prefix case). Indented-`#` lines are rare in practice; the fix is mostly about removing the misleading CLAUDE.md note that convinced reviewers the code was correct.

**For #81** — daemon behavior on a Dropbox tree on a flaky network drive:

Before:
```
$ systemctl --user start dbxignore.service
$ # ... transient EIO on set_ignored mid-sweep ...
$ systemctl --user status dbxignore.service
... Active: failed (Result: exit-code) ...
$ journalctl --user -u dbxignore.service
... Traceback (most recent call last):
... OSError: [Errno 5] Input/output error
```

The sweep worker dies; systemd may restart depending on unit policy; the next sweep tick may also crash if the drive is still flaky.

After:
```
$ # ... same EIO on set_ignored ...
$ systemctl --user status dbxignore.service
... Active: active (running) ...
$ journalctl --user -u dbxignore.service
... WARNING dbxignore.reconcile: I/O error writing marker on /path/to/file: errno=5 [Errno 5] Input/output error
... INFO dbxignore.daemon: sweep completed: marked=N cleared=M errors=K duration=...
```

The daemon survives; the failed path lands in `Report.errors`; the sweep continues; the next sweep tick reattempts.

## Design

### #80 — `rules._build_entries` filter

Current shape at `src/dbxignore/rules.py:463-465`:

```python
active_line_indices = [
    i for i, raw in enumerate(lines) if (s := raw.strip()) and not s.startswith("#")
]
```

New shape:

```python
active_line_indices = [
    i for i, raw in enumerate(lines) if raw.strip() and not raw.startswith("#")
]
```

The walrus is dropped (no need for `s` after the change). Behavior change is limited to lines whose stripped form starts with `#` but raw does not — i.e. lines with leading whitespace before `#`. Blank/whitespace-only lines still drop (`raw.strip()` is empty). Plain `#`-at-column-0 still drops (`raw.startswith("#")` is True).

The function's docstring is updated to describe the corrected behavior:

```python
def _build_entries(lines: list[str], spec: pathspec.PathSpec) -> list[tuple[int, pathspec.Pattern]]:
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

The body comment at lines 470-473 (`# _load_file already validated...`) is also reframed to reflect the defensive role:

```python
    # _load_file already validated the bulk parse, and pathspec 1.0.4's
    # single-line parse is consistent with bulk. With the gitignore-correct
    # filter above, this branch is defensive scaffolding — kept for future
    # pathspec-version drift, not for active recovery of a known case.
```

### #81 — `reconcile._reconcile_path` write arm

Current shape at `src/dbxignore/reconcile.py:141-149`:

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

New shape:

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

The ENOTSUP/EOPNOTSUPP path retains its specific log message — sysadmins seeing "Filesystem does not support ignore markers" know exactly what to do (move tree to a different filesystem, accept the limitation, etc.). All other `OSError` errnos fall through to the generic `errno=NN` log line, which mirrors the read-side log shape ("I/O error reading marker on %s: errno=%s %s").

Both branches converge on `return currently_ignored` — the existing PermissionError arm's pattern. Subtree pruning behaves consistently regardless of which errno fired.

The bare `raise` is removed; the broad-`OSError` `except` no longer re-raises. Non-`OSError` exceptions still propagate normally (no `except Exception` catch-all).

### CLAUDE.md updates

**Gotchas section** — replace the existing bullet about indented-`#` patterns:

> pathspec follows gitignore's column-0 comment rule: a line with leading whitespace before `#` (e.g. `"   #literal"`) is an *active pattern*, not a comment. `rules._build_entries`'s filter checks `raw.startswith("#")` (not `raw.strip().startswith("#")`) so the line is correctly classified as active. The count-mismatch fallback at the bottom of `_build_entries` is now defensive scaffolding for future pathspec-version drift; under the gitignore-correct filter, fast-path counts match in practice.

**Architecture section** — replace the asymmetric-by-design paragraph about `_reconcile_path`'s error arms:

> `reconcile._reconcile_path` has symmetric error arms: both **read** and **write** sides catch broad `OSError`, log a `WARNING` + append to `Report.errors`, and return a value that lets `reconcile_subtree` continue. Read returns `None` (vanished/unreadable path — don't drive subtree pruning); write returns `currently_ignored` (mirrors `PermissionError`'s arm — last-known marker state drives subtree pruning when an already-marked directory's write fails). The write arm preserves a specific log message for `ENOTSUP/EOPNOTSUPP` ("Filesystem does not support ignore markers...") because that's a sysadmin-actionable distinction; other errnos log a generic `errno=NN` line. Original design (PR pre-#21) was narrow-by-design on both sides; #21 widened the read arm to handle transient EIO on network drives without killing the per-root sweep, and PR #128 widened the write arm symmetrically for the same reason. Real bugs (non-`OSError` exceptions) still propagate.

The provenance brackets ("PR pre-#21" / "PR #128") record why the design changed without overburdening the prose. PR # is predicted at this writing; the implementation will verify and amend if different.

## Test plan

Four new tests across two files. No modifications to existing tests — both fixes preserve existing contracts (ENOTSUP-specific log message, blank-line dropping, `#`-at-column-0 dropping).

### #80 — `tests/test_rules_basic.py`

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

The assertion is on `loaded.entries`, not on `cache.match()` against a literal `   #literal` filename — files starting with whitespace + `#` are platform-fiddly to create reliably. The contract being pinned is "the line gets parsed as a pattern," which the entries-list check verifies directly.

### #81 — `tests/test_reconcile_enotsup.py`

Three new tests appended to the existing file (which already has the existing `_raise_eio` and `_raise_enotsup` helpers at module scope).

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

The third test is the safety-net pin: with broad-`OSError` widening, the only reason `_reconcile_path` doesn't swallow EVERYTHING is that the `except` is typed to `OSError` specifically. A future refactor accidentally widening to `Exception` (or a bare `except`) would silently swallow real bugs; this test catches that.

## Risks and edge cases

- **Existing `_build_entries` fallback's body comment becomes near-correct-but-dead.** Updated to reflect the new defensive role. If a future pathspec-version regression actually triggers the fallback in the wild, the function's defensive shape lets the cache still work correctly (just slower); this is the value of keeping the fallback rather than dropping it per #80's candidate 2.

- **`Report.errors` count growth on pre-existing flaky-network-drive setups.** Users currently running dbxignore on a network drive whose Dropbox tree experiences occasional EIO will see error counts rise after this PR ships — but the daemon will stay up, where it would have crashed before. Net win.

- **Testing surface for #81 doesn't exercise every `OSError` errno.** EIO is the representative case; the test trusts that the broad `except OSError` catches all errnos. Adding ENOSPC, EBUSY, etc. as parametrize cases would be defensive but YAGNI — Python's exception machinery doesn't care about the errno value.

- **The `# _load_file already validated...` comment in `_build_entries`** described a no-longer-true claim ("the fallback handles edge cases like leading-whitespace `#` lines"). Updated body comment makes the new defensive role explicit. A reviewer reading the diff sees the comment change adjacent to the filter change, which reinforces the design intent.

- **CLAUDE.md provenance brackets** ("PR pre-#21" / "PR #128") are stylistic: they capture why the design evolved. If the project's convention is to omit such brackets, the prose works without them. Including them aids future archaeology.

## Backlog interactions

- **Resolves #80, #81.** Two inline `**Status: RESOLVED <date> (PR #<N>).**` markers, two entries under `## Status > Resolved > #### 2026-05-07` (or a new date heading if implementation slips a day), removal from the Open list, lead-paragraph count update.

- **Companion to #21** (already resolved). #21's resolution context becomes "now also applies to write side." Worth a brief mention in the PR description.

- **No companion to #41** (already resolved, write-arm `currently_ignored` return). #41's contract is preserved — the return-`currently_ignored` pattern stays exactly as it was.

- **Companion to #82** stays open. Different layer; bundle with next install-layer touch.

## Implementation notes

- `errno` module: already imported in `reconcile.py`. No new imports required.
- `pathspec` module: already imported in `rules.py`. The fix doesn't change the pathspec interface dependency.
- The `_build_entries` change is one-line; the docstring + body-comment changes are co-located.
- The `_reconcile_path` change replaces 9 lines (the existing `except OSError` block) with 14 lines (the new `if/else/return` block). Net +5 LOC in the function.

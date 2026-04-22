# dropboxignore — negation semantics and rule-conflict detection

**Date:** 2026-04-21
**Status:** Accepted. Implementation plan to follow.
**Resolves:** v0.2 follow-up [item 10](../plans/2026-04-21-dropboxignore-v0.2-linux-followups.md#10-prune--negation-leaves-stale-markers-on-children-of-ignored-directories).

## Problem

`.dropboxignore` accepts gitignore-style grammar, including negation rules (`!pattern`). A user who writes the idiomatic gitignore pattern

```
build/
!build/keep/
```

expects `build/keep/` to be re-included (synced by Dropbox) despite `build/` being ignored. That's how `.gitignore` works under git.

Under dropboxignore the picture is different. At rule-change time a race surfaces: the watchdog DIR_CREATE event for `build/keep/` dispatches first (0 ms debounce vs. 100 ms for RULES), reconcile runs with the *old* rule cache (containing only `build/`), and `build/keep/` gets its own marker set. The RULES event then fires, reloads the cache with the new rules, walks the root — but the reconcile prune optimization skips descent into `build/` (still matched-as-ignored by the `build/` rule), so `build/keep/` is never revisited to apply the negation. The marker persists indefinitely; neither the hourly sweep nor subsequent events recover it.

This race is flaky on Linux (inotify fires DIR_CREATE near-instantly) and happens to be masked on Windows (ReadDirectoryChangesW timing makes RULES dispatch before DIR_CREATE so the child is never marked in the first place). Surfaced during v0.2 follow-up item 3 when the Linux daemon smoke test hit the race 3/8 runs.

## Root cause: a semantic mismatch, not a race

Dropbox's [ignored-files documentation](https://help.dropbox.com/sync/ignored-files) states that when a folder is ignored, files and subfolders within it are automatically ignored as well. Ancestor-to-descendant inheritance is built into Dropbox's sync layer, not our responsibility.

That constraint has a hard implication for negations: **if any ancestor directory is ignored, Dropbox does not sync the descendant, no matter what xattrs our code sets on the descendant.** The gitignore pattern `build/` + `!build/keep/` cannot be faithfully translated to Dropbox's ignore model — not as a race condition, but as a fundamental semantic impossibility. The negation can only "work" in gitignore terms if we also clear the ancestor's marker, which contradicts the include rule's intent.

The race above made the bug observable. The race isn't the bug. The bug is that dropboxignore promises a gitignore feature (negation through ignored ancestors) that Dropbox's model cannot honor.

## Scope

**In scope:**
- Detect `.dropboxignore` rule patterns that cannot translate to Dropbox's inheritance model.
- Drop detected conflicts from the active rule set so that the tool's observable behavior matches what Dropbox will actually do.
- Surface detected conflicts through three channels: daemon log (WARNING), `dropboxignore status`, and `dropboxignore explain`.
- Document the Dropbox inheritance model and our limitation in the README.

**Out of scope:**
- Changing Dropbox's inheritance behavior (not our system).
- Removing the reconcile prune optimization — it's correct under the inheritance model.
- Supporting negation patterns that contain glob metacharacters in the leading segments (e.g. `!**/foo/bar/`). Documented as a limitation.
- CHANGELOG tooling (project doesn't currently have one; GitHub Releases serves that role).

## User contract

Two-part promise:

1. **Tool carries the burden of detecting mismatches.** If dropboxignore accepts a rule without warning, that rule behaves as gitignore specifies. When a rule cannot translate to Dropbox's model, the tool tells the user at the moment the rule is loaded — not hours later when sync behavior surprises them. (See also: [alternatives considered](#alternatives-considered).)
2. **README documents the underlying model.** For users who want the mental model upfront rather than via runtime warnings.

When these two combine, users get protection regardless of whether they read the docs or wait for the warning.

## Design

### Detection

Static analysis at rule-load time. The detector runs inside `RuleCache._recompute_conflicts()`, invoked after any of `load_root()`, `reload_file()`, `remove_file()`.

**Input.** The ordered rule sequence across all loaded `.dropboxignore` files under a root, flattened by: shallower files before deeper files; source order within each file. This is the same ordering `RuleCache.match()` already uses.

**Target pattern class.** A negation rule is conflicted if its literal path prefix (the leading path segments before any glob metacharacter, cut at the last `/` before the glob) is matched by any earlier include rule in the sequence.

**Literal prefix extraction.**

```python
def literal_prefix(pattern: str) -> str | None:
    """Return leading path segments before the first glob, or None."""
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
            return None          # glob in the first segment, no anchor
        return p[:last_sep + 1]  # include trailing slash
    # No glob present. If there's no `/`, return the whole thing (it's a
    # single segment and downstream code treats it as a file target, which
    # the file-target guard in _detect_conflicts then filters out). If it
    # ends with `/`, return as-is. Otherwise cut at the last `/` so the
    # prefix is a directory-shaped string — the ancestor-walk consumer
    # needs a directory to anchor on, not a file path.
    if "/" not in p:
        return p
    if p.endswith("/"):
        return p
    last_sep = p.rfind("/")
    return p[:last_sep + 1]
```

Examples:
- `build/keep/` → `build/keep/`
- `build/keep` → `build/` (no trailing slash → cut at last `/`)
- `src/**/test.py` → `src/`
- `foo*/bar/` → `None` (glob in first segment)
- `**/cache/` → `None` (starts with glob)
- `/anchored/path/` → `anchored/path/` (leading-slash normalized)
- `""` → `None` (empty input)
- `plain` → `plain` (single segment, no glob; downstream file-target guard skips)

*Note:* An earlier draft of this pseudocode ended with `return p or None` for the no-glob case, which returned the full pattern as-is even for non-directory shapes like `"build/keep"`. That form was inadequate because `_detect_conflicts` only flags negations whose literal prefix is a directory (trailing `/`); passing a file-path through would falsely skip detection on the enclosing directory. The updated form above cuts at the last `/` for no-trailing-slash inputs, which both preserves the file-target skip (via `_detect_conflicts`' directory guard) and keeps the ancestor walk directory-shaped.

**Conflict check.** For each entry in the sequence, in order:

1. If the entry is an include rule (not a negation), skip.
2. Otherwise compute the negation's `literal_prefix`. If `None`, skip (documented limitation).
3. Walk up ancestors of the prefix: the prefix itself, then its parent directory, up to the root. Each ancestor is formed as a directory path with trailing `/` (pathspec's directory-rule matching needs the trailing slash).
4. For each ancestor, iterate the include rules that appeared earlier in the sequence. If any one of them matches the ancestor via `pattern.match_file(ancestor)`, record a conflict: `(dropped=this negation, masking=that include)`, and stop walking further ancestors for this entry.

The result is a list of `Conflict(dropped, masking)` pairs, one per flagged negation. Pseudocode and pathspec-specific attribute names are deferred to the implementation plan.

**Known limitation.** Patterns where the negation starts with a glob (`**/`, `*/`, `?`, `[`) have no literal anchor. The detector skips these; they pass through as active rules. If such a rule lands under an ignored ancestor at runtime, it silently fails to take effect. Documented in the README; see [alternatives considered](#alternatives-considered) for the rejected noisy-warn-but-don't-drop approach.

### Rule disposition

`RuleCache` holds:

- `_rules: dict[Path, _LoadedRules]` — unchanged. Per-file parse result; canonical, immutable per load.
- `_match_sequence: list[SequenceEntry]` — derived. The ordered rule sequence across all loaded files, **excluding** rules dropped by detection. `match()` and `explain()` iterate this.
- `_conflicts: list[Conflict]` — detected at recompute time. Each entry contains the dropped rule's source-file + source-line + pattern, and the masking rule's same info.

On any rule mutation (`load_root`, `reload_file`, `remove_file`), `_recompute_conflicts()` rebuilds both `_match_sequence` and `_conflicts` from the current `_rules`. No incremental update path — correctness over perf; rule changes are rare.

`_LoadedRules` stays as-is. The filtering happens at the cache layer; each `_LoadedRules` retains its complete parsed entries for future re-derivation when rules elsewhere change.

### Diagnostic surfaces

Three surfaces, each covering a different moment in the user's debugging loop.

**Log record.** Primary surface. Every `_recompute_conflicts()` call emits one WARNING per conflict at `dropboxignore.rules`. Fixed-format message:

```
WARNING dropboxignore.rules: negation `!build/keep/` at
  /home/kilo/Dropbox/.dropboxignore:3 is masked by include `build/` at
  /home/kilo/Dropbox/.dropboxignore:1 (Dropbox inherits ignored state from
  ancestor directories). Dropping the negation from the active rule set.
  See README §Gotchas.
```

Lands in `daemon.log` (both platforms), systemd-journald on Linux (via the PR #7 stderr handler), Task Scheduler's log on Windows. CLI commands that load the cache (`apply`, `list`, `explain`) print the WARNING to stderr via `logging.basicConfig`.

Emits on every recompute, so edits that introduce a conflict produce a fresh warning. Users who edit their `.dropboxignore` away from a conflict see the warnings stop — no "stale warning" issue.

**`dropboxignore status`.** Persistent-visibility surface. When `_conflicts` is non-empty, `status` output gains a section:

```
$ dropboxignore status
daemon: running (pid 267980, started 2026-04-21 14:32:11)
last sweep: 2026-04-21 15:32:14 (marked=3, cleared=0, errors=0)
rule conflicts (2):
  .dropboxignore:3  !build/keep/           masked by .dropboxignore:1  build/
  .dropboxignore:7  !node_modules/patched/ masked by .dropboxignore:4  node_modules/
```

Zero conflicts → section omitted entirely. Source-file paths are printed relative to the Dropbox root for readability; when the masking rule lives in a different file, the other file's path is shown in full.

**`dropboxignore explain PATH`.** Targeted diagnostic. When a path's match consults both `_match_sequence` and `_conflicts`, dropped negations that would have matched the path are surfaced with a `[dropped]` marker:

```
$ dropboxignore explain /home/kilo/Dropbox/build/keep/notes.md
/home/kilo/Dropbox/.dropboxignore:1  = build/
/home/kilo/Dropbox/.dropboxignore:3  [dropped]  !build/keep/  (masked by .dropboxignore:1)
```

Without the `[dropped]` row, a user wondering "I wrote `!build/keep/`, why doesn't it work?" has no direct feedback. The marker is the answer.

## Testing

### Unit — detector

New file `tests/test_rules_conflicts.py`. Pure-function coverage, no filesystem.

- `literal_prefix`:
  - `"build/keep/"` → `"build/keep/"`
  - `"src/**/test.py"` → `"src/"`
  - `"**/cache/"` → `None`
  - `"foo*/bar/"` → `None`
  - `"/anchored/path/"` → `"anchored/path/"`
  - `""` → `None`
- `detect_conflicts`:
  - `build/` + `!build/keep/` → one conflict, dropped=`!build/keep/`, masking=`build/`
  - `*.log` + `!important.log` → no conflict (include matches files, no directory ignored)
  - `build/` + `!unrelated/path/` → no conflict
  - `!keep/` before `build/` → no conflict (negation precedes potential mask)
  - Cross-file: root file has `build/`, `build/.dropboxignore` has `!keep/` → one conflict with masking source pointing at root file
  - Multiple independent conflicts in one sequence → all flagged
  - `**/foo/` + `!**/foo/bar/` → no conflict flagged (documented limitation: glob-prefix negation)

### Integration — RuleCache

Extend `tests/test_rules_reload_explain.py`.

- `load_root` populates `cache.conflicts()` with expected pairs.
- `cache.match(path)` returns verdicts consistent with dropped-negation semantics (the dropped rule's verdict is ignored).
- `cache.explain(path)` surfaces dropped negations with a structured marker (an `is_dropped` field or sentinel — chosen during implementation, pinned by test).
- `cache.reload_file` clears stale conflicts when a file is edited to remove the bad rule.
- `cache.remove_file` clears conflicts that depended on that file's rules.

### Integration — CLI

Extend `tests/test_cli_status_list_explain.py`.

- `status` output includes the `rule conflicts (N):` section when `_conflicts` is non-empty; omitted entirely when empty.
- `explain` output shows `[dropped]` marker and `(masked by :N)` reference for dropped negations.
- Stderr capture during `apply` with a conflicted rule shows the WARNING line.

### End-to-end — daemon smoke

- **`tests/test_daemon_smoke.py` (Windows, existing)** — BREAKS under new semantics and must be updated. Current assertion `not markers.is_ignored(build/keep/)` after introducing `!build/keep/` becomes incorrect: under the new design the negation is dropped, so `build/keep/` receives the marker (directly from the include rule matching, or via inheritance from `build/`'s marker). Replace with: the daemon log captures the conflict WARNING, and `markers.is_ignored(build/keep/)` returns `True`. This is a deliberate behavior change; the PR description will call it out.
- **`tests/test_daemon_smoke_linux.py` (existing)** — unchanged for the add/remove scenario. May optionally gain a negation sub-test mirroring the updated Windows assertion, for cross-platform parity. Desired but not strictly required.

### Not tested

The contract we verify is "our xattrs reflect the rules we accepted (minus dropped ones)." We do not test Dropbox's sync behavior end-to-end — that requires a real Dropbox client, which is already covered by the manual VPS smoke (v0.2 follow-up item 4, resolved).

## Documentation

### README

New subsection under "Behaviour" (or peer to it):

> **Negations and Dropbox's ignore inheritance.** Dropbox marks files and folders as ignored using xattrs. When a folder carries the ignore marker, Dropbox does not sync that folder or anything inside it — children inherit the ignored state regardless of whether they individually carry the marker. This matters for gitignore-style negation rules in your `.dropboxignore`.
>
> If you write a negation whose target lives under a directory ignored by an earlier rule — the canonical case is `build/` followed by `!build/keep/` — the negation cannot take effect. Dropbox will ignore `build/keep/` because `build/` is ignored, no matter what xattr we put on the child. dropboxignore detects this at the moment you save the `.dropboxignore`, logs a WARNING naming both rules, and drops the conflicted negation from the active rule set.
>
> Negations that don't conflict with an ignored ancestor work normally. For example:
>
> ```
> *.log
> !important.log
> ```
>
> Here nothing marks a parent directory as ignored (`*.log` matches files, not dirs), so the negation works — `important.log` gets synced, the other `.log` files don't.
>
> **Limitation.** Detection uses static analysis on the rule's literal path prefix. Negations that begin with a glob (`!**/keep/`, `!*/cache/`) have no literal anchor to analyze and are accepted without conflict-check — if they land under an ignored ancestor at runtime, they silently fail to take effect. If you need guaranteed semantics, prefer negations with a literal prefix.

### CLAUDE.md

One new gotcha bullet:

> `RuleCache` keeps a separate `_match_sequence` derived from `_rules` minus conflicted negations, plus a `_conflicts` list with the dropped rules and their masking includes. `match()` and reconcile iterate `_match_sequence`; `explain()` additionally surfaces dropped rules with a `[dropped]` marker. Detection runs statically at `_recompute_conflicts()` on every rule mutation. Semantic reason: Dropbox's ignored-folder inheritance makes negations inert under ignored ancestors, so we drop them rather than letting the xattr state diverge from what Dropbox will honor.

### Follow-ups plan — item 10 reframed as RESOLVED

The existing item 10 text (describing the prune+negation race with three fix options) gets replaced with a RESOLVED block pointing at this spec.

## Migration and backward compatibility

Existing `.dropboxignore` files containing conflicted-negation patterns will produce new WARNINGs at first load after upgrade. Runtime impact:

- Paths that were previously inconsistently marked (due to the race) will now be consistently marked. `dropboxignore list` output may grow.
- **Dropbox's sync behavior does not change.** Those paths were already not being synced because of ancestor inheritance. The xattr state changes; the user-observable sync outcome does not.
- `uninstall --purge` handles the broader set naturally — it walks all markers regardless of count.

No opt-in flag, no deprecation period. The WARNING is the user-facing signal; the README explains the model. Release notes for the version bump will call out the new WARNING and the `list` output growth.

## Alternatives considered

**Option (2) from brainstorming: warn but honor the negation as-is.** Keeps the negation in the active rule set, emits a WARNING. Rejected because it produces divergence between our recorded xattrs and Dropbox's actual sync behavior — `explain` would say "matched", user would believe the rule took effect, and they'd be left to discover the sync-level silence on their own. That's the worst UX of the three.

**Option (C) from brainstorming: error and refuse to load the `.dropboxignore`.** One conflicted rule would disable every other rule in the file. Too heavy-handed — conflicted negations are localized to the rule that introduces them.

**Drop all negations unconditionally.** Proposed mid-brainstorm as a simplicity-first move. Rejected because it breaks the legitimate `*.log` + `!important.log` pattern — a common, useful gitignore idiom that maps cleanly to Dropbox (nothing marks the parent, so the negation works). Saved ~25 lines of detector code; cost was real functionality loss.

**Dynamic detection at reconcile time.** Consulted at runtime by checking whether a negation's target has an ignored ancestor. Rejected: delays the diagnostic to some future reconcile, sometimes hours after the user wrote the rule. Also more expensive (ancestor check per reconcile). Our contract is "tell the user at the moment they wrote the bad rule" — the rule is written at load time, so detection runs at load time.

**Detection Option X — lean entirely on pathspec.** Use the negation's whole pattern as the target path; skip detection for any negation containing glob characters. Simpler (~15 lines, zero custom parsing) but misses negations with a literal head (`!src/**/test.py`). Rejected in favor of Option Y (~23 lines total with literal-prefix extractor) because the incremental cost is small and catches more real cases.

**Glob-prefix negations: noisy-warn-but-don't-drop fallback.** When `literal_prefix` returns `None`, log a warning regardless ("this negation can't be statically analyzed for conflicts"), but keep the rule active. Rejected: produces false alarms on benign negations like `!**/*.bak` where no ancestor is ignored anywhere in the tree. Signal-to-noise too poor.

**Dropping the prune optimization entirely.** Originally proposed as Option 3 of item 10's three fixes. Rejected after the Dropbox-inheritance finding: the prune isn't an optimization, it's a correctness-consistent implementation of the inheritance model. Removing it would mark every descendant of an ignored directory (redundant xattrs, bigger `list` output, slower sweeps) without fixing the underlying semantic mismatch.

## Open questions

None outstanding. All design decisions have been resolved through the brainstorming flow.

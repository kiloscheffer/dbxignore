# Path-taking `ignore` / `unignore` verbs — design spec

**Item:** BACKLOG #93
**Date:** 2026-05-10
**Status:** Approved (awaiting writing-plans + implementation)

## Problem

The CLI's existing operation verbs walk the entire watched-root tree:

- `apply [<path>]` reconciles markers against `.dropboxignore` rules, scoped to the whole tree or a subtree.
- `clear [<path>]` removes every marker, scoped likewise.

Neither *mutates* `.dropboxignore` — both treat the rule files as read-only inputs. A user wanting to ignore a single folder ad-hoc has to either:

1. Edit the relevant `.dropboxignore` in a text editor, then wait for the daemon's RULES debouncer (default 100 ms) plus the next reconcile, or
2. Call `markers.set_ignored(path)` directly from a Python REPL — bypassing the rule system, which the daemon's hourly recovery sweep then silently undoes.

Neither is a one-shot CLI invocation, and the marker-only path is broken-by-design under the daemon's recovery semantic.

This is also a hard prerequisite for BACKLOG #65 (Windows Explorer right-click integration): the registry verb in #65's design has nothing to invoke today because the CLI has no path-taking operation that mutates rules.

## Goals

After this change:

- `dbxignore ignore <path>` appends a literal-path rule to the relevant `.dropboxignore` AND sets the ignore marker on `<path>` in one synchronous invocation.
- `dbxignore unignore <path>` removes that rule (idempotently across multiple files if the rule was duplicated) AND clears the marker, with a fail-loud refusal when other rules still match the target.
- Both verbs are safe to invoke when the daemon is running (the daemon's RULES debouncer reacts to the file edit and finds the marker already consistent — no race, no spurious cloud activity).
- Both verbs survive marker-write failures on filesystems that don't support extended attributes (`ENOTSUP` on FAT32, some SMB mounts) by leaving the rule on disk and surfacing the OSError; the daemon will set the marker when reconciling on a marker-capable filesystem.
- The shell-integration registry value designed in BACKLOG #65 has a working CLI verb to invoke: `dbxignore.exe ignore --yes "%1"`.

## Non-goals

- **Pattern-mode ignore.** `dbxignore ignore "**/build/"` is out of scope — disambiguating path-vs-pattern from CLI args, scoping confirmation to match counts, and selecting a rule file for a non-anchored pattern are a separate design problem. If demand surfaces, a follow-up item adds a `--rule <pathspec>` flag. Path-only covers the right-click case and per-target scripting loops.
- **Marker-only / ad-hoc ignore.** Setting a marker without writing a rule is silently undone by the daemon's recovery sweep; that interpretation was rejected during design (Q&A 2026-05-09).
- **Daemon-alive guard.** Unlike `clear`, neither `ignore` nor `unignore` needs `--force` — both verbs *are* the kind of mutation the daemon is built to react to, and after the verb returns the steady-state is rule + marker consistent regardless of daemon liveness.
- **Cleanup of dead rules.** A rule whose target path no longer exists on disk is dead weight, but `unignore` requires the path to exist (so the marker-clear step has a target). Cleaning up dead rules stays an editor-driven workflow.

## Design

### Architecture

Three layers, mirroring the project's existing CLI / rules / markers split:

1. **CLI layer** (`src/dbxignore/cli.py`). Two new `@main.command()` blocks (`ignore`, `unignore`) that parse + validate the path argument, run the rule-file selection, branch on idempotence + redundancy + collision cases, prompt for confirmation (or skip on `--yes`/`--dry-run`), and call into the rules + markers layers. Reuses `_discover_roots()`, `find_containing()`, and `_load_cache()` already present in `cli.py`.

2. **Rules layer** (`src/dbxignore/rules.py`). Three new helpers:
   - `format_literal_rule(target: Path, rule_file: Path) -> str` — compute the canonical, gitignore-anchored, path-relative-to-`rule_file.parent` rule string for `target`. Trailing `/` if `target.is_dir()`. Backslash-escapes gitignore meta-chars (`*`, `?`, `[`, `\`) per path segment, plus a leading-segment `!` or `#`.
   - `append_rule(rule_file: Path, rule_line: str) -> bool` — atomic append-iff-missing using temp-then-replace (mirroring `state.write()`'s pattern). Returns `True` if appended, `False` if line already present. Creates the rule file with a leading comment header (`# .dropboxignore — managed by dbxignore`) if absent.
   - `remove_rule(rule_file: Path, rule_line: str) -> int` — atomic rewrite removing all lines whose `rstrip()` matches `rule_line.rstrip()`. Returns the count of removed lines.

3. **Markers layer**. Unchanged. The verbs call `markers.set_ignored(target)` / `markers.clear_ignored(target)` directly. No reconcile_subtree walk is needed because the rule-line is path-anchored to exactly the target, so a single-path marker write is sufficient (the verb's correctness invariant: rule covers exactly the target, no other paths).

### Rule-file selection

Helper `_select_rule_file(target: Path, root: Path) -> Path` in `cli.py`: walks from `target.parent` toward `root`, returning the closest existing `.dropboxignore` ancestor. If none exists, returns `root / IGNORE_FILENAME` (the canonical root rule file) — `append_rule` will create that file on first invocation, with a comment header so the file isn't anonymous.

### Rule-line construction (`format_literal_rule`)

Given `target = ~/Dropbox/proj/foo/bar/` (resolved) and `rule_file = ~/Dropbox/proj/.dropboxignore`:

1. `relative = target.relative_to(rule_file.parent)` → `Path("foo/bar")`.
2. Per-segment escape: each segment is scanned for `*`, `?`, `[`, `\` and the meta-char gets a backslash prefix.
3. Leading-segment escape: if the FIRST segment starts with `!` or `#`, prepend a `\` to that segment (gitignore would otherwise misread the line as a negation or comment).
4. Re-join with `/`.
5. If `target.is_dir()`, append `/`.

Example: target `~/Dropbox/proj/!foo*bar/baz` (a file literally named `baz` inside a literally-named-`!foo*bar` dir) under `~/Dropbox/proj/.dropboxignore` → `\!foo\*bar/baz` (no trailing `/` because `baz` is a file).

### Order of operations

**Rule first, then marker.** Concretely:

1. `rules.append_rule(rule_file, rule_line)` returns `True` (or `False` for idempotent re-call).
2. `markers.set_ignored(target)`.

Rationale: marker-first risks a daemon-race where the marker write fires `IN_ATTRIB` (Linux), the daemon's `EventKind.OTHER` debouncer (default 500 ms) reconciles the path's subtree, finds a marker the rules don't justify, and clears it. The rule isn't on disk yet, so the daemon's "no rule → clear marker" arm fires correctly but spuriously — Dropbox sees "start syncing this folder, then stop again," which results in cloud-side activity that wasn't intended. Rule-first eliminates the window: the rule is on disk before the marker, so any reconcile (ours, daemon's debounce, hourly sweep) sees a consistent rule + marker pair.

`unignore` is symmetric: rule-remove first, then `markers.clear_ignored(target)`. Same rationale inverted — if marker-clear fired first and the daemon reconciled before rule-remove landed, it would re-set the marker (rule still present), and we'd then remove the rule and clear again, going through the same spurious-clear-then-set churn.

### Algorithm — `ignore`

```text
target = path.resolve()
discovered = _discover_roots()  # exit 2 if empty
if not target.exists(): exit 2
root = find_containing(target, discovered)  # exit 2 if None
cache = _load_cache(discovered)
rule_file = _select_rule_file(target, root)
canonical = format_literal_rule(target, rule_file)

# Idempotence + redundancy guards
if cache.match(target):
    matches = cache.explain(target)
    via_us = any(m.pattern.rstrip() == canonical.rstrip() for m in matches)
    if via_us:
        ensure marker set on target  # half-state recovery
        print "<target> is already ignored"
        exit 0
    else:
        # Covered by a wildcard or other rule, not by our literal-path rule.
        ensure marker set on target  # half-state recovery
        print "<target> is already covered by <matches[0].pattern> in <matches[0].ignore_file>;"
        print "not adding redundant rule. Marker confirmed."
        exit 0

# Confirmation
if not yes and not dry_run:
    confirm("Marking <target> ignored will tell Dropbox to delete the cloud copy"
            " and remove it from every linked device. Continue?")

if dry_run:
    print "would append <canonical> to <rule_file>"
    print "would set marker on <target>"
    exit 0

# Mutation: rule first, then marker.
appended = rules.append_rule(rule_file, canonical)
markers.set_ignored(target)
print "ignore: rule added to <rule_file>; marker set on <target>"
```

### Algorithm — `unignore`

```text
target = path.resolve()
discovered = _discover_roots()  # exit 2 if empty
if not target.exists(): exit 2
root = find_containing(target, discovered)  # exit 2 if None
cache = _load_cache(discovered)

if not cache.match(target):
    print "<target> is not ignored; nothing to do"
    exit 0

# Find the rules that match target. Each Match has ignore_file + line + pattern.
matches = cache.explain(target)

# Compute canonical rule-line for each candidate ancestor file.
# Two matches against the same target may live in different rule files (e.g.
# user ran `ignore` from two different cwds and we picked different ancestors
# for the same path), so canonicalize per match.source.
canonical_per_file = {
    m.ignore_file: format_literal_rule(target, m.ignore_file) for m in matches
}

removable = [m for m in matches if m.pattern.rstrip() == canonical_per_file[m.ignore_file].rstrip()]
blockers  = [m for m in matches if m not in removable]

if blockers:
    print "error: <target> is also matched by:"
    for m in blockers:
        print "  line " + str(m.line) + " of " + str(m.ignore_file) + ": " + m.pattern.rstrip()
    print "Remove these manually if you want to unignore <target>."
    exit 2

# Confirmation
if not yes and not dry_run:
    confirm("Unmarking <target> will tell Dropbox to start syncing it again"
            " and re-upload its contents to cloud. Continue?")

if dry_run:
    print "would remove <canonical> from <rule_file> (× N)"
    print "would clear marker on <target>"
    exit 0

# Mutation: rule(s) first, then marker.
for m in removable:
    rules.remove_rule(m.ignore_file, m.pattern)
markers.clear_ignored(target)
print "unignore: rule removed from <files>; marker cleared on <target>"
```

### Daemon coexistence

Both verbs are designed to be safe under a running daemon. Walking through the timeline of `dbxignore ignore <target>` with the daemon running:

| t | Event |
|---|---|
| 0 ms | `rules.append_rule` writes to `<rule_file>`. |
| ~0 ms | Watchdog observes the write, classifies as `EventKind.RULES`, queues into the 100 ms debouncer. |
| ~0 ms | Verb calls `markers.set_ignored(target)`. |
| 100 ms | RULES debouncer fires, daemon's `_dispatch` runs `RuleCache.reload_file` then `reconcile_subtree`. Sees the new rule + the existing marker → no-op. |

If the marker-write step fails between t=0 and t=100ms (e.g. `ENOTSUP`), the verb exits 2 with an explanatory error. The rule is on disk. At t=100 ms the daemon's reconcile sees the rule but no marker, attempts to set the marker, hits the same `ENOTSUP`, and logs a WARNING via `_reconcile_path`'s OSError arm (preserved behavior, no change). The user sees the verb's error and the daemon's WARNING in `daemon.log` — same root cause surfaced at two layers.

If the daemon is **not** running, the rule lands on disk and the marker is set by the verb itself. Next `dbxignore daemon` start picks up the rule and finds the marker already consistent.

## Error handling

| Failure | Behavior |
|---|---|
| `<path>` does not exist | exit 2, "Path X does not exist" |
| `<path>` outside Dropbox roots | exit 2, "Path X is not under any Dropbox root" |
| `_discover_roots()` returns empty | exit 2, "No Dropbox roots found. Is Dropbox installed?" — same wording as `apply` / `clear`. |
| Rule file is read-only / disk full / parent dir unwritable | exit 2, "Failed to write <rule_file>: <OSError>". No partial state — atomic temp-then-replace ensures either the file is fully updated or unchanged. |
| `markers.set_ignored` raises `OSError` (FAT32 / SMB / `ENOTSUP`) | exit 2, "Marker write failed on <target>: <errno>. The rule was added to <rule_file>; the daemon will set the marker when running on a filesystem that supports extended attributes." Cleanup of the rule append is **not** attempted — the rule is correct on disk and reverting would diverge from user intent (the user wanted this path ignored; the daemon will catch up when the FS permits). |
| `unignore` blockers exist | exit 2, list each blocker with file/line/pattern, no mutation. |
| User declines confirmation prompt | exit 0, "Aborted." — same shape as `apply`'s confirmation flow at `cli.py:326`. |

## Testing

A new module `tests/test_cli_ignore.py` (windows_only / linux_only / macos_only NOT applicable — verb is platform-portable; markers backend is exercised via existing `fake_markers` fixture).

### Unit tests for new helpers

| Helper | Cases |
|---|---|
| `format_literal_rule` | Plain dir target → trailing `/`; plain file target → no trailing `/`; meta-chars in segment (`foo*bar`, `[brackets]`, `?question`) → backslash escapes; leading-segment `!literal` and `#literal` → backslash-prefix; deeply nested target → multi-segment relative path. |
| `append_rule` | Empty rule file (creates with header); existing file without target rule (appends); existing file with target rule already present (idempotent, returns False); existing file with target rule present at end without trailing newline (appends correctly); concurrent invocation safe via temp-then-replace. |
| `remove_rule` | File with single matching line (removes, returns 1); file with multiple matching lines (removes all, returns N); file with no match (returns 0, file unchanged); file with line that matches after `rstrip()` (handles trailing whitespace per the design); file becoming empty after removal (preserves the comment header). |
| `_select_rule_file` | Ancestor `.dropboxignore` exists at `target.parent` → returned; ancestor exists higher up → walks correctly; no ancestor → returns `root / IGNORE_FILENAME`. |

### CLI integration tests

Test fixtures: existing `fake_markers`, `write_file` from `tests/conftest.py`, plus a new fixture for staging a Dropbox root with multiple `.dropboxignore` ancestors (extracted from setup-pattern repeated across `test_cli_status_list_explain.py` and `test_cli_apply.py`).

| Case | Verb | Setup | Assertion |
|---|---|---|---|
| Happy path — fresh ignore | `ignore` | Path under root, no rule file at any ancestor | Root `.dropboxignore` created with header + rule line; marker set on target; idempotent on re-call. |
| Happy path — ancestor file exists | `ignore` | `.dropboxignore` at `<root>/proj/`, target at `<root>/proj/foo/bar/` | Rule appended to `<root>/proj/.dropboxignore` (NOT root file); rule line is `foo/bar/` (relative). |
| Wildcard already matches | `ignore` | `**/build/` rule already present, target is `<root>/proj/build/` | No redundant rule appended; informational message printed; marker confirmed set. |
| Half-state — rule on disk, marker missing | `ignore` | Rule already in file, marker not set | Rule untouched; marker set; "already ignored" message. |
| Meta-char escaping | `ignore` | Target dir literally named `foo*bar/` | Rule line is `foo\*bar/`. |
| File target (no trailing `/`) | `ignore` | Target is a file, e.g. `notes.txt` | Rule line is `notes.txt` (no trailing slash). |
| `--dry-run` | `ignore` | Any | Predictions printed to stdout; no rule file or marker mutated; exit 0. |
| `--yes` | `ignore` | Any | No prompt; mutation proceeds. |
| Default behavior prompts | `ignore` | Any | `confirm()` invoked; declining aborts cleanly. |
| Path doesn't exist | `ignore` | Target path doesn't exist | Exit 2 with "Path X does not exist". |
| Path outside roots | `ignore` | Target path under `/tmp/foo` | Exit 2 with "Path X is not under any Dropbox root". |
| Daemon-coexistence smoke | `ignore` | Synthetic RULES event dispatched to `daemon._dispatch` after the verb runs | Reconcile sees consistent state — no spurious clear, no extra mark. |
| Happy path — unignore literal | `unignore` | Path was ignored via literal rule in nearest ancestor | Rule removed, marker cleared, file preserves comment header. |
| Multi-file unignore | `unignore` | Same target literal rule in two different ancestor `.dropboxignore` files | Both files updated, marker cleared. |
| Wildcard collision | `unignore` | Target covered by `**/foo/` AND a literal `proj/foo/` | Exit 2; blocker named with file/line/pattern; neither rule mutated. |
| Already not ignored | `unignore` | Target has no marker, no rule matches | Exit 0 with "X is not ignored; nothing to do". |
| Trailing-whitespace tolerance | `unignore` | Rule line manually written as `proj/foo/bar/   ` (trailing spaces) | `rstrip()`-based equality matches; rule removed normally. |
| `--dry-run` | `unignore` | Any | Predictions printed; no mutation; exit 0. |

### Manual-test scripts (Phase 4.5)

CLAUDE.md's user-visible-CLI-surface convention: any new subcommand or flag with marker / Dropbox side effects extends Phase 4.5 in all three manual-test scripts. Per the existing pattern (extracted helper `scripts/_phase_extended_cli.sh` for Linux + macOS, hand-synced `scripts/manual-test-windows.ps1`):

- New Phase 4.5 case `4o — ignore (PR #<N>)`: ignores a folder, asserts rule landed in nearest ancestor, marker set, daemon's RULES debounce reconciles cleanly.
- New Phase 4.5 case `4p — unignore (PR #<N>)`: unignores the just-ignored folder, asserts rule removed, marker cleared.
- Phase 4.5 case `4q — unignore wildcard collision (PR #<N>)`: asserts the fail-loud blocker behavior.

Each carries the inline `# 4X — <description> (PR #NNN)` provenance comment per the existing convention.

## Files touched

| File | Change |
|---|---|
| `src/dbxignore/cli.py` | Two new `@main.command()` blocks (`ignore`, `unignore`); `_select_rule_file` helper. ~80 LOC. |
| `src/dbxignore/rules.py` | Three new helpers: `format_literal_rule`, `append_rule`, `remove_rule`. ~60 LOC. |
| `tests/test_cli_ignore.py` | New test module per the testing matrix above. ~250 LOC. |
| `README.md` | New §"CLI reference" entries for `ignore` and `unignore`. |
| `BACKLOG.md` | Item #93's `**Status: RESOLVED <date> (PR #<N>).**` inline marker; Status > Open list update (remove #93, update #65's "Blocked by #93" cross-reference to note #93 is resolved). |
| `scripts/_phase_extended_cli.sh` | Three new Phase 4.5 cases (4o, 4p, 4q). |
| `scripts/manual-test-windows.ps1` | Same three cases mirrored in PowerShell. |

Total: ~470 LOC of new code + tests + docs + manual-test-script updates.

## Out of scope (filed elsewhere or deferred)

- **Pattern-mode `ignore`** (`dbxignore ignore "**/build/"`): if demand surfaces, file as a follow-up item with a `--rule <pathspec>` flag; reconcile_subtree replaces the direct marker-set; new rule-file-selection logic for non-anchored patterns. Same shape as the project's "Awaits demand signal" pattern (#27, #28, #29, #30 in BACKLOG.md).
- **Marker-only ad-hoc ignore.** Rejected during design — silently undone by the daemon's recovery sweep.
- **Cleanup of dead rules** (where the target path no longer exists). Editor-driven workflow remains the established path.
- **BACKLOG #65** (Windows Explorer right-click integration). Unblocked by this work; spec/plan/PR cycle resumes after #93 ships.

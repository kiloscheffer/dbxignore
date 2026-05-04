# dbxignore — `.gitignore` import (`generate` and `apply --from-gitignore`)

**Date:** 2026-05-04
**Status:** Accepted. Implementation plan to follow.
**Resolves:** [BACKLOG.md item #56](../../../BACKLOG.md).

## Problem

Users with a populated `.gitignore` who want the same exclusions for Dropbox sync currently have to author a parallel `.dropboxignore` by hand. The rule grammars are identical — both files are consumed by `pathspec`'s gitwildmatch parser, including negations and directory-only patterns — so the gap is purely at the CLI/file-source layer.

Two related affordances are missing:

1. **A way to translate a `.gitignore` to a `.dropboxignore` on disk** so the user can diverge afterwards and treat the `.dropboxignore` as the durable rule source.
2. **A way to apply `.gitignore` rules in a one-shot run** without committing to a new file the daemon will keep watching forever.

## Scope

**In scope:**

- New CLI subcommand `dbxignore generate <path>` that produces a `.dropboxignore` from a `.gitignore` (or any nominated file).
- New flag `dbxignore apply --from-gitignore <path>` that runs reconcile using rules loaded from `<path>` instead of from discovered `.dropboxignore` files.
- A new public method on `rules.RuleCache`, `load_external(source, mount_at)`, that loads an arbitrary file's lines as if it were a `.dropboxignore` mounted at a given directory.
- A README section explaining the gitignore-vs-dbxignore semantic divergence (cloud removal vs. local untracking) and the two new entry points.

**Out of scope:**

- Auto-discovery of every `.gitignore` under the Dropbox tree. The user nominates the source file or directory.
- Live "translate-on-modify" linkage where edits to `.gitignore` propagate to `.dropboxignore`. The two files are independent after `generate`.
- A `--scope <subtree>` flag for `apply --from-gitignore` to narrow reconcile below the gitignore's directory. Filed for later if the use case shows up.
- Filtering or rewriting gitignore patterns. Lines pass through verbatim.
- Runtime warning banners. The semantic divergence is documented in the README and in `--help` text only — same posture as the rest of the CLI.

## User contract

The user nominates a rule source. dbxignore translates or applies it without mutating the source.

- `dbxignore generate <path> [-o <out>] [--stdout] [--force]`: write a `.dropboxignore` derived byte-for-byte from `<path>`. Default output location is `<dirname(source)>/.dropboxignore`. Refuse to overwrite an existing `.dropboxignore` unless `--force` is passed.
- `dbxignore apply --from-gitignore <path>`: load rules from `<path>` into a fresh in-memory `RuleCache`, mount them at `dirname(<path>).resolve()`, and reconcile that subtree. Existing `.dropboxignore` files in the tree do **not** participate in this run. The mount directory must be under a discovered Dropbox root.

The two verbs are independent. A user who wants the durable handoff runs `generate` once. A user who wants a one-shot ephemeral sweep runs `apply --from-gitignore`. A user who wants both runs `generate` then `apply` (the regular `apply`, since the file is now on disk).

## Design

### Architecture

Two new entry points share one new internal seam.

```
src/dbxignore/cli.py
  + generate(...)             # new @main.command
  + _resolve_gitignore_arg()  # helper: file-or-dir → path to gitignore file
  ~ apply(...)                # add --from-gitignore option

src/dbxignore/rules.py
  + RuleCache.load_external(source, mount_at)  # new public method
  ~ RuleCache._load_file(... , as_path=None)   # add optional cache-key override
```

### `dbxignore generate` — argument and output semantics

`<path>` accepts either a file or a directory.

- File: used as the source as-is. The filename does not need to be `.gitignore` — `.npmignore`, `.dockerignore`, or any user-named file works as long as its contents parse.
- Directory: the source is resolved as `<path>/.gitignore`. If that file doesn't exist, error.

Output destination, in priority order:

1. `--stdout`: bytes go to stdout; no file written. Mutually exclusive with `-o`.
2. `-o <out>`: bytes go to `<out>`. Path must be writable; parent directory must exist.
3. Default: `<dirname(source)>/.dropboxignore`.

Collision policy: if the resolved file destination exists and `--force` is not passed, exit non-zero without writing. The error message points the user at `--force` (overwrite) and `--stdout` (preview without writing).

If the file destination resolves to a path outside any discovered Dropbox root, write proceeds but a stderr warning is printed: the file will not be picked up by reconcile or the daemon.

### `dbxignore apply --from-gitignore` — rule scope

Rule source: `<path>` must be a file (no directory auto-resolution; an explicit file makes the destructive action's target unambiguous).

Mount point: `dirname(<path>).resolve()`. The synthesized rules are cached as if a `.dropboxignore` had been temporarily placed there. The mount point must be under a discovered Dropbox root; otherwise exit non-zero with a message that names the offending directory.

Rule isolation: a fresh `RuleCache` is constructed for the run. `load_root` is **not** called — existing `.dropboxignore` files in the tree are deliberately invisible to this command. The mount directory is registered as the cache's only root via `load_external`'s implicit `_roots.append`.

Reconcile target: `reconcile_subtree(root=mount_at, subdir=mount_at, cache=cache)`. The summary line uses the same format as `apply`: `apply: marked=N cleared=M errors=K duration=Xs`.

`--from-gitignore` is mutually exclusive with the existing positional `[path]` argument on `apply`. Passing both is a usage error.

### The `load_external` seam

```python
# rules.py
def load_external(
    self, source: Path, mount_at: Path, *, log_warnings: bool = True
) -> None:
    """Load `source`'s lines as if it were a .dropboxignore at `mount_at`.

    Used by `apply --from-gitignore`: rules in `source` are mounted at
    `mount_at` (which becomes a tracked root for this cache). The cache
    treats them indistinguishably from rules discovered at
    `mount_at/.dropboxignore`.
    """
    mount_at = mount_at.resolve()
    synthetic_path = mount_at / IGNORE_FILENAME
    with self._lock:
        if mount_at not in self._roots:
            self._roots.append(mount_at)
        self._load_file(source, as_path=synthetic_path)
        self._recompute_conflicts(log_warnings=log_warnings)
```

The `_load_file` change is one parameter and one line:

```python
def _load_file(
    self, ignore_file: Path, *, st: os.stat_result | None = None,
    as_path: Path | None = None,
) -> None:
    # ... unchanged read + parse ...
    cache_key = (as_path or ignore_file).resolve()
    self._rules[cache_key] = _LoadedRules(...)
```

All consumers — `match`, `explain`, `_applicable`, `_recompute_conflicts` — key on `_rules` paths and operate on the mount-derived cache key. They need no changes.

### Validation order: parse before write

`generate` parses the source bytes through `rules._build_spec` before writing. If any pattern is malformed, the write does not happen and the user gets the parse error. This matches the project's existing parse-before-write posture (item #20's `state.write` parse-back validation).

### Documentation

A new README section, "Using `.gitignore` rules", covers:

- The two verbs and their distinct workflows.
- The cloud-removal divergence: `.gitignore` says "git doesn't track this"; a dbxignore marker tells Dropbox to remove the matched paths from cloud sync.
- The interaction with the running daemon: writing a `.dropboxignore` (whether by `generate` or by hand) triggers a reconcile within the next debounce window, which then applies the marker writes immediately. `generate` is not "preview-only" when the daemon is running.
- The negation footnote: rules that negate under an ignored ancestor are silently dropped per the existing `_dropped` semantics. `dbxignore explain <path>` surfaces the masking rule.

`--help` text on both `generate` and `apply --from-gitignore` includes a one-line note that points to the README section.

### Error handling

`generate`:

| Condition | Exit | Stream | Message |
|---|---|---|---|
| Source path doesn't exist | 2 | stderr | `error: <path> not found` |
| Source is a directory with no `.gitignore` inside | 2 | stderr | `error: no .gitignore in <dir>` |
| Source unreadable (`OSError` other than `FileNotFoundError`) | 2 | stderr | `error: cannot read <path>: <strerror>` |
| Source decoded as non-UTF-8 | 2 | stderr | `error: <path> is not valid UTF-8` |
| Source parses but `_build_spec` raises | 2 | stderr | `error: <path> contains invalid pattern: <exc>` |
| Target exists and `--force` not passed | 2 | stderr | `error: <target> exists; pass --force to overwrite or --stdout to preview` |
| Target dir not writable | 2 | stderr | `error: cannot write <target>: <strerror>` |
| `-o` and `--stdout` both passed | 2 | stderr | `error: -o and --stdout are mutually exclusive` |
| Target outside all Dropbox roots | 0 | stderr (warning) | `warning: <target> is not under any discovered Dropbox root; reconcile will not see it` |
| Source empty or only comments/blanks | 0 | stdout | `wrote 0 rules to <target>` |

`apply --from-gitignore`:

| Condition | Exit | Stream | Message |
|---|---|---|---|
| Source path doesn't exist | 2 | stderr | `error: <path> not found` |
| Source unreadable / invalid UTF-8 / invalid pattern | 2 | stderr | (same wording as `generate` matrix) |
| `dirname(source).resolve()` not under any Dropbox root | 2 | stderr | `error: <path>'s directory <dir> is not under any Dropbox root` |
| Both `--from-gitignore` and the positional `[path]` passed | 2 | stderr | `error: --from-gitignore and the positional path argument are mutually exclusive` |
| Source is a directory | 2 | stderr | `error: --from-gitignore requires a file path, not a directory` |
| No Dropbox roots discovered | 2 | stderr | `error: no Dropbox roots found. Is Dropbox installed?` |

Reconcile-time errors (per-file `OSError`, `PermissionError`, `ENOTSUP`/`EOPNOTSUPP`) inherit unchanged from `reconcile_subtree`. They surface in the `marked=N cleared=M errors=K` summary; no new code paths are needed there.

## Testing

Three new or extended test files. ~17 tests total.

`tests/test_cli_generate.py` (new, ~9 tests):

- File arg → writes sibling `.dropboxignore`; bytes match source.
- Directory arg → resolves `<dir>/.gitignore`; same write target.
- `--stdout` → content on stdout, no file at default location.
- `-o <path>` → writes to that path, ignoring default.
- Existing `.dropboxignore` without `--force` → exit 2, original bytes unchanged.
- Existing `.dropboxignore` with `--force` → exit 0, new bytes on disk.
- Source contains malformed pattern → exit 2, target file does not exist after.
- `-o` and `--stdout` both → exit 2.
- Source named `.npmignore` (file arg) → succeeds.

`tests/test_cli_apply.py` (extending, +5 tests):

- gitignore at `<root>/sub/.gitignore` with rule `build/` → `<root>/sub/build` is marked, `<root>/other/build` is not.
- Tree has both a hand-written `.dropboxignore` and the nominated gitignore — only the gitignore's rules apply.
- gitignore at a path outside the discovered Dropbox root → exit 2.
- `apply --from-gitignore X Y` → exit 2.
- `apply --from-gitignore <directory>` → exit 2.

`tests/test_rules_load_external.py` (new, ~3 tests):

- `load_external` then `match()` returns True for a matching path under `mount_at`.
- `cache._rules` contains `mount_at/.dropboxignore`, NOT the source path.
- `_load_file`'s "log warning, swallow" contract is preserved through the seam (invalid pattern logs warning, does not raise).

Test conventions match existing CLI tests: `CliRunner`, `tmp_path`, `monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])`, `fake_markers` fixture for marker assertions. No `state.json` redirection is needed since neither verb writes state.

No platform-specific tests. Both verbs are pure-Python plus the existing platform-dispatched marker layer (already abstracted by `fake_markers`).

## Alternatives considered

### Filter gitignore lines on translation

Drop or rewrite patterns that "don't make sense" for Dropbox sync (e.g. `.git/`, `*.pyc`). Rejected: the user knows their workflow; the engine handles all gitignore syntax correctly; transparent passthrough is the predictable contract.

### Auto-discover gitignore files under the Dropbox tree

`dbxignore generate` with no args walks roots, offers to translate every `.gitignore` it finds. Rejected for the first iteration: opt-in per-source is more legible; auto-discovery raises questions about already-translated files, conflicts, and per-tree opt-out that the explicit form sidesteps.

### Live linkage between `.gitignore` and `.dropboxignore`

Have the daemon watch `.gitignore` files and sync changes to the corresponding `.dropboxignore`. Rejected: doubles the watchdog event volume; the relationship between the two files is inherently user-mediated (the user decided which gitignore rules belong in dbxignore's stricter "remove from cloud" semantic), and a one-way sync would obscure that decision.

### Make `apply --from-gitignore` compose with existing `.dropboxignore` files

Rules from the gitignore would layer additively on top of any `.dropboxignore` files in the tree. Rejected: the conflict-detection machinery would have to reason about a synthesized rule source, and the `explain` output would need new formatting for "rules from a file that does not exist on disk." The isolated-fresh-cache model is simpler to explain and simpler to implement.

### Daemon-aware warning banners

Detect a live daemon via `_process_is_alive` and emit warnings only when the daemon is alive (because then `generate` triggers an immediate reconcile). Rejected: the liveness inference is fragile (stale `state.json`, dead-but-marked-alive PIDs); the README documents the daemon interaction once for both code paths; consistency with the rest of the CLI's no-banner posture wins.

# Post-v0.2 follow-ups

Items surfaced by the 2026-04-20 simplify-review pass that weren't in scope for the review itself. Each is either a larger perf change needing its own PR with benchmarks, or a judgment call worth revisiting with fresh eyes.

## 1. `Path.resolve()` in the `match()` / `explain()` hot path

**Where:** `rules.RuleCache.match()` and `.explain()` both call `path = path.resolve()` at entry.

**Why:** on Windows `resolve()` maps to `GetFinalPathNameByHandleW` — a real syscall per call, opening the file to read its canonical path. `match()` runs once per file during a full sweep; a 200k-file Dropbox is 200k extra syscalls per hourly sweep.

**Shape of fix:** two paths:
- Option A (contract change): require callers to pass already-resolved absolute paths; document it; assert `path.is_absolute()` at entry. `_reconcile_path` and `cli.apply` already resolve at their top, so they'd be no-ops; the CLI `explain` path would need to resolve once at CLI entry.
- Option B (implementation change): short-circuit `resolve()` when `path.is_absolute()` and `".." not in path.parts` (the common case from `os.walk`). Symlinks still get normalized if they appear (rare for Dropbox).

**Prereq for either:** a Windows benchmark. Gut feel says option A is cleaner but risks being a trap if a future caller passes a relative path. Option B is safe-by-default at the cost of per-call introspection.

## 2. `ads` module also `.resolve()`s every call

**Where:** `ads.is_ignored / set_ignored / clear_ignored` each call `path.resolve()` before opening the stream.

**Why:** `_reconcile_path` makes one `is_ignored` + 0–1 `set_ignored`/`clear_ignored` per file = **2× resolve syscalls per file per sweep** on top of item 1's cost.

**Shape of fix:** same decision as item 1. If the cache contract becomes "callers pass resolved paths," `ads` should follow — they'd be consistent, and the syscall count drops by another factor of two. Do items 1 and 2 in the same PR.

## 3. Parallel root sweep

**Where:** `daemon._sweep_once` iterates `roots` sequentially.

**Why:** users with both a personal and business Dropbox have two independent subtrees. Sweep wall-clock is `sum(per_root)` instead of `max(per_root)`.

**Shape of fix:** `concurrent.futures.ThreadPoolExecutor(max_workers=len(roots))`. Independent roots write to disjoint keys in `_rules`, but the dict itself needs a lock (or per-root caches). Low urgency — most users have one root, and the savings are wall-clock, not CPU.

## 4. Double reconcile on same-directory `.dropboxignore` rename

**Where:** `daemon._dispatch` RULES moved branch (`daemon.py` around L58–67).

**Why:** the current code reconciles `src.parent` unconditionally, then reloads the dest and reconciles again if the parent differs. In the most common case (pure rename inside the same directory), `src.parent == dest.parent`, so the first reconcile runs with the *old* cache state — partly stale, then redone moments later. One wasted subtree walk per in-place rename.

**Shape of fix:** before the unconditional `reconcile_subtree(root, src.parent, cache)`, check whether `src.parent == (dest.parent if dest_in_root else None)`; if so, skip straight to `remove_file(src)` + `reload_file(dest)` + single reconcile.

**Risk:** small — the path is well-tested. Worth adding a test that counts reconcile calls for the same-directory rename case before shipping.

## 5. `_build_entries` fallback try/except

**Where:** `rules._build_entries` fallback path — the per-line reparse loop catches `(ValueError, TypeError, re.error)`.

**Why:** `_load_file` already guards the whole-file parse with the same three exceptions and rejects the whole file if any line fails. If the whole file compiled, then either (a) every individual line also compiles (making the fallback's `try/except` dead defensive code), or (b) there's a pathspec-version case where a line compiles in bulk but not individually (making the silent `continue` hide a real bug — `entries` ends up short and `explain()` would report incomplete matches).

**Shape of fix:** investigate (b) first. If no such case exists in pathspec 1.0.4, drop the try/except and let exceptions propagate (they'd indicate a real bug). If the case exists, document it inline and consider logging at DEBUG level so at least it's observable.

## Non-deferrals (decided not to pursue)

- **Fold `_ancestors` into `_applicable`**: cosmetic, no concrete payoff.
- **Merge `test_daemon_logging.py::isolated_pkg_logger` fixture with `_configured_logging`**: fixture installs a *sentinel* state for assertions, not pure dup of the context manager.
- **Unify ad-hoc ADS test stubs in `test_reconcile_return_state.py`**: four local stubs of ≤8 lines each; unifying would add indirection worse than the local code.
- **Extract a move-dest helper in `_dispatch`**: two 7-line blocks with genuinely different logic (rules events need `reload_file`, other events don't). Premature abstraction.

# dbxignore — backlog

Central tracker for open items and planned work.

**Conventions** (also noted in `CONTRIBUTING.md`):
- New items append at the bottom (`## <N>. <title>`) with body, fix candidates, urgency, and a `Touches:` file list.
- When an item ships, remove its entry from this file (and from the at-a-glance `Open` list at the bottom). The item's history lives in the merging PR's description and in the commit log; this file tracks only what's still pending.

**Scope.** Mixes engineering tech-debt, CI flake observations, release-workflow hardening, and CONTRIBUTING.md currency findings. Not user-filed issues — the project doesn't currently host any (PyPI traffic + zero open GitHub issues at last check). When external reports show up, this file may need to evolve toward GitHub Issues; for now, in-tree provenance + grep is the right tradeoff.

## 1. Intel Mac (x86_64) Mach-O binary build leg

dbxignore ships arm64 Mach-O binaries for macOS only (built on `macos-latest` which aliases to `macos-14` / Apple Silicon). Intel Mac users install via the universal Python wheel from PyPI — documented in the README's macOS section.

If x86_64 demand surfaces, add a `macos-13` runner to `.github/workflows/release.yml` (similar shape to `build-macos`, different artifact name to avoid collision). The `pyinstaller/dbxignore-macos.spec` is already arch-agnostic (`target_arch=None` follows the runner), so no spec changes needed.

**Fix candidates:**

- **Add `build-macos-x86_64` job** alongside the existing `build-macos`. Two parallel arm64 + x86_64 artifacts on the GitHub Release.
- **Switch to universal2** (covered by item #2 below) — single artifact, more complex setup. Mutually exclusive with the dual-build approach.

**Urgency:** low until demand surfaces. The user-base is small enough that field signals will reach via GitHub Issues if Intel users hit the gap.

Touches: `.github/workflows/release.yml` (new build job + publish-github files: list extension); README's macOS section (remove the "Apple Silicon only" caveat).

## 2. Universal2 macOS binary as the single artifact

Apple's `universal2` Mach-O format bundles arm64 + x86_64 in one binary. Would replace the current arm64-only artifact (and the eventual x86_64 artifact from item #1) with a single one. PyInstaller supports this via `target_arch="universal2"` in the spec, but the build environment must have a universal2 Python interpreter.

**Fix candidates:**

- **Switch `pyinstaller/dbxignore-macos.spec` to `target_arch="universal2"`** and verify `macos-latest`'s Python is universal2-built (likely yes — Homebrew Python on `macos-14` ships as universal2). Test by inspecting the resulting binary with `lipo -info`.
- **Defer until item #1 actually fires** and decide between dual-build vs universal2 at that time.

**Urgency:** very low. Quality-of-life only; doesn't change what users can install. Not pressing until either x86_64 demand surfaces (then we choose between #1 and #2) or some other reason makes the unified artifact preferable.

Touches: `pyinstaller/dbxignore-macos.spec` (`target_arch` change); `release.yml` (potentially simplify if #2 obviates the need for a second build job from #1).

## 3. Codesigning + notarization for macOS binaries

Currently the GitHub-Release Mach-O binaries are unsigned. macOS Gatekeeper refuses unsigned binaries on first launch with "cannot be opened because it is from an unidentified developer." The README documents the workaround (`xattr -d com.apple.quarantine /usr/local/bin/dbxignore`), but a proper signed-and-notarized binary would just work.

Requires:

1. An **Apple Developer Program** membership (~$99/year — recurring).
2. A **Developer ID Application** signing certificate.
3. An **app-specific password** for the notarization service.
4. GitHub Secrets to hold the certificate (base64'd `.p12`), the certificate password, and the notarization credentials.
5. A `codesign` step in `release.yml` after the PyInstaller build, then a `xcrun notarytool submit` step.

**Fix candidates:**

- **Defer indefinitely.** The current workaround is one shell command; users who hit it can copy-paste from the README. The $99/year cost + the secret-management complexity is a real ongoing burden.
- **Adopt** if Gatekeeper-bypass friction becomes a frequently-reported pain point or if a friction-free install story becomes load-bearing for adoption.

**Urgency:** lowest of the macOS-binary followups. Worth filing for visibility but not for action absent a concrete user-pain signal.

Touches: `.github/workflows/release.yml` (signing + notarization steps); GitHub Secrets (cert, password, notarization creds); README's macOS section (remove the Gatekeeper-bypass instructions).

## 4. Dual `paths` for-loops in `_detected_attr_name()` could share a helper

`_detected_attr_name()` has two consecutive for-loops over the `paths` list, each with similar shape: try `os.path.realpath(p)`, catch `OSError`, check a predicate against the result, return `ATTR_FILEPROVIDER` if matched, log the match. The loops differ only in:

- The predicate (`is_relative_to(cloud_storage)` vs. `len(real_parts) >= 3 and real_parts[1] == "Volumes"`).
- The log message ("Detected File Provider mode: %s under ~/Library/CloudStorage/" vs. "Detected File Provider mode (external drive): %s").

One option is extracting `_first_match(paths, predicate, log_msg) -> bool` to dedupe. The counterargument: the dual structure correctly encodes different *priority levels* — CloudStorage match wins unconditionally (regardless of pluginkit state); `/Volumes` match only fires if `extension_state == "allowed"`. Merging into a single pass would either change priority semantics (a CloudStorage hit on account[1] would lose to a Volumes hit on account[0]) or require carrying a "found Volumes match, hold it" variable — both worse.

The question isn't whether the code is correct (it is); it's whether the dual-loop structure is the best way to express the priority semantics, or whether a shared helper called twice (with different predicates) would be clearer.

**Fix candidates:**

- **Extract `_first_match(paths, predicate, log_msg) -> bool`** and call it twice in priority order. Concrete shape:
  ```python
  def _first_match(paths, predicate, log_msg):
      for p in paths:
          try:
              real = Path(os.path.realpath(p))
          except OSError:
              continue
          if predicate(real):
              logger.debug(log_msg, p)
              return True
      return False
  ```
  Then `if _first_match(paths, is_under_cloudstorage, "..."):` returns FP, etc. Preserves priority via the order of the two `if` blocks. Saves ~10 lines of repetition.
- **Status quo.** The dual structure is verbose but correctly encodes the priority. A future reader can see "CloudStorage check first, Volumes check second" at a glance; with the helper they'd have to read the helper definition + both call sites to understand priority. Argument against extraction is "verbose-but-clear beats terse-but-indirect."

**Recommendation:** keep as-is. The verbose structure correctly documents the priority. A `_first_match` helper would be cleaner if a third predicate ever appeared, but with two it's "rule of three" territory — not yet.

**Urgency:** very low. Code-quality observation only; current shape is defensible.

Touches: `src/dbxignore/_backends/macos_xattr.py` (one helper added, two call sites updated).

## 5. `install/__init__.py` platform dispatch duplicated across `install_service` and `uninstall_service`

`src/dbxignore/install/__init__.py` has two near-identical 14-line if-elif-else dispatchers (`install_service` and `uninstall_service`), each branching `sys.platform` against `win32` / `linux*` / `darwin` and importing+calling the matching backend's `install_*` / `uninstall_*` function. The two functions differ only in the imported function name and the call.

One option is extracting a `_dispatch_platform_action(action: str) -> Callable` helper that takes `"install"` or `"uninstall"` and returns the matching backend function, eliminating the duplicate branching.

**Counterargument** (chosen direction): the current shape is six trivial blocks (3 platforms × 2 ops), each block is two lines (lazy import + call), and the structure makes it trivial to add a fourth platform — touch one place per op. A factored-out dispatcher would (a) introduce a stringly-typed `action` parameter, (b) couple install and uninstall behind one indirection so a reader has to walk through the helper to see what each op does, and (c) violate the project's preference for "three similar lines over a premature abstraction". The duplication is *intentional clarity*, not accidental copy-paste.

This is the same shape as item #4 — filed for the design-tension record so future readers see "this was considered and explicitly rejected" rather than re-discovering the pattern.

**Fix candidates:**

- **Status quo** (recommended). Current shape is the right balance for 3 platforms × 2 ops. Re-evaluate if a fourth platform lands or if a third op (e.g. `enable_service` / `disable_service`) is added — at that point the rule-of-three trigger fires and extraction becomes proportionate.
- **Extract `_dispatch_platform_action(action)`.** Saves ~10 lines but adds a layer of indirection. Defensible if the maintainer prioritizes line-count over branching-structure-clarity.

**Urgency:** very low. Code-quality observation only; current shape is defensible.

Touches: `src/dbxignore/install/__init__.py` (would touch all 14 lines if the extract path is chosen).

## 6. Initial-sweep wall-clock on a fresh tree (no existing markers) — 49.62s on 27k dirs

Measured on a VPS — Ubuntu 24.04, Python 3.14, 27,000-directory personal Dropbox tree — journalctl shows the initial sweep takes 49.62s. The manual-test daemon-startup poll uses a 180s budget to cover this. The daemon writes `state.json` early and runs the initial sweep on a worker thread (so `dbxignore status` returns immediately and systemd-startup doesn't pause), and `_sweep_once` fans out across each root's top-level subdirs via a single `ThreadPoolExecutor`. Wall-clock of the sweep itself is still ~50s on a tree this size; that's the open concern.

`reconcile.reconcile_subtree` (called by `daemon._sweep_once`) traverses every directory under each root via `os.walk(followlinks=False)` and calls `markers.is_ignored()` (one xattr query / syscall) + `cache.match()` per visited directory. Cost is dominated by per-directory stat + xattr work, not by the reconcile match logic.

**Steady-state pruning is already implemented and pinned by tests.** When a child directory is already marked AND `match()` still confirms it should be ignored, `_reconcile_path` returns `currently_ignored=True` (the no-mutation tail at `reconcile.py:187`), and `dirnames[:]` filtering at `reconcile.py:81-85` drops the child from the walk — descendants are never queried. Pinned by `tests/test_reconcile_basic.py::test_does_not_descend_into_marked_subtree`. **Hourly recovery sweeps on a tree whose markers are already in place are O(unmarked dirs), not O(all dirs).**

What pruning does NOT help: the **initial sweep on a fresh install** where no markers exist yet. Every directory has to be visited at least once to call `match()`, write the marker for matching dirs, and let pruning kick in for subsequent sweeps. The 49.62s VPS measurement is this case. After the first sweep, hourly sweeps are fast.

**Fix candidates:**

- **Persist a "last sweep completed" hint per root in state.json.** On daemon start, if a recorded successful-sweep marker exists AND the root's tree mtime is at-or-before that timestamp AND `RuleCache.load_root` reports no rule changes since, skip the initial sweep — let watchdog events + the hourly recovery handle drift. ~80 LOC. Reliability concern: directory mtime semantics on network filesystems and File-Provider-mode Dropbox trees are not always monotonic, so the hint can lie. Worth measuring in a beta-tester install before committing.

- **Defer.** The initial-sweep cost is a one-time per-fresh-install pain. Workaround documented today: run `dbxignore apply` synchronously before `dbxignore install` so the markers exist before the daemon's first sweep, which then prunes correctly.

**Urgency:** medium-low. Fundamental to evaluating an N-dir tree once; reproduces only on fresh installs of personal-account-sized trees. Hourly steady-state sweeps are unaffected. With the early `state.json` write hiding the systemd-startup pause, the remaining concern is purely "the sweep itself takes 50s".

Touches: `src/dbxignore/state.py` (the hint field); `src/dbxignore/daemon.py` (skip logic at startup); `tests/test_reconcile_basic.py` already documents the steady-state pruning contract via `test_does_not_descend_into_marked_subtree` — new tests would cover the chosen candidate's incremental contract.

## 7. Watchdog observer schedules one inotify watch per directory; doesn't skip ignored subtrees

`daemon.run` passes `recursive=True` to `observer.schedule(handler, root, recursive=True)`. Watchdog's inotify backend adds one watch per directory in the recursive subtree. Marked-ignored subtrees consume watch slots even though dbxignore has nothing to react to inside them — Dropbox isn't syncing them, and any user changes inside e.g. a `node_modules/` shouldn't trigger reconcile.

For a 27,000-dir Dropbox tree this consumes ~27k watch slots out of the per-user `fs.inotify.max_user_watches` budget. Default 8192 is exceeded out of the box. Bumped to the standard 524,288 it works fine, but ~95% of the budget is allocated to subtrees the daemon doesn't care about — only really matters at much larger scales (~500k+ dirs).

Architectural shape of the fix:

1. Walk the tree at startup (or piggyback on `_sweep_once`'s walk) and identify directories WITHOUT the ignored marker.
2. For each unmarked directory, call `observer.schedule(handler, dir, recursive=False)` — N independent non-recursive watches instead of one recursive one.
3. Maintain watch lifecycle: when a directory is newly marked during reconcile, `observer.unschedule` its watch and any descendants. When a directory is unmarked, schedule a new watch and walk it to catch any newly-unmarked descendants.
4. Handle delete and move events for watched directories — `unschedule` is required to avoid stale-state warnings from watchdog.

Race conditions to design against: a directory event arriving for a path that was just unscheduled; a `.dropboxignore` change firing reconcile mid-walk; the observer's internal handlers seeing events for paths the daemon thinks aren't watched. The `RuleCache._rules` RLock pattern (per CONTRIBUTING.md's architecture note: "If you add cross-root shared state to RuleCache or reconcile, revisit this") is the existing precedent — a similar invariant would have to hold for watch state.

The deepest scalability fix in the perf-sweep trio but also the most invasive — race-condition-prone state machine work that is easy to get subtly wrong.

**Fix candidates:**

- **Per-directory watches with full mark/unmark lifecycle.** The architecture above — ~200+ LOC plus extensive race-condition tests and large-tree perf benchmarks. Worth the cost only if a beta tester actually hits the watch ceiling on a system with `max_user_watches` already raised to 524,288.

- **Per-directory watches without dynamic lifecycle** (simpler subset). Walk once at startup, schedule non-recursive on unmarked dirs, accept that newly-marked dirs continue to consume their watch slot until daemon restart. ~50 LOC. Catches the static-state savings (~80% of the budget) without the lifecycle complexity. Trade-off: a user who marks a 10,000-file dir doesn't see the watch budget recover until daemon restart, AND changes inside a dir that was previously ignored but had its rule removed won't be caught until restart (a real correctness regression vs. status quo).

- **Defer.** Status quo. A sysctl bump to 524,288 is sufficient for any plausibly-sized Dropbox account in 2026.

**Urgency:** low. No production hit yet — `max_user_watches=524288` (standard recommendation) is sufficient for any plausibly-sized tree. Defer until a beta tester observes the watch budget exceeded after raising it; until then, the architectural complexity is unjustified.

Touches: `src/dbxignore/daemon.py` (observer setup + new watch-lifecycle helper); `src/dbxignore/reconcile.py` (callback hook for "directory just marked/unmarked"); new `tests/test_daemon_watch_lifecycle.py` (per-dir scheduling, mark/unmark transitions, race scenarios).

## 8. macOS sync-mode detection is process-global, not root/account-specific

`_backends/macos_xattr.py:_detect()` returns one cached list of attribute names for the whole process. Its multi-account rule is "any account path under `~/Library/CloudStorage/` means File Provider." That is safe for a single active sync stack, but a mixed setup can have one Dropbox account/root still in legacy mode and another in File Provider mode.

In that case, selecting only `com.apple.fileprovider.ignore#P` because one account is under CloudStorage can make marker writes under a legacy root no-op from Dropbox's perspective. The reverse can happen if detection falls to legacy while a File Provider root is actually active. Dual-attribute mode exists, but only for the pluginkit-unknown/no-decisive-path branch, not for mixed decisive paths.

**Fix candidates:**

- **Detect per root/path** (best correctness). Thread the path/root into marker operations or expose a root-to-attribute decision cache. More invasive because the marker facade currently has only `Path -> operation`.
- **Write/read both attributes for mixed-account decisive cases** (preferred minimal fix). If info.json reports both legacy-shaped and File-Provider-shaped account paths, return `[ATTR_LEGACY, ATTR_FILEPROVIDER]` instead of a single attr. This preserves the current facade and favors correctness over metadata cleanliness.
- **Defer.** Mixed macOS account modes may be rare, but the current "any CloudStorage path wins" rule is too coarse for a multi-root tool.

**Urgency:** medium-low. Cross-platform correctness gap limited to macOS mixed-account/migration setups.

Touches: `src/dbxignore/_backends/macos_xattr.py`; `tests/test_macos_xattr_unit.py`; possibly `markers.detection_summary()` wording.

## 9. `dropbox_root` fixture centralization (design-tension record)

About 27 sites across `tests/test_cli_apply.py`, `tests/test_cli_clear.py`, and `tests/test_cli_status_list_explain.py` use the inline pattern `monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])`. `tests/test_cli_symlink_path_args.py:87` packages this as a `dropbox_root` fixture that creates a real `Dropbox` subdirectory under `tmp_path` and points `_discover_roots` at it.

The fixture form removes the inline duplication; the inline form is more explicit at the test site (a reader can see exactly what's being monkeypatched). Filed for the design-tension record (precedent: items #4 / #5); current dual shape is defensible.

**Fix candidates:**

- Move `dropbox_root` to `conftest.py`. Migrate the ~27 inline sites to use it. Many tests would shrink by 2-3 lines each. Risk: tests that depend on the *exact* `tmp_path` rather than `tmp_path / "Dropbox"` need separate handling.
- Leave the dual pattern in place; the inline form is defensible as more explicit at the test site.

**Urgency:** very low. Pre-existing duplication; the 27th instance is packaged as a fixture rather than inline. Awaits a rule-of-three trigger from a fourth context that wants the fixture pattern.

Touches: `tests/conftest.py`; ~27 sites across `tests/test_cli_*.py`.

## 10. `FileNotFoundError`-before-`OSError` 'vanished path' idiom now repeats across three modules

The specific-before-general pattern that handles "the path I'm about to read or write may have vanished between listing and access" — a benign race window on a live tree — now lives at seven call sites across three modules:

- `src/dbxignore/reconcile.py:160,206` — `_reconcile_path` read and write arms. `FileNotFoundError → logger.debug + return None`. The established precedent; the docstring at the top of the module captures the contract.
- `src/dbxignore/state.py:215` — `_read_at`. `FileNotFoundError → return None` (silent, because the file-doesn't-exist case is the first-run common case and emitting a WARNING would spam every fresh install's log).
- `src/dbxignore/cli.py` (`uninstall --purge` marker loop) — four sites: root `is_ignored`, root `clear_ignored`, child `is_ignored`, child `clear_ignored`. Each has a `FileNotFoundError` arm before the broad `OSError` arm; the local response varies (`root_marked = False`, `pass`, `continue`).

The idiom is recognizable at each site, but the **response action** after the FNF catch varies enough that a generic helper would need to express the post-FNF behavior in a parameterized way. Two candidate shapes:

- A `suppress_vanished()` context manager — fits the silent-pass purge clear arms cleanly, but loses the read arms' need to set local state (`root_marked = False`) or jump to the next iteration (`continue`). Those sites would still need their own try/except, defeating the dedup.
- A wrapper function like `read_marker_or_none(path) -> bool | None` — specific enough to fit one caller's shape (the purge read loop), but would need a different signature for each call site (state.py wants `State | None`; reconcile wants the read tuple).

Surfaced during a review cycle (the third site needing the idiom was identified by an automated review on the purge loop).

**Fix candidates:**

- **Extract a `suppress_vanished()` context manager** and apply it at the silent-pass sites only. Pros: removes two-line `except FileNotFoundError: pass` boilerplate at the purge clear arms. Cons: the read sites cannot use it (they need to set a flag or `continue`), so the helper would only apply to a minority of the seven sites.
- **Status quo.** Each site's two-line `except FileNotFoundError: <local action>` reads in context; the local action varies meaningfully. The contract is documented in `reconcile.py`'s module docstring, so a future implementer encountering the idiom at a new site has a reference.

**Recommendation:** keep as-is. Precedents #4 (dual `paths` for-loops in macOS xattr backend), #5 (`install/__init__.py` platform dispatch), #9 (`dropbox_root` fixture) — same shape: rule-of-three triggered, helper extraction is tempting but the local variation makes the generic shape less clear than the duplicated idiom. Await a fourth concrete site or a real maintenance burden before reopening.

**Urgency:** very low. Code-quality observation; current per-site shape is defensible and the idiom is documented.

Touches: `src/dbxignore/cli.py`, `src/dbxignore/state.py`, `src/dbxignore/reconcile.py` (one helper added, ~3-7 call sites updated depending on which extraction shape is picked).

## 12. Stale uv tool venv survives failed uninstall, partial state on next install

When `uv tool uninstall dbxignore` fails mid-cleanup (e.g. the Windows daemon is still running and holds `dbxignorew.exe` mapped), the tool venv at `%APPDATA%\uv\tools\dbxignore` (or platform equivalent) is left behind in a partially-cleaned state — the entry-point shims under `~/.local/bin/` may be removed but the venv's `site-packages/` survives. A subsequent `uv tool install .` detects the existing venv and does an *incremental update* rather than a fresh install: only changed packages get reinstalled, others (psutil, watchdog, etc.) are retained from the prior install. Result: a hybrid venv with packages from two different install events that can produce subtle compatibility issues — partially-initialized module errors on Python 3.14, daemon hangs during initial sweep, etc.

Surfaced when a Phase 7 `uv tool uninstall` failed (daemon-running race) and the follow-up `uv tool install .` produced a venv where freshly-installed dbxignore + click/colorama/pathspec/markdown-it-py/mdurl coexisted with carryover psutil 7.2.2 + watchdog + pygments + rich + rich-click from the prior install. The daemon hung silently after the slow-sweep WARNING (likely a watchdog C-extension state issue), and `uninstall --purge` raised an opaque `SystemError` chain rooted in a `psutil` circular import.

**Fix candidates:**

- (1) Add a Phase 0 sanity check: if `~/.local/bin/dbxignore.exe` exists but `uv tool list | grep dbxignore` returns empty, the previous install was partially cleaned. Abort with instructions to remove the orphan venv directory.
- (2) Force `uv tool install --reinstall .` in Phase 2 (overrides incremental-update behavior). Trades fresh-build cost on every test run for hygiene.
- (3) Document recovery sequence in script docstring: `Remove-Item %APPDATA%\uv\tools\dbxignore -Recurse -Force` after a failed uninstall before next install attempt.

**Urgency:** low. Occurs only when a prior test run aborted mid-cleanup (script-internal precondition). Each manual-test run is supposed to leave a clean state on the happy path. But contributors hitting the failure mode lose substantial debugging time.

Touches: `scripts/manual-test-windows.ps1` (Phase 0), `scripts/manual-test-{ubuntu-vps,macos}.sh`.

## 13. `_DeferredEvents.drain` redispatches serially on the worker thread; large bursts could delay Phase 2

`daemon._sweep_once`'s drain block (`daemon.py:946-953`) iterates `deferred.drain()` and calls `redispatch(event)` synchronously per event before Phase 2 reconcile begins. Each `redispatch` runs `_dispatch` which can call `reconcile_subtree` synchronously. If a burst of N OTHER events lands during the ~50s initial-sweep window on a large tree, the drain's wall-clock becomes `N × per-event reconcile cost` — directly delaying Phase 2's start.

Practical bound: Phase 2 follows immediately after drain and reconciles every root anyway, so drained events are mostly redundant with Phase 2 — they exist to provide faster reaction time inside the startup window, not to be a load-bearing event queue. A burst large enough to delay Phase 2 by seconds is dominated by Phase 2's wall-clock in any case.

**Fix candidates:**

1. **Cap the queue at a few hundred entries.** `_DeferredEvents.append` returns False when full; the caller (`_dispatch`) treats it as "cache_ready set, dispatch directly" — except with `cache_ready` still actually False, the gate above skips the event. Effectively: overflow drops events; Phase 2's full walk catches them. Crisp boundary, minimal LOC.
2. **Track drain wall-clock; if it exceeds N seconds, abort and let Phase 2 handle the rest.** More dynamic; harder to test deterministically.
3. **Defer.** The bound is not observable today — no report of a startup-window event burst large enough to matter. Document the trade-off where `_DeferredEvents` lives (currently the docstring at line 177-197 describes the protocol but not the unbounded-queue assumption).

**Urgency:** low. No observed problem. Bundle with the next `daemon._DeferredEvents` edit.

Touches: `src/dbxignore/daemon.py` `_DeferredEvents` class (~10 LOC for cap if fix candidate 1); `tests/test_daemon_synthetic_events.py` (new overflow test).

## 16. Two-tier ignore/skip rule structure as an alternative to interleaved negations

The rule model is single-tier: one gitignore-style spec per `.dropboxignore`, with `!pattern` negations the only re-include mechanism. Negations under an ignored ancestor are dropped (`is_dropped`) because Dropbox's folder-inheritance model genuinely cannot express them. An alternative authoring model would split each file into two independent specs — an ignore-spec and a separately-evaluated skip-spec — instead of interleaving negations into one list.

The honest scope: this does **not** bypass the ancestor-inheritance constraint (nothing can — that's a Dropbox limitation, not a dbxignore one). It is purely an authoring-ergonomics RFC — separating "what to ignore" from "what to never ignore" into two lists can read more clearly than interleaved `!` lines, and it sidesteps the conflict-detection pass entirely for the skip side. `is_dropped` is the current answer and it is defensible.

**Fix candidates:** none yet — RFC only. Would need a spec defining how a two-tier file is parsed, how it interacts with hierarchical `.dropboxignore` files up the tree, and a migration story for existing single-tier files.

**Urgency:** low. Filed for the design-tension record. Awaiting trigger: a concrete authoring case where `is_dropped` UX is demonstrably insufficient.

Touches: `src/dbxignore/rules.py` (parse + match model); `src/dbxignore/rules_conflicts.py` (skip-spec changes the conflict surface); `README.md` (rule-syntax docs).

## 17. Evaluate `igittigitt` as a `pathspec` replacement

Rule matching is built on `pathspec` (a `GitIgnoreSpecPattern` subclass) plus dbxignore's own `rules_conflicts.py` static conflict detector and `is_dropped` annotation machinery. `igittigitt` is another gitignore-matching library; if its hierarchical-file and negation handling is at parity, some of the home-grown machinery around `pathspec` might collapse.

**Fix candidates:**

1. **30-minute spike** — run the existing rules test corpus through both libraries, diff the match results. If `igittigitt` diverges on any case dbxignore relies on, stop there; the answer is "no".
2. If parity holds, scope a follow-up RFC for the actual migration — but note the conflict detector and `is_dropped` are dbxignore-specific regardless of the underlying matcher, so the simplification ceiling may be lower than it looks.

**Urgency:** low. Awaiting trigger: the next time the rules layer needs significant work, do the spike first rather than extending `pathspec`-based code blind.

Touches: spike only — no production files until the spike says go.

## 18. Confirm watchdog doesn't rewalk subtrees on every directory event under burst load

The daemon uses watchdog's recursive observer. Some recursive-watch implementations re-walk a subtree on every Create/Rename/Remove event to keep their watch set current — a per-event cost that compounds badly under burst workloads (`git checkout` of a large branch, `npm install`). It's unconfirmed whether watchdog's Linux inotify backend does this internally.

This is a different axis from #7 (which is about the *number* of inotify watches, one per directory). #18 is about per-event *CPU cost* during bursts, regardless of watch count.

**Fix candidates:** investigation only — instrument `_dispatch` event-rate and wall-clock during a synthetic burst (create/rename/delete thousands of files under a watched root), and read watchdog's inotify-emitter source to confirm or rule out an internal rewalk. If a rewalk exists and is costly, the fix is a separate item.

**Urgency:** low. Awaiting trigger: a beta tester reports daemon CPU spikes during bulk file operations.

Touches: investigation — `src/dbxignore/daemon.py` (`_dispatch`, `_WatchdogHandler`) instrumentation; no production change until findings land.

## 19. Finer-grained intra-root parallelism for the initial/recovery sweep

#6's existing per-subdir fan-out parallelizes the sweep by fanning `reconcile_subtree` out across top-level subdirs (one worker per subdir). A tree with one very large top-level subdir and many small ones still bottlenecks on the single worker handling the big one — the fan-out granularity is "top-level subdir", not "directory frame".

**Fix candidates:**

1. **Bounded work pool below the top-level granularity** — a semaphore-bounded executor that fans out *within* a root's walk, so a lopsided tree balances across workers. Bound the worker count explicitly to avoid FD/thread exhaustion on deep trees.
2. **Defer** — for typical trees the existing top-level fan-out is adequate; this only matters when one subtree dominates wall-clock.

**Urgency:** low. Largely subsumed by #6's existing fan-out. Awaiting trigger: a profiled sweep where one subtree still dominates wall-clock after the existing fan-out.

Touches: `src/dbxignore/daemon.py` (`_sweep_once` fan-out); `src/dbxignore/reconcile.py` (`reconcile_subtree` would need a parallel-walk variant or an injectable executor).

## 20. Observer/callback hook on `RuleCache` mutations

`RuleCache` mutations (`load_root`, `reload_file`, `remove_file`) have no notification mechanism — a consumer that wants to react to rule changes has to poll. Not needed today: the daemon's reconcile is the only consumer and it's driven by watchdog events, not by observing cache state. A future TUI/GUI surface displaying live rule state would need this.

**Fix candidates:** a registered-callback list invoked after each mutation. Care required: mutations already run under the `_rules` `RLock`, and callbacks fired inside the lock must not re-enter it (and must not block, or they stall the debouncer thread). Likely fire callbacks *after* releasing the lock, with a snapshot passed in.

**Urgency:** low. Awaiting trigger: TUI/GUI work begins.

Touches: `src/dbxignore/rules.py` (`RuleCache` mutation methods); whatever consumer triggers the need.

## 21. Windows daemon occasionally writes `daemon_create_time: null` to `state.json`

While diagnosing a manual-test Phase 2 race, `state.json` was observed with `daemon_create_time: null` despite a populated `daemon_pid`. A subsequent observation in the same session showed `daemon_create_time` populated as a normal float (e.g. `1778913203.7717907`). Non-deterministic; both observations were on Windows. Not confirmed on Linux/macOS.

`state.is_daemon_alive(pid, create_time)` falls back to a PID-only check when `create_time` is None, silently disabling the PID-reuse-race protection that the create-time comparison provides. A recycled PID claimed by an unrelated process would register as alive in that mode. Happy-path liveness still works (the PID check covers it), so this doesn't surface as a user-visible bug — it just removes a safety net.

The same null-create_time state also defeats `install/windows_task.py:uninstall_task`'s wait loop: when state.json's PID is stale (different from the actual daemon), `is_daemon_alive(stale_pid, create_time=None)` returns False on the first poll, the loop breaks immediately, and `schtasks /Delete /F` runs while `schtasks /End` is still asynchronously stopping the real daemon. That race is what surfaced the symptom; the create_time-null path is the underlying enabler.

**Fix candidates:**

1. **Trace why `psutil.Process(os.getpid()).create_time()` returns or is captured as None on the daemon's first `state.write()` call.** Candidate causes: a transient psutil import-state issue on Python 3.14 (related to the symptom class in #12); a call-site path in `state.write()` where `create_time` isn't queried before the first early write; a `state.read` codepath that drops the field while round-tripping.
2. **Retry on null capture.** If the create_time read can legitimately fail, retry once after a brief delay; log a WARNING and write what's available so existing PID-only checks continue to work.
3. **Reject stale-PID writes earlier.** Independent of root cause — when `state.write()` updates `daemon_pid`, treat a None `daemon_create_time` as a write-error and skip persisting until both fields are available; the next sweep tick will retry.

**Urgency:** low. Doesn't surface as a user-visible bug on its own; affects only PID-reuse-race protection plus the `uninstall_task` wait when state.json is stale. Awaiting trigger: an actual PID-reuse incident, or independent reproduction of the null-capture write path.

Touches: `src/dbxignore/daemon.py` (startup `state.write` call site); `src/dbxignore/state.py` (`State.write`, `is_daemon_alive` if behavior changes); a new unit test in `tests/test_state.py` covering both the null-capture path and the round-trip.

## Status

### Open

Sixteen items. Most are passive (no concrete trigger requires action) — bundle each with the next code-touch in its respective layer.

- **#1** — Intel Mac (x86_64) Mach-O binary build leg. dbxignore ships arm64-only Mach-O binaries; Intel users install via PyPI. Awaits demand signal.
- **#2** — Universal2 macOS binary as the single artifact. Quality-of-life cleanup; mutually exclusive with #1. Defer until item #1 actually triggers.
- **#3** — Codesigning + notarization for macOS binaries. Smooths Gatekeeper UX but requires $99/yr Apple Developer membership. Awaits concrete pain signal.
- **#4** — Dual `paths` for-loops in `_detected_attr_name()` could share a `_first_match` helper. Reviewers disagreed: one proposed extraction, another argued the dual structure correctly documents priority semantics. Filed for the design-tension record; current shape is defensible. Awaits a third predicate (rule-of-three trigger).
- **#5** — `install/__init__.py` platform dispatch duplicated across `install_service`/`uninstall_service`. Filed for the design-tension record (precedent: #4); current 6-block shape is defensible vs a factored-out helper that would introduce stringly-typed action coupling.
- **#6** — Initial-sweep wall-clock on a fresh install (no existing markers) is ~49.62s on a 27k-dir tree. The early `state.json` write keeps systemd readiness unblocked and `_sweep_once` fans out across top-level subdirs, but the sweep itself still costs ~50s on a tree this size. The persisted sweep-complete hint candidate (~80 LOC) remains open — reliability concerns on network FS / File Provider mtime semantics; no fired trigger yet.
- **#7** — Watchdog observer's recursive watch schedules one inotify watch per directory under `~/Dropbox`, including marked-ignored subtrees. Architectural fix (per-directory watches with mark/unmark lifecycle) is ~200 LOC of race-condition-prone state-machine work; deferred until a beta tester hits the watch ceiling on a system with limits already raised.
- **#8** — macOS sync-mode detection is process-global; mixed legacy/File-Provider account setups may need per-root or write-both behavior.
- **#9** — `dropbox_root` fixture from `test_cli_symlink_path_args.py` packages the ~27-site inline `monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])` pattern across `test_cli_apply.py` / `test_cli_clear.py` / `test_cli_status_list_explain.py`. Filed for design-tension record (precedent: #4, #5); current dual shape is defensible.
- **#10** — `FileNotFoundError`-before-`OSError` 'vanished path' idiom now repeats across `reconcile._reconcile_path` (2 sites), `state._read_at` (1 site), and `cli.uninstall --purge` (4 sites). Filed for design-tension record (precedent: #4, #5, #9); current per-site shape is defensible because the local response action varies (return None / set flag / continue / pass) and no generic helper fits all seven sites.
- **#12** — Failed `uv tool uninstall` leaves the venv directory behind; the next `uv tool install` does an incremental update instead of fresh install, producing a hybrid venv (mixed install-event packages). Triggers subtle import / C-extension issues. Recovery is manual `Remove-Item ~\AppData\Roaming\uv\tools\<pkg>`.
- **#13** — `_DeferredEvents.drain` redispatches serially on the worker thread before Phase 2 starts; a large startup-window burst could delay Phase 2's wall-clock unnecessarily. Mostly redundant with Phase 2 anyway. No observed problem.
- **#16** — Two-tier ignore/skip rule structure as an alternative to interleaved negations. RFC only; does not bypass Dropbox's ancestor-inheritance constraint — purely an authoring-ergonomics question. `is_dropped` is the defensible current answer. Awaiting a concrete UX-insufficiency case.
- **#17** — Evaluate `igittigitt` as a `pathspec` replacement. 30-min spike (diff both libraries against the rules test corpus) before the next significant rules-layer change; conflict detector + `is_dropped` stay dbxignore-specific regardless, so the simplification ceiling may be modest.
- **#18** — Confirm watchdog doesn't internally rewalk subtrees on every directory event under burst load. Per-event CPU cost axis, distinct from #7's watch-count axis. Investigation only. Awaiting a beta-tester CPU-spike report during bulk file ops.
- **#19** — Finer-grained intra-root sweep parallelism below #6's top-level-subdir fan-out granularity. Matters only for trees where one subtree dominates wall-clock after the existing fan-out. Awaiting a profiled lopsided-tree case.
- **#20** — Observer/callback hook on `RuleCache` mutations. Not needed until a TUI/GUI surface wants live rule state; callbacks must not re-enter the `_rules` lock. Awaiting TUI/GUI work.
- **#21** — Windows daemon occasionally writes `daemon_create_time: null` to `state.json`; non-deterministic, both observations on Windows. Silently disables PID-reuse-race protection in `is_daemon_alive` AND defeats `uninstall_task`'s wait loop when state.json's PID is stale (the underlying enabler of the manual-test Phase 2 race fixed in `fix/manual-test-phase2-cleanup`). Awaiting trigger: independent reproduction or a real PID-reuse incident.

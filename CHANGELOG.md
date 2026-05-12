# Changelog

All notable changes to dbxignore are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Windows Explorer right-click integration. `dbxignore install` now registers
  two HKCU shell verbs — "Ignore from Dropbox" and "Restore to Dropbox" —
  scoped to discovered Dropbox roots via an `AppliesTo` query filter. Pass
  `--no-shell-integration` to opt out of the registry write; pass it to
  `uninstall` to preserve the verbs across a daemon reinstall; `uninstall
  --purge` always removes them. The asymmetric command bindings reflect the
  data-loss asymmetry between the two directions: "Ignore" runs without
  `--yes` (confirmation in the spawned console), "Restore" runs with `--yes`
  (one-click safe). On Linux and macOS the flag is silently accepted as a
  no-op so portable scripts work unchanged. (#65)

### Changed

- **Breaking** — `dbxignore` CLI default log level lowered from `INFO` to `WARNING`. The `--verbose` / `-v` flag is now a counted form: `-v` selects `INFO` (was `DEBUG` under the old boolean flag), and a new `-vv` form selects `DEBUG`. Net effect: `dbxignore install` and other CLI commands no longer emit `logger.info` chatter from install backends and other modules alongside the intentional `click.echo` summary lines by default. Operators wanting the previous noise level can pass `-v`; full debug traces are still available via `-vv`. The `dbxignored` daemon foreground entry point gets the same change for symmetry; the daemon's own `_configured_logging()` (driven by `DBXIGNORE_LOG_LEVEL`) takes over after startup, so this only affects the brief pre-startup window. (#114)

### Fixed

- `windows_ads._stream_path()` emits the `\\?\UNC\server\share\…` long-path form for UNC paths rather than concatenating `\\?\` with the leading `\\` (which produced the malformed `\\?\\\server\share\…`, undefined to the Win32 object manager). Dropbox roots on network shares, redirected profiles, or other UNC-backed locations now receive ignore markers correctly; drive-letter paths are unchanged. (#96)

## [0.5.1] — 2026-05-12

Patch release. Pins LF line endings on `dbxignore generate` and `dbxignore init` writes on Windows (two `cli.py` write sites that pre-dated v0.5.0's `_atomic_write_rule_file` LF pin and were never folded into the same convention); plus a README symmetry fix so the install-verification step works on every platform.

### Fixed

- **`dbxignore generate` and `dbxignore init` now pin LF line endings on Windows.** The two `cli.py` write sites used Python's `newline=None` default from inception, translating `\n` → `\r\n` on Windows. v0.5.0's PR #207 pinned `newline=""` on the parallel `rules._atomic_write_rule_file` write site used by `ignore`/`unignore`, but the two direct-write sites in `cli.py` were not folded into that convention. `generate`'s byte-for-byte invariant broke when the source `.gitignore` was LF-canonical; `init`'s LF-canonical output contract broke on every Windows install. Both call sites now pass `newline=""` explicitly so the bytes written match the bytes intended. Resolves BACKLOG #110.

### Changed

- **README install sections now symmetrically end with `dbxignore status` as the verification step.** Previously only the Linux section had a verification command (`systemctl --user status dbxignore.service`); Windows and macOS had none. A reader scrolling for "did the install work" could land on the wrong section's command and run a Linux-only `systemctl` invocation on PowerShell. The Linux section's `systemctl --user status` and `journalctl --user -u dbxignore.service` references move into the following prose paragraph as "for systemd-level unit state or recent log output."

### Internal

- Five manual-test-script fixes (Phase 4 `--yes` flag for Linux/macOS bash scripts; Phase 5f `set -o pipefail` + `grep -q` false-failure on a multi-line click producer; Windows Phase 5 case 5e narrowed to a single file; case 4m target renamed to avoid an ancestor-rule mask; Windows Reset-TestDir retry loop for transient file-lock contention).
- Three new gotcha entries in `docs/internals/active-gotchas.md`: SIGPIPE+pipefail with `grep -q` on Python producers; `pre-commit install` needs explicit `--hook-type` flags for commit-msg + pre-push; uv tool cache can serve a stale wheel after `uv tool uninstall`.

## [0.5.0] — 2026-05-12

Reliability sweep across destructive verbs, rule cache, and path validators. Adds two new CLI verbs (`ignore`/`unignore`) for per-path rule management; changes the symlink-handling contract on path-taking verbs so they operate on the link object rather than the target. Multiple Breaking callouts — see entries below.

### Fixed

- **Conflict detector no longer drops effective negations under children-only patterns.** Previously, a `.dropboxignore` containing `build/*` followed by `!build/keep/` had the negation reported as a conflict and dropped from the active rule set, leaving `build/keep` marked ignored. The detector now distinguishes directory negations whose target is matched by an earlier include (pathspec last-match-wins handles these — the negation's own match overrides the include for the target itself) from negations whose path lives strictly under a marked ancestor directory (Dropbox's directory inheritance makes those inert). Implemented via a `strict` flag on `_ancestors_of` (skip the target itself for directory negations whose `raw == prefix`) and last-match-wins logic in `_find_masking_include` (account for negations between an earlier include and the current rule). Behaviour change: `build/` + `!build/keep/` continues to flag (`build/` marks the dir itself, inheritance overrides the negation); `build/*` + `!build/keep/` and `build/*` + `!build/keep/` + `!build/keep/**` no longer flag and now take effect — `build/keep` stays unmarked and Dropbox keeps it in sync.

### Added

- **`dbxignore generate` warns at write time about dropped negations in the source.** After producing the `.dropboxignore` (or before emitting it on `--stdout`), the static conflict detector runs against the source as a self-contained rule set; any conflicts are listed on stderr with line numbers and the masking rule. The byte-for-byte invariant of `generate` is preserved — the warning is informational, the file content is unchanged. Within-file conflicts only; cross-file conflicts (a `.dropboxignore` higher in the tree masking a negation in this one) still surface only at runtime via `dbxignore status` / `explain`.

### Changed

- **`dbxignore --help` and per-command help text render with colors, panels, and inline-code highlighting.** Switched the CLI from `click` to `rich-click` (drop-in import alias) with `TEXT_MARKUP = "markdown"`, so single backticks in command/option help text render as colored monospace tokens, sections appear in panels, and option flags are highlighted. Converted the existing rST `` ``foo`` `` literals in `cli.py` docstrings to Markdown `` `foo` `` form (37 sites). Adds `rich-click>=1.8` to dependencies (transitively pulls in `rich`, `markdown-it-py`, `mdurl`, `pygments`).

### Added

- **`dbxignore apply --dry-run` previews what would be marked/cleared without mutating.** Threads a `dry_run: bool` keyword-only parameter through `reconcile_subtree` → `_reconcile_path`; when set, marker mutations are skipped and the would-be paths are recorded in new `Report.would_mark` / `Report.would_clear` lists. CLI emits per-path `would mark: <p>` / `would clear: <p>` lines (sorted for determinism) followed by an `apply --dry-run: would_mark=N would_clear=N errors=N (no changes made)` summary. Works with the `--from-gitignore <path>` variant. Steady-state daemon sweeps continue to use `dry_run=False` (the default), so the new `Report` lists stay empty and per-sweep memory is unchanged.
- **`dbxignore init [PATH]` scaffolds a starter `.dropboxignore`.** Writes a packaged template (Node.js, Python, Rust, JVM, .NET, frontend frameworks, build/dist outputs, OS detritus) into `PATH` (or cwd). Walks the target tree to depth 3 looking for marker-bait dirs (`node_modules`, `__pycache__`, `.venv`, `target`, etc.) and annotates the generated header with which ones were detected — informational, the file content is always the full template per the "edit-down beats edit-up" UX choice. `--force` overwrites an existing `.dropboxignore`; `--stdout` previews without writing. The packaged template ships at `src/dbxignore/templates/default.dropboxignore` (loaded via `importlib.resources`); editing it updates the `init` output for all installs after the next release. README §"First-time setup" added.
- **`dbxignore clear [PATH]` clears every ignore marker under the watched roots.** Inverse of `apply`: where `apply` sets every marker the rules dictate, `clear` unsets every marker regardless of rules. Leaves `.dropboxignore` rule files and `state.json` untouched (`uninstall --purge` is the heavier verb that also wipes state). Refuses to run when the daemon is alive — the daemon's next sweep (rule-reload events within seconds; recovery sweep within the hour) would re-apply rule-driven markers and undo the clear; pass `--force` for known short-window tests. Confirmation prompt fires by default since Dropbox starts syncing previously-ignored paths immediately after the clear (potentially gigabytes); pass `--yes` for scripted use. `--dry-run` previews what would be cleared without touching markers. Optional `PATH` argument scopes the walk to a subtree, parallel to `apply`. README §"Clearing all markers" added.
- **`dbxignore status --summary` emits a stable single-line summary on stdout.** Format is `state=<token> [pid=N] marked=N cleared=N errors=N conflicts=N` with state tokens `running` / `not_running` / `no_state`. Suitable for status-bar widgets (polybar, tmux, i3blocks, sketchybar) and cron-friendly polling — replaces the multi-line human output with a stable contract callers can parse. The format is treated as public API per SemVer: field additions are non-breaking, renames/removals bump MINOR pre-1.0 / MAJOR post-1.0. README §"Status-bar integration" added.

### Changed

- **`dbxignore status` daemon-liveness check is more accurate and the "not running" message clearer.** New shared helper `state.is_daemon_alive(pid)` verifies BOTH that the PID exists AND that the process at that PID is plausibly a dbxignore daemon (matches `python` or `dbxignored` in the process name). Previously `cli._process_is_alive` did only `psutil.pid_exists` — a recycled PID claimed by an unrelated process registered as "alive" (false positive). The "not running" branch now reads `daemon: not running (last pid=X — state.json may be stale)` instead of just `daemon: not running (pid=X)`, distinguishing a cleanly-stopped daemon from stale state. Internal: `daemon._is_other_live_daemon` deduplicated to delegate to the new helper, with the singleton-check-specific `pid == os.getpid()` self-exclusion preserved.

### Added

- **`dbxignore status` shows the macOS sync-mode detection result on darwin.** A new `sync mode: <mode>: <reason>` line in `dbxignore status` (and an INFO log line `sync mode detection: ...` at daemon startup) surfaces what the path-primary + pluginkit-disambiguating detection landed on (`legacy:` / `file_provider:` / `both:`) without needing `DBXIGNORE_LOG_LEVEL=DEBUG`. The line prints only on darwin — Windows and Linux are single-attribute platforms with nothing to detect. Cross-platform via the new `markers.detection_summary()` facade returning `None` on non-darwin.

### Changed

- **macOS xattr backend writes both attributes when sync-mode detection is genuinely uncertain.** When `pluginkit` is unavailable (binary missing, hung query, subprocess error) AND `~/.dropbox/info.json` gave no decisive path signal (no path under `~/Library/CloudStorage/` and no `/Volumes/` path with extension allowed), `_detect()` now returns `[ATTR_LEGACY, ATTR_FILEPROVIDER]` and `set_ignored` writes both names. Previously this case fell through to a single-attribute legacy default, which silently no-ops on File Provider users whose detection misfires for environmental reasons. `is_ignored` short-circuits True on the first non-empty hit (so legacy users don't pay two getxattr calls per file); `clear_ignored` iterates with per-attribute ENOATTR no-op. The trade is a stray attribute on the inactive sync stack (metadata cleanliness) versus a silent no-op on the active stack (correctness).
- **`_WatchdogHandler.on_any_event` fast-paths matched `DIR_CREATE` events.** When a freshly-created directory's path already matches a cached rule, the handler calls `reconcile_subtree` synchronously and skips `Debouncer.submit`. The marker write therefore lands without the per-kind debouncer queue's queueing + worker-thread context-switch cost, narrowing the race window where Dropbox's own watcher sees the new directory and starts ingesting children before the parent's marker is set. Other event kinds (`RULES`, `OTHER`) and unmatched `DIR_CREATE`s still go through the debouncer. Trade-off: a queued-but-unprocessed `RULES` event that would invalidate the match means the bypass marks a path the next `reconcile_subtree` (driven by that `RULES` event) clears — bounded transient false-positive.
- **Breaking** — `dbxignore status --summary` now emits a fourth state token `state=starting` while the daemon is alive but the initial sweep has not yet completed. During the starting window, the summary line contains only `state` and `pid` (no `marked`/`cleared`/`errors`/`conflicts` — those would all be 0 and falsely imply a completed sweep). Consumers branching exhaustively on `state == "running"` need to handle the new value. Pre-1.0, this rides the next MINOR version bump per the SemVer note in CLAUDE.md. Resolves BACKLOG #53 candidate 1.
- **`daemon._sweep_once` parallelizes across each root's top-level subdirs.** Each root contributes one `descend=False` reconcile (root's own marker, no walk) plus one `descend=True` reconcile per immediate child to a single `ThreadPoolExecutor` capped at `os.cpu_count()`. The previous shape spawned one worker per root, leaving a single 27k-dir Dropbox account's walk single-threaded. The fan-out subsumes the prior multi-root pool — every (root, child) pair lands in the same pool. `reconcile_subtree` gains a `descend: bool = True` parameter; default callers (CLI `apply`, watchdog dispatch) keep their existing semantics. Resolves BACKLOG #53 candidate 3.
- **`RuleCache.load_root` cancels per directory rather than per rule-file yield.** Replaces `root.rglob(IGNORE_FILENAME)` with `os.walk(root, followlinks=False)`; the `stop_event` check now fires for every directory visited, regardless of whether any `.dropboxignore` file is present. The previous rglob-based shape checked between yields, so a tree with many directories and few rule files (worst case: zero rule files in a 100k-directory tree) had cancellation latency bounded by rglob's internal traversal time — observable as systemd-shutdown waits up to the `TimeoutStopSec=90s` default. Case-insensitive existence is preserved via `Path.is_file()`. Resolves BACKLOG #86.
- **Mixed-case `.dropboxignore` filenames are recognized end-to-end.** A new `is_ignore_filename(name)` predicate (case-insensitive `.dropboxignore` check) and `_canonical_cache_key(path)` helper (lowercase-basename normalization) replace exact-case `name == IGNORE_FILENAME` checks at every site that recognizes rule files: `match`, `explain`, `reload_file`, `remove_file`, `_load_file`, `_load_if_changed`, `daemon._classify`, `daemon._moved_dest_under_root`, `daemon._dispatch`'s move-event arm, `reconcile._reconcile_path`'s rule-file-marked-ignored warning, and `cli.uninstall --purge`'s symmetric warning during the marker-auto-clear walk. End state: a `.DropboxIgnore` (mixed casing on disk) is treated identically to `.dropboxignore` across discovery, watchdog events, cache mutations, and reconcile walks regardless of filesystem case sensitivity. Pre-fix, watchdog edits to mixed-case files had up-to-1-hour staleness on case-sensitive filesystems (Linux ext4, case-sensitive APFS) because `_classify` filtered them out; the cache also held entries under non-canonical keys after `reload_file`, so subsequent `match()` lookups silently missed. Resolves BACKLOG #92.

### Fixed

- **`state.write()` parse-back validation.** Between writing the temp `state.json.tmp` and `os.replace`-ing it into place, `state.write()` now reads the temp file back and `json.loads`-parses it; on `JSONDecodeError`, the temp is unlinked and the exception re-raised, leaving any prior `state.json` untouched. Closes a latent path where a future serializer regression producing malformed JSON would otherwise reach disk, then `_read_at`'s `JSONDecodeError` arm would silently fall through to "no prior daemon" and bypass `daemon.run`'s singleton check — the same failure mode the v0.3.1 atomic-write change defended from torn JSON.

### Added — path-taking `ignore` / `unignore` verbs

- **`dbxignore ignore <path>` and `dbxignore unignore <path>` add or remove a rule for a single path.** `ignore` selects the closest `.dropboxignore` ancestor (or creates one at the Dropbox root), appends a literal-path rule via the new `rules.format_literal_rule()` (gitignore-anchored, with escaping for `*`, `?`, `[`, `]`, `\`, and a column-0 `!`/`#` guard), then sets the marker via `reconcile`. `unignore` does the inverse: removes any rstrip-matching rule from the file, then clears the marker. The order of operations is rule-first-then-marker so the daemon's debouncer can't trigger a spurious clear in the 500 ms window between the two operations. `unignore` fails loud with exit 2 and a friendly stderr message when a non-removable wildcard rule (`**/<path>/`, etc.) would still match the path after the literal rule is removed — pointing at the blocking rule. Both verbs go through the standard validator (Dropbox-root containment, symlinked-ancestor refusal, `..`-after-symlink rejection). Resolves BACKLOG #93.

### Fixed — top-level target / root markers

- **`clear`, `list`, and `uninstall --purge` now include the top-level target's own marker.** `_walk_marked_paths(target)` checks `target` before invoking `os.walk` and returns `[target]` immediately when the target itself is marked, preserving the "marked directories prune descendants" behavior. `uninstall --purge` performs the same root pre-check before traversing. Previously, a marker on a discovered Dropbox root, or on a directory passed as a `clear`/`list` argument, was silently skipped — `os.walk` enters at the root and only yields children. The same pre-check makes `list <marked-dir>` and `clear <marked-dir>` round-trip cleanly with `apply <marked-dir>`. Resolves BACKLOG #94.

### Changed — symlink-object preservation in path-taking verbs

- **Breaking** — `apply`, `clear`, `list`, `explain`, `check-ignore`, `ignore`, and `unignore` now operate on the symlink **object** when handed a symlink argument, rather than following it. The new shared validator `_normalize_under_root` uses `path.absolute()` + `os.path.normpath` for path normalization, preserving symlinks (the prior `path.resolve()` form followed every link). Two filesystem-state-only guards land alongside: a symlinked-ancestor refusal (`apply`/`clear`/`list`/`ignore`/`unignore` on a path with a symlinked ancestor exits 2 because daemon-side reconcile walks with `followlinks=False` and would leave the marker stranded) and a walk-root short-circuit (`_walk_marked_paths` and `_run_apply_pass` skip the `os.walk`/`descend=True` when the explicit target is a symlink — `followlinks=False` does NOT stop `os.walk` from following the root, so an explicit guard is required). Pre-fix, `apply /Dropbox/link-to-external/` would have written markers in the external target tree and the daemon's own walk could never reach them. Resolves BACKLOG #95.

### Added — `..`-after-symlink rejection in path validator

- All path-taking verbs now refuse paths where `..` follows a symlinked component: `link/../file` collapses lexically to `parent/file` but the filesystem would resolve to `<target-of-link>/../file`, and silently operating on the lexical interpretation surprised users. The validator's new sticky-flag scan walks the un-normalized absolute path's segments, tracks `seen_symlink` via `os.path.islink(prefix)`, and exits 2 on the first `..` that follows a symlinked prefix. A `..` **before** any symlink remains accepted (it cancels a regular segment, no FS-divergence). The guard fires only when the lexical interpretation will be used — out-of-Dropbox alias paths that fall through to the existing `path.resolve()` fallback are unaffected (the resolve fallback is filesystem-true and unambiguous). Resolves BACKLOG #105.

### Changed — destructive verbs harden their failure surface

- **Breaking** — `cli.clear` now fails-closed when `state.json` is present but unreadable (locked, permission-denied, cloud-placeholder). The daemon-alive guard previously short-circuited on `s is None`, silently treating "unknown" as "no daemon" and proceeding with the marker wipe; a live daemon would then re-apply markers seconds later, leaving the user with no record of what happened. Now `clear` exits 2 with `error: state.json at <path> is present but unreadable; daemon liveness is unknown.` Pass `--force` to override. Same change-on-failure-mode shape as the existing daemon-alive guard.
- **Breaking** — `uninstall --purge` now reports per-path marker failures on stderr and exits 2 on any failure. New stderr format: `Could not fully clear markers (N errors):` followed by up to 10 lines of `  <op> failed on <path>: <message>` plus an `... and X more.` tail when applicable. Operations are tagged `read` (failure during `markers.is_ignored`) or `clear` (failure during `markers.clear_ignored`). The prior `except OSError: pass` shape silently swallowed permission errors, unsupported xattr/ADS operations, transient EIO, and symlink xattr edges; users saw `Cleared N` regardless of actual outcome and Dropbox could keep ignoring paths after the tool was supposedly fully removed. Local state cleanup (`state.json`, daemon logs, Linux systemd drop-ins) still runs after a partial marker failure — only the exit code and stderr report are new. Resolves BACKLOG #98.
- **`uninstall --purge` skips vanished paths silently.** `FileNotFoundError` raised by `markers.is_ignored` or `markers.clear_ignored` (a path listed by `os.walk` that disappeared before our call — Dropbox sync deleting an ignored path, an IDE moving a temp file, concurrent user activity) is no longer reported as a partial-failure error. Mirrors the `reconcile._reconcile_path` read arm's existing vanished-path treatment.

### Fixed — state-file and rule-cache robustness

- **`state.read()` no longer crashes CLI verbs on an unreadable `state.json`.** A locked, permission-denied, cloud-placeholder, or transiently-unavailable file would previously propagate `OSError` out of `_read_at`, crashing `dbxignore status`, `clear`'s daemon-alive guard, `daemon.run`'s legacy-state migration path, and Windows `uninstall`'s synchronous daemon-exit wait. The error path now matches the existing corrupt-state arm: log a `WARNING` and return `None`. The pre-`exists()` LBYL check is dropped in favour of catching `FileNotFoundError` from the read itself (closes a TOCTOU window where the file vanishes between the existence test and the read). Resolves BACKLOG #97.
- **`RuleCache` catches same-size `.dropboxignore` edits with preserved mtimes.** The cache-invalidation gate switched from `(mtime_ns, size)` stat values to a blake2b-128 hash of the file's bytes — a heuristic-vs-correctness trade. Same heuristic used by `git core.checkStat` and rsync's default mode; it misses content swaps when an editor preserves mtime AND the new content is the same size. The hash gate catches these cleanly; the expensive part of the load (pathspec compile) is still skipped when content is unchanged, so the optimization that the `(mtime, size)` shortcut was originally meant to provide is preserved. Resolves BACKLOG #102.
- **A `.dropboxignore` that turns into invalid UTF-8 is treated like a parse error.** Previously `_load_file`'s `read_text("utf-8")` would propagate `UnicodeDecodeError` uncaught and crash the sweep. New `read_bytes()` + explicit `decode("utf-8")` flow surfaces the decode failure to a deliberate arm that logs a `WARNING` and drops the cached entry — same shape as the existing pathspec parse-error arm.
- **CLI rule-file writes use a unique-name temp file.** `rules.append_rule` and `rules.remove_rule` (the implementation of the path-taking `ignore`/`unignore` verbs) previously wrote through a fixed `<rule_file>.tmp` name and the docstrings warned "not safe against concurrent writers." Now use `tempfile.mkstemp(dir=rule_file.parent, prefix=..., suffix=...)` so each writer picks a unique sibling — two concurrent CLI invocations, an editor's atomic-save backup, or a user-created `.dropboxignore.tmp` cannot collide on the path. The lost-update window (two readers, two writers, replace race) remains; an advisory lock is deferred until a concrete failure shows. Resolves BACKLOG #101.
- **CLI rule-file writes preserve the existing POSIX mode.** `tempfile.mkstemp` creates files at mode `0o600` for security; the `os.replace` step would have silently relocked group-readable `.dropboxignore` files to user-only after the first `ignore`/`unignore` invocation. The writer now captures the existing file's mode (or `0o666 & ~umask` for new files, matching what the prior `Path.write_text` produced) and `os.chmod`s the temp before the replace, so shared-workflow setups keep working.

### Changed — internal infrastructure

- **`.dropboxignore` files written by the CLI now use LF line endings on Windows too.** Was CRLF via Python's default text-mode `\n` → `\r\n` translation that the prior `Path.write_text(content, encoding="utf-8")` shape inherited from `open()`'s defaults. Gitignore-style files are LF-canonical; reads through `read_text("utf-8")` normalize CRLF→LF transparently, so no observable behavior change at the rule-cache or daemon layer — but the on-disk byte format is now platform-consistent for users committing `.dropboxignore` to git.
- **CI test workflow uses canonical `uv run python -m pytest`** (was `uv run pytest`). Aligns CI with the documented full-suite command per AGENTS.md, which warns the trampoline form can fail in stale environments with `ModuleNotFoundError` or `uv trampoline failed to canonicalize`. Resolves BACKLOG #103.
- **Manual-test scripts extended with Phase 4.5 cases 4s (clear fail-closed on unreadable state.json) and 4t (`..`-after-symlink rejection on `explain`), plus a Phase 6 regression guard for `uninstall --purge`'s new partial-failure stderr report.** Backfilled in PR #209 after the per-PR extension dropped across #203/#204/#205; the AGENTS.md "Manual test scripts" rule was strengthened from descriptive to a hard requirement at the same time.

## [0.4.0] — 2026-05-02

First release with macOS support. The beta-tester sign-off path (10-step checklist from the v0.4 spec § "Beta-test workflow") completed against `v0.4.0a5` on 2026-05-02; this release promotes that alpha to final without code changes.

The alpha cycle iterated through five tags (`a1` through `a5`) as beta-testing exposed each layer of the macOS port. `a1` was yanked from PyPI immediately after a CI publish-gating bug (PR #62 fixed). `a2` was a docs-only roll. `a3` shipped the cffi-bundling fix that finally let the macOS binary launch (PR #71). `a4` shipped the launchd-plist invocation fix that let the daemon actually start (PR #76) plus the first File Provider auto-detection (PR #77). `a5` shipped the path-primary mode-detection refinement (PR #79) that closed a system-level-vs-user-level signal-conflation gap caught by reasoning about the design — pre-tester-exposure. v0.4.0 is `a5`'s code, validated end-to-end on real macOS hardware (Tahoe 26.4 / Dropbox 250.4, File Provider mode), promoted to final.

The v0.4 ship cycle's headline behavior: dbxignore's macOS xattr backend auto-detects which Dropbox sync mode is active for each account (legacy ↔ `~/Dropbox/`, File Provider ↔ `~/Library/CloudStorage/<vendor>/`, plus an eligibility-gated external-drive variant) and writes the matching attribute name (`com.dropbox.ignored` or `com.apple.fileprovider.ignore#P` per [Dropbox docs](https://help.dropbox.com/sync/ignored-files)). Detection is path-primary (info.json's `path` field is the user-level mode signal) with pluginkit-disambiguation (Apple's PluginKit registry tells us about user-toggled extension state). Result is cached at module-load time so the per-file reconcile loop pays the detection cost exactly once per process.

Brief summary of what's landed across PRs #53 – #80:

### Added — macOS support

- **macOS xattr backend.** `src/dbxignore/_backends/macos_xattr.py` talks to the `com.dropbox.ignored` extended attribute via the `xattr` PyPI package with `symlink=True` (operates on the link itself, not its target). Three-function API matches the Linux and Windows backends. Symlinks are marked silently and successfully on macOS — the kernel allows `user.*`-style xattrs on symlinks via the NOFOLLOW path. (Linux symlinks cannot carry `user.*` xattrs — kernel restriction; the Linux backend logs `EPERM` and skips them. Divergence documented in CLAUDE.md.)
- **launchd User Agent installer.** `src/dbxignore/install/macos_launchd.py` writes `~/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist` and bootstraps it into the user's GUI session via `launchctl bootstrap gui/<uid>` / `bootout gui/<uid>/<label>`. `KeepAlive: {SuccessfulExit: False, Crashed: True}` matches systemd's `Restart=on-failure` semantics. Install requires that the user has logged into the GUI at least once since the last reboot — SSH-on-fresh-boot installs fail with `Bootstrap failed: 5: Input/output error` and need a GUI login + retry.
- **Conditional `xattr>=1.0` runtime dep** on `sys_platform == 'darwin'`. Linux and Windows installs don't pull it. Dev dep is also conditional on `sys_platform != 'win32'` because the package's C extension fails to build on Windows (no Windows wheel).
- **Split state vs. log directories on macOS.** `state.json` lives in `~/Library/Application Support/dbxignore/`; `daemon.log` and `launchd.log` live in `~/Library/Logs/dbxignore/`. Apple's app-data conventions. Windows and Linux behavior unchanged — state and log remain combined under `user_state_dir()`. New `state.user_log_dir()` function exposes the split; `daemon._log_dir()` now reads from it.
- **arm64 Mach-O binaries** ship to the GitHub Release. Built via `pyinstaller/dbxignore-macos.spec` on the `macos-latest` runner. Intel Mac users install via the universal Python wheel from PyPI.
- **`macos_only` pytest marker** + `macos-latest` CI matrix leg in `.github/workflows/test.yml`.
- **`prerelease:` expression on the GitHub Release publish step.** Tags containing `-a` (alphas) or `-rc` (release candidates) are auto-marked as pre-releases. Enables the v0.4 beta-test workflow: cut `v0.4.0a1` → GitHub Release auto-pre-release → PyPI publish stays gated until manual approval → beta tester downloads the binaries → promote to `v0.4.0` after sign-off.

### Changed

- **`detect_invocation()` extracted** to `src/dbxignore/install/_common.py`. Previously inlined as `_detect_invocation` in `linux_systemd.py`; now shared between linux + macOS installers.
- **`cli._purge_local_state()` refactored** to use a per-dir helper (`_purge_dir`) so it can clean both `user_state_dir()` AND `user_log_dir()` on macOS.
- **Project description string** mentions macOS.

### Fixed — Windows correctness

- **Per-machine Dropbox installs are now discovered.** `roots.discover()` checks both `%APPDATA%\Dropbox\info.json` (per-user installer) and `%LOCALAPPDATA%\Dropbox\info.json` (per-machine "install for all users") on Windows. Per-machine installs previously surfaced as "No Dropbox roots found" and required `DBXIGNORE_ROOT` as a manual workaround. `_info_json_path()` (singular, returning `Path | None`) refactored to `_info_json_paths()` (plural, returning `list[Path]`) — Windows arm yields up to two candidates in priority order; Linux/macOS arm unchanged. `discover()` iterates and uses the first existing file. Surfaced during v0.4 alpha testing on a per-machine Windows install.
- **`dbxignore install` now starts the daemon immediately on Windows.** Previously the Task Scheduler entry was registered but only kicked at the next user logon; users saw "Installed scheduled task" but `dbxignore status` reported no daemon until they logged out and back in. `install_task()` now runs `schtasks /Run /TN dbxignore` after `/Create` and treats `/Run` failures as non-fatal WARNINGs (the task is registered and will start at next logon regardless). Aligns Windows with Linux (`systemctl --user enable --now`) and macOS (`launchctl bootstrap` + `RunAtLoad: true`), both of which already started during install.

### Fixed — macOS binary bundling

- **macOS arm64 binary now bundles `_cffi_backend`.** v0.4.0a1 + v0.4.0a2 both shipped a Mach-O binary that failed at first launch with `ModuleNotFoundError: No module named '_cffi_backend'`. The xattr backend's import chain (`_backends/macos_xattr.py` → `xattr` → `cffi` → `_cffi_backend`) reaches a top-level C extension that ships *alongside* the `cffi` package on disk, not as a submodule — PyInstaller's static AST trace doesn't follow the sibling. The contrib hook for cffi normally adds it to hidden imports; on the v0.4.0a1/a2 builds it didn't fire (likely PyInstaller version drift, no version pin in the workflow's `--with pyinstaller` invocation). `pyinstaller/dbxignore-macos.spec` now lists `_cffi_backend` explicitly alongside the existing `watchdog.observers.fsevents` entry. Surfaced by the beta tester on M2 MacBook Air, macOS Tahoe 26.4 (`dbxignore --version`). Linux + Windows binaries unaffected.
- **Smoke-test step on both `build` and `build-macos` legs of `release.yml`.** After PyInstaller emits and before the artifact-upload step, `<binary> --help` runs the full import chain (the cffi class of regression fires at import time, before click parses argv). Catches the regression class for any future analyzer miss in any backend's transitive deps; the explicit `_cffi_backend` hidden import is the prior bullet's defense for the specific shape, the smoke test is the broader regression net.

### Fixed — macOS daemon launch and File Provider support

- **launchd plist now invokes `dbxignored`, not `dbxignore`** (PR #76). On v0.4.0a1–a3 macOS PyInstaller installs (and Windows PyInstaller installs structurally), `dbxignore install` wrote a launchd plist (or Task Scheduler XML) whose invocation target was the long-form `dbxignore` CLI binary with no subcommand. Click would parse argv as "group invoked without subcommand," print help, exit status 2; KeepAlive policy retried the same loop forever. The daemon never reached `daemon_mod.run()`. Beta-tester `launchctl print` showed `last exit code = 2 / runs = 4` and confirmed `program = /usr/local/bin/dbxignore` (the wrong binary). Fix: `install/_common.py:detect_invocation` and `install/windows_task.py:detect_invocation` frozen branches now resolve via a three-step rule — return `sys.executable` directly if it's already the daemon shim, else look for the `dbxignored` sibling next to it (the common case — both PyInstaller binaries ship as a paired set), else fall through to `(sys.executable, "daemon")` as defensive fallback. Linux unaffected — Linux installs reach the non-frozen branch which already routed correctly via `shutil.which("dbxignored")`.
- **macOS xattr backend auto-detects Dropbox sync mode** (PRs #77 + #79). Modern Dropbox on macOS runs in two distinct modes: legacy mode (Dropbox folder at `~/Dropbox`, synced by Dropbox's own daemon, watches `com.dropbox.ignored`) and File Provider mode (Dropbox folder at `~/Library/CloudStorage/Dropbox/`, synced by Apple's File Provider extension via `DropboxFileProvider.appex`, watches `com.apple.fileprovider.ignore#P`). File Provider has been the default since 2023; v0.4.0a3 hardcoded the legacy attribute name, silently no-op'ing on every File Provider install. Detection landed in PR #77 as pluginkit-primary, then refined in PR #79 to **path-primary, pluginkit-disambiguating**: read `~/.dropbox/info.json`'s `path` field (multi-account aware, handles `personal` + `business` keys), query pluginkit for the framework-level state, combine. Decision rules: extension disabled → legacy regardless of path; any account path under `~/Library/CloudStorage/` → File Provider; path on `/Volumes/<Drive>/` + extension allowed → File Provider (external-drive eligibility-gated case); otherwise → legacy. Why the refinement: PluginKit registration is a system-level fact (does macOS know about `DropboxFileProvider.appex`?). The user-level fact (which mode is *this account* in?) lives in info.json. v0.4.0a4 conflated the two and would have misdetected users who had Dropbox.app installed but had declined the File Provider migration. Cached after first call so the per-file reconcile loop pays the detection cost exactly once per process.

### Documentation

- **README** gains an "Install (macOS)" section between Linux and `.exe` install paths. Updated platform-support table now includes the macOS row. Logs and State sections list macOS paths.
- **README + CLAUDE.md: Windows OneDrive hardlink workaround.** `uv tool install` can fail with `ERROR_CLOUD_FILE_INCOMPATIBLE_HARDLINKS` (os error 396) when `%AppData%` is OneDrive-synced via Files On-Demand — the Cloud Files API refuses hardlinks on placeholder files. The documented workaround is `uv tool install --link-mode=copy git+...` or session-wide `$env:UV_LINK_MODE = "copy"`. Surfaced during v0.4 alpha install on a OneDrive-backed AppData.
- **CLAUDE.md** gains four new gotchas covering: the `xattr` package's `symlink=True` API surface (NOT `options=XATTR_NOFOLLOW` despite what Apple's libc docs suggest); macOS-only test pattern; the symlink-marking divergence between Linux/macOS/Windows; the launchctl bootstrap GUI-domain prerequisite; `_common.detect_invocation` shared module + the import-site monkeypatching gotcha.
- **`docs/release-notes/v0.4.0.md`** — hand-crafted GitHub Release body for promotion via `gh release edit v0.4.0 --notes-file ...`.

### Caveats

- **No Intel Mac binary in v0.4.** Pre-built binaries are arm64 only; Intel users install via PyPI. If demand surfaces, an x86_64 build leg will land in a point release.
- **Beta-validated, not field-validated at scale.** v0.4.0 is validated by a single beta tester on real hardware before tagging, but lacks the multi-user shake-out that Windows and Linux have accumulated. File issues for anything unexpected.

## [0.3.2] — 2026-04-26

Maintenance release. One silent-failure-mode bug fix that prevents a daemon-startup crash on shape-mismatched `state.json`, plus an internal cleanup in the watchdog dispatch path. **No breaking changes.** Upgrade is `pip install --upgrade dbxignore` (or download the new binaries) followed by restarting the daemon (`systemctl --user restart dbxignore.service` on Linux; log out / back in or `schtasks /Run /TN dbxignore` on Windows).

### Fixed

- **`state._read_at()` no longer crashes the daemon on shape-mismatched `state.json`.** The function caught `json.JSONDecodeError` from the JSON parser but called `_decode(raw)` *outside* the try/except. `_decode` directly indexed nested `last_error` sub-keys (`raw["last_error"]["time"]/["path"]/["message"]`), so a hand-edited or schema-mismatched `state.json` that's still valid JSON raised `KeyError`/`TypeError`/`ValueError` from `_decode`, propagated out of `daemon.run`'s singleton check, and the daemon crashed on startup. Recovery via systemd's `Restart=on-failure RestartSec=60s` was loud-and-slow — an unfortunate experience for the "user just upgraded and the daemon won't start" case. Fix: move `_decode(raw)` inside the try and broaden the except to `(json.JSONDecodeError, KeyError, TypeError, ValueError)`. Same recovery shape as before — log WARNING, return `None`, daemon treats the file as "no prior state" and starts fresh. Symmetric to the v0.3.1 atomic-write fix (which addressed the write-side torn-JSON case); together the two close the read/write halves of the same I/O contract.

### Changed

- **`daemon._classify()` now returns the root path along with the event kind and key.** Previously `_classify` called `find_containing(src, roots)` purely as a gate (return value discarded), then `_dispatch` called it again to obtain the actual root — two passes over the roots list per accepted watchdog event. The widened return shape (`tuple[EventKind, str, Path] | None`) eliminates the redundant lookup. Per-event work only — not in any per-file hot path. No observable behavior change.

## [0.3.1] — 2026-04-25

Maintenance release. Two silent-failure-mode bug fixes around state I/O and the per-file reconcile loop, a per-file `is_dir()` syscall cache in the match path, and a stale-doc cleanup. **No breaking changes.** Upgrade is `pip install --upgrade dbxignore` (or download the new binaries) followed by restarting the daemon (`systemctl --user restart dbxignore.service` on Linux; log out / back in or `schtasks /Run /TN dbxignore` on Windows).

### Fixed

- **`state.write()` is now atomic.** Writes go to `state.json.tmp` and `os.replace` into place — POSIX-atomic on Linux, `MoveFileExW(MOVEFILE_REPLACE_EXISTING)` on Windows. Previously a `path.write_text()` truncate-then-write could leave a zero-length / partial `state.json` if the daemon was SIGKILLed or the machine lost power mid-write; on next start, the singleton check would see corrupt state, treat it as "no prior daemon", and let a second daemon launch alongside the first. Narrow conjunction (hard crash within the few-ms write window AND the user re-runs the daemon before the prior process exits), but the failure mode was silent — two daemons writing markers concurrently, hard to attribute back to corrupt state. `uninstall --purge` also cleans a leaked `state.json.tmp` if one is found.
- **Per-file reconcile loop now survives generic `OSError` on the read side.** `_reconcile_path` previously caught `FileNotFoundError` and `PermissionError` only when checking a marker; any other `OSError` (e.g. `EIO` on a flaky network drive, or `ENOTSUP` from `getxattr` on a filesystem that doesn't support `user.*` xattrs at all) escaped the per-file try/except and silently killed the per-root sweep worker — markers stopped being maintained on that root and nothing surfaced in the report. The read-side except is now broadened to a generic `OSError` arm with errno classification, mirroring the existing write-side ENOTSUP/EOPNOTSUPP handling.
- **Flaky `test_daemon_reacts_to_dropboxignore_and_directory_creation` on Windows CI.** Widened the second `_poll_until` timeout from 3.0s to 5.0s. The test exercises a watchdog event-ordering race ("RULES before DIR_CREATE" — documented in the v0.2.1 negation-semantics spec as "masked on Windows") that two same-commit observations (PR #30, PR #38) showed isn't absolute under runner load. No production-code change; the daemon's behavior is unchanged.

### Changed

- **`RuleCache.match()` and `RuleCache.explain()` now compute `path.is_dir()` once per call instead of once per ancestor `.dropboxignore`.** The directory-only-pattern check inside `_rel_path_str()` was repeating the syscall `D` times for the same path when `D` ancestor rule files applied. Hoisting the call to the per-call layer is a stat-syscall reduction of `N × (D − 1)` per full sweep; on a 100k-file tree with one nested ruleset, that's ~100k fewer `is_dir()` calls per hourly sweep. No behavior change.

### Documentation

- **README:** removed a stale paragraph in the "Logging and state" subsection that claimed v0.3 would transparently read v0.2.x state from the old `~/AppData/Local/dbxignore/` path with a WARNING. The paragraph contradicted the README's top-level `## Upgrading from v0.2.x` section and predated the v0.3.0 fallback removal — it would have misled v0.2.x users into expecting their state to migrate when in fact the upgrade requires running `dropboxignore uninstall --purge` first.

## [0.3.0] — 2026-04-24

Renames the project's owned surfaces from `dropboxignore` to `dbxignore`. The `.dropboxignore` rule-file name and the `com.dropbox.ignored` marker key are Dropbox's contracts and are **not** changed.

**Upgrade path (clean break):** on an existing v0.2.x install, run `dropboxignore uninstall --purge` to clear all ignore markers and remove v0.2.x local state, then `pip install dbxignore` (or download the new binaries), then `dbxignore install`. Your `.dropboxignore` rule files carry over untouched — they are not renamed and require no edits.

**GitHub repo rename:** `kiloscheffer/dropboxignore` → `kiloscheffer/dbxignore`, performed out-of-tree. GitHub auto-redirects handle all existing URLs.

### Added

- **Published to PyPI as `dbxignore`.** First PyPI release — install with `pip install dbxignore` or `uv pip install dbxignore`.
- **PyPI publishing via Trusted Publishing (OIDC), gated on the `pypi` GitHub environment.** Release workflow (`.github/workflows/release.yml`) split into `build`, `publish-github`, and `publish-pypi` jobs. The PyPI upload step runs only after a required maintainer approval — single human checkpoint at the one irreversible step in the pipeline. No long-lived PyPI API token is stored as a repo secret; OIDC issues a short-lived credential scoped to this workflow and job.

### Changed

- **PyPI distribution name: `dropboxignore` → `dbxignore`.** **Breaking** — `pip install dropboxignore` will no longer receive updates; switch to `pip install dbxignore`.
- **Python package directory: `src/dropboxignore/` → `src/dbxignore/`.** **Breaking** — any code that imports `dropboxignore.*` must be updated to `dbxignore.*`.
- **CLI entry points: `dropboxignore` / `dropboxignored` → `dbxignore` / `dbxignored`.** **Breaking** — shell scripts, aliases, and Task Scheduler / systemd registrations using the old names must be recreated. Run `dropboxignore uninstall` before upgrading, then `dbxignore install` after.
- **Logger hierarchy root: `dropboxignore` → `dbxignore`.** Affects any external log filter or handler referencing the old name (e.g. `logging.getLogger("dropboxignore")`).
- **Environment variables: `DROPBOXIGNORE_*` → `DBXIGNORE_*`.** All public env vars (`DBXIGNORE_ROOT`, `DBXIGNORE_DEBOUNCE_RULES_MS`, `DBXIGNORE_DEBOUNCE_DIRS_MS`, `DBXIGNORE_DEBOUNCE_OTHER_MS`) are renamed. **Breaking** — old names are not read.
- **Per-user state and log directory:**
  - Windows: `%LOCALAPPDATA%\dropboxignore\` → `%LOCALAPPDATA%\dbxignore\`
  - Linux: `$XDG_STATE_HOME/dropboxignore/` → `$XDG_STATE_HOME/dbxignore/` (fallback `~/.local/state/dbxignore/`)
  - **Breaking** — existing `state.json` and `daemon.log` are not migrated automatically. `dropboxignore uninstall --purge` removes v0.2.x state as part of the recommended upgrade path.
- **systemd user unit: `dropboxignore.service` → `dbxignore.service`.** **Breaking** — the old unit name is not recognized; `dropboxignore uninstall` must be run on v0.2.x before upgrading.
- **Windows Task Scheduler task name: `dropboxignore` → `dbxignore`.** Same clean-break requirement.
- **PyInstaller binaries: `dropboxignore.exe` / `dropboxignored.exe` → `dbxignore.exe` / `dbxignored.exe`.** GitHub Release assets are renamed accordingly; the PyInstaller spec is now `pyinstaller/dbxignore.spec`.
- **GitHub Release asset names** changed to `dbxignore.exe` / `dbxignored.exe` to match the renamed entry points.
- **README** updated throughout: install examples, CLI examples, env-var reference table, state/log paths, systemd unit name, and GitHub repo links all reflect the new `dbxignore` name. An "Upgrading from v0.2.x" section with step-by-step instructions was added.
- **v0.2.0-era Linux legacy state-path fallback removed.** `state._legacy_linux_path()` and its transparent read fallback from `~/AppData/Local/dropboxignore/` are gone. The v0.2.0 CHANGELOG had scheduled this for v0.4; it is brought forward to v0.3 because the clean-break upgrade path (`dropboxignore uninstall --purge` before `dbxignore install`) eliminates any remaining callers. **Breaking** — anyone who skipped `uninstall --purge` on v0.2.x and had a legacy path will not have their old state read; run `dbxignore install` and let the daemon rebuild state from scratch.

## [0.2.1] — 2026-04-22

Maintenance release. Release-workflow hardening and project-documentation scaffolding. **No user-facing behavior changes.** Existing `.dropboxignore` rules, CLI commands, and daemon behavior are identical to v0.2.0; upgrade is a no-op for anyone running v0.2.0 today.

### Added

- **`workflow_dispatch` trigger on `.github/workflows/release.yml`.** The release workflow is now manually runnable via `gh workflow run release.yml` (or the GitHub Actions UI) for dry-run validation without cutting a tag. The `Publish GitHub Release` step is gated on `startsWith(github.ref, 'refs/tags/')`, so dispatch runs build + surface artifacts in the workflow run summary but don't create a Release object. Prevents the "workflow's first real exercise is the actual release" failure mode.
- **`GH_RELEASE_TOKEN` PAT override on the Publish step.** When the repo secret `GH_RELEASE_TOKEN` is set (fine-grained PAT with `Contents: Read and write`), releases attribute to the repo owner instead of `github-actions[bot]`. Missing secret falls back to the default `GITHUB_TOKEN` via a `||` expression — zero risk of workflow breakage if the PAT isn't configured or expires.
- **`CHANGELOG.md`** — this file. Retrospective v0.1.0 and v0.2.0 entries plus this one, following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
- **`docs/release-notes/v<X.Y.Z>.md`** convention. Hand-crafted per-release bodies override the workflow's auto-generated PR list via `gh release edit <tag> --notes-file docs/release-notes/<tag>.md` after the workflow publishes. Each release's body is versioned alongside its tag.

### Documentation

- **CLAUDE.md Git workflow:** new bullet documenting a pre-flight snippet that runs `commit-check` against every commit in `origin/main..HEAD`, matching CI's behavior. Prevents the "local HEAD-only check passes, intermediate commit trips CI, amend + force-push" round-trip hit on PR #12.
- **CLAUDE.md Release:** additional bullets for `hatch-vcs`-derived versioning (no manual `pyproject.toml` bumps), the Keep a Changelog + per-version release-notes conventions, and the pre-1.0 SemVer stance (breaking changes ride MINOR bumps with explicit `**Breaking**` callouts).
- **`docs/superpowers/plans/2026-04-22-dropboxignore-negation-polish-followups.md`:** expanded backlog — items 9–13 covering release-workflow gaps, the PyPI + rename dependency chain, and the Node.js 20 action deprecation timeline.

## [0.2.0] — 2026-04-22

First cross-platform release. Adds Linux support alongside the existing Windows port, plus rule-conflict detection, cross-platform CI with Conventional Commits enforcement, and significant UX + docs hardening.

### Added

#### Linux support

- **`user.com.dropbox.ignored` xattr backend** covering files and directories. Tested on Ubuntu 22.04 / 24.04.
- **systemd user-unit integration** — `dropboxignore install` writes `~/.config/systemd/user/dropboxignore.service`, runs `daemon-reload` + `enable --now`. `dropboxignore uninstall` is the symmetric operation.
- **XDG-compliant paths** — `state.json` and `daemon.log` land at `$XDG_STATE_HOME/dropboxignore/` (fallback `~/.local/state/dropboxignore/`).
- **Dual-sink logging** — records flow to both the rotating file and `sys.stderr` so systemd-journald captures them (`journalctl --user -u dropboxignore.service`).
- **Linux root discovery** from `~/.dropbox/info.json`.
- Graceful handling of filesystems that reject `user.*` xattrs (tmpfs without `user_xattr`, vfat, some FUSE mounts) — `OSError(errno.ENOTSUP|EOPNOTSUPP)` is treated as WARNING + continue, not a sweep abort.
- Linux xattr operations use `follow_symlinks=False`; symlinks cannot themselves carry `user.*` xattrs (kernel restriction), handled via existing `PermissionError` arm.

#### Rule-conflict detection

- `.dropboxignore` negation patterns whose target lives under a directory ignored by an earlier rule (canonical case: `build/` + `!build/keep/`) are detected at rule-load time and **dropped from the active rule set**. Dropbox's ignored-folder inheritance makes such negations inert regardless of xattr state; the tool now surfaces the mismatch rather than letting users discover the failure via sync surprise.
- Three diagnostic surfaces: daemon-log WARNING, `dropboxignore status` "rule conflicts" section, `dropboxignore explain` `[dropped]` annotation with a pointer to the masking rule.
- Design doc: `docs/superpowers/specs/2026-04-21-dropboxignore-negation-semantics.md`.

#### Configuration & escape hatches

- **`DROPBOXIGNORE_ROOT` environment variable** — pre-`info.json` override for non-stock Dropbox installs. Set to an existing absolute path → that path is the sole Dropbox root. Automatically forwarded into the generated systemd unit at `dropboxignore install` time so shell-exported values survive the service boundary.

#### CI & repo hygiene

- **Conventional Commits + Conventional Branch enforcement** via [`commit-check-action@v2.6.0`](https://github.com/commit-check/commit-check-action) on every PR. `cchk.toml` at repo root is the single source of truth shared by the local `pre-commit` hook (commit-msg + pre-push stages) and CI.
- Linux test leg — `pytest -m linux_only` runs on `ubuntu-latest` alongside the existing Windows leg.
- Linux daemon smoke test with a `"watching roots:"` log-line readiness probe (inotify's strict post-`observer.schedule()` event window).
- Real-xattr reconcile integration test and full-daemon-loop integration test.

### Changed

- **`dropboxignore uninstall --purge` now matches its name.** Previously cleared only ignore markers. Now also deletes `state.json`, `daemon.log` + rotated backups, the state directory itself (if empty — user-authored content preserved via `rmdir` not `rmtree`), and on Linux the systemd drop-in directory `~/.config/systemd/user/dropboxignore.service.d/`. Dropbox's sync behavior is unaffected — only our own bookkeeping is removed. **Breaking** for any automation that relied on `state.json` surviving `--purge`.
- **`dropboxignore explain` output format** — compact relative paths (via a formatter shared with `status`) and two-space field separators. The previous `path:line: = pattern` arrow-style form is replaced; include/negation distinction is now conveyed by the leading `!` on the raw pattern text. **Breaking** for any script that parses `explain` output.
- **`state.default_path()` on Linux migrated to XDG.** Pre-v0.2 Linux installs wrote to `~/AppData/Local/dropboxignore/` — a Windows-shaped tree inside a Linux HOME. Existing installs are read transparently from the legacy path for one release with a WARNING; the next daemon write migrates forward. Legacy fallback to be removed in v0.4.
- **`state.user_state_dir()`** is the single source of truth for the per-user state/log directory, used by both `state.default_path()` and `daemon._log_dir()`.

### Fixed

- `cli.install` catches `RuntimeError` from the install backend and exits with `2` + a clean stderr message, mirroring `cli.uninstall`'s existing behavior. Previously install-backend failures escaped as raw Python tracebacks.
- `install/linux_systemd.py` emits POSIX paths in `ExecStart` regardless of the build platform.

### Documentation

- README sections: Install (Linux), Configuration (with env-var reference table), Logs (with platform breakdown), State (with legacy-fallback note), Negations and Dropbox's ignore inheritance.
- CLAUDE.md expanded: Linux-specific gotchas, rule-cache conflict invariant, Git workflow section pointing at `cchk.toml`.
- Design specs and implementation plans for each major v0.2 arc under `docs/superpowers/`.

## [0.1.0] — 2026-04-21

Initial release. Windows-only.

### Added

- **Hierarchical `.dropboxignore` files** — drop a `.dropboxignore` at any level of a Dropbox tree; rules apply recursively from there. Supports full gitignore syntax via `pathspec`, including negations and anchored paths.
- **NTFS Alternate Data Stream backend** — writes the `com.dropbox.ignored` ADS that Dropbox's Windows client reads to skip sync.
- **Dual-trigger daemon** — `watchdog` observer for real-time filesystem events + hourly safety-net sweep + initial full sweep on startup (catches offline drift).
- **Event debouncer** — coalesces bursts of related events; per-event-kind timeouts (`RULES` 100 ms, `DIR_CREATE` 0 ms, `OTHER` 500 ms), configurable via `DROPBOXIGNORE_DEBOUNCE_{RULES,DIRS,OTHER}_MS`.
- **Case-insensitive rule matching** — NTFS-appropriate; `node_modules/` matches a directory named `Node_Modules`.
- **Automatic Dropbox root discovery** from `%APPDATA%\Dropbox\info.json` (Personal + Business accounts).
- **Task Scheduler integration** — `dropboxignore install` registers a user-logon trigger via `schtasks` XML; `dropboxignore uninstall` removes it.
- **CLI commands** — `apply` (one-shot reconcile), `status` (daemon pid, last sweep, last error), `list` (print all marked paths), `explain PATH` (show matching rules), `daemon` (run in foreground), `install` / `uninstall`.
- **`uninstall --purge` flag** — clears every ignore marker under each discovered root. (v0.2 broadens this to also remove local state.)
- **Rotating log file** at `%LOCALAPPDATA%\dropboxignore\daemon.log` (5 MB × 4 backups).
- **Persisted state** at `%LOCALAPPDATA%\dropboxignore\state.json` (daemon pid, sweep stats, watched roots).
- **`.dropboxignore` protection** — the rule file itself is never marked ignored; any stray marker on one is cleared with a WARNING.
- **PyInstaller-built standalone binaries** — `dropboxignore.exe` + `dropboxignored.exe`, published via GitHub Releases.
- **Windows test leg** with `pytest -m windows_only` NTFS-ADS integration tests.

[0.3.2]: https://github.com/kiloscheffer/dbxignore/releases/tag/v0.3.2
[0.3.1]: https://github.com/kiloscheffer/dbxignore/releases/tag/v0.3.1
[0.3.0]: https://github.com/kiloscheffer/dbxignore/releases/tag/v0.3.0
[0.2.1]: https://github.com/kiloscheffer/dbxignore/releases/tag/v0.2.1
[0.2.0]: https://github.com/kiloscheffer/dbxignore/releases/tag/v0.2.0
[0.1.0]: https://github.com/kiloscheffer/dbxignore/pull/1

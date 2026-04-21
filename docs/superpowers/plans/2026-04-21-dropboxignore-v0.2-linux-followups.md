# dropboxignore v0.2 — post-execution follow-ups

Items surfaced during execution of [`2026-04-21-dropboxignore-v0.2-linux.md`](./2026-04-21-dropboxignore-v0.2-linux.md) that were intentionally deferred out of scope. Carry into v0.3 planning or address as standalone PRs.

## 1. Linux state file path is Windows-styled — **RESOLVED**

`state.default_path()` and `daemon._log_dir()` previously read `LOCALAPPDATA` on every platform, producing `~/AppData/Local/dropboxignore/...` on Linux — a Windows-shaped tree inside a Linux HOME. Functional but non-idiomatic.

**Fix:** platform branching now lives in a shared `state.user_state_dir()` helper that both `default_path()` and `daemon._log_dir()` call. Linux reads `$XDG_STATE_HOME` with a fallback to `~/.local/state`, under `dropboxignore/`; Windows preserves `LOCALAPPDATA` with the existing `~/AppData/Local` fallback. `state.read()` (no-arg form) transparently falls back to the legacy Linux path for one release, logs a WARNING naming both paths, and the next `write()` persists to the XDG path. Explicit `state.read(path)` is unaffected. Five tests in `tests/test_state.py` pin the contract (Windows + Linux happy paths, XDG_STATE_HOME fallback, legacy migration + warning, XDG-wins-when-both-exist, explicit-path-does-not-trigger-fallback). `tests/test_daemon_logging.py::log_dir` fixture now branches on platform to exercise the same code path for the log directory.

Touched: `src/dropboxignore/state.py`, `src/dropboxignore/daemon.py`, `tests/test_state.py`, `tests/test_daemon_logging.py`, `CLAUDE.md`.

## 2. `cli.install` has no error handling — **RESOLVED**

Both the v0.1 Windows `install_task` and the new Linux `install_unit` can fail with `RuntimeError` (e.g. missing systemd user session, `schtasks` permission error). `cli.uninstall` catches `RuntimeError` and exits with a clean message + code 2. `cli.install` catches nothing — any failure escapes as a raw Python traceback.

**Fix:** `cli.install` now mirrors `cli.uninstall`'s try/except around the backend call, echoing `Failed to install daemon service: {exc}` to stderr and exiting with code 2. Test `test_cli_install_reports_backend_failure` in `tests/test_install.py` pins the contract (message, exit code, and that "Installed ..." is *not* printed on failure).

## 3. No Linux daemon smoke test exercises the full event loop — **RESOLVED**

`test_daemon_smoke.py` was `@pytest.mark.windows_only` with no Linux counterpart — `fake_markers`-based dispatch tests covered the event logic but the integrated "real xattr writes reach the filesystem through the daemon's actual watchdog loop" path was not exercised on Linux.

**Fix:** new `tests/test_daemon_smoke_linux.py` — `pytest.mark.linux_only` + `_xattr_supported` autouse skip, monkey-patches `daemon.roots_module.discover` to point at `tmp_path`, routes state/log off the real per-user dir via `XDG_STATE_HOME`, waits for the daemon's `watching roots:` log line (readiness signal — inotify only sees events fired after `observer.schedule()`), then asserts (phase 1) adding a `build/` rule + creating `build/` marks the directory, and (phase 2) emptying the rule clears it. Runs in ~0.25s on inotify.

**Deviation from the Windows smoke:** the Windows version's phase 2 adds `!build/keep/` as a negation; the Linux version drops the rule entirely. Surfaced a product bug in the prune+negation interaction — see new item 10 — which happens to be masked by Windows event timing. A Linux smoke that exercised negation flaked reliably; the simpler rule-add/remove flow is sufficient to prove the event pipeline and avoids the edge case that's now tracked separately.

Touched: `tests/test_daemon_smoke_linux.py` (new).

## 4. Manual Ubuntu VPS smoke check — **RESOLVED**

Ran 2026-04-21 on Kilo's Ubuntu VPS (systemd 255, ext4 `$HOME`). No stock Dropbox install on this host, so the smoke test used the `DROPBOXIGNORE_ROOT` escape hatch (item 5) against `~/dbx-smoke/` rather than a real Dropbox root. `attr` package wasn't installed; substituted `python3 -c 'import os; os.getxattr(...)'` for `getfattr`. `sequence`:

1. `uv tool install --reinstall .` from the current checkout — installed `dropboxignore` + `dropboxignored` at `~/.local/bin/`.
2. `dropboxignore install` — wrote `~/.config/systemd/user/dropboxignore.service` (`ExecStart=/home/kilo/.local/bin/dropboxignored`, `Restart=on-failure`, `RestartSec=60s`), enabled-and-started cleanly.
3. Initial start: daemon exited clean (`status=0/SUCCESS`) with `WARNING: Dropbox info.json not found at /home/kilo/.dropbox/info.json` + `ERROR: no Dropbox roots discovered; exiting` — **item 7 validated live**: both records surfaced via `systemctl status` and `journalctl --user -u dropboxignore.service`, which was the before/after contract for the dual-sink change.
4. Dropped in `~/.config/systemd/user/dropboxignore.service.d/scratch-root.conf` with `Environment=DROPBOXIGNORE_ROOT=/home/kilo/dbx-smoke`, `daemon-reload` + `restart` → `active (running)`. Initial sweep `marked=0 cleared=0 errors=0 duration=0.00s` and `watching roots: ['/home/kilo/dbx-smoke']` both logged to journald.
5. Created `normal.txt`, `scratch.tmp`, `build/artifact.o`, `.dropboxignore` with rules `*.tmp` + `build/`. After ~1s: `scratch.tmp` + `build/` xattr-marked (`b'1'`), `normal.txt` + `build/artifact.o` + `.dropboxignore` unmarked — matches the reconcile contract (directory-rule match marks the directory only; descendants are covered by Dropbox's parent-exclusion semantics, no redundant per-file xattrs).
6. Touched `late.tmp` post-startup → marked within ~1s via the watchdog+debouncer+reconcile event path. `dropboxignore list` enumerated all three markers; `dropboxignore explain /home/kilo/dbx-smoke/scratch.tmp` correctly reported `.dropboxignore:1: = *.tmp`.
7. State file landed at `~/.local/state/dropboxignore/state.json` — **item 1 validated live**: XDG path, not the pre-v0.2 `~/AppData/Local/…` legacy layout.
8. `dropboxignore uninstall --purge` printed `Cleared 3 ignore markers` (matched the 3 actually-marked paths), removed the unit file, stopped the service. Post-run xattrs: all cleared on all 6 test paths.
9. Teardown checks:
   - Unit file gone (`systemctl status` → "Unit ... could not be found") ✓
   - Drop-in directory `~/.config/systemd/user/dropboxignore.service.d/` **survives** `uninstall --purge` — only the bare unit file is removed. My test cleaned it manually; a real user upgrading would need the same step. Noted in item 9.
   - `~/.local/state/dropboxignore/state.json` + `daemon.log` **still present** — **item 8 symptom confirmed in practice**; `--purge` clears markers only, not local state.

### Surfaced adjacent symptoms (new backlog)

- **Item 8 confirmed empirically** — `state.json` and `daemon.log` remain post-purge. Decision still needed (broaden `--purge` vs. add `--purge-state`).
- **New item 9** — systemd unit doesn't propagate `DROPBOXIGNORE_ROOT` from the shell env. Setting it in `.bashrc` has no effect; users on non-stock Dropbox installs must manually drop in `Environment=…` override as this smoke did. Worth either teaching `dropboxignore install` to inject the current shell's `DROPBOXIGNORE_ROOT` into the generated unit's `Environment=`, or documenting the drop-in pattern in the README Install-on-Linux section.
- **Also noted** — `uninstall` leaves `~/.config/systemd/user/dropboxignore.service.d/` drop-in directory if the user ever created one. Not a bug per se (we're not responsible for cleaning directories we didn't create), but the two together mean "uninstall --purge" isn't a full trace-removal. Bundle with item 8's decision if scope broadens.

## 5. `roots.discover()` JSON schema drift risk — **RESOLVED**

`roots.discover()` still reads Dropbox's `info.json` directly. Dropbox has historically reshaped this file without warning (key rename, encoding quirks — hence `test_discover_non_utf8_bytes`). If Dropbox changes the schema again in v3, every dropboxignore user on the affected release gets an empty `[]` from `discover()` and silent failure.

**Fix:** `roots.discover()` now checks `DROPBOXIGNORE_ROOT` before touching `info.json`. Set to an existing absolute path → `[Path(env)]`; nonexistent path → WARNING + `[]` (so the CLI's "No Dropbox roots found" surfaces rather than a silent no-op); empty string → treated as unset. Single-root only (spec); the override sits above `_info_json_path()` so it also works on platforms that return `None` there. Documented in README "Configuration" and CLAUDE.md "Gotchas". Four tests in `tests/test_roots.py` pin the contract (happy path, wins-over-info.json, empty-string fallback, missing-path WARNING).

## 6. Retire the legacy Linux state-path fallback in v0.4

Item 1's fix landed a Linux-only read-time fallback in `state.read()` that looks at the pre-XDG `~/AppData/Local/dropboxignore/state.json` when the XDG path is empty, logs a WARNING, and lets the next `write()` persist forward to the XDG path. The code and docs describe this as a **one-release** bridge, but there is no enforcement — without a tracked follow-up, the fallback will quietly become permanent and accumulate test-maintenance burden.

**Proposed fix:** in the v0.4 branch, delete `state._legacy_linux_path`, delete the `sys.platform.startswith("linux")` block in `state.read()`, and delete the two migration tests in `tests/test_state.py` (`test_read_falls_back_to_legacy_linux_path_with_warning`, `test_read_prefers_xdg_when_both_exist`). The `test_read_explicit_path_does_not_trigger_legacy_fallback` test can go too since its whole premise is the bridge. Release notes for v0.4 should call out that users who are still running v0.1-era state files will get a fresh (zero-counter) state on first run.

Touches: `src/dropboxignore/state.py`, `tests/test_state.py`, README "State" section.

## 7. Linux daemon logs do not reach the systemd journal — **RESOLVED**

`_configured_logging()` previously installed a `RotatingFileHandler` with `propagate=False`, so every `logger.*` call bypassed stderr and `journalctl --user -u dropboxignore.service` was near-empty — surfacing only uncaught exceptions and subprocess stderr.

**Fix (option A, dual sink):** on Linux, `_configured_logging()` now installs a second handler — `StreamHandler(sys.stderr)` sharing the rotating file's formatter — alongside the existing `RotatingFileHandler`. systemd-journald captures the stderr sink when the daemon runs as a user unit. Windows behavior is unchanged (rotating file only; Task Scheduler doesn't have a journald equivalent worth mirroring). Two tests in `tests/test_daemon_logging.py` pin the contract: `test_configured_logging_installs_rotating_handler` branches on `sys.platform` (expects 1 handler on Windows, 2 on Linux with `StreamHandler.stream is sys.stderr`); `test_configured_logging_does_not_close_stderr_on_exit` guards the cleanup loop — `StreamHandler.close()` must not close `sys.stderr` itself. README "Logs" and the CLAUDE.md gotcha describe both sinks.

Touched: `src/dropboxignore/daemon.py`, `tests/test_daemon_logging.py`, `README.md`, `CLAUDE.md`.

## 8. `uninstall --purge` leaves `state.json` and `daemon.log` behind

`cli.uninstall --purge` clears every ignore marker it can find under the discovered roots but does not touch `state.default_path()` or `_log_dir()/daemon.log`. After `dropboxignore uninstall --purge`, two files linger under `%LOCALAPPDATA%\dropboxignore\` (Windows) or `~/.local/state/dropboxignore/` (Linux). Minor, but it violates the principle-of-least-surprise of `--purge` — the flag's docstring says "clear every ignore marker" which is narrower than user expectation.

**Proposed fix:** either (a) broaden `--purge` to also delete `state.default_path()` and the rotating log files, and rename the docstring accordingly ("clear every ignore marker *and* local dropboxignore state"); or (b) add a second flag `--purge-state` for the state/log sweep, keeping `--purge` marker-only. Option (a) is what users seem to expect ("uninstall + purge = no trace left"); option (b) is safer because it prevents someone from losing sweep stats with a single keystroke.

Touches: `src/dropboxignore/cli.py` (`uninstall`), `tests/test_install.py` (new assertions around post-purge filesystem state), README "Install" / "Uninstall" section.

## 9. Systemd user unit doesn't propagate `DROPBOXIGNORE_ROOT` — **RESOLVED**

`install/linux_systemd.py` previously wrote a unit file with no `Environment=` directives, so a user who exported `DROPBOXIGNORE_ROOT=…` in their shell and ran `dropboxignore install` got a systemd-launched daemon that didn't see the variable — `roots.discover()` fell through to `~/.dropbox/info.json` discovery and logged "no Dropbox roots discovered; exiting".

**Fix:** `build_unit_content()` now accepts an optional `environment: dict[str, str]` and emits one `Environment="KEY=VALUE"` line per entry, placed in `[Service]` before `ExecStart=`. `install_unit()` reads `DROPBOXIGNORE_ROOT` from `os.environ` at install time (via a narrow `_FORWARDED_ENV_VARS` allow-list — other `DROPBOXIGNORE_*` tuning vars deliberately don't propagate) and forwards it into the unit. Empty-string and unset both skip the line; set-but-nonexistent paths still propagate (`roots.discover()` already handles the nonexistent-path WARNING). The outer-quoted form handles paths with spaces; `\\` and `"` in values are escaped by `_escape_systemd_env_value`. README Install-on-Linux section now calls out the "export before install" workflow. Eight tests in `tests/test_linux_systemd.py` pin the contract.

Touched: `src/dropboxignore/install/linux_systemd.py`, `tests/test_linux_systemd.py`, `README.md`.

## 10. Prune + negation leaves stale markers on children of ignored directories

`reconcile_subtree()` prunes descent at any directory whose `_reconcile_path` returns True (ignored) — a correctness-preserving optimization under normal gitignore semantics, since once a parent is ignored Dropbox won't sync its contents. But when a user adds a negation rule like `!build/keep/` after `build/keep/` has already been marked (e.g. because DIR_CREATE dispatched with the old rule cache before RULES reloaded), the subsequent full-tree reconcile marks `build/`, prunes descent into it, and never visits `build/keep/` to evaluate the negation. The stale child marker persists indefinitely — neither event-driven reconciles nor the hourly sweep recover it, because both use the same `reconcile_subtree` code path.

Reproducer — flaky on Linux, masked on Windows by ReadDirectoryChangesW timing:

```
1. Write .dropboxignore = `build/\n`
2. mkdir build/  → marked
3. Rewrite .dropboxignore = `build/\n!build/keep/\n`
4. mkdir build/keep/
   — DIR_CREATE fires first (0ms debounce) with OLD cache (`build/`) → marks build/keep/
   — RULES fires 100ms later, reloads with new rules, reconciles root
   — reconcile at `build/` → still matches → prune, no descent
   — `build/keep/` never re-evaluated, keeps stale marker
```

Surfaced by item 3's Linux daemon smoke: the Windows smoke test with this exact scenario passes reliably because ReadDirectoryChangesW timing makes RULES dispatch before DIR_CREATE, so the child is never marked in the first place. inotify fires DIR_CREATE near-instantly and wins the race. The Linux smoke was narrowed to a rule-add/remove scenario that doesn't hit this edge case.

**Proposed fix:** options, none trivial:
- Always descend on rule-change reconciles (skip the prune optimization when the cache reloaded since the last full sweep). Adds per-sweep cost on big trees but is localized.
- Detect negation patterns whose prefix is ignored, schedule a targeted second reconcile pass on those subtrees. More complex but doesn't regress perf in the common case.
- Give up the prune optimization entirely. Adds maybe hundreds of ms to a 50k-file sweep — possibly acceptable given sweeps are hourly.

Touches: `src/dropboxignore/reconcile.py`, `tests/test_reconcile_basic.py` (new test reproducing the stale-marker scenario).

---

## How these surfaced

Items 1, 2, 5 surfaced in per-task code-quality reviews but were out of plan scope.
Items 2, 3 also flagged by the end-of-branch end-to-end reviewer (see commit `957fd32` which addressed other findings from the same review).
Item 4 is a plan-specified manual check that required environmental access; it was run 2026-04-21 and confirmed two adjacent symptoms as items 8 (empirically) and 9 (new).
Items 6, 7, 8 surfaced during the item-1 implementation pass and its README-accuracy audit.
Item 9 surfaced during the item-4 VPS smoke — the shell-env vs. unit-env gap made the escape hatch from item 5 unusable under systemd without a drop-in override.
Item 10 surfaced during item 3's Linux daemon smoke — a negation-based assertion flaked, investigation revealed the prune optimization silently breaks negation semantics for previously-marked descendants.

## Status

Remaining open after v0.2 follow-ups:
- Item 6 — Retire legacy Linux state-path fallback (v0.4 branch).
- Item 8 — `uninstall --purge` state/log cleanup (design decision: broaden `--purge` vs add `--purge-state`). Empirically confirmed by item 4's VPS run.
- Item 10 — Prune + negation leaves stale markers on children of ignored directories. Real product bug; cross-platform but surfaced on Linux due to inotify timing.

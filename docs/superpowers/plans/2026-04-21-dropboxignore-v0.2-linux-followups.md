# dropboxignore v0.2 — post-execution follow-ups

Items surfaced during execution of [`2026-04-21-dropboxignore-v0.2-linux.md`](./2026-04-21-dropboxignore-v0.2-linux.md) that were intentionally deferred out of scope. Carry into v0.3 planning or address as standalone PRs.

## 1. Linux state file path is Windows-styled — **RESOLVED**

`state.default_path()` and `daemon._log_dir()` previously read `LOCALAPPDATA` on every platform, producing `~/AppData/Local/dropboxignore/...` on Linux — a Windows-shaped tree inside a Linux HOME. Functional but non-idiomatic.

**Fix:** platform branching now lives in a shared `state.user_state_dir()` helper that both `default_path()` and `daemon._log_dir()` call. Linux reads `$XDG_STATE_HOME` with a fallback to `~/.local/state`, under `dropboxignore/`; Windows preserves `LOCALAPPDATA` with the existing `~/AppData/Local` fallback. `state.read()` (no-arg form) transparently falls back to the legacy Linux path for one release, logs a WARNING naming both paths, and the next `write()` persists to the XDG path. Explicit `state.read(path)` is unaffected. Five tests in `tests/test_state.py` pin the contract (Windows + Linux happy paths, XDG_STATE_HOME fallback, legacy migration + warning, XDG-wins-when-both-exist, explicit-path-does-not-trigger-fallback). `tests/test_daemon_logging.py::log_dir` fixture now branches on platform to exercise the same code path for the log directory.

Touched: `src/dropboxignore/state.py`, `src/dropboxignore/daemon.py`, `tests/test_state.py`, `tests/test_daemon_logging.py`, `CLAUDE.md`.

## 2. `cli.install` has no error handling — **RESOLVED**

Both the v0.1 Windows `install_task` and the new Linux `install_unit` can fail with `RuntimeError` (e.g. missing systemd user session, `schtasks` permission error). `cli.uninstall` catches `RuntimeError` and exits with a clean message + code 2. `cli.install` catches nothing — any failure escapes as a raw Python traceback.

**Fix:** `cli.install` now mirrors `cli.uninstall`'s try/except around the backend call, echoing `Failed to install daemon service: {exc}` to stderr and exiting with code 2. Test `test_cli_install_reports_backend_failure` in `tests/test_install.py` pins the contract (message, exit code, and that "Installed ..." is *not* printed on failure).

## 3. No Linux daemon smoke test exercises the full event loop

`test_daemon_smoke.py` is `@pytest.mark.windows_only` and exercises the Windows-specific real-ADS path through the daemon's watchdog event dispatch. There is no Linux-gated counterpart. Dispatch logic is covered via `fake_markers` in `test_daemon_dispatch.py`, `test_daemon_sweep.py`, etc., but the integrated "real xattr writes reach the filesystem through the daemon's actual watchdog loop" path is not tested.

**Proposed fix:** add `tests/test_daemon_smoke_linux.py` (`linux_only`, `_xattr_supported` autouse skip) that spins up the daemon against a temporary Dropbox-root-shaped tree, writes a `.dropboxignore`, waits for debounce, and asserts the expected xattr landed on matching files. Mirror the shape of `test_daemon_smoke.py` on Windows.

Touches: `tests/test_daemon_smoke_linux.py` (new).

## 4. Manual Ubuntu VPS smoke check still outstanding

Task 6 of the v0.2 plan ended with a manual post-merge verification:

```bash
uv tool install .
dropboxignore install
systemctl --user status dropboxignore.service    # expect active (running)
# create a .dropboxignore with a rule and confirm xattr lands
getfattr -n user.com.dropbox.ignored <matched-path>
dropboxignore uninstall --purge                  # markers cleared
```

Needs to be run on Kilo's Ubuntu VPS (or equivalent) with a real Dropbox install. Document the result in the v0.2 release notes or design-doc resolution section.

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

---

## How these surfaced

Items 1, 2, 5 surfaced in per-task code-quality reviews but were out of plan scope.
Items 2, 3 also flagged by the end-of-branch end-to-end reviewer (see commit `957fd32` which addressed other findings from the same review).
Item 4 is a plan-specified manual check that requires environmental access.
Items 6, 7, 8 surfaced during the item-1 implementation pass and its README-accuracy audit.

## Status

Remaining open after v0.2 follow-ups:
- Item 3 — Linux daemon smoke test (tracked for v0.3).
- Item 4 — Manual Ubuntu VPS smoke verification (requires environmental access). Now more diagnostically useful: item 7's dual sink means `journalctl --user -u dropboxignore.service` will surface what's happening in real time.
- Item 6 — Retire legacy Linux state-path fallback (v0.4 branch).
- Item 8 — `uninstall --purge` state/log cleanup (design decision: broaden `--purge` vs add `--purge-state`).

# dropboxignore v0.2 — post-execution follow-ups

Items surfaced during execution of [`2026-04-21-dropboxignore-v0.2-linux.md`](./2026-04-21-dropboxignore-v0.2-linux.md) that were intentionally deferred out of scope. Carry into v0.3 planning or address as standalone PRs.

## 1. Linux state file path is Windows-styled

`state.default_path()` reads `LOCALAPPDATA` on every platform. On Linux where `LOCALAPPDATA` is unset, the fallback resolves to `~/AppData/Local/dropboxignore/state.json` — a Windows-shaped directory tree sitting inside a Linux HOME. Functional but non-idiomatic; users expect XDG compliance.

**Proposed fix:** branch on `sys.platform` the same way `roots._info_json_path()` does. Use `~/.local/state/dropboxignore/state.json` (XDG Base Directory spec) on Linux, preserve `LOCALAPPDATA` on Windows. Migration: first read from the new path; fall back to the legacy Windows-styled path on Linux for one release if present, log a warning, and persist going forward at the XDG path.

Touches: `src/dropboxignore/state.py`, potentially `daemon.py` log-path derivation.

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

---

## How these surfaced

Items 1, 2, 5 surfaced in per-task code-quality reviews but were out of plan scope.
Items 2, 3 also flagged by the end-of-branch end-to-end reviewer (see commit `957fd32` which addressed other findings from the same review).
Item 4 is a plan-specified manual check that requires environmental access.

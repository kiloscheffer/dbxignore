# Changelog

All notable changes to dbxignore are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0a3] — 2026-05-01

Alpha pre-release for beta-tester validation on real macOS hardware. Supersedes `0.4.0a2`, whose macOS arm64 binary failed at first launch with `ModuleNotFoundError: No module named '_cffi_backend'` — the `pyinstaller-hooks-contrib` `cffi` hook didn't bundle the `_cffi_backend` C extension on that build (top-level sibling of the `cffi` package, slips past PyInstaller's static AST trace). PR #71 lists `_cffi_backend` explicitly in the macOS spec's `hiddenimports` and adds `<binary> --help` smoke tests on both `build` and `build-macos` legs of the release workflow before the artifact upload, so any future analyzer miss in any backend's transitive deps fails CI rather than ships. Linux + Windows binaries unaffected (Linux uses stdlib `os.{get,set}xattr`; Windows uses raw NTFS ADS opens — neither imports `cffi`). Also supersedes the earlier-yanked `0.4.0a1` (the PyPI-publish-on-alpha misfire fixed in PR #62). Promotes to `[0.4.0]` once the beta tester signs off on the 10-step checklist from the v0.4 spec § "Beta-test workflow".

Brief summary of what's landed across PRs #53 – #71:

### Added — macOS support

- **macOS xattr backend.** `src/dbxignore/_backends/macos_xattr.py` talks to the `com.dropbox.ignored` extended attribute via the `xattr` PyPI package with `symlink=True` (operates on the link itself, not its target). Three-function API matches the Linux and Windows backends. Symlinks are marked silently and successfully on macOS — a small but meaningful behavioral improvement over Linux, where the kernel refuses `user.*` xattrs on symlinks with `EPERM`. The macOS-vs-Linux divergence is documented in CLAUDE.md and the README's macOS section.
- **launchd User Agent installer.** `src/dbxignore/install/macos_launchd.py` writes `~/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist` and bootstraps it into the user's GUI session via modern `launchctl bootstrap gui/<uid>` / `bootout gui/<uid>/<label>` (not legacy `load -w` / `unload -w`). `KeepAlive: {SuccessfulExit: False, Crashed: True}` matches systemd's `Restart=on-failure` semantics. Install requires that the user has logged into the GUI at least once since the last reboot — SSH-on-fresh-boot installs fail with `Bootstrap failed: 5: Input/output error` and need a GUI login + retry.
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

### Documentation

- **README** gains an "Install (macOS)" section between Linux and `.exe` install paths. Updated platform-support table now includes the macOS row. Logs and State sections list macOS paths.
- **README + CLAUDE.md: Windows OneDrive hardlink workaround.** `uv tool install` can fail with `ERROR_CLOUD_FILE_INCOMPATIBLE_HARDLINKS` (os error 396) when `%AppData%` is OneDrive-synced via Files On-Demand — the Cloud Files API refuses hardlinks on placeholder files. The documented workaround is `uv tool install --link-mode=copy git+...` or session-wide `$env:UV_LINK_MODE = "copy"`. Surfaced during v0.4 alpha install on a OneDrive-backed AppData.
- **CLAUDE.md** gains four new gotchas covering: the `xattr` package's `symlink=True` API surface (NOT `options=XATTR_NOFOLLOW` despite what Apple's libc docs suggest); macOS-only test pattern; the symlink-marking divergence between Linux/macOS/Windows; modern launchctl bootstrap with the GUI-domain prerequisite; `_common.detect_invocation` shared module + the import-site monkeypatching gotcha.
- **`docs/release-notes/v0.4.0.md`** — hand-crafted GitHub Release body for promotion via `gh release edit v0.4.0 --notes-file ...`.

### Caveats (will appear in 0.4.0 release notes)

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

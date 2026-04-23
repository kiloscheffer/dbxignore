# Changelog

All notable changes to dropboxignore are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.2.1]: https://github.com/kiloscheffer/dropboxignore/releases/tag/v0.2.1
[0.2.0]: https://github.com/kiloscheffer/dropboxignore/releases/tag/v0.2.0
[0.1.0]: https://github.com/kiloscheffer/dropboxignore/pull/1

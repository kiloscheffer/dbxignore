# Active gotchas

Operational notes that are still relevant, but too situational for the always-loaded
`AGENTS.md`. Read the matching section before touching that area.

## Rules, pathspec, and CLI

- Pathspec 1.0.4 code should subclass `GitIgnoreSpecPattern`, not deprecated `GitWildMatchPattern`.
- Directory-only rules such as `node_modules/` require a trailing slash on the tested path string.
- Gitignore comments only start at column 0. A line like `"   #literal"` is an active pattern, so `rules._build_entries` checks `raw.startswith("#")`, not `raw.strip().startswith("#")`.
- Pathspec accepts many malformed-looking patterns without raising. To test invalid-pattern handling, monkeypatch `rules._build_spec` to raise `(ValueError, TypeError, re.error)`.
- `cli.py` intentionally imports `rules` as a module (`from dbxignore import rules`) so tests can monkeypatch `rules._build_spec` at the call site.
- rich-click markdown strips unescaped backticks from docstrings before help text rendering. Escape literal backticks in command docstrings.
- click 8.3+ `Result` exposes `.stdout` and `.stderr`; do not use the old `CliRunner(mix_stderr=False)` pattern.

## Markers and paths

- `_backends/windows_ads` opens `\\?\path:com.dropbox.ignored` directly; the `\\?\` prefix is mandatory for long paths.
- NTFS is case-insensitive; `_CaseInsensitiveGitIgnorePattern` prepends `(?i)`.
- `.dropboxignore` files are never ignored. If reconcile finds one marked, it clears the marker and logs `WARNING`.
- `rules.match/explain` and `markers.{is,set,clear}_ignored` require absolute paths and raise `ValueError` on relative ones. Resolve at the CLI/daemon boundary.
- Linux xattrs vanish through common operations such as `cp` without `-a`, cross-filesystem `mv`, many archivers, and vim save-via-rename. Recovery is the watchdog stream plus hourly sweep.
- Symlinks are leaves for user/rule directory checks. Linux refuses `user.*` xattrs on symlinks, macOS marks the link via NOFOLLOW, and Windows ADS attaches to the reparse point. Tests must branch on `sys.platform`.
- When walking a symlink root, short-circuit before `os.walk`: `followlinks=False` does not stop `os.walk` from following the root itself.

## Daemon and state

- `daemon._resolve_under_roots(raw_path, roots)` checks containment before `Path.resolve()` so outside-root events pay no resolve cost. Preserve that order.
- `watchdog.observers.Observer` is a platform-conditional factory, not a class. For annotations use `BaseObserver` from `watchdog.observers.api` under `TYPE_CHECKING`.
- `daemon._configured_logging()` snapshots and restores the `dbxignore` logger. Linux installs file + stderr handlers; Windows only installs the file handler.
- `state.write()` writes `state.json.tmp` then `os.replace`s into place. Do not replace it with direct `path.write_text()`.
- Per-user state-dir paths are platform-divergent. The single source of truth is `state.user_state_dir()` (used by state file, daemon lock, daemon log) and `state.user_log_dir()` (daemon log path only). Resolution: Windows → `%LOCALAPPDATA%\dbxignore\` (falls back to `~/AppData/Local/dbxignore/`); Linux → `$XDG_STATE_HOME/dbxignore/` (falls back to `~/.local/state/dbxignore/`); macOS → `~/Library/Application Support/dbxignore/` for state, with daemon logs split off to `~/Library/Logs/dbxignore/`. Call the helpers rather than reconstructing the path.
- `state.json` means "daemon started", not "initial sweep completed". `last_sweep is None` is the `state=starting` window.
- `_initial_sweep_worker` must keep helper calls, env reads, and validators inside its broad `try/except Exception:` block so failures set `stop_event` instead of stranding the daemon in `state=starting`.
- Validate user-derived wait durations with `math.isfinite(value)` and `value <= threading.TIMEOUT_MAX` before `threading.Event.wait(timeout)`.
- `msvcrt.locking` locks bytes from the current cursor position. Singleton-lock code must `fh.seek(0)` before locking so all Windows contenders compete for byte 0.
- `_logging.timed_debug(...)` is the canonical DEBUG timing context manager for hot paths. It avoids `perf_counter()` overhead when DEBUG logging is disabled.
- Daemon-thread tests using gated marker fakes can deadlock on macOS because FSEvents may enqueue synthetic events for pre-existing files. Stub `daemon.Observer` to a no-op in tests that block before opening the gate.

## Installers and platform backends

- `roots.discover()` honors `DBXIGNORE_ROOT` before `info.json`; empty means unset, nonexistent warns and returns `[]`, and the override is single-root only.
- Windows root discovery checks `%APPDATA%\Dropbox\info.json` and `%LOCALAPPDATA%\Dropbox\info.json` in that order.
- The macOS xattr backend's module docstring documents File Provider detection, dual-attribute fallback, `detection_summary()`, `pluginkit` output, and test seams.
- The `xattr` PyPI package uses `symlink=True` to operate on the link itself. Match the package signature, not Apple's lower-level C API naming.
- macOS launchd install uses modern `launchctl bootstrap gui/<uid>` and `bootout gui/<uid>/<label>`. SSH-on-fresh-boot can fail until the user has logged into the GUI once.
- `install/_common.detect_invocation()` is lazy-imported into Linux/macOS installer modules. Monkeypatch at the import site, not the source module.
- Linux systemd forwards path-resolution env vars (`DBXIGNORE_ROOT`, `XDG_STATE_HOME`) into the user unit so daemon and shell tools agree. Tuning vars belong in drop-ins.
- systemd expands `%` specifiers inside both `ExecStart=` and `Environment=` values. Double `%` before applying C-style quoted-string escaping.
- Tests for subprocess-using code should monkeypatch `subprocess.run`, not the calling helper. See `_fake_pluginkit` in `tests/test_macos_xattr_unit.py`.

## Tests and local environments

- Test helpers (`FakeMarkers`, `fake_markers`, `write_file`) live in `tests/conftest.py`.
- Platform-only test modules set module-level `pytestmark` and skip during collection on other platforms.
- Daemon/sweep tests that write state should monkeypatch `state.default_path` to a temp `state.json`.
- Root-discovery seams differ by layer: CLI tests patch `cli._discover_roots`; daemon tests patch `daemon.roots_module.discover`; root unit tests stage Dropbox `info.json` fixtures.
- Tests asserting status/clear daemon-alive behavior should monkeypatch `state.is_daemon_alive`; pytest process names vary by platform and invocation.
- Prefer `uv run python -m pytest`. Plain `uv run pytest` can miss the editable install or hit uv trampoline canonicalization failures on stale envs.
- `pytest-timeout` defaults to 10s. Tests with longer poll loops or shutdown paths need `@pytest.mark.timeout(N)`.
- `tests/test_daemon_inotify_enospc.py` startup tests fail locally when a real daemon holds the per-user `daemon.lock`; CI passes because no daemon is installed.
- Case-insensitive filesystem tests should probe behavior instead of assuming `Path.resolve()` equality. `Path.rglob('.dropboxignore')` and `os.walk` differ on case-insensitive filesystems.
- Manual-test Phase 5 daemon assertions (5b/5c/5d in all three platform scripts) are timing-sensitive — the budgets (~2-6s for watchdog-event-to-marker-set) can be exceeded on Dropbox-active hosts, antivirus-scanning environments, or otherwise loaded systems. A single failing run is inconclusive; re-run before treating it as a real regression. Observed empirically during the v0.5.0 manual-test validation pass: identical code, two consecutive runs, three Phase 5 failures vs zero.

## Tooling, packaging, and commits

- Packaged non-.py data under `src/dbxignore/**` ships via hatchling's package include. Read it at runtime through `importlib.resources`.
- Cross-platform `# type: ignore` codes differ depending on installed optional deps. Use combined forms such as `[import-not-found, import-untyped, unused-ignore]` or `[attr-defined, unused-ignore]`.
- Keep critical test-runtime deps mirrored in both `[project.optional-dependencies].dev` and `[dependency-groups].dev`.
- Windows cloud-synced working trees can make uv editable installs fail with file locks. `UV_LINK_MODE=copy` is the workaround.
- Reinstalling an already-installed uv tool while the Windows daemon is running can fail with access denied because mapped `.pyd` files are held open. Run `dbxignore uninstall` first.
- PowerShell scripts under `scripts/` should start with `#requires -Version 7.0` to avoid UTF-8 BOM divergence between Windows PowerShell 5.1 and PS 7+.
- If using `--no-verify`, remember it skips all hooks, including commit-msg checks. Validate every new commit subject manually before pushing.
- `commit-check -m <file>` reads the entire file as the subject. In loops, write only the bare subject to the temp file.
- `commit-check` subject length is byte-based. Multi-byte punctuation can exceed the cap even when the subject looks visually short.
- `isinstance(True, int)` is true. JSON numeric validation must explicitly reject bools.
- Swapping a high-level write primitive (`Path.write_text`, text-mode `open(...)`) for a lower-level shape (`tempfile.mkstemp` + `os.fdopen` + `os.replace`) silently loses inherited defaults. Two were observed on PR #207: text-mode `\n`→`\r\n` translation on Windows (pin with `newline=""`) and umask-based file mode on POSIX (mkstemp creates at `0o600`; restore via `os.chmod` to the existing file's mode or `0o666 & ~umask` for a new file). Audit each invariant the prior primitive carried via its defaults before consolidating; preserve or change explicitly, never inherit silently from the new primitive.
- The repo has "delete branch on merge" enabled. A `git push` to a local branch whose origin counterpart was auto-deleted after merging the PR recreates the branch on origin silently (`* [new branch]` in the push output). When the next push to a branch you expected to already exist shows `[new branch]`, run `gh pr view <num> --json state` to confirm; the freshly-pushed commits sit on an unattached branch until a new PR is opened.
- `uv tool install <local-path>` (used when validating a local working tree, e.g. `manual-test-windows.ps1 -InstallSpec C:\Dropbox\git\dbxignore`) can serve a stale cached wheel even after `uv tool uninstall <name>`. The tool's symlinks-and-venv are purged on uninstall, but uv's build/wheel cache at `%LOCALAPPDATA%\uv\cache\` is not — re-installing from the same path can pick up an older build whose source hash uv treats as a cache hit. The drift is detectable by `hatch-vcs`'s embedded commit SHA: compare `uv tool list`'s reported `dev<N>+g<SHA>` against `git rev-parse --short HEAD`; a mismatch means a stale wheel. Remediation: `uv cache clean` between iterations (or `uv tool install --reinstall`).
- Plain `pre-commit install` only installs the default `pre-commit` stage; it does NOT install `commit-msg` or `pre-push` hooks even when `.pre-commit-config.yaml` references those stages. The project's commit-check rules are wired at `commit-msg` (subject validation) and `pre-push` (branch-name validation), so a clone with only `pre-commit install` lets over-cap subjects through to CI. The full install needs explicit flags: `pre-commit install --hook-type commit-msg --hook-type pre-push`. Verify via `ls .git/hooks/` — `commit-msg` and `pre-push` should both be present alongside `pre-commit`. Hit 2026-05-12 when a 75-char `docs(internals):` subject bypassed local enforcement and got rejected by the commit-check-action in CI.
- Under `set -euo pipefail`, piping a Python CLI's output directly into `grep -q` is a false-failure trap when the producer emits more than one `click.echo` call. The interaction: `click.echo` writes-and-flushes on every call (it's not deferred-until-exit), but it issues separate `write+flush` per line. `grep -q` matches the first line, exits 0, closes the pipe's read end. The producer's NEXT `click.echo` then writes to a closed pipe → `BrokenPipeError` → Python exits 1 → `pipefail` propagates that 1 to the overall pipe exit → bash treats the `if`-branch as the failing path even though the regex matched. The diagnostic giveaway: re-running the same command outside the pipe shows the expected line clearly, but the `grep -q` keeps "failing." Fix: capture into a variable first, then grep the variable (`out="$(cmd 2>&1 || true)"; printf '%s\n' "$out" | grep -qE ...`) — single-`click.echo` producers (`--summary`, `--version`) are safe by accident. Hit 2026-05-12 on manual-test 5f's human-path `dbxignore status` check.

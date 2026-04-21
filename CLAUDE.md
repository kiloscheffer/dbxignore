# dropboxignore

Cross-platform Python utility: keeps Dropbox ignore markers (NTFS alternate data streams on Windows; `user.com.dropbox.ignored` xattrs on Linux) in sync with hierarchical `.dropboxignore` files.

## Commands

- `uv sync --all-extras` — install
- `uv run pytest` — full suite; Windows adds a few ADS-integration tests via `@pytest.mark.windows_only`
- `uv run pytest -m "not windows_only"` — portable subset (what Ubuntu CI runs)
- `uv run pytest -W error::DeprecationWarning` — local strict mode (not enforced in CI)
- `uv run ruff check` — lint; rules E, F, I, B, UP, SIM; line length 100
- `dropboxignore <apply|status|list|explain|daemon|install|uninstall>` — CLI console script (`cli:main`). `install` / `uninstall` register / remove the daemon with the platform's user-scoped service manager (Task Scheduler on Windows, systemd user unit on Linux). `uninstall --purge` also clears every ignore marker.
- `dropboxignored` — daemon shim (`cli:daemon_main`), launched by the installed Scheduled Task

## Architecture

`reconcile.reconcile_subtree(root, subdir, cache)` is the single source of truth for rule-driven marker mutations. `cli.apply`, `daemon._dispatch`, and `daemon._sweep_once` all call it — never bypass. The lone exception is `cli.uninstall --purge`, which issues an unconditional marker clear (no rule evaluation) while still honoring the `.dropboxignore`-found-marked `WARNING` contract inline.

Marker I/O is platform-dispatched via `dropboxignore.markers`, which at import time re-exports `is_ignored`/`set_ignored`/`clear_ignored` from `_backends/windows_ads.py` (Windows NTFS ADS) or `_backends/linux_xattr.py` (Linux `user.com.dropbox.ignored`). No other module branches on `sys.platform` for markers. `reconcile._reconcile_path` catches `OSError(errno.ENOTSUP|EOPNOTSUPP)` from the Linux backend and treats it the same way as `PermissionError` — log WARNING, append to `Report.errors`, continue the sweep.

`daemon._sweep_once` fans `reconcile_subtree` out across roots via `ThreadPoolExecutor` (one worker per root). Safe because reconcile reads the cache lock-free (single-op `.get()`s) and writes per-file ignore markers on disjoint paths. `RuleCache._rules` is guarded by a `threading.RLock` — any mutation (`load_root`, `reload_file`, `remove_file`, or the stale-purge iteration in `load_root`) must go through it, otherwise the debouncer thread can race with the main-thread sweep. If you add cross-root shared state to `RuleCache` or reconcile, revisit this.

`rules.RuleCache` stores one `_LoadedRules(lines, entries, mtime_ns, size)` per `.dropboxignore`. `entries` is a list of `(source_line_index, pathspec.Pattern)` pairs and is the single source of truth for both `match()` and `explain()`.

`rules._load_if_changed` skips reparse when a `.dropboxignore`'s `mtime_ns` and `size` both match the cached values — that's why `_LoadedRules` carries stat fields. The sweep path (`load_root`) uses it; watchdog-driven `reload_file` bypasses it because an explicit event is authoritative.

The daemon's watchdog events are classified (`_classify` → `EventKind.{RULES,DIR_CREATE,OTHER}`) and funneled through `Debouncer` before `_dispatch` runs `reconcile_subtree`. `DEFAULT_TIMEOUTS_MS` per kind is overridable via `DROPBOXIGNORE_DEBOUNCE_{RULES,DIRS,OTHER}_MS`.

`install/` is a package with `__init__.py` exposing `install_service()` / `uninstall_service()` dispatchers, plus `windows_task.py` (schtasks XML generation + invocation) and `linux_systemd.py` (writes `~/.config/systemd/user/dropboxignore.service`, runs `systemctl --user daemon-reload && enable --now`). `cli.install` / `cli.uninstall` delegate only to the package dispatcher; don't call backend modules directly from the CLI layer.

## Gotchas

- pathspec 1.0.4: subclass `GitIgnoreSpecPattern`, not deprecated `GitWildMatchPattern`.
- pathspec 1.0.4: `spec.check_file(path)` returns `CheckResult(include, index, file)` — use when you need pattern-level verdicts beyond a bare bool.
- pathspec: `pattern.match_file()` is public; `pattern.regex.match` is private API.
- pathspec: directory-only rules (`node_modules/`) require trailing `/` on the tested path string.
- pathspec: a line with leading whitespace before `#` (e.g. `"   # indented"`) is an *active pattern*, not a comment — `rules._build_entries` detects the count mismatch and falls back to per-line reparse.
- `_backends/windows_ads` uses `open(r"\\?\path:com.dropbox.ignored")` directly — `\\?\` prefix mandatory for >260-char paths.
- NTFS is case-insensitive; `_CaseInsensitiveGitIgnorePattern` prepends `(?i)` to compiled regexes.
- `.dropboxignore` files are never marked ignored — guarded in `match()` and `explain()`; `reconcile._reconcile_path` clears any ADS marker it finds on one and logs at `WARNING` (spec contract — don't silence it in a refactor).
- `rules.match/explain` and `markers.{is,set,clear}_ignored` all require **absolute** paths and raise `ValueError` on relative ones. Resolve at the CLI/daemon boundary, never inside the cache or markers layer — `Path.resolve()` on Windows is a per-call syscall that dominated sweep wall-clock before.
- `daemon._configured_logging()` is a context manager: it snapshots the `dropboxignore` logger on enter and restores handlers/propagate/level on exit. On Linux it installs two handlers — `RotatingFileHandler(daemon.log)` plus `StreamHandler(sys.stderr)` so systemd-journald captures the same records — on Windows only the file handler. Tests that count handlers must branch on `sys.platform`. `run()` wraps its body in it, so tests that call `daemon.run()` don't need to hand-restore logger state — but if you mock it out in a test, use `contextlib.nullcontext` (see `test_daemon_singleton.py`).
- Use `datetime.UTC`, not `timezone.utc` (ruff UP017).
- Test helpers (`FakeMarkers`, `fake_markers` fixture, `write_file` fixture) live in `tests/conftest.py` and are auto-available to every test module.
- Log-contract tests use `caplog.at_level(logging.WARNING, logger="dropboxignore.<module>")` — narrow to the submodule that emits the log (see `tests/test_reconcile_edges.py`).
- Windows-only tests: set `pytestmark = pytest.mark.windows_only` at module level and guard with `if sys.platform != "win32": pytest.skip(..., allow_module_level=True)` so non-Windows collection skips cleanly.
- Daemon/sweep tests that trigger state writes: `monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")` redirects the persisted state off the real per-user state dir and keeps the test hermetic. Per-user state dir is `%LOCALAPPDATA%\dropboxignore\` on Windows and `$XDG_STATE_HOME/dropboxignore/` (fallback `~/.local/state/dropboxignore/`) on Linux; the single source of truth is `state.user_state_dir()`, used by both `state.default_path()` and `daemon._log_dir()`. `state.read()` without an explicit path transparently falls back to the legacy Linux path (`~/AppData/Local/dropboxignore/state.json`) for one release and logs WARNING; `state.read(path)` with an explicit path does not.
- Root-discovery test seams, by layer: CLI tests monkeypatch `cli._discover_roots`; daemon tests monkeypatch `daemon.roots_module.discover`; `roots.discover()` unit tests use `_stage_info(monkeypatch, tmp_path, fixture_name)` which sets `APPDATA` (Windows) or `HOME` (Linux) to point at a staged `Dropbox/info.json` / `.dropbox/info.json`. Pick the layer that matches the code path under test.
- Linux xattrs vanish silently through common operations: `cp` without `-a`, cross-filesystem `mv`, most archivers, and `vim`'s default save-via-rename. The watchdog event stream + hourly sweep are the recovery mechanism — don't add a preservation wrapper, the design intentionally leans on reconcile.
- Linux backends use `follow_symlinks=False` on all xattr calls (mirrors `os.walk(followlinks=False)` in reconcile). A symlink marked ignored means the link itself is marked, not its target — **except** that the Linux kernel refuses `user.*` xattrs on symlinks entirely (EPERM). `set_ignored`/`clear_ignored` raise `PermissionError` on symlinks, which reconcile's existing `PermissionError` arm already handles (log + skip). `is_ignored` on a symlink returns False (ENODATA).
- `roots.discover()` branches on `sys.platform`: `%APPDATA%\Dropbox\info.json` on Windows, `~/.dropbox/info.json` on Linux. Same JSON schema. The `_info_json_path()` helper returns `None` on unsupported platforms (raises a WARNING log); `discover()` then returns `[]`.
- `roots.discover()` honors `DROPBOXIGNORE_ROOT` as a pre-`info.json` escape hatch for non-stock Dropbox installs. Set to an existing absolute path → `[Path(env)]`, bypassing `info.json` (and the platform check — useful on platforms where `_info_json_path()` returns `None`). Set to a nonexistent path → WARNING + `[]`. Empty string → treated as unset. Single-root only; multi-account setups with an override have to pick one.

## Git workflow

- Never commit directly to `main`. Work on a topic branch and open a PR — that's what triggers `.github/workflows/test.yml` (the platform-gated test tiers `pytest -m windows_only` and `pytest -m linux_only` **only run in CI**) and `.github/workflows/commit-check.yml` (commit-message + branch-name validation). A local `uv run pytest` can only exercise one platform, so a single green local run is not a merge gate. The PR matrix is.
- **`cchk.toml` at repo root is the single source of truth** for allowed commit types (Conventional Commits, see `allow_commit_types`), branch types (Conventional Branch, see `allow_branch_types`), and the subject-length cap. Don't restate those lists here or elsewhere — reference the file so it can't drift. Local enforcement is optional but encouraged: `uv tool install pre-commit && pre-commit install --hook-type commit-msg --hook-type pre-push` wires the same rules at commit/push time. CI re-runs them on every PR via `commit-check/commit-check-action@v2.6.0`.
- Branch names follow `<type>/<slug>` where `<type>` is from `cchk.toml`'s `allow_branch_types` and `<slug>` is lowercase-alphanumeric + hyphens. Note the asymmetry with commit subjects: the branch prefix `feature/` is the long form while the Conventional Commits subject tag `feat:` is the short form. Same repo, two conventions. Examples: `feature/v0.2-linux`, `fix/v0.2-followups-2-5`, `fix/v0.2-followup-1-linux-xdg-paths`.
- Commit subjects follow Conventional Commits: `<type>(<scope>): <description>` where `<type>` is from `cchk.toml`'s `allow_commit_types`. Scope tags mirror package names or doc categories, not ticket numbers. `!` before the colon — or a `BREAKING CHANGE:` footer — signals a breaking change.
- Split commits along revertability lines: a code change and a doc-only backlog update belong in separate commits because they could plausibly be reverted at different times. PR #4 is the template — one `feat` commit for the behavior change, one `docs` commit for the new follow-up entries.
- If commits land on `main` locally by mistake, create the topic branch at current `HEAD` **first**, then `git reset --hard origin/main` on `main`. The branch ref preserves the commits so the reset is non-destructive. Never run the reset before the branch is created.
- After a PR merges, `git checkout main && git pull --ff-only && git branch -d <merged-branch>` keeps the local tree clean. The GitHub-side branch is already deleted by the merge-and-delete UI; don't push a deletion for it.

## Release

- `.github/workflows/test.yml` runs ruff + the portable pytest subset on `ubuntu-latest` and `windows-latest` for every push/PR. The Windows leg additionally runs `pytest -m windows_only`; the Linux leg additionally runs `pytest -m linux_only`.
- Push tag `v*` → `.github/workflows/release.yml` builds wheel + `dropboxignore.exe` / `dropboxignored.exe` (via `pyinstaller/dropboxignore.spec`) and publishes a GitHub Release.

## Docs

- Design: `docs/superpowers/specs/2026-04-20-dropboxignore-design.md`
- Plan: `docs/superpowers/plans/2026-04-20-dropboxignore-implementation.md`
- v0.2 product/risk follow-ups: design doc § Open questions.

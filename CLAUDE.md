# dbxignore

Cross-platform Python utility: keeps Dropbox ignore markers (NTFS alternate data streams on Windows; `user.com.dropbox.ignored` xattrs on Linux) in sync with hierarchical `.dropboxignore` files.

## Commands

- `uv sync --all-extras` — install
- `uv run pytest` — full suite; Windows adds a few ADS-integration tests via `@pytest.mark.windows_only`
- `uv run pytest -m "not windows_only"` — portable subset (what Ubuntu CI runs)
- `uv run pytest -W error::DeprecationWarning` — local strict mode (not enforced in CI)
- `uv run ruff check` — lint; rules E, F, I, B, UP, SIM; line length 100
- `dbxignore <apply|status|list|explain|daemon|install|uninstall>` — CLI console script (`cli:main`). `install` / `uninstall` register / remove the daemon with the platform's user-scoped service manager (Task Scheduler on Windows, systemd user unit on Linux). `uninstall --purge` also clears every ignore marker.
- `dbxignored` — daemon shim (`cli:daemon_main`), launched by the installed Scheduled Task

## Architecture

`reconcile.reconcile_subtree(root, subdir, cache)` is the single source of truth for rule-driven marker mutations. `cli.apply`, `daemon._dispatch`, and `daemon._sweep_once` all call it — never bypass. The lone exception is `cli.uninstall --purge`, which issues an unconditional marker clear (no rule evaluation) while still honoring the `.dropboxignore`-found-marked `WARNING` contract inline. After the marker clear, `--purge` also calls `_purge_local_state()` (removes `state.json`, `daemon.log*`, the per-user state dir if empty) and on Linux additionally calls `linux_systemd.remove_dropin_directory()` to drop any user customization unit drop-ins. Goal: leave no dbxignore-authored artifacts on disk.

Marker I/O is platform-dispatched via `dbxignore.markers`, which at import time re-exports `is_ignored`/`set_ignored`/`clear_ignored` from `_backends/windows_ads.py` (Windows NTFS ADS) or `_backends/linux_xattr.py` (Linux `user.com.dropbox.ignored`). No other module branches on `sys.platform` for markers. `reconcile._reconcile_path` catches `OSError(errno.ENOTSUP|EOPNOTSUPP)` from the Linux backend and treats it the same way as `PermissionError` — log WARNING, append to `Report.errors`, continue the sweep.

`daemon._sweep_once` fans `reconcile_subtree` out across roots via `ThreadPoolExecutor` (one worker per root). Safe because reconcile reads the cache lock-free (single-op `.get()`s) and writes per-file ignore markers on disjoint paths. `RuleCache._rules` is guarded by a `threading.RLock` — any mutation (`load_root`, `reload_file`, `remove_file`, or the stale-purge iteration in `load_root`) must go through it, otherwise the debouncer thread can race with the main-thread sweep. If you add cross-root shared state to `RuleCache` or reconcile, revisit this.

`rules.RuleCache` stores one `_LoadedRules(lines, entries, mtime_ns, size)` per `.dropboxignore`. `entries` is a list of `(source_line_index, pathspec.Pattern)` pairs and is the single source of truth for both `match()` and `explain()`.

`rules._load_if_changed` skips reparse when a `.dropboxignore`'s `mtime_ns` and `size` both match the cached values — that's why `_LoadedRules` carries stat fields. The sweep path (`load_root`) uses it; watchdog-driven `reload_file` bypasses it because an explicit event is authoritative.

The daemon's watchdog events are classified (`_classify` → `EventKind.{RULES,DIR_CREATE,OTHER}`) and funneled through `Debouncer` before `_dispatch` runs `reconcile_subtree`. `DEFAULT_TIMEOUTS_MS` per kind is overridable via `DBXIGNORE_DEBOUNCE_{RULES,DIRS,OTHER}_MS`.

`install/` is a package with `__init__.py` exposing `install_service()` / `uninstall_service()` dispatchers, plus `windows_task.py` (schtasks XML generation + invocation) and `linux_systemd.py` (writes `~/.config/systemd/user/dbxignore.service`, runs `systemctl --user daemon-reload && enable --now`). `cli.install` / `cli.uninstall` delegate only to the package dispatcher; don't call backend modules directly from the CLI layer.

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
- `daemon._configured_logging()` is a context manager: it snapshots the `dbxignore` logger on enter and restores handlers/propagate/level on exit. On Linux it installs two handlers — `RotatingFileHandler(daemon.log)` plus `StreamHandler(sys.stderr)` so systemd-journald captures the same records — on Windows only the file handler. Tests that count handlers must branch on `sys.platform`. `run()` wraps its body in it, so tests that call `daemon.run()` don't need to hand-restore logger state — but if you mock it out in a test, use `contextlib.nullcontext` (see `test_daemon_singleton.py`).
- Use `datetime.UTC`, not `timezone.utc` (ruff UP017).
- Test helpers (`FakeMarkers`, `fake_markers` fixture, `write_file` fixture) live in `tests/conftest.py` and are auto-available to every test module.
- Log-contract tests use `caplog.at_level(logging.WARNING, logger="dbxignore.<module>")` — narrow to the submodule that emits the log (see `tests/test_reconcile_edges.py`).
- Windows-only tests: set `pytestmark = pytest.mark.windows_only` at module level and guard with `if sys.platform != "win32": pytest.skip(..., allow_module_level=True)` so non-Windows collection skips cleanly.
- Daemon/sweep tests that trigger state writes: `monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")` redirects the persisted state off the real per-user state dir and keeps the test hermetic. Per-user state dir is `%LOCALAPPDATA%\dbxignore\` on Windows and `$XDG_STATE_HOME/dbxignore/` (fallback `~/.local/state/dbxignore/`) on Linux; the single source of truth is `state.user_state_dir()`, used by both `state.default_path()` and `daemon._log_dir()`. The v0.2-era Linux legacy-path fallback (`~/AppData/Local/dropboxignore/state.json`) was removed in v0.3 — clean-break upgrade path via `dropboxignore uninstall --purge` means no v0.2.x state survives, so the fallback had no remaining callers.
- Root-discovery test seams, by layer: CLI tests monkeypatch `cli._discover_roots`; daemon tests monkeypatch `daemon.roots_module.discover`; `roots.discover()` unit tests use `_stage_info(monkeypatch, tmp_path, fixture_name)` which sets `APPDATA` (Windows) or `HOME` (Linux) to point at a staged `Dropbox/info.json` / `.dropbox/info.json`. Pick the layer that matches the code path under test.
- Linux xattrs vanish silently through common operations: `cp` without `-a`, cross-filesystem `mv`, most archivers, and `vim`'s default save-via-rename. The watchdog event stream + hourly sweep are the recovery mechanism — don't add a preservation wrapper, the design intentionally leans on reconcile.
- Verifying xattrs on a Linux box without the `attr` package (e.g., a fresh VPS without sudo) — Python's stdlib covers it: `python3 -c "import os; print(os.listxattr(path), os.getxattr(path, 'user.com.dropbox.ignored'))"`. Useful for regression smoke tests that would otherwise need `apt install attr`.
- Linux backends use `follow_symlinks=False` on all xattr calls (mirrors `os.walk(followlinks=False)` in reconcile). A symlink marked ignored means the link itself is marked, not its target — **except** that the Linux kernel refuses `user.*` xattrs on symlinks entirely (EPERM). `set_ignored`/`clear_ignored` raise `PermissionError` on symlinks, which reconcile's existing `PermissionError` arm already handles (log + skip). `is_ignored` on a symlink returns False (ENODATA).
- `roots.discover()` branches on `sys.platform`: `%APPDATA%\Dropbox\info.json` on Windows, `~/.dropbox/info.json` on Linux. Same JSON schema. The `_info_json_path()` helper returns `None` on unsupported platforms (raises a WARNING log); `discover()` then returns `[]`.
- `roots.discover()` honors `DBXIGNORE_ROOT` as a pre-`info.json` escape hatch for non-stock Dropbox installs. Set to an existing absolute path → `[Path(env)]`, bypassing `info.json` (and the platform check — useful on platforms where `_info_json_path()` returns `None`). Set to a nonexistent path → WARNING + `[]`. Empty string → treated as unset. Single-root only; multi-account setups with an override have to pick one.
- `RuleCache` runs a static conflict detector at every mutation (`load_root`, `reload_file`, `remove_file`). Negations whose literal path prefix lives under a directory matched by an earlier include rule are recorded in `_conflicts` and their `(source, line_idx)` tuple is added to `_dropped`. `match()` and reconcile ignore anything in `_dropped`; `explain()` still returns dropped matches but sets `Match.is_dropped=True` so CLI/log formatters can annotate them. Semantic reason: Dropbox's ignored-folder inheritance makes negations inert under ignored ancestors, so we don't pretend they're in effect.
- Local pytest gotcha: `uv run pytest` may not pick up the editable install after `uv pip install -e .` (silent `ModuleNotFoundError: No module named 'dbxignore'`). Use `uv run python -m pytest` instead — `python -m` invokes the interpreter that has the `.pth` file. CI doesn't hit this (fresh provisioning).
- Check a GitHub Action's runtime version before bumping: `gh api "repos/<owner>/<repo>/contents/action.yml?ref=<tag>" --jq .content | base64 -d | grep using`. Returns `using: 'nodeNN'` for Node-based actions, `using: composite` for shell-script orchestrators. Composite actions are categorically immune to Node-version deprecations — `commit-check@v2.6.0` and `pypa/gh-action-pypi-publish@release/v1` are both composite.

## Git workflow

- Never commit directly to `main`. Work on a topic branch and open a PR — that's what triggers `.github/workflows/test.yml` (the platform-gated test tiers `pytest -m windows_only` and `pytest -m linux_only` **only run in CI**) and `.github/workflows/commit-check.yml` (commit-message + branch-name validation). A local `uv run pytest` can only exercise one platform, so a single green local run is not a merge gate. The PR matrix is.
- **`cchk.toml` at repo root is the single source of truth** for allowed commit types (Conventional Commits, see `allow_commit_types`), branch types (Conventional Branch, see `allow_branch_types`), and the subject-length cap. Don't restate those lists here or elsewhere — reference the file so it can't drift. Local enforcement is optional but encouraged: `uv tool install pre-commit && pre-commit install --hook-type commit-msg --hook-type pre-push` wires the same rules at commit/push time. CI re-runs them on every PR via `commit-check/commit-check-action@v2.6.0`.
- **Pre-flight against every commit, not just HEAD.** CI runs `commit-check` against the full `origin/main..HEAD` range; a local check that only validates the planned PR title can pass while an intermediate commit fails CI (happened on PR #12 — one commit's description starting with `--` tripped commit-check's regex). Before pushing, loop over every commit's subject:
  ```bash
  git log --pretty=format:'%s%n' origin/main..HEAD | while IFS= read -r msg; do
    [ -z "$msg" ] && continue
    printf '%s\n' "$msg" > /tmp/m.txt
    commit-check --message --no-banner --compact /tmp/m.txt || echo "FAIL: $msg"
  done
  ```
  Local green then matches CI green on the message check.
- Branch names follow `<type>/<slug>` where `<type>` is from `cchk.toml`'s `allow_branch_types` and `<slug>` is lowercase-alphanumeric + hyphens. Note two asymmetries with commit subjects: (1) the branch prefix `feature/` is the long form while the Conventional Commits subject tag `feat:` is the short form; (2) `allow_branch_types` is a **strict subset** of `allow_commit_types` — `docs/`, `style/`, `refactor/`, `perf/`, `test/`, `build/`, `ci/`, `revert/` are valid commit types but NOT valid branch prefixes. Use `chore/` for those categories of work. Examples: `feature/v0.2-linux`, `fix/v0.2-followups-2-5`, `fix/v0.2-followup-1-linux-xdg-paths`.
- Commit subjects follow Conventional Commits: `<type>(<scope>): <description>` where `<type>` is from `cchk.toml`'s `allow_commit_types`. Scope tags mirror package names or doc categories, not ticket numbers. `!` before the colon — or a `BREAKING CHANGE:` footer — signals a breaking change.
- Split commits along revertability lines: a code change and a doc-only backlog update belong in separate commits because they could plausibly be reverted at different times. PR #4 is the template — one `feat` commit for the behavior change, one `docs` commit for the new follow-up entries.
- If commits land on `main` locally by mistake, create the topic branch at current `HEAD` **first**, then `git reset --hard origin/main` on `main`. The branch ref preserves the commits so the reset is non-destructive. Never run the reset before the branch is created.
- Remote is HTTPS (`https://github.com/kiloscheffer/dbxignore.git`). Keep it HTTPS unless you've verified SSH keys for `github.com` are set up on the current machine — a silent `set-url` to `git@github.com:...` produces "Host key verification failed" at push time.
- After a PR merges, `git checkout main && git pull --ff-only && git branch -d <merged-branch>` keeps the local tree clean. The GitHub-side branch is already deleted by the merge-and-delete UI; don't push a deletion for it.

## Release

- `.github/workflows/test.yml` runs ruff + the portable pytest subset on `ubuntu-latest` and `windows-latest` for every push/PR. The Windows leg additionally runs `pytest -m windows_only`; the Linux leg additionally runs `pytest -m linux_only`.
- Push tag `v*` → `.github/workflows/release.yml` builds wheel + `dbxignore.exe` / `dbxignored.exe` (via `pyinstaller/dbxignore.spec`) and publishes to two destinations: GitHub Release (auto, tag-gated) and PyPI (auto-gated on the `pypi` GitHub environment; requires maintainer approval click before the OIDC upload fires). `hatch-vcs` derives the version from the tag — no manual `pyproject.toml` bump needed. PyPI uses Trusted Publishing; no API token secret stored.
- Before tagging, sanity-check wheel metadata: `uv build && unzip -p dist/*.whl '*.dist-info/METADATA' | head` — confirms `Name: dbxignore` and the expected `Version:` are in the wheel before a misnamed upload hits PyPI (immutable; yank-only recovery).
- `CHANGELOG.md` at repo root follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/): new entries accrue under an `[Unreleased]` heading (add it at the top when the first post-release change lands) and roll into a version heading with its release date when the tag goes out. Hand-crafted per-version release bodies live under `docs/release-notes/v<X.Y.Z>.md` for use with `gh release edit v<X.Y.Z> --notes-file docs/release-notes/v<X.Y.Z>.md` after the workflow publishes.
- This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Pre-1.0, breaking changes ride MINOR bumps with explicit **Breaking** callouts in the CHANGELOG — v0.2.0 introduced two (broadened `--purge`, changed `explain` format). Post-1.0, breaking changes will bump MAJOR.

## Docs

Specs and plans are kept side-by-side under `docs/superpowers/{specs,plans}/`, named `<YYYY-MM-DD>-<slug>.md`. Per-version release bodies live under `docs/release-notes/v<X.Y.Z>.md`.

- v0.1 (initial): `specs/2026-04-20-dropboxignore-design.md` + `plans/2026-04-20-dropboxignore-implementation.md`
- v0.2 Linux port: `specs/2026-04-21-dropboxignore-v0.2-linux.md` + `plans/2026-04-21-dropboxignore-v0.2-linux.md`; followups in `plans/2026-04-21-dropboxignore-v0.2-linux-followups.md`
- v0.2.1 negation semantics: `specs/2026-04-21-dropboxignore-negation-semantics.md` + `plans/2026-04-22-dropboxignore-negation-semantics.md`; followups in `plans/2026-04-22-dropboxignore-negation-polish-followups.md`
- v0.3 rename + PyPI: `specs/2026-04-23-v0.3-dbxignore-rename.md` + `plans/2026-04-23-v0.3-dbxignore-rename.md`
- Open follow-ups for any active line of work live in that line's `*-followups.md`; check there before adding new tracking docs.
- Followup-tracker conventions: each item's body ends with `**Status: RESOLVED <date>.** <what landed + provenance PR>` once resolved; bottom `## Status` section maintains a running roll-up with PR provenance. Inline-marker convention started ~PR #24 and was retroactively backfilled for older items in PR #41 — every item should now carry both an inline marker and a Status-section entry.

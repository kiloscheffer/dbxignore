# dropboxignore

Windows-only Python utility: keeps NTFS `com.dropbox.ignored` streams in sync with hierarchical `.dropboxignore` files.

## Commands

- `uv sync --all-extras` — install
- `uv run pytest` — full suite; Windows adds a few ADS-integration tests via `@pytest.mark.windows_only`
- `uv run pytest -m "not windows_only"` — portable subset (what Ubuntu CI runs)
- `uv run pytest -W error::DeprecationWarning` — local strict mode (not enforced in CI)
- `uv run ruff check` — lint; rules E, F, I, B, UP, SIM; line length 100
- `dropboxignore <apply|status|list|explain|daemon|install|uninstall>` — CLI console script (`cli:main`); `uninstall --purge` also clears every ADS marker.
- `dropboxignored` — daemon shim (`cli:daemon_main`), launched by the installed Scheduled Task

## Architecture

`reconcile.reconcile_subtree(root, subdir, cache)` is the single source of truth for rule-driven ADS mutations. `cli.apply`, `daemon._dispatch`, and `daemon._sweep_once` all call it — never bypass. The lone exception is `cli.uninstall --purge`, which issues an unconditional ADS clear (no rule evaluation) while still honoring the `.dropboxignore`-found-marked `WARNING` contract inline.

`daemon._sweep_once` fans `reconcile_subtree` out across roots via `ThreadPoolExecutor` (one worker per root). Safe because reconcile reads the cache lock-free (single-op `.get()`s) and writes per-file ADS markers on disjoint paths. `RuleCache._rules` is guarded by a `threading.RLock` — any mutation (`load_root`, `reload_file`, `remove_file`, or the stale-purge iteration in `load_root`) must go through it, otherwise the debouncer thread can race with the main-thread sweep. If you add cross-root shared state to `RuleCache` or reconcile, revisit this.

`rules.RuleCache` stores one `_LoadedRules(lines, entries, mtime_ns, size)` per `.dropboxignore`. `entries` is a list of `(source_line_index, pathspec.Pattern)` pairs and is the single source of truth for both `match()` and `explain()`.

`rules._load_if_changed` skips reparse when a `.dropboxignore`'s `mtime_ns` and `size` both match the cached values — that's why `_LoadedRules` carries stat fields. The sweep path (`load_root`) uses it; watchdog-driven `reload_file` bypasses it because an explicit event is authoritative.

The daemon's watchdog events are classified (`_classify` → `EventKind.{RULES,DIR_CREATE,OTHER}`) and funneled through `Debouncer` before `_dispatch` runs `reconcile_subtree`. `DEFAULT_TIMEOUTS_MS` per kind is overridable via `DROPBOXIGNORE_DEBOUNCE_{RULES,DIRS,OTHER}_MS`.

## Gotchas

- pathspec 1.0.4: subclass `GitIgnoreSpecPattern`, not deprecated `GitWildMatchPattern`.
- pathspec 1.0.4: `spec.check_file(path)` returns `CheckResult(include, index, file)` — use when you need pattern-level verdicts beyond a bare bool.
- pathspec: `pattern.match_file()` is public; `pattern.regex.match` is private API.
- pathspec: directory-only rules (`node_modules/`) require trailing `/` on the tested path string.
- pathspec: a line with leading whitespace before `#` (e.g. `"   # indented"`) is an *active pattern*, not a comment — `rules._build_entries` detects the count mismatch and falls back to per-line reparse.
- `ads` uses `open(r"\\?\path:com.dropbox.ignored")` directly — `\\?\` prefix mandatory for >260-char paths.
- NTFS is case-insensitive; `_CaseInsensitiveGitIgnorePattern` prepends `(?i)` to compiled regexes.
- `.dropboxignore` files are never marked ignored — guarded in `match()` and `explain()`; `reconcile._reconcile_path` clears any ADS marker it finds on one and logs at `WARNING` (spec contract — don't silence it in a refactor).
- `rules.match/explain` and `ads.{is,set,clear}_ignored` all require **absolute** paths and raise `ValueError` on relative ones. Resolve at the CLI/daemon boundary, never inside the cache or ADS layer — `Path.resolve()` on Windows is a per-call syscall that dominated sweep wall-clock before.
- `daemon._configured_logging()` is a context manager: it snapshots the `dropboxignore` logger on enter and restores handlers/propagate/level on exit. `run()` wraps its body in it, so tests that call `daemon.run()` don't need to hand-restore logger state — but if you mock it out in a test, use `contextlib.nullcontext` (see `test_daemon_singleton.py`).
- Use `datetime.UTC`, not `timezone.utc` (ruff UP017).
- Test helpers (`FakeADS`, `fake_ads` fixture, `write_file` fixture) live in `tests/conftest.py` and are auto-available to every test module.
- Log-contract tests use `caplog.at_level(logging.WARNING, logger="dropboxignore.<module>")` — narrow to the submodule that emits the log (see `tests/test_reconcile_edges.py`).
- Windows-only tests: set `pytestmark = pytest.mark.windows_only` at module level and guard with `if sys.platform != "win32": pytest.skip(..., allow_module_level=True)` so non-Windows collection skips cleanly.
- Daemon/sweep tests that trigger state writes: `monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")` redirects the persisted state off `LOCALAPPDATA` and keeps the test hermetic.
- Root-discovery test seams, by layer: CLI tests monkeypatch `cli._discover_roots`; daemon tests monkeypatch `daemon.roots_module.discover`; `roots.discover()` unit tests set `APPDATA` to point at a staged `Dropbox/info.json`. Pick the layer that matches the code path under test.

## Release

- `.github/workflows/test.yml` runs ruff + the portable pytest subset on `ubuntu-latest` and `windows-latest` for every push/PR; the Windows leg also runs `pytest -m windows_only`.
- Push tag `v*` → `.github/workflows/release.yml` builds wheel + `dropboxignore.exe` / `dropboxignored.exe` (via `pyinstaller/dropboxignore.spec`) and publishes a GitHub Release.

## Docs

- Design: `docs/superpowers/specs/2026-04-20-dropboxignore-design.md`
- Plan: `docs/superpowers/plans/2026-04-20-dropboxignore-implementation.md`
- v0.2 product/risk follow-ups: design doc § Open questions.

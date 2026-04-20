# dropboxignore

Windows-only Python utility: keeps NTFS `com.dropbox.ignored` streams in sync with hierarchical `.dropboxignore` files.

## Commands

- `uv sync --all-extras` — install
- `uv run pytest` — full suite; Windows adds a few ADS-integration tests via `@pytest.mark.windows_only`
- `uv run pytest -m "not windows_only"` — portable subset (what Ubuntu CI runs)
- `uv run pytest -W error::DeprecationWarning` — local strict mode (not enforced in CI)
- `uv run ruff check` — lint; rules E, F, I, B, UP, SIM; line length 100
- `dropboxignore <apply|status|list|explain|install>` — CLI console script (`cli:main`)
- `dropboxignored` — daemon shim (`cli:daemon_main`), launched by the installed Scheduled Task

## Architecture

`reconcile.reconcile_subtree(root, subdir, cache)` is the single source of truth for ADS mutations. `cli.apply`, `daemon._dispatch`, and `daemon._sweep_once` all call it — never bypass.

`rules.RuleCache` stores one `_LoadedRules(lines, entries)` per `.dropboxignore`. `entries` is a list of `(source_line_index, pathspec.Pattern)` pairs and is the single source of truth for both `match()` and `explain()`.

## Gotchas

- pathspec 1.0.4: subclass `GitIgnoreSpecPattern`, not deprecated `GitWildMatchPattern`.
- pathspec 1.0.4: `spec.check_file(path)` returns `CheckResult(include, index, file)` — use when you need pattern-level verdicts beyond a bare bool.
- pathspec: `pattern.match_file()` is public; `pattern.regex.match` is private API.
- pathspec: directory-only rules (`node_modules/`) require trailing `/` on the tested path string.
- pathspec: a line with leading whitespace before `#` (e.g. `"   # indented"`) is an *active pattern*, not a comment — `rules._build_entries` detects the count mismatch and falls back to per-line reparse.
- `ads` uses `open(r"\\?\path:com.dropbox.ignored")` directly — `\\?\` prefix mandatory for >260-char paths.
- NTFS is case-insensitive; `_CaseInsensitiveGitIgnorePattern` prepends `(?i)` to compiled regexes.
- `.dropboxignore` files are never marked ignored — guarded in `match()` and `explain()`.
- `rules.match/explain` and `ads.{is,set,clear}_ignored` all require **absolute** paths and raise `ValueError` on relative ones. Resolve at the CLI/daemon boundary, never inside the cache or ADS layer — `Path.resolve()` on Windows is a per-call syscall that dominated sweep wall-clock before.
- `daemon._configured_logging()` is a context manager: it snapshots the `dropboxignore` logger on enter and restores handlers/propagate/level on exit. `run()` wraps its body in it, so tests that call `daemon.run()` don't need to hand-restore logger state — but if you mock it out in a test, use `contextlib.nullcontext` (see `test_daemon_singleton.py`).
- Use `datetime.UTC`, not `timezone.utc` (ruff UP017).
- Test helpers (`FakeADS`, `fake_ads` fixture, `write_file` fixture) live in `tests/conftest.py` and are auto-available to every test module.
- Windows-only tests: set `pytestmark = pytest.mark.windows_only` at module level and guard with `if sys.platform != "win32": pytest.skip(..., allow_module_level=True)` so non-Windows collection skips cleanly.

## Release

- Push tag `v*` → `.github/workflows/release.yml` builds wheel + `dropboxignore.exe` / `dropboxignored.exe` (via `pyinstaller/dropboxignore.spec`) and publishes a GitHub Release.

## Docs

- Design: `docs/superpowers/specs/2026-04-20-dropboxignore-design.md`
- Plan: `docs/superpowers/plans/2026-04-20-dropboxignore-implementation.md`
- v0.2 product/risk follow-ups: design doc § Open questions.
- Post-v0.2 perf/cleanup follow-ups: `docs/post-v0.2-followups.md`.

# dropboxignore

Windows-only Python utility: keeps NTFS `com.dropbox.ignored` streams in sync with hierarchical `.dropboxignore` files.

## Commands

- `uv sync --all-extras` — install
- `uv run pytest` — full suite (89 tests on Windows, 84 elsewhere)
- `uv run pytest -m "not windows_only"` — portable subset (what Ubuntu CI runs)
- `uv run pytest -W error::DeprecationWarning` — local strict mode (not enforced in CI)
- `uv run ruff check` — lint; rules E, F, I, B, UP, SIM; line length 100
- `dropboxignore <apply|status|list|explain|install>` — CLI console script (`cli:main`)
- `dropboxignored` — daemon shim (`cli:daemon_main`), launched by the installed Scheduled Task

## Architecture

`reconcile.reconcile_subtree(root, subdir, cache)` is the single source of truth for ADS mutations. `cli.apply`, `daemon._dispatch`, and `daemon._sweep_once` all call it — never bypass.

`rules.RuleCache` stores three parallel dicts per `.dropboxignore`: `_specs` (PathSpec), `_lines` (raw text), `_pattern_entries` (line-indexed patterns for `explain`).

## Gotchas

- pathspec 1.0.4: subclass `GitIgnoreSpecPattern`, not deprecated `GitWildMatchPattern`.
- pathspec: `pattern.match_file()` is public; `pattern.regex.match` is private API.
- pathspec: directory-only rules (`node_modules/`) require trailing `/` on the tested path string.
- `ads` uses `open(r"\\?\path:com.dropbox.ignored")` directly — `\\?\` prefix mandatory for >260-char paths.
- NTFS is case-insensitive; `_CaseInsensitiveGitIgnorePattern` prepends `(?i)` to compiled regexes.
- `.dropboxignore` files are never marked ignored — guarded in `match()` and `explain()`.
- `daemon._configured_logging()` is a context manager: it snapshots the `dropboxignore` logger on enter and restores handlers/propagate/level on exit. `run()` wraps its body in it, so tests that call `daemon.run()` don't need to hand-restore logger state — but if you mock it out in a test, use `contextlib.nullcontext` (see `test_daemon_singleton.py`).
- Use `datetime.UTC`, not `timezone.utc` (ruff UP017).
- Test helpers (`FakeADS`, `fake_ads` fixture, `write_file` fixture) live in `tests/conftest.py` and are auto-available to every test module.

## Release

- Push tag `v*` → `.github/workflows/release.yml` builds wheel + `dropboxignore.exe` / `dropboxignored.exe` (via `pyinstaller/dropboxignore.spec`) and publishes a GitHub Release.

## Docs

- Design: `docs/superpowers/specs/2026-04-20-dropboxignore-design.md`
- Plan: `docs/superpowers/plans/2026-04-20-dropboxignore-implementation.md`
- v0.2 product/risk follow-ups: design doc § Open questions.
- v0.2 code-simplification follow-ups: `docs/v0.2-simplification-followups.md`.

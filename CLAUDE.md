# dropboxignore

Windows-only Python utility: keeps NTFS `com.dropbox.ignored` streams in sync with hierarchical `.dropboxignore` files.

## Commands

- `uv sync --all-extras` — install
- `uv run pytest` — full suite (68 tests on Windows, 62 elsewhere)
- `uv run pytest -m "not windows_only"` — portable subset (what Ubuntu CI runs)
- `uv run pytest -W error::DeprecationWarning` — strict mode (CI enforces)
- `uv run ruff check` — lint; rules E, F, I, B, UP, SIM; line length 100

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
- `daemon._configure_logging()` sets `propagate=False` on the `dropboxignore` logger; breaks `caplog`. `test_daemon_smoke.py` snapshots/restores handler state; new daemon tests need the same pattern.
- Use `datetime.UTC`, not `timezone.utc` (ruff UP017).
- `FakeADS` test fixture lives in `tests/test_reconcile_basic.py`; imported via `from tests.test_reconcile_basic import FakeADS`. Requires `tests/__init__.py` (present).

## Docs

- Design: `docs/superpowers/specs/2026-04-20-dropboxignore-design.md`
- Plan: `docs/superpowers/plans/2026-04-20-dropboxignore-implementation.md`
- v0.2 follow-ups: design doc §Open questions + branch final-review report.

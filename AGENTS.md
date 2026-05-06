# Repository Guidelines

## Project Structure & Module Organization

`dbxignore` is a Python 3.11+ package using a `src/` layout. Core code lives in `src/dbxignore/`: CLI entry points are in `cli.py` and `__main__.py`, reconciliation in `reconcile.py`, daemon behavior in `daemon.py`, rule parsing in `rules.py`, and marker backends under `_backends/`. Install integrations are in `src/dbxignore/install/`; the default template is `src/dbxignore/templates/default.dropboxignore`. Tests live in `tests/`, with fixtures in `tests/fixtures/`. Docs, manual scripts, and PyInstaller specs live in `docs/`, `scripts/`, and `pyinstaller/`.

## Build, Test, and Development Commands

- `uv sync --all-extras` installs runtime and dev dependencies.
- `uv run python -m dbxignore status` runs the CLI from the working tree.
- `uv run pytest` runs the full local test suite; use `uv run python -m pytest` if uv script resolution fails.
- `uv run pytest -m "not windows_only and not linux_only and not macos_only"` runs the portable CI subset.
- `uv run ruff check` runs linting and import checks.
- `uv run --with pyinstaller pyinstaller pyinstaller/dbxignore.spec` builds the Windows executable; use `pyinstaller/dbxignore-macos.spec` on macOS.

## Coding Style & Naming Conventions

Follow Ruff settings in `pyproject.toml`: Python 3.11 target, 100-character lines, lint families per `[tool.ruff.lint] select` (do not restate them here — pyproject.toml is the source of truth). Use 4-space indentation, type annotations for new public helpers, and snake_case for modules, functions, variables, and pytest fixtures. Keep platform-specific logic behind the existing backend and install modules.

## Testing Guidelines

Tests use pytest with a 10-second timeout. Name files `test_*.py` and keep behavior-focused test names, such as `test_status_reports_daemon_state`. Mark platform-specific tests with `windows_only`, `linux_only`, or `macos_only`; module-level platform tests should also skip cleanly on other systems. Add or update tests for CLI behavior, rule semantics, marker handling, and install changes.

## Commit & Pull Request Guidelines

Commits and branches follow `cchk.toml`, the source of truth for Conventional Commit and Branch forms. Example: `fix(rules): preserve negation precedence`. Install hooks with `uv tool install pre-commit` and `pre-commit install --hook-type commit-msg --hook-type pre-push`. Do not commit directly to `main`; open a topic-branch PR with a concise description, linked issue or backlog item when applicable, and platform notes. CI runs Ruff plus portable and platform-gated pytest jobs.

## Agent-Specific Instructions

Respect user changes in the working tree. Do not rewrite backlog, changelog, generated version files, or platform scripts unless the task calls for it. When changing user-visible CLI behavior or marker side effects, keep README, tests, and manual scripts in sync.

## How to run checks

Use these commands before claiming a change is safe:

```bash
uv run mypy .
uv run ruff check . --fix
uv run ruff check .
uv run ruff format .
uv run python -m pytest
```

If a tool is not installed, say so and continue with the available checks.

Coding conventions
Prefer simple, explicit Python.
Keep public functions typed.
Avoid broad except Exception unless justified.
Keep filesystem/network side effects isolated and testable.
Do not introduce new dependencies without explaining why.
Review guidelines

When reviewing, prioritize:

Correctness bugs
Data-loss risks
Cross-platform path issues
Race conditions and file-watcher edge cases
Security issues
Poor error handling
Missing tests around changed behavior
Packaging / installation problems
Hidden assumptions about macOS, Windows, Linux, Dropbox, symlinks, or filesystem metadata

For every finding, include:

Severity: critical / high / medium / low
File and function
Why it matters
Minimal suggested fix
Whether a test should be added
# Historical gotchas

Lessons-learned and retrospective notes that lived in `AGENTS.md`'s Gotchas section but no longer pull their weight in the always-loaded context. Kept here because the incidents and reasoning are still useful when they recur or when reading old PRs / commit history.

**This file is NOT `@`-imported by `CLAUDE.md` / `AGENTS.md`.** Reach for it manually when the situations below come up.

## pathspec internals (rarely needed)

- pathspec 1.0.4: `spec.check_file(path)` returns `CheckResult(include, index, file)` — use when you need pattern-level verdicts beyond a bare bool. Currently unused in the codebase.
- pathspec: `pattern.match_file()` is public; `pattern.regex.match` is private API. Stick to the public surface.

## Test fixtures + patterns (covered by their own files)

- `FakeMarkers` (`tests/conftest.py:93`) calls `path.resolve()` inside `is_ignored` / `set_ignored` / `clear_ignored` before recording the argument (lines 106 / 110 / 115). For symlink-correctness tests that need to distinguish "CLI passed the link object" vs "CLI passed the resolved target," use a raw-argument spy that monkeypatches `markers.{set,clear,is}_ignored` at the module level — see the `raw_marker_spy` fixture in `tests/test_cli_symlink_path_args.py`.
- Log-contract tests use `caplog.at_level(logging.WARNING, logger="dbxignore.<module>")` — narrow to the submodule that emits the log (see `tests/test_reconcile_edges.py`).

## macOS sync-mode detection — design rationale

- **Sync-mode detection is path-primary, pluginkit-disambiguating.** PluginKit registration is a system-level fact ("does macOS know about the appex?"); the user-level fact ("which mode is *this account* in?") lives in info.json. v0.4.0a4 conflated the two and misdetected users who had Dropbox.app installed but had declined the File Provider migration; a5 split the signals. The current logic, truth table, and dual-attribute behavior live in `src/dbxignore/_backends/macos_xattr.py`'s module docstring.

## mypy + typing edge cases

- `mypy.ini` in the repo root (or `~/.mypy.ini`) silently overrides `pyproject.toml`'s `[tool.mypy]` block — mypy's config-file precedence is `mypy.ini` > `.mypy.ini` > `pyproject.toml`. Project config lives in `pyproject.toml`; `mypy.ini` is `.gitignored` for local scratch. If a local `mypy.ini` ever appears with a `[mypy]` section, the strict block in pyproject.toml stops applying and mypy reports a small subset of the real errors.
- `uv run mypy .` (project's canonical invocation per `## How to run checks`) reports pre-existing errors on test files that do `from dbxignore import <submodule>`: `tests/conftest.py:9` shows `[attr-defined]` on `cli`/`reconcile` because strict mypy + `no_implicit_reexport=true` rejects the pattern when `__init__.py` doesn't explicitly re-export submodules. File-scoped invocation (`uv run mypy tests/conftest.py`) instead reports `[import-untyped]` ("Skipping analyzing 'dbxignore': missing library stubs or py.typed marker") because the package is treated as an external import without `py.typed`. Both layers are real; fixing one doesn't silence the other. Full fix requires adding `src/dbxignore/py.typed` AND `from . import cli, daemon, install, markers, reconcile, roots, rules, state` to `__init__.py` (deferred — separate PR). PRs work around with `--no-verify` after scoped `mypy <PR-touched-files>` passes.
- `pytest.skip(...)` is typed as `NoReturn`, but mypy strict under host=darwin doesn't always pick up the flow narrowing when skip is the tail of an `if/elif/else` chain — variables bound in the if/elif arms are flagged "not defined" at use sites that follow the chain. Workaround: skip up front (`if sys.platform not in supported: pytest.skip(...)`), then use a 2-arm `if/else` for the supported platforms.

## Windows watchdog mystery (closed)

- **Watchdog `Observer` / `ReadDirectoryChangesW` events are occasionally silently dropped on Windows CI runners** — not delayed, *missing entirely*. PR #135's DEBUG-level instrumentation captured a trace in PR #136 showing the initial sweep + observer-start logs, then zero `on_any_event` records for a 5-second window during which the test wrote files that should have triggered events. This rules out all of #34's H1–H7 hypotheses (latency, debouncer starvation, AV scanning, lock contention, fast-path race, stale-observer interference) and rules out timeout-widening as a fix shape. PR #139 retired the multi-event Windows-only smoke test in favor of `tests/test_daemon_synthetic_events.py`, which fires stub events directly into `daemon._dispatch` against a real `RuleCache` + `FakeMarkers` — deterministic, cross-platform, and covers the same rule-load + reconcile + conflict-detector chain. `tests/test_daemon_smoke.py` survives as a single-event Windows-only canary with a 10s budget; if it ever flakes again, the documented response is to delete it (further widening is provably useless against H8).

## BACKLOG.md merge-conflict resolution

- When an item resolves on `main` while a topic branch still lists it under Open, `git merge` flags the surrounding paragraphs. Resolve by *dropping* the item from HEAD's open list — but first `git show origin/main:BACKLOG.md | awk '/^### Resolved/{on=1; next} /^### /{on=0} on' | grep '#<N>'` to confirm the item appears under `### Resolved` on main (not just in the Open list or Provenance notes). Without that check, a HEAD-wins resolution silently un-resolves the item.

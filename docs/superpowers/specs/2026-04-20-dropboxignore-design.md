# dropboxignore — Design

- **Date:** 2026-04-20
- **Author:** Kilo Scheffer
- **Status:** Approved, ready for implementation plan
- **Target platform:** Windows (Dropbox for Windows, NTFS)

## Summary

`dropboxignore` is a Python utility that adds `.gitignore`-style hierarchical ignore rules to Dropbox on Windows. It discovers `.dropboxignore` files anywhere under each configured Dropbox root and keeps the NTFS alternate data stream `com.dropbox.ignored` in sync with those rules so Dropbox stops syncing matching paths. It runs as a per-user background daemon (Task Scheduler at logon) that combines a `watchdog`-based file-system watcher with an hourly safety-net sweep. The same reconcile logic backs a manual `apply` CLI command.

## Motivation

Dropbox's built-in "ignore" feature works by setting an NTFS alternate data stream (`com.dropbox.ignored = 1`) on a path, but there is no declarative, file-based way to manage it — you have to right-click each path in Explorer or run a PowerShell `Set-Content -Stream` by hand. For developers who keep source trees under Dropbox, that means manually ignoring every `node_modules/`, `.venv/`, `target/`, `dist/`, etc., in every project, on every machine.

`.gitignore` solves the analogous problem for git. `.dropboxignore` should solve it for Dropbox: a text file checked into each project that declares what should not be synced, applied automatically wherever the project is cloned.

## Goals

1. **Hierarchical `.dropboxignore` files.** Any directory under a Dropbox root can contain a `.dropboxignore`; rules apply relative to that file's directory, same semantics as `.gitignore`.
2. **Declarative source of truth.** The `.dropboxignore` files *are* the spec. Removing a rule unignores the matching paths on the next reconcile; removing a `.dropboxignore` unignores its whole subtree.
3. **Low-latency reaction.** Edits to `.dropboxignore` files and creation of ignorable directories (`node_modules/`, `.venv/`, …) are reacted to within a second.
4. **Self-healing.** If the watcher crashes, misses events, or was offline, an hourly sweep reconciles drift.
5. **Multi-root.** Handles both Dropbox Personal and Dropbox Business simultaneously, auto-discovered from `%APPDATA%\Dropbox\info.json`.
6. **Two install paths.** `uv tool install` for Python users (primary); PyInstaller-built single-file `.exe` for non-Python users (published as a GitHub Release asset).

## Non-goals

- **Non-Windows platforms.** NTFS alternate data streams are a Windows-only construct. The pure-Python parts (rule parsing, reconcile logic) happen to be portable and are tested on Linux, but the `ads` module and end-to-end behavior are Windows-only.
- **GUI.** CLI only.
- **Telemetry / crash reporting.** Logs to disk are sufficient.
- **Daemon-as-service.** Runs as the current user via Task Scheduler, not `LocalSystem`. Dropbox itself runs per-user; matching that lifecycle avoids permission and path-visibility complications.
- **Code signing.** Out of scope for v0.x. Users see the standard SmartScreen warning on first run of the unsigned `.exe`.

## Key design decisions

| # | Decision | Chosen | Rationale |
|---|---|---|---|
| 1 | Language | Python | `pathspec` provides correct gitignore semantics for free; `watchdog` handles FSWatcher quirks; `open()` can write ADS directly on Windows via `CreateFileW`'s `path:stream` syntax. |
| 2 | Rule discovery | Hierarchical — any `.dropboxignore` under a root | Matches `.gitignore` mental model; lets each repo self-describe what to ignore. |
| 3 | Unignore semantics | Source-of-truth reconcile | Removing a rule unignores. Symmetric, predictable, matches `.gitignore` mental model. |
| 4 | Trigger model | Hybrid: watcher + hourly sweep | Watcher for latency; sweep as safety net against missed events, crashes, overflow. |
| 5 | Hosting | Task Scheduler at user logon | Per-user, matches Dropbox's own lifecycle, avoids service-account complexity. |
| 6 | Root discovery | Auto from `%APPDATA%\Dropbox\info.json` | Dropbox's own documented integration point; handles Personal + Business automatically. |
| 7 | CLI surface | `daemon`, `apply`, `status`, `list`, `explain`, `install`, `uninstall` | Minimum useful set. `explain` makes pattern debugging tractable. |
| 8 | Packaging | `uv tool install` + PyInstaller `.exe` via GitHub Actions on tag | Two audiences: Python users get fast dev loop, others get a single-file download. |
| 9 | Build backend | `hatchling` + `hatch-vcs` | Declarative `pyproject.toml`; version derived from git tags; avoids hand-bumped version drift. |
| 10 | Debounce | Per-event-type, configurable | `.dropboxignore`: 100 ms. Directory create: 0 ms. Other: 500 ms. Matches cost asymmetry. |
| 11 | Daemon binary naming | `dropboxignored.exe` | Daemon; follows `dockerd` precedent. `dropboxignore.exe` is the console CLI. |

## Architecture

Single Python process. Seven modules, each with a single responsibility and a small, testable surface.

```
dropboxignore/
├── cli.py         Subcommand dispatch (click). Invokes other modules.
├── roots.py       Parse %APPDATA%\Dropbox\info.json → list of Dropbox root paths.
├── rules.py       Find .dropboxignore files, parse with pathspec, maintain rule cache.
├── ads.py         Read / write / clear com.dropbox.ignored on a path.
├── reconcile.py   "Make the filesystem match the rules" — shared by sweep, events, apply.
├── daemon.py      Watchdog observer + sweep timer + event dispatch.
├── install.py     Task Scheduler XML generation; schtasks invocation.
└── _version.py    Generated by hatch-vcs at build time (gitignored).
```

### Module interfaces

```python
# roots.py
def discover() -> list[Path]: ...

# ads.py
def is_ignored(path: Path) -> bool: ...
def set_ignored(path: Path) -> None: ...
def clear_ignored(path: Path) -> None: ...

# rules.py
class RuleCache:
    def load_root(self, root: Path) -> None: ...
    def reload_file(self, ignore_file: Path) -> None: ...
    def remove_file(self, ignore_file: Path) -> None: ...
    def match(self, path: Path) -> bool: ...
    def explain(self, path: Path) -> list[Match]: ...

# reconcile.py
@dataclass
class Report:
    marked: int
    cleared: int
    errors: list[tuple[Path, str]]
    duration_s: float

def reconcile_subtree(root: Path, subdir: Path, cache: RuleCache) -> Report: ...

# daemon.py
def run(stop_event: threading.Event | None = None) -> None: ...
def _dispatch(event: FileSystemEvent, cache: RuleCache, roots: list[Path]) -> None: ...  # pure
```

The keystone is `reconcile_subtree`. It is called by:
- `daemon._dispatch` with a narrow `subdir` (one event),
- the sweep timer with `subdir == root` (everything),
- the `apply` CLI command with a user-supplied `subdir`.

One implementation, three callers, three scopes — no way for the daemon and the CLI to silently disagree.

## Data flow

### Startup

```
1. roots.discover()                        → [C:\Dropbox, C:\Dropbox (Work)]
2. cache = RuleCache()
   for root in roots:
     cache.load_root(root)                 walks root, indexes every .dropboxignore
3. for root in roots:
     reconcile_subtree(root, root, cache)  initial full sweep (catches offline drift)
4. observer = watchdog.Observer()
   for root in roots:
     observer.schedule(handler, root, recursive=True)
   observer.start()
5. schedule sweep timer (hourly)
6. block on stop_event (SIGINT / SIGTERM / schtasks End)
```

### Watcher event dispatch

`watchdog` events are put on a `queue.Queue` for debouncing. A worker thread coalesces events per subtree and per kind, then calls `_dispatch`:

| Event | Debounce | Dispatch action |
|---|---|---|
| `.dropboxignore` created / modified | 100 ms | `cache.reload_file(path)`, `reconcile_subtree(root, parent, cache)` |
| `.dropboxignore` deleted | 100 ms | `cache.remove_file(path)`, `reconcile_subtree(root, parent, cache)` |
| `.dropboxignore` moved | 100 ms | remove at old path + reload at new path, reconcile both parents |
| Directory created | 0 ms | `reconcile_subtree(root, new_dir, cache)` |
| Other file/dir created / moved-in | 500 ms | `reconcile_subtree(root, enclosing_dir, cache)` |
| File/dir deleted | — | ignored (gone; nothing to reconcile) |
| File modified (non-`.dropboxignore`) | — | ignored (content ≠ identity) |

The three debounce windows are configurable via environment variables (`DROPBOXIGNORE_DEBOUNCE_RULES_MS`, `..._DIRS_MS`, `..._OTHER_MS`) and via a `[debounce]` section in a future config file.

### Sweep

```
for root in roots:
    cache.load_root(root)                  rebuild from disk
    reconcile_subtree(root, root, cache)
write state.json (timestamp, counts, errors)
schedule next timer
```

Triggered by: the internal hourly timer; `apply` CLI without a `PATH` argument; startup (step 3 above); a watcher buffer overflow event.

### CLI command flows

| Command | Implementation |
|---|---|
| `dropboxignore daemon` (or bare `dropboxignored`) | Start observer + timer, block until stop signal. |
| `apply [PATH]` | Load cache for the root containing `PATH` (or all roots); `reconcile_subtree(root, PATH or root, cache)`; print report. |
| `status` | Read `state.json`; liveness-check `daemon_pid`; print human-readable summary including last sweep counts and last error. |
| `list [PATH]` | Walk `PATH` (or all roots), collect paths where `ads.is_ignored(p)`, print one per line. Short-circuits into ignored subtrees. |
| `explain PATH` | Load cache; call `cache.explain(PATH)`; print matching `.dropboxignore` file + line + pattern, or "no match." |
| `install` | Generate Task Scheduler XML (logon trigger, runs `dropboxignored` or `pythonw -m dropboxignore daemon`); `schtasks /Create /XML <tmp> /TN dropboxignore /F`. |
| `uninstall [--purge]` | `schtasks /Delete /TN dropboxignore /F`. With `--purge`, also clear all `com.dropbox.ignored` ADS markers under every discovered root. |

## Semantics & edge cases

### Pattern syntax

Full `.gitignore` grammar via `pathspec.GitIgnoreSpec`: comments (`#`), negation (`!`), directory-only (`/`), anchored (`/foo`), `**`, escape sequences. Anchored to the containing `.dropboxignore`'s directory, not to the Dropbox root.

### Case sensitivity

Windows NTFS is case-insensitive; `pathspec` defaults to case-sensitive. We wrap `GitIgnoreSpec` to compile with `re.IGNORECASE` so a rule of `node_modules` matches a directory named `Node_Modules`.

### Hierarchical matching

A `.dropboxignore` deeper in the tree adds rules scoped to its directory. Both parent and child rules apply, with children able to negate parent matches. `RuleCache.match(path)`:

1. Walk from the containing Dropbox root toward `path`.
2. For each directory along the way, if a `.dropboxignore` exists, apply its rules against the path expressed relative to that directory.
3. Accumulate matches; later (deeper) rules can negate earlier matches via `!pattern`.
4. Final result: True iff an odd number of matches, with the last being a non-negation — equivalent to standard gitignore precedence.

### `.dropboxignore` is never itself ignored

We explicitly exclude paths named `.dropboxignore` from ever being marked ignored, even if an ancestor pattern would match. The file must sync to other machines for the rules to be shared. Violations are logged at `WARNING` on every reconcile and continue to be overridden.

### Conflict with Dropbox's built-in "Ignore" menu

The reconcile model means paths marked ignored via Explorer's Dropbox menu that don't match any `.dropboxignore` rule will be **unignored** on the next sweep. This is documented and expected — the `.dropboxignore` files are declared as the source of truth. A `--respect-manual` flag (off by default) is reserved for users who want manual marks preserved; enabling it makes the tool additive-only (never clears an ADS marker).

### Symlinks and junctions

Not followed. `watchdog` is configured with default (non-following) behavior. Reconcile walks use `os.walk(followlinks=False)`. A symlink path can itself be marked ignored if a rule matches its name.

### Long paths

All ADS writes use the `\\?\` UNC prefix internally so paths over 260 characters work without needing the user to enable long-path support in the registry.

### Failure modes inside a pass

- **`FileNotFoundError`** (path vanished between walk and write): log at `DEBUG`, continue.
- **`PermissionError`** (file in use, ACL): log at `WARNING`, increment `Report.errors`, continue. Next sweep retries.
- **Invalid `.dropboxignore`**: keep the previously parsed version in cache (or treat as empty if none); log at `WARNING` with file path and failing line number.
- **Watcher buffer overflow** (`watchdog` surfaces this as an `overflow` flag on some event dispatchers): trigger an immediate sweep of the affected root; log at `WARNING`.

### Descent into already-ignored directories

`reconcile_subtree` does not descend into directories that currently bear `com.dropbox.ignored`. Once a directory is marked, Dropbox stops syncing its contents, and re-marking every descendant adds no value. This keeps sweep cost O(visible directories) rather than O(all files).

Exception: if a directory bears the ADS marker but *no current rule* matches it, we clear the marker (and *do* descend on the next pass) — required for correctness under the reconcile model.

## Error handling & logging

### Log destination

`%LOCALAPPDATA%\dropboxignore\daemon.log` via `RotatingFileHandler` (5 MB × 5 files). Daemon writes there only. CLI commands also write to stderr so interactive users see warnings without tailing the log.

### Levels

| Level | Content |
|---|---|
| `DEBUG` | Every event, every match decision, every ADS read/write. Off by default; `--verbose` on CLI, `DROPBOXIGNORE_LOG_LEVEL=DEBUG` env var for daemon. |
| `INFO` | Startup / shutdown, roots discovered, rule files loaded, per-sweep summary. |
| `WARNING` | Per-path permission denied, invalid `.dropboxignore`, buffer overflow, `.dropboxignore` overridden back to synced. |
| `ERROR` | Reconcile pass crashed entirely, root became inaccessible, state file corrupted. |
| `CRITICAL` | Daemon aborting — observer thread died, unhandled exception in main loop. |

### Error scope partitioning

Errors are caught at the narrowest useful scope:

- **Per-path** inside `reconcile_subtree` — one bad path never aborts a pass.
- **Per-rule-file** inside `rules.reload_file` — one bad `.dropboxignore` doesn't take down the cache.
- **Per-event** inside the event handler thread — wrapped in try/except; the sweep is the safety net.
- **Per-sweep** inside the timer callback — a crashed sweep logs and reschedules; a future sweep will retry.
- **Per-daemon** at the top level — unhandled exceptions log `CRITICAL` and exit non-zero. Task Scheduler's `RestartOnFailure` setting brings the daemon back.

### State file

`%LOCALAPPDATA%\dropboxignore\state.json`, written after every sweep and on shutdown. Schema (version 1):

```json
{
  "schema": 1,
  "daemon_pid": 12345,
  "daemon_started": "2026-04-20T09:14:00+02:00",
  "last_sweep": "2026-04-20T10:14:03+02:00",
  "last_sweep_duration_s": 1.8,
  "last_sweep_marked": 127,
  "last_sweep_cleared": 3,
  "last_sweep_errors": 2,
  "last_error": {
    "time": "2026-04-20T10:14:02+02:00",
    "path": "C:\\Dropbox\\proj\\locked.dll",
    "message": "PermissionError: file in use"
  },
  "watched_roots": ["C:\\Dropbox", "C:\\Dropbox (Work)"]
}
```

### PID singleton

Startup refuses to run if `state.json` lists a `daemon_pid` that (a) is still alive and (b) is a Python process (verified via `psutil.Process().name()`). Prints a clear error pointing at the running PID.

### Deliberate non-features

- No retry / backoff on individual ADS writes. Failures are either permanent or get retried by the next sweep.
- No telemetry, no crash reporting. Logs are local-only.

## Testing strategy

### Tier 1 — Pure unit tests (every PR, Windows + Ubuntu, fast)

No filesystem side effects beyond `tmp_path`. `ads` module monkey-patched to an in-memory fake.

| Module | Coverage focus |
|---|---|
| `roots` | Fixture `info.json` files: personal-only, personal+business, missing, malformed. |
| `rules` | Hierarchical trees, negation across levels, case-insensitive matching, anchored vs unanchored, reload semantics. |
| `reconcile` | Scenarios from "Semantics & edge cases" — one test per: new match sets ADS, removed rule clears ADS, nested negation, `.dropboxignore` protected, ancestor-already-ignored short-circuit, vanishing paths, permission errors. |
| `cli` | `click.testing.CliRunner` for every subcommand; assert exit codes, stdout, state-file effects. |
| `daemon._dispatch` | Fabricated `watchdog` event objects; assert correct cache / reconcile calls. |

### Tier 2 — Windows integration tests (Windows CI, every PR)

Marked `@pytest.mark.windows_only`. Touch real NTFS ADS.

- `ads` roundtrip on file and on directory.
- Path > 260 chars (verifies `\\?\` prefix).
- File-in-use → `PermissionError` → daemon does not crash.
- Full reconcile against a real fixture tree: real `.dropboxignore` files, real files, no mocks on `ads`.

### Tier 3 — End-to-end daemon smoke test (Windows CI, on tag or nightly)

One test that drives the actual daemon loop against `tmp_path`: start daemon thread → write a `.dropboxignore` → create matching directory → poll-until `ads.is_ignored` True (timeout 2 s) → append negation → create exempted child → poll-until False → stop.

### Not in scope

- Hypothesis / property-based tests (would be testing `pathspec` rather than our code).
- Coverage gates (lagging indicator).
- Linux ADS tests (not applicable).

## Repo layout

```
dropboxignore/
├── pyproject.toml
├── README.md
├── LICENSE                            (MIT unless decided otherwise)
├── .gitignore                         includes src/dropboxignore/_version.py
├── .github/
│   └── workflows/
│       ├── test.yml
│       └── release.yml
├── src/
│   └── dropboxignore/
│       ├── __init__.py
│       ├── __main__.py                python -m dropboxignore → cli.main()
│       ├── cli.py
│       ├── roots.py
│       ├── rules.py
│       ├── ads.py
│       ├── reconcile.py
│       ├── daemon.py
│       ├── install.py
│       └── _version.py                hatch-vcs generated, gitignored
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   ├── info.json.personal
│   │   ├── info.json.personal_business
│   │   └── rule_trees/
│   ├── test_roots.py
│   ├── test_rules.py
│   ├── test_reconcile.py
│   ├── test_cli.py
│   ├── test_daemon_dispatch.py
│   ├── test_ads_integration.py
│   └── test_daemon_smoke.py
├── pyinstaller/
│   └── dropboxignore.spec
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-04-20-dropboxignore-design.md
```

`src/` layout (not flat) prevents accidental imports of the working tree instead of the installed package during test runs.

## Packaging

### `pyproject.toml` shape

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[project]
name = "dropboxignore"
description = "Hierarchical .dropboxignore for Dropbox on Windows via NTFS alternate data streams"
requires-python = ">=3.11"
dynamic = ["version"]
dependencies = [
    "watchdog>=4.0",
    "pathspec>=0.12",
    "click>=8.1",
    "psutil>=5.9",
]
[project.optional-dependencies]
dev = ["pytest>=8", "pytest-timeout", "ruff"]

[project.scripts]
dropboxignore = "dropboxignore.cli:main"

[tool.hatch.version]
source = "vcs"
[tool.hatch.build.hooks.vcs]
version-file = "src/dropboxignore/_version.py"

[tool.pytest.ini_options]
markers = ["windows_only: tests requiring NTFS ADS"]
timeout = 10

[tool.ruff]
line-length = 100
target-version = "py311"
```

### PyInstaller

One spec file produces two binaries:

- `dropboxignore.exe` — console mode; what users invoke interactively for `apply`, `status`, `list`, `explain`, `install`, `uninstall`.
- `dropboxignored.exe` — no-console mode (daemon); what Task Scheduler invokes.

Both bootstrap the same `dropboxignore.cli:main`. `dropboxignored.exe` defaults its argv to `["dropboxignored", "daemon"]` when invoked without args, so the scheduled task command is simply `dropboxignored.exe`.

### Install paths

- **From source (primary):** `uv tool install .` from a checkout. Uv creates an isolated venv and puts a shim on `PATH`. Task Scheduler invokes `%LOCALAPPDATA%\uv\tools\dropboxignore\Scripts\pythonw.exe -m dropboxignore daemon`.
- **From GitHub Release (secondary):** download `dropboxignored.exe` + `dropboxignore.exe` from the latest release, place in a stable directory (e.g. `%LOCALAPPDATA%\dropboxignore\bin\`), run `dropboxignore.exe install`. No Python required on the target machine.

The `install` subcommand auto-detects which mode it's running under (via `sys.frozen`) and generates the correct action in the Task Scheduler XML.

## CI/CD

Two GitHub Actions workflows.

### `.github/workflows/test.yml`

Triggers on `push` to any branch and on `pull_request`.

```yaml
on: [push, pull_request]

jobs:
  test:
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-extras
      - run: uv run ruff check
      - run: uv run pytest -m "not windows_only"
      - if: runner.os == 'Windows'
        run: uv run pytest -m windows_only
```

`fetch-depth: 0` is required so `hatch-vcs` can see git tags and resolve a real version. Without it, builds report `0.0.0`.

### `.github/workflows/release.yml`

Triggers only on tags matching `v*`.

```yaml
on:
  push:
    tags: ['v*']

jobs:
  build:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-extras
      - run: uv build
      - run: uv run pyinstaller pyinstaller/dropboxignore.spec
      - uses: softprops/action-gh-release@v2
        with:
          files: |
            dist/*.whl
            dist/*.tar.gz
            dist/dropboxignore.exe
            dist/dropboxignored.exe
          generate_release_notes: true
```

Release notes are auto-generated from merged PR titles / commits between tags.

### Release flow

```
1. git tag v0.2.0
2. git push --tags
3. CI builds on Windows, produces wheel + sdist + two exes
4. GitHub Release v0.2.0 is created automatically with all four artifacts attached
5. Users either `uv tool upgrade dropboxignore` or download the exe
```

## Open questions / risks

- **Unsigned PyInstaller binary.** SmartScreen will warn on first run. Acceptable for v0.x; code-signing can be added later if the tool gets distribution beyond personal use.
- **Task Scheduler "missed run" behavior** when the machine is asleep at logon-trigger time. Default Task Scheduler behavior is to run as soon as the machine wakes, which is correct for us — verify in the generated XML.
- **Watcher on Dropbox-mounted paths.** If the Dropbox client is resyncing a large backlog, `watchdog` may see a burst of rename/write events. Debouncing plus the idempotence of `reconcile_subtree` should absorb this, but real-world observation needed.
- **First run UX.** On a fresh install with a large `node_modules/` already present, the initial sweep may take tens of seconds and drive disk I/O. Consider a `--initial-quiet` flag that logs-only on first sweep, letting the user review what would be marked before running a real pass. (Not in v0.1; good v0.2 idea.)
- **Multi-user on shared machine.** The daemon is per-user. On a shared PC, each user who uses Dropbox needs their own install. The Task Scheduler entry is per-user by design.

## Appendix — Glossary

- **ADS**: NTFS Alternate Data Stream. A named byte stream attached to a file/directory in addition to its primary data. Referenced as `path:streamname`.
- **`com.dropbox.ignored`**: the specific ADS name Dropbox reads; value `1` (any non-empty content appears to work) tells Dropbox not to sync the path.
- **Sweep**: a full walk-and-reconcile pass over a Dropbox root.
- **Reconcile**: comparing current ADS state to rule-implied desired state and making the former match the latter.
- **Rule cache**: in-memory `{.dropboxignore file → parsed PathSpec}` map, rebuilt on sweep and incrementally updated by watcher events.

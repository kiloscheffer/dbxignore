# Daemon ready-before-sweep — design spec

**Item:** BACKLOG #53 (candidate 1)
**Date:** 2026-05-08
**Status:** Approved (awaiting writing-plans + implementation)

## Problem

`dbxignored.run()` performs the initial reconcile sweep before any user-visible readiness signal becomes true: the watchdog observer is not yet running, `state.json` does not yet exist, and the "watching roots" log line has not yet been written. On a fresh install of a 27,000-directory Dropbox tree the initial sweep took 49.62s wall-clock (Ubuntu 24.04, journalctl 2026-05-03), so during that window:

- Manual-test scripts polling `daemon.log` for "watching roots" time out at default thresholds (`scripts/manual-test-ubuntu-vps.sh` had to be widened from 30s to 180s).
- `dbxignore status` reports `state=no_state` because `state.json` does not yet exist.
- Watchdog events that happen during the initial-sweep window are dropped — the observer is not yet listening.

The wall-clock cost of the first sweep itself (the 49.62s) is fundamental: every directory has to be visited at least once before subsequent sweeps can prune already-marked subtrees. That cost is **not** what this design addresses. What this design addresses is *when the daemon becomes ready relative to that cost*.

## Goals

After this change:

- The watchdog observer starts capturing events ~1s after `dbxignored` launch, regardless of tree size.
- `state.json` exists ~1s after launch with a new `state=starting` token, transitioning to `state=running` once the worker thread completes the initial sweep.
- `dbxignore status` distinguishes "alive but initial sweep not yet done" from "alive and swept."
- Watchdog events arriving during the initial-sweep window are processed normally (debouncer + `_dispatch` + `reconcile_subtree`), not dropped.
- Wall-clock for the initial sweep itself is unchanged (still ~50s on a 27k-dir tree).
- Cooperative cancellation: `dbxignored` shutting down mid-sweep returns within ~1s rather than waiting for the sweep to complete.

## Non-goals

- Reducing the wall-clock cost of the initial sweep itself. That requires an algorithmic change (parallel walks, persisted hint, etc.) — separate candidates in the same BACKLOG item.
- Changing reconcile semantics. Convergence under concurrent watchdog + worker activity is already provided by the design in CLAUDE.md ("reconcile reads the cache lock-free... writes per-file ignore markers on disjoint paths"); we rely on it without modification.
- Changing `cli.apply` behavior. `apply` is one-shot and runs to completion; it stays unaware of the new cancellation API.

## Design

### Architecture: `daemon.run()` reordering

**Today** (sequential, blocking):

```
acquire singleton lock
discover + resolve roots
load rule cache
INITIAL SWEEP (50s)              ← writes state.json at end
build observer + debouncer
start observer + debouncer
log "watching roots"
hourly sweep loop
```

**After this change** (observer-first, sweep in worker):

```
acquire singleton lock
discover + resolve roots
load rule cache
build observer + debouncer
start observer + debouncer
log "watching roots"
EARLY state.write()              ← writes state.json with state=starting
spawn worker thread:
    initial sweep (~50s)         ← writes state.json with state=running on success
    on failure: log + stop_event.set()
main thread: hourly sweep loop (waits SWEEP_INTERVAL_S between sweeps)
```

The hourly-sweep loop in the main thread runs `_sweep_once` directly (unchanged from today). Only the *initial* sweep moves to a worker thread.

### Worker thread shape

```python
def _initial_sweep_worker(
    roots: list[Path],
    cache: RuleCache,
    daemon_started: dt.datetime,
    daemon_create_time: float | None,
    stop_event: threading.Event,
) -> None:
    try:
        _sweep_once(
            roots, cache, daemon_started, daemon_create_time,
            stop_event=stop_event,
        )
    except Exception:
        logger.exception("initial sweep worker failed; shutting daemon down")
        stop_event.set()
```

Key shape choices:

- `Exception` (not `BaseException`) — `KeyboardInterrupt` / `SystemExit` propagate normally; the signal handler already routes those through `stop_event`.
- `logger.exception` (not `logger.error`) so the full traceback lands in `daemon.log`.
- `stop_event.set()` on failure triggers main-thread shutdown via the existing `wait()` loop in `run()`.
- `Thread(daemon=False)` — main thread `worker.join()`s on shutdown so cancellation is observed cleanly.

Main-thread shutdown sequence becomes:

```python
# After stop_event is set (signal handler, hourly-loop normal exit, or worker failure):
observer.stop(); observer.join()
debouncer.stop()
worker.join()              # bounded by stop_event check rate in reconcile_subtree
logger.info("daemon stopped")
```

`singleton_lock.close()` runs in the existing outer `try/finally`.

### Cooperative cancellation in `reconcile_subtree`

Add a keyword-only optional parameter:

```python
def reconcile_subtree(
    root: Path, subdir: Path, cache: RuleCache, *,
    dry_run: bool = False,
    stop_event: threading.Event | None = None,
) -> Report:
```

Two check points inside:

1. Top of the `os.walk` loop, before processing each directory.
2. Inside the `filenames` loop, before each `_reconcile_path` call.

Pseudocode (for illustration; final shape may differ slightly):

```python
for current, dirnames, filenames in os.walk(subdir, followlinks=False):
    if stop_event is not None and stop_event.is_set():
        break
    current_path = Path(current)
    dirnames[:] = [
        name for name in dirnames
        if not _reconcile_path(current_path / name, cache, report, dry_run=dry_run)
    ]
    for name in filenames:
        if stop_event is not None and stop_event.is_set():
            break
        _reconcile_path(current_path / name, cache, report, dry_run=dry_run)
```

The `_reconcile_path(subdir, ...)` call at the very top of `reconcile_subtree` (before the walk) stays unchecked — it's a single `_reconcile_path` invocation, cheaper to let it finish than to gate it.

`Report` returned on cancellation has accurate counts for what completed before the break. The next sweep finishes the rest (convergence).

**Cost:** `Event.is_set()` is a non-blocking attribute read (~200ns). 27k dirs × ~10 files = 270k checks ≈ 54ms total overhead — invisible at sweep scale.

**Caller updates:**

- `daemon._sweep_once` grows a keyword-only `stop_event` parameter that it forwards to `reconcile_subtree`. Both the worker call and the hourly-loop call in main thread pass the shared `stop_event`.
- `cli.apply` keeps `stop_event=None` (default). Apply is a one-shot user command; cancellation isn't desired.
- `daemon._dispatch` (watchdog event handler) keeps `stop_event=None`. Per-event reconcile work is short and bounded.
- The `reconcile_subtree` signature change is backward-compatible: the new parameter is keyword-only with a `None` default.

### `state.json` and `--summary` contract

Four token values consumers can observe (one new):

| Token | Means | When |
|---|---|---|
| `state=starting` (new) | Lock acquired, observer up, but initial sweep not yet complete | Between the early `state.write()` and the worker's first successful `_sweep_once` |
| `state=running` | Lock acquired, observer up, *and* at least one sweep complete | After the worker writes the post-sweep state.json |
| `state=not_running` | `state.json` exists but the recorded daemon is dead | Daemon crashed/killed; state is stale |
| `state=no_state` | No `state.json` at all | Daemon never ran |

`--summary` field shape during `state=starting`:

```
state=starting pid=12345
```

Just `state` and `pid`. No `marked`, `cleared`, `errors`, or `conflicts` — those would all be 0 and would mislead consumers into reading "swept and found nothing."

`--summary` during `state=running` is unchanged:

```
state=running pid=12345 marked=7 cleared=1 errors=0 conflicts=0
```

**Internal mechanism:** the early `state.write()` writes a `State` instance with `last_sweep=None` and the existing `daemon_pid` / `daemon_create_time` / `daemon_started` / `watched_roots` populated. `_format_summary` (in `cli.py`) checks `state_obj.last_sweep is None` to decide between `starting` and `running`. The schema does not need a new field.

**Error handling for the early write:** if the early `state.write()` raises `OSError` (disk full, permission denied, etc.), log a WARNING and continue — the worker still runs and will retry the write at end of the initial sweep. Mirrors the existing `_sweep_once` behavior at `daemon.py:733`.

The `cli.status` human path also distinguishes the two states and prints "daemon: starting (initial sweep in progress)" when `last_sweep is None`.

### SemVer impact

Adding `state=starting` as a possible token value in the `--summary` format is a **pre-1.0 breaking change** to the public API per the `_format_summary` docstring's contract ("Field additions are non-breaking; removals or renames bump MINOR pre-1.0"). Adding a new *value* (rather than a new *field*) can break consumers that branch on `state == "running"` or `state == "not_running"` exhaustively. Requires:

- `**Breaking**` callout in `CHANGELOG.md` under `[Unreleased]`.
- README `## Status-bar integration` section updated to document the new token + the field-omission shape during starting.
- MINOR version bump on the next release tag.

## Public API additions

- `reconcile.reconcile_subtree(...)` gains `stop_event: threading.Event | None = None` keyword-only parameter.
- `daemon._sweep_once(...)` gains the same parameter (forwarded to reconcile).
- `state.State` is unchanged — `last_sweep is None` is the canonical signal for the new starting state.
- `--summary` recognizes `state=starting` as a possible value.

## Testing strategy

Five new tests in `tests/test_daemon_initial_sweep.py` (new file):

1. **`test_state_json_appears_before_sweep_completes`** — start daemon thread with a `BlockingMarkers` gate closed; poll for `state.json` to appear (bounded loop, < 5s); assert `state=starting` semantics (`last_sweep is None`, `daemon_pid` set, `watched_roots` populated). Open the gate; poll for `state=running` (bounded). Assert ordering.

2. **`test_observer_up_before_initial_sweep_completes`** — same setup; while the gate is closed, fire a synthetic watchdog event via the existing `stub_event` helper from `tests/conftest.py`; assert it gets dispatched (debouncer drains it, reconcile_subtree fires on the affected subdir). Confirms watchdog events work during the starting window.

3. **`test_worker_failure_shuts_down_daemon`** — monkeypatch `reconcile_subtree` to raise `RuntimeError` on first call. Start daemon. Assert it exits within ~1s; assert ERROR logged with traceback; assert `stop_event` was set; assert lock was released.

4. **`test_cooperative_shutdown_during_initial_sweep`** — start daemon with the gate closed; immediately set `stop_event`; assert daemon exits within ~1s (NOT the full sweep duration). Verify the worker's `reconcile_subtree` saw `stop_event` and broke out of its walk.

5. **`test_status_starting_token_format`** — unit test on `_format_summary` directly: build a `State` with `last_sweep=None` and `daemon_pid=12345`; assert output is exactly `state=starting pid=12345` (no extra fields).

**Test seam.** A `BlockingMarkers` subclass of the existing `FakeMarkers` (in `tests/conftest.py`) that gates `is_ignored` on a `threading.Event`:

```python
class BlockingMarkers(FakeMarkers):
    def __init__(self, gate: threading.Event) -> None:
        super().__init__()
        self._gate = gate

    def is_ignored(self, path: Path) -> bool:
        self._gate.wait(timeout=10.0)  # bounded so test failures don't hang CI
        return super().is_ignored(path)
```

The 10s timeout ensures a broken test fails fast with a clear "gate never opened" error rather than hanging the full pytest timeout.

The existing `is_ignored_calls` list (added in PR #157) provides extra observability if needed.

## Documentation updates

**`CLAUDE.md`** — two new entries:

1. **Architecture paragraph addendum**: note that the daemon's observer starts before the initial sweep completes, and that watchdog events arriving during the starting window are processed normally (convergent with the worker's later sweep over the same paths).
2. **Gotcha bullet**: `last_sweep is None` is the canonical "initial sweep pending" signal in `state.json`. Consumers should branch on that rather than assuming `state.json` exists ⇒ swept.

**`README.md`** — `## Status-bar integration` section updated to:

- List `state=starting` alongside `running`/`not_running`/`no_state`.
- Document the field-omission shape during `starting` (just `state` + `pid`).
- Note that the transition from `starting` to `running` reflects initial-sweep completion.

**`CHANGELOG.md`** — `[Unreleased]` gets a `**Breaking**` callout describing the new token + field-omission shape.

## Risks and open questions

- **Manual-test poll**: `scripts/manual-test-{ubuntu-vps,macos}.sh` polls `daemon.log` for "watching roots" today. After this change that line appears almost immediately, so the 180s widening from 2026-05-03 becomes overkill. Lowering the timeout could be a follow-up — but harmless to leave as-is.
- **systemd Type=simple unaffected**: the systemd unit's `Type=simple` already returns immediately; the `[Service]` section requires no changes. Documented for completeness.
- **psutil availability**: `daemon_create_time` capture stays in its existing position (after lock acquire, before observer build). No interaction with the worker thread changes.
- **Log line ordering**: the "sync mode detection" INFO log on macOS still fires before the early `state.write()`, preserving the existing diagnostic ordering for fresh-install tests.

## What this design does *not* attempt

- **No new `state.json` fields.** The new `state=starting` token is derived from existing schema (`last_sweep is None`).
- **No interruption of in-flight `reconcile._reconcile_path` calls.** Each per-path operation runs to completion (already atomic at the OS level for marker writes); cancellation happens at file/directory boundaries.
- **No changes to `cli.apply`.** Apply remains a one-shot blocking command.
- **No follow-up perf work.** Worker fan-out and persisted-sweep-hint candidates from BACKLOG #53 are deliberately out of scope.

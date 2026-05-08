# #53 ready-before-sweep — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the ~50s window after `dbxignored` startup where the watchdog observer is offline and `state.json` does not exist, by moving the initial sweep to a worker thread and writing `state.json` early with a new `state=starting` token.

**Architecture:** `daemon.run()` reorders to start the observer + write `state.json` *before* the initial sweep, then spawns a `threading.Thread` worker for the sweep itself. `reconcile_subtree` gains a keyword-only `stop_event` parameter the worker passes through, providing cooperative cancellation at file/directory boundaries. `cli.status --summary`'s output gains a new `state=starting` token (alive but `last_sweep is None`).

**Spec:** `docs/superpowers/specs/2026-05-08-53-ready-before-sweep-design.md` — read it before starting; this plan operationalizes that design.

**Tech Stack:** Python 3.11+, `threading`, `watchdog`, `pathspec`, `pytest`, `uv` for env management. Existing fixtures: `FakeMarkers` + `fake_markers` + `write_file` + `stub_event` in `tests/conftest.py`.

---

## File structure

**Create:**
- `tests/test_daemon_initial_sweep.py` — four daemon-thread integration tests + uses the new `BlockingMarkers` helper.

**Modify:**
- `tests/conftest.py` — add `BlockingMarkers` class.
- `src/dbxignore/reconcile.py` — `reconcile_subtree` gains `stop_event` keyword-only parameter; two check points inside the `os.walk` loop.
- `src/dbxignore/daemon.py` — `_sweep_once` forwards `stop_event` to reconcile; `run()` reorders to observer-first; new `_initial_sweep_worker` function; main-thread shutdown sequence joins the worker.
- `src/dbxignore/cli.py` — `_format_summary` recognizes `state=starting` (returns truncated form when `last_sweep is None`); `status` human path prints "daemon: starting (initial sweep in progress)" in the same case.
- `tests/test_reconcile_basic.py` — one new test for cooperative cancellation.
- `tests/test_daemon_sweep.py` — one new test for `_sweep_once` forwarding `stop_event`.
- `tests/test_cli_status_list_explain.py` — two new unit tests for `_format_summary` and the human `status` path during starting.
- `CLAUDE.md` — architecture-paragraph addendum + new gotcha bullet.
- `README.md` — `## Status-bar integration` section gains the new token + field-omission shape.
- `CHANGELOG.md` — `[Unreleased]` Breaking entry.

**Total estimated change:** ~50 LOC of code + ~140 LOC of tests + ~60 LOC of docs.

---

## Task 1: Add `BlockingMarkers` test helper

Test infrastructure for the daemon-thread tests in Task 6/7. Subclass of `FakeMarkers` that gates `is_ignored` on a caller-controlled `threading.Event`.

**Files:**
- Modify: `tests/conftest.py:58-78` (the existing `FakeMarkers` class)

- [ ] **Step 1: Add `BlockingMarkers` after `FakeMarkers` in `tests/conftest.py`**

Insert this code immediately after the `FakeMarkers` class definition (around line 78, before the `@pytest.fixture` line for `fake_markers`):

```python
class BlockingMarkers(FakeMarkers):
    """``FakeMarkers`` whose ``is_ignored`` waits on a caller-controlled gate.

    Used by `tests/test_daemon_initial_sweep.py` to deterministically pause
    the daemon's worker thread mid-sweep so tests can observe the
    ``state=starting`` window. The 10-second timeout on ``gate.wait()``
    bounds the failure mode: if a test forgets to open the gate, the
    daemon thread hangs but the wait returns False after 10s, the worker
    proceeds, and the test fails fast with a meaningful assertion error
    rather than blocking the full pytest timeout.
    """

    def __init__(self, gate: threading.Event) -> None:
        super().__init__()
        self._gate = gate

    def is_ignored(self, path: Path) -> bool:
        self._gate.wait(timeout=10.0)
        return super().is_ignored(path)
```

The `threading` import needs to be added to the top of `tests/conftest.py` if not already present. Check the current imports — if `threading` is missing, add `import threading` to the existing import block.

- [ ] **Step 2: Run existing test suite to verify the helper change didn't break anything**

```bash
uv run python -m pytest -m "not windows_only and not linux_only and not macos_only" -q
```

Expected: 370 passed, 10 skipped, 6 deselected (or current count — should match baseline).

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test(conftest): add BlockingMarkers helper for #53 daemon tests"
```

---

## Task 2: `reconcile_subtree` cooperative cancellation

Add a keyword-only `stop_event: threading.Event | None = None` parameter to `reconcile_subtree`. Add two check points inside the `os.walk` loop. All existing callers continue to work unchanged (default `None` = no cancellation).

**Files:**
- Modify: `src/dbxignore/reconcile.py:34-98` (`reconcile_subtree` function)
- Test: `tests/test_reconcile_basic.py` (add new test at end)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_reconcile_basic.py`:

```python
def test_reconcile_subtree_honors_stop_event(
    tmp_path: Path, fake_markers: FakeMarkers, write_file: WriteFile
) -> None:
    # Cooperative cancellation contract (item #53): when stop_event is set
    # before reconcile_subtree starts, the walk must break out without
    # processing additional directories. Convergence guarantees the next
    # sweep finishes the rest.
    import threading

    write_file(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "deep").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    stop = threading.Event()
    stop.set()  # Already cancelled before reconcile starts.

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache, stop_event=stop)

    # The top-level _reconcile_path(subdir, ...) call still ran (it's the
    # pre-walk path; cheap, single syscall). The os.walk loop never began.
    # No descendant directories were visited.
    assert (tmp_path / "src" / "deep").resolve() not in fake_markers.is_ignored_calls
    assert report.errors == []
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run python -m pytest tests/test_reconcile_basic.py::test_reconcile_subtree_honors_stop_event -v
```

Expected: FAIL with `TypeError: reconcile_subtree() got an unexpected keyword argument 'stop_event'`.

- [ ] **Step 3: Update `reconcile_subtree` signature in `src/dbxignore/reconcile.py`**

Find the function at line 34 and change its signature:

```python
def reconcile_subtree(
    root: Path, subdir: Path, cache: RuleCache, *,
    dry_run: bool = False,
    stop_event: threading.Event | None = None,
) -> Report:
```

Add `import threading` to the top of `reconcile.py` if not already present. (Check the existing imports — `threading` is not imported today, so add it alongside the other stdlib imports.)

Update the docstring to mention `stop_event`:

Find the existing docstring (lines ~37-52) and add a paragraph after the existing dry_run paragraph:

```python
    """...existing docstring...

    When ``dry_run`` is True, marker mutations are skipped: ``markers.set_ignored``
    and ``markers.clear_ignored`` are NOT called. ...

    When ``stop_event`` is supplied and gets set during the walk, the walk
    breaks out at the next directory or file boundary. The ``Report``
    returned has accurate counts for what completed before the break;
    convergence (next sweep over the same paths) finishes the rest. Used
    by the daemon's initial-sweep worker to support cooperative
    cancellation on SIGTERM (item #53).
    """
```

- [ ] **Step 4: Add the two check points inside the `os.walk` loop**

Find the `for current, dirnames, filenames in os.walk(subdir, followlinks=False):` block (around line 77) and modify:

```python
    for current, dirnames, filenames in os.walk(subdir, followlinks=False):
        if stop_event is not None and stop_event.is_set():
            break
        current_path = Path(current)
        # Reconcile each subdirectory; if it ends up ignored, prune it from
        # the walk (os.walk honors in-place modification of dirnames).
        dirnames[:] = [
            name
            for name in dirnames
            if not _reconcile_path(current_path / name, cache, report, dry_run=dry_run)
        ]
        for name in filenames:
            if stop_event is not None and stop_event.is_set():
                break
            _reconcile_path(current_path / name, cache, report, dry_run=dry_run)
```

The pre-walk `_reconcile_path(subdir, ...)` call at the top of the function (line 65) stays unchecked — it's a single syscall, cheaper to let it complete than to gate it.

- [ ] **Step 5: Run the test to verify it passes**

```bash
uv run python -m pytest tests/test_reconcile_basic.py::test_reconcile_subtree_honors_stop_event -v
```

Expected: PASS.

- [ ] **Step 6: Run the full reconcile test suite to check for regressions**

```bash
uv run python -m pytest tests/test_reconcile_basic.py tests/test_reconcile_edges.py tests/test_reconcile_enotsup.py tests/test_reconcile_return_state.py -v
```

Expected: All pass (the new keyword-only parameter is backward-compatible, defaults to `None`).

- [ ] **Step 7: Commit**

```bash
git add src/dbxignore/reconcile.py tests/test_reconcile_basic.py
git commit -m "feat(reconcile): cooperative cancellation via stop_event"
```

---

## Task 3: `_sweep_once` forwards `stop_event`

Add `stop_event` keyword-only parameter to `_sweep_once`, forwarded into both the multi-root `pool.map` lambda and the single-root direct call.

**Files:**
- Modify: `src/dbxignore/daemon.py:665-734` (`_sweep_once` function)
- Test: `tests/test_daemon_sweep.py` (add new test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_daemon_sweep.py`:

```python
def test_sweep_once_forwards_stop_event_to_reconcile(
    tmp_path: Path,
    fake_markers: FakeMarkers,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
) -> None:
    # When _sweep_once is called with stop_event already set, no path under
    # any root should have its marker queried beyond the top-level
    # _reconcile_path call. Confirms the parameter threads through to
    # reconcile_subtree.
    import threading

    root = tmp_path / "root"
    write_file(root / ".dropboxignore", "build/\n")
    (root / "build").mkdir()
    (root / "src").mkdir()
    (root / "src" / "deep").mkdir()

    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")

    cache = RuleCache()
    stop = threading.Event()
    stop.set()

    daemon._sweep_once([root], cache, _utc_now(), stop_event=stop)

    # The deeply-nested directory should NOT have been queried — the walk
    # broke out before descending.
    assert (root / "src" / "deep").resolve() not in fake_markers.is_ignored_calls
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run python -m pytest tests/test_daemon_sweep.py::test_sweep_once_forwards_stop_event_to_reconcile -v
```

Expected: FAIL with `TypeError: _sweep_once() got an unexpected keyword argument 'stop_event'`.

- [ ] **Step 3: Update `_sweep_once` signature in `src/dbxignore/daemon.py`**

Find `_sweep_once` at line 665. Change:

```python
def _sweep_once(
    roots: list[Path],
    cache: RuleCache,
    daemon_started: dt.datetime,
    daemon_create_time: float | None = None,
    *,
    stop_event: threading.Event | None = None,
) -> None:
```

(Note: `*,` makes `stop_event` keyword-only. Existing callers in the codebase pass positional args only through `daemon_create_time`, so this is backward-compatible.)

- [ ] **Step 4: Forward `stop_event` to `reconcile_subtree` in the two callsites**

Find the multi-root branch (around line 682) and the single-root branch (line 685). Update both:

```python
    if len(roots) > 1:
        with ThreadPoolExecutor(max_workers=len(roots)) as pool:
            reports = list(pool.map(
                lambda r: reconcile_subtree(r, r, cache, stop_event=stop_event),
                roots,
            ))
    elif roots:
        reports = [reconcile_subtree(roots[0], roots[0], cache, stop_event=stop_event)]
    else:
        reports = []
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
uv run python -m pytest tests/test_daemon_sweep.py::test_sweep_once_forwards_stop_event_to_reconcile -v
```

Expected: PASS.

- [ ] **Step 6: Run the daemon test suite to verify no regressions**

```bash
uv run python -m pytest tests/test_daemon_sweep.py tests/test_daemon_dispatch.py tests/test_daemon_synthetic_events.py -v
```

Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add src/dbxignore/daemon.py tests/test_daemon_sweep.py
git commit -m "feat(daemon): _sweep_once forwards stop_event to reconcile"
```

---

## Task 4: `_format_summary` adds `state=starting` token

When the State has `last_sweep is None` AND the daemon is alive, emit `state=starting pid=<N>` (no marked/cleared/errors/conflicts fields). When `last_sweep` is non-None and alive, emit the existing `state=running ...` shape.

**Files:**
- Modify: `src/dbxignore/cli.py:430-456` (`_format_summary`)
- Test: `tests/test_cli_status_list_explain.py` (add new test)

- [ ] **Step 1: Write the failing test**

Find an appropriate spot near the existing `_format_summary` tests in `tests/test_cli_status_list_explain.py` and append:

```python
def test_format_summary_starting_token_when_last_sweep_is_none() -> None:
    # state=starting contract (item #53): when daemon is alive but the
    # initial sweep hasn't completed (last_sweep is None), --summary emits
    # only `state=starting` + `pid=<N>` — no marked/cleared/errors/conflicts
    # fields, which would mislead consumers into reading "swept and found
    # nothing." Public API addition; documented in README.
    s = state.State(
        daemon_pid=12345,
        daemon_started=None,
        last_sweep=None,
        last_sweep_marked=0,
        last_sweep_cleared=0,
        last_sweep_errors=0,
        last_sweep_conflicts=0,
    )
    output = cli._format_summary(s, alive=True, conflicts_count=0)
    assert output == "state=starting pid=12345"
```

(Imports needed: `from dbxignore import cli, state`. Confirm they're already at module top.)

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run python -m pytest tests/test_cli_status_list_explain.py::test_format_summary_starting_token_when_last_sweep_is_none -v
```

Expected: FAIL — current `_format_summary` returns `state=running pid=12345 marked=0 cleared=0 errors=0 conflicts=0`.

- [ ] **Step 3: Update `_format_summary` in `src/dbxignore/cli.py`**

Find `_format_summary` at line 430. Replace its body:

```python
def _format_summary(state_obj: state.State | None, alive: bool, conflicts_count: int) -> str:
    """Build the stable single-line summary emitted by `status --summary`.

    Format is part of the public API per SemVer (see README §"Status-bar
    integration"). Field additions are non-breaking; removals or renames
    bump MINOR pre-1.0 / MAJOR post-1.0. Adding a new VALUE for an
    existing field (the `state=starting` token added in item #53) is
    technically a breaking change for consumers branching on
    `state == "running"` exhaustively — README documents the addition.

        state=starting pid=12345
        state=running pid=12345 marked=7 cleared=1 errors=0 conflicts=0
        state=not_running pid=12345 marked=7 cleared=1 errors=0 conflicts=0
        state=no_state conflicts=0

    State token is `starting` (state.json present + daemon alive + initial
    sweep not yet complete: `last_sweep is None`), `running` (state.json
    present + daemon alive + at least one sweep complete), `not_running`
    (state.json present, no live daemon — pid may be stale), or `no_state`
    (no state.json — daemon never ran).
    """
    if state_obj is None:
        return f"state=no_state conflicts={conflicts_count}"
    pid = state_obj.daemon_pid
    if pid is not None and alive and state_obj.last_sweep is None:
        # Alive but initial sweep hasn't completed yet. Emit the truncated
        # form: omit marked/cleared/errors/conflicts because they're all 0
        # and would falsely imply "swept and found nothing." Consumers
        # branching on the token need to handle 'starting' as distinct
        # from 'running'.
        return f"state=starting pid={pid}"
    state_token = "running" if (pid is not None and alive) else "not_running"
    parts = [f"state={state_token}"]
    if pid is not None:
        parts.append(f"pid={pid}")
    parts.append(f"marked={state_obj.last_sweep_marked}")
    parts.append(f"cleared={state_obj.last_sweep_cleared}")
    parts.append(f"errors={state_obj.last_sweep_errors}")
    parts.append(f"conflicts={conflicts_count}")
    return " ".join(parts)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run python -m pytest tests/test_cli_status_list_explain.py::test_format_summary_starting_token_when_last_sweep_is_none -v
```

Expected: PASS.

- [ ] **Step 5: Run the full status test file to check for regressions**

```bash
uv run python -m pytest tests/test_cli_status_list_explain.py -v
```

Expected: All pass — the new branch only fires when `last_sweep is None`, existing tests have non-None values.

- [ ] **Step 6: Commit**

```bash
git add src/dbxignore/cli.py tests/test_cli_status_list_explain.py
git commit -m "feat(cli): add state=starting token to status --summary"
```

---

## Task 5: `cli.status` human path shows "starting"

When `last_sweep is None` and the daemon is alive, the human-readable path should say "daemon: starting (initial sweep in progress)" instead of "daemon: running".

**Files:**
- Modify: `src/dbxignore/cli.py:466-507` (`status` function, human-path branch)
- Test: `tests/test_cli_status_list_explain.py` (add new test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_status_list_explain.py`:

```python
def test_status_human_path_shows_starting_when_initial_sweep_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Human-readable status output (no --summary flag): when last_sweep is
    # None and daemon is alive, output should mark the daemon as "starting"
    # so users know the initial sweep is still in progress, not that the
    # daemon is fully ready.
    import datetime as dt

    state_path = tmp_path / "state.json"
    monkeypatch.setattr(state, "default_path", lambda: state_path)
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid, create_time=None: True)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    # Write a state.json with last_sweep=None — the new "starting" shape.
    s = state.State(
        daemon_pid=12345,
        daemon_started=dt.datetime.now(dt.UTC),
        last_sweep=None,
    )
    state.write(s, path=state_path)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["status"])

    assert result.exit_code == 0
    assert "starting" in result.output
    assert "initial sweep" in result.output
```

(Imports needed: `from click.testing import CliRunner` — confirm it's already at module top, otherwise add.)

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run python -m pytest tests/test_cli_status_list_explain.py::test_status_human_path_shows_starting_when_initial_sweep_pending -v
```

Expected: FAIL — current human path prints "daemon: running (pid=12345)".

- [ ] **Step 3: Update the `status` function's human-path branch**

In `src/dbxignore/cli.py`, find the `status` function around line 466. The relevant block is around lines 489-495:

```python
        if s.daemon_pid is None:
            click.echo("daemon: not running (no pid recorded)")
        elif state.daemon_is_running(s):
            click.echo(f"daemon: running (pid={s.daemon_pid})")
        else:
            click.echo(f"daemon: not running (last pid={s.daemon_pid} — state.json may be stale)")
```

Replace with:

```python
        if s.daemon_pid is None:
            click.echo("daemon: not running (no pid recorded)")
        elif state.daemon_is_running(s):
            if s.last_sweep is None:
                click.echo(
                    f"daemon: starting (initial sweep in progress) (pid={s.daemon_pid})"
                )
            else:
                click.echo(f"daemon: running (pid={s.daemon_pid})")
        else:
            click.echo(f"daemon: not running (last pid={s.daemon_pid} — state.json may be stale)")
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run python -m pytest tests/test_cli_status_list_explain.py::test_status_human_path_shows_starting_when_initial_sweep_pending -v
```

Expected: PASS.

- [ ] **Step 5: Run the full status test file**

```bash
uv run python -m pytest tests/test_cli_status_list_explain.py -v
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/dbxignore/cli.py tests/test_cli_status_list_explain.py
git commit -m "feat(cli): status human path distinguishes starting vs running"
```

---

## Task 6: `daemon.run()` reorder — observer-first, worker thread, early state.write

The big one. Refactor `daemon.run()` to start the observer + write `state.json` *before* the initial sweep. Move the initial sweep into a `threading.Thread` worker that catches exceptions and triggers daemon shutdown via `stop_event`. Update the main-thread shutdown sequence to `worker.join()` after the observer stops.

**Files:**
- Modify: `src/dbxignore/daemon.py:539-662` (`run` function)
- Create: `tests/test_daemon_initial_sweep.py` (new file with first daemon-thread test)

- [ ] **Step 1: Create the new test file with the first failing test**

Create `tests/test_daemon_initial_sweep.py` with:

```python
"""Tests for the worker-thread initial-sweep design (BACKLOG #53).

These tests bring up a real daemon thread with a ``BlockingMarkers`` gate
to deterministically pause the worker mid-sweep and observe behavior in
the ``state=starting`` window. The 10-second gate timeout keeps a
forgotten ``gate.set()`` from hanging the full pytest timeout.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from dbxignore import cli, daemon, reconcile, state

if TYPE_CHECKING:
    from tests.conftest import BlockingMarkers, WriteFile


def _poll_until(fn, timeout_s: float = 5.0, interval_s: float = 0.05) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(interval_s)
    return False


def _make_blocking_markers(gate: threading.Event):
    from tests.conftest import BlockingMarkers
    return BlockingMarkers(gate)


def test_state_json_appears_before_sweep_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_file,
) -> None:
    """state.json must appear with state=starting before the initial sweep
    completes — that's the user-visible value of item #53. The transition
    to state=running must be observable after the sweep finishes."""
    root = tmp_path / "root"
    write_file(root / ".dropboxignore", "build/\n")
    (root / "build").mkdir()
    (root / "src").mkdir()

    state_dir = tmp_path / "state"
    monkeypatch.setattr(state, "default_path", lambda: state_dir / "state.json")
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(state, "user_log_dir", lambda: state_dir)
    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [root])

    gate = threading.Event()
    blocking = _make_blocking_markers(gate)
    monkeypatch.setattr(reconcile, "markers", blocking)
    monkeypatch.setattr(cli, "markers", blocking)

    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)
    t.start()
    try:
        # state.json should appear within ~5s, well before the gate is opened.
        appeared = _poll_until(
            lambda: (state_dir / "state.json").exists(),
            timeout_s=5.0,
        )
        assert appeared, "state.json did not appear within 5s of daemon start"

        # Read it: state should be 'starting' (last_sweep is None).
        s = state.read()
        assert s is not None
        assert s.daemon_pid is not None
        assert s.last_sweep is None, (
            f"expected last_sweep=None during starting window, got {s.last_sweep}"
        )

        # --summary output should reflect state=starting.
        summary = cli._format_summary(
            s, alive=True, conflicts_count=0,
        )
        assert summary == f"state=starting pid={s.daemon_pid}", (
            f"expected starting-form summary, got {summary!r}"
        )

        # Open the gate; worker proceeds and completes the sweep.
        gate.set()

        # state.json should transition to running (last_sweep != None).
        ran = _poll_until(
            lambda: (lambda x: x is not None and x.last_sweep is not None)(state.read()),
            timeout_s=10.0,
        )
        assert ran, "state.json never transitioned to state=running"
    finally:
        gate.set()
        stop.set()
        t.join(timeout=10.0)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run python -m pytest tests/test_daemon_initial_sweep.py::test_state_json_appears_before_sweep_completes -v
```

Expected: FAIL or TIMEOUT — current `daemon.run()` blocks on the initial sweep, gate never opens, the 5s poll for state.json times out.

- [ ] **Step 3: Refactor `daemon.run()` in `src/dbxignore/daemon.py`**

Find `daemon.run()` at line 539. The current ordering is roughly:

```
acquire lock → resolve roots → load cache → INITIAL SWEEP → build observer → start observer → log "watching" → hourly loop
```

Change to:

```
acquire lock → resolve roots → load cache → build observer → start observer → log "watching" → EARLY state.write → SPAWN WORKER → main-thread hourly loop
```

Concretely, replace the body of `run()` (after the existing legacy-compat singleton check + signal handler setup at lines 583-614) with the new ordering. The full replacement for lines ~615-660 (everything between "configured_roots = [r.resolve()..." and "logger.info('daemon stopped')"):

```python
            # Resolve at the daemon boundary; downstream layers must not re-pay.
            configured_roots = [r.resolve() for r in roots_module.discover()]
            if not configured_roots:
                logger.error("no Dropbox roots discovered; exiting")
                return

            # Surface the macOS sync-mode detection result so users can self-
            # diagnose without DBXIGNORE_LOG_LEVEL=DEBUG (followup item 37).
            summary = detection_summary()
            if summary is not None:
                logger.info("sync mode detection: %s", summary)

            cache = RuleCache()
            for r in configured_roots:
                cache.load_root(r)

            debouncer = Debouncer(
                on_emit=lambda item: _dispatch(item[2], cache, configured_roots),
                timeouts_ms=_timeouts_from_env(),
            )
            handler = _WatchdogHandler(debouncer, configured_roots, cache)
            observer = Observer()
            for r in configured_roots:
                observer.schedule(handler, str(r), recursive=True)

            debouncer.start()
            try:
                _start_observer_or_exit(observer)
                logger.info("watching roots: %s", [str(r) for r in configured_roots])

                # Early state.write: signal "daemon alive, sweep pending"
                # to consumers. last_sweep=None is the canonical
                # state=starting marker (item #53). On disk-write failure
                # log WARNING and continue — the worker will retry the
                # write at end of initial sweep.
                early_state = state_module.State(
                    daemon_pid=os.getpid(),
                    daemon_create_time=daemon_create_time,
                    daemon_started=daemon_started,
                    last_sweep=None,
                    watched_roots=configured_roots,
                )
                try:
                    state_module.write(early_state)
                except OSError as exc:
                    logger.warning("could not write early state file: %s", exc)

                # Initial sweep moves to a worker thread so the observer
                # is responsive and state.json reflects daemon-alive
                # immediately, even on large trees where the sweep takes
                # ~50s. On worker failure, the worker logs the exception
                # and sets stop_event, triggering the same shutdown path
                # signal handlers use.
                worker = threading.Thread(
                    target=_initial_sweep_worker,
                    args=(
                        configured_roots,
                        cache,
                        daemon_started,
                        daemon_create_time,
                        stop_event,
                    ),
                    daemon=False,
                    name="dbxignored-initial-sweep",
                )
                worker.start()

                try:
                    while not stop_event.is_set():
                        woke = stop_event.wait(SWEEP_INTERVAL_S)
                        if woke:
                            break
                        _sweep_once(
                            configured_roots, cache, daemon_started, daemon_create_time,
                            stop_event=stop_event,
                        )
                finally:
                    observer.stop()
                    observer.join()
            finally:
                debouncer.stop()
                # Wait for the initial-sweep worker to honor stop_event and
                # exit cleanly. The worker's reconcile_subtree checks
                # stop_event at every directory and file boundary, so the
                # join is bounded by ~one directory's reconcile time. The
                # 60s timeout guards against pathological cases.
                worker.join(timeout=60.0)
                if worker.is_alive():
                    logger.warning(
                        "initial-sweep worker did not exit within 60s of stop_event"
                    )
                logger.info("daemon stopped")
        finally:
            singleton_lock.close()
```

Note the references to `stop_event` — the existing code uses a local variable `stop_event` initialized at line 541 (`stop_event = stop_event or threading.Event()`). That binding stays and is now also passed to the worker thread.

- [ ] **Step 4: Add the `_initial_sweep_worker` function**

Append to `src/dbxignore/daemon.py` (after the `_sweep_once` function, before the end of file):

```python
def _initial_sweep_worker(
    roots: list[Path],
    cache: RuleCache,
    daemon_started: dt.datetime,
    daemon_create_time: float | None,
    stop_event: threading.Event,
) -> None:
    """Run the initial sweep in a worker thread (item #53).

    Catches all non-system exceptions, logs with traceback, and sets
    ``stop_event`` so the main thread shuts the daemon down via the same
    code path SIGTERM uses. ``BaseException`` (KeyboardInterrupt, SystemExit)
    propagates normally — those go through the signal handler.
    """
    try:
        _sweep_once(
            roots, cache, daemon_started, daemon_create_time,
            stop_event=stop_event,
        )
    except Exception:
        logger.exception("initial sweep worker failed; shutting daemon down")
        stop_event.set()
```

- [ ] **Step 5: Run the daemon-initial-sweep test to verify it passes**

```bash
uv run python -m pytest tests/test_daemon_initial_sweep.py::test_state_json_appears_before_sweep_completes -v
```

Expected: PASS.

- [ ] **Step 6: Run all daemon tests to verify no regressions**

```bash
uv run python -m pytest tests/test_daemon_dispatch.py tests/test_daemon_sweep.py tests/test_daemon_synthetic_events.py tests/test_daemon_singleton.py tests/test_daemon_logging.py tests/test_daemon_inotify_enospc.py tests/test_daemon_initial_sweep.py -v
```

Expected: All pass. Pay attention to `test_daemon_singleton` — its setup may have implicit assumptions about the order of state.json writes vs daemon startup. If something fails there, examine the failure message and adjust the test if its assumption is no longer correct, OR fix the implementation if a real regression is present.

- [ ] **Step 7: Run the full portable suite**

```bash
uv run python -m pytest -m "not windows_only and not linux_only and not macos_only" -q
```

Expected: All pass.

- [ ] **Step 8: Commit**

```bash
git add src/dbxignore/daemon.py tests/test_daemon_initial_sweep.py
git commit -m "feat(daemon): mark ready before initial sweep finishes (#53)"
```

---

## Task 7: Three more daemon integration tests

Append three more tests to `tests/test_daemon_initial_sweep.py` covering: observer-up-during-starting, worker-failure-shuts-down-daemon, cooperative-shutdown-during-sweep. The implementation from Task 6 should already make all three pass.

**Files:**
- Modify: `tests/test_daemon_initial_sweep.py` (append three new tests)

- [ ] **Step 1: Add the observer-up test**

Append to `tests/test_daemon_initial_sweep.py`:

```python
def test_observer_up_before_initial_sweep_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_file,
) -> None:
    """While the worker thread is paused on a closed gate, watchdog events
    arriving on the tree should still be classified and dispatched.
    Confirms the observer is genuinely up during the state=starting window
    and not blocked behind the initial sweep."""
    root = tmp_path / "root"
    write_file(root / ".dropboxignore", "")
    (root / "existing").mkdir()

    state_dir = tmp_path / "state"
    monkeypatch.setattr(state, "default_path", lambda: state_dir / "state.json")
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(state, "user_log_dir", lambda: state_dir)
    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [root])

    gate = threading.Event()
    blocking = _make_blocking_markers(gate)
    monkeypatch.setattr(reconcile, "markers", blocking)
    monkeypatch.setattr(cli, "markers", blocking)

    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)
    t.start()
    try:
        # Wait for daemon to reach the "starting" window (state.json exists).
        assert _poll_until(
            lambda: (state_dir / "state.json").exists(),
            timeout_s=5.0,
        ), "daemon never wrote early state.json"

        # While the gate is still closed, create a new directory matching
        # a rule. The watchdog observer should pick this up; reconcile may
        # or may not run depending on rules, but the observer being alive
        # is the contract under test.
        new_dir = root / "newly_created"
        new_dir.mkdir()

        # Wait briefly for the observer to deliver the event. We can't
        # easily assert "event was received" without internal observer
        # state, but we can confirm the daemon thread didn't crash.
        time.sleep(0.5)
        assert t.is_alive(), "daemon thread died while observer should be running"

        # Open the gate; daemon completes initial sweep + remains alive.
        gate.set()
        assert _poll_until(
            lambda: (lambda x: x is not None and x.last_sweep is not None)(state.read()),
            timeout_s=10.0,
        )
    finally:
        gate.set()
        stop.set()
        t.join(timeout=10.0)
```

- [ ] **Step 2: Add the worker-failure test**

Append:

```python
def test_worker_failure_shuts_down_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_file,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the initial sweep raises, the worker logs and sets stop_event,
    causing the main thread to exit. Daemon should be dead within ~5s,
    not lingering forever in state=starting."""
    root = tmp_path / "root"
    write_file(root / ".dropboxignore", "build/\n")
    (root / "build").mkdir()

    state_dir = tmp_path / "state"
    monkeypatch.setattr(state, "default_path", lambda: state_dir / "state.json")
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(state, "user_log_dir", lambda: state_dir)
    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [root])

    # Force the initial-sweep call to raise.
    def _raising_sweep_once(*args, **kwargs):
        raise RuntimeError("simulated sweep failure")

    monkeypatch.setattr(daemon, "_sweep_once", _raising_sweep_once)

    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)

    with caplog.at_level("ERROR", logger="dbxignore.daemon"):
        t.start()
        # Daemon should exit quickly via stop_event.set() from the worker.
        t.join(timeout=10.0)
        assert not t.is_alive(), "daemon did not exit within 10s after worker failure"

    # ERROR log should mention the worker failure with traceback.
    assert any(
        "initial sweep worker failed" in rec.message for rec in caplog.records
    ), "expected ERROR log naming the worker failure"
```

- [ ] **Step 3: Add the cooperative-shutdown test**

Append:

```python
def test_cooperative_shutdown_during_initial_sweep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_file,
) -> None:
    """Setting stop_event while the worker thread is mid-sweep must cause
    the daemon to exit promptly (not wait for the sweep to complete).
    Bound: ~5s, well under the 50s a real sweep on a large tree would
    take. Verifies cooperative cancellation in reconcile_subtree's walk."""
    root = tmp_path / "root"
    write_file(root / ".dropboxignore", "build/\n")
    # Create enough subdirectories that the walk takes meaningful time
    # under the BlockingMarkers gate without it being too slow without.
    for i in range(20):
        (root / f"dir_{i}").mkdir()

    state_dir = tmp_path / "state"
    monkeypatch.setattr(state, "default_path", lambda: state_dir / "state.json")
    monkeypatch.setattr(state, "user_state_dir", lambda: state_dir)
    monkeypatch.setattr(state, "user_log_dir", lambda: state_dir)
    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [root])

    gate = threading.Event()
    blocking = _make_blocking_markers(gate)
    monkeypatch.setattr(reconcile, "markers", blocking)
    monkeypatch.setattr(cli, "markers", blocking)

    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)

    t.start()
    # Wait for daemon to reach the starting window.
    assert _poll_until(
        lambda: (state_dir / "state.json").exists(),
        timeout_s=5.0,
    )

    # Set stop_event while the worker is paused on the gate. Open the gate
    # AFTER setting stop so the worker proceeds past wait() but should see
    # stop_event.is_set() at the next reconcile_subtree check point.
    shutdown_start = time.time()
    stop.set()
    gate.set()

    t.join(timeout=10.0)
    shutdown_duration = time.time() - shutdown_start

    assert not t.is_alive(), "daemon did not exit within 10s after stop_event"
    assert shutdown_duration < 5.0, (
        f"shutdown took {shutdown_duration:.2f}s — cooperative cancellation likely "
        "not honored at reconcile_subtree boundaries"
    )
```

- [ ] **Step 4: Run all four tests**

```bash
uv run python -m pytest tests/test_daemon_initial_sweep.py -v
```

Expected: all four PASS. If any fail, examine the failure carefully — the implementation from Task 6 is supposed to satisfy all three new tests as well.

- [ ] **Step 5: Run the full portable suite**

```bash
uv run python -m pytest -m "not windows_only and not linux_only and not macos_only" -q
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add tests/test_daemon_initial_sweep.py
git commit -m "test(daemon): observer-up + worker-failure + cooperative-shutdown"
```

---

## Task 8: Documentation updates

CLAUDE.md, README.md, and CHANGELOG.md.

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update CLAUDE.md — architecture paragraph**

Find the daemon architecture paragraph in `CLAUDE.md` (around line 30) that begins "The daemon's watchdog events are classified..." and append a new sentence at the end:

```
On daemon startup, the observer + early `state.json` write happen *before* the initial sweep — the sweep itself runs in a worker thread (`_initial_sweep_worker`), so events arriving during the ~50s initial-sweep window on large trees are dispatched normally rather than dropped. Worker failure logs the traceback and sets `stop_event` to trigger the same shutdown path SIGTERM uses (item #53).
```

- [ ] **Step 2: Update CLAUDE.md — gotcha bullet**

Find the gotcha section. Add a new bullet near the existing daemon-related gotchas:

```
- `state.json` existing means "daemon is alive" — NOT "daemon has completed an initial sweep." After item #53, the early `state.write()` fires before the worker starts, so consumers checking `state.json` for readiness should also branch on `state_obj.last_sweep is None` to detect the `state=starting` window. `cli.status --summary` exposes this as a distinct `state=starting` token; callers parsing the summary need to handle the new value alongside `running`/`not_running`/`no_state`.
```

- [ ] **Step 3: Update README.md — Status-bar integration section**

Find the `## Status-bar integration` section in `README.md`. Update the format documentation to include `state=starting`:

Find the existing list of state values and add `state=starting` to it. Add a paragraph immediately after the list:

```markdown
**`state=starting`** is emitted when the daemon is alive but the initial sweep has not yet completed. During this window, the summary contains only `state` and `pid` — `marked`, `cleared`, `errors`, and `conflicts` are omitted because they would all be 0 and would falsely imply the daemon swept and found nothing. The transition to `state=running` happens when the initial sweep completes (a fresh install of a 27,000-directory Dropbox tree took ~50s in testing).
```

- [ ] **Step 4: Update CHANGELOG.md — Breaking entry under [Unreleased]**

If `[Unreleased]` doesn't exist at the top of `CHANGELOG.md`, add it (per the project's "Keep a Changelog" convention).

Add a `**Breaking**` entry:

```markdown
## [Unreleased]

### Changed

- **Breaking** — `dbxignore status --summary` now emits a fourth state token `state=starting` while the daemon is alive but the initial sweep has not yet completed. During the starting window, the summary line contains only `state` and `pid` (no `marked`/`cleared`/`errors`/`conflicts` — those would all be 0 and falsely imply a completed sweep). Consumers branching exhaustively on `state == "running"` need to handle the new value. Pre-1.0, this rides the next MINOR version bump per the SemVer note in CLAUDE.md. Resolves BACKLOG #53 candidate 1.
```

(If a `### Changed` heading already exists, append the bullet there. If the structure differs from this template, follow the existing structure.)

- [ ] **Step 5: Verify CHANGELOG and README render**

```bash
# Manual visual check — the Markdown rendering on GitHub will be the
# canonical view. Local check: ensure no syntax errors.
cat CHANGELOG.md | head -30
cat README.md | grep -A 20 "## Status-bar integration"
```

Expected: Both render correctly, the new content appears in the right place.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md README.md CHANGELOG.md
git commit -m "docs: document #53 ready-before-sweep behavior + Breaking note"
```

---

## Task 9: Final verification + open PR

- [ ] **Step 1: Run full pre-flight checks**

```bash
uv run ruff check .
uv run ruff format --check .
uv run python -m pytest -m "not windows_only and not linux_only and not macos_only" -q
uv run mypy src/dbxignore/reconcile.py src/dbxignore/daemon.py src/dbxignore/cli.py 2>&1 | tail -10
```

Expected:
- ruff check: All checks passed.
- ruff format: 68+ files already formatted.
- pytest: 370+ tests passing (count grew by ~6 from new tests).
- mypy on touched files: only pre-existing `[import-untyped]` errors per the CLAUDE.md gotcha; no NEW errors introduced.

If mypy reports new errors, fix them before continuing. If pre-existing errors are the only ones, that's expected.

- [ ] **Step 2: Pre-flight commit-check on every commit**

```bash
for sha in $(git log origin/main..HEAD --format='%h'); do
  git log -1 --format='%s' $sha > /tmp/subj.txt
  echo "=== $sha ==="
  commit-check -m /tmp/subj.txt
done
```

Expected: Every commit emits "===" header with no error message after.

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin feat/53-ready-before-sweep
```

Then:

```bash
gh pr create --title "feat(daemon): ready before initial sweep finishes (#53)" --body "$(cat <<'EOF'
## Summary

Resolves BACKLOG item **#53** candidate 1 (ready-before-sweep). On a fresh install of a large Dropbox tree (~27k dirs, ~50s initial sweep), the daemon previously had a ~50s window where the watchdog observer was offline, `state.json` did not exist, and `dbxignore status` reported `no_state`.

After this change:
- Observer comes online ~1s after `dbxignored` start, regardless of tree size.
- `state.json` is written immediately with new token `state=starting`.
- Initial sweep moves to a worker thread; transitions `state=starting` → `state=running` on completion.
- Cooperative cancellation: SIGTERM during the initial sweep returns within ~1s rather than waiting for the full sweep.

Wall-clock for the initial sweep itself is unchanged (~50s on 27k dirs). What changed is *when* readiness signals fire relative to the sweep.

## Design

See `docs/superpowers/specs/2026-05-08-53-ready-before-sweep-design.md` (committed in this PR) for the full design rationale, including:
- `daemon.run()` reordering (observer-first, sweep in worker)
- Worker thread shape (Exception catch + stop_event.set on failure)
- Cooperative cancellation in `reconcile_subtree` via new `stop_event` parameter
- `state=starting` token contract + field-omission shape

## Breaking change

`dbxignore status --summary` adds a fourth state token `state=starting`. Consumers branching exhaustively on `state == "running"` need to handle the new value. Documented in `CHANGELOG.md` under `[Unreleased]`. Pre-1.0; rides the next MINOR.

## Test plan

- [x] `commit-check` pre-flight on every commit.
- [x] Full portable test suite passes (`pytest -m "not windows_only and not linux_only and not macos_only"`).
- [x] Five new tests: cooperative cancellation in reconcile_subtree, _sweep_once forwarding, format_summary starting token, status human path, four daemon-thread integration tests.
- [x] Scoped `mypy` on touched files: no new errors introduced.
- [ ] Manual: when CI is green, click "Update branch" once to confirm commit-check still passes (catches any regression in the bot-skip filter from PR #160).
EOF
)"
```

- [ ] **Step 4: Confirm CI green and report PR URL**

After the PR is created, check CI:

```bash
gh pr checks <PR_NUMBER> 2>&1 | head -10
```

Expected: `check`, `test (ubuntu-latest)`, `test (windows-latest)`, `test (macos-latest)` all pass. `claude-review` may go red on workflow-self-modification check (this PR doesn't modify workflows, so should pass — if it does go red, that's worth investigating).

Report the PR URL to the user.

---

## Notes on regression risks

A few non-obvious things to watch when running this plan:

- **`test_daemon_singleton.py`** may contain assumptions about state.json being written only after a sweep. The relevant test is `test_run_refuses_when_singleton_lock_is_held` and similar — these mostly check the lock semantic rather than state.json contents, so they should be unaffected. If a singleton test fails, examine whether the expectation is now stale.

- **Manual test scripts** (`scripts/manual-test-{ubuntu-vps,macos,windows}.{sh,ps1}`) currently poll for "watching roots" in `daemon.log`. After this change, that log line appears almost immediately, so the existing 180s timeout is overkill — but harmless to leave. No script changes required.

- **systemd `Type=simple`** is unchanged; the unit file does not need editing. The reordering is invisible at the systemd boundary.

- **`cli.clear`'s daemon-alive guard** (line 605: `if not force and s is not None and state.daemon_is_running(s)`) still works correctly during the starting window — `state.daemon_is_running(s)` returns True both during starting and running, so `clear` refuses to run in both cases. Desired behavior; no change needed.

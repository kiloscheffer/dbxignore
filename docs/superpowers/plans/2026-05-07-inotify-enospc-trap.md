# inotify ENOSPC/EMFILE trap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trap `OSError(ENOSPC)` and `OSError(EMFILE)` raised by `Observer.start()` in `daemon.run`, log an actionable sysctl runbook to `daemon.log`/journalctl, and exit `75` (`EX_TEMPFAIL`) so systemd marks the unit failed.

**Architecture:** One new private helper `_start_observer_or_exit` in `daemon.py` wraps `observer.start()` in `try/except OSError`, dispatching on `errno` and calling `sys.exit(75)` on the two trapped cases. `daemon.run`'s observer-lifecycle block is restructured so `observer.start()` lives outside the inner `try/finally: observer.stop(); observer.join()` — calling `stop`/`join` on a never-started Thread raises RuntimeError, so SystemExit must skip that cleanup. Other `OSError` errnos propagate unchanged.

**Tech Stack:** Python 3.11+, watchdog (`Observer`, `FileSystemEventHandler`), pytest with `monkeypatch` and `caplog`. No new third-party dependencies.

**Spec:** [`docs/superpowers/specs/2026-05-07-inotify-enospc-trap.md`](../specs/2026-05-07-inotify-enospc-trap.md). Read this before starting Task 1.

---

## File map

**Create:**
- `tests/test_daemon_inotify_enospc.py` — three unit tests (ENOSPC trap, EMFILE trap, unknown-errno propagation) plus shared fakes for `Observer` and `Debouncer`.

**Modify:**
- `src/dbxignore/daemon.py` — add `errno` import; add `_ENOSPC_MESSAGE` and `_EMFILE_MESSAGE` module constants; add `_start_observer_or_exit(observer)` helper; refactor the observer-lifecycle block in `run()` (current lines 443–458) to hoist `observer.start()` out of the inner `try`/`finally`.
- `README.md` — append `### Linux daemon prerequisites` subsection inside `## Install (Linux)` (between current line 84 and the `## Install (macOS)` heading at line 85).
- `BACKLOG.md` — add inline `**Status: RESOLVED 2026-05-07 (PR #<N>).**` marker to item #52's body; add an entry under `## Status > Resolved > #### 2026-05-07`; remove `#52` from the `## Status > Open` list and update the open-count sentence in the `### Open` lead paragraph (currently "Thirty-five items").

**No changes to:** `cli.py`, `state.py`, `reconcile.py`, manual-test scripts (per spec scope-out).

---

## Commit plan

This branch (`fix/inotify-enospc-trap`) already has one commit (the spec). Three more commits land on it before the PR opens:

1. `fix(daemon): trap inotify ENOSPC/EMFILE at observer startup` — Tasks 1–5 (code + tests, including the README addition since it documents the new exit-75 behavior that ships with the code).
2. `docs(backlog): mark item #52 resolved` — Task 6.
3. *(optional)* additional fixup commits if review surfaces issues; never `--amend`.

Per CLAUDE.md: each commit subject must pass `commit-check -m /dev/stdin` locally before push (use the `for sha in $(git log origin/main..HEAD --format='%h'); do ...; done` loop from CLAUDE.md's `--no-verify` workaround section even when hooks pass, since CI re-runs across the full range).

---

## Task 1: Write the failing ENOSPC test

**Files:**
- Create: `tests/test_daemon_inotify_enospc.py`

This task creates the test file with shared fakes and the first failing test. No production code changes yet. Test 1 exercises the ENOSPC path; the daemon should log the sysctl runbook and `sys.exit(75)`.

- [ ] **Step 1.1: Create the test file with shared fakes and Test 1**

Create `tests/test_daemon_inotify_enospc.py` with this exact content:

```python
"""Trap inotify resource exhaustion at observer startup (BACKLOG #52)."""

from __future__ import annotations

import contextlib
import errno
import logging
from pathlib import Path
from typing import Any

import pytest

from dbxignore import daemon, state


class _FakeObserver:
    """Stand-in for watchdog.Observer; .start() raises a configured OSError."""

    def __init__(self, *, start_error: OSError | None = None) -> None:
        self._start_error = start_error
        self.scheduled: list[tuple[Any, str, bool]] = []
        self.started = False
        self.stopped = False
        self.joined = False

    def schedule(self, handler: Any, path: str, recursive: bool = False) -> None:
        self.scheduled.append((handler, path, recursive))

    def start(self) -> None:
        if self._start_error is not None:
            raise self._start_error
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self) -> None:
        self.joined = True


class _FakeDebouncer:
    """Stand-in for Debouncer; records start/stop call ordering."""

    def __init__(self, **_kwargs: Any) -> None:
        self.calls: list[str] = []

    def start(self) -> None:
        self.calls.append("start")

    def stop(self) -> None:
        self.calls.append("stop")

    def submit(self, *_args: Any, **_kwargs: Any) -> None:  # pragma: no cover
        pass


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    start_error: OSError | None = None,
) -> tuple[_FakeObserver, _FakeDebouncer]:
    fake_observer = _FakeObserver(start_error=start_error)
    fake_debouncer = _FakeDebouncer()
    monkeypatch.setattr(daemon, "Observer", lambda: fake_observer)
    monkeypatch.setattr(daemon, "Debouncer", lambda **kw: fake_debouncer)
    monkeypatch.setattr(daemon, "_configured_logging", contextlib.nullcontext)
    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [tmp_path])
    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")
    return fake_observer, fake_debouncer


def test_run_traps_enospc_and_exits_75(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ENOSPC at observer.start() → ERROR log with sysctl block + sys.exit(75)."""
    err = OSError(errno.ENOSPC, "inotify watch limit reached")
    _, fake_debouncer = _install_fakes(monkeypatch, tmp_path, start_error=err)

    caplog.set_level(logging.ERROR, logger="dbxignore.daemon")
    with pytest.raises(SystemExit) as exc_info:
        daemon.run()

    assert exc_info.value.code == 75
    messages = "\n".join(rec.message for rec in caplog.records)
    assert "fs.inotify.max_user_watches=524288" in messages
    assert "ENOSPC" in messages
    # Outer finally must run despite SystemExit so the debouncer thread is stopped.
    assert fake_debouncer.calls == ["start", "stop"]
```

- [ ] **Step 1.2: Run the test; verify it fails**

Run: `uv run python -m pytest tests/test_daemon_inotify_enospc.py::test_run_traps_enospc_and_exits_75 -v`

Expected: FAIL. The current `daemon.run` propagates `OSError(ENOSPC)` directly, so `pytest.raises(SystemExit)` will not match — pytest reports `Failed: DID NOT RAISE <class 'SystemExit'>` (with the underlying `OSError` reported as the actual exception).

If the test fails for any other reason (e.g. `ModuleNotFoundError`, `AttributeError` on `daemon.Observer`), stop and re-check the import lines and the monkeypatch targets before continuing.

---

## Task 2: Implement the trap and refactor `daemon.run`

**Files:**
- Modify: `src/dbxignore/daemon.py` — add `errno` import; add two message constants; add `_start_observer_or_exit`; refactor `run()`'s observer-lifecycle block.

This task adds just the ENOSPC arm of the helper plus the run-loop refactor — enough to make Test 1 pass. EMFILE is added in Task 3.

- [ ] **Step 2.1: Add `errno` to the imports in `daemon.py`**

Open `src/dbxignore/daemon.py`. The current top-of-file import block (lines 3–16) has stdlib imports in alphabetical order. Insert `import errno` between `import datetime as dt` and `import logging`:

Before:
```python
import contextlib
import datetime as dt
import logging
import logging.handlers
```

After:
```python
import contextlib
import datetime as dt
import errno
import logging
import logging.handlers
```

- [ ] **Step 2.2: Add the two message constants above `_log_dir`**

Locate `_log_dir` at `daemon.py:273`. Above it (and below the `_timeouts_from_env` function that ends around line 270), insert these two module-level constants:

```python
_ENOSPC_MESSAGE = (
    "inotify watch limit reached (errno ENOSPC). The kernel's "
    "fs.inotify.max_user_watches is exhausted; recursive watch on a Dropbox "
    "tree larger than the per-user limit fails at observer startup. To raise "
    "the limit, run as root:\n"
    "\n"
    "    sudo sysctl -w fs.inotify.max_user_watches=524288\n"
    "\n"
    "To make the change persist across reboots:\n"
    "\n"
    "    echo 'fs.inotify.max_user_watches=524288' | sudo tee "
    "/etc/sysctl.d/99-dbxignore.conf\n"
    "    sudo sysctl --system\n"
    "\n"
    "Alternatively, reduce the watched tree by adding rules to .dropboxignore. "
    "Daemon exiting with status 75."
)

_EMFILE_MESSAGE = (
    "inotify instance limit reached (errno EMFILE). The kernel's "
    "fs.inotify.max_user_instances is exhausted. To raise the limit, run as "
    "root:\n"
    "\n"
    "    sudo sysctl -w fs.inotify.max_user_instances=1024\n"
    "\n"
    "To make the change persist across reboots:\n"
    "\n"
    "    echo 'fs.inotify.max_user_instances=1024' | sudo tee "
    "/etc/sysctl.d/99-dbxignore.conf\n"
    "    sudo sysctl --system\n"
    "\n"
    "Daemon exiting with status 75."
)
```

The leading underscore makes these private. They are emitted as a single argument to `logger.error` so journalctl shows one log record (multi-line) rather than splitting on `\n`.

- [ ] **Step 2.3: Add `_start_observer_or_exit` above `class _WatchdogHandler`**

Locate `class _WatchdogHandler` at `daemon.py:365`. Above it (and below `_is_other_live_daemon` which ends at line 362), insert the helper:

```python
def _start_observer_or_exit(observer: Observer) -> None:
    """Start ``observer``; trap kernel-watch-resource exhaustion and exit cleanly.

    inotify's per-user limits surface here on Linux when a Dropbox tree
    exceeds ``fs.inotify.max_user_watches`` (raises ``OSError(ENOSPC)``)
    or ``fs.inotify.max_user_instances`` (raises ``OSError(EMFILE)``).
    Without this trap the daemon dies with an opaque traceback in journalctl.

    On a trapped errno: log ERROR with the matching sysctl runbook, then
    ``sys.exit(75)`` (POSIX ``EX_TEMPFAIL``). systemd marks the unit
    ``failed`` so ``systemctl is-failed`` and status-bar widgets catch it.
    Existing fatal paths in ``run()`` (``no Dropbox roots``, ``already
    running``) return silently with exit 0; this path deviates because the
    kernel signal warrants a non-zero exit.

    Other ``OSError`` shapes propagate; we don't suppress unknown causes.

    Caller MUST invoke this from inside ``_configured_logging()`` so the
    ERROR record reaches ``daemon.log`` and (on Linux) systemd-journald.
    """
    try:
        observer.start()
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            logger.error(_ENOSPC_MESSAGE)
            sys.exit(75)
        raise
```

Note this version handles only ENOSPC; the EMFILE arm is added in Task 3 once Test 2 forces it.

- [ ] **Step 2.4: Refactor `run()` to hoist `observer.start()` out of the inner try**

Locate the observer-lifecycle block in `run()` at `daemon.py:443–458`. Current text:

```python
        debouncer.start()
        try:
            observer.start()
            logger.info("watching roots: %s", [str(r) for r in configured_roots])
            try:
                while not stop_event.is_set():
                    woke = stop_event.wait(SWEEP_INTERVAL_S)
                    if woke:
                        break
                    _sweep_once(configured_roots, cache, daemon_started)
            finally:
                observer.stop()
                observer.join()
        finally:
            debouncer.stop()
            logger.info("daemon stopped")
```

Replace with:

```python
        debouncer.start()
        try:
            _start_observer_or_exit(observer)
            logger.info("watching roots: %s", [str(r) for r in configured_roots])
            try:
                while not stop_event.is_set():
                    woke = stop_event.wait(SWEEP_INTERVAL_S)
                    if woke:
                        break
                    _sweep_once(configured_roots, cache, daemon_started)
            finally:
                observer.stop()
                observer.join()
        finally:
            debouncer.stop()
            logger.info("daemon stopped")
```

The only change is `observer.start()` → `_start_observer_or_exit(observer)`. The structural property the spec calls out (start is positioned so that `SystemExit` from inside the helper skips the inner `try/finally` and falls straight through to the outer `finally: debouncer.stop()`) is already satisfied: the helper call sits at the head of the outer try, and the inner try/finally only wraps the loop body. `SystemExit` raised from `_start_observer_or_exit` skips the loop, skips the inner finally entirely (it never entered the inner try), and triggers the outer finally — exactly what we want.

- [ ] **Step 2.5: Run Test 1; verify it passes**

Run: `uv run python -m pytest tests/test_daemon_inotify_enospc.py::test_run_traps_enospc_and_exits_75 -v`

Expected: PASS.

If the test fails:
- `AttributeError: module 'dbxignore.daemon' has no attribute 'Observer'` — the import order in Step 2.1 broke the existing `from watchdog.observers import Observer` line. Re-check `daemon.py:19`.
- `assert fake_debouncer.calls == ["start", "stop"]` failed with `["start"]` — the outer `finally` didn't run; check that the helper actually raises `SystemExit` (not `os._exit`, not `return`).
- `"fs.inotify.max_user_watches=524288" in messages` failed — verify `_ENOSPC_MESSAGE` was inserted as written and `logger.error(_ENOSPC_MESSAGE)` is in the helper.

- [ ] **Step 2.6: Run the full daemon test suite to verify no regressions**

Run: `uv run python -m pytest tests/test_daemon_*.py -v`

Expected: all existing tests pass plus the new one.

The refactor touches `run()`'s observer-lifecycle block, which existing tests in `test_daemon_singleton.py`, `test_daemon_logging.py`, `test_daemon_smoke.py`, `test_daemon_smoke_linux.py`, `test_daemon_sweep.py`, and `test_daemon_dispatch.py` exercise. If any test that mocks `Observer` fails after the refactor, inspect whether it now relies on `daemon.Observer` being callable directly (the new code calls `_start_observer_or_exit(observer)` — but `observer` is still constructed via `Observer()` at line 439, so existing patches of `daemon.Observer` should still work).

- [ ] **Step 2.7: Do NOT commit yet**

EMFILE handling and the propagation pin are still ahead. Wait until Task 5.

---

## Task 3: Add the EMFILE test and EMFILE arm

**Files:**
- Modify: `tests/test_daemon_inotify_enospc.py` — append Test 2.
- Modify: `src/dbxignore/daemon.py` — add the EMFILE arm to `_start_observer_or_exit`.

- [ ] **Step 3.1: Append Test 2 to the test file**

At the end of `tests/test_daemon_inotify_enospc.py`, append:

```python
def test_run_traps_emfile_and_exits_75(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """EMFILE at observer.start() → ERROR log with instances sysctl + sys.exit(75)."""
    err = OSError(errno.EMFILE, "Too many open files")
    _, fake_debouncer = _install_fakes(monkeypatch, tmp_path, start_error=err)

    caplog.set_level(logging.ERROR, logger="dbxignore.daemon")
    with pytest.raises(SystemExit) as exc_info:
        daemon.run()

    assert exc_info.value.code == 75
    messages = "\n".join(rec.message for rec in caplog.records)
    assert "fs.inotify.max_user_instances=1024" in messages
    assert "EMFILE" in messages
    assert fake_debouncer.calls == ["start", "stop"]
```

- [ ] **Step 3.2: Run Test 2; verify it fails**

Run: `uv run python -m pytest tests/test_daemon_inotify_enospc.py::test_run_traps_emfile_and_exits_75 -v`

Expected: FAIL. The helper currently re-raises EMFILE because only the ENOSPC branch is wired up. Pytest reports `Failed: DID NOT RAISE <class 'SystemExit'>` (with `OSError` as the actual exception).

- [ ] **Step 3.3: Add the EMFILE arm to `_start_observer_or_exit`**

In `src/dbxignore/daemon.py`, locate `_start_observer_or_exit` and update the body. Current:

```python
    try:
        observer.start()
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            logger.error(_ENOSPC_MESSAGE)
            sys.exit(75)
        raise
```

Replace with:

```python
    try:
        observer.start()
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            logger.error(_ENOSPC_MESSAGE)
            sys.exit(75)
        if exc.errno == errno.EMFILE:
            logger.error(_EMFILE_MESSAGE)
            sys.exit(75)
        raise
```

The two arms are kept separate (rather than collapsed into a dispatch dict) so the spec's "narrow trap" contract is visible at a glance: each errno is named individually; `raise` is the bottom of the `except OSError` arm and propagates anything we didn't trap.

- [ ] **Step 3.4: Run Test 2; verify it passes**

Run: `uv run python -m pytest tests/test_daemon_inotify_enospc.py::test_run_traps_emfile_and_exits_75 -v`

Expected: PASS.

---

## Task 4: Add the unknown-errno propagation pin

**Files:**
- Modify: `tests/test_daemon_inotify_enospc.py` — append Test 3.

This test pins the contract that errnos other than ENOSPC/EMFILE propagate as plain `OSError`. No production code change is needed — the existing `raise` in the `except OSError` arm already does this.

- [ ] **Step 4.1: Append Test 3 to the test file**

At the end of `tests/test_daemon_inotify_enospc.py`, append:

```python
def test_run_propagates_unknown_oserror(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A non-trapped errno (e.g. EIO) propagates as OSError, not SystemExit."""
    err = OSError(errno.EIO, "I/O error")
    _, fake_debouncer = _install_fakes(monkeypatch, tmp_path, start_error=err)

    with pytest.raises(OSError) as exc_info:
        daemon.run()

    assert exc_info.value.errno == errno.EIO
    # Outer finally still runs despite the propagating OSError.
    assert fake_debouncer.calls == ["start", "stop"]
```

- [ ] **Step 4.2: Run Test 3; verify it passes immediately**

Run: `uv run python -m pytest tests/test_daemon_inotify_enospc.py::test_run_propagates_unknown_oserror -v`

Expected: PASS without any new production code (the `raise` was already there).

If it fails with `Failed: DID NOT RAISE` — the helper is over-trapping. Re-check Step 3.3 and confirm the third branch is `raise`, not a fallthrough.

---

## Task 5: Add the README subsection, run full checks, commit code+tests+README

**Files:**
- Modify: `README.md` — append `### Linux daemon prerequisites` subsection inside `## Install (Linux)`.

- [ ] **Step 5.1: Locate the insertion point in README.md**

Open `README.md`. The `## Install (Linux)` section starts at line 59 and ends at the `## Install (macOS)` heading at line 85. The new subsection goes immediately before line 85.

- [ ] **Step 5.2: Insert the subsection**

Use the Edit tool to insert this content between the last line of `## Install (Linux)` and the `## Install (macOS)` heading. The exact string to insert (with one leading blank line and one trailing blank line):

```markdown

### Linux daemon prerequisites

The daemon uses inotify to watch the Dropbox tree recursively. The kernel
caps the number of watches per user (`fs.inotify.max_user_watches`); on
default-config kernels this is often 8192, which a typical Dropbox tree
exceeds. The daemon refuses to start (exit code 75) when the limit is hit.

Raise the limit (one-time, persistent across reboots):

    echo 'fs.inotify.max_user_watches=524288' | sudo tee /etc/sysctl.d/99-dbxignore.conf
    sudo sysctl --system

If the daemon won't start, check `journalctl --user -u dbxignore.service`
for the exact errno (ENOSPC = watch count, EMFILE = instance count) and the
sysctl command to run.

```

Read the line above `## Install (macOS)` first to find the unique anchor (likely the closing line of the existing Linux install block — read README.md lines 80–86 to confirm). Then `Edit` with `old_string` = the existing two-line transition (last Linux line + blank line + `## Install (macOS)`) and `new_string` = the same with the new subsection inserted before the `## Install (macOS)` heading.

- [ ] **Step 5.3: Run the full project check suite**

Run these in order; do not skip steps:

```
uv run mypy .
uv run ruff check . --fix
uv run ruff check .
uv run ruff format .
uv run python -m pytest
```

Expected: all green. If `mypy` flags `_start_observer_or_exit`'s `Observer` parameter (watchdog's `Observer` is dynamically resolved to a platform-specific subclass and may be untyped), use the project's existing pattern: `# type: ignore[arg-type, unused-ignore]` is unnecessary here because `daemon.py:19` already imports `Observer` and other functions in the file accept it without ignores. If a mypy error appears, post the message — don't paper over with an ignore until the cause is understood.

If `ruff` flags the long string literals in `_ENOSPC_MESSAGE` / `_EMFILE_MESSAGE` for line length, the string concatenation in Step 2.2 was written specifically to keep each physical line under 100 chars; if a line still exceeds 100 chars, split it further.

- [ ] **Step 5.4: Stage and commit the implementation**

Stage only the four files this task touches:

```bash
git add src/dbxignore/daemon.py tests/test_daemon_inotify_enospc.py README.md
```

Verify nothing else is staged:

```bash
git status
```

Expected: `Changes to be committed:` lists exactly `daemon.py`, `test_daemon_inotify_enospc.py`, and `README.md`. The pre-existing `.gitignore` modification stays unstaged.

Create the commit:

```bash
git commit -m "$(cat <<'EOF'
fix(daemon): trap inotify ENOSPC/EMFILE at observer startup

Wraps observer.start() in a new _start_observer_or_exit helper that
catches OSError(ENOSPC) (fs.inotify.max_user_watches exhausted) and
OSError(EMFILE) (fs.inotify.max_user_instances exhausted), logs ERROR
with the matching sysctl runbook, and sys.exit(75). systemd marks the
unit failed instead of dying with an opaque Python traceback.

Hoists observer.start() out of the inner try/finally so SystemExit
skips the never-started Observer's stop()/join(). Other OSError shapes
still propagate. Adds three unit tests pinning the ENOSPC trap, the
EMFILE trap, and the unknown-errno propagation contract. README's
Install (Linux) section gains a Linux daemon prerequisites subsection
documenting the sysctl knob and the exit-code-75 signal.

Resolves #52.
EOF
)"
```

If the pre-commit hook fails on the editable-install step (the Dropbox-on-OneDrive `os error 32` gotcha documented in CLAUDE.md), and only that step — `ruff lint`, `ruff format`, `mypy` showing "Passed" — `--no-verify` is acceptable for this commit. After committing with `--no-verify`, immediately run the manual checks from Step 5.3 to confirm cleanliness.

- [ ] **Step 5.5: Pre-flight commit-check on the new commit**

Run the loop from CLAUDE.md:

```bash
for sha in $(git log origin/main..HEAD --format='%h'); do git log -1 --format='%B' $sha | commit-check -m /dev/stdin; done
```

Expected: no output (silent success across both commits — the spec commit and this one).

If commit-check rejects either commit, fix the subject and create a NEW commit (per CLAUDE.md: never amend in this project).

---

## Task 6: Mark item #52 resolved in BACKLOG.md

**Files:**
- Modify: `BACKLOG.md` — add inline RESOLVED marker to #52's body; add an entry to the `## Status > Resolved > #### 2026-05-07` section; remove `#52` from the `## Status > Open` list; update the lead-paragraph open count.

The PR number prediction at plan-write time is **#125** (most recent: PR #124, no recent issues). Verify with `gh pr list --state all --limit 1 --json number --jq '.[0].number'` plus `gh issue list --state all --limit 1 --json number --jq '.[0].number // 0'` immediately before this task; use `max(...) + 1`. If the PR opens with a different number, amend this commit later (rare per CLAUDE.md note).

- [ ] **Step 6.1: Add the inline RESOLVED marker to item #52's body**

Locate `## 52. Watchdog OSError(ENOSPC)...` at `BACKLOG.md:1122`. The body currently ends at line 1138 with the `Touches:` line. After that line, insert a blank line then:

```markdown
**Status: RESOLVED 2026-05-07 (PR #125).** Trap added to `daemon.py` via `_start_observer_or_exit`; covers both ENOSPC (`fs.inotify.max_user_watches`) and EMFILE (`fs.inotify.max_user_instances`). Logs ERROR with the matching sysctl runbook and `sys.exit(75)` so systemd marks the unit `failed`. Other `OSError` shapes still propagate. README's `## Install (Linux)` gained a `### Linux daemon prerequisites` subsection. (B) scope decision per #52's body: `PollingObserver` fallback declined as worse than failing fast on the trees that hit the limit.
```

- [ ] **Step 6.2: Add the entry to the Resolved section**

Locate `### Resolved (reverse chronological)` at `BACKLOG.md:1781`. The most recent date heading is `#### 2026-05-04` at line 1783. Insert a new date heading above it:

```markdown
#### 2026-05-07

- **#52** in PR #125 — `daemon._start_observer_or_exit` traps `OSError(ENOSPC)` and `OSError(EMFILE)` from `Observer.start()`; logs ERROR with the matching sysctl runbook (`fs.inotify.max_user_watches=524288` / `max_user_instances=1024`) and `sys.exit(75)` so systemd marks the unit `failed`. Hoists `observer.start()` out of the inner `try/finally` to avoid `Observer.stop()`/`join()` on a never-started Thread when `SystemExit` fires. Three unit tests in `tests/test_daemon_inotify_enospc.py` pin the ENOSPC trap, the EMFILE trap, and the unknown-errno propagation contract. README `## Install (Linux)` gained a `### Linux daemon prerequisites` subsection. Surfaced 2026-05-03 by a VPS tester on a default-`max_user_watches=8192` kernel.

```

The trailing blank line separates this block from the existing `#### 2026-05-04` heading.

- [ ] **Step 6.3: Remove #52 from the Open list and update the lead-paragraph count**

Locate the lead paragraph at `BACKLOG.md:1743`. The current text begins:

> Thirty-five items. Thirty-two are passive (no concrete trigger requires action); item #52 has one fired trigger ...

Two edits in this paragraph:
- "Thirty-five items" → "Thirty-four items"
- Strike the `item #52 has one fired trigger ... isn't blocking;` clause; the rest of the sentence still reads naturally with the remaining items.

After the edit, the paragraph should read (approximately — preserve any other clauses verbatim):

> Thirty-four items. Thirty-two are passive (no concrete trigger requires action); item #34 is a recurrence of an already-resolved flake (item #18); item #73 had multiple fired triggers in one session (the local PR-review hook over-fired on Bash commands that didn't match its declared `if` filter — friction not blocking). Item #34's third recurrence fired 2026-05-04 during PR #95 pre-flight; widening 5.0s → 7.0s → 10.0s all failed under full-suite load (different polls exhausted on each run), so the suggested band-aid fix shape was abandoned and #34 stays open pending root-cause diagnosis (the test passes in 0.27s in isolation but >7s in the full suite, so the cause lives in test-order interaction with an earlier test).

Then locate the `- **#52** —` bullet (one line at `BACKLOG.md:1759`) and delete the entire line. Verify the surrounding bullets are untouched.

- [ ] **Step 6.4: Commit the BACKLOG update separately**

```bash
git add BACKLOG.md
git status
```

Verify only `BACKLOG.md` is staged.

```bash
git commit -m "$(cat <<'EOF'
docs(backlog): mark item #52 resolved

Inline RESOLVED marker on the item body + entry in the Status > Resolved
section under #### 2026-05-07. Removed from the open list and updated the
open-count sentence in the Status > Open lead paragraph.
EOF
)"
```

- [ ] **Step 6.5: Pre-flight commit-check across the full range**

```bash
for sha in $(git log origin/main..HEAD --format='%h'); do git log -1 --format='%B' $sha | commit-check -m /dev/stdin; done
```

Expected: silent success across all three commits on this branch (`docs(spec):`, `fix(daemon):`, `docs(backlog):`).

---

## Task 7: Push and open the PR

- [ ] **Step 7.1: Verify the branch is clean and ahead of main**

```bash
git status
git log --oneline origin/main..HEAD
```

Expected: working tree clean (the unrelated `.gitignore` modification remains unstaged from Task 5.4 onward — this is correct, it was never part of this work). Three commits ahead of `main`: spec, fix, backlog.

- [ ] **Step 7.2: Push the branch**

```bash
git push -u origin fix/inotify-enospc-trap
```

- [ ] **Step 7.3: Open the PR**

```bash
gh pr create --title "fix(daemon): trap inotify ENOSPC/EMFILE at observer startup" --body "$(cat <<'EOF'
## Summary

- Trap `OSError(ENOSPC)` and `OSError(EMFILE)` raised by `Observer.start()` in `daemon.run`; log an actionable sysctl runbook to `daemon.log`/journalctl and `sys.exit(75)` so systemd marks the unit `failed` instead of dying with an opaque traceback.
- Add `### Linux daemon prerequisites` subsection to README's `## Install (Linux)`, documenting the `fs.inotify.max_user_watches` knob and the exit-code-75 signal.
- Three unit tests in `tests/test_daemon_inotify_enospc.py` pin the ENOSPC trap, the EMFILE trap, and the unknown-errno propagation contract.

Resolves #52. Spec at `docs/superpowers/specs/2026-05-07-inotify-enospc-trap.md`; plan at `docs/superpowers/plans/2026-05-07-inotify-enospc-trap.md`.

## Test plan

- [x] `uv run python -m pytest tests/test_daemon_inotify_enospc.py -v` — all three tests pass
- [x] `uv run python -m pytest tests/test_daemon_*.py -v` — no regressions in the existing daemon test files
- [x] `uv run mypy .` clean
- [x] `uv run ruff check .` clean
- [x] `uv run ruff format .` no diff
- [x] CI: portable pytest subset green on ubuntu/windows/macos plus each platform's `_only` tier
EOF
)"
```

- [ ] **Step 7.4: Verify the assigned PR number matches the prediction**

```bash
gh pr view --json number --jq '.number'
```

If the result is `125`, the prediction stands. If different, amend Task 6's two `PR #125` references in `BACKLOG.md` to the actual number, commit as `docs(backlog): correct PR number for item #52 resolution`, push.

---

## Self-review

Spec coverage:

- Spec § "In scope" → bullet 1 (ENOSPC trap) → Task 2; bullet 2 (EMFILE) → Task 3; bullet 3 (exit 75 ⇒ unit failed) → Task 2 + 3; bullet 4 (README subsection) → Task 5; bullet 5 (three unit tests) → Tasks 1, 3, 4.
- Spec § "Out of scope" — no plan tasks (correctly absent).
- Spec § "User contract" — exercised end-to-end by Task 5's tests + Task 5.3's full-suite run.
- Spec § "Design > `_start_observer_or_exit`" → Task 2.3; § "`daemon.run` refactor" → Task 2.4; § "Log message constants" → Task 2.2; § "README subsection" → Task 5.2.
- Spec § "Test plan" — Test 1 = Task 1, Test 2 = Task 3, Test 3 = Task 4. Coverage gap acknowledgment is in the spec, not actionable in the plan.
- Spec § "Risks and edge cases" — covered by the test fixtures (debouncer.calls assertion proves outer finally runs; `_configured_logging` dependency is documented in the helper docstring per Step 2.3); the existing-silent-return deviation is documented in the helper docstring per Step 2.3.
- Spec § "Backlog interactions" → Task 6.

No placeholders. No "TODO". Code blocks are concrete in every step that introduces or modifies code.

Type/method consistency: `_start_observer_or_exit(observer)` is called identically in Step 2.4 and tested via `daemon.run()` indirection in all three tests. `_FakeObserver.start()` raises if `start_error` is set; `_FakeDebouncer.calls` is the contract the tests assert. Names align across tasks.

Scope check: all three test cases plus the README plus the BACKLOG update fit one PR. The two-commit split (code+tests+README, then BACKLOG) follows project precedent (PR #4 template per CLAUDE.md).

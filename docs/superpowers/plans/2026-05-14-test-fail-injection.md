# DBXIGNORE_TEST_FAIL_* Failure-Injection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a shared `DBXIGNORE_TEST_FAIL_*` env-var failure-injection mechanism plus four production-code hook sites, so the manual-test shell scripts can drive four exit-2 error paths that unit tests already pin but a healthy machine cannot otherwise force — closing BACKLOG #121, #127, #128, #129.

**Architecture:** A new `src/dbxignore/_testing.py` module exposes two primitives — `raise_if_fail_point(name, exc=None)` for boundaries that fail by raising `OSError`, and `fail_point_active(name)` for boundaries that fail by substituting a value. Both read `os.environ["DBXIGNORE_TEST_FAIL_<name>"]`, are inert when unset, and log a WARNING when they fire. Four call sites (`cli._walk_marked_paths`, `cli._purge_dir`, `macos_launchd.uninstall_agent`, `cli.uninstall`'s `--purge` gate) each add a one-line hook that triggers an *already-implemented, already-unit-tested* exit-2 path. The manual-test scripts gain cases that set the env var, assert exit 2 + stderr, and recover.

**Tech Stack:** Python 3.12+, `uv`, `pytest`, `ruff`, `mypy`, `rich-click`. Manual-test scripts are bash (`scripts/_phase_extended_cli.sh`, `manual-test-{ubuntu-vps,macos}.sh`) and PowerShell 7 (`manual-test-windows.ps1`).

**Reference spec:** `docs/superpowers/specs/2026-05-14-test-fail-injection-design.md`

---

## File Structure

**Created:**
- `src/dbxignore/_testing.py` — the failure-injection primitives + a module docstring enumerating all four fail points. One responsibility: env-var-keyed failure injection. ~70 lines.
- `tests/test_testing.py` — unit tests for the two primitives only (not the boundary contracts, which are already tested elsewhere).

**Modified:**
- `src/dbxignore/cli.py` — add `_testing` to the package import (line 17); hook `MARKER_READ` into `_walk_marked_paths` (3 reads: cli.py:924, 937, 948); hook `STATE_PURGE` into `_purge_dir`'s unlink loop (cli.py:71-72); hook `DAEMON_ALIVE` into `uninstall`'s `--purge` gate (cli.py:1762).
- `src/dbxignore/install/macos_launchd.py` — add `_testing` import; hook `BOOTOUT` into `uninstall_agent` after `subprocess.run` (macos_launchd.py:~208).
- `scripts/_phase_extended_cli.sh` — new Phase 4.5 case `4u` (after `4t`, before the closing `}` at line 387) for the `MARKER_READ` path. Shared by the two bash scripts.
- `scripts/manual-test-windows.ps1` — new `Test-ExtendedCli` case `4v` (after `4u`, before the closing `}` at line 841); new `Test-Uninstall` cases for `STATE_PURGE` and `DAEMON_ALIVE`.
- `scripts/manual-test-ubuntu-vps.sh` — new `phase_uninstall` cases for `STATE_PURGE` and `DAEMON_ALIVE` (Phase 6).
- `scripts/manual-test-macos.sh` — new `phase_uninstall` cases for `STATE_PURGE`, `BOOTOUT` (macOS-only), and `DAEMON_ALIVE` (Phase 6).
- `AGENTS.md` — one Gotchas bullet naming `_testing.py` as the failure-injection convention home.
- `BACKLOG.md` — `Status: RESOLVED` markers on items #121/#127/#128/#129, four Resolved-section entries under a `#### 2026-05-14` heading, Open-list bullet removals, Open-count line `Nineteen` → `Fifteen`.

**Commit grouping (spec §"PR scope"):**
1. `feat(testing)` — `_testing.py` + `tests/test_testing.py` + the four hook sites (Tasks 1-6).
2. `test` — the manual-test script extensions (Tasks 7-9).
3. `docs` — AGENTS.md gotcha + BACKLOG closes (Task 10).

Branch `chore/test-fail-injection` already exists with the committed spec.

---

## Task 1: Create the `_testing.py` failure-injection module

**Files:**
- Create: `src/dbxignore/_testing.py`
- Test: `tests/test_testing.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_testing.py`:

```python
"""Unit tests for the DBXIGNORE_TEST_FAIL_* failure-injection primitives."""

from __future__ import annotations

import errno
import logging

import pytest

from dbxignore import _testing


def test_fail_point_active_false_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DBXIGNORE_TEST_FAIL_SAMPLE", raising=False)
    assert _testing.fail_point_active("SAMPLE") is False


def test_fail_point_active_true_and_warns_when_env_set(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DBXIGNORE_TEST_FAIL_SAMPLE", "1")
    with caplog.at_level(logging.WARNING, logger="dbxignore._testing"):
        assert _testing.fail_point_active("SAMPLE") is True
    assert any("SAMPLE" in r.message for r in caplog.records)


def test_fail_point_active_false_when_env_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicitly empty value is treated as unset — only a non-empty
    value arms the fail point, matching the manual-test scripts' `=1` form."""
    monkeypatch.setenv("DBXIGNORE_TEST_FAIL_SAMPLE", "")
    assert _testing.fail_point_active("SAMPLE") is False


def test_raise_if_fail_point_noop_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DBXIGNORE_TEST_FAIL_SAMPLE", raising=False)
    # Must not raise.
    _testing.raise_if_fail_point("SAMPLE")


def test_raise_if_fail_point_raises_default_enotsup_and_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DBXIGNORE_TEST_FAIL_SAMPLE", "1")
    with caplog.at_level(logging.WARNING, logger="dbxignore._testing"):
        with pytest.raises(OSError) as excinfo:
            _testing.raise_if_fail_point("SAMPLE")
    assert excinfo.value.errno == errno.ENOTSUP
    assert any("SAMPLE" in r.message for r in caplog.records)


def test_raise_if_fail_point_raises_supplied_exc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DBXIGNORE_TEST_FAIL_SAMPLE", "1")
    custom = OSError(errno.EIO, "custom injected error")
    with pytest.raises(OSError) as excinfo:
        _testing.raise_if_fail_point("SAMPLE", custom)
    assert excinfo.value is custom
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest tests/test_testing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dbxignore._testing'` (collection error).

- [ ] **Step 3: Create `src/dbxignore/_testing.py`**

```python
"""Failure-injection hooks for end-to-end manual tests.

The manual-test shell scripts (``scripts/manual-test-*.{sh,ps1}``) drive
the real ``dbxignore`` binary as a subprocess. Their only lever into that
subprocess is the environment, so exit-2 error paths whose underlying
failure mode cannot be forced on a healthy machine are exercised by
setting a ``DBXIGNORE_TEST_FAIL_<name>`` env var that production code
honors at a specific boundary.

Every hook is inert unless its env var is set to a non-empty value, and
logs a WARNING when it fires so a leaked env var is diagnosable rather
than a silent behavior change.

Fail points
-----------
- ``MARKER_READ``  — ``cli._walk_marked_paths`` raises ``OSError`` before
  every ``markers.is_ignored`` read. Drives the ``scan_errors`` exit-2
  path of ``clear`` / ``list`` (BACKLOG #121).
- ``STATE_PURGE``  — ``cli._purge_dir`` raises ``OSError`` before each
  ``f.unlink()``. Drives the ``state_errors`` exit-2 path of
  ``uninstall --purge`` (BACKLOG #127).
- ``BOOTOUT``      — ``install.macos_launchd.uninstall_agent`` treats the
  ``launchctl bootout`` result as a confirmed non-zero-rc failure. Drives
  the bootout exit-2 path of ``uninstall`` on macOS (BACKLOG #128).
- ``DAEMON_ALIVE`` — ``cli.uninstall``'s ``--purge`` daemon-alive gate
  fires as if a daemon survived service removal. Drives the daemon-alive
  purge-refusal exit-2 path (BACKLOG #129).

Test-only. Nothing outside the manual-test scripts and ``tests/`` should
set these env vars. New fail points are a one-liner here (a docstring
entry) plus a one-line hook at the boundary.
"""

from __future__ import annotations

import errno
import logging
import os

logger = logging.getLogger(__name__)

_ENV_PREFIX = "DBXIGNORE_TEST_FAIL_"


def _is_armed(name: str) -> bool:
    """True if ``DBXIGNORE_TEST_FAIL_<name>`` is set to a non-empty value."""
    return bool(os.environ.get(f"{_ENV_PREFIX}{name}"))


def fail_point_active(name: str) -> bool:
    """Return True if the ``name`` fail point is armed via the environment.

    For boundaries that inject failure by substituting a value (a
    subprocess return code, a boolean) rather than raising. Logs a
    WARNING when it returns True.
    """
    if _is_armed(name):
        logger.warning(
            "failure-injection fail point %r is active (%s%s is set)",
            name,
            _ENV_PREFIX,
            name,
        )
        return True
    return False


def raise_if_fail_point(name: str, exc: OSError | None = None) -> None:
    """Raise ``exc`` if the ``name`` fail point is armed via the environment.

    For boundaries that inject failure by raising ``OSError`` into an
    existing ``except OSError`` arm. The default exception is
    ``OSError(errno.ENOTSUP, ...)`` — the errno a filesystem without
    xattr/ADS support reports, which is the real-world failure mode the
    marker-read fail point simulates. Logs a WARNING before raising.
    """
    if not _is_armed(name):
        return
    logger.warning(
        "failure-injection fail point %r is active (%s%s is set); raising",
        name,
        _ENV_PREFIX,
        name,
    )
    if exc is None:
        exc = OSError(errno.ENOTSUP, f"injected failure ({_ENV_PREFIX}{name})")
    raise exc
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest tests/test_testing.py -v`
Expected: PASS — all 6 tests green.

- [ ] **Step 5: Lint and type-check the new module**

Run: `uv run ruff check src/dbxignore/_testing.py tests/test_testing.py && uv run ruff format --check src/dbxignore/_testing.py tests/test_testing.py && uv run mypy src/dbxignore/_testing.py`
Expected: all pass, no diagnostics.

(No commit yet — `_testing.py` is dead code until the hooks land. Commit 1 bundles this with Tasks 2-5 per the spec's revertability grouping.)

---

## Task 2: Hook `MARKER_READ` into `cli._walk_marked_paths`

**Files:**
- Modify: `src/dbxignore/cli.py:17` (import), `src/dbxignore/cli.py:923-948` (three hook insertions)

`_walk_marked_paths` reads `markers.is_ignored` at three sites — the `target` read before the walk (cli.py:924), the `dirnames`-loop read (cli.py:937), and the `filenames`-loop read (cli.py:948). All three are already inside `try: ... except OSError` arms. The hook fires before each read so a `clear <single-marked-file>` (which returns at the `target` read before the walk begins) injects just as reliably as a `clear <dir-tree>`.

- [ ] **Step 1: Add the `_testing` import**

In `src/dbxignore/cli.py`, change line 17 from:

```python
from dbxignore import markers, reconcile, roots, rules, state
```

to:

```python
from dbxignore import _testing, markers, reconcile, roots, rules, state
```

- [ ] **Step 2: Hook the `target` read**

In `src/dbxignore/cli.py`, the current block at lines 922-928 is:

```python
    found: list[Path] = []
    errors: list[tuple[Path, str]] = []
    try:
        if markers.is_ignored(target):
            return [target], errors
    except OSError as exc:
        errors.append((target, str(exc)))
        return found, errors
```

Change the `try` body to:

```python
    found: list[Path] = []
    errors: list[tuple[Path, str]] = []
    try:
        _testing.raise_if_fail_point("MARKER_READ")
        if markers.is_ignored(target):
            return [target], errors
    except OSError as exc:
        errors.append((target, str(exc)))
        return found, errors
```

- [ ] **Step 3: Hook the `dirnames`-loop read**

In `src/dbxignore/cli.py`, the current block at lines 934-943 is:

```python
        for name in dirnames:
            p = current_path / name
            try:
                if markers.is_ignored(p):
                    found.append(p)
                else:
                    kept_dirs.append(name)
            except OSError as exc:
                errors.append((p, str(exc)))
                kept_dirs.append(name)
```

Change the `try` body to:

```python
        for name in dirnames:
            p = current_path / name
            try:
                _testing.raise_if_fail_point("MARKER_READ")
                if markers.is_ignored(p):
                    found.append(p)
                else:
                    kept_dirs.append(name)
            except OSError as exc:
                errors.append((p, str(exc)))
                kept_dirs.append(name)
```

- [ ] **Step 4: Hook the `filenames`-loop read**

In `src/dbxignore/cli.py`, the current block at lines 945-951 is:

```python
        for name in filenames:
            p = current_path / name
            try:
                if markers.is_ignored(p):
                    found.append(p)
            except OSError as exc:
                errors.append((p, str(exc)))
```

Change the `try` body to:

```python
        for name in filenames:
            p = current_path / name
            try:
                _testing.raise_if_fail_point("MARKER_READ")
                if markers.is_ignored(p):
                    found.append(p)
            except OSError as exc:
                errors.append((p, str(exc)))
```

- [ ] **Step 5: Smoke-check the hook fires (throwaway, not committed)**

Run:

```bash
uv run python -c "
import os, tempfile, pathlib
os.environ['DBXIGNORE_TEST_FAIL_MARKER_READ'] = '1'
from dbxignore import cli
d = pathlib.Path(tempfile.mkdtemp())
found, errs = cli._walk_marked_paths(d)
assert found == [] and len(errs) == 1, (found, errs)
print('MARKER_READ hook OK:', errs[0][1])
"
```

Expected: `MARKER_READ hook OK: [Errno 95] injected failure (DBXIGNORE_TEST_FAIL_MARKER_READ): ...` (errno text may vary by platform; the key is `found == []` and exactly one error).

- [ ] **Step 6: Verify existing `clear` / `list` tests still pass**

Run: `uv run python -m pytest tests/test_cli_clear.py tests/test_cli_status_list_explain.py -q`
Expected: PASS — the hook is inert without the env var, so no existing test changes behavior.

(No commit yet — continues into commit 1.)

---

## Task 3: Hook `STATE_PURGE` into `cli._purge_dir`

**Files:**
- Modify: `src/dbxignore/cli.py:69-78` (hook insertion in the unlink loop)

`_purge_dir`'s `f.unlink()` is already inside a `try: ... except FileNotFoundError ... except OSError` arm. The `except OSError` arm appends to `errors` when the caller supplied the list — `uninstall --purge` passes `state_errors`, which drives its exit-2 gate.

- [ ] **Step 1: Hook the unlink loop**

In `src/dbxignore/cli.py`, the current block at lines 69-78 is:

```python
    for pattern in patterns:
        for f in dir_path.glob(pattern):
            try:
                f.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning("could not remove %s: %s", f, exc)
                if errors is not None:
                    errors.append((f, str(exc)))
```

Change the `try` body to:

```python
    for pattern in patterns:
        for f in dir_path.glob(pattern):
            try:
                _testing.raise_if_fail_point("STATE_PURGE")
                f.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning("could not remove %s: %s", f, exc)
                if errors is not None:
                    errors.append((f, str(exc)))
```

(The `_testing` import was already added in Task 2 Step 1.)

- [ ] **Step 2: Smoke-check the hook fires (throwaway, not committed)**

Run:

```bash
uv run python -c "
import os, tempfile, pathlib
os.environ['DBXIGNORE_TEST_FAIL_STATE_PURGE'] = '1'
from dbxignore import cli
d = pathlib.Path(tempfile.mkdtemp())
(d / 'state.json').write_text('{}')
errs = []
cli._purge_dir(d, ['state.json'], errors=errs)
assert len(errs) == 1, errs
assert (d / 'state.json').exists(), 'file should survive the injected failure'
print('STATE_PURGE hook OK:', errs[0][1])
"
```

Expected: `STATE_PURGE hook OK: [Errno 95] injected failure (DBXIGNORE_TEST_FAIL_STATE_PURGE): ...` — one error recorded, the file untouched.

- [ ] **Step 3: Verify existing install/uninstall tests still pass**

Run: `uv run python -m pytest tests/test_install.py -q`
Expected: PASS — hook inert without the env var.

(No commit yet — continues into commit 1.)

---

## Task 4: Hook `BOOTOUT` into `macos_launchd.uninstall_agent`

**Files:**
- Modify: `src/dbxignore/install/macos_launchd.py:22-23` (import), `src/dbxignore/install/macos_launchd.py:~208` (hook after `subprocess.run`)

`uninstall_agent` runs `launchctl bootout` via `subprocess.run`, then branches on `result.returncode`. The hook substitutes a synthetic non-zero-rc `CompletedProcess` whose stderr does NOT match `_NOT_LOADED_STDERR_PATTERNS`, so the existing `if not _is_service_not_loaded(stderr): raise RuntimeError(...)` arm fires — which `cli.uninstall` turns into exit 2.

- [ ] **Step 1: Add the `_testing` import**

In `src/dbxignore/install/macos_launchd.py`, the current import block at lines 22-23 is:

```python
from dbxignore import state as state_module
from dbxignore.install._common import detect_invocation
```

Change it to:

```python
from dbxignore import _testing
from dbxignore import state as state_module
from dbxignore.install._common import detect_invocation
```

(Two `from dbxignore import` lines because `import state as state_module` is an aliased import — ruff's isort keeps an aliased import on its own line rather than merging it with `_testing`. If `ruff check` reports an `I001` reordering, accept its `--fix` output.)

- [ ] **Step 2: Hook the bootout result**

In `src/dbxignore/install/macos_launchd.py`, the current block at lines 199-208 (the `try`/`except` around `subprocess.run`, immediately followed by `if result.returncode != 0:`) is:

```python
    try:
        result = subprocess.run(  # noqa: S603 — hardcoded args, no user data
            ["launchctl", "bootout", _service_target()],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise RuntimeError(f"launchctl bootout could not be invoked: {exc}") from exc
    if result.returncode != 0:
```

Insert the hook between the `except` block and the `if result.returncode != 0:` line:

```python
    try:
        result = subprocess.run(  # noqa: S603 — hardcoded args, no user data
            ["launchctl", "bootout", _service_target()],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise RuntimeError(f"launchctl bootout could not be invoked: {exc}") from exc
    if _testing.fail_point_active("BOOTOUT"):
        # Substitute a confirmed-failure result: non-zero rc with stderr
        # that `_is_service_not_loaded` does NOT match, so the arm below
        # raises RuntimeError instead of treating it as idempotent success.
        result = subprocess.CompletedProcess(
            result.args,
            returncode=5,
            stdout="",
            stderr="injected bootout failure (DBXIGNORE_TEST_FAIL_BOOTOUT)",
        )
    if result.returncode != 0:
```

- [ ] **Step 3: Smoke-check the hook fires — macOS only (throwaway, not committed)**

On macOS, run:

```bash
uv run python -c "
import os
os.environ['DBXIGNORE_TEST_FAIL_BOOTOUT'] = '1'
from dbxignore.install import macos_launchd
try:
    macos_launchd.uninstall_agent()
    print('FAIL: expected RuntimeError')
except RuntimeError as exc:
    assert 'launchctl bootout returned 5' in str(exc), exc
    print('BOOTOUT hook OK:', exc)
"
```

Expected: `BOOTOUT hook OK: launchctl bootout returned 5: injected bootout failure (DBXIGNORE_TEST_FAIL_BOOTOUT)`.

On non-macOS (the module's `subprocess` call would fail before the hook, and `uninstall_agent` is macOS-only), skip the smoke check — verify instead that the diff exactly matches Steps 1-2 and that `uv run mypy src/dbxignore/install/macos_launchd.py` passes (mypy runs cross-platform). Note this limitation in the eventual commit if running on non-macOS.

- [ ] **Step 4: Verify existing macOS launchd tests still pass**

Run: `uv run python -m pytest tests/test_macos_launchd.py -q`
Expected: PASS — hook inert without the env var. (These tests are not `macos_only`-marked; they run cross-platform via subprocess stubbing.)

(No commit yet — continues into commit 1.)

---

## Task 5: Hook `DAEMON_ALIVE` into `cli.uninstall`'s `--purge` gate

**Files:**
- Modify: `src/dbxignore/cli.py:1762` (hook at the top of the `--purge` daemon-alive gate)

The `if purge:` block opens with a daemon-alive gate (cli.py:1762-1772): two probes, each calling `_refuse_purge_daemon_alive(...)` which prints `error: dbxignore daemon is running...` to stderr and `sys.exit(2)`. The hook adds a third trigger at the top of the gate, before `state.read()`, so the refusal fires regardless of real daemon state.

- [ ] **Step 1: Hook the gate**

In `src/dbxignore/cli.py`, the current block starting at line 1762 is:

```python
        s_for_guard = state.read()
        if s_for_guard is not None and state.daemon_is_running(s_for_guard):
            _refuse_purge_daemon_alive(
                f" (pid={s_for_guard.daemon_pid})",
                f"taskkill /F /PID {s_for_guard.daemon_pid}",
            )
        if state.is_any_daemon_running():
            _refuse_purge_daemon_alive(
                " (daemon.lock is held; PID unknown — state.json absent or unreadable)",
                "tasklist | findstr dbxignore  # to find pid, then taskkill /F /PID <pid>",
            )
```

Insert the hook as the first statement of the gate:

```python
        if _testing.fail_point_active("DAEMON_ALIVE"):
            _refuse_purge_daemon_alive(
                " (DBXIGNORE_TEST_FAIL_DAEMON_ALIVE injected)",
                "unset DBXIGNORE_TEST_FAIL_DAEMON_ALIVE",
            )
        s_for_guard = state.read()
        if s_for_guard is not None and state.daemon_is_running(s_for_guard):
            _refuse_purge_daemon_alive(
                f" (pid={s_for_guard.daemon_pid})",
                f"taskkill /F /PID {s_for_guard.daemon_pid}",
            )
        if state.is_any_daemon_running():
            _refuse_purge_daemon_alive(
                " (daemon.lock is held; PID unknown — state.json absent or unreadable)",
                "tasklist | findstr dbxignore  # to find pid, then taskkill /F /PID <pid>",
            )
```

(The `_testing` import was already added in Task 2 Step 1.)

- [ ] **Step 2: Smoke-check the hook fires (throwaway, not committed)**

Run:

```bash
uv run python -c "
import os
os.environ['DBXIGNORE_TEST_FAIL_DAEMON_ALIVE'] = '1'
from click.testing import CliRunner
from dbxignore import cli
res = CliRunner().invoke(cli.main, ['uninstall', '--purge'])
assert res.exit_code == 2, (res.exit_code, res.output)
assert 'daemon is running' in res.output, res.output
print('DAEMON_ALIVE hook OK: exit 2, refusal printed')
"
```

Expected: `DAEMON_ALIVE hook OK: exit 2, refusal printed`. (`uninstall_service()` runs first and is expected to succeed or no-op in the test environment; if it raises `RuntimeError` the command exits 2 before reaching the gate — if the smoke check fails on `uninstall_service`, run it on a machine where `dbxignore` is installed, or trust the manual-test case in Task 8.)

- [ ] **Step 3: Verify existing install/uninstall tests still pass**

Run: `uv run python -m pytest tests/test_install.py -q`
Expected: PASS — hook inert without the env var.

(No commit yet — Task 6 commits.)

---

## Task 6: Full check suite + commit 1 (`feat(testing)`)

**Files:** none (verification + commit only)

- [ ] **Step 1: Run the full check suite**

Run:

```bash
uv run mypy . && uv run ruff check . && uv run ruff format --check . && uv run python -m pytest -q
```

Expected: mypy clean, ruff clean, format clean, full pytest suite PASS. If `ruff format --check` reports diffs, run `uv run ruff format .` and re-run the check.

- [ ] **Step 2: Stage and commit**

```bash
git add src/dbxignore/_testing.py tests/test_testing.py src/dbxignore/cli.py src/dbxignore/install/macos_launchd.py
git commit -m "$(cat <<'EOF'
feat(testing): add DBXIGNORE_TEST_FAIL_* failure-injection helpers

New _testing.py module with raise_if_fail_point / fail_point_active
primitives, plus four production-code hook sites that trigger
already-tested exit-2 paths the manual-test scripts otherwise cannot
reach: MARKER_READ in _walk_marked_paths, STATE_PURGE in _purge_dir,
BOOTOUT in macos_launchd.uninstall_agent, DAEMON_ALIVE in the
uninstall --purge daemon-alive gate. Hooks are inert unless their env
var is set and log a WARNING when they fire.

Groundwork for closing BACKLOG #121, #127, #128, #129.
EOF
)"
```

- [ ] **Step 3: Verify the commit message passes commit-check**

Run: `git log -1 --format=%s%n%n%b | head -1`
Expected: subject `feat(testing): add DBXIGNORE_TEST_FAIL_* failure-injection helpers` — single scope, no leading `#`, under the `cchk.toml` length cap. If a local `pre-commit` commit-msg hook is installed it already validated this; the PR's `commit-check` CI re-runs it.

---

## Task 7: Manual-test case `4u` (bash) / `4v` (Windows) — `MARKER_READ`

**Files:**
- Modify: `scripts/_phase_extended_cli.sh` (new case `4u`, inserted before the closing `}` at line 387)
- Modify: `scripts/manual-test-windows.ps1` (new case `4v`, inserted before the closing `}` of `Test-ExtendedCli` at line 841)

Case-letter note: the bash helper's last Phase 4.5 case is `4t`, so the new bash case is `4u`. The Windows script already has a Windows-specific `4u` (the post-#238 dual-binary `--help` sync check), so its new case is `4v`. Both carry the same `(PR #<THIS_PR>, item #121)` provenance.

- [ ] **Step 1: Add case `4u` to the bash helper**

In `scripts/_phase_extended_cli.sh`, immediately before the closing `}` on line 387 (after the `4t` `assert_grep` at line 385-386), insert:

```bash

    # 4u — clear/list exit 2 on injected marker-read failure (PR #<THIS_PR>, item #121)
    # DBXIGNORE_TEST_FAIL_MARKER_READ makes markers.is_ignored raise OSError
    # inside _walk_marked_paths, exercising the scan_errors exit-2 path that
    # unit tests pin but a healthy filesystem can't otherwise trigger. The
    # injected runs mutate nothing (clear refuses once the scan fails), so the
    # only recovery needed is a plain clear to leave the tree clean.
    note "4u — clear/list exit 2 on injected marker-read failure"
    rm -rf "$T"; mkdir -p "$T"
    printf '*.tmp\n' > "$T/.dropboxignore"
    : > "$T/foo.tmp"
    dbxignore apply "$T" --yes >/dev/null 2>&1

    local clear_fail_rc
    if DBXIGNORE_TEST_FAIL_MARKER_READ=1 dbxignore clear "$T" --yes \
        >/tmp/dbx-4u-clear.out 2>/tmp/dbx-4u-clear.err; then
        clear_fail_rc=0
    else
        clear_fail_rc=$?
    fi
    if [ "$clear_fail_rc" -eq 2 ]; then
        pass "4u — clear exits 2 on injected marker-read failure"
    else
        fail "4u — clear exited $clear_fail_rc instead of 2"
        sed 's/^/    /' /tmp/dbx-4u-clear.err
    fi
    assert_grep /tmp/dbx-4u-clear.err 'scan error' "4u — clear stderr reports scan errors"

    local list_fail_rc
    if DBXIGNORE_TEST_FAIL_MARKER_READ=1 dbxignore list "$T" \
        >/tmp/dbx-4u-list.out 2>/tmp/dbx-4u-list.err; then
        list_fail_rc=0
    else
        list_fail_rc=$?
    fi
    if [ "$list_fail_rc" -eq 2 ]; then
        pass "4u — list exits 2 on injected marker-read failure"
    else
        fail "4u — list exited $list_fail_rc instead of 2"
        sed 's/^/    /' /tmp/dbx-4u-list.err
    fi
    assert_grep /tmp/dbx-4u-list.err 'scan error' "4u — list stderr reports scan errors"

    # Recovery: clear the marker without the fail point so later phases start
    # from a clean tree.
    dbxignore clear "$T" --yes >/dev/null 2>&1
    rm -rf "$T"
```

- [ ] **Step 2: Add case `4v` to the Windows script**

In `scripts/manual-test-windows.ps1`, immediately before the closing `}` of `Test-ExtendedCli` on line 841 (after the `4u` `--help` block ending line 840), insert:

```powershell

    # 4v - clear/list exit 2 on injected marker-read failure (PR #<THIS_PR>, item #121)
    # DBXIGNORE_TEST_FAIL_MARKER_READ makes markers.is_ignored raise OSError
    # inside _walk_marked_paths, exercising the scan_errors exit-2 path that
    # unit tests pin but a healthy filesystem can't otherwise trigger. PowerShell
    # has no inline env-var prefix, so the var is set then removed around each
    # invocation. The injected runs mutate nothing (clear refuses once the scan
    # fails), so recovery is a plain clear.
    Write-Note "4v - clear/list exit 2 on injected marker-read failure"
    Remove-Item -Recurse -Force $T -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $T | Out-Null
    Set-Content -Path (Join-Path $T ".dropboxignore") -Value "*.tmp" -NoNewline
    New-Item -ItemType File -Force -Path (Join-Path $T "foo.tmp") | Out-Null
    dbxignore apply $T --yes *> $null

    $clear4vErr = Join-Path $env:TEMP "dbx-4v-clear.err"
    $env:DBXIGNORE_TEST_FAIL_MARKER_READ = "1"
    & dbxignore clear $T --yes *> $clear4vErr
    $clear4vRc = $LASTEXITCODE
    Remove-Item Env:\DBXIGNORE_TEST_FAIL_MARKER_READ
    if ($clear4vRc -eq 2) {
        Write-Pass "4v - clear exits 2 on injected marker-read failure"
    } else {
        Write-Fail "4v - clear exited $clear4vRc instead of 2"
        if (Test-Path $clear4vErr) { Get-Content $clear4vErr | ForEach-Object { Write-Note "    $_" } }
    }
    $clear4vText = if (Test-Path $clear4vErr) { Get-Content $clear4vErr -Raw } else { "" }
    if ($clear4vText -match 'scan error') {
        Write-Pass "4v - clear stderr reports scan errors"
    } else {
        Write-Fail "4v - clear stderr missing 'scan error'"
    }

    $list4vErr = Join-Path $env:TEMP "dbx-4v-list.err"
    $env:DBXIGNORE_TEST_FAIL_MARKER_READ = "1"
    & dbxignore list $T *> $list4vErr
    $list4vRc = $LASTEXITCODE
    Remove-Item Env:\DBXIGNORE_TEST_FAIL_MARKER_READ
    if ($list4vRc -eq 2) {
        Write-Pass "4v - list exits 2 on injected marker-read failure"
    } else {
        Write-Fail "4v - list exited $list4vRc instead of 2"
        if (Test-Path $list4vErr) { Get-Content $list4vErr | ForEach-Object { Write-Note "    $_" } }
    }
    $list4vText = if (Test-Path $list4vErr) { Get-Content $list4vErr -Raw } else { "" }
    if ($list4vText -match 'scan error') {
        Write-Pass "4v - list stderr reports scan errors"
    } else {
        Write-Fail "4v - list stderr missing 'scan error'"
    }

    # Recovery: clear the marker without the fail point so later phases start clean.
    dbxignore clear $T --yes *> $null
    Remove-Item -Recurse -Force $T -ErrorAction SilentlyContinue
```

- [ ] **Step 3: Syntax-check both scripts**

Run: `bash -n scripts/_phase_extended_cli.sh && pwsh -NoProfile -Command "& { \$null = [System.Management.Automation.Language.Parser]::ParseFile('scripts/manual-test-windows.ps1', [ref]\$null, [ref]\$null); 'ps parse OK' }"`
Expected: no bash syntax error; `ps parse OK`. (If `pwsh` is unavailable on the executor's platform, skip the PowerShell parse and rely on the Windows CI / manual-test run; note the limitation.)

(No commit yet — Task 9 commits the manual-test changes together.)

---

## Task 8: Manual-test Phase 6 cases — `STATE_PURGE`, `BOOTOUT`, `DAEMON_ALIVE`

**Files:**
- Modify: `scripts/manual-test-ubuntu-vps.sh` (`phase_uninstall` — `STATE_PURGE` + `DAEMON_ALIVE` cases)
- Modify: `scripts/manual-test-macos.sh` (`phase_uninstall` — `STATE_PURGE` + `BOOTOUT` + `DAEMON_ALIVE` cases)
- Modify: `scripts/manual-test-windows.ps1` (`Test-Uninstall` — `STATE_PURGE` + `DAEMON_ALIVE` cases)

Per the spec, every Phase 6 injected case re-installs first (the prior happy-path `--purge` left nothing registered and no state files), then injects → asserts exit 2 + stderr → recovers with a clean re-run. The cases land at the END of each `phase_uninstall` / `Test-Uninstall` function, after all existing cases, so they don't disturb the existing end-state assertions. `BOOTOUT` is macOS-only.

### 8a — ubuntu-vps.sh

- [ ] **Step 1: Locate the insertion point**

In `scripts/manual-test-ubuntu-vps.sh`, find the closing `}` of `phase_uninstall()` (the function starts at line 721). The new cases go immediately before that closing `}`, after the last existing case.

- [ ] **Step 2: Insert the `STATE_PURGE` and `DAEMON_ALIVE` cases**

Immediately before the closing `}` of `phase_uninstall()`, insert:

```bash

    # 6f — uninstall --purge exits 2 on injected state-file purge failure (PR #<THIS_PR>, item #127)
    # DBXIGNORE_TEST_FAIL_STATE_PURGE makes _purge_dir's unlink loop raise
    # OSError, exercising the state_errors exit-2 path. Re-install first so the
    # daemon writes state.json / daemon.lock — _purge_dir only injects inside
    # its f.unlink() loop, so with an empty state dir there'd be nothing to
    # fail on. Markers ARE cleared (the failure is in the later state-dir step);
    # recovery is a clean --purge re-run.
    note "6f — uninstall --purge exits 2 on injected state-purge failure"
    dbxignore install >/dev/null 2>&1 || abort "6f re-install failed"
    sleep 2
    local purge_fail_rc
    if DBXIGNORE_TEST_FAIL_STATE_PURGE=1 dbxignore uninstall --purge \
        >/tmp/dbx-6f-purge.out 2>&1; then
        purge_fail_rc=0
    else
        purge_fail_rc=$?
    fi
    if [ "$purge_fail_rc" -eq 2 ]; then
        pass "6f — uninstall --purge exits 2 on injected state-purge failure"
    else
        fail "6f — uninstall --purge exited $purge_fail_rc instead of 2"
        sed 's/^/    /' /tmp/dbx-6f-purge.out
    fi
    assert_grep /tmp/dbx-6f-purge.out 'Could not fully purge state files' \
        "6f — purge stderr reports the state-file failure"
    # Recovery: clean --purge to remove the state files the injected run left.
    dbxignore uninstall --purge >/dev/null 2>&1 || true

    # 6g — uninstall --purge exits 2 on injected daemon-alive guard (PR #<THIS_PR>, item #129)
    # DBXIGNORE_TEST_FAIL_DAEMON_ALIVE fires the --purge daemon-alive gate as if
    # a daemon survived service removal. The gate fires BEFORE the purge body,
    # so nothing is cleared; recovery is a clean --purge re-run.
    note "6g — uninstall --purge exits 2 on injected daemon-alive guard"
    dbxignore install >/dev/null 2>&1 || abort "6g re-install failed"
    sleep 2
    local alive_fail_rc
    if DBXIGNORE_TEST_FAIL_DAEMON_ALIVE=1 dbxignore uninstall --purge \
        >/tmp/dbx-6g-purge.out 2>&1; then
        alive_fail_rc=0
    else
        alive_fail_rc=$?
    fi
    if [ "$alive_fail_rc" -eq 2 ]; then
        pass "6g — uninstall --purge exits 2 on injected daemon-alive guard"
    else
        fail "6g — uninstall --purge exited $alive_fail_rc instead of 2"
        sed 's/^/    /' /tmp/dbx-6g-purge.out
    fi
    assert_grep /tmp/dbx-6g-purge.out 'daemon is running' \
        "6g — purge stderr reports the daemon-alive refusal"
    # Recovery: clean --purge (the gate fired before any cleanup ran).
    dbxignore uninstall --purge >/dev/null 2>&1 || true
```

(Case letters `6f`/`6g` follow the existing ubuntu Phase 6 cases `6a` and the re-install/purge block; if the executor finds the ubuntu script already uses `6f`+, bump to the next free letters and keep the `(PR #<THIS_PR>, item #NNN)` provenance — the BACKLOG item number is the stable cross-reference, not the case letter.)

### 8b — macos.sh

- [ ] **Step 3: Insert the `STATE_PURGE`, `BOOTOUT`, and `DAEMON_ALIVE` cases**

In `scripts/manual-test-macos.sh`, immediately before the closing `}` of `phase_uninstall()` (function starts at line 646), insert the same `STATE_PURGE` and `DAEMON_ALIVE` cases as in Step 2 **plus** a `BOOTOUT` case. Use the next free case letters after the existing macOS Phase 6 cases (the macOS script already has `6b` and `6d` — use `6e`/`6f`/`6g` or the next free letters; provenance is keyed on the item number). Insert:

```bash

    # 6e — uninstall exits 2 on injected launchctl bootout failure (PR #<THIS_PR>, item #128)
    # DBXIGNORE_TEST_FAIL_BOOTOUT makes uninstall_agent treat the bootout result
    # as a confirmed non-zero-rc failure (stderr that _is_service_not_loaded
    # does NOT match), so uninstall_agent raises RuntimeError → cli.uninstall
    # exits 2. macOS-only: launchctl bootout is the macOS daemon-shutdown step.
    # The plist is preserved and the daemon stays registered; recovery is a
    # clean uninstall re-run.
    note "6e — uninstall exits 2 on injected launchctl bootout failure"
    dbxignore install >/dev/null 2>&1 || abort "6e re-install failed"
    sleep 2
    local bootout_fail_rc
    if DBXIGNORE_TEST_FAIL_BOOTOUT=1 dbxignore uninstall \
        >/tmp/dbx-6e-uninstall.out 2>&1; then
        bootout_fail_rc=0
    else
        bootout_fail_rc=$?
    fi
    if [ "$bootout_fail_rc" -eq 2 ]; then
        pass "6e — uninstall exits 2 on injected bootout failure"
    else
        fail "6e — uninstall exited $bootout_fail_rc instead of 2"
        sed 's/^/    /' /tmp/dbx-6e-uninstall.out
    fi
    assert_grep /tmp/dbx-6e-uninstall.out 'launchctl bootout returned' \
        "6e — uninstall stderr reports the bootout failure"
    # Recovery: clean uninstall (the plist + registration survived the injected run).
    dbxignore uninstall >/dev/null 2>&1 || true

    # 6f — uninstall --purge exits 2 on injected state-file purge failure (PR #<THIS_PR>, item #127)
    # DBXIGNORE_TEST_FAIL_STATE_PURGE makes _purge_dir's unlink loop raise
    # OSError, exercising the state_errors exit-2 path. Re-install first so the
    # daemon writes state.json / daemon.lock — _purge_dir only injects inside
    # its f.unlink() loop. Markers ARE cleared (failure is in the later
    # state-dir step); recovery is a clean --purge re-run.
    note "6f — uninstall --purge exits 2 on injected state-purge failure"
    dbxignore install >/dev/null 2>&1 || abort "6f re-install failed"
    sleep 2
    local purge_fail_rc
    if DBXIGNORE_TEST_FAIL_STATE_PURGE=1 dbxignore uninstall --purge \
        >/tmp/dbx-6f-purge.out 2>&1; then
        purge_fail_rc=0
    else
        purge_fail_rc=$?
    fi
    if [ "$purge_fail_rc" -eq 2 ]; then
        pass "6f — uninstall --purge exits 2 on injected state-purge failure"
    else
        fail "6f — uninstall --purge exited $purge_fail_rc instead of 2"
        sed 's/^/    /' /tmp/dbx-6f-purge.out
    fi
    assert_grep /tmp/dbx-6f-purge.out 'Could not fully purge state files' \
        "6f — purge stderr reports the state-file failure"
    dbxignore uninstall --purge >/dev/null 2>&1 || true

    # 6g — uninstall --purge exits 2 on injected daemon-alive guard (PR #<THIS_PR>, item #129)
    # DBXIGNORE_TEST_FAIL_DAEMON_ALIVE fires the --purge daemon-alive gate as if
    # a daemon survived service removal. The gate fires BEFORE the purge body,
    # so nothing is cleared; recovery is a clean --purge re-run.
    note "6g — uninstall --purge exits 2 on injected daemon-alive guard"
    dbxignore install >/dev/null 2>&1 || abort "6g re-install failed"
    sleep 2
    local alive_fail_rc
    if DBXIGNORE_TEST_FAIL_DAEMON_ALIVE=1 dbxignore uninstall --purge \
        >/tmp/dbx-6g-purge.out 2>&1; then
        alive_fail_rc=0
    else
        alive_fail_rc=$?
    fi
    if [ "$alive_fail_rc" -eq 2 ]; then
        pass "6g — uninstall --purge exits 2 on injected daemon-alive guard"
    else
        fail "6g — uninstall --purge exited $alive_fail_rc instead of 2"
        sed 's/^/    /' /tmp/dbx-6g-purge.out
    fi
    assert_grep /tmp/dbx-6g-purge.out 'daemon is running' \
        "6g — purge stderr reports the daemon-alive refusal"
    dbxignore uninstall --purge >/dev/null 2>&1 || true
```

### 8c — windows.ps1

- [ ] **Step 4: Insert the `STATE_PURGE` and `DAEMON_ALIVE` cases**

In `scripts/manual-test-windows.ps1`, find the closing `}` of `Test-Uninstall` (the function starts at line 1347; the last existing case is `6f` ending around line 1650). Immediately before that closing `}`, insert:

```powershell

    # 6g - uninstall --purge exits 2 on injected state-file purge failure (PR #<THIS_PR>, item #127)
    # DBXIGNORE_TEST_FAIL_STATE_PURGE makes _purge_dir's unlink loop raise
    # OSError, exercising the state_errors exit-2 path. Re-install first so the
    # daemon writes state.json / daemon.lock. Markers ARE cleared (the failure
    # is in the later state-dir step); recovery is a clean --purge re-run.
    Write-Note "6g - uninstall --purge exits 2 on injected state-purge failure"
    dbxignore install *> $null
    if ($LASTEXITCODE -ne 0) { Stop-Abort "6g re-install failed" }
    Start-Sleep -Seconds 2
    $purge6gOut = Join-Path $env:TEMP "dbx-6g-purge.out"
    $env:DBXIGNORE_TEST_FAIL_STATE_PURGE = "1"
    & dbxignore uninstall --purge *> $purge6gOut
    $purge6gRc = $LASTEXITCODE
    Remove-Item Env:\DBXIGNORE_TEST_FAIL_STATE_PURGE
    if ($purge6gRc -eq 2) {
        Write-Pass "6g - uninstall --purge exits 2 on injected state-purge failure"
    } else {
        Write-Fail "6g - uninstall --purge exited $purge6gRc instead of 2"
        if (Test-Path $purge6gOut) { Get-Content $purge6gOut | ForEach-Object { Write-Note "    $_" } }
    }
    $purge6gText = if (Test-Path $purge6gOut) { Get-Content $purge6gOut -Raw } else { "" }
    if ($purge6gText -match 'Could not fully purge state files') {
        Write-Pass "6g - purge stderr reports the state-file failure"
    } else {
        Write-Fail "6g - purge stderr missing 'Could not fully purge state files'"
    }
    # Recovery: clean --purge to remove the state files the injected run left.
    dbxignore uninstall --purge *> $null

    # 6h - uninstall --purge exits 2 on injected daemon-alive guard (PR #<THIS_PR>, item #129)
    # DBXIGNORE_TEST_FAIL_DAEMON_ALIVE fires the --purge daemon-alive gate as if
    # a daemon survived service removal. The gate fires BEFORE the purge body,
    # so nothing is cleared; recovery is a clean --purge re-run.
    Write-Note "6h - uninstall --purge exits 2 on injected daemon-alive guard"
    dbxignore install *> $null
    if ($LASTEXITCODE -ne 0) { Stop-Abort "6h re-install failed" }
    Start-Sleep -Seconds 2
    $purge6hOut = Join-Path $env:TEMP "dbx-6h-purge.out"
    $env:DBXIGNORE_TEST_FAIL_DAEMON_ALIVE = "1"
    & dbxignore uninstall --purge *> $purge6hOut
    $purge6hRc = $LASTEXITCODE
    Remove-Item Env:\DBXIGNORE_TEST_FAIL_DAEMON_ALIVE
    if ($purge6hRc -eq 2) {
        Write-Pass "6h - uninstall --purge exits 2 on injected daemon-alive guard"
    } else {
        Write-Fail "6h - uninstall --purge exited $purge6hRc instead of 2"
        if (Test-Path $purge6hOut) { Get-Content $purge6hOut | ForEach-Object { Write-Note "    $_" } }
    }
    $purge6hText = if (Test-Path $purge6hOut) { Get-Content $purge6hOut -Raw } else { "" }
    if ($purge6hText -match 'daemon is running') {
        Write-Pass "6h - purge stderr reports the daemon-alive refusal"
    } else {
        Write-Fail "6h - purge stderr missing 'daemon is running'"
    }
    # Recovery: clean --purge (the gate fired before any cleanup ran).
    dbxignore uninstall --purge *> $null
```

(If the executor finds the Windows `Test-Uninstall` already uses `6g`/`6h`, bump to the next free letters — the `(PR #<THIS_PR>, item #NNN)` provenance keyed on the item number is the stable cross-reference.)

- [ ] **Step 5: Syntax-check all three scripts**

Run: `bash -n scripts/manual-test-ubuntu-vps.sh && bash -n scripts/manual-test-macos.sh && pwsh -NoProfile -Command "& { \$null = [System.Management.Automation.Language.Parser]::ParseFile('scripts/manual-test-windows.ps1', [ref]\$null, [ref]\$null); 'ps parse OK' }"`
Expected: no bash syntax errors; `ps parse OK`. (Skip the `pwsh` parse if unavailable; note the limitation.)

---

## Task 9: Commit 2 (`test`)

**Files:** none (commit only)

- [ ] **Step 1: Stage and commit the manual-test changes**

```bash
git add scripts/_phase_extended_cli.sh scripts/manual-test-windows.ps1 scripts/manual-test-ubuntu-vps.sh scripts/manual-test-macos.sh
git commit -m "$(cat <<'EOF'
test: exercise exit-2 failure paths in manual-test scripts

Add manual-test cases that arm the DBXIGNORE_TEST_FAIL_* fail points and
assert the exit-2 surface unit tests already pin: MARKER_READ for
clear/list (Phase 4.5), STATE_PURGE and DAEMON_ALIVE for uninstall
--purge (Phase 6), and BOOTOUT for uninstall on macOS. Each case
re-installs as needed, injects, asserts exit 2 + stderr, then recovers
with a clean re-run so the script end-state is unchanged.
EOF
)"
```

- [ ] **Step 2: Verify the commit subject**

Run: `git log -1 --format=%s`
Expected: `test: exercise exit-2 failure paths in manual-test scripts` — `test` is a valid `cchk.toml` commit type.

---

## Task 10: Docs — AGENTS.md gotcha + BACKLOG closes + commit 3 (`docs`)

**Files:**
- Modify: `AGENTS.md` (one Gotchas bullet)
- Modify: `BACKLOG.md` (4 RESOLVED markers, 4 Resolved-section entries, Open-list removals, Open-count line)

- [ ] **Step 1: Add the AGENTS.md Gotchas bullet**

In `AGENTS.md`, in the `## Gotchas` section, find the bullet beginning `- Timing/debugging: use \`_logging.timed_debug(...)\``. Immediately after it, insert a new bullet:

```markdown
- Failure injection for manual tests: `src/dbxignore/_testing.py` is the home of the `DBXIGNORE_TEST_FAIL_*` env-var failure-injection convention. `raise_if_fail_point(name)` (raises `OSError` into an existing `except OSError` arm) and `fail_point_active(name)` (predicate for value-substitution sites) let the manual-test shell scripts drive exit-2 error paths whose underlying failure mode can't be forced on a healthy machine. Current fail points: `MARKER_READ`, `STATE_PURGE`, `BOOTOUT`, `DAEMON_ALIVE` (see the module docstring). A new "hard-to-force exit-2 path" is a one-line hook at the boundary plus a docstring entry — don't re-defer it as untested manual-test surface.
```

- [ ] **Step 2: Add the `Status: RESOLVED` markers to the four BACKLOG items**

In `BACKLOG.md`, for each of items #121, #127, #128, #129, add a `**Status: RESOLVED 2026-05-14 (PR #<THIS_PR>).**` sentence. Insert it at the start of the item's `**Urgency:**` line's paragraph, matching the existing in-body RESOLVED-marker convention used by other resolved items (e.g. search `**Status: RESOLVED` in the file for the exact placement pattern — it typically goes as its own bolded sentence right before or after the `**Urgency:**` line). For each item, the marker reads:

- #121: `**Status: RESOLVED 2026-05-14 (PR #<THIS_PR>).** Closed by the DBXIGNORE_TEST_FAIL_* meta-fix — `MARKER_READ` fail point + manual-test case 4u/4v.`
- #127: `**Status: RESOLVED 2026-05-14 (PR #<THIS_PR>).** Closed by the DBXIGNORE_TEST_FAIL_* meta-fix — `STATE_PURGE` fail point + manual-test Phase 6 cases.`
- #128: `**Status: RESOLVED 2026-05-14 (PR #<THIS_PR>).** Closed by the DBXIGNORE_TEST_FAIL_* meta-fix — `BOOTOUT` fail point + manual-test-macos.sh Phase 6 case.`
- #129: `**Status: RESOLVED 2026-05-14 (PR #<THIS_PR>).** Closed by the DBXIGNORE_TEST_FAIL_* meta-fix — `DAEMON_ALIVE` fail point + manual-test Phase 6 cases. The rule-of-four meta-fix (#128's tripwire) landed: a shared `_testing.py` injection mechanism closing all four deferrals at once.`

- [ ] **Step 3: Add the Resolved-section entries**

In `BACKLOG.md`, find the `### Resolved (reverse chronological)` section and its `#### 2026-05-14` subheading (it already exists — items #106, #107, #125, #122 are under it). Add four new bullets under that `#### 2026-05-14` heading, above the existing `#106` bullet (reverse-chronological within the day is fine; group them together). Each bullet:

```markdown
- **#121** (2026-05-14, PR #<THIS_PR>) — `clear` / `list` `scan_errors` exit-2 path now exercised end-to-end by manual-test case 4u (bash helper) / 4v (Windows). Closed by the `DBXIGNORE_TEST_FAIL_*` meta-fix: the new `_testing.raise_if_fail_point("MARKER_READ")` hook fires before every `markers.is_ignored` read in `cli._walk_marked_paths` (the `target` read and both walk-loop reads), so an injected `OSError` lands in the existing `except OSError` arm and drives `scan_errors`. Part of the rule-of-four meta-fix (see #129).
- **#127** (2026-05-14, PR #<THIS_PR>) — `uninstall --purge` `state_errors` exit-2 path now exercised end-to-end by new Phase 6 cases in all three manual-test scripts. Closed by the `DBXIGNORE_TEST_FAIL_*` meta-fix: the `STATE_PURGE` hook in `cli._purge_dir`'s unlink loop raises `OSError` into the existing `except OSError` arm. The Phase 6 case re-installs first so the daemon has written state files for the unlink loop to fail against. Part of the rule-of-four meta-fix (see #129).
- **#128** (2026-05-14, PR #<THIS_PR>) — `uninstall` `launchctl bootout` confirmed-failure exit-2 path now exercised end-to-end by a new `manual-test-macos.sh` Phase 6 case. Closed by the `DBXIGNORE_TEST_FAIL_*` meta-fix: the `BOOTOUT` hook in `macos_launchd.uninstall_agent` substitutes a non-zero-rc `CompletedProcess` whose stderr `_is_service_not_loaded` does not match, so the existing `raise RuntimeError` arm fires. This item's body planted the rule-of-four tripwire; #129 fired it and the meta-fix landed here. Part of the rule-of-four meta-fix (see #129).
- **#129** (2026-05-14, PR #<THIS_PR>) — `uninstall --purge` daemon-alive purge-refusal exit-2 path now exercised end-to-end by new Phase 6 cases in all three manual-test scripts. **This is the rule-of-four meta-fix.** New module `src/dbxignore/_testing.py` provides `raise_if_fail_point` / `fail_point_active`, both env-var-keyed and inert by default, logging a WARNING when armed. Four one-line hook sites (`MARKER_READ`, `STATE_PURGE`, `BOOTOUT`, `DAEMON_ALIVE`) trigger already-unit-tested exit-2 paths the manual-test shell scripts otherwise could not reach. The `DAEMON_ALIVE` hook fires `cli.uninstall`'s `--purge` daemon-alive gate directly. Closes #121, #127, #128, #129 together — converting four unit-test-only contracts into end-to-end manual-test coverage and leaving a forward-compatible mechanism (a new fail point is a one-line hook + a docstring entry) documented in the AGENTS.md Gotchas section.
```

- [ ] **Step 4: Remove the four items from the Open list and update the count**

In `BACKLOG.md`'s `### Open` section: delete the `- **#121** — ...`, `- **#127** — ...`, `- **#128** — ...`, `- **#129** — ...` bullets. Then update the count sentence at the top of the `### Open` section — change `Nineteen items` to `Fifteen items` and adjust the surrounding prose: the sentence currently references `#130 and #131 filed 2026-05-14`, the `#121`/`#126`/`#127`/`#128`/`#129` provenance, and the rule-of-four narrative. Rewrite the rule-of-four sentence to past tense — it currently reads (line ~2837) that #129 "triggers the rule-of-four tripwire ... recommending the next contributor revisit a `DBXIGNORE_TEST_FAIL_*` env-var injection mechanism"; change it to note the meta-fix landed in PR #<THIS_PR>, closing #121/#127/#128/#129. Remove the now-stale references to #121/#127/#128/#129 as open items while keeping #126 (still open) and the #130/#131 provenance intact.

- [ ] **Step 5: Sanity-check the BACKLOG edits**

Run: `grep -n "THIS_PR" BACKLOG.md AGENTS.md && grep -nc "Fifteen items" BACKLOG.md && grep -n "RESOLVED 2026-05-14" BACKLOG.md | tail -8`
Expected: `<THIS_PR>` appears in the 4 RESOLVED markers + 4 Resolved entries in BACKLOG.md (8 occurrences) and NOT in AGENTS.md (the gotcha bullet uses no PR reference); `Fifteen items` appears once; the RESOLVED-2026-05-14 markers include the 4 new ones.

- [ ] **Step 6: Commit**

```bash
git add AGENTS.md BACKLOG.md
git commit -m "$(cat <<'EOF'
docs: close BACKLOG #121/#127/#128/#129 + add failure-injection gotcha

The DBXIGNORE_TEST_FAIL_* meta-fix (the rule-of-four from #128's
tripwire) is implemented, so mark the four deferred manual-test-coverage
items resolved and add an AGENTS.md Gotchas bullet pointing at
_testing.py as the failure-injection convention home.
EOF
)"
```

---

## Task 11: Pre-flight checks, code review, and PR

**Files:** none (verification + PR)

- [ ] **Step 1: Re-run the full check suite against the final tree**

Run: `uv run mypy . && uv run ruff check . && uv run ruff format --check . && uv run python -m pytest -q`
Expected: all green.

- [ ] **Step 2: Pre-flight commit-check over every commit in the branch**

For each commit subject in `origin/main..HEAD`, verify it satisfies `cchk.toml` (Conventional Commits, single scope, no leading `#` after the colon, under the length cap). Run: `git log origin/main..HEAD --format='%s'` and inspect each line. Expected three subjects:
- `docs(specs): design DBXIGNORE_TEST_FAIL_* failure-injection mechanism`
- `docs(specs): tighten MARKER_READ chokepoint + STATE_PURGE sequencing`
- `feat(testing): add DBXIGNORE_TEST_FAIL_* failure-injection helpers`
- `test: exercise exit-2 failure paths in manual-test scripts`
- `docs: close BACKLOG #121/#127/#128/#129 + add failure-injection gotcha`

All five are valid types with valid single scopes; none starts with `#`.

- [ ] **Step 3: Run the code-reviewer agent**

Dispatch the `pr-review-toolkit:code-reviewer` agent against the unstaged-then-committed diff for this branch (`git diff origin/main...HEAD`). Address any critical findings with follow-up commits (re-running the full check suite after each). Note: per AGENTS.md, the `gh pr create` hook requires the review-passed marker.

- [ ] **Step 4: Mark the code review passed**

After the reviewer's critical findings are addressed:

```bash
git rev-parse HEAD
touch ".git/.code-review-passed-$(git rev-parse HEAD)"
```

(The marker must use the full 40-char SHA and is invalidated by any new commit — re-run the reviewer + re-touch if Step 3 produced follow-up commits.)

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin chore/test-fail-injection
gh pr create --title "Add DBXIGNORE_TEST_FAIL_* failure-injection mechanism (closes #121/#127/#128/#129)" --body "$(cat <<'EOF'
## Summary
- New `src/dbxignore/_testing.py` — `raise_if_fail_point` / `fail_point_active` env-var-keyed failure-injection primitives, inert by default, WARNING-logged when armed.
- Four one-line hook sites trigger already-unit-tested exit-2 paths the manual-test shell scripts otherwise can't reach: `MARKER_READ` (`clear`/`list` scan errors), `STATE_PURGE` (`uninstall --purge` state errors), `BOOTOUT` (`uninstall` launchctl bootout failure, macOS), `DAEMON_ALIVE` (`uninstall --purge` daemon-alive refusal).
- Manual-test cases across all three platform scripts assert the exit-2 surface end-to-end.
- This is the rule-of-four meta-fix from BACKLOG #128's tripwire — closes #121, #127, #128, #129 together.

## Test plan
- [ ] `uv run python -m pytest` — full suite green (includes new `tests/test_testing.py`)
- [ ] `uv run mypy .` / `uv run ruff check .` / `uv run ruff format --check .` — clean
- [ ] Manual-test scripts: new Phase 4.5 case (`4u`/`4v`) and Phase 6 cases run inject→assert→recover; verified on at least one platform before merge
- [ ] Hooks confirmed inert when `DBXIGNORE_TEST_FAIL_*` unset (existing tests unchanged)
EOF
)"
```

- [ ] **Step 6: Fill in the `<THIS_PR>` placeholder**

Once `gh pr create` returns the PR number, replace every `<THIS_PR>` occurrence repo-wide (BACKLOG.md RESOLVED markers + Resolved entries, and the manual-test script provenance comments `(PR #<THIS_PR>, item #NNN)` in `_phase_extended_cli.sh`, `manual-test-windows.ps1`, `manual-test-ubuntu-vps.sh`, `manual-test-macos.sh`). Verify with `grep -rn "THIS_PR" .` returning nothing, then:

```bash
git add -A
git commit -m "docs(backlog): fill in PR #<N> on #121/#127/#128/#129 RESOLVED markers"
git push
```

(Replace `<N>` with the actual PR number. Per AGENTS.md the fill-in commit must grep the whole repo, not just BACKLOG.md — the placeholder also lands in the four manual-test scripts' provenance comments.)

---

## Self-Review

**1. Spec coverage:**
- Mechanism (`_testing.py`, two primitives, WARNING on fire) → Task 1. ✓
- `MARKER_READ` hook in `_walk_marked_paths`, all three reads → Task 2. ✓
- `STATE_PURGE` hook in `_purge_dir` → Task 3. ✓
- `BOOTOUT` hook in `uninstall_agent` → Task 4. ✓
- `DAEMON_ALIVE` hook in `cli.uninstall` gate → Task 5. ✓
- Manual-test #121 (Phase 4.5, bash helper + Windows) → Task 7. ✓
- Manual-test #127/#128/#129 (Phase 6, all three scripts, re-install-first) → Task 8. ✓
- Unit tests for the helper only, no new boundary tests → Task 1 (`test_testing.py`); Tasks 2-5 verify via existing test files + throwaway smoke checks. ✓
- Docs: `_testing.py` docstring (Task 1) + AGENTS.md Gotchas bullet (Task 10). ✓
- PR scope: 3 commits along the spec's revertability lines → Tasks 6, 9, 10; branch `chore/test-fail-injection`. ✓
- No CHANGELOG entry → not in any task; `feat(testing)` scope. ✓
- BACKLOG closes (4 markers + 4 entries + Open-list/count) → Task 10. ✓

**2. Placeholder scan:** `<THIS_PR>` is an intentional placeholder per the AGENTS.md fill-in-after-PR convention, resolved in Task 11 Step 6 — not a plan-failure placeholder. Case letters `6f`/`6g`/`6h` carry an explicit "bump if already taken, item number is the stable cross-reference" instruction since the exact next-free letter depends on the current script state. No `TBD`/`TODO`/"add error handling"/"similar to Task N" — every code step shows complete content.

**3. Type consistency:** `raise_if_fail_point(name: str, exc: OSError | None = None) -> None` and `fail_point_active(name: str) -> bool` — signatures defined in Task 1, used consistently in Tasks 2-5 (`_testing.raise_if_fail_point("MARKER_READ")`, `_testing.raise_if_fail_point("STATE_PURGE")`, `_testing.fail_point_active("BOOTOUT")`, `_testing.fail_point_active("DAEMON_ALIVE")`). Fail-point names (`MARKER_READ`, `STATE_PURGE`, `BOOTOUT`, `DAEMON_ALIVE`) match between `_testing.py`'s docstring, the hook sites, the manual-test env-var names, and the BACKLOG entries. The `_testing` import is added once (Task 2 Step 1) and reused by Tasks 3 and 5; Task 4 adds its own import in `macos_launchd.py`.

# #30 Windows-aware single binary — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse `dbxignore.exe` + `dbxignored.exe` into a single `dbxignore` command everywhere. The Windows PyInstaller binary uses `AttachConsole(ATTACH_PARENT_PROCESS)` early in startup to behave correctly across terminal-launch / Task Scheduler / Explorer double-click contexts. Drop the `dbxignored` entry-point from `pyproject.toml`, the PyInstaller specs, and all three installer backends.

**Architecture:** New `src/dbxignore/_windows_console.py` module (stdlib `ctypes`, ~110 LOC) gates entry through `src/dbxignore/__main__.py:main_entry()`. The Windows binary becomes GUI-subsystem (`console=False`); AttachConsole + per-stream `_is_stream_connected()` decide whether to redirect stdio to `CONOUT$`/`CONIN$`. Installers (Linux systemd / macOS launchd / Windows Task Scheduler) reference `dbxignore daemon` instead of `dbxignored`. State guard tuple updated so `dbxignore.exe` daemon process is recognized; `dbxignored` reference removed at the same time.

**Tech Stack:** Python 3.11+, stdlib `ctypes` (no new runtime deps), `rich-click` (existing), PyInstaller, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-05-13-30-windows-aware-single-binary-design.md`

**Target release:** v0.6.0 with `**Breaking**` callout (pre-1.0 SemVer policy).

**Branch:** `feat/30-windows-binary-unification` (cut from `main` after spec PR #237 merges).

---

## Pre-flight (do before Task 1)

- [ ] **P.1: Confirm PR #237 (the spec) has merged**

```bash
gh pr view 237 --json state
# Expected: {"state":"MERGED"}
```

If not merged yet, wait. The implementation plan depends on the spec being on `main` so the implementation PR can reference it.

- [ ] **P.2: Sync `main` and cut the feature branch**

```bash
git checkout main
git pull --ff-only origin main
git checkout -b feat/30-windows-binary-unification
```

- [ ] **P.3: Run the full check suite once on a clean tree**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run python -m pytest
```

Expected: all green, 623+ passed (the count from PR #234 onward). Note the baseline; subsequent task `pytest` runs should preserve or grow it (we add ~10 new tests in Task 1, modify several existing test files).

---

## Task 1: Add `_windows_console.py` module

**Files:**
- Create: `src/dbxignore/_windows_console.py`
- Create: `tests/test_windows_console.py`

The module is pure no-op on non-Windows. We test the orchestrator (`early_init`) cross-platform via mocks, and the ctypes helpers via Windows-only smoke tests. Per the spec, ten tests covering six orchestrator decision branches + the `_is_stream_connected` predicate + the per-stream `_redirect_stdio_to_attached_console` mixed-case behavior.

- [ ] **Step 1.1: Create the test file skeleton**

Create `tests/test_windows_console.py` with imports and pytest fixtures (no tests yet, just the structure):

```python
"""Tests for src/dbxignore/_windows_console.py.

The orchestrator (`early_init`) is tested cross-platform via mocks of
the helpers. The ctypes helpers are tested Windows-only via smoke
tests that gate on `sys.platform == "win32"`.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from dbxignore import _windows_console

if TYPE_CHECKING:
    pass  # placeholder for future imports if needed
```

- [ ] **Step 1.2: Write the first failing test (non-Windows no-op)**

Append to `tests/test_windows_console.py`:

```python
def test_early_init_no_op_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Linux/macOS, early_init returns without touching anything."""
    monkeypatch.setattr(sys, "platform", "linux")
    # Sentinel: if attach helper were called, it'd raise (linux has no ctypes.windll)
    _windows_console.early_init()  # should not raise
```

- [ ] **Step 1.3: Run the test to verify it fails**

```bash
uv run python -m pytest tests/test_windows_console.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'dbxignore._windows_console'` (because we haven't created the module yet).

- [ ] **Step 1.4: Create the module with the minimum to make the test pass**

Create `src/dbxignore/_windows_console.py`:

```python
"""Windows console attach + double-click MessageBox for the unified binary.

This module exists so the Windows PyInstaller binary can be built as a
GUI-subsystem executable (no console flash at Task Scheduler logon) yet
still flow output to the parent's console when launched from one.

Called from src/dbxignore/__main__.py:main_entry() BEFORE any other
imports that capture sys.stdout (notably rich-click / rich).
"""

from __future__ import annotations

import sys


def early_init() -> None:
    """Three-context Windows entry-point setup. No-op on non-Windows."""
    if sys.platform != "win32":
        return
    # TODO: rest of the logic — added in subsequent steps
```

- [ ] **Step 1.5: Run the test, verify it passes**

```bash
uv run python -m pytest tests/test_windows_console.py -v
```

Expected: PASS (1 passed).

- [ ] **Step 1.6: Write tests for `_is_stream_connected`**

Append to `tests/test_windows_console.py`:

```python
def test_is_stream_connected_false_for_none() -> None:
    assert not _windows_console._is_stream_connected(None)


def test_is_stream_connected_false_when_fileno_raises() -> None:
    class BrokenStream:
        def fileno(self) -> int:
            raise OSError("no fd")
    assert not _windows_console._is_stream_connected(BrokenStream())


def test_is_stream_connected_true_for_real_stdio() -> None:
    """Under pytest, sys.stdout has a valid fileno (pytest's capture
    wrappers proxy through to a real FD). Verifies the happy-path
    detection."""
    assert _windows_console._is_stream_connected(sys.stdout)
```

- [ ] **Step 1.7: Run tests, verify the new ones fail**

```bash
uv run python -m pytest tests/test_windows_console.py -v
```

Expected: 1 pass, 3 fails with `AttributeError: module 'dbxignore._windows_console' has no attribute '_is_stream_connected'`.

- [ ] **Step 1.8: Add `_is_stream_connected` to the module**

Append to `src/dbxignore/_windows_console.py`:

```python
def _is_stream_connected(stream: object) -> bool:
    """Return True if `stream` has a valid backing FD (already wired to
    something — parent console, pipe, or file). Returns False for None
    or streams whose .fileno() raises (the GUI-subsystem launch had no
    inherited handle for this slot).
    """
    if stream is None:
        return False
    try:
        stream.fileno()  # type: ignore[union-attr]
    except (AttributeError, OSError, ValueError):
        return False
    return True
```

- [ ] **Step 1.9: Run tests, verify all four pass**

```bash
uv run python -m pytest tests/test_windows_console.py -v
```

Expected: 4 passed.

- [ ] **Step 1.10: Write the orchestrator tests (attach succeeds path)**

Append to `tests/test_windows_console.py`:

```python
def test_early_init_attach_success_redirects_and_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(_windows_console, "_attach_parent_console", lambda: True)
    calls: list[str] = []
    monkeypatch.setattr(
        _windows_console,
        "_redirect_stdio_to_attached_console",
        lambda: calls.append("redirect"),
    )
    monkeypatch.setattr(
        _windows_console,
        "_show_help_message_box",
        lambda: calls.append("messagebox"),
    )
    _windows_console.early_init()  # should not exit
    assert calls == ["redirect"]


def test_early_init_attach_fail_with_argv_returns_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["dbxignore.exe", "daemon"])
    monkeypatch.setattr(_windows_console, "_attach_parent_console", lambda: False)
    calls: list[str] = []
    monkeypatch.setattr(
        _windows_console,
        "_show_help_message_box",
        lambda: calls.append("messagebox"),
    )
    _windows_console.early_init()
    assert calls == []


def test_early_init_attach_fail_no_argv_shows_box_and_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["dbxignore.exe"])
    monkeypatch.setattr(_windows_console, "_attach_parent_console", lambda: False)
    box_calls: list[str] = []
    monkeypatch.setattr(
        _windows_console,
        "_show_help_message_box",
        lambda: box_calls.append("shown"),
    )
    with pytest.raises(SystemExit) as exc_info:
        _windows_console.early_init()
    assert exc_info.value.code == 0
    assert box_calls == ["shown"]


def test_early_init_messagebox_oserror_still_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even if MessageBox itself fails (unusual session), we still exit
    cleanly. `_show_help_message_box` already swallows OSError internally;
    here we simulate that with a no-op stub."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["dbxignore.exe"])
    monkeypatch.setattr(_windows_console, "_attach_parent_console", lambda: False)
    monkeypatch.setattr(_windows_console, "_show_help_message_box", lambda: None)
    with pytest.raises(SystemExit) as exc_info:
        _windows_console.early_init()
    assert exc_info.value.code == 0


@pytest.mark.parametrize("flag", ["--help", "-h", "--version"])
def test_early_init_help_or_version_does_not_take_messagebox_branch(
    monkeypatch: pytest.MonkeyPatch, flag: str,
) -> None:
    """--help / --version are valid CLI usage that must NEVER pop the
    MessageBox even if AttachConsole fails (unusual edge: someone
    double-clicks a desktop shortcut with `--help` in the target).
    Argv with any non-program token always takes the silent-return
    branch; click handles --help / --version normally from there."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["dbxignore.exe", flag])
    monkeypatch.setattr(_windows_console, "_attach_parent_console", lambda: False)
    box_calls: list[str] = []
    monkeypatch.setattr(
        _windows_console,
        "_show_help_message_box",
        lambda: box_calls.append("shown"),
    )
    _windows_console.early_init()
    assert box_calls == []
```

- [ ] **Step 1.11: Run tests, verify the new ones fail with AttributeError**

```bash
uv run python -m pytest tests/test_windows_console.py -v
```

Expected: 4 pass (from earlier), 7 fail with `AttributeError: module 'dbxignore._windows_console' has no attribute '_attach_parent_console'` (and similar for the other helpers).

- [ ] **Step 1.12: Replace `early_init` and add the three helpers**

Update `src/dbxignore/_windows_console.py` — replace the current `early_init` (the `if sys.platform != "win32": return` plus TODO) with the full version, and append the three helpers:

```python
import ctypes


_ATTACH_PARENT_PROCESS = -1
_MB_OK_ICONINFO = 0x00000040  # MB_OK (0) | MB_ICONINFORMATION (0x40)
_MESSAGE_TITLE = "dbxignore"
_MESSAGE_BODY = (
    "dbxignore is a command-line tool.\n\n"
    "Open Windows Terminal, PowerShell, or Command Prompt and run:\n\n"
    "    dbxignore --help\n\n"
    "for the list of available commands."
)


def early_init() -> None:
    """Three-context Windows entry-point setup. No-op on non-Windows.

    1. Attach to parent's console if one exists -> terminal-CLI behavior.
    2. No parent console, argv has subcommand -> silent (Task Scheduler).
    3. No parent console, argv empty -> MessageBox + exit (Explorer double-click).
    """
    if sys.platform != "win32":
        return
    if _attach_parent_console():
        _redirect_stdio_to_attached_console()
        return
    if len(sys.argv) > 1:
        return
    _show_help_message_box()
    sys.exit(0)


def _attach_parent_console() -> bool:
    """Try to attach this process to the parent's console.

    Returns True if attached. False if the parent has no console
    (Task Scheduler, Explorer double-click) or attach otherwise failed.
    """
    try:
        return bool(ctypes.windll.kernel32.AttachConsole(_ATTACH_PARENT_PROCESS))
    except OSError:
        return False


def _show_help_message_box() -> None:
    """Pop a MessageBox saying dbxignore is a CLI tool.

    Wrapped in try/except so an unusual session state (no window station,
    locked-down desktop) falls through to silent exit rather than crashing.
    """
    try:
        ctypes.windll.user32.MessageBoxW(
            None, _MESSAGE_BODY, _MESSAGE_TITLE, _MB_OK_ICONINFO,
        )
    except OSError:
        pass
```

Place `import ctypes` near the top of the file (after the existing `import sys`); place the new constants and functions in declaration order.

- [ ] **Step 1.13: Run tests, verify orchestrator tests pass**

```bash
uv run python -m pytest tests/test_windows_console.py -v
```

Expected: 11 passed, 0 failed. (Some tests reference `_redirect_stdio_to_attached_console`, which doesn't exist yet — pytest will see them fail with AttributeError. Let's count what we expect: the orchestrator tests all mock `_redirect_stdio_to_attached_console` so they don't actually call it; only the test_redirect_preserves_valid... test (not yet added) would.)

Hmm, the attach_success orchestrator test monkey-patches `_redirect_stdio_to_attached_console` — meaning it expects the symbol to exist on the module. Since we haven't added it yet, `monkeypatch.setattr` will fail with `AttributeError`. We need to add a no-op placeholder for `_redirect_stdio_to_attached_console` first.

Update the module: add this stub right after `_show_help_message_box`:

```python
def _redirect_stdio_to_attached_console() -> None:
    """Placeholder; real implementation in Task 1.14."""
    pass
```

Re-run the test. Expected: 11 passed.

- [ ] **Step 1.14: Write the per-stream `_redirect_stdio_to_attached_console` test**

Append to `tests/test_windows_console.py`:

```python
def test_redirect_preserves_valid_stdout_and_reopens_missing_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed case: stdout valid (already wired up), stderr None.
    Must NOT overwrite stdout; MUST reopen stderr against CONOUT$.
    Verifies the per-stream preservation contract from the spec."""
    monkeypatch.setattr(sys, "platform", "win32")
    fake_stdout = sys.stdout  # already valid under pytest
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "stdin", None)

    opened: list[tuple[str, str]] = []

    def fake_open(name: str, mode: str, **kwargs: object) -> object:
        opened.append((name, mode))
        return object()  # sentinel — not a real file object

    monkeypatch.setattr("builtins.open", fake_open)
    _windows_console._redirect_stdio_to_attached_console()
    # stdout untouched (was already valid)
    assert sys.stdout is fake_stdout
    # stderr and stdin reopened
    assert sys.stderr is not None
    assert sys.stdin is not None
    # Confirm the opens went to CONOUT$ (for stderr) and CONIN$ (for stdin) — NOT stdout
    assert opened == [("CONOUT$", "w"), ("CONIN$", "r")]
```

- [ ] **Step 1.15: Run test, verify it fails**

```bash
uv run python -m pytest tests/test_windows_console.py::test_redirect_preserves_valid_stdout_and_reopens_missing_stderr -v
```

Expected: FAIL with assertion error (the current `_redirect_stdio_to_attached_console` is a no-op stub; nothing gets opened, `sys.stderr` stays None, the `opened` list is empty).

- [ ] **Step 1.16: Replace the placeholder with the real implementation**

Replace the `_redirect_stdio_to_attached_console` stub in `src/dbxignore/_windows_console.py`:

```python
def _redirect_stdio_to_attached_console() -> None:
    """Reopen each stream against CONOUT$ / CONIN$ ONLY if it's missing or
    invalid. Each stream is handled independently — preserves mixed cases
    like `dbxignore --version 2> err.log` (stdout to console, stderr to file).

    CRITICAL: don't replace streams that have valid inherited FDs. If the user
    ran `dbxignore --version > out.txt` or `dbxignore --version | findstr ...`
    from a shell, the inherited stdio is the redirected file or pipe —
    overwriting with CONOUT$ would send output to the console instead,
    breaking the redirection contract.
    """
    if not _is_stream_connected(sys.stdout):
        try:
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        except OSError:
            pass  # leave None; print() becomes a no-op
    if not _is_stream_connected(sys.stderr):
        try:
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        except OSError:
            pass
    if not _is_stream_connected(sys.stdin):
        try:
            sys.stdin = open("CONIN$", "r", encoding="utf-8")
        except OSError:
            pass
```

- [ ] **Step 1.17: Run the full test_windows_console suite, verify all pass**

```bash
uv run python -m pytest tests/test_windows_console.py -v
```

Expected: 12 passed (11 from before + the new redirect-preserves test).

- [ ] **Step 1.18: Run ruff / format / mypy on the new module**

```bash
uv run ruff check src/dbxignore/_windows_console.py tests/test_windows_console.py
uv run ruff format --check src/dbxignore/_windows_console.py tests/test_windows_console.py
uv run mypy src/dbxignore/_windows_console.py
```

Expected: all clean. If ruff complains about line length, reflow; if mypy complains about ctypes types, the `ctypes.windll.kernel32` calls are dynamic and mypy treats them as `Any` — no annotations needed.

- [ ] **Step 1.19: Commit**

```bash
git add src/dbxignore/_windows_console.py tests/test_windows_console.py
git commit -m "$(cat <<'EOF'
feat(windows): add _windows_console module for unified binary

New stdlib-ctypes module gating Windows-binary startup. Provides
early_init() called from __main__.main_entry() before cli imports.

Three-context behavior on Windows (no-op on Linux/macOS):
- Terminal launch (parent has console): AttachConsole succeeds, redirect
  stdio per-stream only for streams that lack a valid backing FD
  (preserves redirected pipes/files from cmd.exe / PowerShell shells).
- Task Scheduler launch (no parent console + argv has subcommand):
  silent return, daemon runs without a console window.
- Explorer double-click (no parent console + argv empty): user32
  MessageBoxW dialog "dbxignore is a CLI tool, run dbxignore --help",
  then sys.exit(0).

12 unit tests cover the six orchestrator branches plus the
_is_stream_connected predicate edges plus the per-stream
_redirect_stdio_to_attached_console mixed-case behavior.

Module not yet wired into __main__ — that's Task 2.

Part of BACKLOG #30.
EOF
)"
```

---

## Task 2: Wire `__main__.py` to call `early_init`

**Files:**
- Modify: `src/dbxignore/__main__.py`

The entry point gets rerouted through a new `main_entry()` function that calls `_windows_console.early_init()` on Windows BEFORE importing `cli` (deferred import). Critical: `cli` imports `rich_click` which constructs a `rich.console.Console()` that caches `sys.stdout` at import time. Redirecting stdio after that import would leave rich-click using the stale stdout reference.

- [ ] **Step 2.1: Read the current `__main__.py`**

```bash
cat src/dbxignore/__main__.py
```

Expected: short file, likely something like `from dbxignore.cli import main; main()`. Adapt the rewrite below to preserve any non-import-non-call lines.

- [ ] **Step 2.2: Rewrite `__main__.py`**

Replace the contents of `src/dbxignore/__main__.py` with:

```python
"""Entry point for `python -m dbxignore` and (after Task 10's pyproject
change) for the `dbxignore` console script.

On Windows, _windows_console.early_init() runs BEFORE the cli import:
- Attaches the GUI-subsystem binary to the parent console if one exists.
- Pops a MessageBox on Explorer double-click (no parent + no argv).
- No-op on Linux / macOS.

The cli import is deferred so rich-click's rich.console.Console() (which
captures sys.stdout at module-import time) sees the post-attach stdout.
"""

from __future__ import annotations

import sys


def main_entry() -> None:
    if sys.platform == "win32":
        from dbxignore import _windows_console
        _windows_console.early_init()  # may sys.exit(0) on double-click path
    from dbxignore.cli import main  # deferred import — after stdio redirect
    main()


if __name__ == "__main__":
    main_entry()
```

- [ ] **Step 2.3: Confirm `python -m dbxignore --version` still works**

```bash
uv run python -m dbxignore --version
```

Expected: `dbxignore, version <X.Y.Z>` printed to stdout (cli.main() runs as before; the early_init path is no-op on non-Windows / Windows-without-AttachConsole-needs).

- [ ] **Step 2.4: Run the full pytest to ensure nothing regressed**

```bash
uv run python -m pytest 2>&1 | tail -5
```

Expected: count = baseline + 12 new tests from Task 1 (so ~635 passed).

- [ ] **Step 2.5: Commit**

```bash
git add src/dbxignore/__main__.py
git commit -m "$(cat <<'EOF'
feat(windows): route __main__ through main_entry with early_init hook

Adds main_entry() function that runs _windows_console.early_init() on
Windows before importing dbxignore.cli. The deferred cli import is
load-bearing — rich-click's rich.console.Console() captures sys.stdout
at module-import time, so redirecting stdio after that import would
leave rich-click with a stale stdout reference.

`python -m dbxignore` continues to work via the `if __name__ ==
"__main__"` block; the `[project.scripts].dbxignore` retarget to
__main__:main_entry happens in Task 10.

Part of BACKLOG #30.
EOF
)"
```

---

## Task 3: Delete `daemon_main` from `cli.py`

**Files:**
- Modify: `src/dbxignore/cli.py` (delete the standalone `daemon_main` click command — ~10 LOC at the bottom of the file)
- Modify: `tests/test_cli_entrypoints.py` (delete the four `test_daemon_main_*` tests)

The standalone `daemon_main` click command was the body of the `dbxignored` entry-point. After dropping the entry-point (Task 10), it has no callers. The `@main.command() def daemon(...)` subcommand stays — that's what `dbxignore daemon` runs through.

- [ ] **Step 3.1: Locate `daemon_main` in cli.py**

```bash
grep -n "^def daemon_main\|^def daemon\b" src/dbxignore/cli.py
```

Expected: two matches — one for `def daemon_main(...)` (the standalone command we're removing) and one for `def daemon(...)` (the `@main.command()` subcommand we're keeping).

- [ ] **Step 3.2: Read the `daemon_main` block and its decorators**

```bash
grep -n -B 5 "^def daemon_main" src/dbxignore/cli.py
```

Expected: shows the `@click.command()` + `@click.option(...)` + `@click.version_option(...)` decorators above the function. Note the line range — we'll delete from the first decorator line through the last line of the function body.

- [ ] **Step 3.3: Delete the entire `daemon_main` block**

Use Edit to remove the block. The block looks like (verify against your current line range; if PR #234 is merged, `daemon_main` uses the counted-verbose form):

```python
@click.command()
@click.option(
    "--verbose",
    "-v",
    count=True,
    help="Increase verbosity. Default WARNING; `-v` INFO; `-vv` DEBUG.",
)
@click.version_option(package_name="dbxignore")
def daemon_main(verbose: int) -> None:
    """Run the dbxignore watcher + hourly sweep daemon (foreground)."""
    logging.basicConfig(
        level=_verbosity_to_level(verbose),
        format="%(levelname)s %(name)s: %(message)s",
    )
    _run_daemon()
```

Delete the entire block (decorators + function). Don't delete any blank lines beyond what was attached to this block.

- [ ] **Step 3.4: Confirm cli.py still imports cleanly**

```bash
uv run python -c "from dbxignore import cli; print('ok')"
```

Expected: `ok` printed. If `NameError: name 'daemon_main' is not defined` appears, something is still referencing the deleted function — grep for `daemon_main` in the project:

```bash
grep -rn "daemon_main" src/ tests/ pyproject.toml
```

- [ ] **Step 3.5: Remove the four `test_daemon_main_*` tests**

Open `tests/test_cli_entrypoints.py` and delete these four functions (the entire `def` blocks plus their decorators):
- `test_daemon_main_version_flag_emits_package_version`
- `test_daemon_main_help_has_no_subcommand_token`
- `test_daemon_main_verbose_flag_is_reachable`
- `test_daemon_main_vv_flag_is_reachable`

Leave the `test_main_*`, `test_verbosity_to_level_*`, and any other non-`daemon_main` tests intact.

- [ ] **Step 3.6: Run pytest, verify the remaining tests pass and the count drops appropriately**

```bash
uv run python -m pytest tests/test_cli_entrypoints.py -v
```

Expected: the 4 `test_daemon_main_*` tests are gone (not in output); the remaining `test_main_*` and `test_verbosity_to_level_*` tests pass.

- [ ] **Step 3.7: Run the full suite for a baseline check**

```bash
uv run python -m pytest 2>&1 | tail -3
```

Expected: count = baseline - 4 (the deleted tests) + 12 (Task 1's new tests) = baseline + 8.

- [ ] **Step 3.8: Commit**

```bash
git add src/dbxignore/cli.py tests/test_cli_entrypoints.py
git commit -m "$(cat <<'EOF'
refactor(cli): remove daemon_main standalone command

The daemon_main click command was the body of the dbxignored
entry-point. With #30 dropping the entry-point (Task 10), it has no
callers. The @main.command() def daemon(...) subcommand at the same
file is what `dbxignore daemon` runs through and stays in place.

Four daemon_main-specific tests removed from test_cli_entrypoints.py
(--version, --help, --verbose, -vv flag reachability). All four are
already covered by the equivalent test_main_* / test_verbosity_to_level_*
tests in the same file.

Part of BACKLOG #30.
EOF
)"
```

---

## Task 4: Update `state.is_daemon_alive` guard tuple

**Files:**
- Modify: `src/dbxignore/state.py` (one tuple constant)
- Modify: `tests/test_state.py` (tests for the new guard)

After unification, the frozen daemon process is named `dbxignore.exe`, not `dbxignored.exe`. The guard tuple gets `"dbxignore"` and drops `"dbxignored"`. `"python"` stays (covers non-frozen / trampoline daemons via `pythonw.exe`).

- [ ] **Step 4.1: Locate the guard tuple**

```bash
grep -n "is_daemon_alive\|_DAEMON_NAME\|process_name\|'python'\|'dbxignored'" src/dbxignore/state.py
```

Expected: shows the constant or inline tuple inside `is_daemon_alive`. The name is something like `_DAEMON_NAME_TOKENS` or it's an inline tuple. Note the current shape.

- [ ] **Step 4.2: Write a failing test that the new guard recognizes `dbxignore`**

In `tests/test_state.py`, find the existing `is_daemon_alive` tests (search for `is_daemon_alive(`) and add this near them:

```python
def test_is_daemon_alive_recognizes_dbxignore_process_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After #30 unification, a frozen daemon is named `dbxignore.exe`,
    not `dbxignored.exe`. The process-name guard tuple must accept it."""
    class FakeProcess:
        def __init__(self, name: str) -> None:
            self._name = name
        def name(self) -> str:
            return self._name
        def create_time(self) -> float:
            return 0.0
        def is_running(self) -> bool:
            return True

    fake_psutil = type("FakePsutil", (), {
        "Process": lambda pid: FakeProcess("dbxignore.exe"),
        "NoSuchProcess": Exception,
        "AccessDenied": Exception,
    })()
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert state.is_daemon_alive(pid=12345) is True


def test_is_daemon_alive_no_longer_recognizes_dbxignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-#30 `dbxignored` name is intentionally dropped from the
    guard. A surviving v0.5.x daemon process surfaces as not-alive,
    prompting the migration. Surfacing stale state is the desired
    behavior."""
    class FakeProcess:
        def __init__(self, name: str) -> None:
            self._name = name
        def name(self) -> str:
            return self._name
        def create_time(self) -> float:
            return 0.0
        def is_running(self) -> bool:
            return True

    fake_psutil = type("FakePsutil", (), {
        "Process": lambda pid: FakeProcess("dbxignored.exe"),
        "NoSuchProcess": Exception,
        "AccessDenied": Exception,
    })()
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert state.is_daemon_alive(pid=12345) is False
```

(Adapt the test shape if the existing `is_daemon_alive` tests use a different mocking pattern — match the project's convention.)

- [ ] **Step 4.3: Run the new tests, verify they fail**

```bash
uv run python -m pytest tests/test_state.py::test_is_daemon_alive_recognizes_dbxignore_process_name tests/test_state.py::test_is_daemon_alive_no_longer_recognizes_dbxignored -v
```

Expected: first test fails (process name "dbxignore.exe" not in current guard tuple); second test passes (current guard accepts "dbxignored", so `is_daemon_alive` returns True, the assertion `is False` fails). Both fail — exactly as expected before we change the guard.

- [ ] **Step 4.4: Update the guard tuple**

In `src/dbxignore/state.py`, change the guard tuple from its current shape (e.g., `("python", "dbxignored")`) to `("python", "dbxignore")`. Edit the tuple — don't refactor anything else.

If the tuple is named (`_DAEMON_NAME_TOKENS = ...`), update the constant. If it's inline, edit the inline tuple. Match the existing project pattern.

- [ ] **Step 4.5: Run the tests, verify both new ones pass**

```bash
uv run python -m pytest tests/test_state.py -v
```

Expected: the new dbxignore-recognized test passes; the dbxignored-no-longer-recognized test passes; all existing `is_daemon_alive` tests pass.

- [ ] **Step 4.6: Run the full suite**

```bash
uv run python -m pytest 2>&1 | tail -3
```

Expected: count = previous + 2.

- [ ] **Step 4.7: Commit**

```bash
git add src/dbxignore/state.py tests/test_state.py
git commit -m "$(cat <<'EOF'
fix(state): update is_daemon_alive guard tuple for unified binary

After #30 unification, the frozen daemon process is named dbxignore.exe
(was dbxignored.exe). Replace the guard tuple ("python", "dbxignored")
with ("python", "dbxignore"). The "python" entry stays (covers
non-frozen / trampoline daemons via pythonw.exe per #100).

The "dbxignored" entry is dropped at the same time — post-#30 no
process should bear that name; a surviving v0.5.x daemon process on a
non-upgraded host surfaces as not-alive, prompting the documented
uninstall-before-upgrade migration.

Two new tests in test_state.py cover the new positive
(recognizes "dbxignore.exe") and intentional-negative (no longer
recognizes "dbxignored.exe") behaviors.

Part of BACKLOG #30.
EOF
)"
```

---

## Task 5: Simplify `install/_common.detect_invocation`

**Files:**
- Modify: `src/dbxignore/install/_common.py` (drop the `shutil.which("dbxignored")` lookup; simplify the three-step rule to a one-step single-binary lookup)
- Modify: `tests/test_install_common.py` (simplify the tests)

Current `detect_invocation` has a three-step rule per CLAUDE.md: "(1) return `sys.executable` directly if it's already the daemon shim, (2) else look for the `dbxignored` sibling next to it, (3) else fall through to `(sys.executable, "daemon")` as defensive fallback." After unification, there's no separate `dbxignored` to find — it's always `(<dbxignore>, "daemon")`.

The non-frozen branch retains the `pythonw.exe` selection (item #100, PR #229).

- [ ] **Step 5.1: Read the current `detect_invocation`**

```bash
grep -n -A 60 "^def detect_invocation" src/dbxignore/install/_common.py
```

Note the three-step shape. The new shape: frozen → `(<dbxignore-binary>, "daemon")`; non-frozen → `(<pythonw or python or executable>, "-m dbxignore daemon")`.

- [ ] **Step 5.2: Update existing tests for the new contract**

Read `tests/test_install_common.py` and locate the existing `test_detect_invocation_*` tests. Look for ones that test:
- Frozen + `dbxignored` shim found → return `(<dbxignored-path>, "")` or similar
- Frozen + `dbxignored` not found, falls back → `(sys.executable, "daemon")`
- Non-frozen → `(<python>, "-m dbxignore daemon")` (or the pythonw.exe variant)

Update these tests to reflect the new single-binary contract. The tests that check the "find dbxignored shim" branch are deletable — there's no shim to find.

Specifically:
- Tests that assert the return tuple includes a `dbxignored` path → either delete them or update to expect the `dbxignore` path with `"daemon"` argument.
- The non-frozen Windows pythonw.exe test stays as-is (the pythonw.exe fallback from item #100 is independent and continues to work).

- [ ] **Step 5.3: Run the updated tests, verify they fail**

```bash
uv run python -m pytest tests/test_install_common.py -v
```

Expected: at least one or two tests fail (the ones we updated to expect the new contract; current code still implements the old three-step rule).

- [ ] **Step 5.4: Rewrite `detect_invocation` to the new shape**

In `src/dbxignore/install/_common.py`, replace the function body. The new logic:

```python
import shutil
import sys
from pathlib import Path


def detect_invocation() -> tuple[Path, str]:
    """Return (executable_path, args_string) for the installed service entry.

    Frozen (PyInstaller binary): the binary is dbxignore[.exe]; invoke it
    with "daemon" as the single argument. The pre-#30 three-step "find
    dbxignored shim" logic is gone — there is no separate dbxignored
    binary after #30 unification.

    Non-frozen (uv tool install / pip install): use the Python interpreter
    with `-m dbxignore daemon`. On Windows, prefer `pythonw.exe` for the
    windowless launch (per BACKLOG #100); fall back to `sys.executable` if
    `pythonw.exe` doesn't exist (Microsoft Store Python, embedded
    interpreters).
    """
    if getattr(sys, "frozen", False):
        # PyInstaller frozen path. After #30 there's only one binary —
        # use sys.executable's path directly.
        return Path(sys.executable), "daemon"

    # Non-frozen path.
    if sys.platform == "win32":
        pythonw_path = Path(sys.executable).with_name("pythonw.exe")
        if pythonw_path.exists():
            return pythonw_path, "-m dbxignore daemon"
        # Pythonw.exe absent (Store Python etc.) — fall back to python.exe
        # with a logged warning. The warning + fallback shape was added in
        # PR #229 (item #100).
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(
            "pythonw.exe not found next to %s; falling back to python.exe. "
            "The daemon launched at logon may briefly flash a console window.",
            sys.executable,
        )
        return Path(sys.executable), "-m dbxignore daemon"

    # Linux / macOS non-frozen: shutil.which("dbxignore") if it exists,
    # else sys.executable with -m.
    dbxignore_in_path = shutil.which("dbxignore")
    if dbxignore_in_path:
        return Path(dbxignore_in_path), "daemon"
    return Path(sys.executable), "-m dbxignore daemon"
```

Important: do NOT call `shutil.which("dbxignored")` anywhere in this function. That was the pre-#30 shim lookup.

- [ ] **Step 5.5: Run tests, verify all pass**

```bash
uv run python -m pytest tests/test_install_common.py -v
```

Expected: all tests in `test_install_common.py` pass.

- [ ] **Step 5.6: Commit**

```bash
git add src/dbxignore/install/_common.py tests/test_install_common.py
git commit -m "$(cat <<'EOF'
refactor(install): simplify detect_invocation to single-binary lookup

The pre-#30 three-step rule (find dbxignored shim, fall back to
python+daemon) collapses to:

- Frozen: (Path(sys.executable), "daemon") — the binary IS dbxignore.
- Non-frozen Windows: (pythonw.exe, "-m dbxignore daemon"), with the
  python.exe fallback + WARN from PR #229 (item #100) preserved.
- Non-frozen Linux/macOS: (shutil.which("dbxignore"), "daemon") or
  (sys.executable, "-m dbxignore daemon").

shutil.which("dbxignored") lookup removed. test_install_common.py tests
updated; the "find dbxignored shim" branch tests are deleted.

Part of BACKLOG #30.
EOF
)"
```

---

## Task 6: Update `install/windows_task.build_task_xml`

**Files:**
- Modify: `src/dbxignore/install/windows_task.py`
- Modify: `tests/test_windows_task.py`

Today's task XML has `<Command>...dbxignored.exe</Command><Arguments></Arguments>`. After #30: `<Command>...dbxignore.exe</Command><Arguments>daemon</Arguments>`. Most of the change happens automatically because `detect_invocation` now returns the new tuple shape — but the XML-building code may have assumptions to verify.

- [ ] **Step 6.1: Read `build_task_xml`**

```bash
grep -n -A 80 "^def build_task_xml\|^def install_task" src/dbxignore/install/windows_task.py
```

Find where `detect_invocation`'s return tuple is consumed. The exe-path becomes the `<Command>` value; the args-string becomes the `<Arguments>` value.

- [ ] **Step 6.2: Update the existing `test_build_task_xml_*` tests**

Open `tests/test_windows_task.py`. Find tests that assert on `<Command>` content containing `dbxignored` — update to expect `dbxignore.exe`. Find tests that assert `<Arguments>` is empty (was the case when `dbxignored` was invoked bare) — update to expect `daemon` as the argument.

Specifically:
- Assertions of the form `assert "dbxignored.exe" in xml` → `assert "dbxignore.exe" in xml and "<Arguments>daemon</Arguments>" in xml`
- Assertions that the `<Arguments>` element is empty → expect `daemon`

- [ ] **Step 6.3: Run the updated tests, verify they fail**

```bash
uv run python -m pytest tests/test_windows_task.py -v
```

Expected: the updated tests fail; the unmodified ones pass.

- [ ] **Step 6.4: Update `build_task_xml` to use the new invocation shape**

In most cases, `build_task_xml` already consumes `detect_invocation()`'s return tuple. After Task 5, the tuple is the new shape — `build_task_xml` may not need any change beyond passing the tuple through. Verify the function body:

```bash
grep -n -A 20 "detect_invocation\|exe_path\|args" src/dbxignore/install/windows_task.py
```

If the function explicitly hardcodes the args as empty (e.g., `<Arguments></Arguments>` literal) instead of using the tuple's second element, fix that:
- Replace `<Arguments></Arguments>` with `<Arguments>{args}</Arguments>` (or whatever templating shape the function uses).
- Make sure the args are XML-escaped if `_xml_escape` or similar is in use (look for existing escape helpers in the file).

- [ ] **Step 6.5: Run tests, verify all pass**

```bash
uv run python -m pytest tests/test_windows_task.py -v
```

Expected: all tests pass.

- [ ] **Step 6.6: Commit**

```bash
git add src/dbxignore/install/windows_task.py tests/test_windows_task.py
git commit -m "$(cat <<'EOF'
refactor(install): windows_task.build_task_xml uses unified invocation

Task Scheduler XML now emits <Command>dbxignore.exe</Command>
<Arguments>daemon</Arguments> instead of pointing at the pre-#30
dbxignored.exe with empty Arguments. Most of the change comes
automatically from Task 5's detect_invocation rewrite; this commit
fixes any XML-building site that hardcoded empty Arguments.

test_windows_task.py XML-content assertions updated to expect the
new shape.

Part of BACKLOG #30.
EOF
)"
```

---

## Task 7: Update `install/linux_systemd.build_unit_content`

**Files:**
- Modify: `src/dbxignore/install/linux_systemd.py`
- Modify: `tests/test_linux_systemd.py`

Today's `ExecStart` is `ExecStart=<bin>/dbxignored`. After #30: `ExecStart=<bin>/dbxignore daemon`.

- [ ] **Step 7.1: Read `build_unit_content`**

```bash
grep -n -A 80 "^def build_unit_content\|^def install_unit" src/dbxignore/install/linux_systemd.py
```

- [ ] **Step 7.2: Update existing tests for the new ExecStart**

In `tests/test_linux_systemd.py`, find tests asserting on `ExecStart=` content. Update them to expect `dbxignore` (not `dbxignored`) and the `daemon` subcommand:

```python
# Before:
assert "ExecStart=/usr/local/bin/dbxignored" in unit_content

# After:
assert "ExecStart=/usr/local/bin/dbxignore daemon" in unit_content
```

Adapt the path placeholder to whatever the existing tests use (`/usr/local/bin/dbxignored`, `/home/user/.local/bin/dbxignored`, etc.).

- [ ] **Step 7.3: Run tests, verify they fail**

```bash
uv run python -m pytest tests/test_linux_systemd.py -v
```

Expected: the updated tests fail (current code still emits `dbxignored`).

- [ ] **Step 7.4: Update `build_unit_content` to consume `detect_invocation`'s new tuple**

Same shape as Task 6 — most of the change is automatic once `detect_invocation` returns the new tuple. If the function hardcodes `dbxignored` anywhere, replace with the tuple-derived value. The ExecStart line should be:

```
ExecStart={exe_path} {args_string}
```

where `(exe_path, args_string) = detect_invocation()`.

- [ ] **Step 7.5: Run tests, verify all pass**

```bash
uv run python -m pytest tests/test_linux_systemd.py -v
```

Expected: all tests pass.

- [ ] **Step 7.6: Commit**

```bash
git add src/dbxignore/install/linux_systemd.py tests/test_linux_systemd.py
git commit -m "$(cat <<'EOF'
refactor(install): linux_systemd ExecStart uses dbxignore daemon

systemd unit's ExecStart now references `dbxignore daemon` instead of
`dbxignored`. Most of the change derives automatically from Task 5's
detect_invocation rewrite; this commit fixes any unit-content site
that hardcoded the dbxignored path.

test_linux_systemd.py ExecStart assertions updated.

Part of BACKLOG #30.
EOF
)"
```

---

## Task 8: Update `install/macos_launchd.build_plist_content`

**Files:**
- Modify: `src/dbxignore/install/macos_launchd.py`
- Modify: `tests/test_macos_launchd.py`

Today's `ProgramArguments` array is `[<bin>/dbxignored]`. After #30: `[<bin>/dbxignore, "daemon"]`.

- [ ] **Step 8.1: Read `build_plist_content`**

```bash
grep -n -A 80 "^def build_plist_content\|^def install_agent\|ProgramArguments" src/dbxignore/install/macos_launchd.py
```

- [ ] **Step 8.2: Update existing tests for the new ProgramArguments**

In `tests/test_macos_launchd.py`, find tests asserting on the `ProgramArguments` array (likely via plistlib.loads or string-content checks on the plist XML). Update assertions to expect two elements: the dbxignore path, then `daemon`.

- [ ] **Step 8.3: Run tests, verify they fail**

```bash
uv run python -m pytest tests/test_macos_launchd.py -v
```

Expected: the updated assertions fail.

- [ ] **Step 8.4: Update `build_plist_content`**

Same shape as Tasks 6/7. If `ProgramArguments` is built by templating, ensure it includes both the exe path and the args (split into multiple `<string>` elements if the plist format is XML, or both elements if assembled as a Python list before plist serialization).

```python
program_args = [str(exe_path), args_string]  # args_string from detect_invocation
# Then serialize program_args into the plist's ProgramArguments key.
```

If `args_string` could contain multiple space-separated tokens (it shouldn't for our case — always exactly `"daemon"`), split on whitespace for safety:

```python
program_args = [str(exe_path), *args_string.split()]
```

- [ ] **Step 8.5: Run tests, verify all pass**

```bash
uv run python -m pytest tests/test_macos_launchd.py -v
```

Expected: all tests pass.

- [ ] **Step 8.6: Commit**

```bash
git add src/dbxignore/install/macos_launchd.py tests/test_macos_launchd.py
git commit -m "$(cat <<'EOF'
refactor(install): macos_launchd ProgramArguments uses dbxignore daemon

launchd plist's ProgramArguments array now contains [<bin>/dbxignore,
"daemon"] instead of [<bin>/dbxignored]. Splits the args string from
detect_invocation on whitespace before appending so future args
(none today) extend cleanly.

test_macos_launchd.py ProgramArguments assertions updated.

Part of BACKLOG #30.
EOF
)"
```

---

## Task 9: Update `tests/test_install.py` dispatcher assertions

**Files:**
- Modify: `tests/test_install.py`

The install-dispatcher tests in `test_install.py` may reference the `dbxignored`-named binary in assertions (e.g., asserting that the install backend's spy received a particular invocation tuple). Sweep through and update any such references.

- [ ] **Step 9.1: Grep for `dbxignored` references in test_install.py**

```bash
grep -n "dbxignored" tests/test_install.py
```

Note each line. For each one, decide: is this asserting on the old invocation shape (update to dbxignore + daemon), or is this testing that the old shape is rejected somehow (probably delete)?

- [ ] **Step 9.2: Update each reference**

Edit the file directly:
- Assertions that mention `dbxignored` in a tuple, path, or command-line → update to `dbxignore` + `daemon` args.
- Mocks that simulate a `dbxignored` invocation → update to `dbxignore daemon`.
- Comments that reference `dbxignored` for context → update to reflect the post-#30 contract.

- [ ] **Step 9.3: Run the full install test suite**

```bash
uv run python -m pytest tests/test_install.py tests/test_install_common.py tests/test_windows_task.py tests/test_linux_systemd.py tests/test_macos_launchd.py -v
```

Expected: all pass.

- [ ] **Step 9.4: Commit**

```bash
git add tests/test_install.py
git commit -m "$(cat <<'EOF'
test(install): update dispatcher assertions for unified invocation

test_install.py dispatcher tests had references to the pre-#30
dbxignored invocation shape. Updated to expect (dbxignore, "daemon")
tuples and command lines matching detect_invocation's new contract.

Part of BACKLOG #30.
EOF
)"
```

---

## Task 10: Update `pyproject.toml`

**Files:**
- Modify: `pyproject.toml`

Two changes: drop the `dbxignored` entry-point; retarget the `dbxignore` entry-point to `__main__:main_entry`.

- [ ] **Step 10.1: Read the current `[project.scripts]` table**

```bash
grep -n -A 5 "\[project.scripts\]" pyproject.toml
```

Expected: two entries — `dbxignore = "dbxignore.cli:main"` and `dbxignored = "dbxignore.cli:daemon_main"`.

- [ ] **Step 10.2: Edit `[project.scripts]`**

Replace the table with:

```toml
[project.scripts]
dbxignore = "dbxignore.__main__:main_entry"
```

(Just one entry; `dbxignored` is gone, `dbxignore` retargets through `__main__`.)

- [ ] **Step 10.3: Confirm `dbxignore --version` still works**

```bash
uv sync   # re-generates the dbxignore.exe trampoline against the new entry
uv run dbxignore --version
```

Expected: `dbxignore, version <X.Y.Z>`. The trampoline now invokes `dbxignore.__main__:main_entry`; on non-Windows that's a thin wrapper around `cli.main()` so behavior is unchanged.

- [ ] **Step 10.4: Confirm `dbxignored` is gone**

```bash
uv run dbxignored --version 2>&1 | head -1
```

Expected: an error like `command not found: dbxignored` (or "No such command" on Windows). The trampoline shouldn't exist anymore.

If `dbxignored` is still around, run `uv sync --reinstall` to force trampoline regeneration.

- [ ] **Step 10.5: Run the full test suite**

```bash
uv run python -m pytest 2>&1 | tail -3
```

Expected: all tests pass (no test depends on `dbxignored` being on the path).

- [ ] **Step 10.6: Commit**

```bash
git add pyproject.toml
git commit -m "$(cat <<'EOF'
build: drop dbxignored entry-point; retarget dbxignore to __main__

pyproject.toml [project.scripts] reduced from two entries to one:
- dbxignore = "dbxignore.__main__:main_entry" (was dbxignore.cli:main)
- dbxignored entry-point removed entirely

The retarget routes the trampoline through main_entry (Task 2's hook)
so Windows AttachConsole logic fires before cli imports. Non-Windows
behavior unchanged — main_entry's early_init is a no-op there.

Part of BACKLOG #30.
EOF
)"
```

---

## Task 11: Update `pyinstaller/dbxignore.spec`

**Files:**
- Modify: `pyinstaller/dbxignore.spec`

Three changes: drop the second `EXE(...)` block (the `dbxignored.exe` definition); switch the remaining EXE's `console=True` to `console=False`; point the main script at `src/dbxignore/__main__.py`.

- [ ] **Step 11.1: Read the current spec**

```bash
cat pyinstaller/dbxignore.spec
```

Note the two `EXE(...)` blocks. The first is `dbxignore.exe` (`console=True`); the second is `dbxignored.exe` (`console=False`). Note any shared `Analysis(...)` block — that probably stays, possibly with the `script` value updated.

- [ ] **Step 11.2: Edit the spec**

Apply three changes to `pyinstaller/dbxignore.spec`:

1. **Entry script**: in `Analysis(...)`, change the `script` value (typically `'src/dbxignore/cli.py'` or similar) to `'src/dbxignore/__main__.py'`.
2. **Console mode**: in the remaining `EXE(...)` block (the one that emits `dbxignore.exe`), change `console=True` to `console=False`.
3. **Drop the second EXE block**: delete the entire `EXE(...)` block that emits `dbxignored.exe`. The block typically reuses the same `Analysis` / `PYZ` references but writes to `name='dbxignored'`.

- [ ] **Step 11.3: Build the binary locally to verify the spec parses**

If a Windows build environment is available:

```bash
# Windows only — skip if not on Windows
uv run pyinstaller pyinstaller/dbxignore.spec
ls dist/
```

Expected: `dist/dbxignore.exe` exists; `dist/dbxignored.exe` does NOT exist.

If not on Windows, skip this step; the CI build leg will catch spec errors.

- [ ] **Step 11.4: Commit**

```bash
git add pyinstaller/dbxignore.spec
git commit -m "$(cat <<'EOF'
build(pyinstaller): collapse Windows binaries to single dbxignore.exe

dbxignore.spec changes:
- Analysis() script repointed to src/dbxignore/__main__.py (was cli.py)
- Remaining EXE() block switches from console=True to console=False
  (Windows GUI subsystem; AttachConsole logic in _windows_console
  handles the three launch contexts)
- Second EXE() block (was emitting dbxignored.exe) deleted

After this lands, GitHub Releases ship only dbxignore.exe — half the
Windows binary weight, no more dbxignored.exe.

Part of BACKLOG #30.
EOF
)"
```

---

## Task 12: Update `pyinstaller/dbxignore-macos.spec`

**Files:**
- Modify: `pyinstaller/dbxignore-macos.spec`

Two changes: drop the second `EXE(...)` block; repoint entry script to `src/dbxignore/__main__.py`. No console-mode change on macOS.

- [ ] **Step 12.1: Read the current spec**

```bash
cat pyinstaller/dbxignore-macos.spec
```

Same shape as the Windows spec; two `EXE(...)` blocks — `dbxignore` and `dbxignored` Mach-O outputs.

- [ ] **Step 12.2: Edit the spec**

1. In `Analysis(...)`, change `script` to `'src/dbxignore/__main__.py'`.
2. Delete the second `EXE(...)` block (the `dbxignored` Mach-O definition).

- [ ] **Step 12.3: Verify the spec parses** (if on macOS)

If on macOS:

```bash
uv run pyinstaller pyinstaller/dbxignore-macos.spec
ls dist-macos/
```

Expected: `dist-macos/dbxignore` exists; `dist-macos/dbxignored` does NOT exist.

Skip on Linux/Windows; CI catches it.

- [ ] **Step 12.4: Commit**

```bash
git add pyinstaller/dbxignore-macos.spec
git commit -m "$(cat <<'EOF'
build(pyinstaller): collapse macOS Mach-O to single dbxignore

dbxignore-macos.spec changes:
- Analysis() script repointed to src/dbxignore/__main__.py
- Second EXE() block (was emitting dbxignored Mach-O) deleted

No console-mode change — macOS has no console/GUI subsystem
distinction for terminal tools.

Part of BACKLOG #30.
EOF
)"
```

---

## Task 13: Update `.github/workflows/release.yml`

**Files:**
- Modify: `.github/workflows/release.yml`

Two changes: drop the `dbxignored.exe --help` smoke test (and macOS equivalent if present); drop both pre-#30 binary names from the `gh release upload` step's artifact list.

- [ ] **Step 13.1: Read the workflow**

```bash
grep -n "dbxignored\|--help\|gh release" .github/workflows/release.yml
```

Note each occurrence of `dbxignored.exe` or `dbxignored` (macOS). The smoke test step typically runs both `./dist/dbxignore.exe --help` and `./dist/dbxignored.exe --help`; the upload step lists both binaries.

- [ ] **Step 13.2: Edit the workflow**

For each `dbxignored` reference:
- **Smoke test step**: remove the line that runs `./dist/dbxignored.exe --help` (Windows leg) or `./dist-macos/dbxignored --help` (macOS leg). Keep the corresponding `dbxignore.exe`/`dbxignore` smoke test.
- **Upload step (`gh release upload`)**: remove both binary names from the artifact list. Only `dist/dbxignore.exe` and `dist-macos/dbxignore` ship.

Pay attention to wheel + sdist build steps — those reference the package name, not the binary name; leave them.

- [ ] **Step 13.3: Verify the workflow YAML parses**

```bash
uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"
```

Expected: no exception (valid YAML).

- [ ] **Step 13.4: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "$(cat <<'EOF'
ci: drop dbxignored from release.yml smoke tests + upload list

After #30 unification, neither `dbxignored.exe` (Windows) nor
`dbxignored` (macOS Mach-O) ships from a release. Remove the
corresponding `./dist/dbxignored.exe --help` smoke-test step (and
macOS equivalent) and drop both names from the `gh release upload`
artifact list.

Wheel + sdist build steps unchanged — they reference the package
name `dbxignore`, not the binary names.

Part of BACKLOG #30.
EOF
)"
```

---

## Task 14: Update `scripts/manual-test-ubuntu-vps.sh`

**Files:**
- Modify: `scripts/manual-test-ubuntu-vps.sh`

Add a Phase 5 assertion that the systemd unit's `ExecStart=` references `dbxignore daemon` (not `dbxignored`).

- [ ] **Step 14.1: Find the existing Phase 5 install verification**

```bash
grep -n "Phase 5\|dbxignore install\|service unit file" scripts/manual-test-ubuntu-vps.sh
```

Find the line that checks the unit file exists. The new assertion goes immediately after.

- [ ] **Step 14.2: Add the ExecStart assertion**

Insert after the existing service-unit-file-exists check:

```bash
# install verb-form (PR #30) — unit invokes `dbxignore daemon`, not `dbxignored`
grep -q "^ExecStart=.*dbxignore daemon" \
    "$HOME/.config/systemd/user/dbxignore.service" \
    && pass "ExecStart uses unified 'dbxignore daemon'" \
    || fail "ExecStart still references old 'dbxignored'"
```

- [ ] **Step 14.3: Verify the script parses cleanly**

```bash
bash -n scripts/manual-test-ubuntu-vps.sh && echo "ubuntu-vps OK"
```

Expected: `ubuntu-vps OK`.

- [ ] **Step 14.4: Commit**

```bash
git add scripts/manual-test-ubuntu-vps.sh
git commit -m "$(cat <<'EOF'
test(scripts): assert systemd ExecStart uses dbxignore daemon (PR #30)

Phase 5 install verification now checks that the systemd unit's
ExecStart= line references `dbxignore daemon` rather than the pre-#30
`dbxignored` invocation. Single new grep-based assertion after the
existing unit-file-exists check.

Part of BACKLOG #30.
EOF
)"
```

---

## Task 15: Update `scripts/manual-test-macos.sh`

**Files:**
- Modify: `scripts/manual-test-macos.sh`

Add a Phase 5 assertion that the launchd plist's `ProgramArguments` includes the `daemon` subcommand.

- [ ] **Step 15.1: Find the existing Phase 5 plist check**

```bash
grep -n "Phase 5\|dbxignore install\|LaunchAgent plist" scripts/manual-test-macos.sh
```

Find the line checking the plist file exists.

- [ ] **Step 15.2: Add the ProgramArguments assertion**

Insert after the existing plist-file-exists check:

```bash
# install verb-form (PR #30) — plist invokes `dbxignore daemon`
plutil -p "$HOME/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist" \
    | grep -E '"[^"]*daemon"' >/dev/null \
    && pass "ProgramArguments includes 'daemon' subcommand" \
    || fail "ProgramArguments missing 'daemon'"
```

`plutil -p` prints plist content; `grep` finds a quoted `daemon` string in the ProgramArguments array.

- [ ] **Step 15.3: Verify the script parses**

```bash
bash -n scripts/manual-test-macos.sh && echo "macos OK"
```

Expected: `macos OK`.

- [ ] **Step 15.4: Commit**

```bash
git add scripts/manual-test-macos.sh
git commit -m "$(cat <<'EOF'
test(scripts): assert launchd ProgramArguments contains daemon (PR #30)

Phase 5 install verification now checks that the launchd plist's
ProgramArguments array includes a "daemon" string token — the new
invocation shape per #30 (was a single-element array pointing at the
dbxignored Mach-O).

Part of BACKLOG #30.
EOF
)"
```

---

## Task 16: Update `scripts/manual-test-windows.ps1`

**Files:**
- Modify: `scripts/manual-test-windows.ps1`

Three new Phase 4.5 cases (terminal output, pipe capture, file redirect) + one Phase 5 Task Scheduler XML assertion + docstring header additions for the manual-visual-verification note and the shell-wait limitation.

- [ ] **Step 16.1: Find the existing Phase 5 schtasks query**

```bash
grep -n "schtasks /Query\|Task Scheduler entry" scripts/manual-test-windows.ps1
```

Find the section that queries the registered task. The new XML-content assertion goes there.

- [ ] **Step 16.2: Add the Phase 5 XML assertion**

Insert after the existing `schtasks /Query` block:

```powershell
# install verb-form (PR #30) — task uses `dbxignore.exe daemon`
$xml = schtasks /Query /TN dbxignore /XML 2>$null
if ($xml -match "<Arguments>.*daemon.*</Arguments>") {
    Write-Pass "Task scheduled with 'dbxignore.exe daemon' invocation"
} else {
    Write-Fail "Task scheduled command does not include 'daemon' argument"
}
```

- [ ] **Step 16.3: Find the existing Phase 4.5 section**

```bash
grep -n "Phase 4.5\|Test-ExtendedCli" scripts/manual-test-windows.ps1
```

The Phase 4.5 cases live in a function (likely `Test-ExtendedCli`). Find a sensible insertion point near the end of the function.

- [ ] **Step 16.4: Add the three new Phase 4.5 cases**

Insert at the end of `Test-ExtendedCli`:

```powershell
# 4X — AttachConsole flow + stdio preservation (PR #30)
# Case 1: --version output reaches the PowerShell terminal.
$output = dbxignore --version 2>&1
if ($output -match "^dbxignore, version") {
    Write-Pass "AttachConsole: --version output reaches PowerShell terminal"
} else {
    Write-Fail "AttachConsole: --version output did not surface ($output)"
}

# Case 2: --version output is pipe-capturable. Proves the per-stream
# preservation logic doesn't overwrite the inherited pipe handle.
$captured = (dbxignore --version 2>&1 | Out-String)
if ($captured -match "dbxignore, version") {
    Write-Pass "AttachConsole: --version output capturable via pipe"
} else {
    Write-Fail "AttachConsole: pipe capture failed (got: $captured)"
}

# Case 3: --version output is redirectable to a file. Proves the
# per-stream preservation logic doesn't overwrite the inherited file
# handle.
$redirFile = "$env:TEMP\dbxignore-redir-test.txt"
dbxignore --version > $redirFile 2>&1
$fileContent = Get-Content $redirFile -Raw
if ($fileContent -match "dbxignore, version") {
    Write-Pass "AttachConsole: --version output redirectable to file"
} else {
    Write-Fail "AttachConsole: file redirect failed (got: $fileContent)"
}
Remove-Item $redirFile -ErrorAction SilentlyContinue
```

- [ ] **Step 16.5: Add the docstring header notes**

Open `scripts/manual-test-windows.ps1` and find the header docstring at the top (usually a `<#  ... #>` or `#requires` block at the file head). Add a new paragraph documenting:

1. **Manual visual verification**: "After all phases pass, manually double-click `dbxignore.exe` from File Explorer; expect a MessageBox dialog with title 'dbxignore' and body containing 'dbxignore is a command-line tool'. Click OK to dismiss. This verifies the GUI-subsystem + no-argv → MessageBox path that no scripted UI test can reliably reach."
2. **Known shell-wait limitation**: "When invoked as a foreground command, Windows shells (cmd.exe AND PowerShell) generally do not wait for the GUI-subsystem `dbxignore.exe` before returning the prompt. Pipe / redirect / variable-capture forms (the patterns used by Phase 4.5 cases above) force synchronous behavior. For interactive foreground use, cmd.exe users wrap with `start /wait dbxignore.exe ...`; PowerShell users wrap with `Start-Process -Wait dbxignore -ArgumentList ...`. Not a regression introduced by this script — it's an artifact of Windows GUI-subsystem dispatch semantics. See README's Known limitations for details."

- [ ] **Step 16.6: Verify the PowerShell parses cleanly**

```powershell
$errors = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile("$PWD\scripts\manual-test-windows.ps1", [ref]$null, [ref]$errors)
if ($errors) { $errors | ForEach-Object { Write-Output $_.Message } } else { Write-Output "OK" }
```

Expected: `OK`.

- [ ] **Step 16.7: Commit**

```bash
git add scripts/manual-test-windows.ps1
git commit -m "$(cat <<'EOF'
test(scripts): windows manual-test additions for #30

Three new Phase 4.5 cases covering the GUI-subsystem-CLI surface:
- Terminal output (dbxignore --version reaches the PowerShell terminal)
- Pipe capture (dbxignore --version | Out-String captures the version)
- File redirect (dbxignore --version > $tmp captures to file)

Together the three prove the AttachConsole + per-stream
_is_stream_connected preservation logic works end-to-end: the pipe and
file cases would have failed if _redirect_stdio_to_attached_console
overwrote inherited stdio unconditionally.

Phase 5 install verification gains a schtasks /Query /XML assertion
that the registered task's <Arguments> element contains "daemon".

Docstring header documents:
- Manual visual verification step for the MessageBox-on-double-click
  path (no scripted UI test reaches this reliably).
- The shell-wait limitation: cmd.exe AND PowerShell don't wait for
  GUI-subsystem binaries as foreground commands; pipe/redirect/capture
  force sync; explicit `start /wait` (cmd) or `Start-Process -Wait`
  (PS) for foreground scripts that need sync exit semantics.

Part of BACKLOG #30.
EOF
)"
```

---

## Task 17: Update `README.md`

**Files:**
- Modify: `README.md`

Three edits: drop `dbxignored` references from Install sections; add "Upgrading from v0.5.x" subsection; add two new Known Limitations entries.

- [ ] **Step 17.1: Grep for `dbxignored` references**

```bash
grep -n "dbxignored" README.md
```

Each match points to a section that needs updating. Common sites: Install (Windows) command examples; Install (Linux) systemd unit content; Install (macOS) launchd-related; Configuration / Logs sections.

- [ ] **Step 17.2: Update Install (Windows) section**

Find the Windows install instructions. Replace any:
- `dbxignored.exe` → `dbxignore.exe` (single binary now)
- Mentions of "downloads two binaries" → "downloads `dbxignore.exe`"
- Examples that invoke `dbxignored` → `dbxignore daemon`

- [ ] **Step 17.3: Update Install (Linux) section**

- Drop references to `dbxignored` as a separate binary.
- The systemd unit's `ExecStart` referenced in docs should now show `dbxignore daemon`.

- [ ] **Step 17.4: Update Install (macOS) section**

- Drop references to a `dbxignored` Mach-O binary.
- launchd plist references (`ProgramArguments`) should show `[/usr/local/bin/dbxignore, "daemon"]`.

- [ ] **Step 17.5: Add the "Upgrading from v0.5.x" subsection**

Add a new H2-level subsection after the existing "Upgrading from v0.2.x" subsection (or after the Install sections if no v0.2.x subsection remains):

```markdown
## Upgrading from v0.5.x

v0.6 collapses `dbxignored` into the main `dbxignore` command. After upgrading, the daemon is invoked as `dbxignore daemon` instead of `dbxignored`, and the old `dbxignored` / `dbxignored.exe` binary no longer exists.

The platform service entry (Task Scheduler, systemd unit, launchd plist) written by `dbxignore install` on v0.5.x references `dbxignored`. The cleanest migration sequence runs `dbxignore uninstall` **before** upgrading — the v0.5.x uninstall knows how to remove its own service entry. Then upgrade and re-install:

```bash
# Linux / macOS — recommended order
dbxignore uninstall                # while still on v0.5.x
uv tool upgrade dbxignore          # or: pip install --upgrade dbxignore
dbxignore install                  # registers the new service entry
```

```powershell
# Windows — recommended order
dbxignore uninstall                # while still on v0.5.x
uv tool upgrade dbxignore          # or download new dbxignore.exe (no more dbxignored.exe)
dbxignore install
```

**If you've already upgraded without uninstalling first**, you can still refresh the service entry: `dbxignore uninstall && dbxignore install` from the new binary identifies the service by the same name as v0.5.x and tolerates the old entry's `ExecStart` / `ProgramArguments` shape during the uninstall step.

If you have shell aliases or scripts that call `dbxignored` directly, replace them with `dbxignore daemon`. The two have identical behavior.
```

- [ ] **Step 17.6: Add the two Known Limitations entries**

Find or create a "## Known limitations" section in README. Add these two entries:

```markdown
### Windows: shells may not wait for the GUI-subsystem binary

`dbxignore.exe` is built as a GUI-subsystem executable to suppress the console flash at Task Scheduler logon. As a consequence — and consistent with how Windows treats every GUI-subsystem process — Windows shells generally do *not* wait for the binary to exit before returning the prompt when invoked as a foreground command. Output still reaches the terminal via `AttachConsole(ATTACH_PARENT_PROCESS)`, but the timing is asynchronous and subsequent commands' exit-code checks (`%ERRORLEVEL%` / `$LASTEXITCODE`) may run before the binary actually exits.

The limitation applies to direct foreground invocation in every shell on Windows (cmd.exe, PowerShell, the underlying shell hosted by Windows Terminal, the VS Code integrated terminal — Windows Terminal and VS Code are *hosts*, not shells; the shell waiting behavior is what matters). It does **not** apply when the binary's output is piped or redirected: in those cases the shell waits for the pipe consumer / redirect target to close, which forces synchronous exit.

Shell-specific workarounds for synchronous scripted invocations:

```cmd
:: cmd.exe — use start /wait
C:\> start /wait dbxignore --version
```

```powershell
# PowerShell — use Start-Process -Wait, or capture/redirect (any of which forces sync)
Start-Process -Wait -NoNewWindow dbxignore -ArgumentList "--version"
$version = dbxignore --version 2>&1          # variable capture forces sync
dbxignore --version > out.txt                # file redirect forces sync
dbxignore --version | Out-String             # pipe forces sync
```

Pipe (`|`) and redirect (`>`) operators work correctly in both cmd.exe and PowerShell because the binary preserves the inherited stdio handles when they're valid (only reopens against `CONOUT$` per-stream when an individual stream has no inherited handle, such as Task Scheduler launches with no stdio at all).

### Git Bash / MinTTY

On Windows, running `dbxignore.exe` directly inside Git Bash or any MinTTY-hosted shell (`mintty`, `Cygwin Terminal`) may produce no visible output. MinTTY is a pseudo-terminal that uses pipes for stdio rather than a real Windows console; `AttachConsole` finds no console handle to attach to, so the binary runs silently. Workaround: wrap the call in `winpty`:

```bash
winpty dbxignore.exe --help
```

This is a general Windows-binary-in-MinTTY issue, not specific to dbxignore. PowerShell, `cmd.exe`, Windows Terminal, and the VS Code integrated terminal are not affected.
```

- [ ] **Step 17.7: Verify the README renders cleanly**

```bash
# Run any markdown linter the project uses; otherwise just preview
grep -n "dbxignored" README.md
```

Expected: no remaining `dbxignored` references (other than potentially in the upgrade-from-v0.5.x section where they're describing the legacy state).

- [ ] **Step 17.8: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs(readme): #30 install + upgrade + known-limitations updates

Three edits:
1. Drop dbxignored references from Install (Windows/Linux/macOS)
   sections — one binary, one command everywhere now.
2. New "Upgrading from v0.5.x" subsection documents the recommended
   uninstall-before-upgrade sequence + post-upgrade fallback.
3. New "Known limitations" entries:
   - "Windows: shells may not wait for the GUI-subsystem binary" —
     honest documentation of the cmd.exe / PowerShell foreground-async
     behavior with per-shell sync workarounds.
   - "Git Bash / MinTTY" — pseudo-terminal pipe-stdio caveat with
     winpty workaround.

Part of BACKLOG #30.
EOF
)"
```

---

## Task 18: Update `CHANGELOG.md`

**Files:**
- Modify: `CHANGELOG.md`

Add the **Breaking** entry under `[Unreleased] > ### Changed`.

- [ ] **Step 18.1: Find the `[Unreleased]` section**

```bash
grep -n "## \[Unreleased\]\|^### Added\|^### Changed\|^### Fixed" CHANGELOG.md | head -20
```

Note whether `[Unreleased]` has a `### Changed` subsection yet. If not, add one (after `### Added`, before `### Fixed` per Keep-a-Changelog convention).

- [ ] **Step 18.2: Add the Breaking entry**

In `CHANGELOG.md`, under `[Unreleased] > ### Changed`, prepend:

```markdown
- **Breaking** — `dbxignored` removed as a separate entry-point. The daemon is now invoked via `dbxignore daemon` (a subcommand of the main CLI). Before: `pip install dbxignore` produced both `dbxignore` and `dbxignored` console scripts, and the GitHub Release shipped both `dbxignore.exe` and `dbxignored.exe` PyInstaller binaries. After: one `dbxignore` console script, one `dbxignore.exe` PyInstaller binary, one `dbxignore` Mach-O on macOS. The Windows binary is now built as a GUI-subsystem executable that calls `AttachConsole(ATTACH_PARENT_PROCESS)` early in `main()`: launched from any Windows shell with a console (cmd, PowerShell, Windows Terminal host) it attaches to the parent's console and stdout/stderr flow to the terminal; launched by Task Scheduler at logon (no parent console, `daemon` in argv) it runs silently; launched by Explorer double-click (no parent console, empty argv) it pops a `user32.MessageBoxW` saying "dbxignore is a command-line tool. Open Windows Terminal, PowerShell, or Command Prompt and run `dbxignore --help`." Known limitation: Windows shells (cmd.exe and PowerShell alike) generally do not wait for GUI-subsystem binaries when invoked as a foreground command — output reaches the terminal via the attached console but the timing is asynchronous, so subsequent exit-code checks may run before the binary exits. Pipe / redirect / variable-capture forms force synchronous behavior. Foreground scripts that need synchronous exit semantics: cmd.exe → `start /wait dbxignore.exe ...`; PowerShell → `Start-Process -Wait dbxignore -ArgumentList ...`. Resolves BACKLOG #30. **Migration**: the recommended sequence is `dbxignore uninstall` *before* upgrading (while still on v0.5.x — the old `uninstall` knows how to remove its own service entry), then upgrade, then `dbxignore install` to register the new entry referencing `dbxignore daemon`. If you've already upgraded without uninstalling first, `dbxignore uninstall && dbxignore install` should still refresh the service entry — both versions identify the entry by the same service name and the new uninstall code path tolerates the old `ExecStart` / `ProgramArguments` / `<Arguments>` shape. Shell aliases, scripts, and custom service configs that invoked `dbxignored` should be updated to `dbxignore daemon`. (#30)
```

- [ ] **Step 18.3: Commit**

```bash
git add CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs(changelog): add #30 Breaking entry under [Unreleased] > Changed

Documents the dbxignored removal, the AttachConsole-based three-context
Windows behavior, the cmd.exe / PowerShell shell-wait limitation with
sync workarounds, and the recommended uninstall-before-upgrade
migration sequence.

Pre-1.0 SemVer convention: Breaking change rides MINOR bump (target
v0.6.0) with explicit **Breaking** callout.

Part of BACKLOG #30.
EOF
)"
```

---

## Task 19: Update `BACKLOG.md`

**Files:**
- Modify: `BACKLOG.md`

Three edits: inline RESOLVED marker on #30's body; remove #30 from the Open list and decrement the count; add a new entry to the Resolved section under today's date heading.

- [ ] **Step 19.1: Add the inline RESOLVED marker to #30's body**

Find item #30 in BACKLOG.md (it's around line 501 historically; verify with grep). Insert immediately after the title line:

```markdown
## 30. Windows-aware single binary — collapse `dbxignore.exe` + `dbxignored.exe`

**Status: RESOLVED <YYYY-MM-DD> (PR #N).** Single `dbxignore` command everywhere. `[project.scripts].dbxignored` removed from pyproject.toml; `daemon_main` deleted from `cli.py`; PyInstaller spec collapsed (Windows + macOS) to one EXE/Mach-O each; Windows binary built as GUI-subsystem (`console=False`) with new `src/dbxignore/_windows_console.py` module performing `AttachConsole(ATTACH_PARENT_PROCESS)` early in startup. Three contexts handled in one binary: terminal launch (attach succeeds → stdio flows, per-stream `_is_stream_connected` check preserves redirected pipes/files), Task Scheduler logon (attach fails, argv has `daemon` → silent run), Explorer double-click (attach fails, argv empty → `user32.MessageBoxW` + exit). All three installers (Linux systemd, macOS launchd, Windows Task Scheduler) updated to invoke `dbxignore daemon` instead of `dbxignored`. `install/_common.py:detect_invocation` simplified from three-step "find the shim" rule to single-binary lookup. `state.is_daemon_alive` guard tuple updated to recognize `dbxignore.exe` (was `dbxignored.exe`). `.github/workflows/release.yml` smoke tests + artifact uploads pruned of `dbxignored`. Known limitation: Windows shells generally don't wait for GUI-subsystem binaries as foreground commands; documented per-shell sync workarounds (`start /wait` for cmd, `Start-Process -Wait` for PS). Git Bash / MinTTY users may need `winpty` wrapper (documented in README known limitations). Migration: `dbxignore uninstall` *before* upgrading is recommended; post-upgrade `dbxignore uninstall && dbxignore install` works as fallback. Took fix candidate (1) from the body — stdlib `ctypes` AttachConsole + MessageBoxW, no `pywin32` dependency. Shipped in v0.6.0.

[existing body of #30 below]
```

Replace `<YYYY-MM-DD>` with today's date (`date +%Y-%m-%d` to get it). Replace `PR #N` with the PR number once known (initially leave as `PR #N`; after the PR is opened, edit the file again and amend the commit, or fix in a follow-up commit on the same branch — see Task 20).

- [ ] **Step 19.2: Remove #30 from the Open list and decrement the count**

Find the `## Status > ### Open` section. The line for #30 reads something like:

```markdown
- **#30** — Windows-aware single binary via `AttachConsole(ATTACH_PARENT_PROCESS)`. ...
```

Delete that line. Decrement the count in the Open-list intro paragraph (e.g., "Sixteen items" → "Fifteen items"). If the intro mentions item #30 by name in a contextual sentence, update or remove that mention.

- [ ] **Step 19.3: Add the Resolved-section entry**

Find the `## Status > ### Resolved (reverse chronological)` section. Under the appropriate date heading (today's date — add a new `#### YYYY-MM-DD` heading at the top if none exists for today), insert:

```markdown
- **#30** (<YYYY-MM-DD>, PR #N) — Single `dbxignore` command everywhere. `[project.scripts].dbxignored` dropped from pyproject.toml; PyInstaller Windows + macOS specs collapsed to one EXE / Mach-O each; new `src/dbxignore/_windows_console.py` module (~110 LOC stdlib ctypes) performs `AttachConsole(ATTACH_PARENT_PROCESS)` early in startup via `__main__.main_entry()` (the deferred-cli-import dance ensures rich-click sees the redirected stdio). Three-context behavior on Windows: terminal launch (per-stream stdio preservation), Task Scheduler logon (silent), Explorer double-click (`user32.MessageBoxW`). Installers (Linux systemd, macOS launchd, Windows Task Scheduler) updated to invoke `dbxignore daemon`. `install/_common.py:detect_invocation` simplified; `state.is_daemon_alive` guard tuple replaced from `("python", "dbxignored")` with `("python", "dbxignore")`; `.github/workflows/release.yml` pruned. Known limitation: Windows shells don't wait for GUI-subsystem foreground invocations (documented per-shell sync workarounds in README). Twelve new tests in `tests/test_windows_console.py` cover the orchestrator decision branches plus the `_is_stream_connected` predicate plus per-stream `_redirect_stdio_to_attached_console` mixed-case behavior. Migration via `dbxignore uninstall` before upgrading + `dbxignore install` after. Took fix candidate (1) from the body — stdlib ctypes, no pywin32. Resolves a pre-1.0 SemVer Breaking change targeting v0.6.0.
```

- [ ] **Step 19.4: Commit (BACKLOG bookkeeping commits separately per revertability rule)**

```bash
git add BACKLOG.md
git commit -m "$(cat <<'EOF'
docs(backlog): close #30 (windows-aware single binary)

Inline Status: RESOLVED marker on item #30; removed from Open list
(count decremented); Resolved-section entry added under today's date.

PR # placeholder — to be amended in Task 20 once the implementation
PR is opened on GitHub and the number is known. Doc-only; separate
from the code commits per the revertability-axis split rule.
EOF
)"
```

---

## Task 20: Push, open PR, amend BACKLOG with PR number

**Files:**
- Modify: `BACKLOG.md` (amend the PR # placeholder)

After all 19 tasks above, push the branch, open the implementation PR, get the PR number, and amend BACKLOG.md's `PR #N` placeholders with the real number.

- [ ] **Step 20.1: Run the full check suite one more time**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run python -m pytest 2>&1 | tail -5
```

Expected: all clean. Pytest count = baseline + 12 (Task 1) + 2 (Task 4) - 4 (Task 3) = baseline + 10. (Adjust if Task 9 added or removed any.)

- [ ] **Step 20.2: Push the branch**

```bash
git push -u origin feat/30-windows-binary-unification
```

- [ ] **Step 20.3: Run pr-review-toolkit:code-reviewer agent**

```bash
# Required by repo hook before gh pr create
# Dispatch the code-reviewer agent (see prior PRs in this session for the
# invocation pattern). After review passes, touch the marker:
SHA=$(git rev-parse HEAD)
touch ".git/.code-review-passed-$SHA"
```

- [ ] **Step 20.4: Open the PR**

Use `gh pr create --title "..." --body-file <body.md>` with a body file referencing the spec PR (#237) and summarizing the implementation.

```bash
# Suggested title:
gh pr create --title "feat(windows): collapse dbxignore.exe + dbxignored.exe to single binary (#30)" \
    --body-file /tmp/pr-30-impl-body.md
```

PR body should include: link to spec, summary of the change, list of files, test plan, migration note pointing at the CHANGELOG.

- [ ] **Step 20.5: Amend BACKLOG.md with the real PR number**

Get the PR number from `gh pr create`'s output. Then:

```bash
# Replace 'PR #N' with the actual number (e.g., 'PR #240') in BACKLOG.md
# Two sites: the inline RESOLVED marker and the Resolved-section entry.
```

- [ ] **Step 20.6: Commit the PR# amendment and push**

```bash
git add BACKLOG.md
git commit -m "docs(backlog): fill in PR #<N> on #30 RESOLVED markers"
git push
```

The PR auto-updates with the new commit.

---

## Self-review checklist

Run through this after writing every task. Issues caught here are cheap; issues caught at PR review are not.

- [ ] **Spec coverage:** Each Section 3 file-list row is implemented by exactly one Task (or part of one Task). Files counted in the spec: 24. Tasks: 19 (some Tasks touch multiple files). Cross-check the file-list table:
  - `_windows_console.py`, `test_windows_console.py` → Task 1
  - `__main__.py` → Task 2
  - `cli.py`, `test_cli_entrypoints.py` → Task 3
  - `state.py`, `test_state.py` → Task 4
  - `install/_common.py`, `test_install_common.py` → Task 5
  - `install/windows_task.py`, `test_windows_task.py` → Task 6
  - `install/linux_systemd.py`, `test_linux_systemd.py` → Task 7
  - `install/macos_launchd.py`, `test_macos_launchd.py` → Task 8
  - `test_install.py` → Task 9
  - `pyproject.toml` → Task 10
  - `pyinstaller/dbxignore.spec` → Task 11
  - `pyinstaller/dbxignore-macos.spec` → Task 12
  - `.github/workflows/release.yml` → Task 13
  - `scripts/manual-test-ubuntu-vps.sh` → Task 14
  - `scripts/manual-test-macos.sh` → Task 15
  - `scripts/manual-test-windows.ps1` → Task 16
  - `README.md` → Task 17
  - `CHANGELOG.md` → Task 18
  - `BACKLOG.md` → Task 19 (+ amend in Task 20)
- [ ] **Placeholder scan:** All steps either show full code blocks (for tests/implementation) or exact commands (for test/lint/commit runs). No "TODO", "fill in details", or unspecified error handling.
- [ ] **Type consistency:** Function signatures across tasks:
  - `early_init() -> None` (Task 1)
  - `_attach_parent_console() -> bool` (Task 1)
  - `_redirect_stdio_to_attached_console() -> None` (Task 1)
  - `_show_help_message_box() -> None` (Task 1)
  - `_is_stream_connected(stream: object) -> bool` (Task 1)
  - `main_entry() -> None` (Task 2)
  - `detect_invocation() -> tuple[Path, str]` (Task 5)
  - All consistent across mentions.
- [ ] **Commit-message hygiene:** Each commit message follows Conventional Commits format (`<type>(<scope>): <description>`), starts the description with a non-`#` token, fits the subject-length cap from `cchk.toml`. None start with `--`.
- [ ] **Branch type:** `feat/30-windows-binary-unification` uses `feat/` per AGENTS.md's `allow_branch_types`.
- [ ] **Revertability splits:** Code commits separated from doc/bookkeeping commits per AGENTS.md. BACKLOG bookkeeping is Task 19, separate from any code task.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-13-30-windows-aware-single-binary-implementation.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Each task's commit is its own atomic checkpoint; if a subagent introduces a regression in Task N, we can isolate and fix it without unwinding earlier tasks.
2. **Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

Which approach?

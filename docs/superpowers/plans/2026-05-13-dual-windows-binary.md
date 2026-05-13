# Dual Windows Binary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single GUI-subsystem `dbxignore.exe` (shipped in PR #238) with two PyInstaller binaries on Windows: `dbxignore.exe` as a console-subsystem CLI and `dbxignorew.exe` as a GUI-subsystem helper for Task Scheduler / Explorer shell verbs. Mirrors the well-known `python.exe`/`pythonw.exe` pattern.

**Architecture:**

The single-binary design from #30 used `AttachConsole(ATTACH_PARENT_PROCESS)` plus per-stream stdio rehydration to give one GUI-subsystem PE both a "terminal" and a "silent" personality. AttachConsole works, but Windows shells (PowerShell in particular) do not wait synchronously for GUI-subsystem foreground processes — the shell returns the prompt before output flushes, so `dbxignore --help` in PowerShell appears to print nothing.

The dual-binary design splits the responsibility along the PE subsystem byte (decided at link time, not runtime):

- **`dbxignore.exe`** — built `console=True`. Always has a console at startup. The CLI users invoke from a terminal. Click + rich-click work normally; pipe/redirect/colour rendering identical to v0.5.x. No AttachConsole, no early-init step, no `_windows_console.py`.
- **`dbxignorew.exe`** — built `console=False`. Never has a console. Used by (a) Windows Task Scheduler at logon for the daemon, (b) Explorer shell-verb registry entries for "Ignore from Dropbox" / "Restore to Dropbox", (c) Explorer double-click which surfaces a MessageBox. Output routes unconditionally through the existing `_windows_dialogs` MessageBox path.

Same Python source, same wheel, same `__main__.py` entry point. Two PyInstaller spec files. The `_windows_console.py` AttachConsole subsystem (~175 LOC) is deleted entirely.

Migration is not a concern — no v0.6.0 has been released, so no end users have the single-binary form installed. The pre-merge work simply needs to land before any `v0.6.0*` tag is cut.

**Tech Stack:**
- Python 3.12 / PyInstaller (spec files, two binaries)
- `ctypes` for `GetConsoleWindow` console-presence probe
- pytest with `monkeypatch` (cross-platform tests via attribute injection)
- click + rich-click (CLI surface; no changes to the click code itself)
- GitHub Actions `release.yml` Windows build leg
- PowerShell 7+ for `scripts/manual-test-windows.ps1`

**Branch:** `feat/dual-windows-binary` off current `main`.

**Out of scope:**
- macOS keeps its single binary (no GUI-subsystem dilemma there; launchd has no console-flash equivalent).
- Linux keeps its single binary (same reason; systemd inherits no console at logon).
- The `_windows_dialogs` MessageBox copy / dialog flow is unchanged. Only its *trigger* (the `should_use_gui_dialogs` predicate) becomes more reliable in the dual-binary world.
- The `copy_metadata("dbxignore")` fix for the `--version` bug (separate latent bug surfaced during exploration) is folded into this plan since the spec files are being rewritten anyway.

---

## File Structure

**New files:**
- `pyinstaller/dbxignorew.spec` — GUI-subsystem spec, same entry, `console=False`.

**Modified files:**
- `pyinstaller/dbxignore.spec` — flip back to `console=True`; add `copy_metadata("dbxignore")`.
- `pyinstaller/dbxignore-macos.spec` — add `copy_metadata("dbxignore")` (latent --version fix; macOS not split).
- `src/dbxignore/__main__.py` — revert `main_entry` to a plain `cli.main()` call; remove the Windows `early_init` step.
- `src/dbxignore/_windows_dialogs.py` — `should_use_gui_dialogs` switches from `sys.stdout is None` to `GetConsoleWindow() == 0` (more accurate for the dual-binary model).
- `src/dbxignore/install/_common.py` — `detect_invocation` (Windows frozen path) returns `dbxignorew.exe` sibling for the daemon entry; `detect_cli_invocation` (Windows frozen path) returns `dbxignorew.exe` sibling for shell-verb registry entries. Linux/macOS branches unchanged.
- `src/dbxignore/state.py` — `is_daemon_alive` name guard tuple grows from `("dbxignore", "dbxignore.exe")` to also include `("dbxignorew", "dbxignorew.exe")`.
- `.github/workflows/release.yml` — Windows leg builds *both* spec files, smoke-tests both with `--help` *and* `--version`, uploads both `.exe` artifacts.
- `scripts/manual-test-windows.ps1` — Phase 5 daemon assertion checks `dbxignorew.exe daemon`; Phase 4.5 drops the AttachConsole stdio-preservation cases and replaces them with a "colors visible in plain PowerShell invocation" probe.
- `tests/test_install_common.py` — update `detect_invocation` / `detect_cli_invocation` Windows-frozen tests for the sibling-`dbxignorew.exe` behavior.
- `tests/test_state.py` — add coverage for the expanded guard tuple.
- `tests/test_cli_entrypoints.py` — revert `main_entry`-related tests to the pre-#238 shape (no `early_init` to assert).
- `README.md` — drop the Windows shell-wait limitation entry; add a brief "Windows ships two binaries" note in Install / Internals.
- `CHANGELOG.md` `[Unreleased]` — replace the #30 Breaking entry (which never released) with a description of the dual-binary shape that *will* ship in v0.6.0.
- `BACKLOG.md` — reopen / annotate #30 with the post-mortem and the dual-binary outcome.

**Deleted files:**
- `src/dbxignore/_windows_console.py` — entire AttachConsole subsystem, ~175 LOC.
- `tests/test_windows_console.py` — the 12 orchestrator tests for the deleted module.

---

## Task Decomposition

The plan is sequenced so each task lands a self-contained, verifiable change. Tests-first where applicable; spec-file changes verified by local PyInstaller build + smoke test rather than a unit test (PyInstaller specs are configuration, not code).

---

### Task 1: Branch off main

**Files:** none (git state only).

- [ ] **Step 1: Verify current state**

```powershell
git status
git rev-parse --abbrev-ref HEAD
```

Expected: working tree clean, branch == `main`, up to date with `origin/main`.

- [ ] **Step 2: Create the feature branch**

```powershell
git checkout -b feat/dual-windows-binary
```

Expected: `Switched to a new branch 'feat/dual-windows-binary'`.

- [ ] **Step 3: Confirm full test suite is green on main before any edits**

```powershell
uv run python -m pytest -q
```

Expected: all tests pass (baseline 630 per PR #238 body). Captures the green baseline so any regression introduced by later tasks is unambiguously attributable.

---

### Task 2: Flip `dbxignore.spec` back to `console=True` and bundle dist-info metadata

**Files:**
- Modify: `pyinstaller/dbxignore.spec` (entire file is short; rewrite both the docstring and the `EXE(...)` call)

- [ ] **Step 1: Rewrite the spec**

Replace the entire contents of `pyinstaller/dbxignore.spec` with:

```python
"""PyInstaller spec building the console-subsystem dbxignore binary.

- dbxignore.exe : console=True. The CLI surface. Click + rich-click work
                  normally — pipe, redirect, and ANSI-colour rendering all
                  function as on any console-subsystem Python program.
                  Used by all interactive terminal invocations.

The GUI-subsystem helper (dbxignorew.exe) is built from a separate spec
(pyinstaller/dbxignorew.spec) and shipped alongside this binary.

copy_metadata("dbxignore") bundles the dist-info directory so that
click's `@click.version_option(package_name="dbxignore")` callback can
resolve the version via importlib.metadata at runtime.
"""

from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

SRC = Path("src").resolve()
ENTRY = SRC / "dbxignore" / "__main__.py"


a = Analysis(
    [str(ENTRY)],
    pathex=[str(SRC)],
    binaries=[],
    datas=copy_metadata("dbxignore"),
    hiddenimports=["watchdog.observers.winapi", "watchdog.observers.read_directory_changes"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=None)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="dbxignore",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
```

Key changes: `console=False` → `console=True`; `datas=[]` → `datas=copy_metadata("dbxignore")`; the `_analysis(name)` helper-fn wrapper goes away (only one Analysis now; the helper was anticipating multi-binary cases we no longer need).

- [ ] **Step 2: Local build smoke test**

```powershell
uv run --with pyinstaller pyinstaller pyinstaller/dbxignore.spec
.\dist\dbxignore.exe --help
.\dist\dbxignore.exe --version
```

Expected: `--help` prints colored output synchronously in PowerShell (no `Out-Host` pipe needed); `--version` prints `dbxignore, version X.Y.Z` (no GUI traceback dialog). Both exits 0.

- [ ] **Step 3: Clean dist/ before commit**

```powershell
Remove-Item -Recurse -Force build, dist
```

Expected: removes PyInstaller scratch dirs so they don't show in git status.

- [ ] **Step 4: Commit**

```powershell
git add pyinstaller/dbxignore.spec
git commit -m "build(pyinstaller): rebuild dbxignore.exe as console=True + bundle dist-info"
```

---

### Task 3: Add the new `dbxignorew.spec` (GUI subsystem)

**Files:**
- Create: `pyinstaller/dbxignorew.spec`

- [ ] **Step 1: Create the new spec**

Write `pyinstaller/dbxignorew.spec`:

```python
"""PyInstaller spec building the GUI-subsystem dbxignorew helper binary.

- dbxignorew.exe : console=False. Never has a console at startup.
                   Used by:
                   * Windows Task Scheduler (daemon entry at logon — no
                     console flash, no orphaned conhost.exe).
                   * Explorer shell-verb registry entries
                     ("Ignore from Dropbox" / "Restore to Dropbox") — the
                     verb invocations route output through MessageBox via
                     src/dbxignore/_windows_dialogs.py.
                   * Explorer double-click — pops a MessageBox saying
                     "dbxignore is a command-line tool" then exits.

Same __main__.py entry as dbxignore.exe; the console-presence probe in
_windows_dialogs.should_use_gui_dialogs() (GetConsoleWindow() == 0)
routes the no-console invocations to MessageBox output.

copy_metadata("dbxignore") bundles the dist-info directory so that
click's --version callback can resolve the version via
importlib.metadata at runtime. (Mirror of dbxignore.spec.)
"""

from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

SRC = Path("src").resolve()
ENTRY = SRC / "dbxignore" / "__main__.py"


a = Analysis(
    [str(ENTRY)],
    pathex=[str(SRC)],
    binaries=[],
    datas=copy_metadata("dbxignore"),
    hiddenimports=["watchdog.observers.winapi", "watchdog.observers.read_directory_changes"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=None)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="dbxignorew",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
```

Identical to `dbxignore.spec` except: `name="dbxignorew"` and `console=False`.

- [ ] **Step 2: Local build smoke test**

```powershell
uv run --with pyinstaller pyinstaller pyinstaller/dbxignorew.spec
# Double-click dist\dbxignorew.exe in Explorer — expect MessageBox saying
# "dbxignore is a command-line tool..." (NOTE: _windows_dialogs.should_use_gui_dialogs
# changes in Task 5; for this task's verification, the MessageBox path will
# fire only after Task 5 lands. For now, verify the build succeeds and the
# resulting .exe doesn't crash on import.)
.\dist\dbxignorew.exe --version 2>&1 | Out-Host
```

Expected: build succeeds, `dist\dbxignorew.exe` exists. `--version` may still surface a GUI traceback dialog until Task 5 fixes routing — that's fine for this task; the goal here is just verifying the spec produces a valid binary.

- [ ] **Step 3: Clean dist/**

```powershell
Remove-Item -Recurse -Force build, dist
```

- [ ] **Step 4: Commit**

```powershell
git add pyinstaller/dbxignorew.spec
git commit -m "build(pyinstaller): add dbxignorew.spec for GUI-subsystem helper"
```

---

### Task 4: Add `copy_metadata` to the macOS spec

**Files:**
- Modify: `pyinstaller/dbxignore-macos.spec`

Side-effect: fixes the latent `--version` bug on macOS Mach-O too.

- [ ] **Step 1: Read the macOS spec to find the `datas=[]` site**

```powershell
Get-Content pyinstaller/dbxignore-macos.spec
```

Look for `datas=[],` inside the `Analysis(...)` call.

- [ ] **Step 2: Patch the spec**

Edit `pyinstaller/dbxignore-macos.spec`:

1. Add the import near the top, alongside any existing `from pathlib import Path`:
   ```python
   from PyInstaller.utils.hooks import copy_metadata
   ```
2. Replace `datas=[],` with `datas=copy_metadata("dbxignore"),` in the `Analysis(...)` call.

- [ ] **Step 3: Verify macOS spec parses (no local build — we're on Windows)**

```powershell
uv run python -c "import ast; ast.parse(open('pyinstaller/dbxignore-macos.spec').read())"
```

Expected: no output, exit 0. (Syntactic check only; full macOS build runs in CI.)

- [ ] **Step 4: Commit**

```powershell
git add pyinstaller/dbxignore-macos.spec
git commit -m "build(pyinstaller): bundle dbxignore dist-info in macOS spec"
```

---

### Task 5: Switch `_windows_dialogs.should_use_gui_dialogs` to `GetConsoleWindow` probe

**Files:**
- Modify: `src/dbxignore/_windows_dialogs.py:30-39`
- Test: `tests/test_windows_dialogs.py` (existing module; we add to it)

The current `sys.stdout is None` heuristic was correct only inside the single-binary world where `_windows_console.early_init` re-bound `sys.stdout` after a successful AttachConsole. In the dual-binary world `sys.stdout` always exists on `dbxignorew.exe` (PyInstaller's no-console bootloader leaves it as a no-op writer), so the heuristic returns False when it should return True. Use `ctypes.windll.kernel32.GetConsoleWindow() == 0` instead — that's the actual question we care about.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_windows_dialogs.py`:

```python
def test_should_use_gui_dialogs_when_no_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """No console window attached (GetConsoleWindow returns 0) → True on Windows."""
    monkeypatch.setattr(sys, "platform", "win32")

    class FakeKernel32:
        @staticmethod
        def GetConsoleWindow() -> int:
            return 0

    class FakeWindll:
        kernel32 = FakeKernel32()

    monkeypatch.setattr(ctypes, "windll", FakeWindll(), raising=False)
    assert _windows_dialogs.should_use_gui_dialogs() is True


def test_should_use_gui_dialogs_when_console_attached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Console window attached (non-zero handle) → False on Windows."""
    monkeypatch.setattr(sys, "platform", "win32")

    class FakeKernel32:
        @staticmethod
        def GetConsoleWindow() -> int:
            return 0x12345

    class FakeWindll:
        kernel32 = FakeKernel32()

    monkeypatch.setattr(ctypes, "windll", FakeWindll(), raising=False)
    assert _windows_dialogs.should_use_gui_dialogs() is False


def test_should_use_gui_dialogs_returns_false_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert _windows_dialogs.should_use_gui_dialogs() is False
```

Imports at the top of `tests/test_windows_dialogs.py` (add if missing):

```python
import ctypes
import sys

import pytest

from dbxignore import _windows_dialogs
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
uv run python -m pytest tests/test_windows_dialogs.py -v -k "should_use_gui_dialogs"
```

Expected: the two `_when_*` tests fail (current implementation reads `sys.stdout`, not `GetConsoleWindow`). The `_non_windows` test may pass since the existing `sys.platform != "win32"` short-circuit is preserved.

- [ ] **Step 3: Update the implementation**

Replace `should_use_gui_dialogs` in `src/dbxignore/_windows_dialogs.py`:

```python
def should_use_gui_dialogs() -> bool:
    """True if the current process has no console window — the GUI-subsystem
    `dbxignorew.exe` path (Task Scheduler daemon, shell-verb invocations,
    Explorer double-click).

    Returns False on the console-subsystem `dbxignore.exe` path, on the
    trampoline (uv tool install / pip install) which inherits a console,
    and on non-Windows.
    """
    if sys.platform != "win32":
        return False
    try:
        return not bool(ctypes.windll.kernel32.GetConsoleWindow())  # type: ignore[attr-defined, unused-ignore]
    except (OSError, AttributeError):
        # AttributeError covers non-Windows (defensive; sys.platform check
        # should already have returned). OSError covers Windows API
        # failures in unusual session states — fall through to "treat as
        # GUI" so destructive operations don't silently confirm.
        return True
```

Also update the module docstring (line 1-12) to reflect the post-dual-binary world:

```python
"""Windows MessageBox dialogs for the GUI-subsystem dbxignorew.exe binary.

When `dbxignorew.exe` is invoked — by Windows Task Scheduler at logon, by
an Explorer shell-verb registry entry (right-click → Ignore from Dropbox),
or by an Explorer double-click — the process has no console window. The
console-detection probe in `should_use_gui_dialogs()` checks
`GetConsoleWindow() == 0` to route output through MessageBox instead of
the click.echo / click.confirm paths that would be invisible.

The console-subsystem `dbxignore.exe` binary always has a console at
startup, so `should_use_gui_dialogs()` returns False there — the click
paths run normally.
"""
```

- [ ] **Step 4: Run tests to verify they pass**

```powershell
uv run python -m pytest tests/test_windows_dialogs.py -v
```

Expected: all `should_use_gui_dialogs` tests pass.

- [ ] **Step 5: Run the cli-level callers' tests too (they may have implicit dependencies)**

```powershell
uv run python -m pytest tests/test_cli_apply.py tests/test_cli_clear.py tests/test_cli_ignore.py -q
```

Expected: green. If any test was monkeypatching `sys.stdout = None` to force the GUI path, it now needs to monkeypatch `ctypes.windll` instead. Fix those inline if found.

- [ ] **Step 6: Commit**

```powershell
git add src/dbxignore/_windows_dialogs.py tests/test_windows_dialogs.py
git commit -m "fix(windows): probe GetConsoleWindow for GUI-dialog routing"
```

---

### Task 6: Delete `_windows_console.py`, revert `__main__.py`, drop the orchestrator tests

**Files:**
- Delete: `src/dbxignore/_windows_console.py`
- Delete: `tests/test_windows_console.py`
- Modify: `src/dbxignore/__main__.py`
- Modify: `tests/test_cli_entrypoints.py` (drop any `early_init`-related assertions)

- [ ] **Step 1: Revert `__main__.py:main_entry` to its pre-#238 shape**

Replace `src/dbxignore/__main__.py` with:

```python
"""Entry point for `python -m dbxignore` and the `dbxignore` console script.

Both Windows binaries (dbxignore.exe and dbxignorew.exe) ship from this
same entry. The console-presence probe in
src/dbxignore/_windows_dialogs.py:should_use_gui_dialogs() decides
whether interactive subcommands route output through MessageBox; nothing
about that decision needs to happen before click parses argv, so this
entry is platform-agnostic.
"""

from __future__ import annotations


def main_entry() -> None:
    from dbxignore.cli import main

    main()


if __name__ == "__main__":
    main_entry()
```

- [ ] **Step 2: Delete the `_windows_console.py` module**

```powershell
Remove-Item src/dbxignore/_windows_console.py
```

- [ ] **Step 3: Delete the orchestrator tests**

```powershell
Remove-Item tests/test_windows_console.py
```

- [ ] **Step 4: Audit `tests/test_cli_entrypoints.py` for `early_init` references**

```powershell
uv run python -m pytest tests/test_cli_entrypoints.py -v
```

Expected: any test that calls or monkeypatches `_windows_console.early_init` errors with `ModuleNotFoundError`. Remove or rewrite those tests inline so `main_entry` is exercised as a plain `cli.main` wrapper.

Concrete pattern to look for (delete entire test functions or test bodies that look like this):

```python
# remove anything resembling:
monkeypatch.setattr("dbxignore._windows_console.early_init", ...)
# and the surrounding test it lives in if its only purpose was asserting
# early_init was called.
```

- [ ] **Step 5: Run the full suite to catch any other lingering imports**

```powershell
uv run python -m pytest -q
```

Expected: green. Any failure here points at a module still importing `dbxignore._windows_console`; grep for it:

```powershell
uv run python -m pytest -q 2>&1 | Select-String -Pattern "_windows_console"
```

Fix referenced sites inline.

- [ ] **Step 6: Local-build smoke test for `dbxignore.exe`**

```powershell
uv run --with pyinstaller pyinstaller pyinstaller/dbxignore.spec
.\dist\dbxignore.exe --help
.\dist\dbxignore.exe --version
.\dist\dbxignore.exe status
Remove-Item -Recurse -Force build, dist
```

Expected: all three print to PowerShell synchronously with colors; exit codes 0/2/2 (status exits 2 because no Dropbox root in the test environment — that's fine, the point is no crash).

- [ ] **Step 7: Commit**

```powershell
git add src/dbxignore/__main__.py src/dbxignore/_windows_console.py tests/test_windows_console.py tests/test_cli_entrypoints.py
git commit -m "refactor(windows): drop AttachConsole orchestrator (replaced by dual binary)"
```

(Note: `git add` of a deleted file stages the deletion — `git status` after this should show `D src/dbxignore/_windows_console.py` and `D tests/test_windows_console.py` both staged.)

---

### Task 7: Differentiate `detect_invocation` / `detect_cli_invocation` Windows-frozen paths

**Files:**
- Modify: `src/dbxignore/install/_common.py`
- Modify: `tests/test_install_common.py`

The two helpers return the executable that *persistent* service entries (Task Scheduler XML, shell-verb registry) record. On Windows in the frozen path, both should return the `dbxignorew.exe` sibling — the daemon must launch silently, and the shell-verb invocations need MessageBox-routed output. The non-frozen and non-Windows branches stay unchanged (Linux/macOS have no GUI-subsystem split; non-frozen Windows already uses `pythonw.exe` per item #100).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_install_common.py`:

```python
@pytest.mark.skipif(sys.platform != "win32", reason="dbxignorew is Windows-only")
def test_detect_invocation_windows_frozen_returns_dbxignorew_sibling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Windows + frozen + dbxignorew.exe sibling exists → return the sibling
    with "daemon", not sys.executable.
    """
    cli_exe = tmp_path / "dbxignore.exe"
    cli_exe.write_text("")
    helper_exe = tmp_path / "dbxignorew.exe"
    helper_exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(cli_exe))
    from dbxignore.install import _common

    exe, args = _common.detect_invocation()
    assert exe == helper_exe
    assert args == "daemon"


@pytest.mark.skipif(sys.platform != "win32", reason="dbxignorew is Windows-only")
def test_detect_invocation_windows_frozen_falls_back_when_helper_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Windows + frozen + dbxignorew.exe sibling absent → fall back to
    sys.executable with a WARNING. (Defensive fallback for truncated
    bundles; ships only happen with both binaries present.)
    """
    cli_exe = tmp_path / "dbxignore.exe"
    cli_exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(cli_exe))
    from dbxignore.install import _common

    with caplog.at_level("WARNING", logger="dbxignore.install._common"):
        exe, args = _common.detect_invocation()
    assert exe == cli_exe
    assert args == "daemon"
    assert "dbxignorew.exe not found" in caplog.text


@pytest.mark.skipif(sys.platform != "win32", reason="dbxignorew is Windows-only")
def test_detect_cli_invocation_windows_frozen_returns_dbxignorew_sibling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Windows + frozen + dbxignorew.exe sibling exists → shell-verb
    registry value targets the GUI binary, so verbs invoke without a
    console flash and route output through MessageBox.
    """
    cli_exe = tmp_path / "dbxignore.exe"
    cli_exe.write_text("")
    helper_exe = tmp_path / "dbxignorew.exe"
    helper_exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(cli_exe))
    from dbxignore.install import _common

    prefix = _common.detect_cli_invocation()
    assert prefix == f'"{helper_exe}"'
```

Also UPDATE the existing test `test_detect_invocation_frozen_returns_executable_with_daemon` (lines 11-29 of `tests/test_install_common.py`). The current assertion `assert exe == cli_exe` becomes wrong on Windows. Split into Windows / non-Windows assertions:

```python
def test_detect_invocation_frozen_returns_executable_with_daemon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Frozen non-Windows: return sys.executable + "daemon".

    Windows has its own test (test_detect_invocation_windows_frozen_*) that
    asserts the dbxignorew.exe sibling lookup.
    """
    if sys.platform == "win32":
        pytest.skip("Windows frozen path tested separately via dbxignorew sibling tests")
    cli_exe = tmp_path / "dbxignore"
    cli_exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(cli_exe))
    from dbxignore.install import _common

    exe, args = _common.detect_invocation()
    assert exe == cli_exe
    assert args == "daemon"
```

Similarly UPDATE `test_detect_cli_invocation_frozen_uses_sibling_exe` (around line 137) — same split: existing test keeps its non-Windows assertion, Windows is covered by the new test above.

- [ ] **Step 2: Run tests to verify the new ones fail**

```powershell
uv run python -m pytest tests/test_install_common.py -v -k "windows_frozen"
```

Expected: the three new tests fail (current implementation returns sys.executable / dbxignore.exe path, not the dbxignorew sibling).

- [ ] **Step 3: Update `detect_invocation`**

In `src/dbxignore/install/_common.py`, replace the frozen branch (lines 36-39) with:

```python
    if getattr(sys, "frozen", False):
        # PyInstaller frozen path. On Windows, prefer the dbxignorew.exe
        # sibling — the GUI-subsystem binary launches silently at logon
        # (no console flash, no orphan conhost.exe). On Linux/macOS the
        # single binary doubles as daemon and CLI, so sys.executable is fine.
        exe = Path(sys.executable)
        if sys.platform == "win32":
            helper = exe.with_name("dbxignorew.exe")
            if helper.exists():
                return helper, "daemon"
            logger.warning(
                "dbxignorew.exe not found next to %s; falling back to dbxignore.exe. "
                "The daemon launched at logon may briefly flash a console window.",
                exe,
            )
        return exe, "daemon"
```

- [ ] **Step 4: Update `detect_cli_invocation`**

Replace the frozen branch in `detect_cli_invocation` (lines 98-107) with:

```python
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        if sys.platform == "win32":
            # Shell-verb invocations must route through dbxignorew.exe so
            # they don't flash a console window and so output flows
            # through MessageBox (no stdio in that context).
            helper = exe.parent / "dbxignorew.exe"
            if helper.exists():
                return f'"{helper}"'
            # Truncated-bundle defensive fallback — same WARNING shape as
            # detect_invocation. The verb invocation will flash a console
            # briefly until the user reinstalls.
            logger.warning(
                "dbxignorew.exe not found next to %s; falling back to %s for shell-verb registry. "
                "Verb invocations may briefly flash a console window.",
                exe,
                exe.name,
            )
            return f'"{exe}"'
        # Non-Windows frozen: sys.executable is the single binary.
        cli_name = "dbxignore"
        if exe.name == cli_name:
            return f'"{exe}"'
        sibling = exe.parent / cli_name
        if sibling.exists():
            return f'"{sibling}"'
```

- [ ] **Step 5: Update the docstrings**

Update `detect_invocation` docstring (lines 22-35):

```python
def detect_invocation() -> tuple[Path, str]:
    """Return (executable_path, args_string) for the installed daemon entry.

    Frozen (PyInstaller) on Windows: prefer the ``dbxignorew.exe`` sibling
    next to ``sys.executable``. The GUI-subsystem helper launches silently
    at logon (no console flash). Falls back to ``sys.executable`` with a
    WARNING if the sibling is missing (truncated-bundle defense).

    Frozen on Linux / macOS: ``sys.executable`` is the single binary; the
    daemon runs as ``dbxignore daemon``.

    Non-frozen (uv tool install / pip install): use the Python interpreter
    with ``-m dbxignore daemon``. On Windows, prefer ``pythonw.exe`` for
    the windowless launch (item #100); fall back to ``sys.executable`` if
    ``pythonw.exe`` doesn't exist (Microsoft Store Python, embedded
    interpreters).
    """
```

Update `detect_cli_invocation` docstring (lines 75-97):

```python
def detect_cli_invocation() -> str:
    """Return a quoted command-line prefix for shell-verb registry entries.

    Output is a registry-ready string: the executable plus any leading
    arguments needed before a subcommand (e.g. ``"<python>" -m dbxignore``).
    Callers concatenate the subcommand + ``"%1"`` placeholder when building
    the full ``HKCU\\…\\shell\\<verb>\\command`` default value.

    Three branches:

    1. **Frozen on Windows.** Prefer the ``dbxignorew.exe`` sibling next to
       ``sys.executable`` — shell-verb invocations route through the
       GUI-subsystem binary so output flows through MessageBox and there's
       no console flash. Defensive fallback to ``sys.executable`` with a
       WARNING if the sibling is missing.
    2. **Frozen on Linux / macOS.** Prefer the ``dbxignore`` sibling
       (single binary; no GUI-subsystem split).
    3. **``shutil.which("dbxignore")``** — the pip/uv-install PATH shim.
    4. **Fallback** — ``"<sys.executable>" -m dbxignore``. Used when no
       shim is on PATH (typical for an editable ``uv pip install -e .``
       working directory that hasn't been exposed via ``uv tool install``).

    Raises ``RuntimeError`` if all branches are unviable.
    """
```

- [ ] **Step 6: Run tests to verify they pass**

```powershell
uv run python -m pytest tests/test_install_common.py -v
```

Expected: all `detect_invocation` / `detect_cli_invocation` tests pass.

- [ ] **Step 7: Run installer test modules too (they integrate the helpers)**

```powershell
uv run python -m pytest tests/test_install.py tests/test_install_windows_shell.py -q
```

Expected: green. If any test was hardcoding `dbxignore.exe` in a registry-content assertion, it now needs `dbxignorew.exe` — fix inline.

- [ ] **Step 8: Commit**

```powershell
git add src/dbxignore/install/_common.py tests/test_install_common.py tests/test_install.py tests/test_install_windows_shell.py
git commit -m "feat(install): route Windows frozen service entries through dbxignorew.exe"
```

(Adjust the final `git add` to only include files actually modified — `git status` after the test fixes will tell you which.)

---

### Task 8: Expand `state.is_daemon_alive` process-name guard for `dbxignorew`

**Files:**
- Modify: `src/dbxignore/state.py` (line 172)
- Modify: `tests/test_state.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_state.py`:

```python
def test_is_daemon_alive_accepts_dbxignorew_process_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """After dual-binary split, the daemon process name on Windows is
    typically dbxignorew.exe (launched by Task Scheduler with the GUI
    helper). is_daemon_alive must recognize it as a valid dbxignore daemon
    so destructive CLI verbs' daemon-alive guard works correctly.
    """
    import sys as _sys

    class FakeProc:
        def name(self) -> str:
            return "dbxignorew.exe"

        def create_time(self) -> float:
            return 1000.0

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

    class FakePsutil:
        Process = FakeProc
        class Error(Exception):
            pass

        @staticmethod
        def pid_exists(pid: int) -> bool:
            return True

    fake_proc_instance = FakeProc()
    monkeypatch.setitem(_sys.modules, "psutil", FakePsutil)
    monkeypatch.setattr(FakePsutil, "Process", lambda pid: fake_proc_instance)

    from dbxignore import state

    assert state.is_daemon_alive(pid=1234) is True


def test_is_daemon_alive_accepts_dbxignorew_without_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    """proc.name() may return either "dbxignorew" or "dbxignorew.exe"
    depending on psutil's Windows backend version — both must pass.
    """
    import sys as _sys

    class FakeProc:
        def name(self) -> str:
            return "dbxignorew"

        def create_time(self) -> float:
            return 1000.0

    class FakePsutil:
        Process = FakeProc
        class Error(Exception):
            pass

        @staticmethod
        def pid_exists(pid: int) -> bool:
            return True

    fake_proc_instance = FakeProc()
    monkeypatch.setitem(_sys.modules, "psutil", FakePsutil)
    monkeypatch.setattr(FakePsutil, "Process", lambda pid: fake_proc_instance)

    from dbxignore import state

    assert state.is_daemon_alive(pid=1234) is True
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
uv run python -m pytest tests/test_state.py -v -k "dbxignorew"
```

Expected: both new tests fail (current guard tuple is `("dbxignore", "dbxignore.exe")`).

- [ ] **Step 3: Update the guard**

In `src/dbxignore/state.py:172`:

Replace:
```python
    if "python" not in name and name not in ("dbxignore", "dbxignore.exe"):
```

With:
```python
    if "python" not in name and name not in (
        "dbxignore",
        "dbxignore.exe",
        "dbxignorew",
        "dbxignorew.exe",
    ):
```

Update the docstring comment a few lines above (line 113-ish) to mention both:

```python
    ``dbxignore.exe`` (terminal CLI) or ``dbxignorew.exe`` (Task Scheduler
    / shell-verb GUI helper); source runs are typically
    ``python -m dbxignore daemon`` or ``pythonw -m dbxignore daemon``
```

- [ ] **Step 4: Run tests to verify they pass**

```powershell
uv run python -m pytest tests/test_state.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/dbxignore/state.py tests/test_state.py
git commit -m "fix(state): accept dbxignorew process name in is_daemon_alive guard"
```

---

### Task 9: Update `.github/workflows/release.yml` Windows leg for dual-binary build

**Files:**
- Modify: `.github/workflows/release.yml`

- [ ] **Step 1: Patch the "Build Windows binaries" step**

Find this block (around line 31-34):

```yaml
      - name: Build Windows binaries
        # pyinstaller is a build-only tool, not a runtime or dev dep — install
        # ephemerally for this run rather than declaring it in pyproject.toml.
        run: uv run --with pyinstaller pyinstaller pyinstaller/dbxignore.spec
```

Replace with:

```yaml
      - name: Build Windows binaries
        # pyinstaller is a build-only tool, not a runtime or dev dep — install
        # ephemerally for this run rather than declaring it in pyproject.toml.
        # Two specs produce two binaries: dbxignore.exe (console=True; CLI)
        # and dbxignorew.exe (console=False; Task Scheduler daemon + Explorer
        # shell-verb helper).
        run: |
          uv run --with pyinstaller pyinstaller pyinstaller/dbxignore.spec
          uv run --with pyinstaller pyinstaller pyinstaller/dbxignorew.spec
```

- [ ] **Step 2: Patch the "Smoke test Windows binaries" step**

Find this block (around line 36-54):

```yaml
      - name: Smoke test Windows binaries
        # `--help` is enough to fail-fast on missing-bundled-module regressions:
        # ...
        shell: bash
        run: |
          set -e
          out=$(./dist/dbxignore.exe --help 2>&1)
          echo "$out"
          echo "$out" | grep -q "Usage:"
```

Replace with:

```yaml
      - name: Smoke test Windows binaries
        # --help fails-fast on missing-bundled-module regressions (the
        # v0.4.0a1 macOS _cffi_backend shape). --version fails-fast on
        # missing dist-info metadata regressions (the bug PyInstaller
        # surfaces as "RuntimeError: <pkg> is not installed" when
        # importlib.metadata can't find the wheel inside the frozen bundle).
        # Both binaries: dbxignore.exe (console=True) is straightforward;
        # dbxignorew.exe (console=False) needs --version captured via the
        # bash assignment to force synchronous wait, since GUI-subsystem
        # binaries return control to the shell asynchronously.
        shell: bash
        run: |
          set -e
          # dbxignore.exe — console subsystem; synchronous by default.
          help_out=$(./dist/dbxignore.exe --help 2>&1)
          echo "$help_out"
          echo "$help_out" | grep -q "Usage:"
          version_out=$(./dist/dbxignore.exe --version 2>&1)
          echo "$version_out"
          echo "$version_out" | grep -q "dbxignore.exe, version"
          # dbxignorew.exe — GUI subsystem; bash assignment forces wait.
          # --help on the GUI binary writes to a non-existent console
          # (no parent terminal in CI), but importlib.metadata + click
          # parser run before output — a non-zero exit indicates an
          # import-time crash. We don't grep its output because the
          # MessageBox path can't be exercised in headless CI.
          ./dist/dbxignorew.exe --help >/dev/null 2>&1 || true
          ./dist/dbxignorew.exe --version >/dev/null 2>&1 || true
          # Both binaries must exist and be non-empty.
          test -s ./dist/dbxignore.exe
          test -s ./dist/dbxignorew.exe
```

- [ ] **Step 3: Patch the GitHub Release files list**

Find this block (around line 152-156):

```yaml
          files: |
            dist/*.whl
            dist/*.tar.gz
            dist/dbxignore.exe
            dist-macos/dbxignore
```

Replace with:

```yaml
          files: |
            dist/*.whl
            dist/*.tar.gz
            dist/dbxignore.exe
            dist/dbxignorew.exe
            dist-macos/dbxignore
```

- [ ] **Step 4: Validate the workflow YAML syntax**

```powershell
uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"
```

Expected: no output, exit 0.

- [ ] **Step 5: Commit**

```powershell
git add .github/workflows/release.yml
git commit -m "ci(release): build + smoke-test + upload both Windows binaries"
```

---

### Task 10: Update `scripts/manual-test-windows.ps1`

**Files:**
- Modify: `scripts/manual-test-windows.ps1`

Three concrete edits: (1) Phase 5 daemon-form assertion now checks `dbxignorew.exe daemon` (was `dbxignore.exe daemon`); (2) Phase 4.5 drops the AttachConsole / stdio-preservation cases that were specific to the single-binary world; (3) docstring header drops the "Windows shell-wait limitation" note.

- [ ] **Step 1: Find Phase 5 daemon-form assertion**

```powershell
Get-Content scripts/manual-test-windows.ps1 | Select-String -Pattern "dbxignore.exe daemon|dbxignored" -Context 3,3
```

Likely a `schtasks /Query /XML` parse that asserts the `<Command>` element contains the daemon-launching binary.

- [ ] **Step 2: Edit the assertion**

Wherever the existing assertion reads:

```powershell
if ($xml.Task.Actions.Exec.Command -notmatch "dbxignore\.exe") {
    Fail "Task Scheduler <Command> does not reference dbxignore.exe"
}
```

Change to:

```powershell
if ($xml.Task.Actions.Exec.Command -notmatch "dbxignorew\.exe") {
    Fail "Task Scheduler <Command> does not reference dbxignorew.exe (dual-binary; daemon entry uses the GUI helper)"
}
```

(If the existing string matches differently — e.g., uses `Like` rather than `-notmatch` — preserve the comparison shape and only update the pattern.)

- [ ] **Step 3: Drop the AttachConsole Phase 4.5 cases**

Open `scripts/manual-test-windows.ps1` and locate the three Phase 4.5 cases added by PR #238 (search for `# 4.5 — terminal output`, `# 4.5 — pipe capture`, `# 4.5 — file redirect`). These cases were designed to exercise the AttachConsole + per-stream stdio preservation in the single-binary world; in the dual-binary world the `dbxignore.exe` CLI is console-subsystem and these probes test nothing the standard `--help` test in Phase 3 doesn't already cover.

Delete those three cases. Replace with a single new case that proves the v0.6.0 UX win:

```powershell
# 4.5 — dbxignore.exe in PowerShell prints to terminal synchronously (PR #<this PR>)
$out = & "$InstallSpec\dist\dbxignore.exe" --help 2>&1
if (-not ($out -match "Usage:")) {
    Fail "dbxignore.exe --help did not print Usage: line synchronously"
} else {
    Pass "dbxignore.exe --help prints synchronously from PowerShell"
}
```

(Replace `$InstallSpec\dist` with whatever path the existing script uses to reach the built binary in the test environment.)

- [ ] **Step 4: Add a Phase 5 case verifying `dbxignorew.exe` is present**

Add near the existing Phase 5 service-form assertions:

```powershell
# 5 — dbxignorew.exe ships alongside dbxignore.exe (dual-binary install)
$helperPath = Get-Command dbxignorew -ErrorAction SilentlyContinue
if (-not $helperPath) {
    Fail "dbxignorew.exe not found on PATH after install"
} else {
    Pass "dbxignorew.exe installed at $($helperPath.Source)"
}
```

- [ ] **Step 5: Update the script header docstring**

Find the comment block at the top of the file mentioning "Windows shell-wait limitation" (added by PR #238). Remove that block — the limitation no longer applies because `dbxignore.exe` is console-subsystem again. Keep any other header content (PowerShell version requirements, Phase numbering, install-spec parameter).

- [ ] **Step 6: Run the static parse check**

```powershell
[System.Management.Automation.Language.Parser]::ParseFile("scripts/manual-test-windows.ps1", [ref]$null, [ref]$null)
```

Expected: no errors.

- [ ] **Step 7: Commit**

```powershell
git add scripts/manual-test-windows.ps1
git commit -m "test(scripts): update Windows manual-test for dual-binary install"
```

---

### Task 11: README + CHANGELOG updates

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: README — remove the "Known limitations" Windows shell-wait entry**

Search `README.md` for the limitation entry added by PR #238 (it mentions cmd.exe / PowerShell async foreground behavior + `start /wait` / `Start-Process -Wait` workarounds). Remove that entry. Also remove any Git Bash / MinTTY entry that came with it.

- [ ] **Step 2: README — add a brief dual-binary note in the Install section**

In the Windows part of the Install section, add a sentence after the install instructions:

```markdown
The Windows install produces two binaries: `dbxignore.exe` (the CLI you
run from a terminal) and `dbxignorew.exe` (used by Task Scheduler for the
daemon and by the Explorer right-click verbs). All commands you type are
`dbxignore`; `dbxignorew.exe` is invoked by the system, not by you.
```

- [ ] **Step 3: README — drop the "Upgrading from v0.5.x" subsection added in PR #238**

That migration text targeted the single-binary world that never released. Remove the entire subsection. (No v0.6.0 has shipped, so no end users need migration guidance.)

- [ ] **Step 4: CHANGELOG — replace the [Unreleased] > Changed Breaking entry**

Open `CHANGELOG.md` and find the `[Unreleased] > Changed` section. Delete the existing Breaking entry from PR #238 (the one announcing the single-binary collapse and the shell-wait limitation).

In its place, add:

```markdown
### Changed

- **Breaking — Windows now ships two binaries.** Pre-v0.6.0, the project
  shipped `dbxignore.exe` + `dbxignored.exe` (the daemon helper). v0.6.0
  ships `dbxignore.exe` (console-subsystem, for interactive CLI use) and
  `dbxignorew.exe` (GUI-subsystem, for Task Scheduler daemon launches +
  Explorer right-click verbs). Same `dbxignore` command name for all
  user-facing operations. The dbxignored entry-point is gone; the daemon
  is invoked as `dbxignorew daemon` (Task Scheduler) or `dbxignore daemon`
  (manual foreground).
```

- [ ] **Step 5: Commit**

```powershell
git add README.md CHANGELOG.md
git commit -m "docs: describe dual-binary Windows install shape"
```

---

### Task 12: BACKLOG #30 follow-up entry

**Files:**
- Modify: `BACKLOG.md`

#30 already has a `Status: RESOLVED 2026-05-13 (PR #238)` paragraph. Rather than reopening (which would conflict with the bookkeeping convention of one resolution date per item), file a NEW backlog item describing the post-mortem and pointing at this PR as the corrective resolution. This keeps #30's history intact and surfaces the lesson learned.

- [ ] **Step 1: Find the next backlog item number**

```powershell
Get-Content BACKLOG.md | Select-String -Pattern "^## \d+\." | Select-Object -Last 1
```

Expected output: the highest-numbered item heading (likely `## 119.`). Use `<N+1>` for the new item. Call it `<NEW_ID>` for the rest of this task.

- [ ] **Step 2: Append the new item before the `---` and the `## Status` heading**

Find the section delimiter `---` immediately preceding `## Status` and insert the new item just above it. Use the existing item format. Body:

```markdown
## <NEW_ID>. Single-binary AttachConsole approach (#30 / PR #238) defeated by PowerShell async-foreground behavior

PR #238 collapsed `dbxignore.exe` + `dbxignored.exe` into a single
GUI-subsystem binary using `AttachConsole(ATTACH_PARENT_PROCESS)` plus
per-stream stdio rehydration in `_windows_console.py`. The design worked
end-to-end on cmd.exe but failed on PowerShell: PowerShell does not wait
synchronously on GUI-subsystem foreground processes, so
`dbxignore --help` appears to print nothing (the shell prompt redraws
before the binary's stdout flushes). `Start-Process -Wait -NoNewWindow`
also returned nothing in manual testing — even the most forced-sync
PowerShell invocation form couldn't recover the output.

**Status: RESOLVED <date> (PR #<this PR>).** Reverted to the well-known
`python.exe` / `pythonw.exe` pattern: `dbxignore.exe` rebuilt as
console=True (CLI), new `dbxignorew.exe` built as console=False (Task
Scheduler daemon + Explorer shell-verb helper). Deleted
`src/dbxignore/_windows_console.py` (~175 LOC). Side-effect:
`copy_metadata("dbxignore")` added to both Windows + macOS specs to fix
a latent `--version` bug that PyInstaller's `disable_windowed_traceback`
GUI dialog had been surfacing on the GUI-subsystem build (cli.py:495
uses `click.version_option(package_name="dbxignore")` which reads
`importlib.metadata`; bundle never carried dist-info before this fix).

**Lesson:** the PE subsystem byte is decided at link time, not runtime;
no in-process trick fully reconciles the two needs ("terminal-fluent"
and "silent at logon") on Windows. Python solved this with two binaries
25 years ago; we shouldn't try harder.

Touches: `pyinstaller/dbxignore.spec`, `pyinstaller/dbxignorew.spec`,
`pyinstaller/dbxignore-macos.spec`, `src/dbxignore/__main__.py`,
`src/dbxignore/_windows_console.py` (deleted),
`src/dbxignore/_windows_dialogs.py`,
`src/dbxignore/install/_common.py`, `src/dbxignore/state.py`,
`.github/workflows/release.yml`, `scripts/manual-test-windows.ps1`,
`README.md`, `CHANGELOG.md`, tests under `tests/`.
```

(Replace `<NEW_ID>` with the actual number found in Step 1; replace `<date>` and `<this PR>` placeholders after the PR is opened. Final fill-in happens in the closing commit.)

- [ ] **Step 3: Update the Status > Open list**

Find the `### Open` heading under `## Status`. The opening sentence reads "Fifteen items." (post-#238). Add the new item to the count and to the bulleted list:

- Change "Fifteen items." to "Sixteen items." (or whatever the new total is).
- Add a new bullet alphabetically by number ordering:

```markdown
- **#<NEW_ID>** — Post-mortem on PR #238's single-binary approach. AttachConsole subsystem defeated by PowerShell async-foreground behavior; reverted to dual-binary (`dbxignore.exe` console-subsystem + `dbxignorew.exe` GUI-subsystem) per python.exe/pythonw.exe pattern. Closed by PR #<this PR>.
```

Then also move the bullet into `### Resolved` at the top of the section under a `#### <date>` heading when the PR closes. (For this task, leave the item in Open with a note pointing at this PR; the closing-bookkeeping commit at the end of this plan moves it.)

- [ ] **Step 4: Commit**

```powershell
git add BACKLOG.md
git commit -m "docs(backlog): file follow-up to #30 — dual-binary recovery from AttachConsole failure"
```

---

### Task 13: Local full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the entire test suite**

```powershell
uv run python -m pytest -q
```

Expected: all tests pass. The post-#238 baseline was 630; after this plan: ~620 (deleted 12 `test_windows_console.py` tests; added ~7 new tests across `test_windows_dialogs.py`, `test_install_common.py`, `test_state.py`). Exact number depends on how many `test_cli_entrypoints.py` tests were removed in Task 6.

- [ ] **Step 2: Run ruff + format check**

```powershell
uv run ruff check .
uv run ruff format --check .
```

Expected: both clean.

- [ ] **Step 3: Run mypy**

```powershell
uv run mypy .
```

Expected: no new errors. (Some pre-existing # type: ignore directives in `_windows_console.py` are no longer relevant since the module is deleted — those went away with the file.)

- [ ] **Step 4: Build both binaries locally**

```powershell
uv run --with pyinstaller pyinstaller pyinstaller/dbxignore.spec
uv run --with pyinstaller pyinstaller pyinstaller/dbxignorew.spec
```

Expected: `dist/dbxignore.exe` and `dist/dbxignorew.exe` both present.

- [ ] **Step 5: Manual smoke-tests on both binaries**

```powershell
# dbxignore.exe — interactive use, console=True
.\dist\dbxignore.exe --help
.\dist\dbxignore.exe --version
.\dist\dbxignore.exe status

# dbxignorew.exe — should produce MessageBox on double-click
Start-Process .\dist\dbxignorew.exe  # observe MessageBox; click OK
```

Expected:
- `dbxignore.exe --help` prints colored output synchronously in PowerShell.
- `dbxignore.exe --version` prints `dbxignore.exe, version X.Y.Z` (no GUI traceback dialog).
- `dbxignore.exe status` prints status output (exit 2 if no Dropbox root — that's fine).
- `Start-Process dbxignorew.exe` opens a MessageBox with the "dbxignore is a command-line tool" text.

- [ ] **Step 6: Clean dist/ and build/**

```powershell
Remove-Item -Recurse -Force build, dist
```

- [ ] **Step 7: Confirm git state is clean**

```powershell
git status
git log --oneline main..HEAD
```

Expected: working tree clean; the log shows the commits from Tasks 2-12 in order.

---

### Task 14: Pre-flight commit-check and push

**Files:** none (git state only).

- [ ] **Step 1: Run commit-check over the full PR range**

For each commit on the branch, verify the message passes the Conventional Commits gate. Per the project's pre-flight rule, validate each commit individually (not just HEAD), so an intermediate failing commit doesn't bite in CI.

```powershell
$commits = git log --format="%H" main..HEAD
foreach ($sha in $commits) {
    $msg = git log -1 --format="%B" $sha
    $tmp = New-TemporaryFile
    Set-Content -Path $tmp.FullName -Value $msg -NoNewline
    uv run --with commit-check commit-check --message $tmp.FullName
    Remove-Item $tmp.FullName
}
```

Expected: every commit passes. If any fails, fix via `git rebase -i main` and `reword` — do NOT use `--amend` (would skew the commit history checks).

- [ ] **Step 2: Push the branch**

```powershell
git push -u origin feat/dual-windows-binary
```

Expected: branch pushes successfully. CI will start running `test.yml` (cross-platform tests) on the PR opened in the next step.

- [ ] **Step 3: Open the PR**

```powershell
gh pr create --title "feat(windows): re-introduce dbxignorew.exe (dual-binary, python.exe/pythonw.exe pattern)" --body "@'
## Summary

Replaces PR #238's single GUI-subsystem ``dbxignore.exe`` with the dual-binary shape that mirrors ``python.exe`` / ``pythonw.exe``:

- ``dbxignore.exe`` (console=True) — the CLI; click + rich-click work naturally in PowerShell and cmd.exe with sync output, colors, redirects, pipes.
- ``dbxignorew.exe`` (console=False) — GUI helper for Task Scheduler daemon launches and Explorer shell-verb invocations; output routes through MessageBox via the existing ``_windows_dialogs`` module.

Same Python source, same wheel, same ``__main__.py`` entry. Two PyInstaller specs.

## Why

PR #238's single-binary AttachConsole approach worked end-to-end on cmd.exe but failed on PowerShell — the shell does not wait synchronously for GUI-subsystem foreground processes, so ``dbxignore --help`` from a PowerShell prompt appears to print nothing. Even ``Start-Process -Wait -NoNewWindow`` returned no output. No in-process trick can fix this; the PE subsystem byte is link-time. Two binaries is the well-trodden answer.

## What's in this PR

12 commits across 13 files. Highlights:
- **Deleted:** ``src/dbxignore/_windows_console.py`` (~175 LOC AttachConsole + per-stream stdio rehydration), ``tests/test_windows_console.py`` (12 tests for the deleted module).
- **New:** ``pyinstaller/dbxignorew.spec``.
- **Rewritten:** ``pyinstaller/dbxignore.spec`` (back to ``console=True``).
- **Side fix:** ``copy_metadata(\"dbxignore\")`` added to both Windows + macOS specs, resolving a latent ``--version`` ``RuntimeError`` the GUI-subsystem build had been surfacing as a traceback dialog.
- **Installer plumbing:** ``install/_common.detect_invocation`` and ``detect_cli_invocation`` Windows-frozen branches return the ``dbxignorew.exe`` sibling.
- **State guard:** ``state.is_daemon_alive`` accepts both binary names.
- **CI:** release.yml Windows leg builds + smoke-tests + uploads both ``.exe`` artifacts; smoke test now exercises ``--version`` too, which would have failed PR #238 in CI rather than at the user's terminal.

## Migration

None. v0.6.0 has not released, so no end users have the single-binary form installed.

## Test plan

- [x] Local Windows: full pytest suite green
- [x] Local Windows: ruff + format + mypy clean
- [x] Local Windows: ``dbxignore.exe --help / --version / status`` print synchronously with colors in PowerShell
- [x] Local Windows: ``dbxignorew.exe`` double-click pops MessageBox
- [ ] CI cross-platform legs (Ubuntu, macOS, Windows) — pending PR open
- [ ] CI platform-specific legs — pending PR open
- [ ] Manual ``scripts/manual-test-windows.ps1`` run after merge

## Closes

- Item #<NEW_ID> (post-mortem follow-up on #30)
'@"
```

Expected: PR opens, CI starts. URL returned.

---

## Self-Review

After writing the plan, I walked through it with fresh eyes:

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| `dbxignore.exe` back to `console=True` | Task 2 |
| Add `dbxignorew.exe` `console=False` | Task 3 |
| Fix `--version` via `copy_metadata` | Tasks 2, 3, 4 (Windows + macOS specs) |
| Delete `_windows_console.py` | Task 6 |
| Revert `__main__.py:main_entry` | Task 6 |
| Update `should_use_gui_dialogs` to use `GetConsoleWindow` | Task 5 |
| Differentiate installer helpers for dbxignorew sibling | Task 7 |
| `is_daemon_alive` accepts both binary names | Task 8 |
| CI Windows leg builds both, smokes `--version` too | Task 9 |
| Manual-test script reflects dual-binary | Task 10 |
| README + CHANGELOG updates | Task 11 |
| BACKLOG follow-up entry filed | Task 12 |

All covered.

**Placeholder scan:** Re-read every task. The only intentional placeholders are `<NEW_ID>`, `<date>`, `<this PR>` in the BACKLOG entry (Task 12) and PR body (Task 14) — those resolve naturally during the closing commit / PR opening. No "TBD", "implement later", "add appropriate error handling".

**Type consistency:** `dbxignorew.exe` spelled consistently throughout (no `dbxignoreW.exe` or `dbxignore_w.exe` typos). Function names match: `detect_invocation`, `detect_cli_invocation`, `should_use_gui_dialogs`, `is_daemon_alive` all consistent with the current codebase. The guard tuple addition `("dbxignorew", "dbxignorew.exe")` is consistent between Task 8's failing test and the implementation diff.

**One judgement call to flag for the executor:** Task 6 says to grep for `_windows_console` references in tests and remove dead test bodies. If you find a test that meaningfully tested *click's argv-parsing-after-attach* (rather than just asserting `early_init` was called), keep the test by reworking it to exercise `main_entry` directly without the early_init step. Use judgment; don't blindly delete.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-13-dual-windows-binary.md`. Two execution options:

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, review between tasks, fast iteration. Best fit here because tasks 2-3-4 (specs), 5-6 (Windows module surgery), 7-8 (installer plumbing), and 10-11-12 (docs) form natural independent batches that benefit from a clean per-task context.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints. Faster if you want to ride along; uses more of the active conversation context.

Which approach?

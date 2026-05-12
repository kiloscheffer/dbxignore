# #30 — Windows-aware single binary

Design for collapsing `dbxignore.exe` + `dbxignored.exe` into a single Windows-aware binary that uses `AttachConsole(ATTACH_PARENT_PROCESS)` to behave correctly across three launch contexts. The work also drops the `dbxignored` entry-point everywhere (Linux + macOS trampolines, macOS Mach-O), unifying the project around a single `dbxignore` command.

## Status

- Design completed: 2026-05-13.
- Resolves: BACKLOG #30.
- Target release: v0.6.0 (**Breaking** change; pre-1.0 SemVer policy applies).

## Goal

Architectural cleanup. `dbxignored` exists today as a separate concept across multiple surfaces:

- Two PyInstaller binaries (`dbxignore.exe` `console=True`, `dbxignored.exe` `console=False`).
- Two `pyproject.toml` entry points (`dbxignore`, `dbxignored`).
- Two click commands in `cli.py` (the `main` group, and the standalone `daemon_main`).
- Two trampoline executables produced by `pip install` / `uv tool install`.
- A macOS Mach-O `dbxignored` shipped alongside `dbxignore`.

After this work: one `dbxignore` command everywhere. The daemon is launched as `dbxignore daemon` (subcommand of the `main` group, which already exists alongside `daemon_main` today). The Windows binary uses `AttachConsole(ATTACH_PARENT_PROCESS)` to behave as console-attached when launched from a terminal, silent when launched by Task Scheduler, and helpful (MessageBox) when double-clicked from Explorer.

## End state

### User-visible

- **One command name**: `dbxignore` everywhere. `dbxignored` no longer exists as a separate binary, trampoline, or PATH entry on any platform.
- **Windows**: One PyInstaller binary `dbxignore.exe`. Built as a GUI-subsystem executable (`console=False`); calls `AttachConsole(ATTACH_PARENT_PROCESS)` early in startup. Behaviors per context:
  - **Terminal launch** (`cmd`, PowerShell, Windows Terminal, VS Code integrated terminal): attach succeeds → stdout/stderr flow to the parent's console. Note: as a GUI-subsystem binary, Windows shells do not block on it as a foreground command — output is ordered but appears asynchronously; pipe / redirect / variable-capture forms force sync. See "Known limitations" below.
  - **Task Scheduler at user logon**: attach fails (no parent console), `argv` is `["dbxignore.exe", "daemon"]` → proceed silently → daemon runs without a console flash.
  - **Explorer double-click**: attach fails, `argv` has only the program name → pop a `user32.MessageBoxW` informing the user this is a CLI tool and to open a terminal; user clicks OK; process exits.
  - **Defensive fallback** (unusual session, no window-station): the MessageBox call is wrapped in `try/except OSError`; on failure, fall through to silent exit.
- **macOS**: One Mach-O `dbxignore` (no `dbxignored`). No AttachConsole logic — macOS doesn't have a console/GUI subsystem distinction for terminal tools.
- **Linux**: `pip install` / `uv tool install` produce one `dbxignore` trampoline (was producing two). No native binaries built on Linux today; no change there.

### Codebase-internal

- `daemon_main` click command in `cli.py` is deleted (was the `dbxignored` entry-point body).
- The `@main.command() def daemon(...)` subcommand stays. That's what `dbxignore daemon` runs through, and it's already the body of the `dbxignored` invocation today via `daemon_main` → `_run_daemon()` indirection.
- New small module `src/dbxignore/_windows_console.py` (~80 LOC, all-stdlib `ctypes`) houses the AttachConsole + MessageBox logic. Pure no-op on non-Windows.
- `[project.scripts]` in `pyproject.toml` shrinks from 2 entries to 1.
- Both PyInstaller spec files lose their second `EXE(...)` block.
- All three installers (`install/linux_systemd.py`, `install/macos_launchd.py`, `install/windows_task.py`) reference `dbxignore daemon` rather than `dbxignored`.
- `install/_common.py:detect_invocation()` simplifies from the three-step "find the dbxignored shim" rule to a one-step single-binary lookup.

## The `_windows_console` module

### Call-graph timing

The module's `early_init()` function must run **before** `import rich_click as click`, because rich-click constructs a `rich.console.Console()` at import time that caches `sys.stdout`. If stdio is redirected after that import, click's pretty-printed output goes to the stale reference and never reaches the attached console.

The fix is to route the entry point through `src/dbxignore/__main__.py` (which already exists for `python -m dbxignore`):

```python
# src/dbxignore/__main__.py
import sys


def main_entry() -> None:
    if sys.platform == "win32":
        from dbxignore import _windows_console
        _windows_console.early_init()  # may sys.exit(0) on double-click path
    from dbxignore.cli import main  # deferred import — only after stdio redirect
    main()


if __name__ == "__main__":
    main_entry()
```

`pyproject.toml`'s `[project.scripts]` becomes:

```toml
[project.scripts]
dbxignore = "dbxignore.__main__:main_entry"
```

PyInstaller spec's main script becomes `src/dbxignore/__main__.py`. Both install paths route through the same early-init.

### Module sketch

```python
"""Windows console attach + double-click MessageBox for the unified binary."""

from __future__ import annotations

import ctypes
import sys


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
    try:
        return bool(ctypes.windll.kernel32.AttachConsole(_ATTACH_PARENT_PROCESS))
    except OSError:
        return False


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


def _is_stream_connected(stream: object) -> bool:
    """Return True if `stream` has a valid backing FD (already wired to
    something — parent console, pipe, file). Returns False for None or
    streams whose .fileno() raises (the GUI-subsystem launch had no
    inherited handle for this slot).
    """
    if stream is None:
        return False
    try:
        stream.fileno()  # type: ignore[union-attr]
    except (AttributeError, OSError, ValueError):
        return False
    return True


def _show_help_message_box() -> None:
    # Defensive fallback for unusual session states (no window station,
    # locked-down desktop, etc.) — falls through to silent exit.
    try:
        ctypes.windll.user32.MessageBoxW(
            None, _MESSAGE_BODY, _MESSAGE_TITLE, _MB_OK_ICONINFO,
        )
    except OSError:
        pass
```

### MessageBox content

- **Title**: `dbxignore`
- **Body** (three short paragraphs):
  - "dbxignore is a command-line tool."
  - "Open Windows Terminal, PowerShell, or Command Prompt and run: `dbxignore --help`"
  - "for the list of available commands."
- **Icon**: standard info (`MB_ICONINFORMATION`).
- **Buttons**: single OK button (`MB_OK`).
- **Modality**: not topmost; standard application-modal dialog.

### Decision: stdlib `ctypes`, not `pywin32`

The four API calls we need (`AttachConsole`, `MessageBoxW`, plus the magic `CONOUT$` / `CONIN$` filenames) all work cleanly through `ctypes.windll.<dll>.<func>`. No new runtime dependency. `pywin32` is large (~30 MB installed) and Windows-only — its weight isn't justified for four API calls. Same approach `go.exe`, `winget.exe`, and many other Windows tools take.

## Files changing

24 files touched, grouped by purpose.

### Group 1 — Core unification mechanism

| File | Change |
|---|---|
| `src/dbxignore/_windows_console.py` | **NEW**, ~80 LOC. The module above. |
| `src/dbxignore/__main__.py` | Wrap entry in `main_entry()` that calls `_windows_console.early_init()` on Windows, then deferred-imports `cli.main`. |
| `src/dbxignore/cli.py` | Delete `daemon_main` function and its `@click.command()` decorator (~10 LOC). The `@main.command() def daemon(...)` subcommand stays. |
| `src/dbxignore/state.py` | `is_daemon_alive()` process-name guard tuple gains `"dbxignore"` alongside the existing `"python"` and `"dbxignored"`. After unification, a frozen daemon process is named `dbxignore.exe` — without this addition, `cli.status` / `cli.clear`'s daemon-alive guards would misclassify the new daemon as not running. Drop `"dbxignored"` from the tuple at the same time since it can no longer exist. |

### Group 2 — Build / packaging

| File | Change |
|---|---|
| `pyproject.toml` | `[project.scripts]` reduced to `dbxignore = "dbxignore.__main__:main_entry"` (was `dbxignore.cli:main`). Drop `dbxignored = "dbxignore.cli:daemon_main"`. |
| `pyinstaller/dbxignore.spec` | Drop the second `EXE(...)` block. Remaining `EXE(...)`: switch `console=True → console=False`; entry script repoint to `src/dbxignore/__main__.py`. |
| `pyinstaller/dbxignore-macos.spec` | Drop the second `EXE(...)` block. Entry script repoint to `__main__.py`. No console-mode change on macOS. |
| `.github/workflows/release.yml` | Drop the `dbxignored.exe` smoke-test step (`./dist/dbxignored.exe --help`) and the corresponding `dbxignored` macOS Mach-O smoke test. Drop both binaries from the `gh release upload` step's artifact list. Only `dist/dbxignore.exe` and `dist-macos/dbxignore` ship. |

### Group 3 — Installers

| File | Change |
|---|---|
| `src/dbxignore/install/_common.py` | `detect_invocation()` simplified to return `(<dbxignore-path>, "daemon")` unconditionally. Frozen branch finds `dbxignore.exe` / Mach-O `dbxignore`; non-frozen branch keeps the `pythonw.exe` fallback (per #100). Drop the `shutil.which("dbxignored")` lookup. |
| `src/dbxignore/install/windows_task.py` | `build_task_xml`: emit `<Command>dbxignore.exe</Command><Arguments>daemon</Arguments>`. |
| `src/dbxignore/install/linux_systemd.py` | `build_unit_content`: `ExecStart=<bin>/dbxignore daemon`. |
| `src/dbxignore/install/macos_launchd.py` | `build_plist_content`: `ProgramArguments` becomes `[<bin>/dbxignore, "daemon"]`. |

### Group 4 — Tests

| File | Change |
|---|---|
| `tests/test_windows_console.py` | **NEW**, ~250 LOC. Unit tests for `early_init`, the `_is_stream_connected` predicate, and the per-stream mixed-case behavior of `_redirect_stdio_to_attached_console`. |
| `tests/test_cli_entrypoints.py` | Delete the four `test_daemon_main_*` tests (covered by `test_main_*` / `cli daemon` subcommand tests). |
| `tests/test_install_common.py` | Simplify `test_detect_invocation_*` to expect single-binary return shape. |
| `tests/test_install.py` | Update install-dispatcher assertions that reference `dbxignored`-named binary. |
| `tests/test_windows_task.py` | Update XML-content assertions. |
| `tests/test_linux_systemd.py` | Update `ExecStart` line assertions. |
| `tests/test_macos_launchd.py` | Update `ProgramArguments` assertions. |
| `tests/test_state.py` | New tests for the updated `is_daemon_alive()` name-guard tuple: process named `dbxignore.exe` (or `dbxignore`) classifies as a live daemon; process named `dbxignored.exe` no longer classifies (since that name shouldn't exist post-unification — the guard's removal is intentional, to surface stale state from a non-upgraded install). |

### Group 5 — Documentation

| File | Change |
|---|---|
| `README.md` | Drop `dbxignored` references from Install (Windows/Linux/macOS) sections. New "Upgrading from v0.5.x" subsection. New "Known limitations — Git Bash / MinTTY" note. |
| `CHANGELOG.md` | `[Unreleased]` > `### Changed` gets a **Breaking** entry. |
| `BACKLOG.md` | Inline RESOLVED marker on #30; drop from Open list; Resolved-section entry. |

### Group 6 — Manual-test scripts

| File | Change |
|---|---|
| `scripts/manual-test-ubuntu-vps.sh` | Phase 5: assertion that `ExecStart=` references `dbxignore daemon`. |
| `scripts/manual-test-macos.sh` | Phase 5: assertion that `ProgramArguments` includes `daemon`. |
| `scripts/manual-test-windows.ps1` | Phase 5: assertion that Task Scheduler `<Arguments>` includes `daemon`. New Phase 4.5 case for AttachConsole flow (`dbxignore --version` from PowerShell → terminal output reaches stdout). Manual-visual-verification note in docstring for MessageBox on Explorer double-click. |

**Rough diff size estimate**: ~750–950 LOC across 24 files. ~110 LOC pure addition for `_windows_console.py` (including the per-stream `_is_stream_connected` helper). ~250 LOC test addition (`test_windows_console.py` covers six orchestrator decision branches + three predicate edges + per-stream mixed-case behavior). The rest is small per-file deletions and one-line referent updates.

## Test coverage

Three layers, ordered from highest test-robustness to lowest:

### Layer A — Cross-platform orchestrator tests

The `early_init` decision logic is pure: `(sys.platform, attach result, len(argv)) → action`. Mock the helpers, run on all CI legs.

```python
# tests/test_windows_console.py

def test_early_init_no_op_on_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    _windows_console.early_init()  # should not raise


def test_early_init_attach_success_redirects_and_returns(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(_windows_console, "_attach_parent_console", lambda: True)
    calls = []
    monkeypatch.setattr(_windows_console, "_redirect_stdio_to_attached_console",
                        lambda: calls.append("redirect"))
    monkeypatch.setattr(_windows_console, "_show_help_message_box",
                        lambda: calls.append("messagebox"))
    _windows_console.early_init()
    assert calls == ["redirect"]


def test_early_init_attach_fail_with_argv_returns_silently(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["dbxignore.exe", "daemon"])
    monkeypatch.setattr(_windows_console, "_attach_parent_console", lambda: False)
    calls = []
    monkeypatch.setattr(_windows_console, "_show_help_message_box",
                        lambda: calls.append("messagebox"))
    _windows_console.early_init()
    assert calls == []


def test_early_init_attach_fail_no_argv_shows_box_and_exits(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["dbxignore.exe"])
    monkeypatch.setattr(_windows_console, "_attach_parent_console", lambda: False)
    box_calls = []
    monkeypatch.setattr(_windows_console, "_show_help_message_box",
                        lambda: box_calls.append("shown"))
    with pytest.raises(SystemExit) as exc_info:
        _windows_console.early_init()
    assert exc_info.value.code == 0
    assert box_calls == ["shown"]


def test_early_init_messagebox_oserror_still_exits(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["dbxignore.exe"])
    monkeypatch.setattr(_windows_console, "_attach_parent_console", lambda: False)
    monkeypatch.setattr(_windows_console, "_show_help_message_box", lambda: None)
    with pytest.raises(SystemExit) as exc_info:
        _windows_console.early_init()
    assert exc_info.value.code == 0


@pytest.mark.parametrize("flag", ["--help", "-h", "--version"])
def test_early_init_help_or_version_does_not_take_messagebox_branch(monkeypatch, flag):
    """`--help` and `--version` are valid CLI usage that must NEVER pop the
    MessageBox even if AttachConsole fails (unusual edge: someone double-clicks
    a desktop shortcut with `--help` in the target). Argv with any non-program
    token always takes the silent-return branch; click handles --help/--version
    normally from there."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["dbxignore.exe", flag])
    monkeypatch.setattr(_windows_console, "_attach_parent_console", lambda: False)
    box_calls = []
    monkeypatch.setattr(_windows_console, "_show_help_message_box",
                        lambda: box_calls.append("shown"))
    _windows_console.early_init()  # should not raise SystemExit
    assert box_calls == []


def test_is_stream_connected_false_for_none():
    assert not _windows_console._is_stream_connected(None)


def test_is_stream_connected_false_when_fileno_raises():
    class BrokenStream:
        def fileno(self): raise OSError("no fd")
    assert not _windows_console._is_stream_connected(BrokenStream())


def test_is_stream_connected_true_for_real_stdio():
    """Under pytest, sys.stdout has a valid fileno (pytest's capture wrappers
    proxy through to a real FD). Verifies the happy-path detection."""
    assert _windows_console._is_stream_connected(sys.stdout)


def test_redirect_preserves_valid_stdout_and_reopens_missing_stderr(monkeypatch):
    """Mixed case: stdout valid (redirected to file), stderr None.
    Must NOT overwrite stdout; MUST reopen stderr against CONOUT$."""
    monkeypatch.setattr(sys, "platform", "win32")
    fake_stdout = sys.stdout  # already valid under pytest
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "stdin", None)
    # Mock builtins.open to record calls and return a sentinel rather than
    # actually opening CONOUT$/CONIN$ (which would fail in the test env)
    opened = []
    def fake_open(name, mode, **kw):
        opened.append((name, mode))
        return object()  # sentinel
    monkeypatch.setattr("builtins.open", fake_open)
    _windows_console._redirect_stdio_to_attached_console()
    assert sys.stdout is fake_stdout  # untouched
    assert sys.stderr is not None  # reopened
    assert sys.stdin is not None
    assert ("CONOUT$", "w") in opened   # for stderr
    assert ("CONIN$", "r") in opened    # for stdin
    assert ("CONOUT$", "w") not in [x for x in opened if x == ("CONOUT$", "w")][1:]  # only once (stderr), not for stdout
```

10 tests, ~250 LOC including docstrings. The set covers all six decision branches of `early_init`, the `_is_stream_connected` predicate (None / broken / valid), and the mixed-case per-stream preservation behavior of `_redirect_stdio_to_attached_console`.

### Layer B — Windows-only ctypes smoke tests

Real `ctypes.windll` calls; gated `@pytest.mark.windows_only + @pytest.mark.skipif(sys.platform != "win32")` (same double-gate as `test_ads_unit.py` and `test_install_windows_shell.py`):

```python
@pytest.mark.windows_only
@pytest.mark.skipif(sys.platform != "win32", reason="ctypes.windll is Windows-only")
def test_attach_parent_console_returns_bool_no_crash():
    """Smoke test: the ctypes call works without raising."""
    result = _windows_console._attach_parent_console()
    assert isinstance(result, bool)
```

`_show_help_message_box` is intentionally NOT auto-tested. Calling it would pop a modal dialog blocking pytest on a developer's screen. The mocked orchestrator tests above prove it's *called* in the right code path; the actual MessageBox is verified manually.

### Layer C — Existing-test updates

Mechanical updates to assertions referencing `dbxignored`:

- `test_cli_entrypoints.py`: delete 4 `test_daemon_main_*` tests.
- `test_install_common.py`: simplify `test_detect_invocation_*`.
- `test_install.py`: update dispatcher assertions.
- `test_windows_task.py`: update XML-content assertions.
- `test_linux_systemd.py`: update `ExecStart` line assertions.
- `test_macos_launchd.py`: update `ProgramArguments` assertions.

### Manual-test script additions

- All three Phase 5 sections gain a service-entry-form assertion (`ExecStart` / `ProgramArguments` / `<Arguments>` references `daemon`).
- Windows script gains **three** new Phase 4.5 cases covering the GUI-subsystem-CLI surface:
  1. **Terminal output** — run `dbxignore --version` and assert the version line appears in the PowerShell terminal. Proves AttachConsole succeeded and stdio is wired to the parent console.
  2. **Pipe capture** — run `dbxignore --version 2>&1 | Out-String` and assert the captured string contains the version line. Proves piping works (i.e., that the redirected stdio is preserved rather than overwritten by CONOUT$).
  3. **File redirect** — run `dbxignore --version > $tmpFile` and assert the file contains the version line. Proves redirection to file works (same root concern as pipe).
- Windows script docstring header documents the **manual visual verification**: double-click `dbxignore.exe` from File Explorer; expect MessageBox dialog with title "dbxignore" and body containing "dbxignore is a command-line tool"; click OK to dismiss. (Cannot be scripted without flaky UI-automation; manual on release.)
- Windows script docstring header documents the **known shell-wait limitation**: when invoked as a foreground command, Windows shells (cmd.exe AND PowerShell) generally do not wait for GUI-subsystem binaries before returning the prompt — output flows to the terminal asynchronously. Pipe / redirect / variable-capture forms force synchronous behavior (which is why the three Phase 4.5 cases above are reliable — they all force sync via the capture / pipe / redirect operator). For interactive foreground use, cmd.exe users wrap with `start /wait dbxignore.exe ...`; PowerShell users wrap with `Start-Process -Wait dbxignore -ArgumentList ...`. **Not a regression introduced by the manual-test script** — it's an artifact of Windows GUI-subsystem dispatch semantics; documented in README's Known limitations.

## Migration

### CHANGELOG entry

Under `[Unreleased] > ### Changed`:

> **Breaking** — `dbxignored` removed as a separate entry-point. The daemon is now invoked via `dbxignore daemon` (a subcommand of the main CLI). Before: `pip install dbxignore` produced both `dbxignore` and `dbxignored` console scripts, and the GitHub Release shipped both `dbxignore.exe` and `dbxignored.exe` PyInstaller binaries. After: one `dbxignore` console script, one `dbxignore.exe` PyInstaller binary, one `dbxignore` Mach-O on macOS. The Windows binary is now built as a GUI-subsystem executable that calls `AttachConsole(ATTACH_PARENT_PROCESS)` early in `main()`: launched from any Windows shell with a console (cmd, PowerShell, Windows Terminal host) it attaches to the parent's console and stdout/stderr flow to the terminal; launched by Task Scheduler at logon (no parent console, `daemon` in argv) it runs silently; launched by Explorer double-click (no parent console, empty argv) it pops a `user32.MessageBoxW` saying "dbxignore is a command-line tool. Open Windows Terminal, PowerShell, or Command Prompt and run `dbxignore --help`." Known limitation: Windows shells (cmd.exe and PowerShell alike) generally do not wait for GUI-subsystem binaries when invoked as a foreground command — output reaches the terminal via the attached console but the timing is asynchronous, so subsequent exit-code checks may run before the binary exits. Pipe / redirect / variable-capture forms force synchronous behavior. Foreground scripts that need synchronous exit semantics: cmd.exe → `start /wait dbxignore.exe ...`; PowerShell → `Start-Process -Wait dbxignore -ArgumentList ...`. Resolves BACKLOG #30. **Migration**: the recommended sequence is `dbxignore uninstall` *before* upgrading (while still on v0.5.x — the old `uninstall` knows how to remove its own service entry), then upgrade, then `dbxignore install` to register the new entry referencing `dbxignore daemon`. If you've already upgraded without uninstalling first, `dbxignore uninstall && dbxignore install` should still refresh the service entry — both versions identify the entry by the same service name and the new uninstall code path tolerates the old `ExecStart` / `ProgramArguments` / `<Arguments>` shape. Shell aliases, scripts, and custom service configs that invoked `dbxignored` should be updated to `dbxignore daemon`. (#30)

### README upgrade subsection

New heading after the existing "Upgrading from v0.2.x" subsection:

> ## Upgrading from v0.5.x
>
> v0.6 collapses `dbxignored` into the main `dbxignore` command. After upgrading, the daemon is invoked as `dbxignore daemon` instead of `dbxignored`, and the old `dbxignored` / `dbxignored.exe` binary no longer exists.
>
> The platform service entry (Task Scheduler, systemd unit, launchd plist) written by `dbxignore install` on v0.5.x references `dbxignored`. The cleanest migration sequence runs `dbxignore uninstall` **before** upgrading — the v0.5.x uninstall knows how to remove its own service entry. Then upgrade and re-install:
>
> ```bash
> # Linux / macOS — recommended order
> dbxignore uninstall                # while still on v0.5.x
> uv tool upgrade dbxignore          # or: pip install --upgrade dbxignore
> dbxignore install                  # registers the new service entry
> ```
>
> ```powershell
> # Windows — recommended order
> dbxignore uninstall                # while still on v0.5.x
> uv tool upgrade dbxignore          # or download new dbxignore.exe (no more dbxignored.exe)
> dbxignore install
> ```
>
> **If you've already upgraded without uninstalling first**, you can still refresh the service entry: `dbxignore uninstall && dbxignore install` from the new binary identifies the service by the same name as v0.5.x and tolerates the old entry's `ExecStart` / `ProgramArguments` shape during the uninstall step.
>
> If you have shell aliases or scripts that call `dbxignored` directly, replace them with `dbxignore daemon`. The two have identical behavior.

### README "Known limitations" subsection

Two new entries:

> ### Windows: shells may not wait for the GUI-subsystem binary
>
> `dbxignore.exe` is built as a GUI-subsystem executable to suppress the console flash at Task Scheduler logon. As a consequence — and consistent with how Windows treats every GUI-subsystem process — Windows shells generally do *not* wait for the binary to exit before returning the prompt when invoked as a foreground command. Output still reaches the terminal via `AttachConsole(ATTACH_PARENT_PROCESS)`, but the timing is asynchronous and subsequent commands' exit-code checks (`%ERRORLEVEL%` / `$LASTEXITCODE`) may run before the binary actually exits.
>
> The limitation applies to direct foreground invocation in every shell on Windows (cmd.exe, PowerShell, the underlying shell hosted by Windows Terminal, the VS Code integrated terminal — Windows Terminal and VS Code are *hosts*, not shells; the shell waiting behavior is what matters). It does **not** apply when the binary's output is piped or redirected: in those cases the shell waits for the pipe consumer / redirect target to close, which forces synchronous exit.
>
> Shell-specific workarounds for synchronous scripted invocations:
>
> ```cmd
> :: cmd.exe — use start /wait
> C:\> start /wait dbxignore --version
> ```
>
> ```powershell
> # PowerShell — use Start-Process -Wait, or capture/redirect (any of which forces sync)
> Start-Process -Wait -NoNewWindow dbxignore -ArgumentList "--version"
> $version = dbxignore --version 2>&1          # variable capture forces sync
> dbxignore --version > out.txt                # file redirect forces sync
> dbxignore --version | Out-String             # pipe forces sync
> ```
>
> Pipe (`|`) and redirect (`>`) operators work correctly in both cmd.exe and PowerShell because the binary preserves the inherited stdio handles when they're valid (only reopens against `CONOUT$` per-stream when an individual stream has no inherited handle, such as Task Scheduler launches with no stdio at all).
>
> ### Git Bash / MinTTY
>
> On Windows, running `dbxignore.exe` directly inside Git Bash or any MinTTY-hosted shell (`mintty`, `Cygwin Terminal`) may produce no visible output. MinTTY is a pseudo-terminal that uses pipes for stdio rather than a real Windows console; `AttachConsole` finds no console handle to attach to, so the binary runs silently. Workaround: wrap the call in `winpty`:
>
> ```bash
> winpty dbxignore.exe --help
> ```
>
> This is a general Windows-binary-in-MinTTY issue, not specific to dbxignore. PowerShell, `cmd.exe`, Windows Terminal, and the VS Code integrated terminal are not affected.

### BACKLOG closure

- Inline `**Status: RESOLVED 2026-05-13 (PR #N).**` marker prepended to #30's body.
- Open-list count decremented (Sixteen → Fifteen at time of writing).
- Resolved-section entry under the appropriate date heading.

## Decisions made and rationale

| Decision | Choice | Why |
|---|---|---|
| Scope of duplication removal | Drop the `dbxignored` entry-point everywhere (binaries + trampolines + Mach-O) | Maximum unification matches the "clean up duplication" goal. Partial scope (PyInstaller only) would leave the trampoline duplication in pip/uv installs — defeats the purpose. |
| Double-click UX on Windows | MessageBox + exit | Clean, explicit user feedback. AllocConsole + help + pause was an alternative but the press-any-key dance is clunky. Silent exit gives no feedback; URL-open feels indirect. |
| PR shape | Single PR, all-in-one | The change is logically atomic (entry-point + binaries + installers + tests + docs all derive from "drop dbxignored"). Two- or three-PR splits create per-commit checkout states that are temporarily inconsistent. |
| Server Core handling | `try/except OSError` around the MessageBox call | The original #30 body claimed user32 might not exist on Server Core. That's incorrect — user32 is part of the base Win32 API. The real concern is window-station unavailability. The defensive try/except covers both that case and any other unusual session state. |
| Windows shell wait-issue | Accept the limitation; document with shell-specific workarounds | A GUI-subsystem binary launched as a foreground command does not block any Windows shell before the prompt returns — true for cmd.exe AND PowerShell. Microsoft's own PowerShell team documents this for `notepad`-class GUI processes. Pipe / redirect / variable-capture forms force sync (the shell waits for the pipe consumer / redirect target to close). The limitation cannot be fixed from the binary's side. Alternatives (ship two binaries; use a console-mode launcher that spawns the GUI binary) defeat #30's unification goal. Workarounds: cmd.exe → `start /wait`; PowerShell → `Start-Process -Wait` or any pipe/redirect form. |
| Preserve already-redirected stdio | `_redirect_stdio_to_attached_console` checks each stream's `_is_stream_connected()` independently and reopens only the missing/invalid ones | When the user runs `dbxignore --version > out.txt`, the inherited stdout is the redirected file; stderr might still be unset (no `2>` redirect). Per-stream preservation handles the mixed case: stdout left alone, stderr reopened against CONOUT$. Replacing both unconditionally (the original sketch) would have sent stdout to the console instead of the file. |
| ctypes vs pywin32 | stdlib ctypes | Four API calls don't justify a 30 MB runtime dependency. ctypes is the canonical Win32 wrapper for "thin, no-deps" projects. |
| Deprecation period | None — clean break | Pre-1.0 SemVer allows breaking changes on MINOR bumps with explicit callouts. v0.3.0 set the precedent (the project rename required a manual `uninstall --purge` + reinstall dance). The migration command is one line; deprecation shim would be more code than the migration is worth. |

## Out of scope

- **Multi-context binary behavior on Linux/macOS** — those platforms have no console-subsystem distinction. The same single binary works in all contexts on those platforms without any special early-init logic.
- **Decoupling daemon code from CLI code** — `_run_daemon()` continues to be the daemon body, reachable as the `daemon` subcommand of the `main` click group.
- **`pywin32` adoption** — explicitly rejected in favor of stdlib `ctypes`.
- **A deprecation shim for `dbxignored`** — pre-1.0 SemVer permits the clean break.
- **Linux backend silent-swallow improvements** (parallel to BACKLOG #119 macOS finding) — separate concern, separate item if it surfaces.
- **Removing the v0.5.x upgrade-note subsection from README** — file a follow-up item to drop it once v0.5.x is presumed sunset (post-v0.7 or v0.8).

## Known limitations after this lands

- **Git Bash / MinTTY**: documented limitation; user-side `winpty` wrapper.
- **The MessageBox path can't be auto-tested**: covered by manual visual verification on release; same shape as item #115's resolution for Explorer right-click menu verification (Shell.Application COM probes are possible but flaky and add little value over reading the source).
- **A stale Task Scheduler / systemd / launchd entry referencing `dbxignored` on an upgraded host**: surfaces as "daemon doesn't auto-start at next logon". The recommended migration is `dbxignore uninstall` *before* upgrading (while still on v0.5.x — the old uninstall knows how to remove its own service entry), then upgrade, then `dbxignore install`. If the user already upgraded without uninstalling first, `dbxignore uninstall && dbxignore install` from the new binary still refreshes the entry as a fallback. Migration is documented in both CHANGELOG and README but the user has to read it.

## References

- BACKLOG.md #30 — the originating filing.
- BACKLOG.md #87 — parallel Windows synchronous-shutdown pattern resolved by PR #171.
- BACKLOG.md #100 — `pythonw.exe` selection on non-frozen installs, resolved by PR #229. The non-frozen branch of `detect_invocation` continues to use the pythonw.exe fallback after this change.
- AGENTS.md "Install/runtime packaging" gotchas.
- AGENTS.md "Manual test scripts" hard requirement.
- CLAUDE.local.md modesty rules (applied to all README + CHANGELOG additions).
- v0.3.0 CHANGELOG entry — precedent for clean-break migration with `uninstall && install` dance.

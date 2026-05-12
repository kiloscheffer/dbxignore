# Windows Explorer right-click integration — design spec

**Item:** BACKLOG #65
**Date:** 2026-05-12
**Status:** Approved (awaiting writing-plans + implementation)

## Problem

`dbxignore` is CLI-and-daemon-only on Windows. A user wanting to ignore a single folder ad-hoc has to either open a terminal and run `dbxignore ignore <path>` (PR #191, v0.5.0) or edit a `.dropboxignore` file by hand and wait for the daemon's RULES debouncer plus a reconcile. There is no Explorer-native way to mark a single folder ignored.

Windows shell extensions registered under `HKEY_CURRENT_USER\Software\Classes\…\shell\<verb>` can add custom right-click verbs that invoke a console command with `%1` substituted for the right-clicked path — no DLL, no codesigning, no UAC. Routing the actual marker write through `dbxignore.exe` rather than re-implementing the ADS write inline in the registry value gets the `\\?\` long-path correctness in `_backends/windows_ads.py` for free (item #96 caveat still applies for UNC paths, tracked separately).

The path-taking CLI verbs that this design depends on landed in PR #191 (item #93): `dbxignore.exe ignore "%1"` and `dbxignore.exe unignore --yes "%1"` are working entry points today.

## Goals

After this change:

- `dbxignore install` (default) writes two `HKCU` registry keys that surface as right-click verbs **"Ignore from Dropbox"** and **"Restore to Dropbox"** in Explorer, scoped to discovered Dropbox roots via an `AppliesTo` query filter.
- `dbxignore install --no-shell-integration` skips the registry write; daemon install proceeds as before.
- `dbxignore uninstall` removes both the daemon and the registry keys. `dbxignore uninstall --no-shell-integration` removes only the daemon.
- `dbxignore uninstall --purge` always removes both, overriding `--no-shell-integration` if also passed — `--purge` contract is "leave no dbxignore-authored artifacts on disk."
- The asymmetric `--yes` policy in the registered commands reflects the data-loss asymmetry: ignore is destructive (Dropbox deletes from cloud + every linked device), restore is recovery (Dropbox re-syncs). Ignore opens a confirmation prompt; restore is one-click.
- On Linux and macOS, `--no-shell-integration` is silently accepted as a no-op so portable scripts work unchanged.
- If `roots.discover()` returns no Dropbox roots at install time, daemon install proceeds, the shell-integration arm is skipped with a WARNING, and the user can re-run `dbxignore install` after Dropbox is set up.

## Non-goals

- **Dynamic verb visibility based on current marker state.** Showing "Ignore" only when the path is unmarked and "Restore" only when the path is marked would require a per-menu-display state probe (~50ms `is_ignored` call per menu render), which would noticeably slow Explorer. Both verbs are always shown; the CLI is idempotent.
- **Custom menu icon.** A free auto-icon via `Icon = "<dbxignore.exe>"` is plausible but a separate visual choice. V1 ships without an icon.
- **Submenu / cascade layout.** Q3 of the brainstorm settled on two top-level entries.
- **Auto-refresh of `AppliesTo` when Dropbox roots change.** Documented as "re-run `dbxignore install` after moving Dropbox." Background-refresh would require a daemon-side path-watch on Dropbox's `info.json`, out of scope.
- **Toast notifications on action result.** Both registered verbs invoke `dbxignore.exe` directly (not the daemon), so errors land on the CLI's stderr in the briefly-visible console window — not in `daemon.log`. The asymmetric `--yes` policy in the registered commands means the destructive `ignore` verb's console window stays open for the confirmation prompt (so the user sees errors). The safe `unignore --yes` verb completes faster than the window is readable, but a non-zero exit is rare and unactionable for a one-click recovery action.
- **Localization.** English-only V1.
- **Per-machine (HKLM) registration.** HKCU only — avoids UAC, matches per-user install precedent (`schtasks` runs as the current user, not SYSTEM).
- **An "Explain ignore status" diagnostic verb.** Q3 weighed it against two-verb minimal; minimal won.

## Design

### Architecture

Three layers:

1. **Platform module** — `src/dbxignore/install/windows_shell.py`. Exports `install_shell_integration(dropbox_roots: list[Path]) -> None` and `uninstall_shell_integration(*, errors: list[tuple[str, str]] | None = None) -> None`. Imports `winreg` lazily (inside the functions) so the module is safely importable on Linux and macOS — same gating pattern `_backends/windows_ads.py` uses today. Tests at `tests/test_install_windows_shell.py` are truly Windows-only via module-level double guard (see Testing). Cross-platform dispatcher behavior is tested separately in `tests/test_install.py`.

2. **Dispatcher** — two new helpers in `src/dbxignore/install/__init__.py`:
   - `install_shell_integration_if_supported(*, dropbox_roots: list[Path]) -> InstallOutcome`
   - `uninstall_shell_integration_if_supported(*, errors: list[tuple[str, str]] | None = None) -> UninstallOutcome`

   `InstallOutcome = Literal["installed", "skipped-no-roots", "skipped-bad-roots", "skipped-platform"]` — the `skipped-bad-roots` variant is the refusal case for paths containing `"` (see Refusal section below). `UninstallOutcome = Literal["uninstalled", "skipped-platform"]` (no `skipped-no-roots` or `skipped-bad-roots` analogues on uninstall — we don't gate removal on root discovery, we just delete by fixed key path).

   On `sys.platform == "win32"`, dispatch to `windows_shell.py`; otherwise log `DEBUG` ("shell-integration arm: no-op on this platform") and return the `skipped-platform` literal.

3. **CLI layer** — `src/dbxignore/cli.py` `install` and `uninstall` commands gain a `--no-shell-integration` flag and the orchestration shown in CLI integration below.

### Registry layout

Two top-level keys under `HKCU\Software\Classes\AllFilesystemObjects\shell`:

```
HKCU\Software\Classes\AllFilesystemObjects\shell\DbxignoreIgnore
    MUIVerb     = "Ignore from Dropbox"             (REG_SZ)
    AppliesTo   = "<query, see below>"              (REG_SZ)
  \command
    (default)   = "\"<dbxignore.exe>\" ignore \"%1\""           (REG_SZ)

HKCU\Software\Classes\AllFilesystemObjects\shell\DbxignoreRestore
    MUIVerb     = "Restore to Dropbox"              (REG_SZ)
    AppliesTo   = "<same query>"                    (REG_SZ)
  \command
    (default)   = "\"<dbxignore.exe>\" unignore --yes \"%1\""   (REG_SZ)
```

`AllFilesystemObjects` covers both files and directories with a single path — markers work on both, and Dropbox-ignore semantics are identical for either. The custom verb names are prefixed `Dbxignore<Verb>` so uninstall can delete exactly our keys without touching anything else under `\shell\`.

### `AppliesTo` query construction

The `AppliesTo` property is a Windows Property System query that gates whether the verb is shown for a given Explorer item. Built at install time from `roots.discover()`, with two clauses per root ORed together — root-itself (`:=` exact-equal) plus root-prefix-with-trailing-backslash (`:~<` starts-with) — to match the root and everything under it without falsely matching siblings (e.g. `Dropbox-other`):

```
System.ItemPathDisplay:="C:\Users\kilo\Dropbox" OR
System.ItemPathDisplay:~<"C:\Users\kilo\Dropbox\" OR
System.ItemPathDisplay:="D:\Dropbox (Personal)" OR
System.ItemPathDisplay:~<"D:\Dropbox (Personal)\"
```

AQS performs no escape interpretation inside quoted string literals — backslashes are stored and matched literally. A single trailing `\` immediately before the closing `"` is parsed as a literal trailing backslash without escaping the quote. So embed the discovered root path with its natural single backslashes; the prefix clause appends one trailing `\` to disambiguate `C:\Dropbox` from sibling folders like `C:\Dropbox-other`. For drive-root mounts like `D:\`, `str(Path)` already ends in `\`, so `_format_applies_to_query` normalizes via `.rstrip("\\") + "\\"` before embedding — without that, the prefix clause would have a double trailing backslash (matching nothing).

Helper `_format_applies_to_query(roots: list[Path]) -> str` handles the construction.

### Refusal: paths containing `"`

If any discovered root contains a literal `"` (Windows allows this in some niche filesystems but Explorer rejects it for most operations), `_format_applies_to_query` raises `RuntimeError("Dropbox root path %s contains a quote character; cannot generate AppliesTo query")`. The shell-integration arm is then skipped with a WARNING. Daemon install still proceeds.

### CLI executable discovery

New helper `src/dbxignore/install/_common.detect_cli_invocation() -> str` returns the fully-quoted command-line prefix for the dbxignore CLI, formatted for embedding in a registry command-string. Three cases mirror the existing `detect_invocation()`:

1. **Frozen (PyInstaller):** `Path(sys.executable).parent / "dbxignore.exe"` exists → return `"\"<that path>\""`.
2. **`shutil.which("dbxignore")` resolves:** return `"\"<that path>\""`.
3. **Fallback:** `"\"<sys.executable>\" -m dbxignore"`.

Validate that case (1) or (2) produces a path that `.exists()`; if not, fall through to (3). If even (3) is unviable (`sys.executable` empty — embedded interpreter edge case from item #26's docstring), raise `RuntimeError` and let the caller surface the WARNING + skip.

The full registered command then becomes `<detect_cli_invocation()> ignore "%1"` and `<detect_cli_invocation()> unignore --yes "%1"`.

### CLI integration

**`install` flow:**

```python
@main.command()
@click.option("--no-shell-integration", is_flag=True,
              help="Skip Explorer right-click integration (Windows only). "
                   "No effect on Linux or macOS.")
def install(no_shell_integration: bool) -> None:
    from dbxignore.install import install_service, install_shell_integration_if_supported

    try:
        install_service()
    except RuntimeError as exc:
        click.echo(f"Failed to install daemon service: {exc}", err=True)
        sys.exit(2)
    click.echo("Installed dbxignore daemon service.")

    if not no_shell_integration:
        try:
            outcome = install_shell_integration_if_supported(
                dropbox_roots=_discover_roots(),
            )
        except OSError as exc:
            logger.warning("shell-integration install failed: %s", exc)
            outcome = None
        if outcome == "installed":
            click.echo("Installed Explorer right-click integration.")
```

The `OSError` arm catches partial-write failures (a `winreg.SetValueEx` raising mid-install) and WARN-and-continues — the daemon is already up; shell integration is recoverable by re-running `install`. The `windows_shell.py` module is responsible for best-effort cleanup of any partially-written keys on its own raised exception (see Error handling below).

**`uninstall` flow:**

```python
@main.command()
@click.option("--purge", is_flag=True, help=...)
@click.option("--no-shell-integration", is_flag=True,
              help="Preserve Explorer right-click integration. "
                   "Ignored under --purge.")
def uninstall(purge: bool, no_shell_integration: bool) -> None:
    from dbxignore.install import uninstall_service, uninstall_shell_integration_if_supported

    try:
        uninstall_service()
    except RuntimeError as exc:
        click.echo(f"Failed to uninstall daemon service: {exc}", err=True)
        sys.exit(2)
    click.echo("Uninstalled dbxignore daemon service.")

    shell_errors: list[tuple[str, str]] = []
    if purge:
        # Purge mode: --no-shell-integration is overridden; errors escalate
        # to exit 2 alongside the existing marker-clear errors arm.
        uninstall_shell_integration_if_supported(errors=shell_errors)
    elif not no_shell_integration:
        # Default mode: WARN-and-continue on registry failures.
        uninstall_shell_integration_if_supported()

    if purge:
        # ... existing marker-clear arm builds its own `errors` list ...
        # ... existing _purge_local_state() arm ...

        for key, msg in shell_errors[:_MAX_REPORTED_ERRORS]:
            click.echo(f"  error: {key} - {msg}", err=True)
        if errors or shell_errors:  # `errors` is the existing marker-clear list
            sys.exit(2)
```

The mutually exclusive `if purge / elif not no_shell_integration` arms mean the shell-integration dispatcher runs exactly once per `dbxignore uninstall` invocation, with the call site determining error-escalation behavior. Idempotent calls within the dispatcher (via `winreg.DeleteKey`'s `FileNotFoundError` arm) are still relied on for re-running `uninstall` after a prior failure.

### Error handling

**Install — partial-write recovery.** `install_shell_integration()` writes keys in a fixed order: parent key first, then `command` subkey, then values. If any `SetValueEx` raises mid-install, the function's own `try/except OSError` arm calls `uninstall_shell_integration()` to remove whatever was partially written, then re-raises. The result is "nothing or everything" — the user never sees a half-installed state where one verb is registered and the other isn't.

**Uninstall — idempotence and `--purge` error escalation.** `uninstall_shell_integration()` walks both verb keys + their `command` subkeys + their values in reverse order. Each `DeleteKey` / `DeleteValue` call is wrapped in `try/except FileNotFoundError: pass` (already gone — fine). Other `OSError` instances are accumulated into a caller-supplied list (or, if omitted, just logged as WARNING). Function signature:

```python
def uninstall_shell_integration(*, errors: list[tuple[str, str]] | None = None) -> None:
    """Remove the two HKCU verb keys. Idempotent.

    On non-FileNotFoundError OSError: if `errors` is provided, append
    (registry_key_path, message); otherwise log WARNING. Loop always
    continues to the next key — never aborts partway.
    """
```

Dispatcher `uninstall_shell_integration_if_supported(*, errors=...)` plumbs the parameter through. CLI semantics differ by code path:

- **Plain `uninstall` (no `--purge`)**: dispatcher called with `errors=None`. Failures WARN-and-continue; no impact on the exit code (consistent with the existing schtasks `/Run` partial-success pattern).
- **`uninstall --purge`**: dispatcher called with a `shell_errors: list[tuple[str, str]] = []` accumulator. After the call, if `shell_errors` is non-empty, surface each entry on stderr (`error: <key> - <message>`, capped at `_MAX_REPORTED_ERRORS` to match the marker-clear arm) and set a non-zero-exit flag. The existing marker-clear arm already builds its own `errors` list and exits 2 on non-empty; we add the shell-integration list to the same exit check. Net effect: `--purge` exits 2 if either marker cleanup or shell-integration cleanup leaves work undone — matches the documented "leave no dbxignore-authored artifacts" contract from `cli.uninstall`'s docstring.

**No-roots install.** `install_shell_integration(dropbox_roots=[])` raises `ValueError` ("no Dropbox roots; cannot scope AppliesTo"); the dispatcher catches that, logs the documented WARNING, and returns `"skipped-no-roots"`. The CLI layer sees the outcome string and emits no install echo (the WARNING already surfaced).

**Refusal — `"` in root path.** `_format_applies_to_query` raises `RuntimeError`; dispatcher logs WARNING + returns a sentinel outcome. We treat this distinctly from no-roots because it's user-actionable (rename the directory) vs. environmental (Dropbox not installed). Outcome name: `"skipped-bad-roots"` — `InstallOutcome` becomes `Literal["installed", "skipped-no-roots", "skipped-bad-roots", "skipped-platform"]`.

### Idempotence

Re-running `dbxignore install`:
- Daemon install: idempotent today (schtasks `/Create /F` overwrites).
- Shell integration: `winreg.CreateKey` is idempotent (returns the existing key); `SetValueEx` overwrites. Re-running picks up new Dropbox roots in the refreshed `AppliesTo`.

Re-running `dbxignore uninstall`:
- Daemon: existing behavior unchanged.
- Shell integration: idempotent via the `FileNotFoundError` arm.

## Testing

Tests split across three files to match the project's platform-test conventions:

#### `tests/test_install_windows_shell.py` — Windows-only registry mechanics

Module-level double guard (matches `tests/test_windows_ads_integration.py:1-9`):

```python
import sys
import pytest

pytestmark = pytest.mark.windows_only
if sys.platform != "win32":
    pytest.skip("HKCU registry mechanics are Windows-only", allow_module_level=True)
```

Ten tests against the platform module's `install_shell_integration` / `uninstall_shell_integration` functions directly (not the dispatcher):

*Install*
1. `test_install_writes_both_verb_keys` — both `DbxignoreIgnore` and `DbxignoreRestore` keys present after `install_shell_integration()`.
2. `test_install_sets_command_strings` — `\command` default values match `"<exe>" ignore "%1"` and `"<exe>" unignore --yes "%1"` (asymmetric `--yes`).
3. `test_install_applies_to_query_includes_each_root` — `AppliesTo` value contains one `:=` + one `:~<` clause per supplied root, OR-joined.
4. `test_install_applies_to_escapes_trailing_backslash` — root `D:\` produces `"D:\\\\"` (which is `"D:\\"` rendered with Python literal-escaping).
5. `test_install_refuses_root_with_embedded_double_quote` — root containing `"` raises `RuntimeError`; no keys written.
6. `test_install_overwrites_existing_keys` — re-running over an already-installed state cleanly overwrites; no stale values from the previous install (e.g. an obsolete root's clause must not survive).
7. `test_install_partial_write_failure_warns_and_attempts_cleanup` — monkeypatch `winreg.SetValueEx` to raise after the first call; assert no `DbxignoreIgnore`/`DbxignoreRestore` keys remain; assert WARNING logged.

*Uninstall*
8. `test_uninstall_removes_both_verb_keys` — clean removal after a clean install.
9. `test_uninstall_idempotent_when_keys_missing` — no error when keys were never written.
10. `test_uninstall_other_oserror_routes_to_errors_or_warning` — parametrized over `errors=[]` and `errors=None`. Non-`FileNotFoundError` `OSError` on one `DeleteKey` call doesn't abort the loop and the second key is still removed in both cases. With `errors=[]`: entry appended as `(registry_key_path, message)`; no WARNING logged (caller chose to accumulate). With `errors=None`: WARNING logged via `caplog`; list-append branch is unreachable.

#### `tests/test_install.py` — cross-platform dispatcher + CLI plumbing

Extend the existing cross-platform install-test file. Tests use mocks (no real registry writes, no real subprocess calls), so they run on Linux/macOS CI legs uniformly.

*Dispatcher contract (runs everywhere — verifies cross-platform branch logic):*

The dispatcher branches on `sys.platform == "win32"` *first*, returning `skipped-platform` for non-Windows before any roots check. To exercise the `skipped-no-roots` and `skipped-bad-roots` branches on non-Windows CI legs, tests #12, #13, and #15 monkeypatch `sys.platform = "win32"` AND inject a fake `windows_shell` module so the dispatcher's Windows arm executes against a stub instead of calling real `winreg`.

The fake-injection must match the dispatcher's actual import shape. The dispatcher in `install/__init__.py` uses a lazy `from dbxignore.install.windows_shell import install_shell_integration` (inside the `if sys.platform == "win32":` arm), not a top-level `from . import windows_shell`. The reliable test-time injection is therefore via `sys.modules`, not `monkeypatch.setattr` on a package attribute:

```python
fake = types.ModuleType("dbxignore.install.windows_shell")
fake.install_shell_integration = MagicMock(side_effect=RuntimeError(...))
fake.uninstall_shell_integration = MagicMock()
monkeypatch.setitem(sys.modules, "dbxignore.install.windows_shell", fake)
monkeypatch.setattr(sys, "platform", "win32")
```

The `sys.modules` injection guarantees the lazy `from … import …` resolves to the fake regardless of whether `install/__init__.py` ever bound a package-level attribute. (Patching `install_pkg.windows_shell` with `monkeypatch.setattr` would silently miss if the attribute isn't there yet — which it won't be on a fresh test process where the lazy import hasn't fired.)

11. `test_dispatcher_install_skipped_platform_on_non_windows` — under the real `sys.platform` (or explicitly monkeypatched to `"linux"` on Windows hosts), `install_shell_integration_if_supported(dropbox_roots=[...])` returns `"skipped-platform"` without referencing `windows_shell` at all (assert via `mocker.patch` on the import).
12. `test_dispatcher_install_skipped_no_roots` — with `sys.platform` monkeypatched to `"win32"` and `windows_shell` replaced by a fake whose `install_shell_integration` would raise if called, `install_shell_integration_if_supported(dropbox_roots=[])` returns `"skipped-no-roots"` and emits the documented WARNING. The fake's `install_shell_integration` is asserted not-called.
13. `test_dispatcher_install_skipped_bad_roots` — with `sys.platform = "win32"` and `windows_shell` faked so `install_shell_integration` raises `RuntimeError("...contains a quote character...")`, the dispatcher catches and returns `"skipped-bad-roots"` with a WARNING.
14. `test_dispatcher_uninstall_skipped_platform_on_non_windows` — under non-Windows `sys.platform`, the uninstall dispatcher returns `"skipped-platform"` without referencing `windows_shell`.
15. `test_dispatcher_uninstall_threads_errors_list` — with `sys.platform = "win32"` and `windows_shell` faked, calling the dispatcher with `errors=[]` causes the fake's `uninstall_shell_integration` to receive that exact list object (assert via identity check). Verifies the keyword-argument plumbing.

*CLI plumbing (uses click's `CliRunner` against `main`; mocks `install_service` and the shell-integration dispatcher):*
16. `test_install_calls_shell_helper_by_default` — `dbxignore install` invokes both `install_service()` and `install_shell_integration_if_supported(...)` with `dropbox_roots=<discovered>`.
17. `test_install_no_shell_integration_skips_helper` — `dbxignore install --no-shell-integration` invokes `install_service()` only; shell-integration dispatcher not called.
18. `test_uninstall_calls_shell_helper_by_default` — `dbxignore uninstall` invokes `uninstall_service()` and the shell-integration dispatcher exactly once each.
19. `test_uninstall_no_shell_integration_skips_helper` — `dbxignore uninstall --no-shell-integration` (no `--purge`) skips the shell-integration dispatcher entirely.
20. `test_uninstall_purge_overrides_no_shell_integration` — `dbxignore uninstall --purge --no-shell-integration` invokes the shell-integration dispatcher exactly once (the `--purge` branch), with `errors=[]` supplied. Asserts no double-invocation when both flags are present.
21. `test_uninstall_purge_exits_2_on_shell_errors` — when the shell-integration dispatcher populates the supplied `errors` list, `--purge` echoes each entry to stderr and `sys.exit(2)` fires (matching the existing marker-clear-errors arm).

### Test isolation (Windows-only file)

Tests in `test_install_windows_shell.py` monkeypatch the registry root from `HKCU\Software\Classes` to a throwaway subkey `HKCU\Software\Classes\DbxignoreTest\<test-uuid>\…` so a test never collides with a real shell-integration install on the developer's machine. Cleanup fixture (autouse, function-scoped) deletes the throwaway subtree on teardown. Cross-platform tests in `test_install.py` use mocks for the platform module, so no real registry access is needed there.

### Manual-test extensions

Per the `CLAUDE.md` "manual-test scripts are kept current" requirement.

**Phase placement.** Shell-integration assertions belong in Phase 5 and Phase 6 of `manual-test-windows.ps1`, not Phase 4.5:

- Phase 4.5 (`_phase_extended_cli.sh` + `Test-ExtendedCli`) operates against a daemon-not-running fixture; `dbxignore install` (default) starts the daemon and would break Phase 4.5's existing `clear`/daemon-alive-guard assumptions.
- Phase 5 already owns `dbxignore install` invocation and Task Scheduler/watchdog assertions.
- Phase 6 already owns `dbxignore uninstall` and post-cleanup assertions.

The existing `4o-4t` labels in `_phase_extended_cli.sh` (PRs #191/#195/#203/#205) are also already taken — moving to Phase 5/6 sidesteps the collision entirely.

**Non-Windows `--no-shell-integration` flag acceptance** is covered via the cross-platform CLI plumbing tests (`test_install_no_shell_integration_skips_helper` runs on Linux/macOS CI legs). No bash manual-test arm is needed — `_phase_extended_cli.sh` stays unchanged.

**`scripts/manual-test-windows.ps1` — Phase 5 addition** (after the existing `5b-5f` watchdog/sweep cases):
- `5g` — Read both `HKCU:\Software\Classes\AllFilesystemObjects\shell\DbxignoreIgnore` and `…\DbxignoreRestore` keys after `dbxignore install` ran (already done in Phase 5's install step). Assert `MUIVerb` matches "Ignore from Dropbox" / "Restore to Dropbox" and `AppliesTo` contains the live Dropbox root path. Read the `\command` default values and assert the format `"<exe>" ignore "%1"` (no `--yes`) and `"<exe>" unignore --yes "%1"` (with `--yes`) — guards against regression where the asymmetric `--yes` flips direction.

Phase 5 ends with a fully-installed daemon, exactly as today — `5g` only reads registry state, doesn't mutate. Phase 6's existing precondition (installed daemon ready for `dbxignore uninstall`) is preserved.

**`scripts/manual-test-windows.ps1` — Phase 6 additions.** Phase 6's existing flow is:

1. Plain `dbxignore uninstall`
2. Existing `6a` — `--summary` returns `state=not_running` post-uninstall
3. Re-install for the purge test (PR #87 race-protection dance, including the daemon_pid-advance poll)
4. `dbxignore uninstall --purge`
5. Existing purge happy-path assertions (markers cleared, state.json + daemon.log removed)
6. Existing `6b` — `--summary` returns `state=no_state` post-purge

New cases slot in without disturbing this:

- `6c` — Insert after `6a` (between step 2 and step 3). Assert both `DbxignoreIgnore` and `DbxignoreRestore` keys are gone (default plain `uninstall` removed them in step 1). Read-only — no state change.
- `6d` — Insert after `6b` (after step 6, when daemon + state + registry are all gone from the existing `--purge` flow). Run `dbxignore install` to recreate everything, then `dbxignore uninstall --no-shell-integration`. Assert: scheduled task gone, state.json may or may not be present (plain uninstall retains it; not our concern), BUT both verb keys are still present in `HKCU` (the flag preserved them).
- `6e` — Immediately after `6d`. Run `dbxignore install` again (recreates daemon + state + keys), then `dbxignore uninstall --purge --no-shell-integration`. Assert: scheduled task gone, state.json + daemon.log gone (purge's normal contract), AND both verb keys gone (purge overrode `--no-shell-integration`).

Each case carries an inline `# 5g — registry keys after default install (PR #NNN)` provenance comment.

The `6d`/`6e` append at the END of Phase 6 means: (a) the existing PR #87 daemon_pid-advance race-protection logic is untouched, (b) Phase 7's cleanup starts from "daemon gone, state gone, registry gone" — same end-state Phase 6 produces today, (c) the contrast cases don't depend on the unrelated `6a`/`6b` `--summary` shape, so a future change to either is unlikely to ripple. Trade-off accepted: one extra install + uninstall cycle in Phase 6 (about ~15-30 s on a typical run; Phase 6 today already does two install/uninstall cycles).

**Linux + macOS scripts** — no changes. The `--no-shell-integration` flag's silent no-op on those platforms is covered by the CLI plumbing tests above; running it from a bash manual-test arm would just exercise the same no-op the unit test covers.

## Documentation

- `README.md` — new section "Windows Explorer integration" with two paragraphs: the menu items, the asymmetric `--yes` policy rationale ("Ignore opens a confirmation window because Dropbox-cloud deletion is the destructive direction; Restore is one-click"), and the `--no-shell-integration` opt-out.
- `CHANGELOG.md` `[Unreleased]` → `Added`: "Windows Explorer right-click integration: `Ignore from Dropbox` and `Restore to Dropbox` verbs are registered by `dbxignore install` on Windows. Use `dbxignore install --no-shell-integration` to opt out; `dbxignore uninstall --no-shell-integration` to preserve the verbs across a daemon reinstall."
- `CLAUDE.md` — short bullet under the `install/` package paragraph describing `windows_shell.py`'s role, the asymmetric `--yes` policy, and the `AppliesTo` query shape. The "why" (data-loss asymmetry; `:=` + `:~<` to avoid sibling-Dropbox-folder false matches) is the load-bearing detail.

## Rollout

No migration required. New install/uninstall on Windows picks up the shell-integration arm automatically. Existing installs continue to function — re-running `dbxignore install` after upgrading is the documented refresh.

## Files touched

- `src/dbxignore/install/windows_shell.py` (new module, ~100 LOC)
- `src/dbxignore/install/__init__.py` (new dispatcher helpers, ~40 LOC)
- `src/dbxignore/install/_common.py` (new `detect_cli_invocation()` helper, ~20 LOC)
- `src/dbxignore/cli.py` (`install` + `uninstall` flag plumbing + `--purge` shell-error escalation, ~40 LOC)
- `tests/test_install_windows_shell.py` (new — 10 Windows-only registry-mechanics tests, ~220 LOC)
- `tests/test_install.py` (extend — 5 dispatcher tests + 6 CLI-plumbing tests, ~180 LOC)
- `tests/test_install_common.py` (extend with `detect_cli_invocation` cases, ~30 LOC)
- `scripts/manual-test-windows.ps1` (Phase 5 case `5g` + Phase 6 cases `6c`/`6d`/`6e`, ~120 LOC — three Phase-6 cases include two extra install/uninstall cycles for the contrast tests)
- `README.md` (~25 LOC)
- `CHANGELOG.md` (~3 LOC)
- `CLAUDE.md` (~6 LOC)
- `BACKLOG.md` (close #65 with PR reference; add inline provenance)

Estimated total: ~770 LOC across implementation + tests + docs. (Bash manual-test scripts unchanged — non-Windows flag-accept covered by cross-platform CLI plumbing tests.)

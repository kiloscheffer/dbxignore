# #65 Windows Explorer shell integration — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `dbxignore install` (the default arm) register two Windows Explorer right-click verbs — "Ignore from Dropbox" and "Restore to Dropbox" — scoped to discovered Dropbox roots via an `AppliesTo` query filter. Make `dbxignore uninstall` remove them. Surface a `--no-shell-integration` flag on both verbs and have `--purge` override it.

**Architecture:** Three layers mirroring the existing `install/` package:
1. `install/_common.py` gains a pure `detect_cli_invocation()` helper that produces a quoted command-line prefix for `dbxignore.exe` (frozen → shutil.which → `<python> -m dbxignore` fallback).
2. `install/windows_shell.py` is a new platform module exporting `install_shell_integration(dropbox_roots)` and `uninstall_shell_integration(*, errors=None)`. Imports `winreg` lazily inside its functions so the module loads cleanly on Linux/macOS.
3. `install/__init__.py` gains `install_shell_integration_if_supported` and `uninstall_shell_integration_if_supported` dispatchers that branch on `sys.platform` first, then delegate.
4. `cli.py`'s `install` and `uninstall` commands gain a `--no-shell-integration` flag, wire the dispatchers in, and (for `--purge`) escalate registry-cleanup errors into the existing exit-2 path.

**Spec:** `docs/superpowers/specs/2026-05-12-65-windows-shell-integration-design.md` — read it before starting; this plan operationalizes that design.

**Tech Stack:** Python 3.11+, `winreg` (stdlib, Windows-only — lazy import), `click` / `rich_click`, `pytest` with `CliRunner` + `pytest.MonkeyPatch`, `pathspec` (unrelated, unaffected). Existing reference: `install/windows_task.py` (same dispatch shape, subprocess-based instead of winreg-based) and `_backends/windows_ads.py` (same lazy-Windows-only import gate).

---

## File structure

**Create:**
- `src/dbxignore/install/windows_shell.py` — platform module exporting `install_shell_integration`, `uninstall_shell_integration`, and the pure `_format_applies_to_query` helper.
- `tests/test_install_windows_shell.py` — Windows-only registry mechanics tests (10 tests, module-level double guard).

**Modify:**
- `src/dbxignore/install/_common.py` — add `detect_cli_invocation()` helper alongside the existing `detect_invocation()`.
- `src/dbxignore/install/__init__.py` — add two new dispatcher helpers and their `Literal` outcome types.
- `src/dbxignore/cli.py` — `install` and `uninstall` commands gain `--no-shell-integration`; `uninstall` wires `--purge` shell-error escalation.
- `tests/test_install_common.py` — three new tests for `detect_cli_invocation()`.
- `tests/test_install.py` — five dispatcher tests + six CLI plumbing tests (11 new tests).
- `scripts/manual-test-windows.ps1` — Phase 5 case `5g` (read-only registry assertions); Phase 6 cases `6c`/`6d`/`6e` (registry-gone after uninstall, preserve-on-flag, purge-overrides-flag).
- `README.md` — new "Windows Explorer integration" subsection.
- `CHANGELOG.md` — `[Unreleased]` `Added` entry.
- `AGENTS.md` — short bullet describing `windows_shell.py`'s role and the asymmetric `--yes` policy.
- `BACKLOG.md` — close #65 with PR reference; update Open list count; add inline `Status: RESOLVED` line.

**Total estimated change:** ~160 LOC of code in `_common.py` + `windows_shell.py` + `install/__init__.py` + `cli.py`; ~420 LOC of tests; ~140 LOC of manual-test script extensions + docs.

---

## Task 1: `detect_cli_invocation()` helper

Pure function returning a quoted command-line prefix string for the dbxignore CLI binary (or the `python -m dbxignore` fallback). Output is the prefix to be embedded in a registry command-string — callers append the subcommand + `"%1"` placeholder.

Three branches mirror `detect_invocation()`:
1. **Frozen (PyInstaller):** prefer `Path(sys.executable).parent / "dbxignore.exe"` if it exists.
2. **`shutil.which("dbxignore"):**` PATH-shim from pip/uv install.
3. **Fallback:** `<sys.executable> -m dbxignore`.

If even the fallback is unviable (empty `sys.executable`), raise `RuntimeError`.

**Files:**
- Modify: `src/dbxignore/install/_common.py` (add at end of file, after `detect_invocation`)
- Test: `tests/test_install_common.py` (add four new tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_install_common.py`:

```python
def test_detect_cli_invocation_frozen_uses_sibling_exe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Frozen PyInstaller install: dbxignore.exe sibling is the registered target."""
    daemon_exe = tmp_path / _daemon_name()
    daemon_exe.write_text("")
    cli_name = "dbxignore.exe" if sys.platform == "win32" else "dbxignore"
    cli_exe = tmp_path / cli_name
    cli_exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(daemon_exe))
    from dbxignore.install import _common

    result = _common.detect_cli_invocation()
    assert result == f'"{cli_exe}"'


def test_detect_cli_invocation_uses_shutil_which_shim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-frozen install: `dbxignore` PATH shim is the registered target."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    shim_path = "C:\\Users\\u\\.local\\bin\\dbxignore.exe"

    def fake_which(name: str) -> str | None:
        if name == "dbxignore":
            return shim_path
        return None

    monkeypatch.setattr("shutil.which", fake_which)
    from dbxignore.install import _common

    result = _common.detect_cli_invocation()
    assert result == f'"{shim_path}"'


def test_detect_cli_invocation_falls_back_to_python_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No frozen install, no `dbxignore` on PATH: use `<sys.executable> -m dbxignore`."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    python_exe = tmp_path / "Scripts" / "python.exe"
    python_exe.parent.mkdir()
    python_exe.write_text("")
    monkeypatch.setattr(sys, "executable", str(python_exe))
    monkeypatch.setattr("shutil.which", lambda _name: None)
    from dbxignore.install import _common

    result = _common.detect_cli_invocation()
    assert result == f'"{python_exe}" -m dbxignore'


def test_detect_cli_invocation_raises_when_no_python_and_no_sys_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive guard: empty sys.executable + no shim must raise, not return Path('.')."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", "")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    from dbxignore.install import _common

    with pytest.raises(RuntimeError, match="dbxignore not on PATH"):
        _common.detect_cli_invocation()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_install_common.py -v -k detect_cli_invocation`
Expected: 4 FAIL with `AttributeError: module 'dbxignore.install._common' has no attribute 'detect_cli_invocation'`

- [ ] **Step 3: Implement `detect_cli_invocation()`**

Append to `src/dbxignore/install/_common.py` (after `detect_invocation`):

```python
def detect_cli_invocation() -> str:
    """Return a quoted command-line prefix for the dbxignore CLI.

    Output is a registry-ready string: the executable plus any leading
    arguments needed before a subcommand (e.g. `"<python>" -m dbxignore`).
    Callers concatenate the subcommand + `"%1"` placeholder when building
    the full ``HKCU\\…\\shell\\<verb>\\command`` default value.

    Three branches mirror ``detect_invocation()``:

    1. **Frozen (PyInstaller).** Prefer the ``dbxignore.exe`` sibling next
       to ``sys.executable``. Both binaries ship from the same PyInstaller
       Analysis; if the user invoked ``dbxignore.exe install`` the sibling
       check returns ``sys.executable`` unchanged.
    2. **`shutil.which("dbxignore")`** — the pip/uv-install PATH shim.
    3. **Fallback** — ``"<sys.executable>" -m dbxignore``. Used when no
       shim is on PATH (typical for an editable ``uv pip install -e .``
       working directory that hasn't been exposed via ``uv tool install``).

    Raises ``RuntimeError`` if all three branches are unviable — same
    defensive guard as ``detect_invocation`` (empty ``sys.executable``
    on embedded interpreters / misconfigured frozen deployments).
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        cli_name = "dbxignore.exe" if sys.platform == "win32" else "dbxignore"
        if exe.name == cli_name:
            return f'"{exe}"'
        sibling = exe.parent / cli_name
        if sibling.exists():
            return f'"{sibling}"'
        # Fall through — shipped frozen installs always have the sibling,
        # but defend against truncated bundles by falling through.
    shim = shutil.which("dbxignore")
    if shim:
        return f'"{shim}"'
    python = sys.executable
    if not python:
        raise RuntimeError(
            "dbxignore not on PATH and sys.executable is empty; "
            "run `uv tool install .` from the dbxignore checkout first"
        )
    return f'"{python}" -m dbxignore'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_install_common.py -v -k detect_cli_invocation`
Expected: 4 PASS.

- [ ] **Step 5: Run the full `test_install_common.py` to ensure no regression**

Run: `uv run python -m pytest tests/test_install_common.py -v`
Expected: all tests pass (existing `detect_invocation` tests + new `detect_cli_invocation` tests).

- [ ] **Step 6: Commit**

```bash
git add src/dbxignore/install/_common.py tests/test_install_common.py
git commit -m "feat(install): add detect_cli_invocation helper for shell-integration registry values (#65)"
```

---

## Task 2: `_format_applies_to_query()` helper

Pure function building the `AppliesTo` query string from a list of Dropbox-root paths. Each root produces two clauses: an exact-equal (`:=`) for the root itself, and a starts-with (`:~<`) for descendants. Raises `RuntimeError` if any root contains a literal `"` (paths Explorer can't sanely match anyway).

This is the only non-trivial function in `windows_shell.py` that can be tested cross-platform (it operates on string paths, not winreg). The module's full layout is also established in this task.

**Files:**
- Create: `src/dbxignore/install/windows_shell.py` (skeleton + helper)
- Test: tests for `_format_applies_to_query` live in `tests/test_install_windows_shell.py`, which is Windows-only — but the pure helper deserves a portable test too. Add it to `tests/test_install.py` so all platforms exercise the string-shaping logic.

- [ ] **Step 1: Create the module skeleton**

Create `src/dbxignore/install/windows_shell.py`:

```python
"""Windows Explorer right-click verb registration (backlog #65).

Writes two HKCU registry keys that surface "Ignore from Dropbox" and
"Restore to Dropbox" verbs on every file and directory inside discovered
Dropbox roots. The actual marker write is routed through `dbxignore.exe`
rather than re-implementing the ADS write inline, so the `\\\\?\\` long-path
correctness in `_backends/windows_ads.py` is reused.

The module loads on every platform but its public functions only do work
on Windows — `winreg` is imported lazily inside `install_shell_integration`
and `uninstall_shell_integration` so non-Windows imports succeed cleanly.
"""

from __future__ import annotations

import logging
from pathlib import Path

from dbxignore.install._common import detect_cli_invocation

logger = logging.getLogger(__name__)

# Registry layout — fixed paths so uninstall can target exactly our keys
# without touching other shell extensions.
_REG_BASE = r"Software\Classes\AllFilesystemObjects\shell"
_IGNORE_VERB = "DbxignoreIgnore"
_RESTORE_VERB = "DbxignoreRestore"


def _format_applies_to_query(roots: list[Path]) -> str:
    """Build the AppliesTo query string for the given Dropbox roots.

    Each root produces two clauses ORed together:
    - ``System.ItemPathDisplay:="<root>"`` — exact match the root itself
    - ``System.ItemPathDisplay:~<"<root>\\\\"`` — prefix match for descendants

    The trailing-backslash variant prevents false matches on sibling
    folders (e.g. matching ``C:\\Dropbox-other`` when scoped to ``C:\\Dropbox``).

    Inside the AppliesTo quoted string literals, ``\\\\`` is the escape for a
    single backslash. So every backslash in the input path is doubled
    before being embedded in the query.

    Raises ``RuntimeError`` if any root contains a literal ``"`` — escaping
    quotes inside AppliesTo's grammar is unsound for typical filesystem
    paths and Explorer rejects them for most operations anyway.
    """
    clauses: list[str] = []
    for root in roots:
        root_str = str(root)
        if '"' in root_str:
            raise RuntimeError(
                f"Dropbox root path {root_str!r} contains a quote character; "
                "cannot generate AppliesTo query"
            )
        escaped_root = root_str.replace("\\", "\\\\")
        clauses.append(f'System.ItemPathDisplay:="{escaped_root}"')
        prefix = root_str + "\\"
        escaped_prefix = prefix.replace("\\", "\\\\")
        clauses.append(f'System.ItemPathDisplay:~<"{escaped_prefix}"')
    return " OR ".join(clauses)
```

- [ ] **Step 2: Write the failing tests in `tests/test_install.py`**

Append to `tests/test_install.py` (creating the file if it doesn't have the imports already; check first with `head -30 tests/test_install.py`):

```python
from pathlib import Path

import pytest

from dbxignore.install.windows_shell import _format_applies_to_query


def test_format_applies_to_query_single_root() -> None:
    roots = [Path(r"C:\Users\kilo\Dropbox")]
    result = _format_applies_to_query(roots)
    assert result == (
        r'System.ItemPathDisplay:="C:\\Users\\kilo\\Dropbox" OR '
        r'System.ItemPathDisplay:~<"C:\\Users\\kilo\\Dropbox\\"'
    )


def test_format_applies_to_query_multiple_roots_or_joined() -> None:
    roots = [Path(r"C:\Users\kilo\Dropbox"), Path(r"D:\Dropbox (Personal)")]
    result = _format_applies_to_query(roots)
    # Each root contributes := + :~< ; four clauses total OR-joined.
    assert result.count(" OR ") == 3
    assert r'System.ItemPathDisplay:="C:\\Users\\kilo\\Dropbox"' in result
    assert r'System.ItemPathDisplay:~<"C:\\Users\\kilo\\Dropbox\\"' in result
    assert r'System.ItemPathDisplay:="D:\\Dropbox (Personal)"' in result
    assert r'System.ItemPathDisplay:~<"D:\\Dropbox (Personal)\\"' in result


def test_format_applies_to_query_refuses_root_with_quote() -> None:
    roots = [Path('C:\\bad"path')]
    with pytest.raises(RuntimeError, match="contains a quote character"):
        _format_applies_to_query(roots)


def test_format_applies_to_query_empty_roots_returns_empty_string() -> None:
    # The dispatcher guards against this case (skipped-no-roots), but
    # the pure helper itself handles it cleanly — empty list ⇒ empty string.
    assert _format_applies_to_query([]) == ""
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_install.py -v -k format_applies_to_query`
Expected: 4 PASS. (The implementation in Step 1 already handles all branches.)

- [ ] **Step 4: Run the full `test_install.py` to ensure no regression**

Run: `uv run python -m pytest tests/test_install.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/dbxignore/install/windows_shell.py tests/test_install.py
git commit -m "feat(install): add windows_shell module skeleton + AppliesTo query builder (#65)"
```

**Hot-fix follow-up:** The original Step 3 implementation doubled backslashes via `.replace("\\", "\\\\")` based on a misreading of `.reg` file syntax as AQS syntax. Empirical Windows testing post-merge proved AQS does no escape interpretation; backslashes are literal. See PR #224 for the fix. The corrected `_format_applies_to_query` embeds path strings with single backslashes and a single trailing `\` on the prefix clause.

---

## Task 3: `install_shell_integration()` happy path

Windows-only function that writes the two HKCU verb keys plus their `\command` subkeys with the right values. Uses `winreg` via lazy import. Idempotent — re-running over an existing install cleanly overwrites all values.

Tests in `tests/test_install_windows_shell.py` use a throwaway registry root under `HKCU\Software\Classes\DbxignoreTest\<uuid>\…` to avoid colliding with a real install.

**Files:**
- Modify: `src/dbxignore/install/windows_shell.py` (add `install_shell_integration`)
- Test: `tests/test_install_windows_shell.py` (new file)

- [ ] **Step 1: Create the test file with module-level Windows-only guard**

Create `tests/test_install_windows_shell.py`:

```python
"""Windows-only tests for HKCU shell-integration registry mechanics (#65).

Module-level double guard mirrors the project's other Windows-only
integration test files (e.g. tests/test_windows_ads_integration.py).
Cross-platform dispatcher behavior is tested separately in
tests/test_install.py.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Iterator

import pytest

pytestmark = pytest.mark.windows_only
if sys.platform != "win32":
    pytest.skip("HKCU registry mechanics are Windows-only", allow_module_level=True)

import winreg  # noqa: E402  # safe — module-level skip above blocks import on non-Windows

from dbxignore.install import windows_shell  # noqa: E402


@pytest.fixture
def isolated_reg_base(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Redirect the module's _REG_BASE to a throwaway HKCU subkey.

    Avoids colliding with a real shell-integration install on the
    developer's machine. The throwaway subtree is deleted on teardown.
    """
    test_id = uuid.uuid4().hex[:8]
    base = f"Software\\Classes\\DbxignoreTest\\{test_id}\\shell"
    monkeypatch.setattr(windows_shell, "_REG_BASE", base)
    try:
        yield base
    finally:
        # Best-effort cleanup. Walk children and delete bottom-up because
        # winreg's DeleteKey only removes leaf keys.
        _delete_subtree_silently(winreg.HKEY_CURRENT_USER, f"Software\\Classes\\DbxignoreTest\\{test_id}")


def _delete_subtree_silently(root: int, path: str) -> None:
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as key:
            # Enumerate subkeys first, then recurse.
            subkeys: list[str] = []
            i = 0
            while True:
                try:
                    subkeys.append(winreg.EnumKey(key, i))
                    i += 1
                except OSError:
                    break
        for sub in subkeys:
            _delete_subtree_silently(root, f"{path}\\{sub}")
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _read_value(base: str, verb: str, value_name: str) -> str:
    """Read a string value from HKCU\\<base>\\<verb> or its \\command subkey.

    Pass value_name="(default)" to read the command-subkey default value.
    """
    if value_name == "(default)":
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{base}\\{verb}\\command") as key:
            value, _ = winreg.QueryValueEx(key, "")
            return value  # type: ignore[no-any-return]
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{base}\\{verb}") as key:
        value, _ = winreg.QueryValueEx(key, value_name)
        return value  # type: ignore[no-any-return]


def test_install_writes_both_verb_keys(
    isolated_reg_base: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Both DbxignoreIgnore and DbxignoreRestore keys present after install."""
    monkeypatch.setattr(
        windows_shell, "detect_cli_invocation",
        lambda: r'"C:\test\dbxignore.exe"',
    )
    windows_shell.install_shell_integration([tmp_path])

    # Both verb keys should be openable.
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{isolated_reg_base}\\DbxignoreIgnore"):
        pass
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{isolated_reg_base}\\DbxignoreRestore"):
        pass


def test_install_sets_mui_verb_labels(
    isolated_reg_base: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        windows_shell, "detect_cli_invocation",
        lambda: r'"C:\test\dbxignore.exe"',
    )
    windows_shell.install_shell_integration([tmp_path])

    assert _read_value(isolated_reg_base, "DbxignoreIgnore", "MUIVerb") == "Ignore from Dropbox"
    assert _read_value(isolated_reg_base, "DbxignoreRestore", "MUIVerb") == "Restore to Dropbox"


def test_install_sets_asymmetric_command_strings(
    isolated_reg_base: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Ignore: no --yes (confirms in console). Restore: --yes (one-click safe)."""
    monkeypatch.setattr(
        windows_shell, "detect_cli_invocation",
        lambda: r'"C:\test\dbxignore.exe"',
    )
    windows_shell.install_shell_integration([tmp_path])

    ignore_cmd = _read_value(isolated_reg_base, "DbxignoreIgnore", "(default)")
    restore_cmd = _read_value(isolated_reg_base, "DbxignoreRestore", "(default)")
    assert ignore_cmd == r'"C:\test\dbxignore.exe" ignore "%1"'
    assert restore_cmd == r'"C:\test\dbxignore.exe" unignore --yes "%1"'


def test_install_applies_to_query_includes_each_root(
    isolated_reg_base: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        windows_shell, "detect_cli_invocation",
        lambda: r'"C:\test\dbxignore.exe"',
    )
    roots = [Path(r"C:\Users\u\Dropbox"), Path(r"D:\Dropbox (Personal)")]
    windows_shell.install_shell_integration(roots)

    applies_to = _read_value(isolated_reg_base, "DbxignoreIgnore", "AppliesTo")
    assert r'System.ItemPathDisplay:="C:\\Users\\u\\Dropbox"' in applies_to
    assert r'System.ItemPathDisplay:~<"C:\\Users\\u\\Dropbox\\"' in applies_to
    assert r'System.ItemPathDisplay:="D:\\Dropbox (Personal)"' in applies_to
    assert r'System.ItemPathDisplay:~<"D:\\Dropbox (Personal)\\"' in applies_to


def test_install_applies_to_same_on_both_verbs(
    isolated_reg_base: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        windows_shell, "detect_cli_invocation",
        lambda: r'"C:\test\dbxignore.exe"',
    )
    windows_shell.install_shell_integration([tmp_path])

    ignore_at = _read_value(isolated_reg_base, "DbxignoreIgnore", "AppliesTo")
    restore_at = _read_value(isolated_reg_base, "DbxignoreRestore", "AppliesTo")
    assert ignore_at == restore_at


def test_install_overwrites_existing_keys(
    isolated_reg_base: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Re-install with different roots: AppliesTo refreshed, no stale clauses."""
    monkeypatch.setattr(
        windows_shell, "detect_cli_invocation",
        lambda: r'"C:\test\dbxignore.exe"',
    )
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    old_root.mkdir()
    new_root.mkdir()

    windows_shell.install_shell_integration([old_root])
    windows_shell.install_shell_integration([new_root])

    applies_to = _read_value(isolated_reg_base, "DbxignoreIgnore", "AppliesTo")
    assert str(new_root).replace("\\", "\\\\") in applies_to
    assert str(old_root).replace("\\", "\\\\") not in applies_to
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_install_windows_shell.py -v` *(on Windows)*
Expected: 6 FAIL with `AttributeError: module 'dbxignore.install.windows_shell' has no attribute 'install_shell_integration'`. On Linux/macOS CI legs the entire file is skipped at module-level.

- [ ] **Step 3: Implement `install_shell_integration()`**

Append to `src/dbxignore/install/windows_shell.py`:

```python
def install_shell_integration(dropbox_roots: list[Path]) -> None:
    """Write the two HKCU verb keys for the given Dropbox roots.

    Raises ``RuntimeError`` if any root contains a literal ``"`` (propagated
    from ``_format_applies_to_query``). On ``OSError`` mid-write, calls
    ``uninstall_shell_integration()`` to clean up partially-written keys
    and re-raises — the result is "nothing or everything," never a
    half-installed state.
    """
    import winreg  # noqa: PLC0415  # lazy import — module loads on non-Windows

    applies_to = _format_applies_to_query(dropbox_roots)
    cli_prefix = detect_cli_invocation()

    verbs = [
        (_IGNORE_VERB, "Ignore from Dropbox", f'{cli_prefix} ignore "%1"'),
        (_RESTORE_VERB, "Restore to Dropbox", f'{cli_prefix} unignore --yes "%1"'),
    ]

    try:
        for verb_key, mui_verb, command in verbs:
            verb_path = f"{_REG_BASE}\\{verb_key}"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, verb_path) as key:
                winreg.SetValueEx(key, "MUIVerb", 0, winreg.REG_SZ, mui_verb)
                winreg.SetValueEx(key, "AppliesTo", 0, winreg.REG_SZ, applies_to)
            command_path = f"{verb_path}\\command"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, command_path) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    except OSError as exc:
        logger.warning(
            "shell-integration install failed mid-write (%s); attempting cleanup", exc
        )
        try:
            uninstall_shell_integration()
        except OSError:
            # Cleanup failure on top of install failure — log but don't mask
            # the original exception below.
            logger.warning("shell-integration cleanup after failed install also failed")
        raise

    logger.info("Installed Explorer right-click integration (HKCU verbs).")
```

**Note:** This references `uninstall_shell_integration` which isn't defined yet. The test runs in Task 3 will fail with `NameError` for the cleanup arm until Task 5 lands the uninstall function. Task 4 covers the partial-write test that needs uninstall to actually clean up; for now the happy-path tests pass because they never enter the except arm.

To unblock the test run, add a stub at module level (Task 5 will replace with the real implementation):

```python
def uninstall_shell_integration(*, errors: list[tuple[str, str]] | None = None) -> None:
    """Stub — full implementation in Task 5."""
    raise NotImplementedError("uninstall_shell_integration: implemented in Task 5")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_install_windows_shell.py -v -k "test_install_writes or test_install_sets or test_install_applies_to or test_install_overwrites"` *(on Windows)*
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dbxignore/install/windows_shell.py tests/test_install_windows_shell.py
git commit -m "feat(install): write HKCU shell-integration verb keys on Windows (#65)"
```

---

## Task 4: `install_shell_integration()` partial-write recovery

When a registry write fails mid-install (e.g. `winreg.SetValueEx` raising on the second key after the first key already landed), the function must clean up the partially-written state before re-raising. Tested by monkeypatching `winreg.SetValueEx` to raise after a configurable number of calls.

This task implements the real `uninstall_shell_integration()` *partially* (just enough for the cleanup arm to work). Task 5 finishes it with the errors-list accumulator and all edge cases.

**Files:**
- Modify: `src/dbxignore/install/windows_shell.py`
- Test: `tests/test_install_windows_shell.py`

- [ ] **Step 1: Add the failing test**

Append to `tests/test_install_windows_shell.py`:

```python
def test_install_partial_write_failure_cleans_up(
    isolated_reg_base: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """SetValueEx raising mid-install: no DbxignoreIgnore/DbxignoreRestore keys remain."""
    monkeypatch.setattr(
        windows_shell, "detect_cli_invocation",
        lambda: r'"C:\test\dbxignore.exe"',
    )

    call_count = {"n": 0}
    real_set = winreg.SetValueEx

    def flaky_set(key: int, name: str, *args: object) -> None:
        call_count["n"] += 1
        if call_count["n"] == 3:  # After Ignore verb's MUIVerb + AppliesTo, before command.
            raise OSError(13, "Access denied (simulated)")
        real_set(key, name, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(winreg, "SetValueEx", flaky_set)

    with caplog.at_level("WARNING"):
        with pytest.raises(OSError, match="Access denied"):
            windows_shell.install_shell_integration([tmp_path])

    # Neither verb key should be present after the partial-write recovery.
    for verb in ("DbxignoreIgnore", "DbxignoreRestore"):
        with pytest.raises(FileNotFoundError):
            winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, f"{isolated_reg_base}\\{verb}"
            )

    assert any("install failed mid-write" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m pytest tests/test_install_windows_shell.py::test_install_partial_write_failure_cleans_up -v` *(on Windows)*
Expected: FAIL with `NotImplementedError: uninstall_shell_integration: implemented in Task 5` (the stub from Task 3 raises) — confirming the cleanup arm is being reached.

- [ ] **Step 3: Implement a minimal `uninstall_shell_integration()` for cleanup**

Replace the stub `uninstall_shell_integration` in `src/dbxignore/install/windows_shell.py` with the minimal cleanup-capable version. (Task 5 extends with the errors-list accumulator and full test coverage.)

```python
def uninstall_shell_integration(*, errors: list[tuple[str, str]] | None = None) -> None:
    """Remove the two HKCU verb keys. Idempotent.

    Walks each verb's tree in reverse order: command subkey first
    (winreg.DeleteKey only deletes leaf keys), then the verb key itself.
    FileNotFoundError on any DeleteKey call is treated as "already gone."

    On non-FileNotFoundError OSError: if ``errors`` is provided, append
    ``(registry_key_path, message)``; otherwise log WARNING. Loop always
    continues to the next key — never aborts partway.
    """
    import winreg  # noqa: PLC0415  # lazy import — module loads on non-Windows

    for verb_key in (_IGNORE_VERB, _RESTORE_VERB):
        for subpath in (f"{_REG_BASE}\\{verb_key}\\command", f"{_REG_BASE}\\{verb_key}"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subpath)
            except FileNotFoundError:
                pass
            except OSError as exc:
                msg = f"DeleteKey failed: {exc}"
                if errors is not None:
                    errors.append((f"HKCU\\{subpath}", msg))
                else:
                    logger.warning("shell-integration uninstall: %s on %s", exc, subpath)
```

- [ ] **Step 4: Run the partial-write test to verify it passes**

Run: `uv run python -m pytest tests/test_install_windows_shell.py::test_install_partial_write_failure_cleans_up -v` *(on Windows)*
Expected: PASS.

- [ ] **Step 5: Run all Task 3 + Task 4 tests to ensure no regression**

Run: `uv run python -m pytest tests/test_install_windows_shell.py -v -k "test_install"` *(on Windows)*
Expected: all install-related tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/dbxignore/install/windows_shell.py tests/test_install_windows_shell.py
git commit -m "feat(install): clean up partial-write registry state on install failure (#65)"
```

---

## Task 5: `uninstall_shell_integration()` — full coverage

Round out the uninstall function's tests for the two contract paths: clean removal after a clean install, and the parametrized `errors=[]` / `errors=None` behavior for non-FileNotFoundError `OSError`.

**Files:**
- Modify: `tests/test_install_windows_shell.py` (add three new tests)
- No code change needed — Task 4 already wrote the function. This task is test-only.

- [ ] **Step 1: Add the three failing tests**

Append to `tests/test_install_windows_shell.py`:

```python
def test_uninstall_removes_both_verb_keys(
    isolated_reg_base: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Clean install + clean uninstall: both verb keys are gone."""
    monkeypatch.setattr(
        windows_shell, "detect_cli_invocation",
        lambda: r'"C:\test\dbxignore.exe"',
    )
    windows_shell.install_shell_integration([tmp_path])
    windows_shell.uninstall_shell_integration()

    for verb in ("DbxignoreIgnore", "DbxignoreRestore"):
        with pytest.raises(FileNotFoundError):
            winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, f"{isolated_reg_base}\\{verb}"
            )


def test_uninstall_idempotent_when_keys_missing(
    isolated_reg_base: str,
) -> None:
    """No error when uninstall is called against a clean registry."""
    # Should not raise — the FileNotFoundError arms swallow the missing-key case.
    windows_shell.uninstall_shell_integration()
    windows_shell.uninstall_shell_integration(errors=[])


@pytest.mark.parametrize("with_errors_list", [True, False])
def test_uninstall_other_oserror_routes_to_errors_or_warning(
    isolated_reg_base: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    with_errors_list: bool,
) -> None:
    """Non-FileNotFoundError OSError: routed to errors list OR logged as WARNING.

    Loop must always continue to the next key — never abort partway.
    """
    monkeypatch.setattr(
        windows_shell, "detect_cli_invocation",
        lambda: r'"C:\test\dbxignore.exe"',
    )
    windows_shell.install_shell_integration([tmp_path])

    real_delete = winreg.DeleteKey
    call_count = {"n": 0}

    def flaky_delete(root: int, path: str) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:  # First call (DbxignoreIgnore's command subkey).
            raise OSError(5, "Access denied (simulated)")
        real_delete(root, path)

    monkeypatch.setattr(winreg, "DeleteKey", flaky_delete)

    errors: list[tuple[str, str]] | None = [] if with_errors_list else None
    with caplog.at_level("WARNING"):
        windows_shell.uninstall_shell_integration(errors=errors)

    # The second verb's keys (DbxignoreRestore) should still be removed —
    # the loop didn't abort.
    with pytest.raises(FileNotFoundError):
        winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, f"{isolated_reg_base}\\DbxignoreRestore"
        )

    if with_errors_list:
        assert errors is not None and len(errors) == 1
        assert errors[0][0].endswith("DbxignoreIgnore\\command")
        assert "Access denied" in errors[0][1]
        # WARNING path is NOT taken when errors list provided.
        assert not any("shell-integration uninstall" in r.message for r in caplog.records)
    else:
        assert any(
            "shell-integration uninstall" in r.message and "Access denied" in r.message
            for r in caplog.records
        )
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_install_windows_shell.py -v` *(on Windows)*
Expected: All 10 tests pass (6 from Task 3, 1 from Task 4, 3 from Task 5 — the parametrized test counts as 1 for the file's test inventory even though it runs as 2 cases).

- [ ] **Step 3: Commit**

```bash
git add tests/test_install_windows_shell.py
git commit -m "test(install): cover uninstall_shell_integration error-routing branches (#65)"
```

---

## Task 6: Dispatcher helpers in `install/__init__.py`

Two new helpers that branch on `sys.platform` first, then delegate to `windows_shell` on Windows. Cross-platform tests inject a fake `windows_shell` module via `sys.modules` to exercise the Windows arm on Linux/macOS CI legs.

**Files:**
- Modify: `src/dbxignore/install/__init__.py`
- Test: `tests/test_install.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_install.py`:

```python
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dbxignore import install as install_pkg


def _inject_fake_windows_shell(
    monkeypatch: pytest.MonkeyPatch,
    *,
    install_side_effect: object = None,
) -> types.ModuleType:
    """Inject a fake dbxignore.install.windows_shell module into sys.modules.

    The dispatcher uses a lazy `from dbxignore.install.windows_shell import ...`
    inside the `if sys.platform == "win32":` arm. To exercise that arm on
    non-Windows test legs we must populate sys.modules so the lazy import
    resolves to our fake — patching install_pkg.windows_shell would silently
    miss because the lazy import hasn't fired yet to create the attribute.
    """
    fake = types.ModuleType("dbxignore.install.windows_shell")
    fake.install_shell_integration = MagicMock(side_effect=install_side_effect)
    fake.uninstall_shell_integration = MagicMock()
    monkeypatch.setitem(sys.modules, "dbxignore.install.windows_shell", fake)
    return fake


def test_dispatcher_install_skipped_platform_on_non_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    fake = _inject_fake_windows_shell(monkeypatch)

    outcome = install_pkg.install_shell_integration_if_supported(dropbox_roots=[tmp_path])

    assert outcome == "skipped-platform"
    fake.install_shell_integration.assert_not_called()


def test_dispatcher_install_skipped_no_roots(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    fake = _inject_fake_windows_shell(monkeypatch)

    with caplog.at_level("WARNING"):
        outcome = install_pkg.install_shell_integration_if_supported(dropbox_roots=[])

    assert outcome == "skipped-no-roots"
    fake.install_shell_integration.assert_not_called()
    assert any("no Dropbox roots" in r.message for r in caplog.records)


def test_dispatcher_install_skipped_bad_roots(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    fake = _inject_fake_windows_shell(
        monkeypatch,
        install_side_effect=RuntimeError("contains a quote character"),
    )

    with caplog.at_level("WARNING"):
        outcome = install_pkg.install_shell_integration_if_supported(dropbox_roots=[tmp_path])

    assert outcome == "skipped-bad-roots"
    assert any("quote character" in r.message for r in caplog.records)


def test_dispatcher_uninstall_skipped_platform_on_non_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    fake = _inject_fake_windows_shell(monkeypatch)

    outcome = install_pkg.uninstall_shell_integration_if_supported()

    assert outcome == "skipped-platform"
    fake.uninstall_shell_integration.assert_not_called()


def test_dispatcher_uninstall_threads_errors_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dispatcher must pass through the exact `errors` list object."""
    monkeypatch.setattr(sys, "platform", "win32")
    fake = _inject_fake_windows_shell(monkeypatch)

    my_errors: list[tuple[str, str]] = []
    outcome = install_pkg.uninstall_shell_integration_if_supported(errors=my_errors)

    assert outcome == "uninstalled"
    # The kwarg `errors=` must be the same object we passed in (identity check).
    fake.uninstall_shell_integration.assert_called_once()
    assert fake.uninstall_shell_integration.call_args.kwargs["errors"] is my_errors
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_install.py -v -k dispatcher`
Expected: 5 FAIL with `AttributeError: module 'dbxignore.install' has no attribute 'install_shell_integration_if_supported'`.

- [ ] **Step 3: Implement the dispatcher helpers**

Append to `src/dbxignore/install/__init__.py`:

```python
import logging
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

InstallOutcome = Literal[
    "installed", "skipped-no-roots", "skipped-bad-roots", "skipped-platform"
]
UninstallOutcome = Literal["uninstalled", "skipped-platform"]


def install_shell_integration_if_supported(
    *, dropbox_roots: list[Path]
) -> InstallOutcome:
    """Install Windows Explorer right-click verbs; no-op on Linux/macOS.

    Branches on ``sys.platform`` first — non-Windows returns ``"skipped-platform"``
    without referencing the windows_shell module. On Windows, an empty
    ``dropbox_roots`` returns ``"skipped-no-roots"`` with a WARNING; a
    ``RuntimeError`` from the platform module (typically a refused root
    containing ``"``) returns ``"skipped-bad-roots"`` with a WARNING.
    """
    if sys.platform != "win32":
        logger.debug("shell-integration install: no-op on platform %s", sys.platform)
        return "skipped-platform"
    if not dropbox_roots:
        logger.warning(
            "shell-integration install: no Dropbox roots discovered; skipping. "
            "Re-run `dbxignore install` after Dropbox is set up."
        )
        return "skipped-no-roots"
    from dbxignore.install.windows_shell import install_shell_integration

    try:
        install_shell_integration(dropbox_roots)
    except RuntimeError as exc:
        logger.warning("shell-integration install refused: %s", exc)
        return "skipped-bad-roots"
    return "installed"


def uninstall_shell_integration_if_supported(
    *, errors: list[tuple[str, str]] | None = None
) -> UninstallOutcome:
    """Remove Windows Explorer right-click verbs; no-op on Linux/macOS.

    The optional ``errors`` accumulator is threaded through to the platform
    module so CLI ``--purge`` can escalate registry failures into a non-zero
    exit. When ``errors=None`` (plain ``uninstall``), the platform module
    falls back to logging WARNINGs for each failed DeleteKey.
    """
    if sys.platform != "win32":
        logger.debug("shell-integration uninstall: no-op on platform %s", sys.platform)
        return "skipped-platform"
    from dbxignore.install.windows_shell import uninstall_shell_integration

    uninstall_shell_integration(errors=errors)
    return "uninstalled"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_install.py -v -k dispatcher`
Expected: 5 PASS.

- [ ] **Step 5: Run full `test_install.py` to ensure no regression**

Run: `uv run python -m pytest tests/test_install.py -v`
Expected: all tests pass (existing tests + new dispatcher tests + Task 2's pure-helper tests).

- [ ] **Step 6: Commit**

```bash
git add src/dbxignore/install/__init__.py tests/test_install.py
git commit -m "feat(install): add dispatcher helpers for shell-integration arm (#65)"
```

---

## Task 7: CLI flag plumbing

Add `--no-shell-integration` to both `install` and `uninstall` commands. Wire the dispatchers. For `uninstall --purge`, escalate registry cleanup failures into the existing exit-2 path.

**Files:**
- Modify: `src/dbxignore/cli.py`
- Test: `tests/test_install.py`

- [ ] **Step 1: Write the failing CLI plumbing tests**

Append to `tests/test_install.py`:

```python
from click.testing import CliRunner

from dbxignore import cli as cli_module


def _make_cli_test_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[CliRunner, MagicMock, MagicMock]:
    """Mock install_service, uninstall_service, and the shell-integration dispatcher.

    Returns the runner plus the two mock objects for assertions.
    """
    install_service = MagicMock()
    uninstall_service = MagicMock()
    install_shell = MagicMock(return_value="installed")
    uninstall_shell = MagicMock(return_value="uninstalled")

    monkeypatch.setattr("dbxignore.install.install_service", install_service)
    monkeypatch.setattr("dbxignore.install.uninstall_service", uninstall_service)
    monkeypatch.setattr(
        "dbxignore.install.install_shell_integration_if_supported", install_shell
    )
    monkeypatch.setattr(
        "dbxignore.install.uninstall_shell_integration_if_supported", uninstall_shell
    )
    # Stub _discover_roots so we get deterministic roots into the dispatcher.
    monkeypatch.setattr(cli_module, "_discover_roots", lambda: [tmp_path])

    return CliRunner(), install_shell, uninstall_shell


def test_install_calls_shell_helper_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner, install_shell, _ = _make_cli_test_env(monkeypatch, tmp_path)
    result = runner.invoke(cli_module.main, ["install"])
    assert result.exit_code == 0, result.output
    install_shell.assert_called_once()
    assert install_shell.call_args.kwargs["dropbox_roots"] == [tmp_path]


def test_install_no_shell_integration_skips_helper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner, install_shell, _ = _make_cli_test_env(monkeypatch, tmp_path)
    result = runner.invoke(cli_module.main, ["install", "--no-shell-integration"])
    assert result.exit_code == 0, result.output
    install_shell.assert_not_called()


def test_uninstall_calls_shell_helper_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner, _, uninstall_shell = _make_cli_test_env(monkeypatch, tmp_path)
    result = runner.invoke(cli_module.main, ["uninstall"])
    assert result.exit_code == 0, result.output
    uninstall_shell.assert_called_once()
    # Plain uninstall: no errors list, so WARN-and-continue applies.
    assert uninstall_shell.call_args.kwargs.get("errors") is None


def test_uninstall_no_shell_integration_skips_helper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner, _, uninstall_shell = _make_cli_test_env(monkeypatch, tmp_path)
    result = runner.invoke(
        cli_module.main, ["uninstall", "--no-shell-integration"]
    )
    assert result.exit_code == 0, result.output
    uninstall_shell.assert_not_called()


def test_uninstall_purge_overrides_no_shell_integration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--purge --no-shell-integration: shell dispatcher invoked exactly once with errors list."""
    runner, _, uninstall_shell = _make_cli_test_env(monkeypatch, tmp_path)
    result = runner.invoke(
        cli_module.main, ["uninstall", "--purge", "--no-shell-integration"]
    )
    assert result.exit_code == 0, result.output
    uninstall_shell.assert_called_once()
    # Under --purge, errors list IS provided (escalation path).
    assert uninstall_shell.call_args.kwargs["errors"] == []


def test_uninstall_purge_exits_2_on_shell_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the shell-integration arm populates the errors list, --purge must exit 2."""
    runner, _, uninstall_shell = _make_cli_test_env(monkeypatch, tmp_path)

    def populate_errors(*, errors: list[tuple[str, str]]) -> str:
        errors.append((r"HKCU\Software\Classes\…\DbxignoreIgnore", "Access denied"))
        return "uninstalled"

    uninstall_shell.side_effect = populate_errors
    result = runner.invoke(cli_module.main, ["uninstall", "--purge"])
    assert result.exit_code == 2, result.output
    assert "Access denied" in result.output or "Access denied" in (result.stderr or "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_install.py -v -k "install_calls or install_no_shell or uninstall_calls or uninstall_no_shell or uninstall_purge"`
Expected: 6 FAIL — flags not yet defined; results depend on click's option-validation surface but will not pass.

- [ ] **Step 3: Update the `install` command in `src/dbxignore/cli.py`**

Locate the existing `install` command (around line 1508 — check with `grep -n "^def install" src/dbxignore/cli.py`). Replace its body with the new version:

```python
@main.command()
@click.option(
    "--no-shell-integration",
    is_flag=True,
    help=(
        "Skip Explorer right-click integration (Windows only). "
        "No effect on Linux or macOS."
    ),
)
def install(no_shell_integration: bool) -> None:
    """Register the daemon with the platform's user-scoped service manager.

    On Windows, also registers two right-click verbs in Explorer
    ("Ignore from Dropbox" and "Restore to Dropbox"), scoped to discovered
    Dropbox roots. Pass --no-shell-integration to skip the registry write.
    """
    from dbxignore.install import (
        install_service,
        install_shell_integration_if_supported,
    )

    try:
        install_service()
    except RuntimeError as exc:
        click.echo(f"Failed to install daemon service: {exc}", err=True)
        sys.exit(2)
    click.echo("Installed dbxignore daemon service.")

    if not no_shell_integration:
        outcome = install_shell_integration_if_supported(
            dropbox_roots=_discover_roots(),
        )
        if outcome == "installed":
            click.echo("Installed Explorer right-click integration.")
```

- [ ] **Step 4: Update the `uninstall` command in `src/dbxignore/cli.py`**

Locate the existing `uninstall` command (around line 1521 — check with `grep -n "^def uninstall" src/dbxignore/cli.py`). Add the new flag and the dispatcher calls. The `--purge` block already has a marker-clear errors list; we add shell errors next to it.

Replace:

```python
@main.command()
@click.option(
    "--purge",
    is_flag=True,
    help=(
        "Also clear every ignore marker and remove local dbxignore state "
        "(state.json, daemon.log*, the state directory, and any systemd "
        "drop-in directory on Linux)."
    ),
)
def uninstall(purge: bool) -> None:
```

with:

```python
@main.command()
@click.option(
    "--purge",
    is_flag=True,
    help=(
        "Also clear every ignore marker and remove local dbxignore state "
        "(state.json, daemon.log*, the state directory, and any systemd "
        "drop-in directory on Linux). Under --purge, --no-shell-integration "
        "is overridden — Explorer integration is always removed."
    ),
)
@click.option(
    "--no-shell-integration",
    is_flag=True,
    help=(
        "Preserve Explorer right-click integration across the uninstall. "
        "Ignored under --purge. No effect on Linux or macOS."
    ),
)
def uninstall(purge: bool, no_shell_integration: bool) -> None:
```

Then, immediately after the existing `click.echo("Uninstalled dbxignore daemon service.")` (which fires after the `install_service()` call), insert the shell-integration plumbing. The full block:

```python
    from dbxignore.install import (
        uninstall_service,
        uninstall_shell_integration_if_supported,
    )

    try:
        uninstall_service()
    except RuntimeError as exc:
        click.echo(f"Failed to uninstall daemon service: {exc}", err=True)
        sys.exit(2)
    click.echo("Uninstalled dbxignore daemon service.")

    # Mutually exclusive: --purge ALWAYS runs the dispatcher and accumulates
    # errors for exit-2 escalation; plain uninstall runs it only if the user
    # didn't pass --no-shell-integration, and uses WARN-and-continue.
    shell_errors: list[tuple[str, str]] = []
    if purge:
        uninstall_shell_integration_if_supported(errors=shell_errors)
    elif not no_shell_integration:
        uninstall_shell_integration_if_supported()
```

Then, inside the existing `if purge:` block (the one that runs marker-clear etc.), find the existing exit-2 escalation:

```python
        if errors:
            sys.exit(2)
```

and replace with:

```python
        for key, msg in shell_errors[:_MAX_REPORTED_ERRORS]:
            click.echo(f"  error: {key} - {msg}", err=True)
        if errors or shell_errors:
            sys.exit(2)
```

- [ ] **Step 5: Run the CLI plumbing tests**

Run: `uv run python -m pytest tests/test_install.py -v -k "install_calls or install_no_shell or uninstall_calls or uninstall_no_shell or uninstall_purge"`
Expected: 6 PASS.

- [ ] **Step 6: Run the full test suite to catch regressions**

Run: `uv run python -m pytest -m "not windows_only"`
Expected: all portable tests pass (Linux/macOS-equivalent of CI).

- [ ] **Step 7: Run the Windows-only tests too if on Windows**

Run: `uv run python -m pytest tests/test_install_windows_shell.py -v` *(on Windows)*
Expected: 10 tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/dbxignore/cli.py tests/test_install.py
git commit -m "feat(cli): plumb --no-shell-integration through install + uninstall (#65)"
```

---

## Task 8: Manual-test script extensions

Add Phase 5 case `5g` (read-only registry assertions after default install) and Phase 6 cases `6c` / `6d` / `6e` (registry-gone after plain uninstall; `--no-shell-integration` preserves; `--purge` overrides). Phase 5 ends in installed-daemon state (unchanged); Phase 6 cases append at the end so the existing flow's PR #87 race-protection dance is untouched.

**Files:**
- Modify: `scripts/manual-test-windows.ps1`

- [ ] **Step 1: Add Phase 5 case `5g`**

Locate the end of `Test-WatchdogAndSweep` (or whichever function owns Phase 5; check with `grep -n "5f" scripts/manual-test-windows.ps1`). Append the new case `5g` right before the function's closing `}`:

```powershell
    # 5g — registry keys after default install (PR #NNN)
    # Read-only: doesn't mutate state. Phase 5 ends in installed-daemon
    # state, exactly as today, so Phase 6's `dbxignore uninstall` precondition
    # is preserved.
    Write-Note "5g - HKCU verb keys present after default install"
    $regBase = "HKCU:\Software\Classes\AllFilesystemObjects\shell"
    $ignoreKey = "$regBase\DbxignoreIgnore"
    $restoreKey = "$regBase\DbxignoreRestore"

    if ((Test-Path $ignoreKey) -and (Test-Path $restoreKey)) {
        Write-Pass "5g - both verb keys present"
    } else {
        Write-Fail "5g - verb keys missing after default install"
    }

    # MUIVerb labels.
    $ignoreLabel = (Get-ItemProperty -Path $ignoreKey -Name "MUIVerb").MUIVerb
    $restoreLabel = (Get-ItemProperty -Path $restoreKey -Name "MUIVerb").MUIVerb
    if ($ignoreLabel -eq "Ignore from Dropbox" -and $restoreLabel -eq "Restore to Dropbox") {
        Write-Pass "5g - MUIVerb labels correct"
    } else {
        Write-Fail "5g - MUIVerb labels wrong: ignore='$ignoreLabel' restore='$restoreLabel'"
    }

    # AppliesTo includes the Dropbox root.
    $appliesTo = (Get-ItemProperty -Path $ignoreKey -Name "AppliesTo").AppliesTo
    $dropboxEscaped = $script:DropboxDir -replace "\\", "\\"
    if ($appliesTo -like "*$dropboxEscaped*") {
        Write-Pass "5g - AppliesTo contains Dropbox root"
    } else {
        Write-Fail "5g - AppliesTo missing Dropbox root: $appliesTo"
    }

    # Command strings — asymmetric --yes policy.
    $ignoreCmd = (Get-ItemProperty -Path "$ignoreKey\command" -Name "(default)").'(default)'
    $restoreCmd = (Get-ItemProperty -Path "$restoreKey\command" -Name "(default)").'(default)'
    if ($ignoreCmd -match '\bignore "%1"$' -and $ignoreCmd -notmatch "--yes") {
        Write-Pass "5g - ignore command lacks --yes (confirms in console)"
    } else {
        Write-Fail "5g - ignore command shape unexpected: $ignoreCmd"
    }
    if ($restoreCmd -match '\bunignore --yes "%1"$') {
        Write-Pass "5g - restore command has --yes (one-click safe)"
    } else {
        Write-Fail "5g - restore command shape unexpected: $restoreCmd"
    }
```

- [ ] **Step 2: Add Phase 6 case `6c` (between existing 6a and the re-install)**

In `Test-Uninstall`, locate the block where the existing `6a` assertion finishes (around line 1083 — search for `# 6a -`). Right after the closing brace of the `if/else` for 6a, before `Write-Note "re-installing for --purge test..."`, insert `6c`:

```powershell
    # 6c — registry keys gone after plain uninstall (PR #NNN)
    Write-Note "6c - HKCU verb keys removed by default uninstall"
    $regBase = "HKCU:\Software\Classes\AllFilesystemObjects\shell"
    if (-not (Test-Path "$regBase\DbxignoreIgnore") -and -not (Test-Path "$regBase\DbxignoreRestore")) {
        Write-Pass "6c - both verb keys removed"
    } else {
        Write-Fail "6c - verb keys persisted after plain uninstall"
    }
```

- [ ] **Step 3: Add Phase 6 cases `6d` and `6e` at end of `Test-Uninstall`**

Append at the end of `Test-Uninstall` (after the existing `6b` block, just before the function's closing `}`):

```powershell
    # 6d / 6e — --no-shell-integration preservation + --purge override (PR #NNN)
    # Phase 6's existing flow ended with `dbxignore uninstall --purge` →
    # daemon gone, state gone, registry gone. We now exercise the
    # --no-shell-integration contrast cycle: re-install, plain uninstall
    # with the flag (keys preserved), re-install, --purge with the flag
    # (keys gone — purge override).

    Write-Note "6d setup - re-install for --no-shell-integration test"
    dbxignore install 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Stop-Abort "6d setup re-install failed" }
    # Wait briefly for the new daemon to write state.json.
    for ($i = 0; $i -lt 10; $i++) {
        if (Test-Path $stateFile) { break }
        Start-Sleep -Seconds 1
    }

    # 6d - plain uninstall --no-shell-integration: daemon gone, keys preserved.
    Write-Note "6d - uninstall --no-shell-integration preserves verb keys"
    dbxignore uninstall --no-shell-integration 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "6d - uninstall --no-shell-integration failed"
    } else {
        $regBase = "HKCU:\Software\Classes\AllFilesystemObjects\shell"
        if ((Test-Path "$regBase\DbxignoreIgnore") -and (Test-Path "$regBase\DbxignoreRestore")) {
            Write-Pass "6d - verb keys preserved after --no-shell-integration uninstall"
        } else {
            Write-Fail "6d - verb keys removed despite --no-shell-integration"
        }
    }

    Write-Note "6e setup - re-install for --purge --no-shell-integration test"
    dbxignore install 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Stop-Abort "6e setup re-install failed" }
    for ($i = 0; $i -lt 10; $i++) {
        if (Test-Path $stateFile) { break }
        Start-Sleep -Seconds 1
    }

    # 6e - --purge --no-shell-integration: --purge overrides the preserve flag.
    Write-Note "6e - uninstall --purge --no-shell-integration removes verb keys"
    dbxignore uninstall --purge --no-shell-integration 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "6e - uninstall --purge --no-shell-integration failed"
    } else {
        $regBase = "HKCU:\Software\Classes\AllFilesystemObjects\shell"
        if (-not (Test-Path "$regBase\DbxignoreIgnore") -and -not (Test-Path "$regBase\DbxignoreRestore")) {
            Write-Pass "6e - verb keys removed by --purge override"
        } else {
            Write-Fail "6e - verb keys persisted despite --purge"
        }
    }
```

- [ ] **Step 4: PowerShell syntax check**

Run: `pwsh -NoProfile -Command "& { Get-Command -Syntax (Get-Item ./scripts/manual-test-windows.ps1).FullName }"` *(if on Windows or with pwsh installed elsewhere)*

If `pwsh` isn't available, the minimum check is that the script tokenizes:
```bash
python3 -c "import re; content = open('scripts/manual-test-windows.ps1').read(); assert content.count('{') == content.count('}'), 'brace mismatch'; print('balanced braces')"
```

Expected: no errors / "balanced braces".

- [ ] **Step 5: Commit**

```bash
git add scripts/manual-test-windows.ps1
git commit -m "test(scripts): add Phase 5/6 shell-integration manual-test cases (#65)"
```

---

## Task 9: Docs & backlog close

Update README, CHANGELOG, AGENTS.md, and BACKLOG.md.

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `AGENTS.md`
- Modify: `BACKLOG.md`

- [ ] **Step 1: Add `Windows Explorer integration` section to README.md**

Locate the Windows-specific section in `README.md` (search for `## Windows` or `Windows` headings). Insert a new subsection in the natural place (likely after the Windows install instructions, before any Linux section):

```markdown
### Windows Explorer integration

On Windows, `dbxignore install` registers two right-click verbs in Explorer:

- **Ignore from Dropbox** — opens a console window asking for confirmation,
  then runs `dbxignore.exe ignore <path>`. The confirmation prompt is intentional:
  marking a path ignored causes Dropbox to delete it from the cloud and propagate
  the deletion to every linked device.
- **Restore to Dropbox** — one-click; runs `dbxignore.exe unignore --yes <path>`.
  Safe direction (Dropbox re-syncs the path).

The verbs only appear under discovered Dropbox roots — the `AppliesTo` filter is
generated at install time from `~/.dropbox/info.json`. To skip the registry write,
pass `--no-shell-integration` to `install` (also accepted on Linux/macOS as a no-op
for portable scripts). To preserve the verbs across a daemon reinstall, pass
`--no-shell-integration` to `uninstall`. `uninstall --purge` always removes them.

If you move your Dropbox folder, re-run `dbxignore install` to refresh the
`AppliesTo` filter.
```

- [ ] **Step 2: Add `[Unreleased]` entry to CHANGELOG.md**

Open `CHANGELOG.md`. If there's no `## [Unreleased]` section at the top, add one. Under `### Added`, add:

```markdown
- Windows Explorer right-click integration. `dbxignore install` now registers
  two HKCU shell verbs — "Ignore from Dropbox" and "Restore to Dropbox" —
  scoped to discovered Dropbox roots via an `AppliesTo` query filter. Pass
  `--no-shell-integration` to opt out of the registry write; pass it to
  `uninstall` to preserve the verbs across a daemon reinstall; `uninstall
  --purge` always removes them. The asymmetric command bindings reflect the
  data-loss asymmetry between the two directions: "Ignore" runs without
  `--yes` (confirmation in the spawned console), "Restore" runs with `--yes`
  (one-click safe). On Linux and macOS the flag is silently accepted as a
  no-op so portable scripts work unchanged. (#65)
```

- [ ] **Step 3: Add a bullet to AGENTS.md**

Open `AGENTS.md` and find the paragraph about `install/` (search for `install/` in the file — section starts at "`install/` is a package …"). Add this sentence to the paragraph, after the existing description of `windows_task.py`:

```markdown
On Windows, `install/windows_shell.py` additionally writes two HKCU shell-integration
verb keys (`HKCU\Software\Classes\AllFilesystemObjects\shell\DbxignoreIgnore` and
`…\DbxignoreRestore`), opt-out via `dbxignore install --no-shell-integration`.
The `Ignore from Dropbox` command runs without `--yes` (console-confirm because
ignoring is destructive — Dropbox deletes from cloud and propagates), the `Restore
to Dropbox` command runs with `--yes` (one-click safe). The `AppliesTo` filter
is generated from `roots.discover()` and uses `:=` for the root itself plus
`:~<` with a trailing `\` for descendants — together avoiding false matches on
sibling folders like `Dropbox-other`.
```

- [ ] **Step 4: Close #65 in BACKLOG.md**

Open `BACKLOG.md`. Find the `## 65.` header (around line 1431). At the end of the title line, append a status marker on a new line right after the title:

```markdown
## 65. No Windows Explorer right-click context-menu integration

**Status: RESOLVED 2026-05-12 (PR #NNN).** Took fix candidate (1) — `dbxignore install --shell-integration` shipped, bundled into the default `install` arm with a `--no-shell-integration` opt-out. HKCU keys under `…\AllFilesystemObjects\shell\DbxignoreIgnore` and `…\DbxignoreRestore`. Asymmetric `--yes` policy (confirm on ignore, one-click on restore) — confirmed via design Q2. `AppliesTo` uses `:=` + `:~<` per discovered root to avoid sibling false-matches. Routes through `dbxignore.exe ignore "%1"` / `dbxignore.exe unignore --yes "%1"` (item #93 prerequisite landed in PR #191). Uninstall mirrors install; `uninstall --purge` overrides `--no-shell-integration` and escalates registry-cleanup errors to exit 2.

…existing body remains for historical context…
```

Then update the `### Open` summary at the top of the Status section (line 2487-2489) — change "Twenty-three items" to "Twenty-two items" and remove the `#65` bullet (line 2499).

Add an entry to `### Resolved (reverse chronological)`. Find the `#### 2026-05-12 (v0.5.1)` subsection or create a new `#### 2026-05-12` subsection if the v0.5.1 block already exists separately. Add:

```markdown
- **#65** (2026-05-12, PR #NNN) — Windows Explorer right-click integration shipped. `dbxignore install` now writes two HKCU shell verbs ("Ignore from Dropbox" / "Restore to Dropbox") scoped to discovered Dropbox roots via an `AppliesTo` query; `dbxignore uninstall` removes them. `--no-shell-integration` opts out on either side; `--purge` overrides the preserve flag and escalates registry failures into the exit-2 path. Implementation: new `install/windows_shell.py` (lazy `winreg` import), new `_common.detect_cli_invocation()` helper, two new `install/__init__.py` dispatcher helpers with `Literal` outcome types, CLI plumbing in `install`/`uninstall`. Tests: 10 Windows-only registry-mechanics tests in `test_install_windows_shell.py` (module-level double guard) + 5 dispatcher + 6 CLI plumbing tests in `test_install.py` (cross-platform via `sys.modules` fake-injection so the dispatcher's Windows arm runs on Linux/macOS CI legs). Manual-test extensions: Phase 5 case `5g` (read-only registry assertions, doesn't mutate Phase 5 end-state); Phase 6 cases `6c`/`6d`/`6e` (registry-gone after plain uninstall, preserve-on-flag, purge-overrides-flag).
```

- [ ] **Step 5: Commit**

```bash
git add README.md CHANGELOG.md AGENTS.md BACKLOG.md
git commit -m "docs: announce windows shell integration + close BACKLOG #65"
```

---

## Task 10: Final verification

Run the full check suite locally, fix anything that's loose, and verify pre-flight commit-check passes on every commit in the branch.

- [ ] **Step 1: Run ruff lint + format**

```bash
uv run ruff check . --fix
uv run ruff check .
uv run ruff format --check .
```

Expected: all clean. If `--fix` made changes, re-run lint to confirm and amend the relevant feature commit (Task 1, 3, 6, or 7) — never bundle ruff fixes into a docs commit.

- [ ] **Step 2: Run mypy**

```bash
uv run mypy .
```

Expected: no errors. If errors appear, common fixes:
- `winreg` import-not-found on non-Windows: the lazy import inside functions avoids this at module level; verify the type-check leg doesn't try to resolve it eagerly.
- `Literal` mismatch: ensure dispatcher return types match exactly the spelled-out tuple in `install/__init__.py`.

- [ ] **Step 3: Run the full pytest suite (portable subset)**

```bash
uv run python -m pytest -m "not windows_only" -v
```

Expected: all tests pass. The Windows-only file is skipped at module-level.

- [ ] **Step 4: Run the Windows-only test file (if on Windows)**

```bash
uv run python -m pytest tests/test_install_windows_shell.py -v
```

Expected: 10 tests pass. *(Skipped on Linux/macOS — the platform CI matrix exercises this on the Windows leg.)*

- [ ] **Step 5: Pre-flight commit-check on every commit**

Loop over every commit in `origin/main..HEAD`. Use the TEMP-FILE form (memory note `feedback_commit_check_preflight` — `/dev/stdin` form fails on Windows Git Bash):

```bash
for sha in $(git log origin/main..HEAD --format=%H); do
  git log -1 --format=%B "$sha" > /tmp/cm.txt
  uv tool run commit-check -m /tmp/cm.txt || echo "FAIL on $sha"
done
rm /tmp/cm.txt
```

Expected: no FAIL lines. Each commit subject:
- Under 72 chars
- Starts with a valid type from `cchk.toml` (feat, fix, docs, test, etc.)
- Description doesn't start with `#`
- Single scope (no commas)

If any commit subject violates, use `git rebase -i origin/main` to reword the offending commit, then re-run the loop.

- [ ] **Step 6: Verify the working tree is clean**

```bash
git status
```

Expected: `On branch chore/65-shell-integration-implementation` (or whatever the implementation branch is named) `working tree clean`.

- [ ] **Step 7: Smoke-check the CLI on Linux/macOS hosts**

```bash
uv run dbxignore install --no-shell-integration
uv run dbxignore uninstall --no-shell-integration
```

Expected: flag accepted silently (no warning about shell integration), daemon install/uninstall behaves as before. The flag IS supposed to be a no-op on non-Windows; if it errors, the silent-accept contract in `install/__init__.py` is broken.

- [ ] **Step 8: Final commit (if any fix-ups landed)**

If Steps 1-7 surfaced any small fix-ups that weren't amended back into their original commits (e.g. a ruff auto-fix in a test file), commit them as `chore: lint cleanups before #65 PR`. Otherwise this step is a no-op.

---

## Notes for the implementing agent

- **Don't ship `windows_shell.py` with the `uninstall_shell_integration` stub from Task 3 still in place.** Task 4 replaces the stub with the real implementation. If you split work across PRs, do not merge an intermediate state where the stub is the user-visible function.
- **PR provenance comments** in `scripts/manual-test-windows.ps1` use `# 5g — ... (PR #NNN)` style. Substitute the actual PR number before merging — this is the project's CLAUDE.md convention for mapping test cases back to PRs (CLAUDE.md "Manual test scripts" section).
- **The `--no-shell-integration` flag is silently accepted on Linux/macOS.** Test #11 (`test_dispatcher_install_skipped_platform_on_non_windows`) verifies the platform branch returns `"skipped-platform"` without invoking the platform module. The CLI plumbing tests on cross-platform legs verify the flag itself is accepted by click without raising.
- **`detect_cli_invocation()` quotes paths defensively.** Some Dropbox installs land at `C:\Program Files\…` (with a space); the quoted form is safe to embed in registry command strings that Explorer parses with `CommandLineToArgvW`. Don't strip the quotes "for tidiness" — they're load-bearing.
- **Throwaway-test-registry isolation** (`isolated_reg_base` fixture in `test_install_windows_shell.py`) is critical. Without it, running the test suite on a developer's machine that already has shell integration installed would overwrite their production keys mid-test. The fixture monkeypatches the module's `_REG_BASE` constant to a `DbxignoreTest\<uuid>` subkey and cleans up on teardown.

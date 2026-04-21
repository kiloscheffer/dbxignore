# dropboxignore v0.2 — Linux support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Linux support to the v0.1 Windows-only dropboxignore — without regressing Windows — by consolidating platform-specific marker I/O behind a single `markers` facade, adding a `user.com.dropbox.ignored` xattr backend, a Linux-aware `roots.discover()`, and a systemd user-unit install path.

**Architecture:** Single new public module `dropboxignore.markers` dispatches at import time to `_backends/windows_ads.py` (current `ads.py` verbatim) or `_backends/linux_xattr.py` (new). `reconcile.py`, `cli.py`, and tests call `markers.{is,set,clear}_ignored` — no other module branches on `sys.platform`. `install.py` becomes an `install/` package with `windows_task.py` / `linux_systemd.py` siblings behind a one-function dispatcher. `roots.py` grows a single platform branch around the `info.json` lookup path (`%APPDATA%\Dropbox\info.json` vs `~/.dropbox/info.json`).

**Tech Stack:** Python ≥ 3.11, `os.setxattr` / `os.getxattr` / `os.removexattr` (Linux), `systemctl --user` (Linux daemon host), existing `watchdog` / `pathspec` / `click` / `psutil` stack. No new dependencies.

**Design doc:** [`docs/superpowers/specs/2026-04-21-dropboxignore-v0.2-linux.md`](../specs/2026-04-21-dropboxignore-v0.2-linux.md)

**Prerequisites:**
- v0.1 is merged to `main` and tagged `v0.1.0` (or equivalent). This plan assumes a clean main checkout.
- Branch for this work: `feature/v0.2-linux`, created from `main`.
- Development machine has access to a Linux environment (native, VPS, WSL2 with systemd, or container with user xattrs). Kilo's Ubuntu VPS is the intended dev target.

**Conventions for this plan:**
- Working directory for every command is the repo root. On Linux: the clone path; on Windows: `C:\Dropbox\git\dropboxignore`. `uv run` is assumed.
- Commits use Conventional Commit prefixes (`feat`, `test`, `refactor`, `ci`, `docs`). Each task produces exactly one commit unless noted.
- Every code block is complete and paste-ready. No elisions for existing code unless a specific line range is called out.
- Tests that require real Linux xattrs carry `@pytest.mark.linux_only` at module scope. Tests that require real NTFS ADS carry `@pytest.mark.windows_only` (existing).

---

## Task 1: Add `linux_only` marker and Linux CI leg

**Goal:** Register the `linux_only` pytest marker and wire the Ubuntu CI leg to run it, so subsequent tasks can add integration tests under that marker without CI complaining about unknown markers.

**Files:**
- Modify: `pyproject.toml` (one line in `[tool.pytest.ini_options]`)
- Modify: `.github/workflows/test.yml` (one new step)

**Steps:**

- [ ] **Step 1: Add the marker to `pyproject.toml`**

Find the `[tool.pytest.ini_options]` block and replace the `markers` list. Current:

```toml
markers = [
    "windows_only: test requires NTFS alternate data streams",
]
```

Replace with:

```toml
markers = [
    "windows_only: test requires NTFS alternate data streams",
    "linux_only: test requires Linux user.* xattrs",
]
```

- [ ] **Step 2: Add the Linux CI step**

Open `.github/workflows/test.yml`. Append this step **after** the existing "Windows-only integration tests" step:

```yaml
      - name: Linux-only integration tests
        if: runner.os == 'Linux'
        run: uv run pytest -m linux_only -v
```

- [ ] **Step 3: Verify the marker is registered locally**

Run: `uv run pytest --markers | grep -E "windows_only|linux_only"`
Expected output contains both lines:
```
@pytest.mark.windows_only: test requires NTFS alternate data streams
@pytest.mark.linux_only: test requires Linux user.* xattrs
```

- [ ] **Step 4: Verify the empty linux_only selection returns 0 tests cleanly (not an error)**

Run: `uv run pytest -m linux_only --collect-only`
Expected: exit code 0, `collected 0 items`. No "unknown marker" warning.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .github/workflows/test.yml
git commit -m "ci: add linux_only pytest marker and Linux CI leg"
```

---

## Task 2: Introduce `markers` facade; move `ads.py` behind `_backends/`

**Goal:** Rename `ads` → `markers` without changing behavior on Windows. After this task, every caller imports `markers`, and the Windows implementation lives at `src/dropboxignore/_backends/windows_ads.py`. No Linux code yet — that's Task 3.

This is a pure refactor. All existing tests (`not windows_only` on every platform; `windows_only` on Windows) must remain green before and after.

**Files:**
- Create: `src/dropboxignore/_backends/__init__.py`
- Create: `src/dropboxignore/_backends/windows_ads.py`
- Create: `src/dropboxignore/markers.py`
- Delete: `src/dropboxignore/ads.py`
- Create: `tests/test_markers_facade.py`
- Modify: `src/dropboxignore/reconcile.py`
- Modify: `src/dropboxignore/cli.py`
- Modify: `tests/conftest.py`
- Rename: `tests/test_ads_integration.py` → `tests/test_windows_ads_integration.py`
- Modify: `tests/test_reconcile_basic.py`, `tests/test_reconcile_edges.py`, `tests/test_reconcile_return_state.py`, `tests/test_daemon_sweep.py`, `tests/test_cli_apply.py`, `tests/test_cli_status_list_explain.py` — each uses the `fake_ads` fixture, rename to `fake_markers`.

**Steps:**

- [ ] **Step 1: Write the facade test (fails because `markers` doesn't exist yet)**

Create `tests/test_markers_facade.py`:

```python
"""The markers facade must re-export three callables, platform-dispatched."""

from __future__ import annotations

import sys

import pytest


def test_markers_exports_three_callables():
    from dropboxignore import markers

    assert callable(markers.is_ignored)
    assert callable(markers.set_ignored)
    assert callable(markers.clear_ignored)


def test_markers_unsupported_platform_raises(monkeypatch):
    # Force a re-import under a fake platform by removing the cached module
    # and patching sys.platform before the import runs.
    monkeypatch.setattr(sys, "platform", "sunos5")
    monkeypatch.delitem(sys.modules, "dropboxignore.markers", raising=False)

    from dropboxignore import markers

    with pytest.raises(NotImplementedError, match="sunos5"):
        markers.is_ignored("/whatever")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_markers_facade.py -v`
Expected: both tests FAIL with `ModuleNotFoundError: No module named 'dropboxignore.markers'`.

- [ ] **Step 3: Create the `_backends` package**

Create `src/dropboxignore/_backends/__init__.py` (empty file):

```python
```

Create `src/dropboxignore/_backends/windows_ads.py` with the **exact content** of the current `src/dropboxignore/ads.py` — no edits. For completeness:

```python
"""Read/write the Dropbox 'ignore' NTFS alternate data stream.

Dropbox treats a file or directory as ignored if it has an NTFS alternate
data stream named ``com.dropbox.ignored`` containing any non-empty value.
This module exposes three operations via Python's built-in ``open()``,
which on Windows passes the ``path:streamname`` syntax through to
``CreateFileW`` at the kernel level — so no subprocess or pywin32 needed.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

STREAM_NAME = "com.dropbox.ignored"
_MARKER_VALUE = "1"
_LONG_PATH_PREFIX = "\\\\?\\"


def _stream_path(path: Path) -> str:
    """Return the ``\\\\?\\…:streamname`` path for ``path``.

    ``path`` must be absolute — the ``\\\\?\\`` long-path prefix is only
    meaningful before a full path. Callers normalize at the CLI/daemon
    boundary; relative paths here are a caller bug.
    """
    if not path.is_absolute():
        raise ValueError(f"markers requires an absolute path; got {path!r}")
    return f"{_LONG_PATH_PREFIX}{path}:{STREAM_NAME}"


def is_ignored(path: Path) -> bool:
    """Return True if ``path`` bears a non-empty com.dropbox.ignored stream."""
    try:
        with open(_stream_path(path), encoding="ascii") as f:
            return bool(f.read(1))
    except FileNotFoundError:
        return False


def set_ignored(path: Path) -> None:
    """Mark ``path`` as ignored by Dropbox.

    Raises ``FileNotFoundError`` if ``path`` vanished before the write;
    raises ``PermissionError`` if the stream cannot be written. Callers
    (notably ``reconcile_subtree``) catch and log both per the design's
    failure-mode contract.
    """
    with open(_stream_path(path), "w", encoding="ascii") as f:
        f.write(_MARKER_VALUE)


def clear_ignored(path: Path) -> None:
    """Remove the Dropbox ignore marker from ``path`` (no-op if absent)."""
    try:
        os.remove(_stream_path(path))
    except FileNotFoundError:
        logger.debug("clear_ignored: stream absent or path gone: %s", path)
```

Note the only wording change from the original: the `ValueError` message now says `markers requires an absolute path`, not `ads requires`. This matches the new public-facing name.

- [ ] **Step 4: Create `markers.py` with the platform dispatch**

Create `src/dropboxignore/markers.py`:

```python
"""Platform-dispatched ignore-marker API.

Every module that needs to read or write the Dropbox ignore marker
imports this module. The concrete implementation is chosen at import
time based on ``sys.platform``.
"""
from __future__ import annotations

import sys

if sys.platform == "win32":
    from dropboxignore._backends.windows_ads import (
        clear_ignored,
        is_ignored,
        set_ignored,
    )
elif sys.platform.startswith("linux"):
    from dropboxignore._backends.linux_xattr import (
        clear_ignored,
        is_ignored,
        set_ignored,
    )
else:
    def _unsupported(*_args, **_kwargs):
        raise NotImplementedError(
            f"dropboxignore has no ignore-marker backend for platform "
            f"{sys.platform!r}; supported: 'win32', 'linux'. "
            "macOS support is planned for v0.3."
        )
    is_ignored = set_ignored = clear_ignored = _unsupported

__all__ = ["is_ignored", "set_ignored", "clear_ignored"]
```

Note: this references `dropboxignore._backends.linux_xattr`, which doesn't exist yet. On a Windows machine, that import is never reached — the `if sys.platform == "win32"` branch wins. On Linux, this module will raise `ImportError` until Task 3 lands. That's fine: this task's tests run under `not windows_only` on Windows and under `not linux_only` (with `test_markers_unsupported_platform_raises` monkeypatching to `sunos5`, which triggers the `else` branch without importing `linux_xattr`) on Linux.

**On a Linux development machine, expect Task 2's suite to fail at `from dropboxignore import markers` until Task 3 creates `_backends/linux_xattr.py`.** If you're sequencing tasks, run Task 2 + Task 3 as a pair before running the suite, OR add a `pytest.importorskip("dropboxignore._backends.linux_xattr")` skip in `tests/conftest.py` temporarily. Simplest path: execute Task 2 on Windows, Task 3 on Linux, then sync.

- [ ] **Step 5: Delete `src/dropboxignore/ads.py`**

```bash
git rm src/dropboxignore/ads.py
```

- [ ] **Step 6: Update `src/dropboxignore/reconcile.py` imports**

Current import block (line 11–12):
```python
from dropboxignore import ads
from dropboxignore.rules import IGNORE_FILENAME, RuleCache
```

Replace with:
```python
from dropboxignore import markers
from dropboxignore.rules import IGNORE_FILENAME, RuleCache
```

Then replace every `ads.` call site in `_reconcile_path` (there are three: `ads.is_ignored`, `ads.set_ignored`, `ads.clear_ignored`) with `markers.`. After this step, `grep -n "ads\." src/dropboxignore/reconcile.py` should return nothing.

- [ ] **Step 7: Update `src/dropboxignore/cli.py` imports**

Current (line 12):
```python
from dropboxignore import ads, reconcile, roots, state
```

Replace with:
```python
from dropboxignore import markers, reconcile, roots, state
```

Then replace every `ads.` call site. There are exactly five: three in `list_ignored` (two in the dir loop, one in the files loop — `ads.is_ignored`), two in `uninstall --purge` (`ads.is_ignored`, `ads.clear_ignored`). Replace each with `markers.`.

After this step: `grep -n "ads\." src/dropboxignore/cli.py` should return nothing (and the `ads` import must be gone).

- [ ] **Step 8: Update `tests/conftest.py`**

Replace the entire file with:

```python
"""Shared fixtures and helpers for the dropboxignore test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from dropboxignore import cli, reconcile


class FakeMarkers:
    """In-memory stand-in for the ``markers`` module."""

    def __init__(self) -> None:
        self._ignored: set[Path] = set()
        self.set_calls: list[Path] = []
        self.clear_calls: list[Path] = []

    def is_ignored(self, path: Path) -> bool:
        return path.resolve() in self._ignored

    def set_ignored(self, path: Path) -> None:
        p = path.resolve()
        self._ignored.add(p)
        self.set_calls.append(p)

    def clear_ignored(self, path: Path) -> None:
        p = path.resolve()
        self._ignored.discard(p)
        self.clear_calls.append(p)


@pytest.fixture
def fake_markers(monkeypatch):
    """Replace ``markers`` in both ``reconcile`` and ``cli`` with a shared FakeMarkers."""
    fake = FakeMarkers()
    monkeypatch.setattr(reconcile, "markers", fake)
    monkeypatch.setattr(cli, "markers", fake)
    return fake


@pytest.fixture
def write_file():
    """Write a file, creating parent dirs; returns a callable ``(path, content="")``."""
    def _write(path: Path, content: str = "") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path
    return _write
```

- [ ] **Step 9: Rename the fixture in every consumer test file**

For each of these six files:
- `tests/test_reconcile_basic.py`
- `tests/test_reconcile_edges.py`
- `tests/test_reconcile_return_state.py`
- `tests/test_daemon_sweep.py`
- `tests/test_cli_apply.py`
- `tests/test_cli_status_list_explain.py`

Do a whole-file find/replace: `fake_ads` → `fake_markers`. Ruff will catch any missed uses at the next `uv run ruff check`.

Verify: `grep -rn "fake_ads" tests/` should return zero matches.

- [ ] **Step 10: Rename `tests/test_ads_integration.py` → `tests/test_windows_ads_integration.py`**

```bash
git mv tests/test_ads_integration.py tests/test_windows_ads_integration.py
```

Then open the renamed file and change its import. Current line 4:
```python
from dropboxignore import ads
```

Replace with:
```python
from dropboxignore import markers
```

Then replace every `ads.` call with `markers.` across the four existing tests (roundtrip on file, roundtrip on directory, long-path-over-260, idempotent clear). Trust the grep below over any count you mentally make.

Verify: `grep -n "ads\." tests/test_windows_ads_integration.py` returns nothing, and `grep -n "markers\." tests/test_windows_ads_integration.py` returns a call on every test case.

- [ ] **Step 11: Run the full suite on Windows**

Run: `uv run pytest -v`
Expected: every test that passed before Task 2 passes after. Zero new failures. Zero skipped tests that weren't skipped before. `tests/test_markers_facade.py` is the only new file — both of its tests pass.

If Linux-side testing is desired before Task 3: `uv run pytest -m "not linux_only" -v` will still fail on the `from dropboxignore import markers` import because the Linux branch tries to import a non-existent `_backends/linux_xattr`. Defer full Linux-side testing until Task 3 is done.

- [ ] **Step 12: Run ruff**

Run: `uv run ruff check`
Expected: no violations. If ruff flags an unused import in `cli.py` or `reconcile.py` (leftover `ads`), remove it.

- [ ] **Step 13: Commit**

```bash
git add src/dropboxignore/ tests/ 
git commit -m "refactor: rename ads → markers facade; move Windows impl to _backends/

Introduces src/dropboxignore/markers.py as the platform-dispatched public
API for ignore-marker I/O. The Windows NTFS ADS implementation moves
verbatim to src/dropboxignore/_backends/windows_ads.py. No behavior
change on Windows. Prepares for the Linux xattr backend (Task 3)."
```

---

## Task 3: Add the Linux xattr backend

**Goal:** Implement `_backends/linux_xattr.py` so `markers.is_ignored` / `set_ignored` / `clear_ignored` work on Linux against real files with real xattrs. After this task, a full `uv run pytest -v` on Linux passes.

**Files:**
- Create: `src/dropboxignore/_backends/linux_xattr.py`
- Create: `tests/test_linux_xattr_integration.py`

**Steps:**

- [ ] **Step 1: Write the failing integration tests**

Create `tests/test_linux_xattr_integration.py`:

```python
"""Integration tests for the Linux user.com.dropbox.ignored xattr backend."""

from __future__ import annotations

import os
import sys

import pytest

pytestmark = pytest.mark.linux_only

if not sys.platform.startswith("linux"):
    pytest.skip("user.* xattrs are Linux-only in v0.2", allow_module_level=True)

from dropboxignore._backends import linux_xattr


def _xattr_supported(path) -> bool:
    """Probe whether the filesystem under `path` accepts user.* xattrs."""
    probe = path / ".xattr_probe"
    probe.touch()
    try:
        os.setxattr(os.fspath(probe), "user.dropboxignore.probe", b"1")
    except OSError:
        return False
    finally:
        probe.unlink(missing_ok=True)
    return True


@pytest.fixture(autouse=True)
def _require_xattr_fs(tmp_path):
    if not _xattr_supported(tmp_path):
        pytest.skip(f"tmp_path {tmp_path} rejects user.* xattrs — cannot test")


def test_roundtrip_on_file(tmp_path):
    p = tmp_path / "file.txt"
    p.touch()
    assert linux_xattr.is_ignored(p) is False
    linux_xattr.set_ignored(p)
    assert linux_xattr.is_ignored(p) is True
    linux_xattr.clear_ignored(p)
    assert linux_xattr.is_ignored(p) is False


def test_roundtrip_on_directory(tmp_path):
    d = tmp_path / "subdir"
    d.mkdir()
    assert linux_xattr.is_ignored(d) is False
    linux_xattr.set_ignored(d)
    assert linux_xattr.is_ignored(d) is True
    linux_xattr.clear_ignored(d)
    assert linux_xattr.is_ignored(d) is False


def test_clear_is_idempotent_on_unmarked_path(tmp_path):
    p = tmp_path / "unmarked.txt"
    p.touch()
    linux_xattr.clear_ignored(p)
    assert linux_xattr.is_ignored(p) is False


def test_is_ignored_on_nonexistent_path_raises_filenotfound(tmp_path):
    p = tmp_path / "does-not-exist.txt"
    with pytest.raises(FileNotFoundError):
        linux_xattr.is_ignored(p)


def test_requires_absolute_path(tmp_path):
    from pathlib import Path
    rel = Path("relative/path.txt")
    with pytest.raises(ValueError, match="absolute"):
        linux_xattr.is_ignored(rel)
    with pytest.raises(ValueError, match="absolute"):
        linux_xattr.set_ignored(rel)
    with pytest.raises(ValueError, match="absolute"):
        linux_xattr.clear_ignored(rel)


def test_symlink_marked_not_target(tmp_path):
    target = tmp_path / "target.txt"
    target.touch()
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    linux_xattr.set_ignored(link)

    # link itself is ignored; target is not
    assert linux_xattr.is_ignored(link) is True
    assert linux_xattr.is_ignored(target) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (on Linux): `uv run pytest tests/test_linux_xattr_integration.py -v`
Expected: every test FAILS or ERRORS — `ModuleNotFoundError: No module named 'dropboxignore._backends.linux_xattr'`.

- [ ] **Step 3: Implement the backend**

Create `src/dropboxignore/_backends/linux_xattr.py`:

```python
"""Read/write the Dropbox 'ignore' user-namespace xattr on Linux.

Dropbox on Linux treats a path as ignored if it carries the extended
attribute ``user.com.dropbox.ignored`` with any non-empty value.
This module uses ``os.setxattr`` / ``getxattr`` / ``removexattr`` with
``follow_symlinks=False`` so a symlink path is marked on the link
itself — mirroring the ``os.walk(followlinks=False)`` walk discipline
in ``reconcile_subtree``.
"""
from __future__ import annotations

import errno
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

ATTR_NAME = "user.com.dropbox.ignored"
_MARKER_VALUE = b"1"

# errno.ENOATTR is a BSD-ism. On Linux it equals errno.ENODATA (61).
# Accept both defensively — Python exposes both on all platforms.
_NO_ATTR_ERRNOS = {errno.ENODATA, getattr(errno, "ENOATTR", errno.ENODATA)}


def _require_absolute(path: Path) -> None:
    if not path.is_absolute():
        raise ValueError(f"markers requires an absolute path; got {path!r}")


def is_ignored(path: Path) -> bool:
    _require_absolute(path)
    try:
        value = os.getxattr(os.fspath(path), ATTR_NAME, follow_symlinks=False)
    except OSError as exc:
        if exc.errno in _NO_ATTR_ERRNOS:
            return False
        if exc.errno == errno.ENOENT:
            raise FileNotFoundError(str(path)) from exc
        raise
    return bool(value)


def set_ignored(path: Path) -> None:
    _require_absolute(path)
    os.setxattr(os.fspath(path), ATTR_NAME, _MARKER_VALUE, follow_symlinks=False)


def clear_ignored(path: Path) -> None:
    _require_absolute(path)
    try:
        os.removexattr(os.fspath(path), ATTR_NAME, follow_symlinks=False)
    except OSError as exc:
        if exc.errno in _NO_ATTR_ERRNOS:
            logger.debug("clear_ignored: xattr absent on %s", path)
            return
        if exc.errno == errno.ENOENT:
            logger.debug("clear_ignored: path gone: %s", path)
            return
        raise
```

- [ ] **Step 4: Run the integration tests to verify they pass**

Run (on Linux): `uv run pytest tests/test_linux_xattr_integration.py -v`
Expected: all six tests PASS. If the underlying filesystem of `tmp_path` doesn't support `user.*` xattrs, the autouse fixture skips each test with a clear message — investigate the mount (`stat -f -c %T tmp_path`) rather than weakening the backend.

- [ ] **Step 5: Run the full suite on Linux**

Run: `uv run pytest -v`
Expected: every test passes. `windows_only` tests skip cleanly. `linux_only` tests (six of them, all new) pass. All prior portable tests still pass.

- [ ] **Step 6: Run the full suite on Windows (smoke-test the cross-platform import)**

Run (on a Windows machine): `uv run pytest -v`
Expected: every Windows test still passes. `linux_only` tests skip cleanly. The `from dropboxignore import markers` path takes the `win32` branch and never imports `linux_xattr`, so no regression from this task.

- [ ] **Step 7: Run ruff**

Run: `uv run ruff check`
Expected: no violations.

- [ ] **Step 8: Commit**

```bash
git add src/dropboxignore/_backends/linux_xattr.py tests/test_linux_xattr_integration.py
git commit -m "feat: add Linux user.com.dropbox.ignored xattr backend

Implements set/get/remove of the Dropbox ignore marker on Linux via
os.setxattr with follow_symlinks=False. ENODATA (attribute absent) is
mapped to is_ignored→False and a no-op clear; ENOENT (path gone) maps
to FileNotFoundError on read, debug-log no-op on clear. Integration
tests run behind @pytest.mark.linux_only."
```

---

## Task 4: Handle `ENOTSUP` in `_reconcile_path`

**Goal:** A path on a filesystem that doesn't support user xattrs (tmpfs without `user_xattr`, vfat, some FUSE, some NFS/SMB) raises `OSError(errno.ENOTSUP)` from `markers.set_ignored` / `clear_ignored`. Without this task, such a path crashes the sweep. With it, the offending path logs a `WARNING`, gets appended to `Report.errors`, and the sweep continues — mirroring the existing `PermissionError` scope partitioning.

**Files:**
- Modify: `src/dropboxignore/reconcile.py`
- Create: `tests/test_reconcile_enotsup.py`

**Steps:**

- [ ] **Step 1: Write the failing test**

Create `tests/test_reconcile_enotsup.py`:

```python
"""Reconcile must log + skip paths on filesystems that reject the ignore marker."""

from __future__ import annotations

import errno
import logging
from pathlib import Path

from dropboxignore import reconcile
from dropboxignore.rules import RuleCache


def _raise_enotsup(*_args, **_kwargs):
    raise OSError(errno.ENOTSUP, "Operation not supported")


def test_enotsup_on_set_is_reported_not_raised(
    fake_markers, tmp_path, write_file, monkeypatch, caplog
):
    root = tmp_path
    write_file(root / ".dropboxignore", "ignoreme.txt\n")
    target = write_file(root / "ignoreme.txt")

    # fake_markers starts clean; override set_ignored to raise ENOTSUP.
    monkeypatch.setattr(fake_markers, "set_ignored", _raise_enotsup)

    cache = RuleCache()
    cache.load_root(root)

    with caplog.at_level(logging.WARNING, logger="dropboxignore.reconcile"):
        report = reconcile.reconcile_subtree(root, root, cache)

    assert report.marked == 0
    assert len(report.errors) == 1
    errored_path, message = report.errors[0]
    assert errored_path.resolve() == target.resolve()
    assert "unsupported" in message.lower()
    assert any("does not support ignore markers" in r.message for r in caplog.records)


def test_enotsup_on_clear_is_reported_not_raised(
    fake_markers, tmp_path, write_file, monkeypatch, caplog
):
    root = tmp_path
    # Pre-mark a path that no rule covers, so reconcile would clear it.
    target = write_file(root / "manually_marked.txt")
    fake_markers.set_ignored(target)
    # Sanity: no rules → reconcile will try to clear.
    (root / ".dropboxignore").write_text("", encoding="utf-8")

    monkeypatch.setattr(fake_markers, "clear_ignored", _raise_enotsup)

    cache = RuleCache()
    cache.load_root(root)

    with caplog.at_level(logging.WARNING, logger="dropboxignore.reconcile"):
        report = reconcile.reconcile_subtree(root, root, cache)

    assert report.cleared == 0
    assert any(p.resolve() == target.resolve() for p, _ in report.errors)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_reconcile_enotsup.py -v`
Expected: both tests FAIL — the `OSError(ENOTSUP)` propagates out of `reconcile_subtree` and is not caught.

- [ ] **Step 3: Add the handler in `_reconcile_path`**

Open `src/dropboxignore/reconcile.py`. At the top of the file, add:

```python
import errno
```

(Place it in alphabetical order with the other stdlib imports.)

In `_reconcile_path`, find the existing `except PermissionError` block that wraps the `markers.set_ignored` / `markers.clear_ignored` calls (lines 72–93 in the current file). Immediately **after** that `except PermissionError` arm, add a new arm:

```python
    except OSError as exc:
        if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            logger.warning(
                "Filesystem does not support ignore markers on %s: %s", path, exc
            )
            report.errors.append((path, f"unsupported: {exc}"))
            return None
        raise
```

The resulting block looks like:

```python
    try:
        if should_ignore and not currently_ignored:
            markers.set_ignored(path)
            report.marked += 1
            return True
        if currently_ignored and not should_ignore:
            if path.name == IGNORE_FILENAME:
                logger.warning(
                    ".dropboxignore at %s was marked ignored; overriding back to synced",
                    path,
                )
            markers.clear_ignored(path)
            report.cleared += 1
            return False
    except FileNotFoundError:
        logger.debug("Path vanished before ADS write: %s", path)
        return None
    except PermissionError as exc:
        logger.warning("Permission denied writing ADS on %s: %s", path, exc)
        report.errors.append((path, f"write: {exc}"))
        return currently_ignored
    except OSError as exc:
        if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            logger.warning(
                "Filesystem does not support ignore markers on %s: %s", path, exc
            )
            report.errors.append((path, f"unsupported: {exc}"))
            return None
        raise
```

Note: the `PermissionError` arm catches a subclass of `OSError`, so it must come first. The generic `except OSError` catches the rest. The final `raise` re-raises any `OSError` that is not `ENOTSUP`/`EOPNOTSUPP` — those are unexpected and should propagate.

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_reconcile_enotsup.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: every test still passes, including all existing reconcile tests (the new `except OSError` arm sits at the end; `FileNotFoundError` and `PermissionError` still take precedence where they fire).

- [ ] **Step 6: Run ruff**

Run: `uv run ruff check`
Expected: no violations.

- [ ] **Step 7: Commit**

```bash
git add src/dropboxignore/reconcile.py tests/test_reconcile_enotsup.py
git commit -m "feat: log-and-skip ENOTSUP / EOPNOTSUPP in reconcile

On Linux, a file on a filesystem that rejects user.* xattrs
(tmpfs without user_xattr, vfat, some FUSE mounts) raises OSError
with errno ENOTSUP/EOPNOTSUPP. Classify both in _reconcile_path,
log at WARNING, append to Report.errors, and continue the sweep —
same scope partitioning as PermissionError."
```

---

## Task 5: Linux-aware root discovery

**Goal:** `roots.discover()` finds the Dropbox `info.json` at `~/.dropbox/info.json` on Linux instead of `%APPDATA%\Dropbox\info.json`. Existing Windows behavior preserved. Existing `tests/test_roots.py` generalized to run the same behavioral assertions on both platforms.

**Files:**
- Modify: `src/dropboxignore/roots.py`
- Modify: `tests/test_roots.py`

**Steps:**

- [ ] **Step 1: Update `tests/test_roots.py` to cover both platforms**

Replace the entire file with:

```python
import sys
from pathlib import Path

from dropboxignore import roots

FIXTURES = Path(__file__).parent / "fixtures"


def _stage_info(monkeypatch, tmp_path, fixture_name: str | None):
    """Stage a fake Dropbox info.json at the platform's documented location."""
    if sys.platform == "win32":
        base = tmp_path / "AppData"
        dropbox_dir = base / "Dropbox"
        env_var = "APPDATA"
    elif sys.platform.startswith("linux"):
        base = tmp_path / "home"
        dropbox_dir = base / ".dropbox"
        env_var = "HOME"
    else:
        import pytest
        pytest.skip(f"unsupported platform {sys.platform}")

    dropbox_dir.mkdir(parents=True)
    if fixture_name is not None:
        content = (FIXTURES / fixture_name).read_text(encoding="utf-8")
        (dropbox_dir / "info.json").write_text(content, encoding="utf-8")
    monkeypatch.setenv(env_var, str(base))


def _clear_platform_env(monkeypatch):
    if sys.platform == "win32":
        monkeypatch.delenv("APPDATA", raising=False)
    elif sys.platform.startswith("linux"):
        monkeypatch.delenv("HOME", raising=False)


def test_discover_personal_only(monkeypatch, tmp_path):
    _stage_info(monkeypatch, tmp_path, "info_personal.json")
    result = roots.discover()
    assert result == [Path(r"C:\Dropbox")]


def test_discover_personal_and_business(monkeypatch, tmp_path):
    _stage_info(monkeypatch, tmp_path, "info_personal_business.json")
    result = roots.discover()
    assert result == [Path(r"C:\Dropbox"), Path(r"C:\Dropbox (Work)")]


def test_discover_missing_info_file(monkeypatch, tmp_path):
    _stage_info(monkeypatch, tmp_path, fixture_name=None)
    assert roots.discover() == []


def test_discover_malformed_json(monkeypatch, tmp_path):
    _stage_info(monkeypatch, tmp_path, "info_malformed.json")
    assert roots.discover() == []


def test_discover_no_platform_env(monkeypatch):
    _clear_platform_env(monkeypatch)
    assert roots.discover() == []


def test_discover_json_not_object(monkeypatch, tmp_path):
    _stage_info(monkeypatch, tmp_path, "info_not_object.json")
    assert roots.discover() == []


def test_discover_non_utf8_bytes(monkeypatch, tmp_path):
    if sys.platform == "win32":
        base = tmp_path / "AppData"
        dropbox_dir = base / "Dropbox"
        env_var = "APPDATA"
    else:
        base = tmp_path / "home"
        dropbox_dir = base / ".dropbox"
        env_var = "HOME"
    dropbox_dir.mkdir(parents=True)
    (dropbox_dir / "info.json").write_bytes(b'{"personal": {"path": "C:\\\\Dr\xf6pbox"}}')
    monkeypatch.setenv(env_var, str(base))
    assert roots.discover() == []
```

Note: the path content of the fixture JSONs (`C:\Dropbox` etc.) is treated as an opaque string by `Path()` on Linux (produces a `PosixPath` whose single segment contains a backslash). The equality assertions hold identically on both platforms; we're testing the **plumbing**, not that the paths are real OS paths.

- [ ] **Step 2: Run the modified tests (both fail on Linux, most still pass on Windows)**

Run (on Linux): `uv run pytest tests/test_roots.py -v`
Expected: the first two tests, and `test_discover_missing_info_file` / `test_discover_malformed_json` / `test_discover_json_not_object` / `test_discover_non_utf8_bytes` all FAIL because `discover()` currently only looks at `APPDATA`. `test_discover_no_platform_env` passes (unset env → `[]`).

Run (on Windows): the suite still passes — the refactored helper resolves to the same `APPDATA` path.

- [ ] **Step 3: Update `src/dropboxignore/roots.py`**

Replace the entire file with:

```python
"""Discover configured Dropbox root paths from Dropbox's own info.json."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_ACCOUNT_TYPES = ("personal", "business")


def find_containing(path: Path, roots: list[Path]) -> Path | None:
    """Return the first root that contains ``path``, or ``None`` if none do."""
    for root in roots:
        try:
            path.relative_to(root)
            return root
        except ValueError:
            continue
    return None


def _info_json_path() -> Path | None:
    """Return the platform's Dropbox info.json location, or None if unknown."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            logger.warning("APPDATA not set; cannot locate Dropbox info.json")
            return None
        return Path(appdata) / "Dropbox" / "info.json"
    if sys.platform.startswith("linux"):
        home = os.environ.get("HOME")
        if not home:
            logger.warning("HOME not set; cannot locate Dropbox info.json")
            return None
        return Path(home) / ".dropbox" / "info.json"
    logger.warning("Unsupported platform %s; cannot locate Dropbox info.json", sys.platform)
    return None


def discover() -> list[Path]:
    info_path = _info_json_path()
    if info_path is None:
        return []

    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("Dropbox info.json not found at %s", info_path)
        return []
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Cannot read Dropbox info.json at %s: %s", info_path, exc)
        return []

    if not isinstance(data, dict):
        logger.warning(
            "Unexpected Dropbox info.json structure at %s (top-level is not an object)", info_path
        )
        return []

    roots: list[Path] = []
    for account_type in _ACCOUNT_TYPES:
        account = data.get(account_type)
        if isinstance(account, dict) and isinstance(account.get("path"), str):
            roots.append(Path(account["path"]))
    return roots
```

- [ ] **Step 4: Run the tests to verify they pass**

Run (on Linux): `uv run pytest tests/test_roots.py -v`
Expected: all seven tests PASS.

Run (on Windows): `uv run pytest tests/test_roots.py -v`
Expected: all seven tests PASS. (Behavior on Windows is byte-for-byte unchanged — `discover()` still reads `APPDATA`.)

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: every test passes on both platforms.

- [ ] **Step 6: Run ruff**

Run: `uv run ruff check`
Expected: no violations.

- [ ] **Step 7: Commit**

```bash
git add src/dropboxignore/roots.py tests/test_roots.py
git commit -m "feat: discover Dropbox info.json at ~/.dropbox/info.json on Linux

Adds _info_json_path() with a sys.platform branch: %APPDATA%\\Dropbox
on Windows (unchanged), ~/.dropbox on Linux. tests/test_roots.py
generalized via a staging helper that writes to the platform's
documented location and sets the corresponding env var."
```

---

## Task 6: Split `install.py` into a package; add systemd unit generator

**Goal:** `cli.install` on Linux creates and enables a systemd user unit at `~/.config/systemd/user/dropboxignore.service`; `cli.uninstall` disables and removes it. Windows behavior unchanged.

**Files:**
- Delete: `src/dropboxignore/install.py`
- Create: `src/dropboxignore/install/__init__.py`
- Create: `src/dropboxignore/install/windows_task.py`
- Create: `src/dropboxignore/install/linux_systemd.py`
- Modify: `src/dropboxignore/cli.py` (`install` and `uninstall` commands)
- Create: `tests/test_linux_systemd.py`
- Verify: `tests/test_install.py` (if present) — retarget imports.

**Steps:**

- [ ] **Step 1: Check whether existing install tests exist**

Run: `ls tests/ | grep install`
If `tests/test_install.py` exists, read it — its imports are about to change (`from dropboxignore import install` → `from dropboxignore.install import windows_task`). If it doesn't, skip the retarget subtask.

- [ ] **Step 2: Write failing systemd tests**

Create `tests/test_linux_systemd.py`:

```python
"""Unit tests for the Linux systemd-user-unit install/uninstall backend.

Mocks all subprocess calls and the filesystem write. No real systemd
required, so this is a pure unit test running under ``not linux_only``
on every OS — the logic is pure-Python string manipulation + subprocess
argument assembly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def test_unit_file_content_has_exec_start_and_wanted_by(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "dropboxignore.install.linux_systemd._detect_invocation",
        lambda: (Path("/usr/local/bin/dropboxignored"), ""),
    )

    from dropboxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(Path("/usr/local/bin/dropboxignored"), "")
    assert "ExecStart=/usr/local/bin/dropboxignored" in content
    assert "Restart=on-failure" in content
    assert "WantedBy=default.target" in content


def test_unit_file_content_appends_arguments(tmp_path):
    from dropboxignore.install import linux_systemd

    content = linux_systemd.build_unit_content(
        Path("/home/u/.local/bin/python"),
        "-m dropboxignore daemon",
    )
    assert (
        "ExecStart=/home/u/.local/bin/python -m dropboxignore daemon" in content
    )


def test_install_writes_unit_and_invokes_systemctl(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "dropboxignore.install.linux_systemd._detect_invocation",
        lambda: (Path("/usr/local/bin/dropboxignored"), ""),
    )

    calls: list[list[str]] = []

    def fake_run(cmd, check, capture_output=False, text=False):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    from dropboxignore.install import linux_systemd

    linux_systemd.install_unit()

    unit_path = tmp_path / ".config" / "systemd" / "user" / "dropboxignore.service"
    assert unit_path.exists()
    assert "ExecStart=/usr/local/bin/dropboxignored" in unit_path.read_text()

    assert calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "dropboxignore.service"],
    ]


def test_uninstall_disables_removes_unit_and_reloads(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    unit_path = tmp_path / ".config" / "systemd" / "user" / "dropboxignore.service"
    unit_path.parent.mkdir(parents=True)
    unit_path.write_text("[Unit]\nDescription=stub\n")

    calls: list[list[str]] = []

    def fake_run(cmd, check=False, capture_output=False, text=False):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    from dropboxignore.install import linux_systemd

    linux_systemd.uninstall_unit()

    assert not unit_path.exists()
    assert calls == [
        ["systemctl", "--user", "disable", "--now", "dropboxignore.service"],
        ["systemctl", "--user", "daemon-reload"],
    ]


def test_install_raises_when_executable_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    def _raise_not_found():
        raise RuntimeError("dropboxignored not on PATH; run `uv tool install .`")

    monkeypatch.setattr(
        "dropboxignore.install.linux_systemd._detect_invocation",
        _raise_not_found,
    )

    from dropboxignore.install import linux_systemd

    with pytest.raises(RuntimeError, match="dropboxignored not on PATH"):
        linux_systemd.install_unit()
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_linux_systemd.py -v`
Expected: every test FAILS — `ModuleNotFoundError: No module named 'dropboxignore.install'` (the current `install.py` is a module, not a package, so `dropboxignore.install.linux_systemd` doesn't resolve).

- [ ] **Step 4: Create the install package scaffolding**

```bash
git rm src/dropboxignore/install.py
mkdir -p src/dropboxignore/install
```

Create `src/dropboxignore/install/__init__.py`:

```python
"""Platform-dispatched install/uninstall for the dropboxignore daemon."""

from __future__ import annotations

import sys


def install_service() -> None:
    if sys.platform == "win32":
        from dropboxignore.install.windows_task import install_task
        install_task()
    elif sys.platform.startswith("linux"):
        from dropboxignore.install.linux_systemd import install_unit
        install_unit()
    else:
        raise NotImplementedError(
            f"install: no backend for platform {sys.platform!r}; "
            "supported: 'win32', 'linux'"
        )


def uninstall_service() -> None:
    if sys.platform == "win32":
        from dropboxignore.install.windows_task import uninstall_task
        uninstall_task()
    elif sys.platform.startswith("linux"):
        from dropboxignore.install.linux_systemd import uninstall_unit
        uninstall_unit()
    else:
        raise NotImplementedError(
            f"uninstall: no backend for platform {sys.platform!r}; "
            "supported: 'win32', 'linux'"
        )
```

- [ ] **Step 5: Move the Windows implementation**

Create `src/dropboxignore/install/windows_task.py` with the **exact content** of the old `src/dropboxignore/install.py`, except:
- Update the module docstring to say "Windows Task Scheduler entry" (unchanged from original).
- Public function names stay `install_task`, `uninstall_task`, `build_task_xml`, `detect_invocation`, `TASK_NAME`.

Paste the current `install.py` content verbatim (it's already correct; we're just relocating it):

```python
"""Generate and install the Windows Task Scheduler entry for the daemon."""

from __future__ import annotations

import getpass
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

TASK_NAME = "dropboxignore"


def detect_invocation() -> tuple[Path, str]:
    """Return (executable, arguments) to run the daemon in the current install."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable), ""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return pythonw, "-m dropboxignore daemon"


def build_task_xml(exe_path: Path, arguments: str = "") -> str:
    """Return a Task Scheduler v1.2 XML document for a logon-trigger daemon."""
    user = getpass.getuser()
    args_element = f"<Arguments>{arguments}</Arguments>" if arguments else ""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>dropboxignore daemon: sync com.dropbox.ignored with .dropboxignore</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{exe_path}</Command>
      {args_element}
    </Exec>
  </Actions>
</Task>
"""


def install_task() -> None:
    exe, args = detect_invocation()
    xml = build_task_xml(exe, args)
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as tmp:
        tmp.write(xml)
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(  # noqa: S603 — hardcoded args, no user data
            ["schtasks", "/Create", "/XML", str(tmp_path), "/TN", TASK_NAME, "/F"],
            check=True,
        )
        logger.info("Installed scheduled task %s", TASK_NAME)
    finally:
        tmp_path.unlink(missing_ok=True)


def uninstall_task() -> None:
    """Remove the Task Scheduler entry; raises RuntimeError if schtasks fails."""
    result = subprocess.run(  # noqa: S603 — hardcoded args, no user data
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"schtasks /Delete returned {result.returncode}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    logger.info("Uninstalled scheduled task %s", TASK_NAME)
```

- [ ] **Step 6: Create the Linux systemd implementation**

Create `src/dropboxignore/install/linux_systemd.py`:

```python
"""Generate and install a systemd user unit for the daemon on Linux."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

UNIT_NAME = "dropboxignore.service"


def _unit_path() -> Path:
    """Return ``~/.config/systemd/user/dropboxignore.service``."""
    import os
    home = os.environ.get("HOME")
    if not home:
        raise RuntimeError("HOME not set; cannot locate systemd user unit directory")
    return Path(home) / ".config" / "systemd" / "user" / UNIT_NAME


def _detect_invocation() -> tuple[Path, str]:
    """Return (executable, arguments) to run the daemon in the current install."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable), ""
    # uv tool install places a `dropboxignored` shim on PATH.
    exe = shutil.which("dropboxignored")
    if exe:
        return Path(exe), ""
    # Fallback: the current Python + `-m dropboxignore daemon`.
    python = shutil.which("python3") or sys.executable
    if not python:
        raise RuntimeError(
            "dropboxignored not on PATH and no python3 found; "
            "run `uv tool install .` from the dropboxignore checkout first"
        )
    return Path(python), "-m dropboxignore daemon"


def build_unit_content(exe_path: Path, arguments: str = "") -> str:
    """Return the full [Unit]/[Service]/[Install] text for the systemd user unit."""
    exec_start = f"{exe_path} {arguments}".strip()
    return f"""[Unit]
Description=dropboxignore daemon
Documentation=https://github.com/kiloscheffer/dropboxignore
After=default.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=60s

[Install]
WantedBy=default.target
"""


def install_unit() -> None:
    exe, args = _detect_invocation()
    content = build_unit_content(exe, args)
    path = _unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info("Wrote systemd user unit to %s", path)

    subprocess.run(  # noqa: S603 — hardcoded args, no user data
        ["systemctl", "--user", "daemon-reload"], check=True,
    )
    subprocess.run(  # noqa: S603 — hardcoded args, no user data
        ["systemctl", "--user", "enable", "--now", UNIT_NAME], check=True,
    )
    logger.info("Enabled and started %s", UNIT_NAME)


def uninstall_unit() -> None:
    path = _unit_path()
    # disable --now: stop and disable. Missing unit → non-zero exit, which we swallow.
    subprocess.run(  # noqa: S603 — hardcoded args, no user data
        ["systemctl", "--user", "disable", "--now", UNIT_NAME],
        check=False, capture_output=True, text=True,
    )
    if path.exists():
        path.unlink()
        logger.info("Removed %s", path)
    subprocess.run(  # noqa: S603 — hardcoded args, no user data
        ["systemctl", "--user", "daemon-reload"], check=True,
    )
```

- [ ] **Step 7: Update `cli.py` to use the dispatcher**

Current `install` command (lines 181–185):
```python
@main.command()
def install() -> None:
    """Register the daemon as a Task Scheduler entry (logon trigger)."""
    from dropboxignore import install as install_mod
    install_mod.install_task()
    click.echo("Installed scheduled task 'dropboxignore'.")
```

Replace with:
```python
@main.command()
def install() -> None:
    """Register the daemon with the platform's user-scoped service manager."""
    from dropboxignore.install import install_service
    install_service()
    click.echo("Installed dropboxignore daemon service.")
```

Current `uninstall` command (lines 188–198 — the `try/except RuntimeError` around `install_mod.uninstall_task()`):
```python
    try:
        install_mod.uninstall_task()
    except RuntimeError as exc:
        click.echo(f"Failed to uninstall scheduled task: {exc}", err=True)
        sys.exit(2)
    click.echo("Uninstalled scheduled task 'dropboxignore'.")
```

Replace with (note the import change and the dispatcher call):
```python
    from dropboxignore.install import uninstall_service
    try:
        uninstall_service()
    except RuntimeError as exc:
        click.echo(f"Failed to uninstall daemon service: {exc}", err=True)
        sys.exit(2)
    click.echo("Uninstalled dropboxignore daemon service.")
```

Remove the `from dropboxignore import install as install_mod` line that precedes the `try` (the old code had it at the top of the `uninstall` body; the new code uses a named import of `install_service` / `uninstall_service` from the package).

- [ ] **Step 8: Run the new systemd tests**

Run: `uv run pytest tests/test_linux_systemd.py -v`
Expected: all five tests PASS.

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest -v`
Expected: every test passes on both platforms. If an existing `tests/test_install.py` references `from dropboxignore import install` or `install.install_task`, update its imports:
- `from dropboxignore import install` → `from dropboxignore.install import windows_task as install_mod`
- Call sites: `install.install_task()` → `install_mod.install_task()`, `install.TASK_NAME` → `install_mod.TASK_NAME`, etc.

- [ ] **Step 10: Run ruff**

Run: `uv run ruff check`
Expected: no violations.

- [ ] **Step 11: End-to-end manual check on the Ubuntu VPS (not automated)**

This step requires a real systemd user session — it's not part of CI. On the Ubuntu VPS:

```bash
uv tool install .
dropboxignore install
systemctl --user status dropboxignore.service
```

Expected: `status` shows `active (running)` and the ExecStart line points at the shim installed by uv tool. If `status` shows `failed`, read `journalctl --user -u dropboxignore.service` to diagnose — most likely causes: (a) `HOME` or `DISPLAY` env var differences between login and service context, (b) Dropbox not installed / `~/.dropbox/info.json` absent. **Document the finding** in the PR description.

Then:
```bash
dropboxignore uninstall
systemctl --user status dropboxignore.service
```
Expected: `Unit dropboxignore.service could not be found`. Uninstall succeeded.

- [ ] **Step 12: Commit**

```bash
git add src/dropboxignore/install/ src/dropboxignore/cli.py tests/test_linux_systemd.py
git commit -m "feat: add Linux systemd user-unit install/uninstall

install.py becomes an install/ package dispatching by sys.platform:
windows_task.py (current behavior, unchanged) and linux_systemd.py
(new — writes ~/.config/systemd/user/dropboxignore.service, invokes
systemctl --user daemon-reload + enable --now). cli.install /
cli.uninstall delegate to the package-level install_service /
uninstall_service dispatchers."
```

---

## Task 7: End-to-end Linux reconcile smoke test

**Goal:** One integration test that exercises the full stack — `.dropboxignore` parsing, hierarchical match, `markers` facade on Linux, real xattrs on a real tmp_path — via the `apply` CLI. This is the Linux counterpart to the v0.1 `tests/test_reconcile_with_real_ads.py` (if present) or equivalent.

**Files:**
- Create: `tests/test_linux_reconcile_smoke.py`

**Steps:**

- [ ] **Step 1: Write the test**

Create `tests/test_linux_reconcile_smoke.py`:

```python
"""End-to-end: real xattrs + real reconcile_subtree on a Linux tmp tree."""

from __future__ import annotations

import os
import sys

import pytest

pytestmark = pytest.mark.linux_only

if not sys.platform.startswith("linux"):
    pytest.skip("Linux-only smoke test", allow_module_level=True)


def _xattr_supported(path) -> bool:
    probe = path / ".xattr_probe"
    probe.touch()
    try:
        os.setxattr(os.fspath(probe), "user.dropboxignore.probe", b"1")
    except OSError:
        return False
    finally:
        probe.unlink(missing_ok=True)
    return True


@pytest.fixture(autouse=True)
def _require_xattr_fs(tmp_path):
    if not _xattr_supported(tmp_path):
        pytest.skip(f"tmp_path {tmp_path} rejects user.* xattrs")


def test_apply_marks_and_clears_via_real_xattrs(tmp_path, write_file):
    from dropboxignore import markers
    from dropboxignore.reconcile import reconcile_subtree
    from dropboxignore.rules import RuleCache

    root = tmp_path
    write_file(root / ".dropboxignore", "build/\nsecrets.env\n")
    build_dir = root / "build"
    build_dir.mkdir()
    write_file(build_dir / "artifact.bin", "x")
    write_file(root / "secrets.env", "TOKEN=...")
    keeper = write_file(root / "src" / "keep.py", "print('hi')")

    cache = RuleCache()
    cache.load_root(root)

    report = reconcile_subtree(root, root, cache)

    # build/ itself is marked; descent into it is pruned, so its contents
    # are not individually marked (same contract as Windows).
    assert markers.is_ignored(build_dir) is True
    assert markers.is_ignored(root / "secrets.env") is True
    assert markers.is_ignored(keeper) is False
    assert report.marked >= 2
    assert report.errors == []

    # Drop the rule and re-sweep. The marker should be cleared.
    (root / ".dropboxignore").write_text("", encoding="utf-8")
    cache = RuleCache()
    cache.load_root(root)
    report2 = reconcile_subtree(root, root, cache)

    assert markers.is_ignored(build_dir) is False
    assert markers.is_ignored(root / "secrets.env") is False
    assert report2.cleared >= 2


def test_dropboxignore_itself_never_marked(tmp_path, write_file):
    from dropboxignore import markers
    from dropboxignore.reconcile import reconcile_subtree
    from dropboxignore.rules import RuleCache

    root = tmp_path
    # Rule tries to ignore .dropboxignore itself; must be overridden.
    write_file(root / ".dropboxignore", ".dropboxignore\n")

    cache = RuleCache()
    cache.load_root(root)
    reconcile_subtree(root, root, cache)

    assert markers.is_ignored(root / ".dropboxignore") is False
```

- [ ] **Step 2: Run the test**

Run (on Linux): `uv run pytest tests/test_linux_reconcile_smoke.py -v`
Expected: both tests PASS. If a test skips because `tmp_path` rejects xattrs, the CI runner's filesystem is misconfigured — investigate (`stat -f -c %T $(mktemp -d)`), don't weaken the test.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -v`
Expected: every test passes. `windows_only` skips on Linux, `linux_only` skips on Windows, portable tests pass on both.

- [ ] **Step 4: Commit**

```bash
git add tests/test_linux_reconcile_smoke.py
git commit -m "test: Linux end-to-end reconcile smoke test with real xattrs

Exercises the full stack: .dropboxignore parsing, hierarchical match,
markers facade dispatch to linux_xattr, reconcile_subtree walk. Also
verifies the .dropboxignore-never-marked contract on Linux."
```

---

## Task 8: Documentation

**Goal:** Update README, CLAUDE.md, and the v0.2 design doc's open-questions section so future maintainers (and you-in-six-months) know Linux is supported, how it's installed, and what the Linux-specific gotchas are.

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Steps:**

- [ ] **Step 1: Update `README.md`**

Read the current README (`cat README.md`) and locate the install section. Add a new Linux install subsection immediately after (or in parallel to) the Windows install instructions:

```markdown
### Linux

Requires Python ≥ 3.11 and a systemd user session (standard on Ubuntu, Fedora, Debian, Arch, and most modern distros; WSL2 requires `systemd=true` in `/etc/wsl.conf`).

```bash
uv tool install .
dropboxignore install                    # writes systemd user unit, enables it
systemctl --user status dropboxignore.service
```

To uninstall:

```bash
dropboxignore uninstall                  # disables unit, removes the file
dropboxignore uninstall --purge          # also clears every xattr marker
```

Notes:
- Dropbox on Linux marks ignored paths with the xattr `user.com.dropbox.ignored=1`. Files on filesystems that don't support `user.*` xattrs (tmpfs without `user_xattr`, vfat, some FUSE mounts) are skipped with a WARNING in the daemon log — not an error.
- Several common operations strip xattrs silently: `cp` without `-a`, `mv` across filesystems, most archivers, `vim`'s default save. The watchdog + hourly sweep re-apply markers automatically; no action needed.
```

Add to the "Platform support" section (or create one):

```markdown
## Platform support

- **Windows 10 / 11** — first-class (v0.1). Uses NTFS Alternate Data Streams.
- **Linux** — first-class (v0.2). Uses `user.*` xattrs. Tested on Ubuntu 22.04 / 24.04.
- **macOS** — planned for v0.3. Dropbox on macOS uses a different attribute mechanism (Apple File Provider) that requires runtime detection — not yet implemented.
```

- [ ] **Step 2: Update `CLAUDE.md`**

Open `CLAUDE.md`. Under the "Architecture" section, find the sentence describing `reconcile.reconcile_subtree` as the single source of truth. After it, add:

```markdown
Marker I/O is platform-dispatched via `dropboxignore.markers`, which at import time re-exports `is_ignored`/`set_ignored`/`clear_ignored` from `_backends/windows_ads.py` (Windows NTFS ADS) or `_backends/linux_xattr.py` (Linux `user.com.dropbox.ignored`). No other module branches on `sys.platform` for markers. `reconcile._reconcile_path` catches `OSError(errno.ENOTSUP|EOPNOTSUPP)` from the Linux backend and treats it the same way as `PermissionError` — log WARNING, append to `Report.errors`, continue the sweep.
```

Under "Gotchas", append three Linux-specific items:

```markdown
- Linux xattrs vanish silently through common operations: `cp` without `-a`, cross-filesystem `mv`, most archivers, and `vim`'s default save-via-rename. The watchdog event stream + hourly sweep are the recovery mechanism — don't add a preservation wrapper, the design intentionally leans on reconcile.
- Linux backends use `follow_symlinks=False` on all xattr calls (mirrors `os.walk(followlinks=False)` in reconcile). A symlink marked ignored means the link itself is marked, not its target.
- `roots.discover()` branches on `sys.platform`: `%APPDATA%\Dropbox\info.json` on Windows, `~/.dropbox/info.json` on Linux. Same JSON schema. The `_info_json_path()` helper returns `None` on unsupported platforms (raises a WARNING log); `discover()` then returns `[]`.
```

Under "Commands", update:
- `dropboxignore install` / `uninstall` descriptions to say "user-scoped service (Task Scheduler on Windows, systemd user unit on Linux)" instead of mentioning only Task Scheduler.

Under the "Release" section:
- Note that v0.2 adds a `linux_only` pytest marker; Ubuntu CI now runs both the portable subset and `-m linux_only`.

- [ ] **Step 3: Update the v0.2 design doc's open-questions (if any resolved)**

Open `docs/superpowers/specs/2026-04-21-dropboxignore-v0.2-linux.md`. If the Ubuntu VPS smoke test from Task 6 resolved any of the "Open questions / risks" bullets (e.g., "systemd in a container / WSL" — was it actually fine or did it surface friction?), add a short resolution note under that bullet. If nothing resolved, leave the section unchanged.

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md docs/superpowers/specs/2026-04-21-dropboxignore-v0.2-linux.md
git commit -m "docs: Linux install, gotchas, and architecture notes for v0.2"
```

---

## Final verification checklist

Run on both platforms before opening the PR:

- [ ] `uv run ruff check` — clean on both
- [ ] `uv run pytest -v` — clean on both
- [ ] Windows: `uv run pytest -m windows_only -v` — all pass
- [ ] Linux: `uv run pytest -m linux_only -v` — all pass
- [ ] CI on a pushed branch: both matrix legs green
- [ ] Ubuntu VPS manual: `dropboxignore install` → `systemctl --user status dropboxignore.service` shows `active (running)` → create a `.dropboxignore` in a Dropbox-synced directory → `getfattr -n user.com.dropbox.ignored <matched-path>` returns `user.com.dropbox.ignored="1"` → `dropboxignore uninstall --purge` → xattr cleared.

## Open tasks deferred to v0.3

- macOS support (dual backend: Apple File Provider + legacy kext detection; launchd plist install).
- `--initial-quiet` first-sweep flag for large existing trees.
- PyPI wheel publishing (Linux install today is `uv tool install .` from source).
- Optional `DROPBOXIGNORE_CASE_SENSITIVE=1` env var for Linux-only installs that want POSIX semantics instead of the Windows-compatible case-insensitive default.

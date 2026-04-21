# dropboxignore v0.2 — Linux support (Design)

- **Date:** 2026-04-21
- **Author:** Kilo Scheffer
- **Status:** Draft, ready for implementation plan
- **Target platform:** Linux (Dropbox for Linux, user-namespace xattrs). Windows parity retained.
- **Predecessor:** [v0.1 design](2026-04-20-dropboxignore-design.md) — Windows, NTFS ADS

## Summary

v0.2 ports dropboxignore to Linux while keeping the Windows implementation intact. Dropbox on Linux marks paths as ignored with the extended attribute `user.com.dropbox.ignored=1` — the same semantic contract as the NTFS ADS `com.dropbox.ignored` on Windows. All platform-specific code is consolidated behind a single new facade, `dropboxignore.markers`, that dispatches to `_backends/windows_ads.py` or `_backends/linux_xattr.py` at import time based on `sys.platform`. The reconcile engine, rule cache, CLI, and daemon are unchanged structurally — they call `markers.{is,set,clear}_ignored` where they previously called `ads.{is,set,clear}_ignored`. Two other touch points move behind a platform branch: `roots.discover()` (Dropbox's `info.json` lives at `%APPDATA%\Dropbox\info.json` on Windows, `~/.dropbox/info.json` on Linux), and the `install`/`uninstall` subcommands (Task Scheduler on Windows, systemd user unit on Linux).

## Motivation

The v0.1 engine — rule parsing, hierarchical match, reconcile, watchdog debounce, hourly sweep, CLI — is already platform-neutral. Only `ads.py` (NTFS ADS via the `\\?\path:stream` `CreateFileW` convention) and `install.py` (Task Scheduler XML + `schtasks`) are Windows-locked. The v0.1 CI matrix already runs the portable pytest subset on `ubuntu-latest`, so every module except those two has had Linux test coverage since day one.

Dropbox's Linux client honors `user.com.dropbox.ignored` the same way the Windows client honors the ADS marker: set the attribute and sync stops immediately; clear the attribute and sync resumes. The help center documents it with `attr -s com.dropbox.ignored -V 1 <path>` (the `attr(1)` tool implicitly writes in the `user.*` namespace). Community tools like `pimterry/dropbox-ignore` have shipped against this contract for years.

The value proposition is the same as on Windows: Linux developers keeping source trees under Dropbox don't want to `attr -s` every `node_modules/`, `.venv/`, `target/` by hand on every machine. A declarative `.dropboxignore` per project, applied automatically, is the same win.

## Goals

1. **Linux parity with the v0.1 Windows feature set.** The same `apply`, `status`, `list`, `explain`, `daemon`, `install`, `uninstall` commands work the same way on Linux, with the same reconcile semantics and the same per-user background-service model.
2. **Single public marker API.** `dropboxignore.markers` is the one module all callers use. Platform-specific backends are private (`_backends/`) and selected at import time — callers never branch on `sys.platform`.
3. **No Windows regressions.** The v0.1 behavior on Windows is byte-for-byte preserved. The Windows ADS code moves files but does not change.
4. **Graceful degradation on unsupported filesystems.** On Linux, some filesystems (tmpfs without `user_xattr`, FAT/exFAT, some FUSE mounts, some SMB/NFS shares) reject `user.*` xattrs with `OSError(errno=ENOTSUP)`. The reconcile engine logs a `WARNING` per path and skips rather than aborting the sweep — same blast-radius discipline as the existing `PermissionError` path.
5. **systemd user unit as the Linux daemon host.** Installed at `~/.config/systemd/user/dropboxignore.service`, enabled via `systemctl --user enable --now`. Matches Dropbox's own per-user lifecycle and the Task Scheduler decision from v0.1.

## Non-goals

- **macOS.** Dropbox on macOS switched to Apple's File Provider API circa 2022–2023. File Provider uses a different attribute (`com.apple.fileprovider.ignore#P`) in Apple's own namespace, and older kext-mode installs still use `com.dropbox.ignored` without the `user.` prefix. That's a runtime-detection problem plus two backends plus a separate install story (launchd), which is enough surface to warrant its own milestone — deferred to **v0.3**. Anything that says "macOS" in v0.2 code is expected to raise a clear `NotImplementedError`.
- **Protocol class.** A `typing.Protocol` for `IgnoreBackend` is tempting but adds no value — the backends are module-level functions (no `self`), the facade is a three-line re-export, and we have exactly two concrete backends to coordinate. If a third backend arrives in v0.3, revisit.
- **Config-driven backend selection.** Backend is chosen by `sys.platform` alone. No env var, no config file, no `--backend=...` flag. A user on Linux cannot use the Windows backend and vice versa; the OS determines it.
- **Watching non-Dropbox roots.** Still out of scope (was out of scope in v0.1).
- **Running as root.** The daemon runs as the current user. `user.*` xattrs require owning the file; the Dropbox folder is owned by the user, so this is fine. If a user manages to put files they don't own inside their Dropbox, those fail per-path with `PermissionError` (same pattern as v0.1).

## Key design decisions

| # | Decision | Chosen | Rationale |
|---|---|---|---|
| 1 | Platform dispatch location | `markers.py` — single module, `sys.platform` branch at import time | Keeps every other module platform-agnostic. Alternative (sprinkled `if sys.platform` branches) fragments the decision. |
| 2 | Linux marker mechanism | `user.com.dropbox.ignored=1` via `os.setxattr` / `getxattr` / `removexattr` | Documented by Dropbox (help.dropbox.com/sync/ignored-files). Mirrors Windows ADS semantics. |
| 3 | Attribute namespace | Always `user.` prefix in code; Dropbox's public contract is unqualified `com.dropbox.ignored` but Linux's xattr API requires the namespace | The `user.` prefix is a Linux VFS requirement, not a Dropbox concern — Dropbox reads `user.*` implicitly. |
| 4 | Symlink following | `follow_symlinks=False` on all xattr calls | Mirrors `os.walk(followlinks=False)` in `reconcile_subtree`. A symlink path marked ignored means the *symlink itself* is ignored, not its target. |
| 5 | `ENOTSUP` handling | Catch in `reconcile._reconcile_path`, log `WARNING`, append to `Report.errors`, continue | Same scope partitioning as `PermissionError` in v0.1. One bad filesystem doesn't abort a sweep. |
| 6 | `ENODATA` on clear | Treat as "already cleared"; no error, debug-level log | Mirrors Windows `clear_ignored` swallowing `FileNotFoundError`. |
| 7 | Module rename | `ads.py` → `markers.py`; current code moves verbatim to `_backends/windows_ads.py` | `ads` means "Alternate Data Stream" — literally a Windows-only term. Continuing to use it on Linux would be misleading. |
| 8 | Backends as modules, not classes | Three functions per backend module; facade re-exports by platform | Zero runtime overhead vs. class instantiation; simpler to monkeypatch in tests; no `self` adds nothing. |
| 9 | Linux info.json path | `~/.dropbox/info.json` (XDG-ignored on purpose — Dropbox's own convention) | Matches Dropbox's own documented integration point. Same JSON schema as Windows. |
| 10 | Linux daemon hosting | systemd user unit (`~/.config/systemd/user/dropboxignore.service`), `WantedBy=default.target` | User-scoped; matches Windows Task Scheduler design decision. `default.target` (not `graphical-session.target`) so headless VPS installs work. |
| 11 | Long-path prefix | Still Windows-only; Linux has no 260-char limit | `_backends/linux_xattr.py` uses plain `Path` strings. |
| 12 | CI matrix | Add `linux_only` marker mirroring `windows_only`. Ubuntu leg runs both `not windows_only` and `linux_only`. Windows leg unchanged. | Mirrors v0.1's structure — integration tier gated by `*_only` marker. |

## Architecture changes

### Module map (delta from v0.1)

```
dropboxignore/
├── cli.py                    imports markers (was: ads)
├── roots.py                  platform branch in discover()
├── rules.py                  unchanged
├── reconcile.py              imports markers (was: ads)
├── daemon.py                 unchanged
├── markers.py                NEW — facade, 3 functions, re-exports by sys.platform
├── ads.py                    REMOVED (content lives in _backends/windows_ads.py)
├── install/                  NEW — package, was a single file
│   ├── __init__.py           dispatcher: windows_task | linux_systemd
│   ├── windows_task.py       current install.py content, unchanged
│   └── linux_systemd.py      NEW — unit file generation, systemctl invocation
├── _backends/                NEW — private package
│   ├── __init__.py           empty
│   ├── windows_ads.py        current ads.py content, unchanged
│   └── linux_xattr.py        NEW — xattr-based implementation
└── state.py                  unchanged
```

The rename `ads.py` → `markers.py` ripples into four files: `reconcile.py` (one import, three call sites), `cli.py` (one import, five call sites in `list_ignored` and `uninstall --purge`), `tests/conftest.py` (the `FakeADS` class and `fake_ads` fixture target the new name), and `tests/test_ads_integration.py` (renamed to `tests/test_windows_ads_integration.py`, still `windows_only`).

### The facade

```python
# src/dropboxignore/markers.py
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

### The Linux backend

```python
# src/dropboxignore/_backends/linux_xattr.py
"""Read/write the Dropbox 'ignore' user-namespace xattr on Linux.

Dropbox on Linux treats a path as ignored if it carries the extended
attribute ``user.com.dropbox.ignored`` with any non-empty value.
This module uses ``os.setxattr`` / ``getxattr`` / ``removexattr`` with
``follow_symlinks=False`` so a symlink path is marked on the link
itself (mirroring the ``os.walk(followlinks=False)`` walk discipline
in ``reconcile_subtree``).
"""
from __future__ import annotations

import errno
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

ATTR_NAME = "user.com.dropbox.ignored"
_MARKER_VALUE = b"1"


def _require_absolute(path: Path) -> None:
    if not path.is_absolute():
        raise ValueError(f"markers requires an absolute path; got {path!r}")


def is_ignored(path: Path) -> bool:
    _require_absolute(path)
    try:
        value = os.getxattr(os.fspath(path), ATTR_NAME, follow_symlinks=False)
    except OSError as exc:
        if exc.errno in (errno.ENODATA, errno.ENOATTR):
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
        if exc.errno in (errno.ENODATA, errno.ENOATTR):
            logger.debug("clear_ignored: xattr absent on %s", path)
            return
        if exc.errno == errno.ENOENT:
            logger.debug("clear_ignored: path gone: %s", path)
            return
        raise
```

Notes:
- `errno.ENOATTR` is a BSD-ism; on Linux it's the same value as `ENODATA` (`61`). Python's `errno` module exposes both; checking both future-proofs against a theoretical macOS reuse of this backend (which we're not doing in v0.2, but costs nothing to handle).
- `ENOTSUP` (`95`) is **not** caught here. It propagates as `OSError` to `reconcile._reconcile_path`, which gets a small addition to classify and log it separately from `PermissionError`.
- `os.setxattr` raises `PermissionError` directly on `EACCES` / `EPERM` and `FileNotFoundError` on `ENOENT`, so the v0.1 error-scope partitioning in `reconcile._reconcile_path` already catches those correctly.

### reconcile.py — the single new branch

Add one `except OSError` arm in `_reconcile_path` that classifies by `errno`:

```python
except OSError as exc:
    if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
        logger.warning("Filesystem does not support ignore markers on %s: %s", path, exc)
        report.errors.append((path, f"unsupported: {exc}"))
        return None
    raise
```

Placed after the existing `FileNotFoundError` / `PermissionError` arms. The v0.1 Windows code path never hits `ENOTSUP` (NTFS always supports ADS), so this arm is Linux-only in practice but is not guarded by `sys.platform` — the errno discrimination is sufficient.

### roots.py — platform branch

```python
# src/dropboxignore/roots.py (excerpt)

def _info_json_path() -> Path | None:
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
```

`discover()` calls `_info_json_path()` and returns `[]` if `None`. The JSON schema is identical across platforms — `{"personal": {"path": "..."}, "business": {...}}` — so the parsing loop is unchanged.

### install/ — package split

`install/__init__.py` dispatches by platform:

```python
# src/dropboxignore/install/__init__.py
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
        raise NotImplementedError(f"install: no backend for platform {sys.platform!r}")


def uninstall_service() -> None:
    if sys.platform == "win32":
        from dropboxignore.install.windows_task import uninstall_task
        uninstall_task()
    elif sys.platform.startswith("linux"):
        from dropboxignore.install.linux_systemd import uninstall_unit
        uninstall_unit()
    else:
        raise NotImplementedError(f"uninstall: no backend for platform {sys.platform!r}")
```

`cli.install` / `cli.uninstall` call `install_service()` / `uninstall_service()` — no CLI surface change.

### The systemd unit

```ini
[Unit]
Description=dropboxignore daemon
Documentation=https://github.com/kiloscheffer/dropboxignore
After=default.target

[Service]
Type=simple
ExecStart={executable} {arguments}
Restart=on-failure
RestartSec=60s

[Install]
WantedBy=default.target
```

- `{executable}` comes from `detect_invocation()`. For `uv tool install`: `~/.local/share/uv/tools/dropboxignore/bin/dropboxignored` (resolved via `shutil.which("dropboxignored")`). For a PyInstaller build (future): the absolute path to `dropboxignored` in `sys.executable`.
- `WantedBy=default.target` (not `graphical-session.target`) so the daemon starts on any user login, including headless VPS / SSH-only sessions.
- No `Type=notify` — the daemon doesn't sd_notify; `Type=simple` is honest.
- `Restart=on-failure` + `RestartSec=60s` mirrors v0.1's Task Scheduler `<RestartOnFailure><Interval>PT1M</Interval>`.

Install flow:
```
1. write file at ~/.config/systemd/user/dropboxignore.service
2. systemctl --user daemon-reload
3. systemctl --user enable --now dropboxignore.service
```

Uninstall flow:
```
1. systemctl --user disable --now dropboxignore.service   (ignore non-zero: already disabled)
2. rm ~/.config/systemd/user/dropboxignore.service        (missing_ok=True)
3. systemctl --user daemon-reload
```

PyInstaller for Linux is **not** in v0.2 scope. Install path is `uv tool install .` only; `shutil.which("dropboxignored")` must return a valid path for install to succeed. If not, `install_unit()` raises `RuntimeError` with the installation instructions.

## Semantics & edge cases (Linux-specific)

### xattrs are lossier than NTFS ADS

The watcher + reconcile loop is even more load-bearing on Linux than on Windows. Common ways a `user.com.dropbox.ignored` attribute gets lost without a corresponding `.dropboxignore` change:

| Operation | Effect on xattr | How reconcile recovers |
|---|---|---|
| `cp` without `--preserve=xattr` or `-a` | Destination has no xattr | Next sweep (or watchdog create event) re-marks it. |
| `mv` across filesystems | Destination has no xattr | Watchdog create event at new location. |
| `tar`/`zip`/`rsync` default | Archive entries have no xattr | On restore, sweep re-marks. |
| `vim :w` (default `:set backupcopy=auto`) | vim renames tempfile over original; new inode has no xattr | Watchdog modify event re-marks. |
| `git checkout` / `git pull` | Git rewrites files; xattrs lost | Watchdog modify event re-marks. |

All of the above are handled by the existing event dispatch + hourly sweep — no new code needed. They're documented here so future maintainers don't over-engineer a preservation layer.

### Unsupported filesystems

`setxattr` / `getxattr` / `removexattr` fail with `OSError(errno=ENOTSUP)` on filesystems that don't implement user xattrs. Observed failure modes:

- **tmpfs** without `user_xattr` mount option (default varies by kernel/distro)
- **vfat / exFAT** (no xattr support at all)
- **many FUSE filesystems** (sshfs default, some rclone mounts)
- **some NFS mounts** (server-dependent)
- **some SMB/CIFS mounts** (server-dependent)

The reconcile engine logs `WARNING` per offending path and continues. A user who points Dropbox at a vfat mount (unusual) gets log spam but no crash. We do not preemptively probe the filesystem — too many false positives, and the per-path errno is authoritative.

### Ownership / namespace

`user.*` xattrs require either owning the file or `CAP_FOWNER`. The Dropbox folder is the user's own, so this is a non-issue for the expected deployment. If a user somehow ends up with root-owned files inside their Dropbox (e.g., `sudo cp`), those specific paths fail with `PermissionError` — v0.1's existing handling.

### The `.dropboxignore` protection contract is unchanged

A file named `.dropboxignore` is never marked ignored, regardless of rules matching it — enforced in `rules.match`/`explain` and `reconcile._reconcile_path`. This is platform-agnostic; the v0.1 behavior (including the `WARNING` log when one is found already marked) applies verbatim on Linux.

### Case sensitivity

Linux filesystems are typically case-sensitive (ext4, btrfs, xfs by default). The v0.1 case-insensitive regex compilation (`_CaseInsensitiveGitIgnorePattern` prepending `(?i)`) is a Windows correctness fix. On Linux it's slightly wrong — a rule `node_modules` would also match a directory literally named `Node_Modules`, which doesn't exist but could. We leave it as-is in v0.2 for consistency with v0.1 across machines that sync the same `.dropboxignore` file between Windows and Linux. If a Linux-only user finds this a problem, a `DROPBOXIGNORE_CASE_SENSITIVE=1` env var is a one-line future addition — documented as an open question, not a v0.2 deliverable.

## Testing strategy

### Unit tier (portable)

All existing `not windows_only` tests pass unchanged after the rename (the `fake_ads` fixture becomes `fake_markers` with the same contract — the `reconcile` and `cli` modules reference `markers` instead of `ads`). One new pure-unit test file:

- `tests/test_markers_facade.py` — verifies the facade re-exports three callables, and that on an unsupported platform (simulated by monkeypatching `sys.platform`) the stubs raise `NotImplementedError` with a message naming the supported platforms. Doesn't exercise real xattrs or ADS.

### Linux integration tier (new, `@pytest.mark.linux_only`)

```
tests/test_linux_xattr_integration.py    # roundtrip, ENODATA/ENOENT, symlink
tests/test_linux_reconcile_smoke.py      # one end-to-end: real tmp_path tree, real xattrs, reconcile
```

### Linux unit tier (new, runs on every platform)

```
tests/test_linux_systemd.py              # unit file generation + subprocess call assembly; subprocess mocked
```

Root discovery is covered by generalizing the existing `tests/test_roots.py` (staging a fake `info.json` at the platform's documented location and setting `APPDATA`/`HOME` accordingly) rather than a parallel Linux-only file — the test logic is behavioral and wants to run on both legs. The end-to-end smoke test requires a filesystem that supports `user.*` xattrs; `ubuntu-latest` runners use ext4, which works. A session-level skip fires if the tmp filesystem rejects xattrs, with a clear message rather than erroring.

`ENOTSUP` coverage: directly invoke `reconcile._reconcile_path` on a monkeypatched `markers.set_ignored` that raises `OSError(errno.ENOTSUP, ...)`; assert one `report.errors` entry, no crash, `WARNING` logged. This is a portable unit test, not an integration test — no real unsupported filesystem needed.

### Windows integration tier (unchanged)

The existing `windows_only` tests continue to run on the Windows leg. File `tests/test_ads_integration.py` is renamed to `tests/test_windows_ads_integration.py` to match the new module name; the tests themselves are byte-for-byte unchanged (they import via the `markers` facade).

### Not covered by v0.2 tests

- macOS (any variant).
- Linux with SELinux enforcing unusual xattr policies (possible on RHEL-family; out of scope).
- Windows-Linux cross-sync of a `.dropboxignore` file where case sensitivity differs (see "Case sensitivity" above).

## CI/CD additions

### `.github/workflows/test.yml`

Add one step to the Ubuntu leg; Windows leg unchanged:

```yaml
      - name: Linux-only integration tests
        if: runner.os == 'Linux'
        run: uv run pytest -m linux_only -v
```

`pyproject.toml` markers section grows by one line:

```toml
markers = [
    "windows_only: test requires NTFS alternate data streams",
    "linux_only: test requires Linux user.* xattrs",
]
```

### `.github/workflows/release.yml`

Unchanged for v0.2. PyInstaller still builds Windows-only binaries. Linux distribution is `uv tool install .` from source or from a published wheel on PyPI (wheel publishing is a future enhancement, not a v0.2 deliverable).

## Repo layout (delta)

```
src/dropboxignore/
├── ads.py                         DELETED
├── install.py                     → split into install/ package
├── markers.py                     NEW
├── install/
│   ├── __init__.py                NEW (dispatcher)
│   ├── windows_task.py            NEW (moved from install.py)
│   └── linux_systemd.py           NEW
├── _backends/
│   ├── __init__.py                NEW (empty)
│   ├── windows_ads.py             NEW (moved from ads.py)
│   └── linux_xattr.py             NEW
└── …                              rest unchanged

tests/
├── test_ads_integration.py        → renamed test_windows_ads_integration.py
├── test_markers_facade.py         NEW
├── test_linux_xattr_integration.py NEW
├── test_linux_roots.py            NEW
├── test_linux_systemd.py          NEW
└── test_linux_reconcile_smoke.py  NEW
```

## Open questions / risks

- **systemd in a container / WSL.** `systemctl --user` requires a running systemd user manager. On WSL1 it doesn't exist; on WSL2 it needs `systemd=true` in `/etc/wsl.conf`. A WSL user who runs `dropboxignore install` on a non-systemd setup gets a clear error from `systemctl` (non-zero exit), which we surface as a `RuntimeError`. No auto-fallback to a different supervisor (cron, user-level shell-script-in-`.profile`) in v0.2 — it's niche and not worth the surface area.
- **Dropbox headless-CLI feature parity.** Dropbox for Linux has a `dropbox` CLI tool that can itself set the ignore flag via `dropbox exclude`. We do not use it — we set the xattr directly, same as the Windows path does with ADS — so our behavior is independent of whether the `dropbox` CLI is installed. Confirm via smoke test.
- **xattr disappearance on busy editing.** Several common editor save patterns drop xattrs. The watchdog-driven recovery handles this at sub-second latency in testing, but real-world usage may surface cases where a `modify` event doesn't fire (editor uses `rename()` without writing). Mitigation: the hourly sweep. If a user reports visible desync, log a specific `DEBUG` line for `modify` events on already-ignored paths so the missing-event case is diagnosable from logs.
- **First-sweep cost on Ubuntu VPS dev box.** An existing large Dropbox tree with `.venv/`, `node_modules/`, etc. already present triggers a long initial mark pass. Same concern as Windows v0.1. `--initial-quiet` is still not in v0.2 — noted as a v0.3 candidate alongside macOS.
- **Case sensitivity mismatch for cross-platform syncs.** (See "Case sensitivity" above.) Deferred.

## Appendix — Linux specifics

### `os.setxattr` behavior reference (Python 3.11+)

| Call | Raises | Our handling |
|---|---|---|
| `setxattr` on nonexistent path | `FileNotFoundError` (auto-mapped from `ENOENT`) | Caught in `reconcile._reconcile_path` (v0.1 behavior). |
| `setxattr` with no permission | `PermissionError` (auto-mapped from `EACCES`/`EPERM`) | Caught in `reconcile._reconcile_path` (v0.1 behavior). |
| `setxattr` on unsupported FS | `OSError` with `errno.ENOTSUP` (95) | NEW arm in `reconcile._reconcile_path`. |
| `getxattr` on nonexistent attr | `OSError` with `errno.ENODATA` (61) | Returned as `False` from `is_ignored`. |
| `removexattr` on nonexistent attr | `OSError` with `errno.ENODATA` | Logged `DEBUG`, treated as no-op. |

### Why `follow_symlinks=False` everywhere

`os.walk(followlinks=False)` in `reconcile_subtree` means we never *descend into* a symlinked directory. But we do visit symlinks as entries. If a symlink's name matches an ignore rule, we want to mark the symlink itself — not the target. `lsetxattr` (what Python calls when `follow_symlinks=False`) does exactly that. If we used `follow_symlinks=True` and the symlink pointed outside the Dropbox root, we'd mark a file that's not Dropbox's concern.

### Attribute name reference

| Platform | Full attribute name | Write tool (manual) |
|---|---|---|
| Windows | NTFS ADS `com.dropbox.ignored` | `Set-Content -Stream com.dropbox.ignored -Value 1 <path>` |
| Linux | xattr `user.com.dropbox.ignored` | `attr -s com.dropbox.ignored -V 1 <path>` (the `user.` prefix is implicit to `attr(1)`) |
| macOS (File Provider, default since ~2022) | xattr `com.apple.fileprovider.ignore#P` | Apple API; user-tool setting not officially supported — **v0.3** concern |
| macOS (legacy kext) | xattr `com.dropbox.ignored` (no `user.` — macOS has no user/system split) | `xattr -w com.dropbox.ignored 1 <path>` — **v0.3** concern |

### Sources

- Dropbox help — "Ignore a file or folder": <https://help.dropbox.com/sync/ignored-files>
- Linux `xattr(7)` man page — user namespace semantics
- Python docs — `os.setxattr`, `os.getxattr`, `os.removexattr`, `os.listxattr`
- systemd documentation — user units, `systemctl --user`

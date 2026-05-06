"""Cross-platform unit tests for the macOS xattr backend.

These tests monkeypatch the ``xattr`` module so they run on Linux and macOS
(the ``xattr`` package is not installable on Windows — no C-extension wheel
exists). The mocking exercises the full errno-handling logic without touching
real extended attributes.
"""

from __future__ import annotations

import errno
import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

if sys.platform == "win32":
    pytest.skip("xattr package not installable on Windows", allow_module_level=True)

import xattr  # type: ignore[import-not-found]  # noqa: E402  # after skip guard

from dbxignore._backends import macos_xattr as mod  # noqa: E402  # after skip guard

# ---- helpers ----------------------------------------------------------------

# ENOATTR is BSD/macOS-specific (errno 93). Python on Linux may not define it;
# fall back to 93 so the tests exercise the right numeric code everywhere.
_ENOATTR = getattr(errno, "ENOATTR", 93)


def _oserr(err_no: int) -> OSError:
    exc = OSError(err_no, f"mock OSError[{err_no}]")
    return exc


# ---- is_ignored -------------------------------------------------------------


def test_is_ignored_returns_false_when_attr_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ENOATTR from getxattr → False (attribute simply not set)."""
    p = tmp_path / "file.txt"
    p.touch()

    monkeypatch.setattr(xattr, "getxattr", MagicMock(side_effect=_oserr(_ENOATTR)))
    assert mod.is_ignored(p) is False


def test_is_ignored_returns_true_when_attr_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful getxattr with non-empty bytes → True."""
    p = tmp_path / "file.txt"
    p.touch()

    monkeypatch.setattr(xattr, "getxattr", MagicMock(return_value=b"1"))
    assert mod.is_ignored(p) is True


def test_is_ignored_raises_filenotfound_on_enoent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ENOENT from getxattr → FileNotFoundError (path is gone)."""
    p = tmp_path / "gone.txt"

    monkeypatch.setattr(xattr, "getxattr", MagicMock(side_effect=_oserr(errno.ENOENT)))
    with pytest.raises(FileNotFoundError):
        mod.is_ignored(p)


def test_is_ignored_propagates_unexpected_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unexpected OSError (e.g. EACCES) propagates unchanged."""
    p = tmp_path / "file.txt"
    p.touch()

    monkeypatch.setattr(xattr, "getxattr", MagicMock(side_effect=_oserr(errno.EACCES)))
    with pytest.raises(OSError) as exc_info:
        mod.is_ignored(p)
    assert exc_info.value.errno == errno.EACCES


def test_is_ignored_rejects_relative_path() -> None:
    """Relative path → ValueError before any xattr call."""
    with pytest.raises(ValueError, match="absolute"):
        mod.is_ignored(Path("relative/path.txt"))


# ---- set_ignored ------------------------------------------------------------


@pytest.fixture
def legacy_mode(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force `_detected_attr_names()` to return [ATTR_LEGACY] for one test.

    Pins the single-attr legacy-write behavior regardless of the host's
    pluginkit availability. Without this, Linux test runners (no
    pluginkit binary → `_pluginkit_extension_state()` returns "unknown")
    would land in the dual-attr mode and break ``assert_called_once_with``
    invariants in tests that pre-date item 58.
    """
    monkeypatch.setattr(mod, "_decision_cache", ([mod.ATTR_LEGACY], "legacy: test override"))
    yield
    monkeypatch.setattr(mod, "_decision_cache", None)


def test_set_ignored_calls_setxattr_with_correct_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy_mode
) -> None:
    """set_ignored calls xattr.setxattr with the detected attr name and _MARKER_VALUE."""
    p = tmp_path / "file.txt"
    p.touch()

    mock_setxattr = MagicMock()
    monkeypatch.setattr(xattr, "setxattr", mock_setxattr)
    mod.set_ignored(p)

    mock_setxattr.assert_called_once_with(str(p), mod.ATTR_LEGACY, mod._MARKER_VALUE, symlink=True)


def test_set_ignored_propagates_unexpected_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unexpected OSError from setxattr propagates unchanged."""
    p = tmp_path / "file.txt"
    p.touch()

    monkeypatch.setattr(xattr, "setxattr", MagicMock(side_effect=_oserr(errno.EACCES)))
    with pytest.raises(OSError) as exc_info:
        mod.set_ignored(p)
    assert exc_info.value.errno == errno.EACCES


def test_set_ignored_rejects_relative_path() -> None:
    """Relative path → ValueError before any xattr call."""
    with pytest.raises(ValueError, match="absolute"):
        mod.set_ignored(Path("relative/path.txt"))


# ---- clear_ignored ----------------------------------------------------------


def test_clear_ignored_calls_removexattr_with_correct_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy_mode
) -> None:
    """clear_ignored calls xattr.removexattr with the detected attr name and symlink=True."""
    p = tmp_path / "file.txt"
    p.touch()

    mock_removexattr = MagicMock()
    monkeypatch.setattr(xattr, "removexattr", mock_removexattr)
    mod.clear_ignored(p)

    mock_removexattr.assert_called_once_with(str(p), mod.ATTR_LEGACY, symlink=True)


def test_clear_ignored_is_noop_when_attr_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ENOATTR from removexattr → no-op (already cleared)."""
    p = tmp_path / "file.txt"
    p.touch()

    monkeypatch.setattr(xattr, "removexattr", MagicMock(side_effect=_oserr(_ENOATTR)))
    mod.clear_ignored(p)  # must not raise


def test_clear_ignored_is_noop_when_path_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ENOENT from removexattr → no-op (path is already gone)."""
    p = tmp_path / "gone.txt"

    monkeypatch.setattr(xattr, "removexattr", MagicMock(side_effect=_oserr(errno.ENOENT)))
    mod.clear_ignored(p)  # must not raise


def test_clear_ignored_propagates_unexpected_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unexpected OSError from removexattr propagates unchanged."""
    p = tmp_path / "file.txt"
    p.touch()

    monkeypatch.setattr(xattr, "removexattr", MagicMock(side_effect=_oserr(errno.EACCES)))
    with pytest.raises(OSError) as exc_info:
        mod.clear_ignored(p)
    assert exc_info.value.errno == errno.EACCES


def test_clear_ignored_rejects_relative_path() -> None:
    """Relative path → ValueError before any xattr call."""
    with pytest.raises(ValueError, match="absolute"):
        mod.clear_ignored(Path("relative/path.txt"))


# ---- _detected_attr_name (Dropbox sync mode auto-detection) -----------------


@pytest.fixture
def reset_attr_cache(monkeypatch):
    """Reset `_decision_cache` to None for each test so detection re-runs.

    Without this, the first test to call `_detect()` would populate the
    cache, and subsequent tests would see whatever value that test
    happened to monkeypatch — order-dependent flakiness.
    """
    monkeypatch.setattr(mod, "_decision_cache", None)
    yield
    monkeypatch.setattr(mod, "_decision_cache", None)


def _fake_pluginkit(stdout: str = "", side_effect: Exception | None = None) -> None:
    """Build a `subprocess.run` replacement that simulates `pluginkit` output.

    Returns a function suitable for `monkeypatch.setattr("subprocess.run", ...)`.
    If `side_effect` is given, the fake raises it instead of returning a result —
    used to test the "unknown" pluginkit state (FileNotFoundError on non-macOS
    hosts, TimeoutExpired on a hung pluginkit).
    """

    def fake(args, **kwargs):
        if side_effect is not None:
            raise side_effect
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

    return fake


_DROPBOX_ACCOUNT_KEYS = ("personal", "business")


def _stage_dropbox_info(home: Path, *paths: str) -> None:
    """Write a `~/.dropbox/info.json` under `home` listing the given account paths.

    Matches the shape Dropbox actually writes: one top-level key per account
    (`personal`, `business`), each with a `path` field. Pass 1 path for a
    personal-only account, 2 for personal+business. Calling with more than
    2 paths raises — Dropbox doesn't support more than the two account types.
    """
    if len(paths) > len(_DROPBOX_ACCOUNT_KEYS):
        raise ValueError(
            f"Dropbox info.json supports at most {len(_DROPBOX_ACCOUNT_KEYS)} "
            f"accounts; got {len(paths)}"
        )
    info_dir = home / ".dropbox"
    info_dir.mkdir(parents=True, exist_ok=True)
    accounts = {
        key: {"path": p, "host": 1, "is_team": False}
        for key, p in zip(_DROPBOX_ACCOUNT_KEYS, paths, strict=False)
    }
    (info_dir / "info.json").write_text(json.dumps(accounts), encoding="utf-8")


# ---- Path-primary detection: info.json + pluginkit combination ---------------


def test_detected_attr_name_fileprovider_when_path_under_cloudstorage_and_extension_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_attr_cache
) -> None:
    """The default File Provider case: info.json path under
    ~/Library/CloudStorage/ AND pluginkit shows extension allowed
    (whitespace prefix) → File Provider.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    cloud_storage_dropbox = tmp_path / "Library" / "CloudStorage" / "Dropbox"
    cloud_storage_dropbox.mkdir(parents=True)
    _stage_dropbox_info(tmp_path, str(cloud_storage_dropbox))
    monkeypatch.setattr(
        "subprocess.run",
        _fake_pluginkit(stdout="     com.getdropbox.dropbox.fileprovider(250.4.3245)\n"),
    )
    assert mod._detected_attr_name() == mod.ATTR_FILEPROVIDER


def test_detected_attr_name_legacy_when_path_outside_cloudstorage_and_extension_not_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_attr_cache
) -> None:
    """Pure legacy install: info.json path is `~/Dropbox`, pluginkit returns
    no matching extension (Dropbox.app version doesn't ship the FP extension,
    or app isn't installed) → legacy.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy_dropbox = tmp_path / "Dropbox"
    legacy_dropbox.mkdir()
    _stage_dropbox_info(tmp_path, str(legacy_dropbox))
    monkeypatch.setattr("subprocess.run", _fake_pluginkit(stdout=""))
    assert mod._detected_attr_name() == mod.ATTR_LEGACY


def test_detected_attr_name_legacy_when_extension_installed_but_user_in_legacy_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_attr_cache
) -> None:
    """The bug v0.4.0a4 missed: user has Dropbox.app with FP extension
    registered (pluginkit allowed) BUT this account is still on legacy mode
    (info.json path is `~/Dropbox`, NOT under CloudStorage). Pre-fix
    detection would have wrongly returned File Provider; correct detection
    follows the path → legacy.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy_dropbox = tmp_path / "Dropbox"
    legacy_dropbox.mkdir()
    _stage_dropbox_info(tmp_path, str(legacy_dropbox))
    monkeypatch.setattr(
        "subprocess.run",
        _fake_pluginkit(stdout="     com.getdropbox.dropbox.fileprovider(250.4.3245)\n"),
    )
    assert mod._detected_attr_name() == mod.ATTR_LEGACY


def test_detected_attr_name_legacy_when_extension_disabled_overrides_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_attr_cache
) -> None:
    """Disabled extension wins over path. Even if info.json's path is under
    CloudStorage (perhaps Dropbox hadn't updated info.json yet after the
    user disabled the extension), a `-` prefix in pluginkit means File
    Provider isn't actually running for any account → legacy.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    cloud_storage_dropbox = tmp_path / "Library" / "CloudStorage" / "Dropbox"
    cloud_storage_dropbox.mkdir(parents=True)
    _stage_dropbox_info(tmp_path, str(cloud_storage_dropbox))
    monkeypatch.setattr(
        "subprocess.run",
        _fake_pluginkit(stdout="-    com.getdropbox.dropbox.fileprovider(250.4.3245)\n"),
    )
    assert mod._detected_attr_name() == mod.ATTR_LEGACY


def test_detected_attr_name_fileprovider_external_drive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_attr_cache
) -> None:
    """External-drive File Provider: info.json path is `/Volumes/<Drive>/...`
    (mounted external drive) and pluginkit shows the extension allowed.
    Per Dropbox's docs, File Provider supports external drives via an
    eligibility-gated feature; we treat this case as File Provider mode.

    Uses a literal `/Volumes/MyDrive/Dropbox` path even though the
    directory doesn't exist on the test runner — `os.path.realpath` returns
    paths unchanged when intermediate components don't exist, so the
    `parts[1] == "Volumes"` check still fires correctly.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    _stage_dropbox_info(tmp_path, "/Volumes/MyDrive/Dropbox")
    monkeypatch.setattr(
        "subprocess.run",
        _fake_pluginkit(stdout="     com.getdropbox.dropbox.fileprovider(250.4.3245)\n"),
    )
    assert mod._detected_attr_name() == mod.ATTR_FILEPROVIDER


def test_detected_attr_name_legacy_when_path_elsewhere_and_not_volumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_attr_cache
) -> None:
    """Path is neither under CloudStorage nor under /Volumes — even with
    extension allowed, this is the "legacy with extension installed" case
    and we want legacy. Pins the narrow scoping of the external-drive
    branch (must be /Volumes/, not just "anywhere not CloudStorage").
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    custom_legacy = tmp_path / "Documents" / "MyDropbox"
    custom_legacy.mkdir(parents=True)
    _stage_dropbox_info(tmp_path, str(custom_legacy))
    monkeypatch.setattr(
        "subprocess.run",
        _fake_pluginkit(stdout="     com.getdropbox.dropbox.fileprovider(250.4.3245)\n"),
    )
    assert mod._detected_attr_name() == mod.ATTR_LEGACY


def test_detected_attr_name_fileprovider_when_business_account_uses_cloudstorage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_attr_cache
) -> None:
    """Multi-account: any account with a path under CloudStorage triggers
    File Provider mode. info.json has both `personal` (legacy ~/Dropbox)
    and `business` (under CloudStorage) — we follow the File Provider one.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy_dropbox = tmp_path / "Dropbox"
    legacy_dropbox.mkdir()
    business_dropbox = tmp_path / "Library" / "CloudStorage" / "Dropbox-Work"
    business_dropbox.mkdir(parents=True)
    _stage_dropbox_info(tmp_path, str(legacy_dropbox), str(business_dropbox))
    monkeypatch.setattr(
        "subprocess.run",
        _fake_pluginkit(stdout="     com.getdropbox.dropbox.fileprovider(250.4.3245)\n"),
    )
    assert mod._detected_attr_name() == mod.ATTR_FILEPROVIDER


# ---- Defensive paths (no info.json / pluginkit unavailable / no HOME) -------


def test_detected_attr_name_legacy_when_no_info_json_and_no_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_attr_cache
) -> None:
    """Dropbox not installed at all: no info.json, pluginkit empty → legacy
    (defensive default; the daemon won't actually be running anyway because
    `roots.discover()` would have returned empty before any marker call).
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("subprocess.run", _fake_pluginkit(stdout=""))
    assert mod._detected_attr_name() == mod.ATTR_LEGACY


def test_detected_attr_names_writes_both_when_pluginkit_unknown_and_no_info_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_attr_cache
) -> None:
    """pluginkit errored (test host without the binary, e.g. Linux CI) AND
    info.json is missing → uncertain. Dual-attribute defensive write
    (followup item 58): the active sync stack reads its own attribute,
    the inactive one ignores the stray, and the user-visible "Dropbox
    stops syncing the marked path" outcome holds either way.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "subprocess.run",
        _fake_pluginkit(side_effect=FileNotFoundError("pluginkit not found")),
    )
    assert mod._detected_attr_names() == [mod.ATTR_LEGACY, mod.ATTR_FILEPROVIDER]


def test_detected_attr_name_fileprovider_when_pluginkit_unknown_but_path_under_cloudstorage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_attr_cache
) -> None:
    """pluginkit unavailable BUT info.json path is under CloudStorage → File
    Provider. Path is the user-level fact and we trust it without pluginkit
    when pluginkit can't be queried.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    cloud_storage_dropbox = tmp_path / "Library" / "CloudStorage" / "Dropbox"
    cloud_storage_dropbox.mkdir(parents=True)
    _stage_dropbox_info(tmp_path, str(cloud_storage_dropbox))
    monkeypatch.setattr(
        "subprocess.run",
        _fake_pluginkit(side_effect=FileNotFoundError("pluginkit not found")),
    )
    assert mod._detected_attr_name() == mod.ATTR_FILEPROVIDER


def test_detected_attr_name_legacy_when_home_unset(
    monkeypatch: pytest.MonkeyPatch, reset_attr_cache
) -> None:
    """No HOME → no info.json, no path-prefix check possible. With pluginkit
    empty too, defensive legacy default.
    """
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setattr("subprocess.run", _fake_pluginkit(stdout=""))
    assert mod._detected_attr_name() == mod.ATTR_LEGACY


def test_detected_attr_names_writes_both_when_pluginkit_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_attr_cache
) -> None:
    """TimeoutExpired (pluginkit hung) returns "unknown" extension state.
    No info.json + unknown state → dual-attribute defensive write.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "subprocess.run",
        _fake_pluginkit(side_effect=subprocess.TimeoutExpired(cmd="pluginkit", timeout=2)),
    )
    assert mod._detected_attr_names() == [mod.ATTR_LEGACY, mod.ATTR_FILEPROVIDER]


# ---- Caching ----------------------------------------------------------------


def test_detected_attr_name_caches_first_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_attr_cache
) -> None:
    """First call invokes both `subprocess.run` (pluginkit) and the
    info.json read; subsequent calls hit the cache.

    Critical for per-file reconcile loop performance — the daemon
    processes thousands of paths per sweep, and re-running detection
    on every marker call would add ~50ms × N to the sweep wall-clock.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    cloud_storage_dropbox = tmp_path / "Library" / "CloudStorage" / "Dropbox"
    cloud_storage_dropbox.mkdir(parents=True)
    _stage_dropbox_info(tmp_path, str(cloud_storage_dropbox))

    pluginkit_calls = 0

    def fake_run(args, **kwargs):
        nonlocal pluginkit_calls
        pluginkit_calls += 1
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="     com.getdropbox.dropbox.fileprovider(250.4.3245)\n",
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", fake_run)

    first = mod._detected_attr_name()
    second = mod._detected_attr_name()
    third = mod._detected_attr_name()

    assert first == second == third == mod.ATTR_FILEPROVIDER
    assert pluginkit_calls == 1, "pluginkit should only be invoked once"


# ---- File Provider mode end-to-end ------------------------------------------


@pytest.fixture
def fileprovider_mode(monkeypatch):
    """Force `_detected_attr_names()` to return [ATTR_FILEPROVIDER] for one test.

    Sets the cache directly so the tests don't depend on filesystem state —
    cleaner than constructing the full ~/Library/CloudStorage/Dropbox path
    via tmp_path. Each test in this section asserts the xattr backend
    routes the correct attribute name through to the underlying xattr call.
    """
    monkeypatch.setattr(
        mod, "_decision_cache", ([mod.ATTR_FILEPROVIDER], "file_provider: test override")
    )
    yield
    monkeypatch.setattr(mod, "_decision_cache", None)


@pytest.fixture
def both_mode(monkeypatch):
    """Force `_detected_attr_names()` to return both attrs for one test.

    Models the genuinely-uncertain case (followup item 58): pluginkit
    unavailable AND no decisive info.json signal. The backend writes
    BOTH com.dropbox.ignored AND com.apple.fileprovider.ignore#P.
    """
    monkeypatch.setattr(
        mod,
        "_decision_cache",
        ([mod.ATTR_LEGACY, mod.ATTR_FILEPROVIDER], "both: test override"),
    )
    yield
    monkeypatch.setattr(mod, "_decision_cache", None)


def test_is_ignored_uses_fileprovider_attr_in_fileprovider_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fileprovider_mode
) -> None:
    """In File Provider mode, is_ignored reads `com.apple.fileprovider.ignore#P`."""
    p = tmp_path / "file.txt"
    p.touch()

    mock_getxattr = MagicMock(return_value=b"1")
    monkeypatch.setattr(xattr, "getxattr", mock_getxattr)

    assert mod.is_ignored(p) is True
    mock_getxattr.assert_called_once_with(str(p), mod.ATTR_FILEPROVIDER, symlink=True)


def test_set_ignored_writes_fileprovider_attr_in_fileprovider_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fileprovider_mode
) -> None:
    """In File Provider mode, set_ignored writes `com.apple.fileprovider.ignore#P`."""
    p = tmp_path / "file.txt"
    p.touch()

    mock_setxattr = MagicMock()
    monkeypatch.setattr(xattr, "setxattr", mock_setxattr)
    mod.set_ignored(p)

    mock_setxattr.assert_called_once_with(
        str(p), mod.ATTR_FILEPROVIDER, mod._MARKER_VALUE, symlink=True
    )


def test_clear_ignored_removes_fileprovider_attr_in_fileprovider_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fileprovider_mode
) -> None:
    """In File Provider mode, clear_ignored removes `com.apple.fileprovider.ignore#P`."""
    p = tmp_path / "file.txt"
    p.touch()

    mock_removexattr = MagicMock()
    monkeypatch.setattr(xattr, "removexattr", mock_removexattr)
    mod.clear_ignored(p)

    mock_removexattr.assert_called_once_with(str(p), mod.ATTR_FILEPROVIDER, symlink=True)


# ---- Dual-attribute (both-mode) end-to-end ----------------------------------
# Followup item 58: when pluginkit is unavailable AND info.json gave no
# decisive path signal, write BOTH attribute names so whichever sync stack
# is actually active reads its own and the inactive one ignores the stray.


def test_set_ignored_writes_both_attrs_in_both_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, both_mode
) -> None:
    """In both-mode, set_ignored issues two setxattr calls — one per attr."""
    p = tmp_path / "file.txt"
    p.touch()

    mock_setxattr = MagicMock()
    monkeypatch.setattr(xattr, "setxattr", mock_setxattr)
    mod.set_ignored(p)

    assert mock_setxattr.call_count == 2
    called_names = [call.args[1] for call in mock_setxattr.call_args_list]
    assert called_names == [mod.ATTR_LEGACY, mod.ATTR_FILEPROVIDER]


def test_is_ignored_returns_true_if_first_attr_set_in_both_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, both_mode
) -> None:
    """If the legacy attr is set, is_ignored short-circuits without reading
    the second — short-circuit semantics matter when the user is on legacy
    sync; we want an early True rather than two getxattr calls per file."""
    p = tmp_path / "file.txt"
    p.touch()

    mock_getxattr = MagicMock(return_value=b"1")
    monkeypatch.setattr(xattr, "getxattr", mock_getxattr)

    assert mod.is_ignored(p) is True
    mock_getxattr.assert_called_once_with(str(p), mod.ATTR_LEGACY, symlink=True)


def test_is_ignored_returns_true_if_second_attr_set_in_both_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, both_mode
) -> None:
    """First attr ENOATTR, second attr present → True. Exercises the
    fall-through path that's the whole point of writing both."""
    p = tmp_path / "file.txt"
    p.touch()

    call_outcomes = [_oserr(_ENOATTR), b"1"]
    mock_getxattr = MagicMock(side_effect=call_outcomes)
    monkeypatch.setattr(xattr, "getxattr", mock_getxattr)

    assert mod.is_ignored(p) is True
    assert mock_getxattr.call_count == 2


def test_is_ignored_returns_false_if_both_attrs_absent_in_both_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, both_mode
) -> None:
    """Both attrs ENOATTR → False (path simply not marked under either name)."""
    p = tmp_path / "file.txt"
    p.touch()

    mock_getxattr = MagicMock(side_effect=[_oserr(_ENOATTR), _oserr(_ENOATTR)])
    monkeypatch.setattr(xattr, "getxattr", mock_getxattr)

    assert mod.is_ignored(p) is False
    assert mock_getxattr.call_count == 2


def test_clear_ignored_removes_both_attrs_in_both_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, both_mode
) -> None:
    """clear_ignored issues two removexattr calls — one per attr — and
    treats per-attr ENOATTR as a no-op (so a half-marked path that only
    ever had one attr written gets cleaned up regardless)."""
    p = tmp_path / "file.txt"
    p.touch()

    mock_removexattr = MagicMock(side_effect=[None, _oserr(_ENOATTR)])
    monkeypatch.setattr(xattr, "removexattr", mock_removexattr)
    mod.clear_ignored(p)

    assert mock_removexattr.call_count == 2
    called_names = [call.args[1] for call in mock_removexattr.call_args_list]
    assert called_names == [mod.ATTR_LEGACY, mod.ATTR_FILEPROVIDER]


# ---- detection_summary public API (followup item 37) ------------------------


def test_detection_summary_starts_with_mode_token_in_legacy_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_attr_cache
) -> None:
    """The summary returned by detection_summary() leads with the mode
    keyword (`legacy:`/`file_provider:`/`both:`) so daemon-log-grep and
    `dbxignore status` parsers get a stable prefix."""
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy_dropbox = tmp_path / "Dropbox"
    legacy_dropbox.mkdir()
    _stage_dropbox_info(tmp_path, str(legacy_dropbox))
    monkeypatch.setattr("subprocess.run", _fake_pluginkit(stdout=""))

    summary = mod.detection_summary()
    assert summary.startswith("legacy:")


def test_detection_summary_starts_with_both_when_pluginkit_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reset_attr_cache
) -> None:
    """The both-mode summary starts with `both:` so users can tell at a
    glance that detection landed in the dual-attribute defensive arm."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "subprocess.run",
        _fake_pluginkit(side_effect=FileNotFoundError("pluginkit not found")),
    )

    summary = mod.detection_summary()
    assert summary.startswith("both:")

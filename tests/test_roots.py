import json
import logging
import sys
from pathlib import Path

import pytest

from dbxignore import roots

FIXTURES = Path(__file__).parent / "fixtures"


def _stage_info(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fixture_name: str | None = None,
    *,
    content: str | None = None,
) -> None:
    """Stage a fake Dropbox info.json at the platform's documented location.

    Pass ``fixture_name`` to copy a static fixture file, or ``content`` to
    write a literal JSON string (used by positive-path tests that need
    info.json to point at real, existing directories under ``tmp_path`` —
    ``discover()`` now validates account paths and filters non-existent
    ones). Pass neither to stage the directory without an info.json.
    """
    # Skip on unsupported platforms first so mypy's flow narrowing sees the
    # win32/linux variables as always bound below (pytest.skip is NoReturn,
    # but mypy's strict mode under host=darwin doesn't always pick that up
    # when it's the tail of an if/elif/else).
    if sys.platform != "win32" and not sys.platform.startswith("linux"):
        pytest.skip(f"unsupported platform {sys.platform}")
    if sys.platform == "win32":
        base = tmp_path / "AppData"
        dropbox_dir = base / "Dropbox"
        env_var = "APPDATA"
    else:
        base = tmp_path / "home"
        dropbox_dir = base / ".dropbox"
        env_var = "HOME"

    dropbox_dir.mkdir(parents=True)
    if content is not None:
        (dropbox_dir / "info.json").write_text(content, encoding="utf-8")
    elif fixture_name is not None:
        fixture_content = (FIXTURES / fixture_name).read_text(encoding="utf-8")
        (dropbox_dir / "info.json").write_text(fixture_content, encoding="utf-8")
    monkeypatch.setenv(env_var, str(base))
    if sys.platform == "win32":
        # Clear LOCALAPPDATA so the LOCALAPPDATA fallback in _info_json_paths
        # doesn't pick up the developer's real `%LOCALAPPDATA%\Dropbox\info.json`
        # when running tests on a machine that has Dropbox installed per-machine.
        # CI runners don't hit this (no Dropbox), but local dev machines do.
        monkeypatch.delenv("LOCALAPPDATA", raising=False)


def _info_for(accounts: dict[str, str]) -> str:
    """Return info.json text mapping each account type to a ``path`` entry."""
    return json.dumps({atype: {"path": p} for atype, p in accounts.items()})


def _clear_platform_env(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform == "win32":
        monkeypatch.delenv("APPDATA", raising=False)
        # Clear LOCALAPPDATA too — `_info_json_paths` checks both Windows
        # candidates, so leaving LOCALAPPDATA set would point discovery
        # at the runner's real `%LOCALAPPDATA%` (no info.json there in
        # CI, but an environment-dependent assertion regardless).
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
    elif sys.platform.startswith("linux"):
        monkeypatch.delenv("HOME", raising=False)


def test_discover_personal_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dbx = tmp_path / "Dropbox"
    dbx.mkdir()
    _stage_info(monkeypatch, tmp_path, content=_info_for({"personal": str(dbx)}))
    assert roots.discover() == [dbx]


def test_discover_personal_and_business(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dbx = tmp_path / "Dropbox"
    dbx.mkdir()
    work = tmp_path / "Dropbox (Work)"
    work.mkdir()
    _stage_info(
        monkeypatch,
        tmp_path,
        content=_info_for({"personal": str(dbx), "business": str(work)}),
    )
    assert roots.discover() == [dbx, work]


def test_discover_missing_info_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stage_info(monkeypatch, tmp_path, fixture_name=None)
    assert roots.discover() == []


def test_discover_malformed_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stage_info(monkeypatch, tmp_path, "info_malformed.json")
    assert roots.discover() == []


def test_discover_no_platform_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_platform_env(monkeypatch)
    assert roots.discover() == []


def test_discover_json_not_object(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _stage_info(monkeypatch, tmp_path, "info_not_object.json")
    assert roots.discover() == []


def test_discover_env_override_returns_env_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DBXIGNORE_ROOT set to an existing dir returns [Path(env)],
    bypassing info.json entirely."""
    fake_root = tmp_path / "custom-dropbox"
    fake_root.mkdir()
    monkeypatch.setenv("DBXIGNORE_ROOT", str(fake_root))
    # Deliberately do NOT stage info.json — override must not need it.
    _clear_platform_env(monkeypatch)

    assert roots.discover() == [fake_root]


def test_discover_env_override_wins_over_info_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When both DBXIGNORE_ROOT and a valid info.json are present, the
    env var wins — the whole point of the escape hatch."""
    fake_root = tmp_path / "custom-dropbox"
    fake_root.mkdir()
    _stage_info(monkeypatch, tmp_path, "info_personal.json")
    monkeypatch.setenv("DBXIGNORE_ROOT", str(fake_root))

    result = roots.discover()

    assert result == [fake_root]
    assert result != [Path(r"C:\Dropbox")]  # would be the info.json answer


def test_discover_env_override_empty_string_falls_back_to_info_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DBXIGNORE_ROOT="" is indistinguishable from unset in practice
    (shell quirks), so treat it as unset and fall back to info.json."""
    dbx = tmp_path / "Dropbox"
    dbx.mkdir()
    _stage_info(monkeypatch, tmp_path, content=_info_for({"personal": str(dbx)}))
    monkeypatch.setenv("DBXIGNORE_ROOT", "")

    assert roots.discover() == [dbx]


def test_discover_env_override_missing_path_warns_and_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If DBXIGNORE_ROOT points at a nonexistent path, return [] with a
    WARNING — so the CLI's "No Dropbox roots found" surfaces rather than a
    silent no-op sweep that leaves the user puzzled."""
    import logging

    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv("DBXIGNORE_ROOT", str(missing))
    _clear_platform_env(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="dbxignore.roots"):
        result = roots.discover()

    assert result == []
    assert any(
        "DBXIGNORE_ROOT" in rec.message and str(missing) in rec.message for rec in caplog.records
    ), [rec.message for rec in caplog.records]


def test_discover_env_override_file_path_warns_and_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """DBXIGNORE_ROOT pointing at a file (not a directory) is rejected with
    a WARNING. A file as a "root" would silently produce no-op applies and
    fail the daemon's recursive observer schedule."""
    import logging

    file_path = tmp_path / "not-a-dir.txt"
    file_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("DBXIGNORE_ROOT", str(file_path))
    _clear_platform_env(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="dbxignore.roots"):
        result = roots.discover()

    assert result == []
    assert any(
        "DBXIGNORE_ROOT" in rec.message and "not a directory" in rec.message
        for rec in caplog.records
    ), [rec.message for rec in caplog.records]


def test_discover_env_override_relative_path_warns_and_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """DBXIGNORE_ROOT must be an absolute path. A relative path's meaning
    drifts with the daemon's CWD — Task Scheduler, systemd, and launchd
    all set their own working directory at launch."""
    import logging

    monkeypatch.chdir(tmp_path)
    (tmp_path / "subdir").mkdir()
    monkeypatch.setenv("DBXIGNORE_ROOT", "subdir")
    _clear_platform_env(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="dbxignore.roots"):
        result = roots.discover()

    assert result == []
    assert any(
        "DBXIGNORE_ROOT" in rec.message and "absolute" in rec.message for rec in caplog.records
    ), [rec.message for rec in caplog.records]


def test_discover_non_utf8_bytes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    if sys.platform == "win32":
        base = tmp_path / "AppData"
        dropbox_dir = base / "Dropbox"
        env_var = "APPDATA"
    else:
        base = tmp_path / "home"
        dropbox_dir = base / ".dropbox"
        env_var = "HOME"
    dropbox_dir.mkdir(parents=True)
    # Write raw CP1252-encoded bytes that aren't valid UTF-8 where Dropbox
    # has historically stored non-ASCII path components on older installs.
    (dropbox_dir / "info.json").write_bytes(b'{"personal": {"path": "C:\\\\Dr\xf6pbox"}}')
    # Clear the OTHER candidate env vars so the fallback path doesn't pick up
    # the test runner's real LOCALAPPDATA (which on Windows can contain a real
    # Dropbox install) and silently "succeed." Without this, the post-item-5
    # fallback behavior masks the malformed-bytes case under test.
    _clear_platform_env(monkeypatch)
    monkeypatch.setenv(env_var, str(base))
    assert roots.discover() == []


# ---- Windows LOCALAPPDATA fallback (per-machine Dropbox install) ----------
# Per-machine Dropbox installs put info.json under `%LOCALAPPDATA%\Dropbox\`
# instead of `%APPDATA%\Dropbox\`. `_info_json_paths` returns both candidates
# in priority order (APPDATA first); discover picks the first existing one.


def test_discover_finds_info_json_via_localappdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """LOCALAPPDATA fallback fires when APPDATA's candidate doesn't exist."""
    if sys.platform != "win32":
        import pytest

        pytest.skip("LOCALAPPDATA is Windows-only")

    # Stage info.json under LOCALAPPDATA only, pointing at a real dir.
    dbx = tmp_path / "Dropbox"
    dbx.mkdir()
    localappdata = tmp_path / "LocalAppData"
    (localappdata / "Dropbox").mkdir(parents=True)
    (localappdata / "Dropbox" / "info.json").write_text(
        _info_for({"personal": str(dbx)}), encoding="utf-8"
    )
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))
    # Point APPDATA at an empty dir (env set, but no Dropbox subdir).
    appdata_empty = tmp_path / "AppData"
    appdata_empty.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata_empty))

    assert roots.discover() == [dbx]


def test_discover_appdata_wins_over_localappdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When both candidates exist, APPDATA (per-user install) takes priority."""
    if sys.platform != "win32":
        import pytest

        pytest.skip("LOCALAPPDATA is Windows-only")

    # Stage distinct real dirs at APPDATA vs LOCALAPPDATA so the assertion
    # can distinguish which candidate was picked.
    appdata_dbx = tmp_path / "AppDataDropbox"
    appdata_dbx.mkdir()
    localappdata_dbx = tmp_path / "LocalAppDataDropbox"
    localappdata_dbx.mkdir()

    appdata = tmp_path / "AppData"
    (appdata / "Dropbox").mkdir(parents=True)
    (appdata / "Dropbox" / "info.json").write_text(
        _info_for({"personal": str(appdata_dbx)}), encoding="utf-8"
    )
    monkeypatch.setenv("APPDATA", str(appdata))

    localappdata = tmp_path / "LocalAppData"
    (localappdata / "Dropbox").mkdir(parents=True)
    (localappdata / "Dropbox" / "info.json").write_text(
        _info_for({"personal": str(localappdata_dbx)}), encoding="utf-8"
    )
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))

    # APPDATA's candidate wins; LOCALAPPDATA's file is ignored.
    assert roots.discover() == [appdata_dbx]


def test_discover_warns_with_both_candidates_when_neither_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If both env vars are set but neither file exists, the WARNING message
    names both candidate paths — so a user who hits this in the wild knows
    both standard locations were checked."""
    if sys.platform != "win32":
        import pytest

        pytest.skip("LOCALAPPDATA is Windows-only")

    import logging

    appdata = tmp_path / "AppData"
    appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    localappdata = tmp_path / "LocalAppData"
    localappdata.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))

    with caplog.at_level(logging.WARNING, logger="dbxignore.roots"):
        result = roots.discover()

    assert result == []
    assert any(
        "Dropbox info.json not found at any of" in rec.message
        and "AppData" in rec.message
        and "LocalAppData" in rec.message
        for rec in caplog.records
    ), [rec.message for rec in caplog.records]


def test_discover_falls_back_when_first_candidate_malformed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A stale APPDATA\\Dropbox\\info.json from an uninstalled per-user
    install used to mask a valid LOCALAPPDATA\\Dropbox\\info.json. Now, if
    the first existing candidate fails to parse, discover falls through to
    the next candidate, logs a warning per failed candidate, and returns the
    first usable result."""
    if sys.platform != "win32":
        import pytest

        pytest.skip("LOCALAPPDATA is Windows-only")

    import logging

    # APPDATA: malformed JSON (parses as JSON but top-level is a list,
    # not an object — tripping _read_dropbox_account_paths's isinstance
    # check and raising ValueError).
    appdata = tmp_path / "AppData"
    (appdata / "Dropbox").mkdir(parents=True)
    (appdata / "Dropbox" / "info.json").write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))

    # LOCALAPPDATA: valid personal info.json pointing at a real dir.
    dbx = tmp_path / "Dropbox"
    dbx.mkdir()
    localappdata = tmp_path / "LocalAppData"
    (localappdata / "Dropbox").mkdir(parents=True)
    (localappdata / "Dropbox" / "info.json").write_text(
        _info_for({"personal": str(dbx)}), encoding="utf-8"
    )
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))

    with caplog.at_level(logging.WARNING, logger="dbxignore.roots"):
        result = roots.discover()

    # Falls back to the LOCALAPPDATA personal account path.
    assert result == [dbx]
    # A per-candidate warning surfaces the bad APPDATA file with a
    # "trying next candidate" hint, but discover still succeeds.
    assert any(
        "AppData" in rec.message and "trying next candidate" in rec.message
        for rec in caplog.records
    ), [rec.message for rec in caplog.records]


def test_discover_falls_back_when_first_candidate_parses_to_empty_accounts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An info.json that parses cleanly but contains no usable account paths
    (e.g. ``{}`` or ``{"personal": {}}`` from a Dropbox-uninstall residue)
    used to short-circuit ``discover()`` with the same empty result as if the
    per-machine candidate didn't exist. Now an empty ``account_paths`` falls
    through to the next candidate, and the log includes a WARNING naming the
    empty-but-existing file."""
    if sys.platform != "win32":
        import pytest

        pytest.skip("LOCALAPPDATA is Windows-only")

    import logging

    # APPDATA: parses cleanly but the personal account has no `path`.
    appdata = tmp_path / "AppData"
    (appdata / "Dropbox").mkdir(parents=True)
    (appdata / "Dropbox" / "info.json").write_text('{"personal": {}}', encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))

    # LOCALAPPDATA: valid personal info.json pointing at a real dir.
    dbx = tmp_path / "Dropbox"
    dbx.mkdir()
    localappdata = tmp_path / "LocalAppData"
    (localappdata / "Dropbox").mkdir(parents=True)
    (localappdata / "Dropbox" / "info.json").write_text(
        _info_for({"personal": str(dbx)}), encoding="utf-8"
    )
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))

    with caplog.at_level(logging.WARNING, logger="dbxignore.roots"):
        result = roots.discover()

    assert result == [dbx]
    assert any(
        "no usable account paths" in rec.message and "AppData" in rec.message
        for rec in caplog.records
    ), [rec.message for rec in caplog.records]


def test_discover_returns_empty_when_all_candidates_malformed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If every existing candidate fails to parse, discover returns []
    with a summary warning listing the attempts."""
    if sys.platform != "win32":
        import pytest

        pytest.skip("LOCALAPPDATA is Windows-only")

    import logging

    appdata = tmp_path / "AppData"
    (appdata / "Dropbox").mkdir(parents=True)
    (appdata / "Dropbox" / "info.json").write_text("not valid json", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))

    localappdata = tmp_path / "LocalAppData"
    (localappdata / "Dropbox").mkdir(parents=True)
    (localappdata / "Dropbox" / "info.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))

    with caplog.at_level(logging.WARNING, logger="dbxignore.roots"):
        result = roots.discover()

    assert result == []
    assert any(
        "No usable Dropbox info.json after trying" in rec.message for rec in caplog.records
    ), [rec.message for rec in caplog.records]


# ---- info.json account-path validation -----------------------------------
# A stale info.json entry (account removed, drive unmounted, folder
# relocated) used to become a configured root unchecked. The daemon then
# crashed at `observer.schedule()` on the nonexistent path. `discover()`
# now validates account paths the same way it validates the
# DBXIGNORE_ROOT override — invalid entries are skipped with a WARNING.


def test_discover_skips_nonexistent_account_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An info.json path that doesn't exist on disk is skipped with a
    WARNING — it must not reach the daemon's observer.schedule()."""
    missing = tmp_path / "gone-dropbox"  # never created
    _stage_info(monkeypatch, tmp_path, content=_info_for({"personal": str(missing)}))

    with caplog.at_level(logging.WARNING, logger="dbxignore.roots"):
        result = roots.discover()

    assert result == []
    assert any(str(missing) in rec.message for rec in caplog.records), [
        rec.message for rec in caplog.records
    ]


def test_discover_skips_account_path_that_is_a_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An info.json path pointing at a file (not a directory) is skipped —
    a file as a "root" breaks the daemon's recursive observer schedule."""
    not_a_dir = tmp_path / "dropbox.txt"
    not_a_dir.write_text("", encoding="utf-8")
    _stage_info(monkeypatch, tmp_path, content=_info_for({"personal": str(not_a_dir)}))

    with caplog.at_level(logging.WARNING, logger="dbxignore.roots"):
        result = roots.discover()

    assert result == []
    assert any(str(not_a_dir) in rec.message for rec in caplog.records), [
        rec.message for rec in caplog.records
    ]


def test_discover_skips_relative_account_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A relative account path is rejected: its meaning drifts with the
    daemon's CWD, and a bare name like ``Dropbox`` could spuriously match
    a CWD-relative directory. Dropbox writes absolute paths; a relative one
    means the file is corrupt."""
    (tmp_path / "Dropbox").mkdir()  # exists relative to a hypothetical CWD
    _stage_info(monkeypatch, tmp_path, content=_info_for({"personal": "Dropbox"}))

    with caplog.at_level(logging.WARNING, logger="dbxignore.roots"):
        result = roots.discover()

    assert result == []
    assert any("absolute" in rec.message for rec in caplog.records), [
        rec.message for rec in caplog.records
    ]


def test_discover_keeps_valid_account_path_skips_invalid_sibling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One stale account must not mask a valid sibling: validation filters
    per-entry, so a real personal folder still discovers even when the
    business account's path is stale."""
    dbx = tmp_path / "Dropbox"
    dbx.mkdir()
    stale = tmp_path / "old-business-dropbox"  # never created
    _stage_info(
        monkeypatch,
        tmp_path,
        content=_info_for({"personal": str(dbx), "business": str(stale)}),
    )

    with caplog.at_level(logging.WARNING, logger="dbxignore.roots"):
        result = roots.discover()

    assert result == [dbx]
    assert any(str(stale) in rec.message for rec in caplog.records), [
        rec.message for rec in caplog.records
    ]

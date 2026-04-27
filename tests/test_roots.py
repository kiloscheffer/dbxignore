import sys
from pathlib import Path

from dbxignore import roots

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
    if sys.platform == "win32":
        # Clear LOCALAPPDATA so the LOCALAPPDATA fallback in _info_json_paths
        # doesn't pick up the developer's real `%LOCALAPPDATA%\Dropbox\info.json`
        # when running tests on a machine that has Dropbox installed per-machine.
        # CI runners don't hit this (no Dropbox), but local dev machines do.
        monkeypatch.delenv("LOCALAPPDATA", raising=False)


def _clear_platform_env(monkeypatch):
    if sys.platform == "win32":
        monkeypatch.delenv("APPDATA", raising=False)
        # Clear LOCALAPPDATA too — `_info_json_paths` checks both Windows
        # candidates, so leaving LOCALAPPDATA set would point discovery
        # at the runner's real `%LOCALAPPDATA%` (no info.json there in
        # CI, but an environment-dependent assertion regardless).
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
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


def test_discover_env_override_returns_env_path(monkeypatch, tmp_path):
    """DBXIGNORE_ROOT set to an existing dir returns [Path(env)],
    bypassing info.json entirely."""
    fake_root = tmp_path / "custom-dropbox"
    fake_root.mkdir()
    monkeypatch.setenv("DBXIGNORE_ROOT", str(fake_root))
    # Deliberately do NOT stage info.json — override must not need it.
    _clear_platform_env(monkeypatch)

    assert roots.discover() == [fake_root]


def test_discover_env_override_wins_over_info_json(monkeypatch, tmp_path):
    """When both DBXIGNORE_ROOT and a valid info.json are present, the
    env var wins — the whole point of the escape hatch."""
    fake_root = tmp_path / "custom-dropbox"
    fake_root.mkdir()
    _stage_info(monkeypatch, tmp_path, "info_personal.json")
    monkeypatch.setenv("DBXIGNORE_ROOT", str(fake_root))

    result = roots.discover()

    assert result == [fake_root]
    assert result != [Path(r"C:\Dropbox")]  # would be the info.json answer


def test_discover_env_override_empty_string_falls_back_to_info_json(monkeypatch, tmp_path):
    """DBXIGNORE_ROOT="" is indistinguishable from unset in practice
    (shell quirks), so treat it as unset and fall back to info.json."""
    _stage_info(monkeypatch, tmp_path, "info_personal.json")
    monkeypatch.setenv("DBXIGNORE_ROOT", "")

    assert roots.discover() == [Path(r"C:\Dropbox")]


def test_discover_env_override_missing_path_warns_and_returns_empty(
    monkeypatch, tmp_path, caplog
):
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
        "DBXIGNORE_ROOT" in rec.message and str(missing) in rec.message
        for rec in caplog.records
    ), [rec.message for rec in caplog.records]


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
    # Write raw CP1252-encoded bytes that aren't valid UTF-8 where Dropbox
    # has historically stored non-ASCII path components on older installs.
    (dropbox_dir / "info.json").write_bytes(b'{"personal": {"path": "C:\\\\Dr\xf6pbox"}}')
    monkeypatch.setenv(env_var, str(base))
    assert roots.discover() == []


# ---- Windows LOCALAPPDATA fallback (per-machine Dropbox install) ----------
# Per-machine Dropbox installs put info.json under `%LOCALAPPDATA%\Dropbox\`
# instead of `%APPDATA%\Dropbox\`. `_info_json_paths` returns both candidates
# in priority order (APPDATA first); discover picks the first existing one.


def test_discover_finds_info_json_via_localappdata(monkeypatch, tmp_path):
    """LOCALAPPDATA fallback fires when APPDATA's candidate doesn't exist."""
    if sys.platform != "win32":
        import pytest
        pytest.skip("LOCALAPPDATA is Windows-only")

    # Stage info.json under LOCALAPPDATA only.
    localappdata = tmp_path / "LocalAppData"
    (localappdata / "Dropbox").mkdir(parents=True)
    (localappdata / "Dropbox" / "info.json").write_text(
        (FIXTURES / "info_personal.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))
    # Point APPDATA at an empty dir (env set, but no Dropbox subdir).
    appdata_empty = tmp_path / "AppData"
    appdata_empty.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata_empty))

    assert roots.discover() == [Path(r"C:\Dropbox")]


def test_discover_appdata_wins_over_localappdata(monkeypatch, tmp_path):
    """When both candidates exist, APPDATA (per-user install) takes priority."""
    if sys.platform != "win32":
        import pytest
        pytest.skip("LOCALAPPDATA is Windows-only")

    # Stage personal at APPDATA, business at LOCALAPPDATA — different content
    # so the assertion can distinguish which candidate was picked.
    appdata = tmp_path / "AppData"
    (appdata / "Dropbox").mkdir(parents=True)
    (appdata / "Dropbox" / "info.json").write_text(
        (FIXTURES / "info_personal.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(appdata))

    localappdata = tmp_path / "LocalAppData"
    (localappdata / "Dropbox").mkdir(parents=True)
    (localappdata / "Dropbox" / "info.json").write_text(
        (FIXTURES / "info_personal_business.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))

    # APPDATA's personal-only fixture wins; LOCALAPPDATA's two-account file is ignored.
    assert roots.discover() == [Path(r"C:\Dropbox")]


def test_discover_warns_with_both_candidates_when_neither_exists(
    monkeypatch, tmp_path, caplog
):
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

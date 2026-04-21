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


def test_discover_env_override_returns_env_path(monkeypatch, tmp_path):
    """DROPBOXIGNORE_ROOT set to an existing dir returns [Path(env)],
    bypassing info.json entirely."""
    fake_root = tmp_path / "custom-dropbox"
    fake_root.mkdir()
    monkeypatch.setenv("DROPBOXIGNORE_ROOT", str(fake_root))
    # Deliberately do NOT stage info.json — override must not need it.
    _clear_platform_env(monkeypatch)

    assert roots.discover() == [fake_root]


def test_discover_env_override_wins_over_info_json(monkeypatch, tmp_path):
    """When both DROPBOXIGNORE_ROOT and a valid info.json are present, the
    env var wins — the whole point of the escape hatch."""
    fake_root = tmp_path / "custom-dropbox"
    fake_root.mkdir()
    _stage_info(monkeypatch, tmp_path, "info_personal.json")
    monkeypatch.setenv("DROPBOXIGNORE_ROOT", str(fake_root))

    result = roots.discover()

    assert result == [fake_root]
    assert result != [Path(r"C:\Dropbox")]  # would be the info.json answer


def test_discover_env_override_empty_string_falls_back_to_info_json(monkeypatch, tmp_path):
    """DROPBOXIGNORE_ROOT="" is indistinguishable from unset in practice
    (shell quirks), so treat it as unset and fall back to info.json."""
    _stage_info(monkeypatch, tmp_path, "info_personal.json")
    monkeypatch.setenv("DROPBOXIGNORE_ROOT", "")

    assert roots.discover() == [Path(r"C:\Dropbox")]


def test_discover_env_override_missing_path_warns_and_returns_empty(
    monkeypatch, tmp_path, caplog
):
    """If DROPBOXIGNORE_ROOT points at a nonexistent path, return [] with a
    WARNING — so the CLI's "No Dropbox roots found" surfaces rather than a
    silent no-op sweep that leaves the user puzzled."""
    import logging

    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv("DROPBOXIGNORE_ROOT", str(missing))
    _clear_platform_env(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="dropboxignore.roots"):
        result = roots.discover()

    assert result == []
    assert any(
        "DROPBOXIGNORE_ROOT" in rec.message and str(missing) in rec.message
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

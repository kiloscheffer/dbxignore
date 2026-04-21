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
    # Write raw CP1252-encoded bytes that aren't valid UTF-8 where Dropbox
    # has historically stored non-ASCII path components on older installs.
    (dropbox_dir / "info.json").write_bytes(b'{"personal": {"path": "C:\\\\Dr\xf6pbox"}}')
    monkeypatch.setenv(env_var, str(base))
    assert roots.discover() == []

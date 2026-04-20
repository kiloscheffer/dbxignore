from pathlib import Path

from dropboxignore import roots

FIXTURES = Path(__file__).parent / "fixtures"


def _monkeypatch_info(monkeypatch, tmp_path, fixture_name: str | None):
    """Stage a fake %APPDATA%\\Dropbox\\info.json and point APPDATA at it."""
    appdata = tmp_path / "AppData"
    dropbox_dir = appdata / "Dropbox"
    dropbox_dir.mkdir(parents=True)
    if fixture_name is not None:
        content = (FIXTURES / fixture_name).read_text(encoding="utf-8")
        (dropbox_dir / "info.json").write_text(content, encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))


def test_discover_personal_only(monkeypatch, tmp_path):
    _monkeypatch_info(monkeypatch, tmp_path, "info_personal.json")
    result = roots.discover()
    assert result == [Path(r"C:\Dropbox")]


def test_discover_personal_and_business(monkeypatch, tmp_path):
    _monkeypatch_info(monkeypatch, tmp_path, "info_personal_business.json")
    result = roots.discover()
    assert result == [Path(r"C:\Dropbox"), Path(r"C:\Dropbox (Work)")]


def test_discover_missing_info_file(monkeypatch, tmp_path):
    _monkeypatch_info(monkeypatch, tmp_path, fixture_name=None)
    assert roots.discover() == []


def test_discover_malformed_json(monkeypatch, tmp_path):
    _monkeypatch_info(monkeypatch, tmp_path, "info_malformed.json")
    assert roots.discover() == []


def test_discover_no_appdata_env(monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    assert roots.discover() == []

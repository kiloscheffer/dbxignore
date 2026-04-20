import pytest
from click.testing import CliRunner

from dropboxignore import cli, reconcile
from tests.test_reconcile_basic import FakeADS


@pytest.fixture
def fake_ads(monkeypatch):
    fake = FakeADS()
    monkeypatch.setattr(reconcile, "ads", fake)
    return fake


def test_apply_marks_matching_paths(tmp_path, fake_ads, monkeypatch):
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "src").mkdir()

    # Force roots.discover() to return tmp_path.
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "build").resolve() in fake_ads._ignored
    assert (tmp_path / "src").resolve() not in fake_ads._ignored
    assert "marked=1" in result.output or "1 marked" in result.output


def test_apply_with_path_argument_scopes_reconcile(tmp_path, fake_ads, monkeypatch):
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "build").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "build").mkdir()

    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", str(tmp_path / "a")])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "a" / "build").resolve() in fake_ads._ignored
    assert (tmp_path / "b" / "build").resolve() not in fake_ads._ignored

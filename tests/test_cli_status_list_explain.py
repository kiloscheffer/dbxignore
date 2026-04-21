import datetime as dt
import os
from pathlib import Path

from click.testing import CliRunner

from dropboxignore import cli, state


def test_status_reports_no_state_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "missing.json")
    runner = CliRunner()
    result = runner.invoke(cli.main, ["status"])
    assert result.exit_code == 0
    assert "not running" in result.output.lower() or "no state" in result.output.lower()


def test_status_reports_running_daemon(tmp_path, monkeypatch):
    s = state.State(
        daemon_pid=os.getpid(),
        daemon_started=dt.datetime.now(dt.UTC),
        last_sweep=dt.datetime.now(dt.UTC),
        last_sweep_duration_s=1.23,
        last_sweep_marked=7,
        last_sweep_cleared=1,
        last_sweep_errors=0,
        watched_roots=[Path(r"C:\Dropbox")],
    )
    path = tmp_path / "state.json"
    state.write(s, path)
    monkeypatch.setattr(state, "default_path", lambda: path)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["status"])
    assert result.exit_code == 0
    assert "running" in result.output.lower()
    assert "7" in result.output


def test_list_prints_paths_with_ads_set(tmp_path, fake_ads, monkeypatch):
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    fake_ads.set_ignored(tmp_path / "a")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["list"])
    assert result.exit_code == 0
    assert str(tmp_path / "a") in result.output
    assert str(tmp_path / "b") not in result.output


def test_explain_prints_matching_rule(tmp_path, monkeypatch):
    (tmp_path / ".dropboxignore").write_text("# h\nbuild/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["explain", str(tmp_path / "build")])
    assert result.exit_code == 0
    assert "build/" in result.output
    assert ".dropboxignore:2" in result.output or "line 2" in result.output


def test_explain_no_match_output(tmp_path, monkeypatch):
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["explain", str(tmp_path / "src")])
    assert result.exit_code == 0
    assert "no match" in result.output.lower()


def test_list_does_not_descend_into_ignored_directories(tmp_path, fake_ads, monkeypatch):
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "deep").mkdir()
    (tmp_path / "build" / "deep" / "file.o").touch()
    fake_ads.set_ignored(tmp_path / "build")  # parent is ignored

    runner = CliRunner()
    result = runner.invoke(cli.main, ["list"])

    assert result.exit_code == 0
    assert str(tmp_path / "build") in result.output
    # Descendants must NOT appear — list pruned into build/.
    assert str(tmp_path / "build" / "deep") not in result.output
    assert "file.o" not in result.output

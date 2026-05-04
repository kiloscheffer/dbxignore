import datetime as dt
import os
from pathlib import Path

from click.testing import CliRunner

from dbxignore import cli, state


def test_status_reports_no_state_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "missing.json")
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
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
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["status"])
    assert result.exit_code == 0
    assert "running" in result.output.lower()
    assert "7" in result.output


def test_list_prints_paths_with_ads_set(tmp_path, fake_markers, monkeypatch):
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    fake_markers.set_ignored(tmp_path / "a")

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


def test_list_does_not_descend_into_ignored_directories(tmp_path, fake_markers, monkeypatch):
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "deep").mkdir()
    (tmp_path / "build" / "deep" / "file.o").touch()
    fake_markers.set_ignored(tmp_path / "build")  # parent is ignored

    runner = CliRunner()
    result = runner.invoke(cli.main, ["list"])

    assert result.exit_code == 0
    assert str(tmp_path / "build") in result.output
    # Descendants must NOT appear — list pruned into build/.
    assert str(tmp_path / "build" / "deep") not in result.output
    assert "file.o" not in result.output


# ---- status --summary (followup item 60) ------------------------------------


def test_format_summary_no_state_returns_minimal_line():
    """No state.json → only `state=no_state conflicts=N` is meaningful."""
    assert cli._format_summary(None, alive=False, conflicts_count=0) == "state=no_state conflicts=0"
    assert cli._format_summary(None, alive=True, conflicts_count=3) == "state=no_state conflicts=3"


def test_format_summary_running_includes_pid_and_counts():
    """state.json + alive PID → `state=running pid=N marked=N cleared=N errors=N conflicts=N`."""
    s = state.State(
        daemon_pid=12345,
        last_sweep_marked=7,
        last_sweep_cleared=1,
        last_sweep_errors=0,
    )
    assert cli._format_summary(s, alive=True, conflicts_count=0) == (
        "state=running pid=12345 marked=7 cleared=1 errors=0 conflicts=0"
    )


def test_format_summary_not_running_uses_same_pid_field():
    """state.json present but daemon dead → state=not_running, same pid= field
    (parsing stays uniform; the not_running token tells the consumer the pid
    is stale)."""
    s = state.State(
        daemon_pid=12345,
        last_sweep_marked=7,
        last_sweep_cleared=1,
        last_sweep_errors=0,
    )
    assert cli._format_summary(s, alive=False, conflicts_count=2) == (
        "state=not_running pid=12345 marked=7 cleared=1 errors=0 conflicts=2"
    )


def test_format_summary_no_pid_omits_pid_field():
    """state.json present but daemon_pid is None (rare edge: state.json from
    a partial write) → omit pid=, force state=not_running."""
    s = state.State(daemon_pid=None)
    assert cli._format_summary(s, alive=False, conflicts_count=0) == (
        "state=not_running marked=0 cleared=0 errors=0 conflicts=0"
    )


def test_status_summary_flag_emits_single_line(tmp_path, monkeypatch):
    """`dbxignore status --summary` produces exactly one line on stdout
    (the public-API contract for status-bar widgets).

    Pins is_daemon_alive=True via monkeypatch rather than relying on the
    test process's own name matching the dbxignore daemon-name guard:
    pytest entry-point exec'd by `uv run pytest` shows up as `pytest` on
    Linux, which fails the `"python" in name or "dbxignored" in name`
    check and would land us in not_running. Same shape as #58's
    legacy_mode pinning lesson — explicit fixture > host-dependent guess.
    """
    s = state.State(
        daemon_pid=12345,
        daemon_started=dt.datetime.now(dt.UTC),
        last_sweep=dt.datetime.now(dt.UTC),
        last_sweep_marked=5,
        last_sweep_cleared=2,
        last_sweep_errors=0,
        watched_roots=[Path(r"C:\Dropbox")],
    )
    path = tmp_path / "state.json"
    state.write(s, path)
    monkeypatch.setattr(state, "default_path", lambda: path)
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid: True)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["status", "--summary"])
    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert len(lines) == 1, f"expected one line, got {len(lines)}: {result.output!r}"
    line = lines[0]
    assert line.startswith("state=running pid=12345 ")
    assert "marked=5" in line
    assert "cleared=2" in line
    assert "errors=0" in line
    assert "conflicts=0" in line


def test_status_summary_no_state_emits_no_state_token(tmp_path, monkeypatch):
    """No state.json + no roots → `state=no_state conflicts=0` and nothing else."""
    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "missing.json")
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["status", "--summary"])
    assert result.exit_code == 0
    assert result.output.strip() == "state=no_state conflicts=0"


def test_status_lists_rule_conflicts(tmp_path, monkeypatch):
    """`status` surfaces RuleCache conflicts alongside daemon pid / sweep info."""
    import click.testing

    root = tmp_path
    (root / ".dropboxignore").write_text(
        "build/\n!build/keep/\n", encoding="utf-8"
    )

    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])

    result = click.testing.CliRunner().invoke(cli.main, ["status"])
    assert result.exit_code == 0
    assert "rule conflicts (1):" in result.output
    assert "!build/keep/" in result.output
    assert "build/" in result.output
    assert "masked by" in result.output


def test_status_omits_conflicts_section_when_empty(tmp_path, monkeypatch):
    import click.testing

    root = tmp_path
    (root / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])

    result = click.testing.CliRunner().invoke(cli.main, ["status"])
    assert result.exit_code == 0
    assert "rule conflicts" not in result.output


def test_status_column_aligns_conflicts_with_varying_pattern_lengths(tmp_path, monkeypatch):
    """Multi-conflict output column-aligns the 'masked by' prefix and trailing
    fields even when dropped patterns differ in length. Regression backstop:
    a future "simplification" back to fixed two-space separators would fail
    here rather than only surfacing in real-world `.dropboxignore` files."""
    import click.testing

    root = tmp_path
    # Two independent (include, negation-under-it) pairs with very different
    # negation lengths — short ("!build/keep/") vs long ("!node_modules/...").
    (root / ".dropboxignore").write_text(
        "build/\n"
        "node_modules/some-very-long-package/\n"
        "!build/keep/\n"
        "!node_modules/some-very-long-package/patched/\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])

    result = click.testing.CliRunner().invoke(cli.main, ["status"])
    assert result.exit_code == 0

    conflict_lines = [line for line in result.output.splitlines() if "masked by" in line]
    assert len(conflict_lines) == 2, (
        f"expected 2 conflict lines, got: {conflict_lines}"
    )
    columns = [line.index("masked by") for line in conflict_lines]
    assert len(set(columns)) == 1, (
        f"'masked by' should be column-aligned across conflicts; "
        f"got columns {columns} in lines {conflict_lines}"
    )


def test_explain_annotates_dropped_negations(tmp_path, monkeypatch):
    import click.testing

    root = tmp_path
    (root / ".dropboxignore").write_text(
        "build/\n!build/keep/\n", encoding="utf-8"
    )
    (root / "build").mkdir()
    (root / "build" / "keep").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])

    result = click.testing.CliRunner().invoke(
        cli.main, ["explain", str(root / "build" / "keep")],
    )
    assert result.exit_code == 0
    assert "build/" in result.output
    assert "[dropped]" in result.output
    assert "!build/keep/" in result.output


def test_status_does_not_log_conflict_warning_to_stderr(tmp_path, monkeypatch, caplog):
    """`status` surfaces conflicts via stdout (the `rule conflicts (N):`
    section). The WARNING emitted by `_recompute_conflicts` on the daemon
    path would double up the info on stderr; CLI one-shots suppress it."""
    import logging

    import click.testing

    root = tmp_path
    (root / ".dropboxignore").write_text(
        "build/\n!build/keep/\n", encoding="utf-8"
    )
    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])

    with caplog.at_level(logging.WARNING, logger="dbxignore.rules"):
        result = click.testing.CliRunner().invoke(cli.main, ["status"])

    assert result.exit_code == 0
    assert "rule conflicts (1):" in result.output
    conflict_warnings = [
        r for r in caplog.records
        if r.name == "dbxignore.rules" and "negation" in r.message
    ]
    assert conflict_warnings == [], (
        f"status should not emit conflict WARNINGs; got: "
        f"{[r.message for r in conflict_warnings]}"
    )
    assert "masked by" in result.output

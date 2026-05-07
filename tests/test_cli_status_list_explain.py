import datetime as dt
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from dbxignore import cli, state
from tests.conftest import FakeMarkers


def test_status_reports_no_state_when_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "missing.json")
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])
    runner = CliRunner()
    result = runner.invoke(cli.main, ["status"])
    assert result.exit_code == 0
    assert "not running" in result.output.lower() or "no state" in result.output.lower()


def test_status_reports_running_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_list_prints_paths_with_ads_set(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    fake_markers.set_ignored(tmp_path / "a")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["list"])
    assert result.exit_code == 0
    assert str(tmp_path / "a") in result.output
    assert str(tmp_path / "b") not in result.output


def test_explain_prints_matching_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".dropboxignore").write_text("# h\nbuild/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["explain", str(tmp_path / "build")])
    assert result.exit_code == 0
    assert "build/" in result.output
    assert ".dropboxignore:2" in result.output or "line 2" in result.output


def test_explain_no_match_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Not-ignored path → exit 1 (verdict-driven, parity with git check-ignore)."""
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["explain", str(tmp_path / "src")])
    assert result.exit_code == 1
    assert "no match" in result.output.lower()


def test_list_does_not_descend_into_ignored_directories(
    tmp_path: Path, fake_markers: FakeMarkers, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_format_summary_no_state_returns_minimal_line() -> None:
    """No state.json → only `state=no_state conflicts=N` is meaningful."""
    assert cli._format_summary(None, alive=False, conflicts_count=0) == "state=no_state conflicts=0"
    assert cli._format_summary(None, alive=True, conflicts_count=3) == "state=no_state conflicts=3"


def test_format_summary_running_includes_pid_and_counts() -> None:
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


def test_format_summary_not_running_uses_same_pid_field() -> None:
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


def test_format_summary_no_pid_omits_pid_field() -> None:
    """state.json present but daemon_pid is None (rare edge: state.json from
    a partial write) → omit pid=, force state=not_running."""
    s = state.State(daemon_pid=None)
    assert cli._format_summary(s, alive=False, conflicts_count=0) == (
        "state=not_running marked=0 cleared=0 errors=0 conflicts=0"
    )


def test_status_summary_flag_emits_single_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    monkeypatch.setattr(state, "is_daemon_alive", lambda pid, create_time=None: True)
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


def test_status_summary_no_state_emits_no_state_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No state.json + no roots → `state=no_state conflicts=0` and nothing else."""
    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "missing.json")
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["status", "--summary"])
    assert result.exit_code == 0
    assert result.output.strip() == "state=no_state conflicts=0"


def test_status_lists_rule_conflicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`status` surfaces RuleCache conflicts alongside daemon pid / sweep info."""
    import click.testing

    root = tmp_path
    (root / ".dropboxignore").write_text("build/\n!build/keep/\n", encoding="utf-8")

    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])

    result = click.testing.CliRunner().invoke(cli.main, ["status"])
    assert result.exit_code == 0
    assert "rule conflicts (1):" in result.output
    assert "!build/keep/" in result.output
    assert "build/" in result.output
    assert "masked by" in result.output


def test_status_omits_conflicts_section_when_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import click.testing

    root = tmp_path
    (root / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])

    result = click.testing.CliRunner().invoke(cli.main, ["status"])
    assert result.exit_code == 0
    assert "rule conflicts" not in result.output


def test_status_column_aligns_conflicts_with_varying_pattern_lengths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    assert len(conflict_lines) == 2, f"expected 2 conflict lines, got: {conflict_lines}"
    columns = [line.index("masked by") for line in conflict_lines]
    assert len(set(columns)) == 1, (
        f"'masked by' should be column-aligned across conflicts; "
        f"got columns {columns} in lines {conflict_lines}"
    )


def test_explain_annotates_dropped_negations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import click.testing

    root = tmp_path
    (root / ".dropboxignore").write_text("build/\n!build/keep/\n", encoding="utf-8")
    (root / "build").mkdir()
    (root / "build" / "keep").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])

    result = click.testing.CliRunner().invoke(
        cli.main,
        ["explain", str(root / "build" / "keep")],
    )
    assert result.exit_code == 0
    assert "build/" in result.output
    assert "[dropped]" in result.output
    assert "!build/keep/" in result.output


def test_status_does_not_log_conflict_warning_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`status` surfaces conflicts via stdout (the `rule conflicts (N):`
    section). The WARNING emitted by `_recompute_conflicts` on the daemon
    path would double up the info on stderr; CLI one-shots suppress it."""
    import logging

    import click.testing

    root = tmp_path
    (root / ".dropboxignore").write_text("build/\n!build/keep/\n", encoding="utf-8")
    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])

    with caplog.at_level(logging.WARNING, logger="dbxignore.rules"):
        result = click.testing.CliRunner().invoke(cli.main, ["status"])

    assert result.exit_code == 0
    assert "rule conflicts (1):" in result.output
    conflict_warnings = [
        r for r in caplog.records if r.name == "dbxignore.rules" and "negation" in r.message
    ]
    assert conflict_warnings == [], (
        f"status should not emit conflict WARNINGs; got: {[r.message for r in conflict_warnings]}"
    )
    assert "masked by" in result.output


# ---- explain verdict-driven exit codes (followup item 70/71/72) --------


def test_explain_exits_0_when_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Path matched by an active rule → exit 0."""
    (tmp_path / ".dropboxignore").write_text("node_modules/\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["explain", str(tmp_path / "node_modules")])
    assert result.exit_code == 0
    assert "node_modules/" in result.output


def test_explain_exits_1_when_not_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Path with no matching rule → exit 1."""
    (tmp_path / ".dropboxignore").write_text("node_modules/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["explain", str(tmp_path / "src")])
    assert result.exit_code == 1
    assert "no match" in result.output.lower()


def test_explain_exits_2_when_no_dropbox_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No Dropbox roots discovered → exit 2 (fatal, project convention).

    The `2` exit is preserved despite git's `128` for fatal because
    project-wide convention uses `2` for all fatal CLI errors. See spec.
    """
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["explain", "anything"])
    assert result.exit_code == 2
    # "No Dropbox roots found." goes to stderr.
    assert "No Dropbox roots found." in result.output


def test_explain_dropped_negation_path_still_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Path ignored via an ancestor whose negation under it was dropped → exit 0.

    Pins that the verdict comes from `cache.match()` (post-drops), NOT from
    a list-derivation heuristic over `cache.explain()`'s match list. A naive
    `any(not m.is_dropped for m in matches)` would coincidentally agree here,
    but the contract is that `cache.match()` is canonical.
    """
    (tmp_path / ".dropboxignore").write_text("build/\n!build/keep/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "keep").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["explain", str(tmp_path / "build" / "keep")])
    assert result.exit_code == 0
    assert "[dropped]" in result.output


def test_explain_quiet_suppresses_stdout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--quiet on an ignored path → exit 0, empty stdout."""
    (tmp_path / ".dropboxignore").write_text("node_modules/\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["explain", "--quiet", str(tmp_path / "node_modules")])
    assert result.exit_code == 0
    # Click's CliRunner merges stdout+stderr into result.output by default.
    # When --quiet suppresses stdout AND there's no fatal error (so stderr is
    # also empty), the merged output is empty. Use mix_stderr=False if we
    # need to disambiguate.
    assert result.output == ""


def test_explain_quiet_keeps_stderr_for_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--quiet on no-roots → exit 2, stderr preserved (parity with git -q)."""
    monkeypatch.setattr(cli, "_discover_roots", lambda: [])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["explain", "--quiet", "anything"])
    assert result.exit_code == 2
    # Fatal error still goes to stderr (via click.echo(..., err=True)).
    # CliRunner merges stdout+stderr into result.output, so we just verify
    # the error message is there, not suppressed by --quiet.
    assert "No Dropbox roots found." in result.output


@pytest.mark.parametrize(
    "ignored_path,expected_code",
    [("node_modules", 0), ("src", 1)],
)
def test_check_ignore_alias_identical_to_explain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ignored_path: str,
    expected_code: int,
) -> None:
    """`check-ignore <path>` produces the same output and exit code as `explain <path>`.

    Pins the alias's identical-behavior contract for both ignored and
    not-ignored cases.
    """
    (tmp_path / ".dropboxignore").write_text("node_modules/\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    explain_result = runner.invoke(cli.main, ["explain", str(tmp_path / ignored_path)])
    check_ignore_result = runner.invoke(cli.main, ["check-ignore", str(tmp_path / ignored_path)])

    assert explain_result.exit_code == expected_code
    assert check_ignore_result.exit_code == expected_code
    assert explain_result.output == check_ignore_result.output


def test_check_ignore_help_distinguishes_from_explain() -> None:
    """`check-ignore --help` mentions the alias-of framing; `explain --help` does not.

    Pins the deliberate-distinct-docstring decision (Q2=B in the spec) against
    a future refactor that collapses them via `main.add_command(explain, name=...)`.
    """
    runner = CliRunner()
    explain_help = runner.invoke(cli.main, ["explain", "--help"])
    check_ignore_help = runner.invoke(cli.main, ["check-ignore", "--help"])

    assert explain_help.exit_code == 0
    assert check_ignore_help.exit_code == 0
    assert "Alias of `explain`" in check_ignore_help.output
    assert "Alias of `explain`" not in explain_help.output

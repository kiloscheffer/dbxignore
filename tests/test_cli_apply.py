from click.testing import CliRunner

from dbxignore import cli


def test_apply_marks_matching_paths(tmp_path, fake_markers, monkeypatch):
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "src").mkdir()

    # Force roots.discover() to return tmp_path.
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--yes"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "build").resolve() in fake_markers._ignored
    assert (tmp_path / "src").resolve() not in fake_markers._ignored
    assert "marked=1" in result.output or "1 marked" in result.output


def test_apply_with_path_argument_scopes_reconcile(tmp_path, fake_markers, monkeypatch):
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "build").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "build").mkdir()

    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", str(tmp_path / "a"), "--yes"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "a" / "build").resolve() in fake_markers._ignored
    assert (tmp_path / "b" / "build").resolve() not in fake_markers._ignored


def test_apply_from_gitignore_mounts_at_dirname(tmp_path, fake_markers, monkeypatch):
    sub = tmp_path / "sub"
    sub.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (sub / "build").mkdir()
    (other / "build").mkdir()

    gitignore = sub / ".gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--from-gitignore", str(gitignore), "--yes"])

    assert result.exit_code == 0, result.output
    assert (sub / "build").resolve() in fake_markers._ignored
    assert (other / "build").resolve() not in fake_markers._ignored


def test_apply_from_gitignore_ignores_existing_dropboxignore(tmp_path, fake_markers, monkeypatch):
    (tmp_path / ".dropboxignore").write_text("other/\n", encoding="utf-8")
    (tmp_path / "other").mkdir()
    (tmp_path / "build").mkdir()

    gitignore = tmp_path / "my.gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--from-gitignore", str(gitignore), "--yes"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "build").resolve() in fake_markers._ignored
    # 'other' rule from existing .dropboxignore is NOT applied
    assert (tmp_path / "other").resolve() not in fake_markers._ignored


def test_apply_from_gitignore_out_of_root_errors(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    gitignore = outside / ".gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")

    inside = tmp_path / "dropbox"
    inside.mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [inside])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--from-gitignore", str(gitignore)])

    assert result.exit_code == 2
    assert "not under any Dropbox root" in result.output


def test_apply_from_gitignore_mutex_with_positional_path(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["apply", str(tmp_path), "--from-gitignore", str(gitignore)],
    )

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_apply_from_gitignore_directory_arg_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--from-gitignore", str(tmp_path)])

    assert result.exit_code == 2
    assert "file path, not a directory" in result.output


# ---- apply --dry-run (followup item 64) -------------------------------------


def test_apply_dry_run_does_not_mutate_markers(tmp_path, fake_markers, monkeypatch):
    """--dry-run reads rules and stat results but never calls set/clear_ignored."""
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--dry-run"])

    assert result.exit_code == 0, result.output
    # No marker mutations on disk.
    assert (tmp_path / "build").resolve() not in fake_markers._ignored
    assert (tmp_path / "src").resolve() not in fake_markers._ignored


def test_apply_dry_run_prints_would_mark_lines(tmp_path, fake_markers, monkeypatch):
    """--dry-run output lists every path that would have been marked."""
    (tmp_path / ".dropboxignore").write_text("build/\n*.tmp\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "scratch.tmp").touch()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "would mark:" in result.output
    assert "build" in result.output
    assert "scratch.tmp" in result.output


def test_apply_dry_run_prints_would_clear_lines(tmp_path, fake_markers, monkeypatch):
    """When a path is marked but no longer matches any rule, --dry-run reports
    it as a would-clear (mirroring the real apply behavior)."""
    # Pre-mark a path that the rules will say should NOT be ignored.
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    fake_markers.set_ignored(src_dir)
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "would clear:" in result.output
    assert "src" in result.output
    # Mark still on disk — it's a dry-run.
    assert src_dir.resolve() in fake_markers._ignored


def test_apply_dry_run_summary_uses_would_prefix(tmp_path, fake_markers, monkeypatch):
    """Summary line distinguishes dry-run from a real apply (would_mark vs marked)."""
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "apply --dry-run:" in result.output
    assert "would_mark=1" in result.output
    assert "no changes made" in result.output
    # The non-dry-run summary token must NOT appear (would be confusing).
    # `marked=` is a substring of `would_mark=`, so check for the start-of-token version.
    assert "apply: marked=" not in result.output


def test_apply_dry_run_with_from_gitignore_does_not_mutate(tmp_path, fake_markers, monkeypatch):
    """--from-gitignore + --dry-run combine: same one-shot rule-load path,
    same no-mutation guarantee."""
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--from-gitignore", str(gitignore), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "apply --dry-run:" in result.output
    assert "would mark:" in result.output
    assert (tmp_path / "build").resolve() not in fake_markers._ignored


def test_apply_dry_run_real_apply_still_works_after(tmp_path, fake_markers, monkeypatch):
    """A dry-run preview followed by a real apply mutates as expected — the
    dry-run path doesn't leave any stateful residue that would skew the real
    run's behavior."""
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    dry = runner.invoke(cli.main, ["apply", "--dry-run"])
    assert dry.exit_code == 0
    assert (tmp_path / "build").resolve() not in fake_markers._ignored

    real = runner.invoke(cli.main, ["apply", "--yes"])
    assert real.exit_code == 0
    assert (tmp_path / "build").resolve() in fake_markers._ignored


# ---- apply confirmation prompt + --yes (companion to clear's safety) --------


def test_apply_confirmation_prompt_aborts_on_no(tmp_path, fake_markers, monkeypatch):
    """Without --yes, the prompt fires; saying 'n' aborts without marking."""
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply"], input="n\n")

    assert result.exit_code == 0, result.output
    assert "Aborted" in result.output
    # Marker NOT set — the user declined.
    assert (tmp_path / "build").resolve() not in fake_markers._ignored


def test_apply_confirmation_prompt_proceeds_on_yes(tmp_path, fake_markers, monkeypatch):
    """Without --yes, saying 'y' to the prompt marks the matching paths."""
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply"], input="y\n")

    assert result.exit_code == 0, result.output
    assert "marked=1" in result.output
    assert (tmp_path / "build").resolve() in fake_markers._ignored


def test_apply_yes_skips_confirmation_prompt(tmp_path, fake_markers, monkeypatch):
    """--yes runs without prompting (no input needed); markers get set; the
    confirmation copy doesn't appear."""
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--yes"])

    assert result.exit_code == 0, result.output
    assert "marked=1" in result.output
    assert (tmp_path / "build").resolve() in fake_markers._ignored
    assert "Continue?" not in result.output


def test_apply_no_changes_skips_prompt(tmp_path, fake_markers, monkeypatch):
    """When the dry-run pre-walk finds nothing to mark or clear, exit cleanly
    without prompting. Caller provides no input — if a prompt fires, the
    runner aborts and the test fails on a non-zero exit code."""
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    # No 'build' dir exists; the rule has nothing to match.
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply"])  # no input intentionally

    assert result.exit_code == 0, result.output
    assert "Continue?" not in result.output
    assert "Nothing to apply" in result.output


def test_apply_clear_only_direction_also_prompts(tmp_path, fake_markers, monkeypatch):
    """A previously-marked path no longer matched by rules: apply clears it,
    so the prompt must still fire — clearing causes Dropbox to upload the
    local copy back to cloud, which is the symmetric footgun."""
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    fake_markers.set_ignored(src_dir)  # stale mark, no longer matching any rule
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply"], input="n\n")

    assert result.exit_code == 0, result.output
    assert "Aborted" in result.output
    # Mark must still be set — we said no.
    assert src_dir.resolve() in fake_markers._ignored


def test_apply_from_gitignore_yes_skips_prompt(tmp_path, fake_markers, monkeypatch):
    """--yes works on the --from-gitignore path too."""
    (tmp_path / "build").mkdir()
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--from-gitignore", str(gitignore), "--yes"])

    assert result.exit_code == 0, result.output
    assert "Continue?" not in result.output
    assert (tmp_path / "build").resolve() in fake_markers._ignored


def test_apply_from_gitignore_prompt_aborts_on_no(tmp_path, fake_markers, monkeypatch):
    """The prompt also fires on the --from-gitignore path; 'n' aborts."""
    (tmp_path / "build").mkdir()
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--from-gitignore", str(gitignore)], input="n\n")

    assert result.exit_code == 0, result.output
    assert "Aborted" in result.output
    assert (tmp_path / "build").resolve() not in fake_markers._ignored


def test_apply_prompt_mentions_cloud_delete_for_mark_only(tmp_path, fake_markers, monkeypatch):
    """Prompt copy must explicitly call out the cloud-delete consequence for the
    mark-only direction — that's the footgun the prompt exists to surface."""
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply"], input="n\n")

    assert result.exit_code == 0, result.output
    # Substring matches — wording can evolve, but cloud-delete intent must remain.
    assert "remove" in result.output.lower()
    assert "cloud" in result.output.lower() or "linked device" in result.output.lower()

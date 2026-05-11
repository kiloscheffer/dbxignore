"""Cross-cutting symlink-correctness suite for path-taking CLI verbs (item #95).

Verifies that ``apply``, ``clear``, ``list``, ``explain`` and ``check-ignore``
operate on the symlink OBJECT (not the resolved target) when handed a
symlink argument. Pre-fix, all five called ``path.resolve()`` and operated
on the link target. (``explain`` and ``check-ignore`` share the ``_explain``
body, so this is really four code paths surfacing as five CLI verbs.)

Why a raw-argument spy instead of ``fake_markers``: ``FakeMarkers`` in
``tests/conftest.py`` calls ``path.resolve()`` inside ``is_ignored``,
``set_ignored``, ``clear_ignored`` before recording. That resolution
erases the distinction between "CLI passed the link" and "CLI passed the
target," which is exactly the bug surface this PR is about. The spy
records the raw argument the CLI hands to the markers module.

Why not ``legacy_mode``: that fixture lives in
``tests/test_macos_xattr_unit.py`` (not ``conftest.py``) and pins the
macOS xattr backend's dual-vs-legacy attr decision. This suite intercepts
at the CLI → markers boundary, so the backend decision is never invoked.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from dbxignore import cli, markers

# ---- Symlink-availability gate ---------------------------------------------


@pytest.fixture
def symlink_capable(tmp_path: Path) -> None:
    """Skip the test if symlink creation isn't permitted (Windows without Dev Mode)."""
    probe_target = tmp_path / "_symlink_probe_target"
    probe_target.touch()
    probe_link = tmp_path / "_symlink_probe_link"
    try:
        os.symlink(probe_target, probe_link)
    except OSError as exc:
        pytest.skip(f"symlink creation not permitted on this host: {exc}")
    finally:
        probe_link.unlink(missing_ok=True)
        probe_target.unlink(missing_ok=True)


# ---- Raw-argument spy ------------------------------------------------------


@pytest.fixture
def raw_marker_spy(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Replace ``markers.{set,clear,is}_ignored`` with raw-arg recorders.

    Returns a namespace with three lists: ``set``, ``clear``, ``is_ignored``.
    Each call to a markers function appends the EXACT path argument
    (no resolution, no normalization) to its list.

    Patches at the module level so both ``cli.py`` and ``reconcile.py``
    (which import ``markers`` as a module attribute) flow through the spy.
    """
    set_args: list[Path] = []
    clear_args: list[Path] = []
    is_ignored_args: list[Path] = []

    def _set(p: Path) -> None:
        set_args.append(p)

    def _clear(p: Path) -> None:
        clear_args.append(p)

    def _is_ignored(p: Path) -> bool:
        is_ignored_args.append(p)
        return False  # default: nothing is marked; tests can override per-case

    monkeypatch.setattr(markers, "set_ignored", _set)
    monkeypatch.setattr(markers, "clear_ignored", _clear)
    monkeypatch.setattr(markers, "is_ignored", _is_ignored)
    # reconcile and cli imported `markers` as a module attribute, so the
    # above setattrs are visible through `cli.markers.set_ignored` etc.
    return SimpleNamespace(set=set_args, clear=clear_args, is_ignored=is_ignored_args)


# ---- Dropbox-root staging --------------------------------------------------


@pytest.fixture
def dropbox_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a fake Dropbox root and point ``cli._discover_roots`` at it."""
    root = tmp_path / "Dropbox"
    root.mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])
    return root


@pytest.fixture
def external_dir(tmp_path: Path) -> Path:
    """A directory outside any Dropbox root, for cases #2 and #6b."""
    d = tmp_path / "external"
    d.mkdir()
    return d


# ---- Verb invocation helper ------------------------------------------------


def _invoke(verb: str, path: Path) -> tuple[int, str, str]:
    """Invoke a CLI verb via CliRunner; return (exit_code, stdout, stderr).

    `apply` and `clear` need `--yes` to skip interactive confirmation when
    they would proceed; pass it unconditionally (no-op when the verb
    aborts before the prompt).
    """
    runner = CliRunner()
    args_by_verb = {
        "apply": ["apply", str(path), "--yes"],
        "clear": ["clear", str(path), "--yes", "--force"],
        "list": ["list", str(path)],
        "explain": ["explain", str(path)],
        "check-ignore": ["check-ignore", str(path)],
    }
    result = runner.invoke(cli.main, args_by_verb[verb])
    # Click 8.3+ provides separate .stdout and .stderr attributes.
    return result.exit_code, result.stdout, result.stderr


# Verbs that go through `_validate_target_under_root` or
# `_normalize_under_root(require_exists=True)` and thus reject nonexistent
# paths with "does not exist".
FS_STATE_VERBS = ["apply", "clear", "list"]
# Verbs that go through `_normalize_under_root(require_exists=False)` and
# accept nonexistent paths (rule-lookup semantics).
RULE_LOGIC_VERBS = ["explain", "check-ignore"]
ALL_VERBS = FS_STATE_VERBS + RULE_LOGIC_VERBS


# ---- Case 1: link under Dropbox, target also under Dropbox ----------------


@pytest.mark.parametrize("verb", ALL_VERBS)
def test_case1_link_and_target_both_under_dropbox(
    verb: str,
    dropbox_root: Path,
    raw_marker_spy: SimpleNamespace,
    symlink_capable: None,
) -> None:
    """The verb operates on the link object, not the resolved target."""
    target_file = dropbox_root / "real-file.txt"
    target_file.touch()
    link = dropbox_root / "link-to-file"
    os.symlink(target_file, link)

    exit_code, _stdout, stderr = _invoke(verb, link)

    assert exit_code in (0, 1), f"{verb} exited {exit_code}; stderr={stderr!r}"

    if verb == "apply":
        # apply runs reconcile_subtree → _reconcile_path → markers.set_ignored
        # (rules are empty, so no set_ignored fires — instead is_ignored is
        # queried for the link). Verify the spy saw the LINK path, not the
        # resolved target.
        all_seen = raw_marker_spy.is_ignored + raw_marker_spy.set + raw_marker_spy.clear
        assert link in all_seen, f"apply did not pass link path to markers; spy saw {all_seen}"
        assert target_file not in all_seen, (
            f"apply passed RESOLVED target {target_file} to markers; "
            f"spy saw {all_seen}. This means path.resolve() is still in the code path."
        )
    elif verb in ("clear", "list"):
        # These call _walk_marked_paths(target) which queries is_ignored on
        # the target itself before any walk.
        assert link in raw_marker_spy.is_ignored, (
            f"{verb} did not query is_ignored on link path; "
            f"spy.is_ignored={raw_marker_spy.is_ignored}"
        )
        assert target_file not in raw_marker_spy.is_ignored, (
            f"{verb} queried is_ignored on RESOLVED target {target_file}; "
            f"path.resolve() is still in the code path."
        )
    # explain / check-ignore: no marker calls — rule lookup only.
    # The "operated on link" assertion for these is that exit_code is not
    # 2 (no fatal error from containment) — covered by the assert above.


# ---- Case 2: link under Dropbox, target OUTSIDE Dropbox -------------------


@pytest.mark.parametrize("verb", ALL_VERBS)
def test_case2_link_under_dropbox_target_outside(
    verb: str,
    dropbox_root: Path,
    external_dir: Path,
    raw_marker_spy: SimpleNamespace,
    symlink_capable: None,
) -> None:
    """Link object is in-Dropbox; target lives outside. Containment passes
    on the link's lexical path (was rejected pre-fix because resolve()
    switched containment to the external target)."""
    external_target = external_dir / "external-file.txt"
    external_target.touch()
    link = dropbox_root / "link-to-external"
    os.symlink(external_target, link)

    exit_code, _stdout, stderr = _invoke(verb, link)

    assert exit_code in (0, 1), (
        f"{verb} exited {exit_code} on in-Dropbox link with external target; "
        f"stderr={stderr!r}. "
        f"Expected containment to pass on the link's lexical path."
    )


# ---- Case 3: broken symlink (target nonexistent) --------------------------


@pytest.mark.parametrize("verb", ALL_VERBS)
def test_case3_broken_symlink(
    verb: str,
    dropbox_root: Path,
    raw_marker_spy: SimpleNamespace,
    symlink_capable: None,
) -> None:
    """Link object exists; target does not. ``lexists`` accepts; the four
    verbs must too (apply was the only one that rejected pre-fix)."""
    link = dropbox_root / "broken-link"
    os.symlink(dropbox_root / "nonexistent-target", link)

    exit_code, _stdout, stderr = _invoke(verb, link)

    assert exit_code in (0, 1), (
        f"{verb} exited {exit_code} on broken symlink; stderr={stderr!r}. "
        f"The link object lexists; the verb must accept it."
    )


# ---- Case 4: out-of-Dropbox alias pointing INTO Dropbox -------------------


@pytest.mark.parametrize("verb", ALL_VERBS)
def test_case4_out_of_dropbox_alias_into_dropbox(
    verb: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_marker_spy: SimpleNamespace,
    symlink_capable: None,
) -> None:
    """``/alias → ~/Dropbox``, arg is ``/alias/sub``. Resolved-fallback path:
    unresolved containment fails, resolve() succeeds."""
    real_dropbox = tmp_path / "real-dropbox"
    real_dropbox.mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [real_dropbox])
    sub = real_dropbox / "sub"
    sub.touch()

    alias = tmp_path / "alias"
    os.symlink(real_dropbox, alias)
    alias_arg = alias / "sub"  # /alias/sub; resolves to real_dropbox/sub

    exit_code, _stdout, stderr = _invoke(verb, alias_arg)

    assert exit_code in (0, 1), (
        f"{verb} exited {exit_code} on out-of-Dropbox alias path; "
        f"stderr={stderr!r}. Resolved-fallback should have succeeded."
    )


# ---- Case 5: symlinked ancestor between target and root -------------------


def test_case5_apply_refuses_symlinked_ancestor(
    dropbox_root: Path,
    raw_marker_spy: SimpleNamespace,
    symlink_capable: None,
) -> None:
    """apply refuses (ancestor-orphan guard); clear/list/explain proceed."""
    # Build: dropbox_root/normal/link-dir/sub where link-dir is a symlink.
    normal = dropbox_root / "normal"
    normal.mkdir()
    real_target_dir = dropbox_root / "real-target-dir"
    real_target_dir.mkdir()
    (real_target_dir / "sub").touch()
    link_dir = normal / "link-dir"
    os.symlink(real_target_dir, link_dir)
    arg = link_dir / "sub"

    exit_code, _stdout, stderr = _invoke("apply", arg)

    assert exit_code == 2, f"apply did not refuse; exit={exit_code} stderr={stderr!r}"
    assert "symlinked ancestor" in stderr, (
        f"apply refused but with wrong message; stderr={stderr!r}"
    )


@pytest.mark.parametrize("verb", ["clear", "list", "explain", "check-ignore"])
def test_case5_other_verbs_proceed_through_symlinked_ancestor(
    verb: str,
    dropbox_root: Path,
    raw_marker_spy: SimpleNamespace,
    symlink_capable: None,
) -> None:
    """clear/list/explain do not enforce the ancestor guard."""
    normal = dropbox_root / "normal"
    normal.mkdir()
    real_target_dir = dropbox_root / "real-target-dir"
    real_target_dir.mkdir()
    (real_target_dir / "sub").touch()
    link_dir = normal / "link-dir"
    os.symlink(real_target_dir, link_dir)
    arg = link_dir / "sub"

    exit_code, _stdout, stderr = _invoke(verb, arg)

    assert exit_code in (0, 1), (
        f"{verb} refused symlinked ancestor; exit={exit_code} stderr={stderr!r}. "
        f"Only `apply` enforces the daemon-orphan guard."
    )


# ---- Case 6a: nonexistent path under Dropbox ------------------------------


@pytest.mark.parametrize("verb", FS_STATE_VERBS)
def test_case6a_nonexistent_under_dropbox_filesystem_verbs(
    verb: str, dropbox_root: Path, raw_marker_spy: SimpleNamespace
) -> None:
    """apply, clear, list reject nonexistent paths under Dropbox."""
    arg = dropbox_root / "does-not-exist.txt"

    exit_code, _stdout, stderr = _invoke(verb, arg)

    assert exit_code == 2, f"{verb} accepted nonexistent path; stderr={stderr!r}"
    assert "does not exist" in stderr, f"{verb} rejected with wrong message; stderr={stderr!r}"


@pytest.mark.parametrize("verb", RULE_LOGIC_VERBS)
def test_case6a_nonexistent_under_dropbox_rule_logic_verbs(
    verb: str, dropbox_root: Path, raw_marker_spy: SimpleNamespace
) -> None:
    """explain and check-ignore accept nonexistent paths and return exit 1
    (no match) — rule-lookup verbs answer hypotheticals."""
    arg = dropbox_root / "does-not-exist.txt"

    exit_code, _stdout, _stderr = _invoke(verb, arg)

    assert exit_code == 1, (
        f"{verb} did not return exit 1 (no match) on nonexistent path; exit={exit_code}"
    )


# ---- Case 6b: nonexistent path OUTSIDE Dropbox ----------------------------


@pytest.mark.parametrize("verb", FS_STATE_VERBS)
def test_case6b_nonexistent_outside_dropbox_filesystem_verbs(
    verb: str,
    dropbox_root: Path,
    external_dir: Path,
    raw_marker_spy: SimpleNamespace,
) -> None:
    """lexist check fires before containment, so the 'does not exist'
    message wins even though the path is also outside Dropbox."""
    arg = external_dir / "does-not-exist.txt"

    exit_code, _stdout, stderr = _invoke(verb, arg)

    assert exit_code == 2, f"{verb} accepted nonexistent path; stderr={stderr!r}"
    assert "does not exist" in stderr, (
        f"{verb} rejected with wrong message (expected 'does not exist' "
        f"because lexist is checked before containment); stderr={stderr!r}"
    )


@pytest.mark.parametrize("verb", RULE_LOGIC_VERBS)
def test_case6b_nonexistent_outside_dropbox_rule_logic_verbs(
    verb: str,
    dropbox_root: Path,
    external_dir: Path,
    raw_marker_spy: SimpleNamespace,
) -> None:
    """explain/check-ignore skip the lexist check, hit containment, fail."""
    arg = external_dir / "does-not-exist.txt"

    exit_code, _stdout, stderr = _invoke(verb, arg)

    assert exit_code == 2, f"{verb} accepted out-of-Dropbox path; stderr={stderr!r}"
    assert "not under any Dropbox root" in stderr, (
        f"{verb} rejected with wrong message; stderr={stderr!r}"
    )


# ---- Case 7: EXISTING path outside Dropbox --------------------------------


@pytest.mark.parametrize("verb", ALL_VERBS)
def test_case7_existing_outside_dropbox(
    verb: str,
    dropbox_root: Path,
    external_dir: Path,
    raw_marker_spy: SimpleNamespace,
) -> None:
    """All four verbs reject existing paths outside Dropbox with the
    containment message."""
    arg = external_dir / "real-but-outside.txt"
    arg.touch()

    exit_code, _stdout, stderr = _invoke(verb, arg)

    assert exit_code == 2, f"{verb} accepted out-of-Dropbox path; stderr={stderr!r}"
    assert "not under any Dropbox root" in stderr, (
        f"{verb} rejected with wrong message; stderr={stderr!r}"
    )


# ---- Case 8: apply on symlink-to-directory does not descend ---------------


def test_case8_apply_symlink_to_directory_does_not_descend(
    dropbox_root: Path,
    raw_marker_spy: SimpleNamespace,
    symlink_capable: None,
) -> None:
    """apply with a symlink-to-directory argument processes the link's own
    marker but does NOT walk into the link target's tree.

    Without the ``descend=False`` guard, ``os.walk(link, followlinks=False)``
    follows the link at the walk root (per the CLAUDE.md gotcha) and apply
    would write markers under paths the daemon's own walk (which starts
    from the Dropbox root with ``followlinks=False``) would never reach,
    stranding orphan markers. Partial fix for backlog #104 covering the
    apply mark-write surface.
    """
    # Create a directory and a file in it; the file would be matched by
    # a top-level rule.
    real_target_dir = dropbox_root / "real-target-dir"
    real_target_dir.mkdir()
    inner_file = real_target_dir / "should-not-be-marked.txt"
    inner_file.touch()

    # Plant a rule that would mark `should-not-be-marked.txt` if walked.
    rule_file = dropbox_root / ".dropboxignore"
    rule_file.write_text("should-not-be-marked.txt\n")

    # Symlink under Dropbox pointing at the directory.
    link = dropbox_root / "link-to-dir"
    os.symlink(real_target_dir, link)

    exit_code, _stdout, stderr = _invoke("apply", link)

    assert exit_code == 0, f"apply failed: exit={exit_code} stderr={stderr!r}"

    # No marker writes should target paths reached THROUGH the link.
    paths_through_link = [link / "should-not-be-marked.txt"]
    for bad_path in paths_through_link:
        assert bad_path not in raw_marker_spy.set, (
            f"apply descended into symlink target via the link; "
            f"spy.set contains {bad_path}. The descend=False guard is missing."
        )

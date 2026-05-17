"""Cross-cutting symlink-correctness suite for path-taking CLI verbs.

Verifies that ``apply``, ``clear``, ``list``, ``explain`` and ``check-ignore``
operate on the symlink OBJECT (not the resolved target) when handed a
symlink argument. ``path.resolve()`` would operate on the link target;
the verbs must instead keep the lexical link path. (``explain`` and
``check-ignore`` share the ``_explain`` body, so this is really four
code paths surfacing as five CLI verbs.)

Why a raw-argument spy instead of ``fake_markers``: ``FakeMarkers`` in
``tests/conftest.py`` calls ``path.resolve()`` inside ``is_ignored``,
``set_ignored``, ``clear_ignored`` before recording. That resolution
erases the distinction between "CLI passed the link" and "CLI passed the
target," which is exactly the surface under test. The spy records the
raw argument the CLI hands to the markers module.

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
    """A directory outside any Dropbox root."""
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
    on the link's lexical path; a `resolve()`-based containment check
    would switch to the external target and reject."""
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
    verbs must too."""
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


@pytest.mark.parametrize("verb", FS_STATE_VERBS)
def test_case5_filesystem_verbs_refuse_symlinked_ancestor(
    verb: str,
    dropbox_root: Path,
    raw_marker_spy: SimpleNamespace,
    symlink_capable: None,
) -> None:
    """apply/clear/list refuse symlinked-ancestor paths.

    Two failure modes the guard prevents:
    - apply: marker written under a symlinked ancestor would be stranded
      because the daemon's `followlinks=False` walk can never reach it.
    - clear/list: walking through a symlinked ancestor enumerates or
      mutates xattrs in the link target's tree, potentially outside the
      watched Dropbox root.
    """
    # Build: dropbox_root/normal/link-dir/sub where link-dir is a symlink.
    normal = dropbox_root / "normal"
    normal.mkdir()
    real_target_dir = dropbox_root / "real-target-dir"
    real_target_dir.mkdir()
    (real_target_dir / "sub").touch()
    link_dir = normal / "link-dir"
    os.symlink(real_target_dir, link_dir)
    arg = link_dir / "sub"

    exit_code, _stdout, stderr = _invoke(verb, arg)

    assert exit_code == 2, f"{verb} did not refuse; exit={exit_code} stderr={stderr!r}"
    assert "symlinked ancestor" in stderr, (
        f"{verb} refused but with wrong message; stderr={stderr!r}"
    )


@pytest.mark.parametrize("verb", RULE_LOGIC_VERBS)
def test_case5_rule_logic_verbs_proceed_through_symlinked_ancestor(
    verb: str,
    dropbox_root: Path,
    raw_marker_spy: SimpleNamespace,
    symlink_capable: None,
) -> None:
    """explain/check-ignore proceed through symlinked ancestors.

    Rule-logic verbs are pure lexical lookups against the rule set —
    they don't walk, don't enumerate, don't mutate markers. A symlinked
    ancestor in the queried path can't leak out-of-scope state because
    no filesystem operations fire on the link's target tree.
    """
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
        f"Rule-logic verbs do not enforce the ancestor guard."
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
    follows the link at the walk root (``followlinks=False`` only suppresses
    following at non-root frames) and apply would write markers under paths
    the daemon's own walk (which starts from the Dropbox root with
    ``followlinks=False``) would never reach, stranding orphan markers.
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


# ---- Case 9: clear/list on symlink-to-directory do not descend ------------


@pytest.mark.parametrize("verb", ["clear", "list"])
def test_case9_clear_list_symlink_to_directory_does_not_descend(
    verb: str,
    dropbox_root: Path,
    raw_marker_spy: SimpleNamespace,
    symlink_capable: None,
) -> None:
    """clear/list with a symlink-to-directory argument check only the link's
    own marker; they don't walk into the link target's tree.

    ``_walk_marked_paths`` short-circuits when ``target.is_symlink()`` —
    ``os.walk(target, followlinks=False)`` still follows the link at the
    walk root (``followlinks=False`` only suppresses following at non-root
    frames; same shape as apply's walk and the daemon's per-subdir fan-out).
    Without the guard,
    `dbxignore clear ~/Dropbox/link-to-external` would enumerate and
    clear markers in the link's external target tree.
    """
    real_target_dir = dropbox_root / "real-target-dir"
    real_target_dir.mkdir()
    (real_target_dir / "inner-file-1.txt").touch()
    (real_target_dir / "inner-file-2.txt").touch()

    link = dropbox_root / "link-to-dir"
    os.symlink(real_target_dir, link)

    exit_code, _stdout, stderr = _invoke(verb, link)

    assert exit_code == 0, f"{verb} failed: exit={exit_code} stderr={stderr!r}"

    # The spy may have queried is_ignored on `link` itself. But no query
    # should target a path reached BY DESCENDING THROUGH the link.
    queries_through_link = [
        p
        for p in raw_marker_spy.is_ignored
        if p != link and any(parent == link for parent in p.parents)
    ]
    assert not queries_through_link, (
        f"{verb} descended into symlink target via the link; is_ignored "
        f"was queried on {queries_through_link}. The is_symlink() guard "
        f"at _walk_marked_paths entry is missing."
    )


# ---- Case 10: `..` after a symlinked component ----------------------------
#
# `os.path.normpath` collapses `link/..` lexically (to nothing), but the
# filesystem would resolve it to `<target-of-link>/..`. The two interpretations
# diverge, so `_normalize_under_root` rejects up-front. Tests cover both
# filesystem-state and rule-logic verbs since the guard lives in the shared
# helper used by all path-taking verbs.


@pytest.mark.parametrize("verb", ALL_VERBS)
def test_case10a_dotdot_after_symlink_rejected(
    verb: str,
    dropbox_root: Path,
    raw_marker_spy: SimpleNamespace,
    symlink_capable: None,
) -> None:
    """``apply link/../file`` and the four other path-taking verbs must
    refuse to proceed when ``..`` follows a symlinked path component.
    Without the guard the verb would silently operate on the lexically-
    collapsed path (``Dropbox/file``) instead of the filesystem-true path
    (``<target-of-link>/../file``)."""
    target_dir = dropbox_root / "target-dir"
    target_dir.mkdir()
    (target_dir / "file.txt").touch()
    link = dropbox_root / "link-to-dir"
    os.symlink(target_dir, link)
    # The lexically-collapsed path must also exist so we know the verb
    # would otherwise have a real candidate to operate on.
    (dropbox_root / "file.txt").touch()

    arg = link / ".." / "file.txt"
    exit_code, _stdout, stderr = _invoke(verb, arg)

    assert exit_code == 2, f"{verb} should refuse; got exit={exit_code} stderr={stderr!r}"
    assert "symlinked component" in stderr
    # No markers were queried or mutated — the verb bailed before reaching
    # the marker layer.
    seen = raw_marker_spy.set + raw_marker_spy.clear + raw_marker_spy.is_ignored
    assert not seen, f"{verb} reached markers despite rejection; spy saw {seen}"


@pytest.mark.parametrize("verb", RULE_LOGIC_VERBS)
def test_case10b_dotdot_before_symlink_accepted_rule_logic_verbs(
    verb: str,
    dropbox_root: Path,
    raw_marker_spy: SimpleNamespace,
    symlink_capable: None,
) -> None:
    """``regular-dir/../link/file.txt`` must NOT trip the new ``..``-after-
    symlink guard: the ``..`` cancels a non-symlinked segment
    (``regular-dir``) before any symlink enters the prefix, so lexical
    normalization and filesystem resolution agree.

    Parametrized over RULE_LOGIC_VERBS only because the FS-state verbs
    (``apply``/``clear``/``list``) reject this path shape via the
    PRE-EXISTING symlinked-ancestor guard in ``_validate_target_under_root``
    (after ``..`` cancels ``regular-dir``, the result still has
    ``link-to-dir`` as a symlinked ancestor). That older guard's rejection
    is correct for the path shape; this test only needs to verify the new
    guard doesn't fire on ``..``-before-symlink, which is observable on
    the rule-logic verbs that skip the ancestor guard."""
    regular_dir = dropbox_root / "regular-dir"
    regular_dir.mkdir()
    target_dir = dropbox_root / "target-dir"
    target_dir.mkdir()
    target_file = target_dir / "file.txt"
    target_file.touch()
    link = dropbox_root / "link-to-dir"
    os.symlink(target_dir, link)

    arg = regular_dir / ".." / "link-to-dir" / "file.txt"
    exit_code, _stdout, stderr = _invoke(verb, arg)

    # explain / check-ignore return 0-or-1 (rule-driven verdict). Either
    # is acceptable here — the assertion is "the new guard didn't reject".
    assert exit_code in (0, 1), f"{verb} unexpectedly refused; stderr={stderr!r}"
    assert "symlinked component" not in stderr


@pytest.mark.parametrize("verb", FS_STATE_VERBS)
def test_case10c_plain_dotdot_no_symlink_still_works(
    verb: str,
    dropbox_root: Path,
    raw_marker_spy: SimpleNamespace,
) -> None:
    """Sanity check: a path containing ``..`` but no symlinks anywhere
    must continue to work as before. Pins that the new guard doesn't
    over-fire on the common case where ``..`` is just lexical sugar."""
    parent_dir = dropbox_root / "parent-dir"
    parent_dir.mkdir()
    sibling_dir = dropbox_root / "sibling-dir"
    sibling_dir.mkdir()
    sibling_file = sibling_dir / "file.txt"
    sibling_file.touch()

    # parent-dir/../sibling-dir/file.txt — normpath collapses to
    # Dropbox/sibling-dir/file.txt, which exists.
    arg = parent_dir / ".." / "sibling-dir" / "file.txt"
    exit_code, _stdout, stderr = _invoke(verb, arg)

    assert exit_code == 0, f"{verb} refused plain ..-path; stderr={stderr!r}"
    assert "symlinked component" not in stderr


@pytest.mark.parametrize("verb", ALL_VERBS)
def test_case10d_dotdot_after_alias_uses_resolved_fallback(
    verb: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_marker_spy: SimpleNamespace,
    symlink_capable: None,
) -> None:
    """An out-of-Dropbox alias linking INTO Dropbox combined with ``..`` in the
    path's tail must NOT trip the ``..``-after-symlink guard: the unresolved
    path is not under any discovered Dropbox root (the alias is outside), so
    ``_normalize_under_root`` falls through to ``path.resolve()`` — which is
    filesystem-true and unambiguous. The guard applies only to paths that
    would actually be handled through the lexical in-root branch.

    Without this scoping, an alias like ``/alias → ~/Dropbox`` would set
    ``seen_symlink`` on ``alias``, then the trailing ``..`` would trigger a
    spurious rejection even though resolve() handles the path correctly.
    Same scenario as ``test_case4_out_of_dropbox_alias_into_dropbox`` but
    with ``..`` in the tail."""
    real_dropbox = tmp_path / "real-dropbox"
    real_dropbox.mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [real_dropbox])
    sub = real_dropbox / "sub"
    sub.mkdir()
    target_file = real_dropbox / "file.txt"
    target_file.touch()

    alias = tmp_path / "alias"
    os.symlink(real_dropbox, alias)
    alias_arg = alias / "sub" / ".." / "file.txt"  # resolves to real_dropbox/file.txt

    exit_code, _stdout, stderr = _invoke(verb, alias_arg)

    assert exit_code in (0, 1), (
        f"{verb} refused alias path with ..; stderr={stderr!r}. "
        f"Resolved-fallback should have succeeded without firing the guard."
    )
    assert "symlinked component" not in stderr

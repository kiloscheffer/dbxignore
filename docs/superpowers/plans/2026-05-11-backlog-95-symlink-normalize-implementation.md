# #95 path-taking verbs preserve symlink object — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `apply`, `clear`, `list`, and `explain`/`check-ignore` from calling `Path.resolve()` on their path argument so containment, marker reads/writes, and rule lookups see the symlink object instead of its target. Fix three sub-bugs in `apply` that fall out of the same code path (broken-target symlink rejection; missing symlinked-ancestor guard; outdated `.exists()` check). `ignore`/`unignore` already do this — share the helper with them.

**Architecture:** Split the existing `_validate_target_under_root(path)` helper into two layers in `src/dbxignore/cli.py`: a thin core `_normalize_under_root(path, *, require_exists)` that does non-following normalization + Dropbox-root containment, and the existing `_validate_target_under_root` becomes a thin wrapper that adds `require_exists=True` + symlinked-ancestor rejection on top. Per-verb wiring:
- `ignore`, `unignore`, `apply` → `_validate_target_under_root` (full validator; `apply` migrates from `path.resolve()+exists()`)
- `clear`, `list` → `_normalize_under_root(require_exists=True)` (lexist gate + containment; no ancestor reject)
- `explain`, `check-ignore` → `_normalize_under_root(require_exists=False)` (containment only; rule-lookup verbs accept hypothetical paths)

**Spec:** `docs/superpowers/specs/2026-05-11-backlog-95-symlink-normalize-design.md` — read it before starting. This plan operationalizes that design; spec is source of truth on policy decisions.

**Tech Stack:** Python 3.11+, `click` / `rich_click`, `pytest` (with `CliRunner`), `uv` for env management. Existing fixtures: `FakeMarkers` + `fake_markers` + `write_file` in `tests/conftest.py`. **Do not use `fake_markers` for the new symlink suite** — it `.resolve()`s every path argument internally and erases the bug surface this PR is about. The new suite uses a raw-argument spy (Task 2).

---

## File structure

**Create:**
- `tests/test_cli_symlink_path_args.py` — cross-cutting symlink-correctness suite for the four affected verbs.

**Modify:**
- `src/dbxignore/cli.py` — extract `_normalize_under_root`; refactor `_validate_target_under_root` to wrap it; rewire `apply`, `clear`, `list_ignored`, `_explain`.
- `src/dbxignore/reconcile.py` — update `reconcile_subtree` docstring (no behavior change).
- `scripts/_phase_extended_cli.sh` — one new Phase 4.5 case (Linux + macOS shared helper).
- `scripts/manual-test-windows.ps1` — same case mirrored for PowerShell 7+.
- `BACKLOG.md` — `Status: RESOLVED` marker on item #95; file new item #104 (walk-entry symlink-descent); update Open summary line.

**Total estimated change:** ~40 LOC in `cli.py` (helper extract + 4 rewires); ~250 LOC of tests; ~30 LOC of manual-test scripts; ~25 LOC of BACKLOG.

---

## Task 1: Extract `_normalize_under_root`; refactor `_validate_target_under_root` to wrap it

Pure refactor — no behavior change. All existing tests (ignore/unignore tests in particular) must remain green.

**Files:**
- Modify: `src/dbxignore/cli.py:105-178`

- [ ] **Step 1: Read the existing `_validate_target_under_root`**

Open `src/dbxignore/cli.py` and read lines 105-178. Note the five sections:
1. `target_unresolved = Path(os.path.normpath(path.absolute()))` (135)
2. `os.path.lexists` check + exit 2 (141-143)
3. `_discover_roots()` + exit 2 (144-147)
4. Containment with resolved-fallback (148-162)
5. Symlinked-ancestor rejection (163-177)

Sections 1+3+4 become `_normalize_under_root`. Section 2 is gated behind `require_exists`. Section 5 stays in `_validate_target_under_root`.

- [ ] **Step 2: Replace the function body with the two-layer version**

Edit `src/dbxignore/cli.py:105-178`. Replace the entire `_validate_target_under_root` block (the `def` line, the docstring, and the body) with:

```python
def _normalize_under_root(
    path: Path, *, require_exists: bool
) -> tuple[Path, Path, list[Path]]:
    """Normalize ``path`` (symlink-preserving) and verify Dropbox-root containment.

    Returns ``(target, root, discovered)`` where ``target`` is the
    normalized absolute path (NOT resolved — symlinks preserved),
    ``root`` is the Dropbox root containing it, and ``discovered`` is the
    full list of roots. Exits 2 with a user-friendly stderr message if
    any check fails.

    ``path.absolute()`` is used instead of ``path.resolve()`` so that
    symlinks are preserved — markers and rules apply to the link object
    itself, not the link's target. ``os.path.normpath`` folds ``..`` /
    ``.`` segments without following symlinks.

    When ``require_exists`` is True, ``os.path.lexists(target)`` is
    checked before discovery — broken/missing symlinks still pass
    (the link object lexists even if its target doesn't). Callers that
    answer rule-logic questions about hypothetical paths (``explain``,
    ``check-ignore``) pass ``require_exists=False``.

    If the unresolved path is not under any Dropbox root, the
    containment check falls back to the resolved path — this handles
    out-of-Dropbox symlink aliases that reach into Dropbox. For example,
    ``/alias/Dropbox/file`` where ``/alias`` symlinks to the actual
    Dropbox root fails the unresolved containment (lexical prefix
    mismatch), then succeeds via ``path.resolve()``.

    Used directly by ``clear``, ``list``, and ``_explain``; wrapped by
    ``_validate_target_under_root`` for the write-side verbs.
    """
    target_unresolved = Path(os.path.normpath(path.absolute()))
    if require_exists and not os.path.lexists(target_unresolved):
        click.echo(f"Path {path} does not exist.", err=True)
        sys.exit(2)
    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found. Is Dropbox installed?", err=True)
        sys.exit(2)
    root = find_containing(target_unresolved, discovered)
    if root is not None:
        target = target_unresolved
    else:
        target_resolved = path.resolve()
        root = find_containing(target_resolved, discovered)
        if root is None:
            click.echo(f"Path {path} is not under any Dropbox root.", err=True)
            sys.exit(2)
        target = target_resolved
    return target, root, discovered


def _validate_target_under_root(path: Path) -> tuple[Path, Path, list[Path]]:
    """Normalize, verify existence and Dropbox-root containment, and
    reject symlinked ancestors between target and root.

    Wraps ``_normalize_under_root(path, require_exists=True)`` and adds
    the daemon-orphan guard for write-side verbs: a marker written under
    a symlinked ancestor would never be reconciled by the daemon's
    ``followlinks=False`` walk, so the verb refuses up-front. The target
    itself being a symlink is fine — that's the intended use case
    (markers attach to the link object on macOS/Windows; Linux rejects
    via ``_reconcile_path``'s ``PermissionError`` arm with a WARNING).

    Used by ``ignore``, ``unignore``, and ``apply``.
    """
    target, root, discovered = _normalize_under_root(path, require_exists=True)
    for ancestor in target.parents:
        if ancestor == root:
            break
        if ancestor.is_symlink():
            click.echo(
                f"error: {path} has a symlinked ancestor {ancestor}; "
                f"the daemon walks with followlinks=False and would never "
                f"reconcile this path. Operate on the symlink itself instead.",
                err=True,
            )
            sys.exit(2)
    return target, root, discovered
```

- [ ] **Step 3: Run the full test suite to confirm no regression**

Run: `uv run python -m pytest -x`

Expected: PASS (all existing tests). The refactor is internal — `_validate_target_under_root`'s observable behavior is unchanged.

If any test fails, stop and investigate — the refactor introduced a bug. Likely candidates: missed an `os.path.lexists` call, dropped a `discovered` return, or inverted the containment-fallback order.

- [ ] **Step 4: Run ruff format + check**

Run: `uv run ruff format src/dbxignore/cli.py && uv run ruff check src/dbxignore/cli.py`

Expected: clean exit.

- [ ] **Step 5: No commit yet**

Stay on the working branch. Next tasks will build on this refactor; we commit at the end. (CLAUDE.md "Split commits along revertability lines" — the helper extraction and the per-verb rewires share one design's revertability boundary per the spec.)

---

## Task 2: Create the new symlink-correctness test suite (full matrix)

Write the entire `tests/test_cli_symlink_path_args.py` up front. Many tests will fail until the per-verb rewires (Tasks 3-6) land. That progressive-pass shape IS the TDD signal for this refactor.

**Files:**
- Create: `tests/test_cli_symlink_path_args.py`

- [ ] **Step 1: Write the test file**

Create `tests/test_cli_symlink_path_args.py`:

```python
"""Cross-cutting symlink-correctness suite for path-taking CLI verbs (item #95).

Verifies that ``apply``, ``clear``, ``list``, ``explain`` and ``check-ignore``
operate on the symlink OBJECT (not the resolved target) when handed a
symlink argument. Pre-fix, all four called ``path.resolve()`` and operated
on the link target.

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

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from dbxignore import cli, markers, reconcile

if TYPE_CHECKING:
    from collections.abc import Iterator


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
    runner = CliRunner(mix_stderr=False)
    args_by_verb = {
        "apply": ["apply", str(path), "--yes"],
        "clear": ["clear", str(path), "--yes", "--force"],
        "list": ["list", str(path)],
        "explain": ["explain", str(path)],
        "check-ignore": ["check-ignore", str(path)],
    }
    result = runner.invoke(cli.main, args_by_verb[verb])
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
        assert link in all_seen, (
            f"apply did not pass link path to markers; spy saw {all_seen}"
        )
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
    assert "does not exist" in stderr, (
        f"{verb} rejected with wrong message; stderr={stderr!r}"
    )


@pytest.mark.parametrize("verb", RULE_LOGIC_VERBS)
def test_case6a_nonexistent_under_dropbox_rule_logic_verbs(
    verb: str, dropbox_root: Path, raw_marker_spy: SimpleNamespace
) -> None:
    """explain and check-ignore accept nonexistent paths and return exit 1
    (no match) — rule-lookup verbs answer hypotheticals."""
    arg = dropbox_root / "does-not-exist.txt"

    exit_code, _stdout, _stderr = _invoke(verb, arg)

    assert exit_code == 1, (
        f"{verb} did not return exit 1 (no match) on nonexistent path; "
        f"exit={exit_code}"
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
```

- [ ] **Step 2: Run the new tests; expect many failures**

Run: `uv run python -m pytest tests/test_cli_symlink_path_args.py -v`

Expected: many failures. Cases 1, 2, 3, 5 (apply arm), and 6a (filesystem verbs) all depend on the per-verb rewires (Tasks 3-6). Cases 4 may already pass for some verbs depending on how the existing `path.resolve()` happens to land. Don't try to fix anything yet — the failures are the TDD signal.

Record the baseline by running once and noting which tests pass vs. fail. After Tasks 3-6, this same command should be all-green.

- [ ] **Step 3: Run ruff format + check on the new file**

Run: `uv run ruff format tests/test_cli_symlink_path_args.py && uv run ruff check tests/test_cli_symlink_path_args.py`

Expected: clean exit.

---

## Task 3: Rewire `apply` to use `_validate_target_under_root`

Three sub-bugs fix in one rewire: switches from `path.resolve()` to non-following normalization; switches from `exists()` to `lexists` (broken-symlink-target acceptance); gains symlinked-ancestor rejection.

**Files:**
- Modify: `src/dbxignore/cli.py:596-614`

- [ ] **Step 1: Read the current `apply` body**

Open `src/dbxignore/cli.py:596-614`. Note the current shape:

```python
    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found. Is Dropbox installed?", err=True)
        sys.exit(2)

    cache = _load_cache(discovered)

    if path is None:
        targets: list[tuple[Path, Path]] = [(r, r) for r in discovered]
    else:
        resolved = path.resolve()
        if not resolved.exists():
            click.echo(f"Path {path} does not exist.", err=True)
            sys.exit(2)
        matched_root = find_containing(resolved, discovered)
        if matched_root is None:
            click.echo(f"Path {path} is not under any Dropbox root.", err=True)
            sys.exit(2)
        targets = [(matched_root, resolved)]
```

The `path is None` branch (whole-tree apply) stays as-is. Only the `else` branch changes.

- [ ] **Step 2: Replace the `else` branch with `_validate_target_under_root`**

Use the `Edit` tool. `old_string`:

```python
    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found. Is Dropbox installed?", err=True)
        sys.exit(2)

    cache = _load_cache(discovered)

    if path is None:
        targets: list[tuple[Path, Path]] = [(r, r) for r in discovered]
    else:
        resolved = path.resolve()
        if not resolved.exists():
            click.echo(f"Path {path} does not exist.", err=True)
            sys.exit(2)
        matched_root = find_containing(resolved, discovered)
        if matched_root is None:
            click.echo(f"Path {path} is not under any Dropbox root.", err=True)
            sys.exit(2)
        targets = [(matched_root, resolved)]
```

`new_string`:

```python
    if path is None:
        discovered = _discover_roots()
        if not discovered:
            click.echo("No Dropbox roots found. Is Dropbox installed?", err=True)
            sys.exit(2)
        targets: list[tuple[Path, Path]] = [(r, r) for r in discovered]
    else:
        target, root, discovered = _validate_target_under_root(path)
        targets = [(root, target)]

    cache = _load_cache(discovered)
```

Note: `cache = _load_cache(discovered)` moves below the path-branch because the whole-tree arm and the per-path arm both need `discovered`, and we want to call `_validate_target_under_root` once (it discovers internally). The whole-tree arm keeps its inline `_discover_roots()` call so the empty-roots check fires.

- [ ] **Step 3: Run apply-related tests**

Run: `uv run python -m pytest tests/test_cli_apply.py tests/test_cli_symlink_path_args.py -v -k "apply or case"`

Expected: existing `tests/test_cli_apply.py` PASS (no regression); `tests/test_cli_symlink_path_args.py` cases 1-5 (the `apply` arm) and 6a/6b/7 (the `apply` arm) PASS.

If any existing apply test fails, the most likely cause is a test that asserts the exact `targets` shape or expects `resolved` to be a key elsewhere — check the assertion and verify the new shape is equivalent.

- [ ] **Step 4: Don't commit yet**

Continue with Task 4.

---

## Task 4: Rewire `clear` to use `_normalize_under_root(require_exists=True)`

**Files:**
- Modify: `src/dbxignore/cli.py:850-862`

- [ ] **Step 1: Read the current `clear` body**

Open `src/dbxignore/cli.py:850-862`. Current shape:

```python
    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found.", err=True)
        sys.exit(2)

    if path is None:
        targets = discovered
    else:
        target = path.resolve()
        if find_containing(target, discovered) is None:
            click.echo(f"Path {path} is not under any Dropbox root.", err=True)
            sys.exit(2)
        targets = [target]
```

- [ ] **Step 2: Replace the path-branch**

Use `Edit`. `old_string`:

```python
    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found.", err=True)
        sys.exit(2)

    if path is None:
        targets = discovered
    else:
        target = path.resolve()
        if find_containing(target, discovered) is None:
            click.echo(f"Path {path} is not under any Dropbox root.", err=True)
            sys.exit(2)
        targets = [target]
```

`new_string`:

```python
    if path is None:
        discovered = _discover_roots()
        if not discovered:
            click.echo("No Dropbox roots found.", err=True)
            sys.exit(2)
        targets = discovered
    else:
        target, _root, _discovered = _normalize_under_root(path, require_exists=True)
        targets = [target]
```

- [ ] **Step 3: Run clear-related tests**

Run: `uv run python -m pytest tests/test_cli_clear.py tests/test_cli_symlink_path_args.py -v -k "clear or case"`

Expected: existing clear tests PASS; symlink suite's `clear` arm in cases 1-7 PASS.

---

## Task 5: Rewire `list_ignored` to use `_normalize_under_root(require_exists=True)`

Same shape as Task 4.

**Files:**
- Modify: `src/dbxignore/cli.py:1306-1318`

- [ ] **Step 1: Read the current `list_ignored` body**

Open `src/dbxignore/cli.py:1306-1318`. Current shape:

```python
    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found.", err=True)
        sys.exit(2)

    if path is None:
        targets = discovered
    else:
        target = path.resolve()
        if find_containing(target, discovered) is None:
            click.echo(f"Path {path} is not under any Dropbox root.", err=True)
            sys.exit(2)
        targets = [target]
```

- [ ] **Step 2: Replace the path-branch**

Use `Edit`. `old_string`:

```python
    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found.", err=True)
        sys.exit(2)

    if path is None:
        targets = discovered
    else:
        target = path.resolve()
        if find_containing(target, discovered) is None:
            click.echo(f"Path {path} is not under any Dropbox root.", err=True)
            sys.exit(2)
        targets = [target]
```

`new_string`:

```python
    if path is None:
        discovered = _discover_roots()
        if not discovered:
            click.echo("No Dropbox roots found.", err=True)
            sys.exit(2)
        targets = discovered
    else:
        target, _root, _discovered = _normalize_under_root(path, require_exists=True)
        targets = [target]
```

- [ ] **Step 3: Run list-related tests**

Run: `uv run python -m pytest tests/test_cli_status_list_explain.py tests/test_cli_symlink_path_args.py -v -k "list or case"`

Expected: existing list tests PASS; symlink suite's `list` arm in cases 1-7 PASS.

---

## Task 6: Rewire `_explain` to use `_normalize_under_root(require_exists=False)`

`_explain` is shared by `explain` and `check-ignore`. Rule-logic verbs accept nonexistent paths.

**Files:**
- Modify: `src/dbxignore/cli.py:1339-1346`

- [ ] **Step 1: Read the current `_explain` body**

Open `src/dbxignore/cli.py:1339-1346`. Current shape:

```python
    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found.", err=True)
        return 2

    cache = _load_cache(discovered)
    resolved = path.resolve()
    is_ignored = cache.match(resolved)
```

- [ ] **Step 2: Replace the discovery + resolve block**

Use `Edit`. `old_string`:

```python
    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found.", err=True)
        return 2

    cache = _load_cache(discovered)
    resolved = path.resolve()
    is_ignored = cache.match(resolved)
```

`new_string`:

```python
    target, _root, discovered = _normalize_under_root(path, require_exists=False)
    cache = _load_cache(discovered)
    is_ignored = cache.match(target)
```

- [ ] **Step 3: Replace remaining `resolved` references in `_explain`**

Read `src/dbxignore/cli.py:1349-1368` (the post-fix body that uses `resolved`). The variable `resolved` is referenced at line 1349 (`matches = cache.explain(resolved)`). Edit to use `target` instead.

Use `Edit`. `old_string`:

```python
        matches = cache.explain(resolved)
```

`new_string`:

```python
        matches = cache.explain(target)
```

- [ ] **Step 4: Note the exit-code change for `_explain`**

`_normalize_under_root` calls `sys.exit(2)` directly (matching the existing `_validate_target_under_root` convention). `_explain` previously `return 2` on the no-roots arm, but with the new helper, fatal errors raise `SystemExit(2)` rather than returning 2. The `explain` and `check_ignore` commands wrap `_explain` in `sys.exit(_explain(...))`, so the visible effect is identical: exit code 2 on fatal error.

Tests that monkeypatch `cli._discover_roots` to return `[]` and call `_explain` directly (expecting a `2` return value) need updating. Check `tests/test_cli_status_list_explain.py`:

Run: `uv run python -m pytest tests/test_cli_status_list_explain.py -v -k "explain or check_ignore"`

Expected: most PASS. If any test calls `_explain` directly (not through `runner.invoke`) and asserts `result == 2`, update it to use `pytest.raises(SystemExit)` with `.value.code == 2`. Adjust as needed.

- [ ] **Step 5: Run the full symlink suite to verify all cases pass**

Run: `uv run python -m pytest tests/test_cli_symlink_path_args.py -v`

Expected: ALL tests PASS.

If any fail at this point, the root cause is most likely a test-fixture / monkeypatch ordering issue rather than a missing rewire — re-check the `raw_marker_spy` fixture's patching order vs. `_discover_roots` patching.

---

## Task 7: Update `reconcile_subtree` docstring

Plan-level fix from the spec review: the existing docstring says `root` and `subdir` "MUST be absolute and pre-resolved by the caller." After this PR, `apply <path>` passes a symlink-preserving absolute path (NOT resolved) through to `reconcile_subtree` via `_run_apply_pass`. The code path works (containment is purely lexical), but the comment becomes misleading.

**Files:**
- Modify: `src/dbxignore/reconcile.py:49-53`

- [ ] **Step 1: Read the current docstring**

Open `src/dbxignore/reconcile.py:38-76`. The relevant paragraph is lines 49-53:

```python
    Both ``root`` and ``subdir`` MUST be absolute and pre-resolved by the
    caller — resolution is the CLI/daemon boundary's responsibility (see
    CLAUDE.md "Resolve at the CLI/daemon boundary, never inside the cache
    or markers layer"). ``Path.resolve()`` on Windows is a per-call
    syscall that dominated sweep wall-clock before being hoisted.
```

- [ ] **Step 2: Replace with the updated wording**

Use `Edit`. `old_string`:

```python
    Both ``root`` and ``subdir`` MUST be absolute and pre-resolved by the
    caller — resolution is the CLI/daemon boundary's responsibility (see
    CLAUDE.md "Resolve at the CLI/daemon boundary, never inside the cache
    or markers layer"). ``Path.resolve()`` on Windows is a per-call
    syscall that dominated sweep wall-clock before being hoisted.
```

`new_string`:

```python
    Both ``root`` and ``subdir`` MUST be absolute and normalized at the
    CLI/daemon boundary — the daemon resolves roots upfront via
    ``_discover_roots()`` (avoiding a per-walk ``Path.resolve()`` syscall
    that previously dominated sweep wall-clock on Windows). The CLI's
    path-taking verbs may pass symlink-preserving normalized paths (item
    #95): containment check below is purely lexical and tolerates either
    form. The ``ValueError`` raised on out-of-root ``subdir`` is the
    caller's responsibility to avoid; misuse is a programming error.
```

- [ ] **Step 3: Run reconcile tests**

Run: `uv run python -m pytest tests/test_reconcile_basic.py tests/test_reconcile_edges.py -v`

Expected: PASS. Docstring-only change, no behavior.

- [ ] **Step 4: Run ruff**

Run: `uv run ruff format src/dbxignore/reconcile.py && uv run ruff check src/dbxignore/reconcile.py`

Expected: clean.

---

## Task 8: Manual-test scripts — Phase 4.5 lexist case

Per CLAUDE.md Phase 4.5 convention: new user-visible CLI surface (the `clear` / `list` lexist error message) gets a case in all three manual-test scripts.

**Files:**
- Modify: `scripts/_phase_extended_cli.sh` (sourced by Linux + macOS scripts)
- Modify: `scripts/manual-test-windows.ps1` (PowerShell 7+ counterpart)

- [ ] **Step 1: Find the Phase 4.5 case-numbering scheme**

Read `scripts/_phase_extended_cli.sh` and note the latest case number (look for the most recent `# 4X — <description> (PR #NNN)` comment). Cases are appended in chronological PR order. The new case is the next free number, comment-tagged with the predicted PR number (see Task 9 for prediction).

- [ ] **Step 2: Append the new case to `_phase_extended_cli.sh`**

Read the file to identify the closing pattern (last case + closing function brace, if any). Append:

```bash
# 4X — clear/list now error on nonexistent paths (PR #NNN, item #95)
# Pre-fix: silent "No markers to clear" / empty list output.
# Post-fix: exit 2 with "Path … does not exist." stderr.
NONEXISTENT="${DROPBOX_ROOT}/dbxignore-test-nonexistent-$$"
run_step "clear errors on nonexistent path" \
  "dbxignore clear --yes --force \"${NONEXISTENT}\"" \
  --expect-exit 2 \
  --expect-stderr "does not exist"
run_step "list errors on nonexistent path" \
  "dbxignore list \"${NONEXISTENT}\"" \
  --expect-exit 2 \
  --expect-stderr "does not exist"
```

(Replace `4X` with the actual next-free number; replace `NNN` with the predicted PR number from Task 9 Step 1.)

If `run_step`'s `--expect-stderr` flag doesn't exist in this codebase, simplify to the script's existing per-case shape — read 2-3 adjacent cases in the file to see how they assert.

- [ ] **Step 3: Append the same case to `manual-test-windows.ps1`'s `Test-ExtendedCli`**

Read `scripts/manual-test-windows.ps1` and find the `Test-ExtendedCli` function. Append a parallel case using PowerShell idioms:

```powershell
# 4X — clear/list now error on nonexistent paths (PR #NNN, item #95)
# Pre-fix: silent "No markers to clear" / empty list output.
# Post-fix: exit 2 with "Path ... does not exist." stderr.
$nonexistent = Join-Path $script:DropboxRoot "dbxignore-test-nonexistent-$PID"
$result = & dbxignore clear --yes --force $nonexistent 2>&1
if ($LASTEXITCODE -ne 2) { throw "clear should exit 2 on nonexistent path, got $LASTEXITCODE" }
if ($result -notmatch "does not exist") { throw "clear stderr missing 'does not exist': $result" }
$result = & dbxignore list $nonexistent 2>&1
if ($LASTEXITCODE -ne 2) { throw "list should exit 2 on nonexistent path, got $LASTEXITCODE" }
if ($result -notmatch "does not exist") { throw "list stderr missing 'does not exist': $result" }
```

(Replace `4X` and `NNN` to match.)

Read 2-3 adjacent cases in `Test-ExtendedCli` first to align with the file's existing pattern.

- [ ] **Step 4: Verify scripts at least parse**

Linux/macOS: `bash -n scripts/_phase_extended_cli.sh` (parse-only).
Windows: `pwsh -NoProfile -Command "$null = [scriptblock]::Create((Get-Content -Raw scripts/manual-test-windows.ps1))"`.

Expected: both clean. These scripts are E2E and require a real Dropbox install — actual execution is for release-prep verification (CLAUDE.md "Manual test scripts" section).

---

## Task 9: BACKLOG.md updates

**Files:**
- Modify: `BACKLOG.md` (line 2124 inline RESOLVED marker; bottom of open list for #104; Resolved section at line 2305; Open summary line at 2284)

- [ ] **Step 1: Predict the PR number**

Run both in parallel:

```bash
gh pr list --state all --limit 1
gh issue list --state all --limit 1
```

The next free number is `max(top PR #, top issue #) + 1`. Note it as `<PR-N>`. Confirm after `gh pr create` (Task 10) and amend the marker if wrong (rare; CLAUDE.md mentions this is acceptable).

- [ ] **Step 2: Mark #95 RESOLVED at the inline title**

Open `BACKLOG.md` and find `## 95. Path-taking commands outside ...` (around line 2124). Use `Edit`. `old_string`:

```markdown
## 95. Path-taking commands outside `ignore` / `unignore` resolve symlinks and can operate on the wrong object
```

`new_string`:

```markdown
## 95. Path-taking commands outside `ignore` / `unignore` resolve symlinks and can operate on the wrong object

**Status: RESOLVED 2026-05-11 (PR #<PR-N>).**
```

(Replace `<PR-N>` with the predicted number from Step 1.)

- [ ] **Step 3: File the new walk-entry item**

Find the bottom of the numbered open items (the line containing `## 103. ...` and then its body). Use `Edit` to add a new section after #103's body. `old_string` — use the LAST item's terminating "Touches:" line so the location is unambiguous. Read the file around line ~2280 (just before the `## Status` heading at line 2280) to find the exact insertion point.

Insert after the last numbered item, before `## Status`:

```markdown
## 104. CLI walk-entry callsites descend through symlink-to-directory arguments

`os.walk(path, followlinks=False)` follows the link when ``path`` is itself the walk root (CLAUDE.md gotcha; PR #183 added the corresponding guard at the daemon's per-subdir fan-out). After PR #<PR-N> (item #95), the CLI's path-taking verbs accept symlink-object arguments and pass them through to `_walk_marked_paths` (used by `clear`, `list`) and `_run_apply_pass` → `reconcile_subtree` (used by `apply`). Neither callsite guards `is_symlink()` at the walk root, so a `dbxignore clear ~/Dropbox/some-dir-symlink` walks into the link's target tree after handling the link's own marker. `explain` / `check-ignore` are not affected (no walk).

**Fix candidates:**

- **Guard at each CLI callsite.** Add an `is_symlink()` check at the entry to `_walk_marked_paths` and `_run_apply_pass`'s subtree handling. Short-circuit to "process the link's own marker only; do not descend." Mirrors PR #183's daemon-side approach.
- **Guard inside the helpers themselves** (`_walk_marked_paths`, `reconcile.reconcile_subtree`). Riskier — `reconcile_subtree` is shared with the daemon, which already has its own walk-entry guard from PR #183; adding a second guard would be defense in depth but could mask future daemon-side regressions.

**Urgency:** medium. Same correctness class as #95, narrowly scoped, no user reports yet but the corrected normalization in #95 makes this newly reachable.

Touches: `src/dbxignore/cli.py` (`_walk_marked_paths`, `_run_apply_pass`); `src/dbxignore/reconcile.py` (`reconcile_subtree`); cross-platform symlink tests.

```

(Note the trailing blank line so the next `## Status` heading isn't run-together.)

- [ ] **Step 4: Add resolved entry for #95**

Open `BACKLOG.md` near line 2305 (`### Resolved (reverse chronological)`). The newest resolved entries are at the top. Use `Edit` to add a one-line entry as the first item under that heading.

Read the existing top entry to match its exact format, then `old_string` is the existing top resolved-entry line; `new_string` is the new entry followed by the existing top entry. Example shape (adapt to the file's actual format):

```markdown
### Resolved (reverse chronological)

- **#95** (2026-05-11, PR #<PR-N>) — Path-taking verbs `apply`/`clear`/`list`/`explain`/`check-ignore` now preserve the symlink object via shared `_normalize_under_root` helper. `apply` additionally fixed: broken-target symlinks accepted; symlinked-ancestor refused.
- [existing top entry stays here]
```

- [ ] **Step 5: Update the Open summary line at 2284**

Find the line at ~2284 that reads `Eighteen items. ... Items #95, #97, and #98 are user-facing correctness/error-handling fixes ...`. Use `Edit`. `old_string`:

```markdown
Eighteen items. Most are passive (no concrete trigger requires action) — bundle each with the next code-touch in its respective layer. Items #95, #97, and #98 are user-facing correctness/error-handling fixes and should be prioritized ahead of purely polish work.
```

`new_string`:

```markdown
Eighteen items. Most are passive (no concrete trigger requires action) — bundle each with the next code-touch in its respective layer. Items #97 and #98 are user-facing correctness/error-handling fixes and should be prioritized ahead of purely polish work.
```

(Item count stays at eighteen: #95 removed, #104 added.)

- [ ] **Step 6: Verify BACKLOG.md is well-formed**

Run: `head -n 2310 BACKLOG.md | tail -n 30` (or open in editor)

Spot-check the boundary between the new #104 item and the `## Status` heading; the Resolved-section entry; the inline RESOLVED marker on #95.

---

## Task 10: Final verification + commit + PR

- [ ] **Step 1: Full check stack**

Run each in sequence (stop on first failure):

```bash
uv run mypy .
uv run ruff check . --fix
uv run ruff check .
uv run ruff format .
uv run python -m pytest
```

Expected: all clean. The `test_cli_symlink_path_args.py` suite must be all-green. Existing tests must not regress.

If `mypy` flags new errors, the most likely culprits are `target, _root, _discovered = _normalize_under_root(...)` unpacks (mypy may not infer the tuple shape — add explicit `-> tuple[Path, Path, list[Path]]` annotation if it's missing on the helper).

- [ ] **Step 2: Stage and verify diff**

Run:

```bash
git status
git diff --stat
git diff src/dbxignore/cli.py
git diff src/dbxignore/reconcile.py
git diff BACKLOG.md
```

Spot-check that:
- `cli.py` diff shows the helper extraction + the four verb rewires; no other unintended changes.
- `reconcile.py` diff is the docstring change only.
- `BACKLOG.md` diff matches Task 9's planned edits.

- [ ] **Step 3: Pre-flight commit-check on the planned subject**

Per CLAUDE.md commit conventions:

```bash
echo "fix(cli): preserve symlink object in path-taking verbs (#95)" > /tmp/subj.txt
commit-check -m /tmp/subj.txt
```

Expected: pass (under 72 bytes, conventional commits format, no banned characters).

If `commit-check` isn't installed, count the bytes manually:

```bash
echo -n "fix(cli): preserve symlink object in path-taking verbs (#95)" | wc -c
```

Expected: 60 (well under 72).

- [ ] **Step 4: Stage and commit**

Per CLAUDE.md: stage specific files (not `git add -A`):

```bash
git add src/dbxignore/cli.py \
        src/dbxignore/reconcile.py \
        tests/test_cli_symlink_path_args.py \
        scripts/_phase_extended_cli.sh \
        scripts/manual-test-windows.ps1 \
        BACKLOG.md \
        docs/superpowers/specs/2026-05-11-backlog-95-symlink-normalize-design.md \
        docs/superpowers/plans/2026-05-11-backlog-95-symlink-normalize-implementation.md
git status
```

Verify exactly the expected files are staged. Then commit:

```bash
git commit -m "$(cat <<'EOF'
fix(cli): preserve symlink object in path-taking verbs (#95)

apply, clear, list, and explain/check-ignore previously called
path.resolve() on their path argument, switching containment, marker
reads/writes, and rule lookups to the symlink target. ignore/unignore
already preserved the link object via _validate_target_under_root.

Split _validate_target_under_root into a thin core (_normalize_under_root)
parameterized by require_exists, and a wrapper that adds symlinked-
ancestor rejection. Wire:
- apply, ignore, unignore -> full validator
- clear, list -> thin core, require_exists=True
- explain, check-ignore -> thin core, require_exists=False

Fixes three apply sub-bugs: broken-symlink-target acceptance (lexist
vs exists); symlinked-ancestor rejection (daemon-orphan guard); off
the resolve()-target path.

Walk-root symlink-descent for apply/clear/list on a symlink-to-
directory argument is filed as #104 (separate code path; same
correctness class).
EOF
)"
```

- [ ] **Step 5: Push and open PR**

```bash
git push -u origin HEAD
gh pr create --title "fix(cli): preserve symlink object in path-taking verbs (#95)" --body "$(cat <<'EOF'
## Summary

- `apply`, `clear`, `list`, `explain`/`check-ignore` no longer call `path.resolve()` on their argument. Containment, marker reads/writes, and rule lookups now see the symlink object instead of its target, matching `ignore`/`unignore`'s behavior.
- `apply` additionally fixed: broken-target symlinks accepted via `lexist`; symlinked-ancestor refused with the existing daemon-orphan guard.
- Cross-cutting symlink-correctness test suite at `tests/test_cli_symlink_path_args.py` (raw-argument marker spy; bypasses `FakeMarkers`'s internal `.resolve()` which would erase the bug surface).

Spec: `docs/superpowers/specs/2026-05-11-backlog-95-symlink-normalize-design.md`
Plan: `docs/superpowers/plans/2026-05-11-backlog-95-symlink-normalize-implementation.md`

Walk-root symlink-descent for `apply`/`clear`/`list` on symlink-to-directory arguments is filed as #104 (separate code path; same correctness class).

## Test plan

- [ ] CI matrix green on `ubuntu-latest`, `windows-latest`, `macos-latest`.
- [ ] `tests/test_cli_symlink_path_args.py` covers cases 1-7 from the spec; verb-parametrized.
- [ ] No regression in existing `test_cli_apply.py`, `test_cli_clear.py`, `test_cli_status_list_explain.py`.
- [ ] Manual: confirm `dbxignore clear ~/Dropbox/typo-name` exits 2 with "does not exist".
- [ ] Manual: confirm `dbxignore explain ~/Dropbox/hypothetical-future-file` returns exit 1 (no match), not exit 2.
EOF
)"
```

Save the returned PR URL. Confirm the predicted PR number matches; if not, amend the `Status: RESOLVED` line in `BACKLOG.md` and the commit message accordingly.

- [ ] **Step 6: Watch CI**

```bash
gh pr checks --watch
```

Or kick off a background watch if expecting a long run. Expected: green on all three platform legs + commit-check.

---

## Self-review

After writing the plan, I re-checked against the spec:

**Spec coverage:**
- Architecture (`_normalize_under_root`, `_validate_target_under_root` wrapper) → Task 1.
- Per-verb wiring (apply, clear, list, explain) → Tasks 3-6.
- Existence policy (lexist for filesystem verbs; permissive for rule-logic verbs) → encoded in the `require_exists` flag wiring.
- Resolved-fallback path (for out-of-Dropbox aliases) → preserved inside `_normalize_under_root` (Task 1 Step 2).
- Error message reuse → preserved verbatim in Task 1 Step 2.
- Behavior changes #1-#8 → all exercised by the test cases in Task 2 (case 1-7 matrix maps to behaviors 1, 2, 4-8; behavior 3, "explain/check-ignore on any symlink is fully resolved", is exercised by every `RULE_LOGIC_VERBS` parametrization).
- Raw-argument spy (replaces `FakeMarkers` for this suite) → Task 2 Step 1.
- `legacy_mode` explicitly NOT used → Task 2 Step 1 docstring.
- Case 6a/6b/7 ordering nuance → cases parametrized separately in Task 2.
- `reconcile_subtree` docstring update → Task 7.
- Manual-test scripts (Phase 4.5 lexist case) → Task 8.
- BACKLOG.md (resolve #95, file #104, update summary) → Task 9.
- Commit shape (single `fix(cli)` commit under 72 bytes) → Task 10 Steps 3-4.

**Placeholders:** Only `<PR-N>` and `4X`, which are documented as predict-and-confirm patterns in Task 9 Step 1 / Task 8 Step 1. Not unresolved scope.

**Type consistency:** The helper signature `tuple[Path, Path, list[Path]]` is used identically in Task 1 Step 2 and the unpack sites in Tasks 3-6 (`target, _root, _discovered = _normalize_under_root(...)`). No drift.

**Modesty check (CLAUDE.local.md):** Scanned for banned phrasings (`carefully`, `robust`, `production-grade`, `unlike X`, `uniquely`, `seamless`, etc.) — none present in the plan body.

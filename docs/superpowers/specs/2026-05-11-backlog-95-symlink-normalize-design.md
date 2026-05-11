# Path-taking verbs preserve symlink object — design spec

**Item:** BACKLOG #95
**Date:** 2026-05-11
**Status:** Approved (awaiting writing-plans + implementation)

## Problem

`ignore` and `unignore` (BACKLOG #93, PR #191) normalize their path argument with `Path.absolute()` + `os.path.normpath` so markers and rules apply to the symlink object, not its target. The older path-taking verbs still call `path.resolve()` and operate on the resolved target:

- `apply <path>` (`src/dbxignore/cli.py:606`)
- `clear <path>` (`src/dbxignore/cli.py:858`)
- `list <path>` (`src/dbxignore/cli.py:1314`)
- `_explain <path>` shared by `explain` and `check-ignore` (`src/dbxignore/cli.py:1345`)

For a symlink `~/Dropbox/link → /elsewhere/file`:

- macOS and Windows attach ignore markers to the link object itself. Resolving first switches marker reads/writes to the target's inode/path, so the four verbs see and operate on the wrong filesystem object.
- Linux rejects `user.*` xattrs on symlinks regardless, but the four verbs still report rule matches against the target rather than the link object — `explain` is the most visible: it answers "is the target ignored?" instead of "is this link object ignored?".
- An in-Dropbox symlink whose target lives outside Dropbox is rejected by all four verbs today (containment is checked after resolution), even though the link object is a legitimate in-tree path.

`apply` has two additional bugs that fall out of the same callsite:

- `resolved.exists()` rejects a symlink whose target is broken or missing, even though the link object itself is a valid filesystem entry. `ignore`/`unignore` already use `os.path.lexists`, which accepts these.
- `apply <path>` accepts paths whose ancestor chain between target and root traverses a symlink. The daemon walks each root with `os.walk(..., followlinks=False)` and never reconciles such a path, so the marker `apply` writes is stranded — the daemon's hourly recovery sweep cannot maintain it. `ignore`/`unignore` reject this configuration today; `apply` does not.

## Goals

After this change:

- `apply`, `clear`, `list`, and `explain`/`check-ignore` normalize their path argument without following symlinks. Containment, the link's *own* marker read/write, and rule lookup all see the link object instead of its target. Marker and rule semantics match `ignore`/`unignore` for this scope of operations.
- For a symlink-to-file argument, every operation each verb performs targets the link object cleanly (no `os.walk` descent because the link isn't a directory).
- For `explain` and `check-ignore`, every symlink case is fully resolved (single-path rule lookup, no walk involved).
- For a symlink-to-directory argument on `apply`, `clear`, `list`, the link's *own* marker is read/written correctly, but `os.walk(link, followlinks=False)` continues to descend into the link's target tree at the walk root. Closing that gap is filed as a separate backlog item — see Non-goals.
- `apply` accepts symlinks with broken/missing targets (matching `ignore`/`unignore`).
- `apply` refuses paths with a symlinked ancestor between target and root, with the existing user-facing message.
- An in-Dropbox symlink whose target lives outside Dropbox is accepted by all four verbs (containment is checked on the unresolved path).
- An out-of-Dropbox alias pointing into Dropbox continues to work (the existing resolved-fallback path generalizes to all four verbs).
- `clear` and `list` exit 2 with `Path … does not exist.` on a nonexistent argument, matching the other filesystem-state verbs.
- `explain` and `check-ignore` continue to accept nonexistent paths (rule-lookup verbs answer a hypothetical question; existence is irrelevant to the answer).

## Non-goals

- **`os.walk` symlink-descent at CLI walk-entry sites.** With this PR's normalization fix, `apply`/`clear`/`list` accept a symlink-to-directory argument; the link's own marker is read/written correctly. However, `os.walk(path, followlinks=False)` follows the link when `path` is itself the walk root (per the existing CLAUDE.md gotcha; PR #183 added the corresponding guard at the daemon's per-subdir fan-out). The CLI walk-entry callsites — `_walk_marked_paths` (used by `clear`, `list`) and `reconcile_subtree` via `_run_apply_pass` (used by `apply`) — do not yet guard `is_symlink()` at the walk root, so for a symlink-to-directory argument these three verbs will continue to traverse the link's target tree after the link's own marker is processed. This is the same correctness class as #95 but a separate code path; filed as a new backlog item before this PR opens, and resolved in a follow-up PR so #95 stays narrowly scoped to normalization. `explain`/`check-ignore` are not affected (no walk).
- **Other resolve()-call audits.** `cli.py` has additional `.resolve()` callsites (`_apply_from_gitignore`'s `mount_at`, `init`'s anchor, `_resolve_canonical_to_disk`'s parent-dir scan, the daemon side of `roots.discover()`). Those serve different purposes — rule-file mount points, canonical-name discovery — and are not the symlink-object-vs-target class of bug. Out of scope.
- **Behavior changes to `ignore`/`unignore`.** Both already use `_validate_target_under_root`. This PR only refactors that helper to share its core with the thinner helper; the call sites are untouched.

## Design

### Architecture

Two helpers in `src/dbxignore/cli.py`, one wrapping the other:

```python
def _normalize_under_root(
    path: Path, *, require_exists: bool
) -> tuple[Path, Path, list[Path]]:
    """Symlink-preserving normalization + Dropbox-root containment.

    Steps:
      1. target = Path(os.path.normpath(path.absolute()))   # non-following
      2. if require_exists and not os.path.lexists(target): exit 2.
      3. discovered = _discover_roots(); if empty, exit 2.
      4. root = find_containing(target, discovered)
         if None, fall back to path.resolve() (handles out-of-Dropbox
         symlink aliases that reach into Dropbox).
         if still None, exit 2.
      5. return (target, root, discovered)
    """

def _validate_target_under_root(path: Path) -> tuple[Path, Path, list[Path]]:
    """Wraps `_normalize_under_root(require_exists=True)` and adds
    symlinked-ancestor rejection between target and root. Used by the
    write-side verbs (ignore, unignore, apply).
    """
    target, root, discovered = _normalize_under_root(path, require_exists=True)
    # existing symlinked-ancestor walk + exit 2
    return target, root, discovered
```

`_validate_target_under_root` keeps its existing public signature and return shape. Its body becomes a two-line composition over `_normalize_under_root` plus the existing ancestor walk.

### Per-verb wiring

| Verb | Helper call | Existence check | Ancestor reject |
|---|---|---|---|
| `ignore` | `_validate_target_under_root(path)` | yes | yes |
| `unignore` | `_validate_target_under_root(path)` | yes | yes |
| `apply <path>` | `_validate_target_under_root(path)` | yes | yes |
| `clear <path>` | `_normalize_under_root(path, require_exists=True)` | yes | no |
| `list <path>` | `_normalize_under_root(path, require_exists=True)` | yes | no |
| `explain <path>` | `_normalize_under_root(path, require_exists=False)` | no | no |
| `check-ignore <path>` | (via `_explain`) | no | no |

`apply` moves from `path.resolve() + exists()` to `_validate_target_under_root`. The `targets = [(matched_root, resolved)]` line at `cli.py:614` becomes `targets = [(root, target)]`.

`clear` and `list` move from `path.resolve() + find_containing` to `_normalize_under_root(require_exists=True)`. The `targets = [target]` lines stay; only the normalization changes.

`_explain` moves from `resolved = path.resolve()` to `target, _, _ = _normalize_under_root(path, require_exists=False)` and passes `target` to `cache.match` / `cache.explain`.

### `reconcile_subtree` contract documentation

`src/dbxignore/reconcile.py:38` currently says `root` and `subdir` "MUST be absolute and pre-resolved by the caller." After this PR, `apply <path>` passes a symlink-preserving absolute path (not resolved) into `_run_apply_pass` → `reconcile_subtree`. The code path works — `reconcile_subtree`'s containment check at line 87 (`subdir != root and not subdir.is_relative_to(root)`) is purely lexical and tolerates either form — but the comment becomes misleading.

Update the docstring to say "absolute and normalized at the CLI/daemon boundary" (or equivalent wording). Keep the `Path.resolve()`-is-syscall-expensive rationale, since it still applies to the daemon-side callers that DO resolve roots upfront via `_discover_roots()`. The `ValueError` raised on out-of-root `subdir` (line 88) is unchanged and stays documented.

No behavior change; docstring-only edit.

### Existence policy

Filesystem-state verbs (`ignore`, `unignore`, `apply`, `clear`, `list`) require `os.path.lexists(target)`. Rule-logic verbs (`explain`, `check-ignore`) do not. The split tracks what each verb actually does: filesystem operations need a real path to operate on; rule lookups answer a question against the rule set regardless of whether the path exists.

### Resolved-fallback path

The existing `_validate_target_under_root` falls back to `path.resolve()` when the unresolved path is not under any discovered root, then re-runs `find_containing` against the resolved path. This handles the case of `/alias/Dropbox/sub` where `/alias → ~/Dropbox`: the unresolved path is not under any discovered (resolved) root, but the resolved path is. The fallback moves into `_normalize_under_root` and applies to all four verbs.

### Error messages

Three messages are reused from `_validate_target_under_root` verbatim:

- `Path {path} does not exist.`
- `No Dropbox roots found. Is Dropbox installed?`
- `Path {path} is not under any Dropbox root.`

`apply`'s symlinked-ancestor rejection uses the existing message:

```
error: <path> has a symlinked ancestor <ancestor>; the daemon walks
with followlinks=False and would never reconcile this path. Operate
on the symlink itself instead.
```

No new copy.

### Behavior changes user-visible

1. `apply`, `clear`, `list`, `explain` on a symlink: containment, the link's *own* marker read/write, and rule lookup all see the link object (was: resolved target).
2. For `explain`/`check-ignore` on any symlink, the resolution is complete (no walk, single-path rule lookup).
3. For `apply`/`clear`/`list` on a symlink-to-file, the resolution is complete (no walk descends past the link because the link is not a directory).
4. For `apply`/`clear`/`list` on a symlink-to-directory, the link's own marker is operated on correctly, but the subsequent `os.walk(link, followlinks=False)` still follows the link's target. Closing that gap is the separate follow-up item (see Non-goals).
5. `apply` on a symlink with a broken/missing target: accepted (was rejected).
6. `apply` on a symlinked-ancestor path: refused with the existing ancestor message (was silently processed).
7. `clear` and `list` on a nonexistent path: `Path … does not exist.` exit 2 (was silently empty output).
8. In-Dropbox symlinks whose target lives outside Dropbox are accepted by all four verbs (containment now checked on the link's lexical path).

The out-of-Dropbox alias fallback (`/alias → ~/Dropbox`, arg `/alias/sub`) continues to work across all four verbs.

## Tests

### New file: `tests/test_cli_symlink_path_args.py`

A cross-cutting symlink-correctness suite for the four affected verbs, with verb-parametrized cases sharing symlink fixtures.

### Marker-call spy (replaces FakeMarkers for this suite)

`FakeMarkers` in `tests/conftest.py:93` resolves every path argument internally (`path.resolve()` in `is_ignored`/`set_ignored`/`clear_ignored` at lines 106, 110, 115) before recording it in `set_calls` / `clear_calls`. That resolution erases exactly the distinction this PR is about — link object vs. resolved target both record as the resolved path, so a pre-fix run and a post-fix run produce identical recordings.

The new suite uses a **raw-argument spy** at the CLI → markers boundary instead. The spy is module-local to the test file and operates by `monkeypatch.setattr` on the `markers` module's three functions:

```python
@pytest.fixture
def raw_marker_spy(monkeypatch):
    set_args: list[Path] = []
    clear_args: list[Path] = []
    is_ignored_args: list[Path] = []
    monkeypatch.setattr(markers, "set_ignored",   lambda p: set_args.append(p))
    monkeypatch.setattr(markers, "clear_ignored", lambda p: clear_args.append(p))
    monkeypatch.setattr(markers, "is_ignored",    lambda p: (is_ignored_args.append(p), False)[1])
    return SimpleNamespace(set=set_args, clear=clear_args, is_ignored=is_ignored_args)
```

Assertions then compare against the symlink path object directly:

```python
assert raw_marker_spy.set == [link_path]          # post-fix: link object
# (pre-fix would record link_path.resolve(), which equals target_path on the test FS)
```

This is also why the suite does NOT use `legacy_mode` — the spy replaces the entire `markers` module's call surface at the CLI → markers boundary, so the macOS backend's dual-vs-legacy attr decision is never invoked. (`legacy_mode` is defined in `tests/test_macos_xattr_unit.py:102`, not in `conftest.py`, and is not auto-available to other modules.) The platform-marker-semantics divergence captured in the CLAUDE.md table only affects how the *backend* writes the marker once it receives a path; this suite's bug surface is the path the CLI hands to the backend, which the spy captures verbatim.

### Case matrix

| # | Scenario | Apply | Clear | List | Explain |
|---|---|---|---|---|---|
| 1 | Link object under Dropbox, target also under Dropbox | spy.set == [link_path] | spy.is_ignored sees link_path | spy.is_ignored sees link_path | cache.match called with link_path |
| 2 | Link object under Dropbox, target outside Dropbox | Same as #1 (containment passes on link path) | Same as #1 | Same as #1 | Same as #1 |
| 3 | Broken symlink (target nonexistent), link under Dropbox | Accepted | Accepted | Accepted | Accepted |
| 4 | Out-of-Dropbox alias (`/alias → ~/Dropbox`, arg `/alias/sub`) | Resolved-fallback succeeds | Same | Same | Same |
| 5 | Symlinked ancestor between target and root | Refused (ancestor message) | Proceeds | Proceeds | Proceeds |
| 6a | Nonexistent path **under Dropbox** | Exit 2 "does not exist" | Exit 2 "does not exist" | Exit 2 "does not exist" | Returns exit 1 (no match) |
| 6b | Nonexistent path **outside Dropbox** | Exit 2 "does not exist" | Exit 2 "does not exist" | Exit 2 "does not exist" | Exit 2 "not under any Dropbox root" |
| 7 | **Existing** path outside Dropbox | Exit 2 "not under any Dropbox root" | Same | Same | Same |

Ordering note on cases 6 and 7: the helper checks `lexists` before `find_containing` for `require_exists=True` callers (matching the existing `_validate_target_under_root` order at `cli.py:141` → `cli.py:144`). So a path that is *both* nonexistent and outside Dropbox returns the "does not exist" message rather than the "not under any Dropbox root" one (case 6b). Case 7 specifically tests an *existing* path outside Dropbox to exercise the containment-failure arm. For `explain` (`require_exists=False`), only containment is checked, so 6b returns the containment message.

### Cross-platform handling

Symlink creation on Windows requires Developer Mode or `SeCreateSymbolicLinkPrivilege`. If `os.symlink` raises `OSError` at fixture setup, the affected test `pytest.skip`s cleanly. Linux and macOS create symlinks unconditionally.

Marker-write semantics on real backends are not exercised by this suite — the spy intercepts at the CLI → markers boundary. The CLAUDE.md per-backend symlink table (`Linux → PermissionError on write`, `macOS → NOFOLLOW`, `Windows → ADS on reparse point`) is the existing backends' contract and is covered by `tests/test_*_xattr_unit.py` / `tests/test_windows_ads.py`. This suite's job is verifying the CLI passes the link object through to the backend at all; what the backend does with it is independently tested.

### Existing per-verb tests

`tests/test_cli_apply.py`, `tests/test_cli_clear.py`, `tests/test_cli_status_list_explain.py` each gain a single smoke assertion that the new helper is invoked and its output flows through. The symlink behavior matrix lives in the new file to avoid scattering.

### Manual-test scripts

The lexist gating on `clear` and `list` is new user-visible error surface. Per the CLAUDE.md Phase 4.5 convention, all three manual-test scripts (Linux, macOS, Windows) gain one case each — a typo'd `clear` / `list` path that produces the new `Path … does not exist.` error message — with inline `# 4X — clear/list now error on nonexistent path (PR #NNN)` provenance comments.

The bash scripts source `scripts/_phase_extended_cli.sh`; the helper takes the new case body. `manual-test-windows.ps1`'s `Test-ExtendedCli` is hand-synced.

## BACKLOG.md changes in this PR

- **Resolve #95.** Inline `**Status: RESOLVED <date> (PR #<N>).**` after the title at line 2124, plus an entry at the top of `### Resolved (reverse chronological)` (line 2305).
- **File a new item** for the CLI walk-entry symlink-descent issue, listing the `os.walk(symlink, followlinks=False)` descent into the link's target tree at `_walk_marked_paths` and `_run_apply_pass` → `reconcile_subtree`. Fix candidates: (a) guard at each CLI callsite; (b) guard inside the helpers themselves (riskier — they are shared with the daemon, which has its own walk-entry guard from PR #183). Urgency: medium. Touches: `src/dbxignore/cli.py` (`_walk_marked_paths`, `_run_apply_pass`), `src/dbxignore/reconcile.py` (`reconcile_subtree`), cross-platform symlink tests.
- **Update the Open summary line at line 2284.** Remove #95 from the "user-facing correctness/error-handling fixes" prioritization sentence (becomes "Items #97 and #98 …"). Item count update follows the same edit shape used in prior PRs.

PR number prediction: `gh pr list --state all --limit 1` plus `gh issue list --state all --limit 1`, take max + 1, confirm after `gh pr create`.

## Commit shape

Single commit, type `fix`, scope `cli`. Subject draft (target under 72 bytes):

```
fix(cli): preserve symlink object in path-taking verbs (#95)
```

BACKLOG.md changes ride the same commit because the resolution + new-item filing share the design's revertability boundary.

## Out of scope

See "Non-goals" above. Summary:

- CLI walk-entry symlink-descent: separate backlog item, follow-up PR.
- Other `cli.py` `.resolve()` callsites that serve different purposes.
- Behavior changes to `ignore`/`unignore`.

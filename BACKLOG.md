# dbxignore — backlog and resolved-items log

Central tracker for open items, planned work, and the historical record of what was filed/fixed and when. Originated as the v0.2.1 negation-polish followups doc; renamed and restructured 2026-04-26 (PR #52) once it had outgrown that scope.

**Conventions** (also noted in `CLAUDE.md`):
- New items append at the bottom (`## <N>. <title>`) with body, fix candidates, urgency, and a `Touches:` file list.
- Resolved items get an inline `**Status: RESOLVED <date> (PR #<N>).**` marker AND an entry in the Status section at the bottom.
- The Status section maintains an at-a-glance Open list, a reverse-chronological Resolved log, and Provenance notes covering how items were sourced.

**Scope.** Mixes engineering tech-debt, CI flake observations, release-workflow hardening, and CLAUDE.md currency findings. Not user-filed issues — the project doesn't currently host any (PyPI traffic + zero open GitHub issues at last check). When external reports show up, this file may need to evolve toward GitHub Issues; for now, in-tree provenance + grep is the right tradeoff.

## 1. Stale `# Task 3` banner in `tests/test_rules_conflicts.py`

Left over from the task-by-task execution of the implementation plan. The other tests in the file don't carry similar banners — it reads as an orphan comment now that the feature is integrated. Delete the comment line.

Touches: `tests/test_rules_conflicts.py:51` (one-line removal).

**Status: RESOLVED 2026-04-24.** Stripped the `Task 3:` prefix from the banner — kept the dashed visual divider since it still organizes the file (separates `Conflict`-dataclass tests from `_detect_conflicts` tests), only the rotted task-tracking label needed to go.

## 2. Redundant inline imports in new test functions

`tests/test_cli_status_list_explain.py` has several new test functions with in-body imports like `from dropboxignore import cli, state` even though those modules are already imported at the top of the file. Copied verbatim from the implementation plan's self-contained snippets; works but adds visual noise.

Fix: consolidate to module-level imports; remove the duplicates. Same cleanup applies to `tests/test_rules_reload_explain.py` where a handful of tests have `from dropboxignore.rules import RuleCache` inside the function body.

Touches: `tests/test_cli_status_list_explain.py`, `tests/test_rules_reload_explain.py`.

**Status: RESOLVED 2026-04-24.** Removed all 14 redundant in-function imports: 4 in `tests/test_cli_status_list_explain.py` (3× `from dbxignore import cli, state`, 1× `from dbxignore import cli`) and 10 in `tests/test_rules_reload_explain.py` (`from dbxignore.rules import RuleCache`). Each duplicated a top-level import already present at line 7 / line 1 respectively. Note: the followup's literal strings (`from dropboxignore...`) had been transparently updated to `dbxignore` during the v0.3 rename sweep — the symptoms persisted under the new module name.

## 3. `_SequenceEntry.pattern: object` could be a `Protocol`

The field is typed `object` with a comment noting "duck-typed (.include, .match_file)". This is intentionally loose so that `_FakePattern` in the unit tests can satisfy the type. A `typing.Protocol` with the two expected attributes would be equally permissive and give static type checkers something to verify callers against.

Proposed:

```python
class _PatternLike(Protocol):
    include: bool | None
    def match_file(self, path: str) -> object: ...

@dataclass(frozen=True)
class _SequenceEntry:
    ...
    pattern: _PatternLike
```

Touches: `src/dropboxignore/rules.py` near `_SequenceEntry`; likely a Protocol declaration next to the existing imports.

**Status: RESOLVED 2026-04-24.** Replaced `pattern: object` with a `_PatternLike` Protocol (`include: bool | None`, `match_file(path: str) -> bool | None`) defined just before `_SequenceEntry`. Tightened the followup's proposed return type from `object` to `bool | None` to match the actual contract of both `GitIgnoreSpecPattern` and the `_FakePattern` test shim — gives static checkers something useful to verify against. The 36 tests in `test_rules_conflicts.py` + `test_rules_reload_explain.py` continued to pass without test-side changes (structural typing working as intended).

## 4. `dropboxignore status` output doesn't column-align conflicts

The conflicts section uses fixed two-space separators between fields. At 5+ conflicts with varying pattern lengths, the columns slide based on content, reducing scannability. For example:

```
rule conflicts (2):
  .dropboxignore:2  !build/keep/  masked by .dropboxignore:1  build/
  .dropboxignore:5  !node_modules/some-very-long-package/  masked by .dropboxignore:1  node_modules/
```

Fix: compute column widths first, pad with `f"{s:<width}"`. Cheap (adds ~5 lines) but requires a test update because string comparisons in existing tests would need to tolerate padding. Not worth doing without a concrete user report.

Touches: `src/dropboxignore/cli.py` `status` conflicts block; `tests/test_cli_status_list_explain.py` relaxes substring assertions.

**Status: RESOLVED 2026-04-25.** Implemented column alignment per the followup's proposal — pre-formatted all conflict rows, computed `max(len(...))` per column for `dropped_loc`, `dropped_pattern`, and `masking_loc`, then padded with `f"{s:<width}"` (~17 lines added to the conflicts block, slightly above the followup's "~5 lines" estimate due to the explicit `rows = [...]` precompute). The followup's "test relaxation" cost turned out to be **zero** — the existing substring-based asserts (`"masked by" in result.output` etc.) already tolerated trailing whitespace from padding, no changes needed. Added a regression test (`test_status_column_aligns_conflicts_with_varying_pattern_lengths`) that asserts `"masked by"` lands at the same column across two conflicts with widely varying pattern lengths. Done as part of a backlog completeness sweep, overriding the followup's "not worth doing without a concrete user report" guidance — that guidance was based on assumed test cost, which the actual test shape made moot.

## 5. `_ancestors_of` calls `Path.resolve()` on every rule mutation

CLAUDE.md's Gotchas section flags `Path.resolve()` as a Windows perf hazard (per-call syscall). `_detect_conflicts` invokes `_ancestors_of` once per negation rule, each call doing one `.resolve()`. The cost fires only during rule mutations (rare — `load_root` on daemon start, `reload_file` on watchdog events, manual CLI invocations), and resolves exactly one path per negation. Negligible in practice.

The note here is about documentation, not optimization: add a comment in `_ancestors_of` explaining that the `.resolve()` cost is bounded to mutation events so a future reader doesn't "optimize" it out for the wrong reason (and break the path-equality invariant that downstream `is_relative_to` checks depend on).

Touches: `src/dropboxignore/rules.py` `_ancestors_of` docstring.

**Status: RESOLVED 2026-04-24.** Added a multi-line `NOTE:` comment at the `.resolve()` call in `_ancestors_of` (not the docstring — at the call site, where the temptation to "optimize the syscall" would strike). Captures both facts: (1) cost is bounded to mutation events (`load_root` / `reload_file` / `remove_file`), not the steady-state sweep, and resolves exactly one path per negation rule; (2) removing the resolution would break the downstream `is_relative_to(root)` and equality checks that assume canonical paths — a symlink or `..` component in `target` could fool both into disagreeing on path identity and missing valid ancestors.

## 6. `rules.py` has grown to ~530 lines; detection layer could extract

The detection layer (`literal_prefix`, `_ancestors_of`, `_find_masking_include`, `_detect_conflicts`, `Conflict`) is ~120 lines and has no coupling to `RuleCache` internals beyond the input-sequence shape. It could live in `rules_conflicts.py` or `conflicts.py` alongside `rules.py`; `RuleCache._recompute_conflicts` would import and call.

Not pressing — the file is still single-responsibility at a stretch, and splitting costs a sibling file plus one import edit. Worth revisiting in v0.3 if any further detection logic lands (e.g., cross-root conflicts, conflicts across installs) or if another feature pushes `rules.py` past ~650 lines.

Touches: `src/dropboxignore/rules.py` → `src/dropboxignore/rules_conflicts.py` (new); one import.

**Status: RESOLVED 2026-04-25.** Extracted the detection layer (`literal_prefix`, `_ancestors_of`, `_find_masking_include`, `_detect_conflicts`, `Conflict`) to a new sibling module `src/dbxignore/rules_conflicts.py`. Net: `rules.py` 556 → 389 lines (-167); `rules_conflicts.py` new 186 lines. The followup's "~120 lines" estimate matched the functional content (the additional ~66 lines in `rules_conflicts.py` is the new module docstring + preserved spacing). API preserved — `rules.py` re-imports `Conflict` and `_detect_conflicts` so `RuleCache.conflicts()` still returns `Conflict` objects without external import changes; the only direct importer (`tests/test_rules_conflicts.py`) got a one-line update. Done as part of the 2026-04-25 backlog completeness sweep, overriding the followup's "Not pressing" guidance — neither trigger had fired (file was at 556, not 650+; no new detection feature scheduled). Landed in PR #38.

## 7. No test for the "sandwich" ordering `include → negation → another_include`

By inspection of `_detect_conflicts`, the algorithm only looks at `sequence[:i]` (entries before the current negation), so a later include can't retroactively affect an earlier negation's conflict state. The `include → !negation → another_include` shape therefore works correctly — the `another_include` is invisible to the detector.

But there's no explicit test pinning this. If a future refactor accidentally changed the slice to `sequence[i + 1:]` or iterated the full sequence, the bug would only surface in real-world `.dropboxignore` files, not in the test suite.

Fix: a three-entry test in `tests/test_rules_conflicts.py` with `build/` + `!build/keep/` + `src/`, asserting exactly one conflict and that the presence of `src/` didn't change detection.

Touches: `tests/test_rules_conflicts.py` (one new test).

**Status: RESOLVED 2026-04-24.** Added `test_detect_later_include_does_not_affect_earlier_negation` after the existing `test_detect_multiple_independent_conflicts` in `tests/test_rules_conflicts.py`. Three-entry sandwich (`build/` + `!build/keep/` + `src/`) asserts exactly one conflict and that the trailing `src/` doesn't perturb detection — pinning the `sequence[:i]` slice invariant.

## 8. Pre-flight should run commit-check against every branch commit, not just HEAD

The task-15 pre-flight pattern used in recent PRs runs `commit-check --message` against the planned PR title or HEAD subject only. CI (`commit-check-action@v2.6.0`) runs the check against **every commit in the PR** — i.e. the full `origin/main..HEAD` range.

Surfaced by PR #12: one intermediate commit (`docs: --purge scope broadened (...)`) passed my local HEAD check (which ran against a different planned subject) but failed CI because its description starts with `--`, which commit-check's Conventional Commits regex treats as ambiguous with flag syntax. The force-push round-trip to amend was avoidable.

**Proposed fix:** add a pre-flight snippet to the CLAUDE.md Git workflow section that matches what CI runs:

```bash
git log --pretty=format:'%s%n' origin/main..HEAD | while IFS= read -r msg; do
  [ -z "$msg" ] && continue
  printf '%s\n' "$msg" > /tmp/m.txt
  commit-check --message --no-banner --compact /tmp/m.txt || echo "FAIL: $msg"
done
```

Local green becomes CI green on the message check. Prevents recurrence of the PR #12 force-push round-trip.

Touches: `CLAUDE.md` (Git workflow section, new bullet or extended existing one).

**Status: RESOLVED in v0.2.1.** Landed in PR #18 (one of three commits in the release-workflow polish bundle).

## 9. Release workflow should have a `workflow_dispatch` trigger

`.github/workflows/release.yml` triggers only on `push: tags: ['v*']`. That meant the workflow's first real exercise was the v0.2.0 release itself — where it failed at the PyInstaller step (pyinstaller wasn't installed; see PR #14 for the fix). The bug had been latent for the entire lifetime of the workflow; no PR before v0.2.0 exercised it.

Adding a second trigger lets us dry-run the release build without creating a tag:

```yaml
on:
  push:
    tags: ['v*']
  workflow_dispatch:
```

With `workflow_dispatch`, the workflow becomes runnable via `gh workflow run release.yml` or the GitHub UI. Two tweaks needed in the body: the `Publish GitHub Release` step should probably gate on `if: startsWith(github.ref, 'refs/tags/')` so manual runs don't attempt to publish a Release from a non-tag ref; the workflow can still build and upload artifacts as step outputs / run artifacts for verification.

Next time a release-workflow change lands, we can dispatch-run it manually before tagging. Prevents the "first exercise is the actual release" failure mode.

Touches: `.github/workflows/release.yml`.

**Status: RESOLVED in v0.2.1.** Landed in PR #18 (one of three commits in the release-workflow polish bundle). `workflow_dispatch:` trigger added; `Publish GitHub Release` step gated on `startsWith(github.ref, 'refs/tags/')` so dispatch runs build artifacts but don't publish spurious Releases.

## 10. Publish releases as the repo owner, not `github-actions[bot]`

v0.2.0 was published by `github-actions[bot]` because `softprops/action-gh-release` authenticates via the default `GITHUB_TOKEN`. Visible in `gh release view v0.2.0` → `author: github-actions[bot]`. The release is still authoritative and tied to the repo's audit trail, but the UI-facing attribution reads as machine-authored rather than owner-authored.

Two mechanisms to fix:

- **Personal access token (PAT)** with `contents: write` + `actions: write` scopes. Store as a repo secret (`GH_RELEASE_TOKEN` or similar); pass to the action via `token: ${{ secrets.GH_RELEASE_TOKEN }}`. Simplest. Cost: secret management + periodic rotation.
- **GitHub App** with identity. More complex setup; justified if the token needs organization-wide reach or the PAT's personal scope would be too broad.

PAT is the standard solo-dev choice. Requires a one-time setup (generate PAT → add secret → update workflow), then releases surface under your GitHub identity.

Touches: `.github/workflows/release.yml` (add `token:` input to the `softprops/action-gh-release` step); repo secrets (one-time, outside of the repo tree).

**Status: RESOLVED in v0.2.1.** Landed in PR #18 (one of three commits in the release-workflow polish bundle). PAT-with-fallback pattern adopted: `token: ${{ secrets.GH_RELEASE_TOKEN || github.token }}` — zero-risk to existing workflows since the fallback evaluates to the default token when the secret isn't configured.

## 11. Publish releases to PyPI from the release workflow

Depends on **item 12** — the PyPI name `dropboxignore` is already taken (by a legitimate 2019 project from Michał Karol using the older Selective Sync API, not xattrs). We're renaming to `dbxignore` first; this item publishes under the new name.

Users currently install via `uv tool install git+https://github.com/kiloscheffer/dropboxignore` (source build) or by downloading the wheel from GitHub Releases manually. `pip install <name>` doesn't work yet. Discoverability penalty: PyPI search + `pip`-based pipelines skip the project entirely.

Fix: add a step to `release.yml` that uploads `dist/*.whl` + `dist/*.tar.gz` to PyPI after the GitHub Release is published. Two auth mechanisms:

- **Trusted Publishing via OIDC** (GitHub's recommended approach since 2023). No secrets; PyPI verifies the workflow's GitHub identity via OIDC token. One-time setup: register the repo as a Trusted Publisher on PyPI (account admin page). Workflow uses `pypa/gh-action-pypi-publish@release/v1` with no credentials; the action extracts the OIDC token automatically.
- **API token** stored as a PyPI secret. Older pattern; works but requires token rotation.

Trusted Publishing is the cleaner choice — no secrets to leak or rotate. One-time PyPI registration (as `dbxignore`, not `dropboxignore`), then all future releases publish automatically on tag push. Worth adding a deployment-environment gate (`environment: pypi`) on the publish job so each upload requires a manual approval click — belt-and-braces against rogue releases, removable later if the ergonomics bite.

Touches: `.github/workflows/release.yml` (add PyPI upload step); PyPI account (one-time — register project as Trusted Publisher).

**Status: RESOLVED in v0.3.0.** Implemented via Trusted Publishing + `pypi` environment gate as proposed. Spec: `docs/superpowers/specs/2026-04-23-v0.3-dbxignore-rename.md`. Release notes: `docs/release-notes/v0.3.0.md`. Landed in PR #23.

## 12. Rename the PyPI distribution + CLI + Python package from `dropboxignore` to `dbxignore`

The PyPI name `dropboxignore` is taken by [`MichalKarol/dropboxignore`](https://github.com/MichalKarol/dropboxignore) (a 2019 Selective-Sync-based tool, last release 2019-08 — likely dormant but PyPI name-reuse policy is strict). PyPI takeover is slow and unreliable; renaming is the pragmatic path.

Decision: adopt `dbxignore` — uses Dropbox's own `dbx` abbreviation (as in `dbxcli`, `dbx.com`), shorter, trademark-safer than the full `dropbox` word, and clearly differentiates from the older project.

Scope (**option II** from the brainstorm — rename everything except the rule file):

- **PyPI distribution name** (`pyproject.toml` `[project].name`): `dropboxignore` → `dbxignore`.
- **Python package directory**: `src/dropboxignore/` → `src/dbxignore/` (directory rename + all `from dropboxignore import …` → `from dbxignore import …` across the source tree + tests).
- **CLI entry points** (`pyproject.toml` `[project.scripts]`): `dropboxignore = "dropboxignore.cli:main"` → `dbxignore = "dbxignore.cli:main"`; same for the daemon shim (`dropboxignored` → `dbxignored`).
- **Logger name**: `dropboxignore` → `dbxignore` (changes log message `name=` column; matches the Python package).
- **Rule file name**: **keeps `.dropboxignore`** — it's user-config, renaming would break existing users; and `.dropboxignore` is descriptive where `.dbxignore` requires translation. Gitignore-family names (`.dockerignore`, `.npmignore`) are all descriptive, not abbreviated.
- **State / log directory**: `user_state_dir()` currently composes `<base>/dropboxignore/` — rename to `<base>/dbxignore/`. Existing v0.2.0 installs on disk have `~/.local/state/dropboxignore/` (Linux) or `%LOCALAPPDATA%\dropboxignore\` (Windows); new installs use the `dbxignore` directory. Mirror the XDG-legacy-fallback pattern from v0.2.0: read from both during migration, write only the new one, log WARNING with instructions to delete the old.
- **systemd unit name**: `dropboxignore.service` → `dbxignore.service`. `install` writes the new unit; users upgrading will have the old unit file lingering — `uninstall` on v0.2.x would need to know about both names, OR we document "run `dropboxignore uninstall` from v0.2.x, then `dbxignore install`" as the migration path.
- **GitHub repo name**: optionally rename `kiloscheffer/dropboxignore` → `kiloscheffer/dbxignore`. GitHub auto-redirects old URLs so README links, clones, and `git remote` entries continue to work without breaking changes.
- **README / CHANGELOG / CLAUDE.md / docs/**: grep-and-replace `dropboxignore` → `dbxignore` with discretion (don't rewrite CHANGELOG entries about previously-shipped behavior — those are historical; do rewrite command examples and install instructions).

**SemVer implication**: this is a breaking change (pip install target, CLI command, state directory location all move). Ride a MINOR bump with explicit **Breaking** CHANGELOG callouts per the repo's pre-1.0 convention. Likely shipped as v0.3.0 or a dedicated v0.2.x bump depending on when it lands.

**Migration for existing users** (on v0.2.0 from GitHub Release source install):
1. `dropboxignore uninstall --purge` (v0.2.0 CLI — clears markers, removes systemd unit, removes state/log dir). Explicitly documented as the pre-rename cleanup step.
2. `uv tool uninstall dropboxignore`.
3. `pip install dbxignore` (once v0.3.0+ is on PyPI).
4. `dbxignore install`.
5. `.dropboxignore` rule files keep working — no rename needed.

**Courtesy**: a brief note to Michał Karol letting him know we encountered a name collision and renamed. His project isn't affected; goodwill move. Not required.

Touches: `pyproject.toml`, `src/dropboxignore/` → `src/dbxignore/` (directory + imports), `tests/**` (imports), `README.md`, `CLAUDE.md`, `CHANGELOG.md` (new entry for the rename, not rewriting old), `docs/superpowers/**` (spec/plan references), `src/dropboxignore/install/linux_systemd.py` (UNIT_NAME constant), `src/dropboxignore/install/windows_task.py` (task name), `pyinstaller/dropboxignore.spec` (output names), release workflow (`dropboxignore.exe` asset names). Optional: rename the GitHub repo.

**Status: RESOLVED in v0.3.0.** Option II scope adopted (everything except `.dropboxignore` rule file and `com.dropbox.ignored` marker key). Clean-break upgrade path (Option A from brainstorm) chosen — no migration code; users run `dropboxignore uninstall --purge` → `pip install dbxignore` → `dbxignore install`. GitHub repo renamed. v0.2-era Linux legacy state-path fallback removed in the same release since clean-break left it with no callers. Spec: `docs/superpowers/specs/2026-04-23-v0.3-dbxignore-rename.md`. Plan: `docs/superpowers/plans/2026-04-23-v0.3-dbxignore-rename.md`. Landed in PR #22.

## 13. Bump CI actions off Node.js 20

Every CI run (test.yml, release.yml, commit-check.yml — anywhere JavaScript-based GitHub Actions run) emits a deprecation annotation:

> Node.js 20 actions are deprecated. The following actions are running on Node.js 20 and may not work as expected: `actions/checkout@v4`, `astral-sh/setup-uv@v5`, `softprops/action-gh-release@v2`. Actions will be forced to run with Node.js 24 by default starting June 2nd, 2026. Node.js 20 will be removed from the runner on September 16th, 2026.

The current action versions we use were contemporary when the workflows were written but are now trailing edge. Bump each to its latest major that declares `using: 'node24'` in `action.yml`:

- `actions/checkout@v4` → `actions/checkout@v5` (widely adopted, low risk)
- `astral-sh/setup-uv@v5` → check latest (v6 or newer at time of bump; younger action, verify API parity)
- `softprops/action-gh-release@v2` → check for a v2.x patch release with Node 24 support, or bump to v3 if released

**Urgency:** low until June 2026 (Node 24 forced-default), medium after that (workflows start breaking for any action that hasn't upgraded), hard stop September 2026 (Node 20 removed from the runner).

**Test strategy:** bump one action per commit, dispatch-run `release.yml` after each via `gh workflow run release.yml --ref <branch>` (courtesy of item 9). A bump that breaks surfaces in seconds via the dry-run — no need to cut a tag to test.

Touches: `.github/workflows/test.yml`, `.github/workflows/release.yml`, `.github/workflows/commit-check.yml`.

**Status: RESOLVED 2026-04-25.** Bumped 5 actions across `test.yml` and `release.yml` (`commit-check.yml` was already on `actions/checkout@v5`):

- `actions/checkout` v4 → v5 (followup-recommended; matches existing `commit-check.yml` pin)
- `astral-sh/setup-uv` v5 → v7 (latest moving major-version tag; v6 still on node20, no v8 major-tag yet)
- `softprops/action-gh-release` v2 → v3 (followup predicted; latest moving major)
- `actions/upload-artifact` v4 → v7 (NOT in the followup's literal list — discovered while verifying named actions; same node20 root cause)
- `actions/download-artifact` v4 → v8 (same — not in followup; same root cause)

Per item 13's test strategy, one commit per action so a future regression bisects to a single bump. Test.yml's actions get validated by every push-triggered CI run; release.yml's release-only actions (publish-github, publish-pypi, build) need a `workflow_dispatch` run to fully exercise — courtesy of item 9.

The two un-bumped actions (`commit-check/commit-check-action@v2.6.0`, `pypa/gh-action-pypi-publish@release/v1`) are **composite actions**, not Node-based — they're shell-script orchestrators and immune to the Node 20 deprecation entirely.

## 14. Flaky `test_run_refuses_when_another_pid_is_alive`

`tests/test_daemon_singleton.py::test_run_refuses_when_another_pid_is_alive` failed once during the PR #22 pre-flight full-suite run on Linux (Python 3.14.2), then passed on rerun and passed in isolation. Classic flaky-test signal — likely a psutil race between the test's PID-alive check and concurrent pytest worker processes (no `-p no:xdist` in our config, but other subprocess-launching tests could also perturb the system-wide process table).

Because the test uses real OS primitives (psutil PID enumeration via `os.kill(pid, 0)` or similar), it's sensitive to which processes the runner happens to have at that moment. Single observation so far — worth logging rather than pre-emptively fixing.

**Fix candidates if it recurs:**
- Mock `psutil.pid_exists` in the test rather than relying on a real alive PID (simpler, loses integration coverage).
- Acquire a sentinel process under the test's control (e.g., spawn a short-lived subprocess with `subprocess.Popen(['sleep', '5'])`, use its PID, `terminate()` at teardown) — avoids the "borrow someone else's PID" pattern.
- Retry the test once on failure via `pytest-rerunfailures` — papers over the root cause; last resort.

**Urgency:** low until second observation. Note in CHANGELOG if it recurs on a user-visible CI run.

Touches: `tests/test_daemon_singleton.py` (scope depends on chosen fix).

## 15. CHANGELOG bottom links still reference the old repo URL

`CHANGELOG.md` bottom links for `[0.2.1]`, `[0.2.0]`, `[0.1.0]` all point at `https://github.com/kiloscheffer/dropboxignore/releases/tag/...` rather than the renamed `kiloscheffer/dbxignore`. GitHub's rename-redirect covers these URLs transparently so click-through works, but the canonical path would render cleaner.

Two approaches:
- **Update all three links to `kiloscheffer/dbxignore`.** Style-consistent with the new `[0.3.0]` link. Argument: the `CHANGELOG.md` file is documentation for *the current repo*, not a historical artifact of the old one.
- **Leave as-is.** Argument: those releases genuinely happened under `kiloscheffer/dropboxignore` — the URLs are accurate-for-the-time. Redirects cover functionality.

**Recommendation:** update. Consistent canonical paths beat historical accuracy for a doc that gets read forward, and redirect chains add perceptible latency on slow connections.

**Urgency:** trivial. Candidate for a single-commit `docs(changelog)` PR whenever.

Touches: `CHANGELOG.md` (three bottom-link URLs).

**Status: RESOLVED 2026-04-24.** All three bottom-link URLs switched from `kiloscheffer/dropboxignore` to `kiloscheffer/dbxignore`, matching the existing `[0.3.0]` link. Bundled with item 17 in the same `docs(changelog)` PR.

## 16. `markers.py` NotImplementedError message references v0.3 as unreleased

`src/dbxignore/markers.py:28` reads:

```python
raise NotImplementedError("macOS support is planned for v0.3.")
```

This message pre-dates the rename — it was written when v0.3 was the hypothetical "macOS release." Now that v0.3.0 has shipped as the rename release (macOS still not included per the spec's non-goals), the message is misleading: a macOS user installing v0.3.0 and hitting this error is told "it's planned for v0.3" — which is the version they already have.

**Fix:** replace with either `"macOS support is planned for a future release."` (version-free, can't rot) or `"macOS support is not implemented — v0.4+."` (explicit roadmap hint, still needs an update if v0.4 doesn't include it).

**Urgency:** low, but user-facing. Anyone running v0.3.0 on macOS hits this message — wrong information to show them.

Touches: `src/dbxignore/markers.py` (one line).

**Status: RESOLVED 2026-04-24.** Replaced the rotted `"macOS support is planned for v0.3."` with the version-free `"macOS support is planned for a future release."` (Option A from the Fix section — the recommended choice because it can't rot the same way again). One-line edit in `src/dbxignore/markers.py:28`.

## 17. `CHANGELOG.md` header still says "dropboxignore"

`CHANGELOG.md:3` reads "All notable changes to dropboxignore are documented here." — pre-rename text that survived the v0.3.0 sweep. The per-version entries below it (including the v0.3.0 rename body itself) all use `dbxignore` correctly; only the file's introductory sentence is stale.

Same flavor as item 15 (CHANGELOG bottom links): a one-line `dropboxignore` → `dbxignore` substitution that nothing functionally depends on but reads as residual rename debt to anyone landing on the file.

**Fix:** one-character edit on line 3 — `dropboxignore` → `dbxignore`.

**Urgency:** trivial. Bundle with item 15 in a single `docs(changelog)` PR rather than spawning a one-line PR of its own.

Touches: `CHANGELOG.md` (one line).

**Status: RESOLVED 2026-04-24.** Header line 3 updated to read "All notable changes to dbxignore are documented here." Bundled with item 15 (per its own recommendation) in the same `docs(changelog)` PR.

## 18. Flaky `test_daemon_reacts_to_dropboxignore_and_directory_creation`

`tests/test_daemon_smoke.py::test_daemon_reacts_to_dropboxignore_and_directory_creation` failed once on `windows-latest` during PR #30's initial CI run, then passed on rerun and on the parallel push-triggered run of the same commit. Same-commit duration discrepancy was striking: 0.38s passing vs 3.75s failing — 10× slower on the failing leg, with the second `_poll_until` (3.0s timeout) falling off its cliff on the assertion that `build/keep/` should stay marked.

The test's shape: create `.dropboxignore` with `build/` → wait for `build/` to be marked → append `!build/keep/` to the rule file → create `build/keep/` directory → assert the child stays marked (because the conflict detector drops the inert negation). The first poll passed on the failing run; it was the second one (post-rule-append + post-dir-create) that timed out.

The v0.2.1 negation-semantics spec (`docs/superpowers/specs/2026-04-21-dropboxignore-negation-semantics.md`) documents this race as "masked on Windows due to `ReadDirectoryChangesW` dispatching RULES before DIR_CREATE" — this observation shows the masking isn't absolute under runner load.

Distinct from item 14 (which tracks a flaky daemon *singleton* test in `test_daemon_singleton.py` — a psutil PID-enumeration race, not a watchdog event-ordering race). Same family (daemon tests flake-prone under runner load), different mechanism, different fix candidates.

**Fix candidates if it recurs:**
- Widen the `_poll_until` timeout on the second assertion from 3.0s to ~5–8s — cheapest, preserves real-daemon integration signal.
- Replace the timing-sensitive poll with an explicit flush/drain helper if reconcile or the debouncer exposes one (e.g., synchronous `daemon._dispatch` invocation after a rule write).
- Mock the watchdog layer and drive events deterministically — loses real-OS integration coverage.

**Urgency:** PROMOTED 2026-04-25. Second observation occurred during PR #38's PR-triggered Windows CI run. Same test, same assertion (`build/keep/ should stay marked — the negation is dropped`), same shape — the second `_poll_until` (3.0s timeout) timed out. Same-commit duration discrepancy was again striking: 27s passing (push-triggered) vs 1m26s failing (PR-triggered). Re-run of the failed PR-triggered job passed, confirming flake. PR #38's diff was a structural refactor (extract detection layer to `rules_conflicts.py`) — touches no daemon, watchdog, or debouncer code, ruling out regression as the cause. Per item 18's own "if it recurs on a user-visible CI run (not a PR retry)" guidance, this second occurrence triggers a CHANGELOG note in the next release. The cheapest fix candidate from the list above (widen the `_poll_until` timeout on the second assertion from 3.0s to ~5–8s) is the recommended next move.

Touches: `tests/test_daemon_smoke.py` (scope depends on chosen fix); `CHANGELOG.md` (one-line note in the next release describing the flake + the chosen fix).

**Status: RESOLVED 2026-04-25 (in this PR).** Implemented the cheapest fix candidate from the list above — widened the second `_poll_until` timeout from 3.0s to 5.0s. Chose 5.0s (low end of the followup's "5–8s" range) over 8.0s because the test has three sequential `_poll_until` calls (2.0s + 5.0s + 3.0s = 10s) and pytest's per-test timeout is 10s; bumping to 8.0s would risk pytest-timeout failures on the rare runs where multiple polls slow simultaneously. CHANGELOG note added under `[Unreleased]` per the "Note in CHANGELOG if it recurs on a user-visible CI run" gate. Comment at the call site explains the choice for future readers, citing both observations (PR #30, PR #38) so a third-occurrence reader has full context.

## 19. Items 8, 9, 10 lack inline RESOLVED markers (tracker hygiene)

The bottom Status section lists items 8–10 as resolved ("8–10 in v0.2.1 via PRs #15/#18/#19"), but the items' own bodies have no inline `**Status: RESOLVED**` marker. A reader scanning the tracker top-down sees three open-looking items with no closure indication and has to scroll to the Status section to learn they're resolved — a noticeable asymmetry from items 11–17, which all carry inline markers.

The cause is just timing. Items 8–10 were resolved in v0.2.1 (PRs #15/#18/#19) before the inline-marker convention was established. Items 11–12 got inline markers in PR #24 when the convention started; items 13, 15–17 in the 2026-04-24/25 backlog sweep. Items 8–10 never got backfilled.

**Fix:** add three short `**Status: RESOLVED in v0.2.1.** Landed in PR #N.` lines to the bodies of items 8, 9, 10. Mapping each item to its PR (#15, #18, or #19) requires a one-time `gh pr view <N>` cross-check against the items' stated changes. Three single-line additions total.

**Urgency:** trivial. Tracker hygiene only — improves top-down readability, doesn't block anything. Discovered during the 2026-04-25 backlog sweep while running a `grep "^## [0-9]\|^\*\*Status: RESOLVED"` cross-reference against the tracker. Bundle with any other tracker-only PR or take as a one-commit standalone.

Touches: `docs/superpowers/plans/2026-04-22-dropboxignore-negation-polish-followups.md` (3 lines added).

**Status: RESOLVED 2026-04-25 (in this PR).** Backfilled the three inline RESOLVED markers per the proposed fix. Surprise finding during the cross-check: the Status section's attribution of items 8–10 to "PRs #15/#18/#19" was wrong — PRs #15 and #19 were docs-only (tracking + adding followup items respectively), and **PR #18 alone resolved all three items** in three commits. Status section attribution corrected from "PRs #15/#18/#19" to "PR #18 (single PR, three commits)". 4 single-line additions total — one more than this item's "three single-line additions" estimate, because of the Status correction.

## 20. `state.write()` is not atomic — torn JSON could bypass singleton check

`src/dbxignore/state.py`'s `write()` calls `path.write_text(...)`, which truncates then writes. A crash between truncation and completion (SIGKILL, power loss) leaves a zero-length or partial `state.json`. On next startup, `_read_at` catches `json.JSONDecodeError`, logs WARNING, and returns `None`; `daemon.run`'s singleton check (`if prior is not None and _is_other_live_daemon(prior.daemon_pid)`) sees `None` and proceeds — a second daemon instance can start while the first is still alive.

**Fix:** standard write-temp-then-`os.replace` pattern. Write to `state.json.tmp` in the same directory, then `os.replace(tmp, final)` — POSIX-atomic on Linux; uses `MoveFileExW(MOVEFILE_REPLACE_EXISTING)` on Windows. ~5 lines added to `write()`, no API change.

**Urgency:** low. Hits only on hard-crash within the few-ms write window AND the user re-runs `dbxignore daemon` before the prior process exits — narrow conjunction. But the failure mode is silent (two daemons writing markers concurrently) and hard to attribute back to corrupt state.

Touches: `src/dbxignore/state.py` (`write()`). Optional: regression test that injects a partial file and asserts singleton check still blocks — would need a richer "prior daemon alive but state corrupt" protocol than the current code expresses.

**Status: RESOLVED 2026-04-25 (PR #45).** `state.write()` now writes to `state.json.tmp` and `os.replace`s into place. `_purge_local_state()` also cleans a leaked tmp file if one exists. Two regression tests added (`test_write_leaves_no_tmp_file`, `test_write_overwrites_stale_tmp`). The richer "corrupt state vs. live daemon" coverage suggested in the optional clause was not pursued — would require expressing a state shape the code doesn't currently model.

## 21. Windows backend `is_ignored` only catches `FileNotFoundError`

`src/dbxignore/_backends/windows_ads.py`'s `is_ignored` opens the `:com.dropbox.ignored` ADS stream and returns `False` on `FileNotFoundError`, but propagates any other `OSError`. The matching read-side guard in `reconcile._reconcile_path` catches `FileNotFoundError` and `PermissionError` only — the `OSError(ENOTSUP|EOPNOTSUPP)` arm sits on the *write* side and is Linux-shaped.

So an unexpected `OSError` from `is_ignored` (e.g. `EIO` on a flaky network drive, network-disconnect on a mapped drive) escapes the per-file try/except, propagates out of `_reconcile_path`, and kills the per-root thread-pool worker in `_sweep_once` without landing in `Report.errors`. CLAUDE.md's stated contract for the analogous Linux ENOTSUP case is "log WARNING, append to `Report.errors`, continue the sweep" — applying the same shape on the read side keeps the contract uniform across platforms.

**Fix:** broaden the read-side `except` in `_reconcile_path` to catch `OSError`, classify by `errno` in the log line, append to `Report.errors`. ~5 lines.

**Urgency:** low. Network-drive Dropbox roots are uncommon and locked-file edges on Windows mostly map cleanly to `PermissionError`. Worth doing because "silent worker death on one root" is a hard-to-debug failure mode — markers stop being maintained on that root and the user sees nothing in the report.

Touches: `src/dbxignore/reconcile.py` (`_reconcile_path` read-side except).

**Status: RESOLVED 2026-04-25 (PR #45).** Added a generic `OSError` arm after the existing `FileNotFoundError` / `PermissionError` arms — logs WARNING with errno classification, appends to `Report.errors`, returns `None`. Two regression tests cover the EIO and read-side ENOTSUP paths. The fix is in `_reconcile_path`, not in the Windows backend itself — the title's "Windows backend `is_ignored`" framing was misleading; the right layer to broaden was the reconcile loop, since the same shape covers Linux ENOTSUP-on-read too.

## 22. `README.md` describes a legacy state-path fallback that v0.3 removed

`README.md:151` reads "Installs that pre-date the XDG move are read transparently from the legacy `~/AppData/Local/dbxignore/state.json` for one release, with a WARNING; the next daemon write persists to the XDG path." The path name was rename-swept (`dropboxignore` → `dbxignore`) in commit `48e43a3`, but the underlying fallback was removed in commit `61e95a9` (one commit later). `state.py` has no `_legacy_linux_path()` function and no fallback branch; CLAUDE.md and `CHANGELOG.md` v0.3.0 both document the removal.

A v0.2.x user who skips `uninstall --purge` and reads only the README will silently lose their state on first run of v0.3+. CHANGELOG carries the authoritative text; README is just stale.

**Fix:** rewrite the paragraph to describe the actual upgrade path — clone the CHANGELOG v0.3.0 wording. Something like: "Upgrading from v0.2.x: run `dropboxignore uninstall --purge` first to clear v0.2 state and markers, then `pip install dbxignore`. The v0.2-era legacy state-path fallback was removed in v0.3 — there is no auto-migration."

**Urgency:** low (CHANGELOG is authoritative), but README is the higher-traffic doc.

Touches: `README.md` (~3 lines around line 151).

**Status: RESOLVED 2026-04-25 (PR #46).** Resolved by **deletion**, not rewrite. The README already has a top-level `## Upgrading from v0.2.x` section at line 5 describing the correct manual upgrade path (`dropboxignore uninstall --purge` first, then `pip install dbxignore`); the stale sentence at line 151 *contradicted* that section by claiming an auto-migration. The bullet's first half (`$XDG_STATE_HOME/dbxignore/state.json` with `~/.local/state/...` fallback) stands on its own. Note: this item's prescribed fix ("rewrite the paragraph") turned out to be wrong once the surrounding README structure was checked — same lesson as item 21 (prescribed fix at the wrong layer). Single-line deletion.

## 23. `RuleCache._applicable` does multi-step lock-free reads of `_rules`

`src/dbxignore/rules.py`'s `_applicable` walks ancestor paths and calls `self._rules.get(ancestor / IGNORE_FILENAME)` once per ancestor under the lock-free contract documented in CLAUDE.md ("reconcile reads the cache lock-free, single-op `.get()`s"). Each `.get()` is GIL-atomic on its own, but the loop is not — between two calls the debouncer thread can `reload_file` or `remove_file` and change which ancestor's rules apply.

Worst observable outcome: one path during one sweep tick is matched against a slightly stale ancestor view — recoverable on the next watchdog event or hourly sweep. So the system isn't *broken*, but CLAUDE.md's "single-op `.get()`s" wording arguably promises stronger per-traversal consistency than `_applicable`'s loop delivers.

**Fix candidates:**
- **Snapshot under the lock once per `_applicable` call.** Acquire `self._lock`, build a `dict[Path, _LoadedRules]` for the relevant ancestors, release, then iterate. Trades a brief lock acquisition per file for per-traversal consistency. May regress sweep wall-clock — CLAUDE.md notes locking was avoided on the read path deliberately.
- **Tighten the CLAUDE.md wording** to acknowledge per-traversal consistency isn't guaranteed and is OK because the next event recovers. Documents reality without code changes.
- **Status quo** — accept the borderline drift; downstream behavior is convergent.

**Urgency:** very low. No observed bug; the sweep is event-driven and self-healing. Filing this so a future reader walking `_applicable` doesn't re-derive the same uncertainty cold.

Touches: `src/dbxignore/rules.py` (`_applicable`) OR `CLAUDE.md` (RuleCache lock-free gotcha), depending on which arm gets picked.

**Status: RESOLVED 2026-04-25 (PR #49).** Resolved via the doc-tightening arm — code change deferred indefinitely. CLAUDE.md's lock-free wording in the Architecture section now explicitly acknowledges that multi-step traversals like `_applicable` aren't transactional and may see slightly-stale ancestor views, with downstream convergence (next watchdog event or hourly sweep recovers) as the design rationale. The snapshot-under-lock arm was not pursued — would regress sweep wall-clock for a drift no one has observed, and the new wording lets future readers walking `_applicable` skip the same uncertainty.

## 24. `state._decode()` raises on shape-mismatched `state.json`, bypassing `_read_at`'s graceful fallback

`src/dbxignore/state.py`'s `_read_at()` defends against `json.JSONDecodeError` by logging WARNING and returning `None` — the daemon then treats the situation as "no prior state" and starts fresh. But `_decode(raw)` is called *outside* the try/except. Inside `_decode`, the `last_error` branch directly indexes `raw["last_error"]["time"]`, `raw["last_error"]["path"]`, and `raw["last_error"]["message"]` with no fallback. A `state.json` that's valid JSON but shape-mismatched (hand-edited; produced by a newer/older schema; partially corrupt in a way the JSON parser still accepts) raises `KeyError` or `TypeError` from `_decode`, which propagates out of `_read_at` and out of `daemon.run`'s `prior = state_module.read()` call — daemon crashes on startup.

The atomic-write fix from item 20 (PR #45) made *partial-write* corruption nearly impossible, but does not address shape-mismatch. The asymmetry is: write-side is now defensive; read-side parses defensively at the JSON layer but trusts `_decode` to produce a `State` unconditionally.

**Fix:** broaden the `_read_at` except to `(json.JSONDecodeError, KeyError, TypeError, ValueError)`, log WARNING, return `None`. ~3 lines. Same recovery shape as the existing JSONDecodeError arm.

**Urgency:** low. systemd's `Restart=on-failure RestartSec=60s` would recover the daemon eventually (each restart attempts to re-read state and would retry the crash until something rewrites `state.json`). Worth fixing because (a) the recovery is loud-and-slow rather than silent-and-fast, and (b) any future schema migration adding required fields would re-introduce the same crash for users upgrading from older versions. Filing rather than fixing immediately to keep the second-look pass purely doc-only and let the fix bundle with any future schema work.

Touches: `src/dbxignore/state.py` (`_read_at` except clause).

**Status: RESOLVED 2026-04-26 (PR #50).** Moved `_decode(raw)` inside the existing try/except and broadened the except to `(json.JSONDecodeError, KeyError, TypeError, ValueError)`. Same recovery shape as before — log WARNING, return None, daemon treats as "no prior state" and starts fresh. Three regression tests cover the KeyError (missing nested sub-key), TypeError (last_error is a string), and ValueError (stored datetime no longer parses) arms explicitly. The atomic-write fix from item 20 + this read-side defense form the symmetric pair the first review pass missed — generalizable lesson for I/O hardening: design read and write defenses together.

## 25. `find_containing()` is called twice per watchdog event — once in `_classify`, once in `_dispatch`

`src/dbxignore/daemon.py`'s `_classify(event, roots)` calls `find_containing(src, roots)` purely as a gate (return value discarded). When `_classify` returns a non-None classification, `_dispatch(event, cache, roots)` then calls `find_containing(src, roots)` *again* to obtain the actual root. Two passes over the roots list per accepted event.

Per call cost is small — `find_containing` is `O(R)` where R is the number of Dropbox roots, and most users have R=1 — but the duplication is in the watchdog event path, fired post-debouncer for every accepted event. The redundancy is sloppy more than slow.

**Fix:** widen `_classify`'s return shape from `tuple[EventKind, str] | None` to `tuple[EventKind, str, Path] | None`, including the root. `_dispatch` then unpacks the root from the classification instead of calling `find_containing` a second time. Updates to one production call site (`_dispatch`) and any test that constructs classification tuples directly.

**Urgency:** very low. Not in any per-file hot path; per-event work is post-debouncer. Filed because it's an obvious tightening that surfaced from a tracing audit, not because there's a measurable cost.

Touches: `src/dbxignore/daemon.py` (`_classify` return type, `_dispatch` unpack, watchdog handler unpack), and any test in `tests/test_daemon_dispatch.py` that constructs classification tuples by hand.

**Status: RESOLVED 2026-04-26 (PR #50).** `_classify`'s return type widened from `tuple[EventKind, str] | None` to `tuple[EventKind, str, Path] | None`. `_dispatch` now unpacks the root from the classification (one fewer `find_containing` call per event); `_WatchdogHandler.on_any_event` discards the root since it only needs the kind+key for the debouncer. Two existing `test_classify_*` tests grew an assertion that the returned root matches expectations — verifies the new return shape rather than just unpacking it silently.

## 26. `install._common.detect_invocation` has an unreachable `RuntimeError` branch

`src/dbxignore/install/_common.py`'s `detect_invocation()` ends with:

```python
python = shutil.which("python3") or sys.executable
if not python:
    raise RuntimeError(
        "dbxignored not on PATH and no python3 found; "
        "run `uv tool install .` from the dbxignore checkout first"
    )
return Path(python), "-m dbxignore daemon"
```

`sys.executable` is always a non-empty string in any running Python process, so the `or sys.executable` clause makes `python` always truthy, and `if not python` can never be True. The `RuntimeError` is never raised. The docstring documents this error path as load-bearing (`Raises RuntimeError if no python3 is on PATH…`), but the contract diverges from the actual behavior.

Preexisting bug from `linux_systemd._detect_invocation` that was faithfully preserved during the PR 4 extraction (see PR #57). Surfaced when the function got its own module + dedicated docstring and the inconsistency became more visible.

**Fix candidates:**

- **Drop the guard**, treat `sys.executable` fallback as authoritative — the most honest rendering of current behavior. One-line change to `_common.py` plus a docstring trim.
- **Replace the `or sys.executable` with `None`** so the guard is reachable when no `python3` is on PATH (mostly Windows installs without `python3` aliased to `python.exe`). Gives the documented error-path teeth. Requires verifying the existing test `test_detect_invocation_falls_back_to_python_module` still passes; it might already cover this case.
- **Document as intentional**: keep the safety belt-and-braces, rewrite the docstring to acknowledge that the branch is currently unreachable but kept as a guard against future Python installs where `sys.executable` could be empty (vendored embeddings, `multiprocessing` on certain spawn modes, etc.). Lowest-effort but doesn't fix the divergence.

**Urgency:** low. Hits no production code path, surfaces only as a doc-vs-code inconsistency. Worth fixing when next touching the install layer to avoid future readers re-deriving the same uncertainty cold.

Touches: `src/dbxignore/install/_common.py` (function body + docstring); possibly `tests/test_install_common.py` if the fallback guard's reachability changes.

## 27. Intel Mac (x86_64) Mach-O binary build leg

v0.4 ships arm64 Mach-O binaries only (built on `macos-latest` which aliases to `macos-14` / Apple Silicon). Intel Mac users install via the universal Python wheel from PyPI — documented in the README's macOS section.

If x86_64 demand surfaces, add a `macos-13` runner to `.github/workflows/release.yml` (similar shape to `build-macos`, different artifact name to avoid collision). The `pyinstaller/dbxignore-macos.spec` is already arch-agnostic (`target_arch=None` follows the runner), so no spec changes needed.

**Fix candidates:**

- **Add `build-macos-x86_64` job** alongside the existing `build-macos`. Two parallel arm64 + x86_64 artifacts on the GitHub Release.
- **Switch to universal2** (covered by item #28 below) — single artifact, more complex setup. Mutually exclusive with the dual-build approach.

**Urgency:** low until demand surfaces. The user-base is small enough that field signals will reach via GitHub Issues if Intel users hit the gap.

Touches: `.github/workflows/release.yml` (new build job + publish-github files: list extension); README's macOS section (remove the "Apple Silicon only" caveat).

## 28. Universal2 macOS binary as the single artifact

Apple's `universal2` Mach-O format bundles arm64 + x86_64 in one binary. Would replace the current arm64-only artifact (and the eventual x86_64 artifact from item #27) with a single one. PyInstaller supports this via `target_arch="universal2"` in the spec, but the build environment must have a universal2 Python interpreter.

**Fix candidates:**

- **Switch `pyinstaller/dbxignore-macos.spec` to `target_arch="universal2"`** and verify `macos-latest`'s Python is universal2-built (likely yes — Homebrew Python on `macos-14` ships as universal2). Test by inspecting the resulting binary with `lipo -info`.
- **Defer until item #27 actually fires** and decide between dual-build vs universal2 at that time.

**Urgency:** very low. Quality-of-life only; doesn't change what users can install. Not pressing until either x86_64 demand surfaces (then we choose between #27 and #28) or some other reason makes the unified artifact preferable.

Touches: `pyinstaller/dbxignore-macos.spec` (`target_arch` change); `release.yml` (potentially simplify if #28 obviates the need for a second build job from #27).

## 29. Codesigning + notarization for macOS binaries

Currently the GitHub-Release Mach-O binaries are unsigned. macOS Gatekeeper refuses unsigned binaries on first launch with "cannot be opened because it is from an unidentified developer." The README documents the workaround (`xattr -d com.apple.quarantine /usr/local/bin/dbxignore`), but a proper signed-and-notarized binary would just work.

Requires:

1. An **Apple Developer Program** membership (~$99/year — recurring).
2. A **Developer ID Application** signing certificate.
3. An **app-specific password** for the notarization service.
4. GitHub Secrets to hold the certificate (base64'd `.p12`), the certificate password, and the notarization credentials.
5. A `codesign` step in `release.yml` after the PyInstaller build, then a `xcrun notarytool submit` step.

**Fix candidates:**

- **Defer indefinitely.** The current workaround is one shell command; users who hit it can copy-paste from the README. The $99/year cost + the secret-management complexity is a real ongoing burden.
- **Adopt** if Gatekeeper-bypass friction becomes a frequently-reported pain point or if a more polished install story becomes load-bearing for adoption.

**Urgency:** lowest of the four v0.4 followups. Worth filing for visibility but not for action absent a concrete user-pain signal.

Touches: `.github/workflows/release.yml` (signing + notarization steps); GitHub Secrets (cert, password, notarization creds); README's macOS section (remove the Gatekeeper-bypass instructions).

## 30. Windows-aware single binary — collapse `dbxignore.exe` + `dbxignored.exe`

The project currently ships TWO Windows binaries from the same codebase: `dbxignore.exe` (PyInstaller `console=True`, interactive CLI, brief help-flash on double-click) and `dbxignored.exe` (`console=False`, daemon, no console window when launched by Task Scheduler). The duplication exists because PyInstaller's `console=True/False` switch is binary — there's no built-in "attach to parent console if there is one, else stay silent" mode — and the project has three distinct UX requirements that no single console-mode satisfies:

- **Terminal users** want output to flow to their terminal.
- **Task Scheduler launches** want NO console window pop at every login.
- **Double-click users** want some indication the `.exe` did something (currently a brief help flash via `console=True`).

A "best of all worlds" single binary calls `AttachConsole(ATTACH_PARENT_PROCESS)` early in startup. If attach succeeds (terminal launch), stdout/stderr flow to the parent terminal as if `console=True`. If attach fails (double-click, Task Scheduler), the binary runs without a console — like `console=False`. For double-click specifically, a small Windows MessageBox can pop saying "dbxignore is a CLI tool — open a terminal and run `dbxignore --help`" so the user gets feedback instead of silent no-op.

Some Windows-native tools (`go.exe`, `winget.exe`) implement this pattern. PyInstaller doesn't add it automatically.

**Fix candidates:**

- **`AttachConsole` via stdlib `ctypes`** — call `ctypes.windll.kernel32.AttachConsole(-1)` (where `-1` = `ATTACH_PARENT_PROCESS`) early in `cli.main` before click parses argv. On success, redirect `sys.stdout`/`sys.stderr` to the attached console's handles. On failure, leave stdout/stderr as-is. Then add a no-args + no-attached-console branch that pops `ctypes.windll.user32.MessageBoxW(...)` with a "open a terminal" hint. No new dependencies.
- **`AttachConsole` via `pywin32`** — same logic with `win32console.AttachConsole` and `win32api.MessageBox`. Cleaner ergonomics but adds `pywin32` to runtime deps (large, Windows-only).
- **Status quo** — keep both binaries. The current state works correctly; the duplication has a clear UX justification per binary. ~20MB of redundant binary content per release is the only concrete cost.

**Scope of follow-on work** if implemented:

- `pyinstaller/dbxignore.spec`: drop the second `EXE(...)` block. Switch the remaining one to `console=False`.
- `pyinstaller/dbxignore-macos.spec`: same treatment if simplifying macOS too. (macOS has no console-mode distinction — both binaries are CLI Mach-O — so the duplication there is purely Unix `<name>d` convention.)
- `pyproject.toml`: drop `[project.scripts].dbxignored` entry.
- `src/dbxignore/cli.py`: drop `daemon_main` shim. Add the `AttachConsole` + MessageBox logic at the top of `main()` (Windows-only branch).
- `src/dbxignore/install/_common.py`: `detect_invocation()` no longer searches for `dbxignored` shim.
- `src/dbxignore/install/windows_task.py`: binary-mode invocation now points at `dbxignore.exe daemon` (was `dbxignored.exe`).
- `src/dbxignore/install/linux_systemd.py`, `src/dbxignore/install/macos_launchd.py`: `ExecStart` / `ProgramArguments` reference `dbxignore daemon` instead of `dbxignored`.
- README's "Install (.exe)" section: simplify the binary list (just `dbxignore.exe`).
- CHANGELOG entry: noteworthy as a breaking change for users with `dbxignored.exe` in PATH or referenced in custom configs.

**Urgency:** low. The current two-binary state is correct and has clear UX rationale. Triggers for promotion: binary-size complaints, PyInstaller build-time friction, or a simplification effort post-1.0 that bundles this with other cross-platform installer cleanup. The three-context tradeoff (terminal / Task Scheduler / double-click) is the load-bearing constraint, not Unix `<name>d` aesthetics.

**Risks if implemented:**

- The `AttachConsole` path needs testing in PowerShell, cmd, Windows Terminal, Git Bash, and `powershell -NoProfile` minimal-shell scenarios. Each handles inherited handles slightly differently.
- The MessageBox branch adds a `user32.dll` dependency at startup; on locked-down Server Core systems without the GUI subsystem, that import would fail and the binary would crash before even reaching click. The branch needs a try/except wrapper.
- Beta-testing on real Windows installs across a few editions (Pro, Home, Server) before merging.

Touches: `pyinstaller/dbxignore.spec`, `pyinstaller/dbxignore-macos.spec` (optional but coherent), `pyproject.toml`, `src/dbxignore/cli.py`, `src/dbxignore/install/_common.py`, `src/dbxignore/install/windows_task.py`, `src/dbxignore/install/linux_systemd.py`, `src/dbxignore/install/macos_launchd.py`, `README.md` "Install (.exe)" section, `CHANGELOG.md`.

## 31. macOS PyInstaller binary missed `_cffi_backend` C extension

The v0.4.0a1 macOS arm64 binary failed at first launch with `ModuleNotFoundError: No module named '_cffi_backend'`. Reproduced by a beta tester (M2 MacBook Air, macOS Tahoe 26.4) running `dbxignore --version` after the README's documented Gatekeeper-bypass + `mv ... /usr/local/bin/` install dance.

The failing import chain: `dbxignore.cli` → `dbxignore.markers` → `dbxignore._backends.macos_xattr` line 25 (`import xattr`) → `xattr/__init__.py` → `xattr/lib.py` → `from cffi import FFI` → `cffi/__init__.py`'s `from _cffi_backend import ...`. `_cffi_backend.cpython-3XX-darwin.so` is a top-level C extension that ships *alongside* the `cffi` package on disk (a sibling, not a submodule), so PyInstaller's static AST trace from `cffi` doesn't reach it. `pyinstaller-hooks-contrib` ships a `hook-cffi.py` that should add it as a hidden import automatically; the v0.4.0a1 build skipping it points to version drift (PyInstaller installed via `uv run --with pyinstaller`, no version pin).

The bug is macOS-only. The Linux backend uses `os.{get,set}xattr` (Python stdlib, no cffi), the Windows backend uses raw `open(r"\\?\path:com.dropbox.ignored")` calls (no cffi); v0.4.0a1's Windows binary worked correctly through the same workflow.

CI didn't catch it because `build-macos` uploaded the artifact without ever executing it. A `./dist/dbxignore --help` step after the build exercises the full import chain (the bug fires at import time, before click parses argv) and would have failed the build instead of fails-on-first-tester.

**Fix candidates:**

- **Explicit `_cffi_backend` in `hiddenimports`** (chosen) — adds one entry to the macOS spec's existing list, alongside `watchdog.observers.fsevents`. Belt-and-suspenders the contrib hook so version drift can't silently re-introduce the regression. Same shape as the watchdog entry — both runtime-resolved imports the static analyzer misses.
- **Pin PyInstaller version** in the workflow's `--with pyinstaller` invocation. Trades one regression risk for another (stale PyInstaller missing future bug fixes). Not chosen.
- **Drop the `xattr` PyPI package, use `ctypes` against `libsystem`'s `getxattr(2)`** directly — eliminates the cffi dependency entirely. Larger surgery; the package was deliberately chosen for its `symlink=True`/NOFOLLOW semantics (see `_backends/macos_xattr.py:1-17`). Defer.

Mitigation (broader): smoke-test the built binaries in the release workflow's build legs before the artifact upload. Catches this regression class for both Windows and macOS.

**Status: RESOLVED 2026-05-01 (PR #71).** Two-part fix in the same PR. Part 1: added `_cffi_backend` to `pyinstaller/dbxignore-macos.spec`'s `hiddenimports` list. Part 2 (the broader regression net): added `<binary> --help` smoke tests to both `build` and `build-macos` legs of `.github/workflows/release.yml` after PyInstaller emits and before the artifact upload, so any future analyzer miss in any backend's transitive deps fails CI rather than ships. Tag-prep was authored against the wrong target version (v0.4.0a2 already existed from PR #63); shipped instead as `v0.4.0a3` so the beta tester can retest.

Touches: `pyinstaller/dbxignore-macos.spec`; `.github/workflows/release.yml`.

## 32. CLI polish — three small surface gaps trace to one shim design choice

A v0.4.0a3 ship-time CLI surface review surfaced three small gaps in the user-facing command line. They look unrelated at first glance but share a root cause (the `daemon_main` argv-rewrite shim in `cli.py:365-368`), so they're filed together for a coherent fix.

**Symptom 1 — no `--version` flag.** The v0.4 beta tester typed `dbxignore --version` expecting `0.4.0a1`. Click rejected it ("no such option: --version") and the tester proceeded to other checks. Universal "did the install land the right binary?" pattern; the omission is the only one of the three with a real use-case-from-the-wild trigger so far. `hatch-vcs` already writes `src/dbxignore/_version.py` (per `pyproject.toml:42`), so adding `@click.version_option(package_name="dbxignore")` to the group reads from `importlib.metadata` automatically — one decorator.

**Symptom 2 — `--verbose` is unreachable from `dbxignored`.** `daemon_main` does `sys.argv.insert(1, "daemon")` before calling `main()`, which means `dbxignored --verbose` is parsed as `dbxignore daemon --verbose`. But `--verbose` is on the group, not on the `daemon` subcommand, and Click requires group-level options to appear *before* the subcommand. So the flag is unreachable from the daemon shim entirely; users wanting verbose daemon output must set `DBXIGNORE_LOG_LEVEL=DEBUG` instead. Workaround works but isn't discoverable.

**Symptom 3 — bogus `Usage:` line in `dbxignored --help`.** `dbxignored --help` prints `Usage: dbxignored daemon [OPTIONS]` because Click constructs the usage line from `sys.argv[0]` (the program name as-typed: "dbxignored") plus the command-path it walked to dispatch ("daemon"). The two halves come from different worlds — the user typed only `dbxignored`, the synthetic `daemon` token was injected by the shim. Worse, the literal advertised string `dbxignored daemon` is **not runnable**: the shim prepends another `daemon` token (`['dbxignored', 'daemon', 'daemon']`) and Click rejects with "Got unexpected extra argument (daemon)." The Usage line is advertising a non-runnable invocation.

**Shared root cause.** All three are direct symptoms of the argv-rewrite shim approach to `dbxignored`. The shim was probably written for code reuse — share the `daemon` subcommand body between the long-form (`dbxignore daemon`) and the short shim (`dbxignored`) — but it costs more than it saves: the body is two lines (`from ... import daemon as daemon_mod; daemon_mod.run()`), so the reuse argument is thin, and the shim leaks into argv parsing in three observable ways.

**Fix candidates:**

- **Replace the shim with a standalone `@click.command`** (chosen direction). Define `daemon_main` as its own Click command that shares a small private helper with the existing `daemon` subcommand:

  ```python
  def _run_daemon() -> None:
      from dbxignore import daemon as daemon_mod
      daemon_mod.run()


  @main.command()
  def daemon() -> None:
      """Run the watcher + hourly sweep daemon (foreground)."""
      _run_daemon()


  @click.command()
  @click.option("--verbose", "-v", is_flag=True, help="Enable DEBUG-level logging.")
  @click.version_option(package_name="dbxignore")
  def daemon_main() -> None:
      """Run the watcher + hourly sweep daemon (foreground)."""
      _run_daemon()
  ```

  Net effect: `dbxignored --help` shows `Usage: dbxignored [OPTIONS]` (no leaked `daemon` token); `dbxignored --verbose` works directly; `dbxignored --version` works via Click's auto-binding to `importlib.metadata`. Add `@click.version_option(package_name="dbxignore")` to the `main` group in the same change so `dbxignore --version` also works.

- **Patch with `prog_name=`.** Click's `main()` accepts a `prog_name` kwarg that overrides the displayed program name in usage lines. `daemon_main` could call `main(prog_name="dbxignored")` to fix `sys.argv[0]` in the help output, but that doesn't elide the `daemon` token (it'd still show `Usage: dbxignored daemon [OPTIONS]`) and doesn't fix the unreachable-`--verbose` issue. Band-Aid for symptom 3 alone; not a real fix. Not chosen.

- **Status quo.** All three issues have workarounds: `dbxignore --help` for the version check, `DBXIGNORE_LOG_LEVEL` for verbose daemon output, and the bogus Usage line is mostly harmless (users either don't notice or shrug). Acceptable but worse than a one-touch rewrite.

**Urgency:** low. The `--version` symptom is the only one with a fired trigger (beta tester); the other two are latent UX papercuts. But the rewrite is small (~15 lines diff) and the three symptoms collapse to one fix, so the cost-to-fix ratio is favorable. Reasonable to bundle with the next CLI-touching change rather than ship as a standalone polish PR.

**Risks if implemented:**

- The shim behavior is referenced by `install/_common.detect_invocation` (which decides whether the daemon is being launched from a frozen binary or an editable Python install). Switching `daemon_main` from "argv shim" to "real command" doesn't change the function name or signature, so `detect_invocation` is unaffected — but verify before merging.
- `pyproject.toml`'s `[project.scripts].dbxignored = "dbxignore.cli:daemon_main"` keeps working unchanged (Click decorators don't change the function's callability).
- Tests that monkeypatch around `daemon_main` may break if they depend on the argv-mutation side effect. None observed at file time, but a test sweep is part of the PR.
- Item #30 (Windows-aware single binary via `AttachConsole`) would, if implemented, eliminate `dbxignored` entirely. Item #32's fix lives inside the two-binary world; if #30 lands first, #32's daemon-shim half is moot — but the `--version` arm of #32 still applies to the surviving `dbxignore` binary. Sequence-dependent: do #32 first if both are scheduled, or fold #32's `--version` arm into #30's surgery if #30 lands first.

Touches: `src/dbxignore/cli.py` (`daemon_main` rewrite + `--version` decorator on `main`); maybe a small CLI smoke test under `tests/` confirming `--version` and `dbxignored --help` produce the expected strings.

---

## Status

### Open

Seven items. Six are passive (no concrete trigger requires action); item #32 has one fired trigger (a beta tester typed `dbxignore --version` and got "no such option") but isn't blocking.

- **#14** — Flaky `test_run_refuses_when_another_pid_is_alive`. Single observation 2026-04-24 during PR #22 pre-flight (passed on rerun and in isolation). Awaits 2nd observation; per project flake-handling policy, fix only after recurrence.
- **#26** — `install._common.detect_invocation` has an unreachable `RuntimeError` branch (preexisting from `linux_systemd._detect_invocation`, faithfully extracted in PR #57). Doc-vs-code inconsistency, no production hit. Fix when next touching the install layer.
- **#27** — Intel Mac (x86_64) Mach-O binary build leg. v0.4 ships arm64-only; Intel users install via PyPI. Awaits demand signal.
- **#28** — Universal2 macOS binary as the single artifact. Quality-of-life cleanup; mutually exclusive with #27. Defer until item #27 actually triggers.
- **#29** — Codesigning + notarization for macOS binaries. Smooths Gatekeeper UX but requires $99/yr Apple Developer membership. Awaits concrete pain signal.
- **#30** — Windows-aware single binary via `AttachConsole(ATTACH_PARENT_PROCESS)`. Collapses `dbxignore.exe` + `dbxignored.exe` to one. Three-context UX tradeoff (terminal / Task Scheduler / double-click) is load-bearing today; ctypes path is the implementation route. Awaits binary-size or build-time pain signal.
- **#32** — CLI polish: missing `--version`, unreachable `--verbose` from `dbxignored`, and bogus `Usage: dbxignored daemon` in `dbxignored --help`. All three trace to the `daemon_main` argv-rewrite shim; the consolidated fix is replacing the shim with a standalone Click command and adding `@click.version_option` to both. Sequence-dependent on #30 — fix #32 first if both are scheduled. Awaits CLI-touching change to bundle with.

### Resolved (reverse chronological)

#### 2026-05-01

- **#31** in PR #71 — bundle `_cffi_backend` in macOS PyInstaller spec; smoke-test built binaries before upload on both Windows and macOS build legs.

#### 2026-04-26

- **#24 + #25** in PR #50 — `state._read_at` broadened except for shape-mismatched JSON; `daemon._classify` returns root to skip double-lookup.

#### 2026-04-25

- **#23** in PR #49 — doc-tightening arm: CLAUDE.md lock-free wording now acknowledges multi-step `_applicable` traversals may see slightly-stale views.
- **#20 + #21** in PR #45 — atomic state write + broaden read-side OSError catch in reconcile.
- **#22** in PR #46 — deletion of stale README legacy state-path claim (top-level "Upgrading from v0.2.x" section is authoritative).
- **#19** in PR #41 — backfilled inline RESOLVED markers for items 8-10.

#### 2026-04-24

- **#6** in PR #38 — extract detection layer to `rules_conflicts.py`.
- **#18** in PR #40 — widen flaky daemon smoke test poll timeout 3.0s → 5.0s.
- **#4** in PR #36 — column-align rule-conflict rows in `status` output.
- **#13** in PR #35 — bump CI actions off Node.js 20.
- **#3 + #5** in PR #34 — `_SequenceEntry.pattern` Protocol + remove `_ancestors_of` resolve.
- **#1, #2, #7** in PR #33 — small fixes from negation-polish.
- **#15 + #17** in PR #30 — CHANGELOG repo URL + header rename.
- **#16** in PR #32 — `markers.py` NotImplementedError v0.3 reference.

#### v0.3.0 (2026-04-23 to 2026-04-24)

- **#11 + #12** in PRs #22, #23 — rename project to dbxignore + first PyPI publish.

#### v0.2.1 (2026-04-22)

- **#8, #9, #10** in PR #18 — three commits in one PR. (Status was previously misattributed to "PRs #15/#18/#19", corrected as part of item 19's PR #41.)

### Provenance notes

How items entered this tracker:

- **Items 1-13** — original v0.2.1 negation-polish followups (this file's first scope).
- **Items 14-16** added 2026-04-24 from v0.3.0 post-ship observations.
- **Item 17** added 2026-04-24 from a CLAUDE.md currency audit.
- **Item 18** added 2026-04-24 from a CI flake (PR #30 initial run); promoted to actionable 2026-04-25 after 2nd observation in PR #38; resolved same day in PR #40.
- **Item 19** added 2026-04-25 from a top-down tracker readability audit; resolved same day in PR #41.
- **Items 20-23** added 2026-04-25 from a whole-codebase code-review pass (four 75-confidence advisories — below the ≥80 ship-bar but verified-real, filed for backlog).
- **Items 24-25** added 2026-04-25 from a second-look code-review pass post-v0.3.1 (defensive-coding gap missed by the first pass + sloppy duplication in watchdog dispatch).
- **Items 26-29** added 2026-04-27 from the v0.4 macOS port post-ship. #26 is a preexisting bug surfaced by extraction; #27-29 are deferred macOS-distribution polish (Intel binary, universal2, codesigning) — all noted in the v0.4 spec § "Post-ship backlog candidates" and filed here for visibility.
- **Item 30** added 2026-04-27 from a v0.4 alpha-test conversation about the rationale for shipping two Windows binaries. The author proposed collapsing them, then walked it back after surfacing the three-context UX tradeoff (terminal / Task Scheduler / double-click) that the duplication addresses. Filed for the eventual `AttachConsole`-based simplification path.
- **Item 31** added 2026-05-01 from a v0.4.0a1 beta-test failure on macOS arm64 (M2 MacBook Air, Tahoe 26.4) — first time the macOS binary was end-to-end-exercised on a tester's machine. Filed and resolved in the same PR because the regression net (smoke-test built binaries before upload) was the more important half of the fix.
- **Item 32** added 2026-05-01 from a CLI surface review during the v0.4.0a3 ship (post-PR #71). Three small UX gaps surfaced via a `dbxignored --help` curiosity from the maintainer + the v0.4 beta tester's earlier `dbxignore --version` attempt. All three traced back to the `daemon_main` argv-rewrite shim; consolidated as one item rather than three because the fix collapses to a single ~15-line CLI rewrite.

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

The PyPI name `dropboxignore` is taken by an unrelated 2019 project (last release 2019-08 — likely dormant but PyPI name-reuse policy is strict). PyPI takeover is slow and unreliable.

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
- **Adopt** if Gatekeeper-bypass friction becomes a frequently-reported pain point or if a friction-free install story becomes load-bearing for adoption.

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

**Status: RESOLVED 2026-05-02 (PR #92).** `daemon_main` rewritten as a standalone `@click.command` with its own `--verbose` and `--version`; `_run_daemon` helper extracted and shared with the `daemon` subcommand under `main`; `@click.version_option(package_name="dbxignore")` also added to the `main` group. All three symptoms gone: `dbxignore --version` and `dbxignored --version` print the hatch-vcs-derived version; `dbxignored --help` prints `Usage: dbxignored [OPTIONS]` with no leaked `daemon` token; `dbxignored --verbose` is reachable. Smoke tests in `tests/test_cli_entrypoints.py` pin all four behaviors. `install/_common.detect_invocation` unaffected as predicted (the function's name and signature are unchanged).

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

## 33. macOS File Provider mode unsupported — wrong xattr name written

**Severity: HIGH.** Affects all macOS users on modern Dropbox installs (File Provider has been the default since 2023; modal v0.4 macOS user is on it).

**Symptom.** On macOS, Dropbox now ships in two distinct sync modes:

- **Legacy mode** — Dropbox folder at `~/Dropbox`, ignored files marked via the `com.dropbox.ignored` extended attribute. dbxignore v0.4 supports this.
- **File Provider mode** — Dropbox folder at `~/Library/CloudStorage/Dropbox/`, ignored files marked via the `com.apple.fileprovider.ignore#P` extended attribute (per [Dropbox's docs](https://help.dropbox.com/sync/ignored-files)). dbxignore v0.4 does NOT support this.

The macOS xattr backend (`src/dbxignore/_backends/macos_xattr.py:29`) hardcodes `ATTR_NAME = "com.dropbox.ignored"`. On a File Provider install, every successful write of that attribute is silently ineffective — Dropbox's File Provider extension doesn't watch for it, and the file gets synced regardless of the marker. The user sees a clean install, no errors in logs, and the daemon happily reconciling rules — but the markers don't actually cause Dropbox to ignore anything. Pure silent-failure mode, exactly the kind v0.4's other defensive arms (`reconcile._reconcile_path`'s OSError fallback, `state._read_at`'s graceful-failure shape) were designed to avoid.

Surfaced 2026-05-01 via v0.4 beta-tester confusion: `dbxignore apply` reported `marked=0 cleared=0 errors=0 duration=0.00s` on a File Provider machine. Two compounding causes — the tester created `.dropboxignore` and `build/` in `~/Dropbox` (the legacy folder, which still exists as a leftover on File Provider installs but isn't the active sync location), AND even with the right folder the xattr name would have been wrong. The first compounding cause is a documentation gap addressed by the docs-only PR shipping alongside this filing; the second is the architectural issue this item tracks.

**What's NOT broken (narrows the architectural scope).** Root discovery works correctly on File Provider installs. `~/.dropbox/info.json` is still written by Dropbox on File Provider mode (presumably for backward compat with tooling that reads it), and the `personal.path` field correctly points at `~/Library/CloudStorage/Dropbox/`. The beta tester's info.json was verified to contain this exact value, so `roots.discover()` already returns the right root on File Provider installs without changes. The fix is bounded to the xattr backend layer, not the discovery layer.

**Fix candidates:**

- **(A) Defer File Provider to a future release; ship v0.4 with a documented limitation.** Update README to call out that File Provider mode is unsupported (already done in the docs PR shipping alongside this filing). Track full File Provider support as the v0.5 (or later) headline feature with its own spec, test matrix, and beta cycle. Lowest-risk path to an honest v0.4 release. Cost: most current macOS users get an "unsupported" message; v0.4's "first macOS release" lands as effectively legacy-mode-only.
- **(B) Absorb File Provider support into v0.4 before tagging final.** Now-narrower scope (xattr backend layer only): `_backends/macos_xattr.py` gains a `_detect_attr_name()` helper that probes for File Provider mode (presence of `~/Library/CloudStorage/Dropbox/` is the simplest signal; `fileproviderctl dump | grep -q com.getdropbox.dropbox.fileprovider` is more authoritative but requires subprocess overhead at module-load time). Caches the result on first call. The three exported functions (`is_ignored`, `set_ignored`, `clear_ignored`) gain a one-attr-lookup-per-call indirection. Tests grow a `macos_fileprovider_only` marker; existing macOS unit tests parameterize across both modes via fixture. README's per-mode verification instructions stay; the "not yet supported" callout becomes "supported on both modes since v0.4." Estimated scope: ~30-50 lines code, ~50 lines tests, ~10 lines README delta. 1-2 day implementation including beta-tester roundtrip.
- **(C) Ship v0.4 with the bug, file as critical post-ship.** Worst option — silently produces "everything looks fine" output on File Provider installs while doing nothing useful. Don't pick.

**Status: RESOLVED 2026-05-01 (PR #77).** Scope decision was (B) — absorbed File Provider support into v0.4 before final tag. Implementation followed the spec above: `_detected_attr_name()` helper in `_backends/macos_xattr.py` probes for `~/Library/CloudStorage/Dropbox/`, caches the result on first call, returns either `ATTR_LEGACY` (`com.dropbox.ignored`) or `ATTR_FILEPROVIDER` (`com.apple.fileprovider.ignore#P`). The three exported functions route through the helper. Tests: 7 new tests in `tests/test_macos_xattr_unit.py` covering detection (4) and File Provider mode end-to-end (3); existing tests updated to use `_detected_attr_name()` instead of the removed `ATTR_NAME` constant. README's macOS section + CLAUDE.md gotchas updated. The three open implementation questions below await the beta tester's v0.4.0a4 roundtrip — they're not blockers for ship, just things we want to learn.

**Validated 2026-05-02 (v0.4.0a5).** Beta tester's pass on macOS Tahoe 26.4 / Dropbox 250.4 confirmed the File Provider attribute write actually takes effect — Dropbox stops syncing the marked folder after `dbxignore apply`. All three open implementation questions below resolved in the affirmative: (1) `xattr.setxattr` against `com.apple.fileprovider.ignore#P` works without special entitlements; (2) stub/placeholder files behave the same as materialized files for our marker write (the framework handles the dispatch); (3) `#P` alone is sufficient — no `#N` paired variant needed. The (B) scope decision paid off: v0.4 ships with full File Provider support rather than a documented legacy-only limitation.

**Open implementation questions** (resolved 2026-05-02 — all answered "yes, works as designed"; left in the entry as historical record of what we needed to learn before promoting to v0.4.0):

- Does `xattr.setxattr(path, "com.apple.fileprovider.ignore#P", b"1", symlink=True)` actually take effect, or does the File Provider sandbox model require special entitlements / a different code path? Apple's File Provider framework is more rigid than the legacy xattr API. Needs an end-to-end test on a File Provider machine before (B) can ship — the beta tester's machine is the available validation surface.
- Are there edge cases around stub/placeholder files (File Provider downloads files on-demand by default; an unmaterialized "stub" may behave differently for xattr writes)? Ships with the same beta-test cycle.
- Does the `#P` suffix attribute need a paired `#N` (per-session, non-persistent) variant for any of the operations we do, or is `#P` alone sufficient? Apple's docs describe the convention but the exact semantics for ignore markers aren't stated explicitly.

**Recommendation:** (A) for v0.4 final; (B) for the next macOS feature release. Reasoning: the v0.4 spec was authored against legacy mode; testing was done against legacy mode; the daemon installer and rules engine were validated against legacy mode. v0.5 (or whatever the next milestone is named) is the right place for File Provider work — separate spec, separate test surface, separate mode-detection logic — rather than rushing it into v0.4's tail end. (B) is genuinely defensible if making v0.4 useful to the modal macOS user matters more than the cycle-time delay; it's a real cost-vs-value call rather than a technical one.

**Status section note:** the docs-only fix shipping alongside this filing (`chore/macos-fileprovider-docs` branch) updates the README to disambiguate the two modes and tells File Provider users to wait for File Provider support. That's an immediate-shipping piece independent of the (A)-vs-(B) decision.

Touches: `src/dbxignore/_backends/macos_xattr.py` (mode detection + per-mode attribute name); `tests/` (new `macos_fileprovider_only` marker + parameterization); `README.md` (per-mode verification instructions, when (B) ships); `CLAUDE.md` (gotchas section gains a File Provider entry).

## 34. `test_daemon_reacts_to_dropboxignore_and_directory_creation` flaked again post-resolution

The same `tests/test_daemon_smoke.py` test that prompted item #18 (originally filed 2026-04-24, escalated to actionable on second observation, resolved 2026-04-25 in PR #40 by widening the `_poll_until` timeout from 3.0s to 5.0s) failed again 2026-05-01 in PR #74's post-rebase Windows leg. The failure was the same shape: `_poll_until(lambda: markers.is_ignored(...), timeout_s=5.0)` returned False before the daemon reacted. Reran the failed leg via `gh run rerun --failed`, second run passed in 27s (vs. 39s for the failed run). Pure timing flake — the rebase on top of the freshly-merged PR #73 didn't change content, only the rerun's wall-clock.

Per the project's flake-handling convention (item #14's note: "fix only after recurrence"), the post-resolution recurrence triggers reinvestigation. The 2026-04-24 fix bought roughly a week of green CI on this test before the second post-resolution observation. The widened timeout treated the symptom (not enough time for the watchdog event → reconcile → marker chain to complete), not the underlying cause (whatever's making the chain occasionally take >5s on the Windows runner — maybe Defender/AV scans, maybe NTFS event coalescing, maybe a watchdog-event ordering quirk between the rule-file-change and the directory-creation events the test asserts about).

**Fix candidates:**

- **Widen further (5.0s → 8.0s or 10.0s).** Cheapest. Treats the symptom, kicks the can. Likely buys another month or two of green CI before the next recurrence — or doesn't, depending on whether the underlying cause is bounded by some specific runner-side latency or is open-ended (in which case no timeout will ever be enough). Default option if a deeper diagnosis isn't tractable.
- **Diagnose the actual slowness.** Instrument the test to log timestamps at each stage of the watchdog → debouncer → reconcile → marker chain on the Windows runner. The 5.0s budget is broken into: (1) watchdog event delivery latency, (2) `Debouncer` window (per-`EventKind` defaults in `daemon._TIMEOUT_ENV_VARS`), (3) `_dispatch` → `reconcile_subtree` walk, (4) per-file marker write. Whichever stage occasionally exceeds 1-2s is the culprit. May reveal a fixable bug (e.g., debouncer stalling on a specific event sequence the test triggers) or may reveal "Windows runner is just slow on the day it's slow," in which case fall back to widening.
- **Replace the daemon-driven smoke test with a unit-level test.** The test is exercising the watchdog → debouncer → reconcile chain end-to-end. That coverage is valuable but inherently timing-sensitive. A unit-level test that fires the same `EventKind` events synthetically, bypasses the watchdog event loop, and asserts the reconcile pass produces the right markers would be deterministic. Loses some coverage (we wouldn't catch a watchdog regression), but the existing `tests/test_daemon_smoke.py` could be reduced to just "daemon starts + responds to one minimal event" and the multi-event scenarios moved to unit-level. Larger scope.

**Recommendation:** Diagnose first, widen-as-fallback. If the diagnosis surfaces a real bug (e.g., debouncer issue), fix it. If the diagnosis confirms "Windows runner is occasionally slow with no actionable cause," widen the timeout to 8.0s and accept this is a weather-not-climate test.

**Urgency:** low. Single CI flake doesn't block any release. But if it recurs a third time, the fix needs to happen in the same cycle as the recurrence — accumulating "rerun the failed leg" actions is a hidden ops cost that compounds across maintainers. Track the next recurrence and use it as the trigger.

**Fourth observation 2026-05-07** in PR #124's Windows-only integration leg. Same shape: `_poll_until(lambda: markers.is_ignored(tmp_path / "build" / "keep"), timeout_s=5.0)` returned False before the daemon reacted. PR #124's commits (`fix(rules):` cache-key drop + symlink-loop catch, `fix(cli):` apply path-exists, `fix(daemon):` env-var validation only — not dispatch, `fix(roots):` DBXIGNORE_ROOT type guards) don't touch the daemon dispatch / Windows ADS / conflict detection paths, so the recurrence is purely Windows-runner load. macOS and Ubuntu legs both passed. Reran via `gh run rerun --failed`. The pattern (~weekly recurrence under load on the same negation-semantics assertion) reinforces the 2026-05-04 diagnosis hypothesis: test-order interaction with an earlier test pushes the full-suite Windows leg into a state where this test runs slower than its 5.0s budget.

Touches: `tests/test_daemon_smoke.py` (the failing test); maybe `src/dbxignore/daemon.py` if the diagnosis surfaces a real bug.

## 35. macOS launchd plist + Windows Task Scheduler XML invoke wrong binary on frozen installs

**Severity: HIGH.** On macOS PyInstaller installs the daemon never starts; on Windows PyInstaller installs the same. Latent on Linux installs (which don't ship a frozen binary).

**Symptom.** `dbxignore install` writes a launchd plist (macOS) / Task Scheduler XML (Windows) whose invocation target is the long-form `dbxignore` binary with no subcommand. Service manager execs the binary on every spawn, Click sees the group invoked without subcommand, prints help, exits with status 2, KeepAlive retries on the same loop. The daemon never reaches `daemon_mod.run()`.

The v0.4 macOS beta-tester's `launchctl print gui/501/com.kiloscheffer.dbxignore` 2026-05-01 showed exactly this:

```
program = /usr/local/bin/dbxignore
arguments = {
    /usr/local/bin/dbxignore
}
last exit code = 2
runs = 4
```

`/usr/local/bin/dbxignore` is the long-form CLI; the daemon shim is `/usr/local/bin/dbxignored` (a sibling binary that ships in the same Mach-O bundle release). The plist invoked the wrong binary.

**Root cause.** `install/_common.py:detect_invocation`'s frozen branch returned `(Path(sys.executable), "")`. When the user runs `dbxignore install`, `sys.executable` is the `dbxignore` binary they invoked, not the `dbxignored` daemon shim. Empty args mean no subcommand to dispatch. Same shape in `install/windows_task.py:detect_invocation`'s frozen branch — Windows PyInstaller installs hit the same bug structurally, even though the beta-tester's testing was on macOS.

Linux escapes the bug because Linux has no PyInstaller spec — Linux installs always go through the non-frozen branch, which does `shutil.which("dbxignored")` and finds the entry-point shim. That's the architecture-as-designed: the daemon-shim binary IS the service-manager target. The frozen branches were the asymmetric outlier.

**Fix.** Replace the frozen branch with a three-step resolution:

1. If `sys.executable.name` is already `dbxignored` (Linux/macOS) or `dbxignored.exe` (Windows) — user invoked `dbxignored install` directly — return it as-is with empty args.
2. Else look for a `dbxignored` sibling next to `sys.executable` (common case — user invoked `dbxignore install` from the long-form binary). Both binaries ship as a paired set from the same PyInstaller Analysis, so the sibling is reliably present. Return the sibling with empty args.
3. Else fall through to `(sys.executable, "daemon")` so the service manager invokes the long-form binary with the `daemon` subcommand. Defensive — the PyInstaller specs always emit both binaries.

Same shape in both `install/_common.py` and `install/windows_task.py`'s frozen branches.

**Status: RESOLVED 2026-05-01 (PR #76).** Fix landed alongside three new tests covering each resolution arm (sibling-already-dbxignored, sibling-found-from-dbxignore, sibling-missing-fallback) on both macOS/Linux and Windows code paths. Total scope: ~50 lines code + ~50 lines tests across `install/_common.py`, `install/windows_task.py`, `tests/test_install_common.py`, `tests/test_install.py`. Beta tester retests via v0.4.0a4 — `launchctl print` should now show `program = /usr/local/bin/dbxignored` (the daemon shim, not the long-form CLI), and the daemon should actually start.

Touches: `src/dbxignore/install/_common.py`; `src/dbxignore/install/windows_task.py`; `tests/test_install_common.py`; `tests/test_install.py`.

## 36. macOS sync-mode detection conflated system-level and user-level signals

**Severity: HIGH.** Affects all macOS users on v0.4.0a4 who have Dropbox.app installed but haven't migrated this account to File Provider — they'd silently get `com.apple.fileprovider.ignore#P` written when Dropbox is actually watching `com.dropbox.ignored` (or vice versa for some edge cases). Same shape of silent-failure bug as item #33; just for a different population.

**Symptom.** v0.4.0a4's `_detected_attr_name()` queried `pluginkit -m -A -i com.getdropbox.dropbox.fileprovider` as the primary signal: if the extension was registered AND not user-disabled, return `ATTR_FILEPROVIDER`. This was wrong because PluginKit registration is a *system-level* fact (does macOS know about `DropboxFileProvider.appex`?), not a *user-level* fact (which mode is *this account* in?). A user with Dropbox.app installed who declined the File Provider migration (or rolled back to legacy via Dropbox's UI) would have an active extension registration AND be syncing in legacy mode from `~/Dropbox/`. Pre-fix detection wrongly returned File Provider for that user; markers got written under the wrong attribute name; Dropbox didn't honor them.

**Root cause framing.** The correct user-level signal lives in `~/.dropbox/info.json`'s `path` field — Dropbox writes the actual configured sync location there, and the location tells us which mode this specific account uses. Apple's File Provider folders normally live under `~/Library/CloudStorage/<vendor>/` (per [Dropbox docs](https://help.dropbox.com/installs/fix-domain-conflict-on-mac)); legacy folders live wherever the user configured them. Path-prefix on `Library/CloudStorage` is the determinant for mode-per-account.

**Fix.** Detection is now **path-primary, pluginkit-disambiguating**:

1. `_read_dropbox_paths_from_info()` reads `~/.dropbox/info.json` (multi-account aware — info.json can list `personal` and `business` keys).
2. `_pluginkit_extension_state()` returns one of `"allowed"` / `"disabled"` / `"not_registered"` / `"unknown"`.
3. Combine:
   - Extension `"disabled"` → legacy regardless of path (user explicitly opted out).
   - Any account path under `~/Library/CloudStorage/` → File Provider (the common case, and the beta tester's case).
   - Path elsewhere + extension `"allowed"` → File Provider (external-drive eligibility-gated case Dropbox docs mention).
   - Otherwise → legacy (defensive default — covers no Dropbox installed, pure-legacy without extension, pluginkit unknown without CloudStorage path).

**Status: RESOLVED 2026-05-01 (PR #79).** Path-primary detection landed in `_backends/macos_xattr.py` with two new helpers (`_read_dropbox_paths_from_info`, `_pluginkit_extension_state`). Test surface grew from 7 to 11 tests in `tests/test_macos_xattr_unit.py` covering: the default File Provider case (path under CloudStorage + extension allowed), the bug case (extension installed + legacy path → legacy), disabled-extension override, external-drive File Provider, multi-account info.json, and four defensive fallback paths. CLAUDE.md gotcha rewritten to reflect the corrected logic. v0.4.0a5 ships this for the beta tester's roundtrip.

Touches: `src/dbxignore/_backends/macos_xattr.py`; `tests/test_macos_xattr_unit.py`; `CLAUDE.md`.

## 37. macOS sync-mode detection result should be observable for user-report debugging

**Status: RESOLVED 2026-05-04 (PR #97).**

The path-primary detection landed in PR #79 (item #36) writes its result to `_attr_name_cache` and returns it from `_detected_attr_name()`. Internal logging is at `DEBUG` level (`logger.debug("Detected legacy mode: ...")` and similar) — visible only with `dbxignore -v ...`, not surfaced anywhere else.

That's enough for self-diagnosis when a user knows to add `-v`, but it's a poor experience when a user reports "dbxignore doesn't seem to be working on my Mac" and we want to ask "what mode did detection conclude?" without round-tripping through "run with -v and paste the daemon log." Two small enrichments would close that gap:

**(A) Promote the detection result to INFO at startup.** Currently the daemon's `daemon.run()` reads `DBXIGNORE_LOG_LEVEL` and configures handlers; the per-call `logger.debug` lines from `_detected_attr_name()` only show if level is DEBUG. Adding a single `logger.info` at daemon startup that calls `_detected_attr_name()` and logs the chosen mode + a short reason would surface the answer in the default log without requiring `-v`. Reason field would distinguish: "info.json path under CloudStorage", "extension explicitly disabled", "external-drive Volumes path + extension active", "no info.json + pluginkit unknown → defensive default", etc.

**(B) Add a "macOS sync mode" line to `dbxignore status` output on darwin.** `cli.status()` already prints daemon health, last-sweep stats, and rule conflicts. Adding `macOS sync mode: file_provider (com.apple.fileprovider.ignore#P)` (or `legacy (com.dropbox.ignored)`) below those lines makes it self-serve diagnostic — users hit `dbxignore status` first when something seems off, and seeing the mode there points at the right next step. macOS-only since Linux/Windows backends don't have mode ambiguity.

**Fix candidates:**

- **Both (A) and (B).** Small, complementary. (A) adds startup log; (B) adds status output. Combined scope: ~30 lines code in `daemon.py` + `cli.py`, plus a `_detection_reason()` helper in `_backends/macos_xattr.py` that returns a human-readable explanation alongside the cached attr name.
- **Just (A).** Minimum viable; covers the daemon-side diagnostic. Users still need to know to read the log file.
- **Just (B).** Maximum user-visible; requires running `dbxignore status` to learn the mode but that's an expected diagnostic step.

**Recommendation:** Both. Each is 10-15 lines and they're useful for different contexts (daemon ops vs. user CLI diagnostic). Bundle as one PR since they share the new `_detection_reason()` shape.

**Urgency:** low. v0.4.0a5 works correctly without this; the gap is observability, not correctness. Promote when the macos backend is next touched, OR if a user reports "is dbxignore using the right mode?" and we wish we could answer without `-v`.

**Source:** the proposed-code snippet shared during the v0.4.0a5 detection-design discussion returned a structured `{mode, confidence, reason, ...}` dict. We collapsed to a binary attr-name return for the call site's needs but kept the proposed code's diagnostic richness as a v0.5 follow-up. This item is the place we keep that scope.

Touches: `src/dbxignore/_backends/macos_xattr.py` (new `_detection_reason()` helper); `src/dbxignore/daemon.py` (INFO log at startup); `src/dbxignore/cli.py` (status output line on darwin); `tests/` (small additions covering both surfaces).

## 38. info.json parsing duplicated between `roots.py` and `_backends/macos_xattr.py`

`roots.discover()` and `_backends/macos_xattr._read_dropbox_paths_from_info()` both read `~/.dropbox/info.json`, parse it as JSON, and extract per-account `path` fields. The two implementations differ in superficial ways but encode the same core logic:

- `roots.discover()` (lines 63-110): handles `DBXIGNORE_ROOT` env-var override first, then locates info.json via `_info_json_paths()` (platform-aware), reads + parses, iterates over hardcoded `_ACCOUNT_TYPES = ("personal", "business")`, returns `list[Path]` with WARNING logs on each failure mode.
- `_backends/macos_xattr._read_dropbox_paths_from_info()` (~25 lines): macOS-only path (`~/.dropbox/info.json` directly), reads + parses, iterates over `data.values()` (looser — accepts any account-type key), returns `list[str]` with silent failure (returns `[]` on any error).

The semantic differences are intentional and load-bearing:

- `roots.discover()` honors `DBXIGNORE_ROOT` because that's the daemon's escape hatch for non-stock Dropbox installs. macos_xattr deliberately bypasses it because the override tells us the daemon's *operational* root, not the user's *configured sync mode* — and mode detection needs the configured path, not the override.
- `roots.discover()` only knows `personal` + `business` keys; macos_xattr accepts any. If Dropbox ever adds a third account type (rare but possible), `roots.discover()` would silently miss it; macos_xattr would catch it.

Surfaced 2026-05-02 in a `/simplify` pass on PR #79.

**Fix candidates:**

- **Extract a shared `_parse_info_json_paths(info_path: Path) -> list[str]` helper.** Lives in `roots.py` (or a new `_dropbox_info.py` if we want clean separation). Both `roots.discover()` and `_read_dropbox_paths_from_info()` call it. Each caller wraps it with their own error-logging strategy and post-processing. Net: ~30 lines of duplication eliminated, the looser `data.values()` iteration becomes the shared canonical implementation (fixing `roots.discover()`'s missing-future-account-types blind spot for free).
- **Status quo.** The duplication is small (~25 lines), the semantic differences are real, and the modules' independence is currently a virtue (changes to `roots.py` can't accidentally break mode detection). Acceptable to leave as-is until one or the other needs non-trivial change.

**Recommendation:** extract when next touching either module's info.json handling. No urgency to do it standalone; the cost of the duplication is one-time and bounded.

**Urgency:** low. Pure code-quality refactor; no behavior change for users.

Touches: `src/dbxignore/roots.py`; `src/dbxignore/_backends/macos_xattr.py`; possibly new `src/dbxignore/_dropbox_info.py`; tests for both call sites.

## 39. `_pluginkit_extension_state()` returns stringly-typed state

`_backends/macos_xattr._pluginkit_extension_state()` returns one of four raw strings: `"allowed"` / `"disabled"` / `"not_registered"` / `"unknown"`. The single caller (`_detected_attr_name()`) does `if extension_state == "disabled":` and `if extension_state == "allowed":` comparisons against those literals.

The four-string return type is an enum in everything but type. A typo in either the function (return value) or the caller (comparison string) would silently produce wrong behavior at runtime — the type checker has no way to catch `extension_state == "disablled"` because it's just a string comparison.

Surfaced 2026-05-02 in a `/simplify` pass on PR #79.

**Fix candidates:**

- **`enum.Enum`.** Concrete: `class PluginKitState(Enum): ALLOWED = "allowed"; DISABLED = "disabled"; NOT_REGISTERED = "not_registered"; UNKNOWN = "unknown"`. Caller becomes `if extension_state is PluginKitState.DISABLED:`. Catches typos at type-check time. ~10 lines added, all comparisons updated.
- **`typing.Literal[...]`.** Lighter-weight: `def _pluginkit_extension_state() -> Literal["allowed", "disabled", "not_registered", "unknown"]:`. The return-type annotation lets mypy/ty/pyright catch caller-side typos but doesn't help with internal typos in the function body. ~1 line annotation change.
- **Status quo.** Strings are confined to one module with one callsite. The comparison literals are co-located with the function returning them (in the same file). Risk of typo is real but the blast radius is small (single test would catch most cases).

**Recommendation:** `Literal[...]` annotation if anything — the cost is one line and we get type-checker support for caller-side typos. Don't bother with a full enum; the ergonomics gain is small relative to the boilerplate.

**Urgency:** low. Pure code-quality. Single callsite limits blast radius.

Touches: `src/dbxignore/_backends/macos_xattr.py` (one line annotation, plus possibly an enum class if we go that route).

## 40. Dual `paths` for-loops in `_detected_attr_name()` could share a helper

`_detected_attr_name()` has two consecutive for-loops over the `paths` list, each with similar shape: try `os.path.realpath(p)`, catch `OSError`, check a predicate against the result, return `ATTR_FILEPROVIDER` if matched, log the match. The loops differ only in:

- The predicate (`is_relative_to(cloud_storage)` vs. `len(real_parts) >= 3 and real_parts[1] == "Volumes"`).
- The log message ("Detected File Provider mode: %s under ~/Library/CloudStorage/" vs. "Detected File Provider mode (external drive): %s").

A code-review agent during PR #79's `/simplify` pass proposed extracting `_first_match(paths, predicate, log_msg) -> bool` to dedupe. A second agent argued the dual structure is correct because the two loops encode different *priority levels*: CloudStorage match wins unconditionally (regardless of pluginkit state); `/Volumes` match only fires if `extension_state == "allowed"`. Merging into a single pass would either change priority semantics (a CloudStorage hit on account[1] would lose to a Volumes hit on account[0]) or require carrying a "found Volumes match, hold it" variable — both worse.

The disagreement isn't about whether the code is correct (it is); it's about whether the dual-loop structure is the best way to express the priority semantics, or whether a shared helper called twice (with different predicates) would be clearer.

Surfaced 2026-05-02 in the same `/simplify` pass.

**Fix candidates:**

- **Extract `_first_match(paths, predicate, log_msg) -> bool`** and call it twice in priority order. Concrete shape:
  ```python
  def _first_match(paths, predicate, log_msg):
      for p in paths:
          try:
              real = Path(os.path.realpath(p))
          except OSError:
              continue
          if predicate(real):
              logger.debug(log_msg, p)
              return True
      return False
  ```
  Then `if _first_match(paths, is_under_cloudstorage, "..."):` returns FP, etc. Preserves priority via the order of the two `if` blocks. Saves ~10 lines of repetition.
- **Status quo.** The dual structure is verbose but correctly encodes the priority. A future reader can see "CloudStorage check first, Volumes check second" at a glance; with the helper they'd have to read the helper definition + both call sites to understand priority. Argument against extraction is "verbose-but-clear beats terse-but-indirect."

**Recommendation:** keep as-is. The verbose structure correctly documents the priority. A `_first_match` helper would be cleaner if a third predicate ever appeared, but with two it's "rule of three" territory — not yet.

**Urgency:** very low. Code-quality observation only; current shape is defensible.

Touches: `src/dbxignore/_backends/macos_xattr.py` (one helper added, two call sites updated).

## 41. `_reconcile_path` write-side `ENOTSUP`/`EOPNOTSUPP` arm returns `None`, diverging from `PermissionError`'s `currently_ignored`

`src/dbxignore/reconcile.py`'s `_reconcile_path` write path has two failure arms that CLAUDE.md asserts should be treated identically:

```python
except PermissionError as exc:
    logger.warning(...)
    report.errors.append((path, f"write: {exc}"))
    # Write failed: the ADS state is still whatever we read.
    return currently_ignored          # ← preserves last-known marker state
except OSError as exc:
    if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
        logger.warning(...)
        report.errors.append((path, f"unsupported: {exc}"))
        return None                    # ← discards last-known state
    raise
```

CLAUDE.md says: *"catches `OSError(errno.ENOTSUP|EOPNOTSUPP)` from xattr-based backends and treats it the same way as `PermissionError` — log WARNING, append to `Report.errors`, continue the sweep."* The log line and `Report.errors` shape match, but the return value drives subtree pruning in `reconcile_subtree`:

```python
dirnames[:] = [name for name in dirnames if not _reconcile_path(...)]
```

A truthy return prunes the directory from the walk. `PermissionError` returns `currently_ignored` (truthy if the directory was already marked) → directory is pruned, walk does not descend. `ENOTSUP` returns `None` (falsy) → directory is *not* pruned, walk descends into a subtree the filesystem can't mark. Per-file `set_ignored` then re-fails with ENOTSUP for every child, spamming WARNING logs across the whole subtree.

Companion to item 21 (RESOLVED PR #45) which set the precedent that ENOTSUP and PermissionError are the same failure class on the read side.

**Fix:** change the ENOTSUP arm's `return None` to `return currently_ignored` so pruning matches `PermissionError`. ~1 line, plus a regression test that simulates ENOTSUP via `FakeMarkers` and asserts the subtree is pruned.

**Urgency:** low. Hits only filesystems that don't support xattrs (FAT32 / tmpfs / older NFS mounts inside a Dropbox folder). Manifests as log-spam rather than a correctness regression in marker state. Worth fixing because the fix is one line and CLAUDE.md is explicit about the contract.

Touches: `src/dbxignore/reconcile.py:113`; new test in `tests/test_reconcile_enotsup.py` or `tests/test_reconcile_edges.py`.

**Status: RESOLVED 2026-05-02 (PR #91).** One-line return-value fix as filed. Regression test (`test_enotsup_on_directory_clear_prunes_subtree`) added to `tests/test_reconcile_enotsup.py`: pre-marks both a directory and a file inside it, monkeypatches `clear_ignored` to record-and-raise, asserts only the directory's clear is attempted (the child's clear is not reached because the still-marked directory pruned the walk). Verified by stashing the fix and re-running — the new test fails with two clear attempts on un-fixed code, passes with one on fixed code. The set-arm case (currently_ignored=False, set fails) is behaviorally unchanged because both `None` and `False` are falsy → walk still descends, which is correct (the dir wasn't marked anyway); the fix's value is on the clear-arm.

## 42. `_timeouts_from_env()` crashes the daemon at startup if a debounce env var is non-integer

`src/dbxignore/daemon.py:107-111`:

```python
def _timeouts_from_env() -> dict[EventKind, int]:
    return {
        kind: int(os.environ.get(_TIMEOUT_ENV_VARS[kind], str(default)))
        for kind, default in DEFAULT_TIMEOUTS_MS.items()
    }
```

`int()` on a non-numeric string (e.g. `DBXIGNORE_DEBOUNCE_RULES_MS=fast`) raises `ValueError` with no try/except and no fallback. The crash propagates through `daemon.run()` before the watchdog observer or debouncer is started. Under systemd, `Restart=on-failure RestartSec=60s` would loop the failure indefinitely; under Task Scheduler, the task dies on each launch with no entry in `daemon.log` (logging is set up *after* `_timeouts_from_env` runs).

The same module already has a precedent for graceful env-var handling: `DBXIGNORE_LOG_LEVEL` validates against `_VALID_LEVELS`, logs a warning, and falls back to INFO. The debounce-timeout handler is conspicuously missing the equivalent.

**Fix candidates:**

- **Per-key try/except.** Wrap each `int(...)` call, log a warning naming the env var and the offending value, fall back to `default`. Mirrors the log-level handler's shape. ~10 lines.
- **Single helper `_int_from_env(name, default)`** that wraps parse + warning + fallback. Cleaner if more numeric env vars get added.

**Urgency:** low. Triggered only by user error (typo in env var name's value). Worth filing because (a) the failure mode is loud-and-recurring rather than soft-and-recoverable, (b) the in-module precedent makes the inconsistency stand out, and (c) there are zero tests for `_timeouts_from_env()` despite extensive test coverage of `_configured_logging`.

Touches: `src/dbxignore/daemon.py:107-111`; new test in `tests/test_daemon_logging.py` or a new `test_daemon_timeouts.py`.

## 43. `reconcile_subtree` re-resolves `root` and `subdir` on every call; daemon callers don't pre-resolve

`src/dbxignore/reconcile.py:30-31`:

```python
def reconcile_subtree(root: Path, subdir: Path, cache: RuleCache) -> Report:
    start = time.perf_counter()
    report = Report()
    root = root.resolve()
    subdir = subdir.resolve()
```

CLAUDE.md states: *"Resolve at the CLI/daemon boundary, never inside the cache or markers layer — `Path.resolve()` on Windows is a per-call syscall that dominated sweep wall-clock before."* The CLI path honors this (`cli.apply` pre-resolves at the top of the function). The daemon path does not:

- `daemon._dispatch(event, cache, roots)` passes `event.src_path`-derived paths and `roots` through to `reconcile_subtree` without pre-resolving.
- `daemon._sweep_once` passes `roots` (from `roots.discover()`, also un-resolved).

On Windows, `Path.resolve()` calls `GetFinalPathNameByHandleW` even for already-absolute paths. The cost is per-event for `_dispatch` (every accepted watchdog event, post-debouncer) and per-sweep for `_sweep_once` (hourly). The hourly cost is negligible; the per-event cost compounds during burst activity (e.g. `git checkout` of a large branch).

Companion to item 5 (RESOLVED PR #34) which removed a `_ancestors_of` resolve at a different layer.

**Fix candidates:**

- **Pre-resolve at the daemon boundary.** Resolve `roots` once in `daemon.run()` before passing to `_sweep_once`/`_dispatch`; resolve `event.src_path` once in `_classify` (which already inspects events). Drop the redundant `.resolve()` calls inside `reconcile_subtree`. Most aligned with CLAUDE.md's stated boundary contract.
- **Memoize inside reconcile_subtree.** A `@functools.lru_cache`-style guard. Adds state to a stateless function; rejected.
- **Status quo + comment.** Add a NOTE explaining the redundant resolves are kept defensively in case a future caller forgets. Documents the inconsistency without fixing it.

**Urgency:** low. Per-event cost is small in absolute terms; the precedent (item 5) was about correctness *and* perf, while this is purely perf. Bundle with the next daemon-touching change.

Touches: `src/dbxignore/reconcile.py:30-31`, `src/dbxignore/daemon.py` (`run`, `_dispatch`, `_sweep_once`, `_classify`), possibly tests that pass un-resolved paths.

**Status: RESOLVED 2026-05-02 (PR #91).** Took the **pre-resolve at the daemon boundary** arm (the prescribed fix). Changes: (1) `reconcile_subtree`'s two `.resolve()` calls dropped, replaced by an expanded docstring stating the pre-resolve contract; (2) `cli._discover_roots` now wraps `roots.discover()` with `[r.resolve() for r in ...]` — single hoist serves all six CLI commands; (3) `daemon.run()` resolves `configured_roots` once at startup; (4) `daemon._classify` returns `(kind, key, root, resolved_src)` — adds the resolved src as a 4th tuple element so downstream consumers don't re-resolve, mirroring item #25's tuple expansion (PR #50); (5) `daemon._dispatch` destructures the 4-tuple and uses the resolved src directly, plus inline-resolves `event.dest_path` when present. Test churn: `tests/test_daemon_dispatch.py` updated — the two destructuring tests unpack 4 elements and assert the new return shape; the seven `_dispatch` tests pre-resolve `tmp_path` at the top so path-equality assertions compare resolved-vs-resolved (required for macOS CI's `/tmp -> /private/tmp` divergence). The 2x classify cost (handler + dispatch each call `_classify`) is unchanged from before — same shape as item #25 accepted.

## 44. `build_task_xml` interpolates `getpass.getuser()` and `exe_path` into XML without escaping

`src/dbxignore/install/windows_task.py:42-92`'s `build_task_xml` builds a Task Scheduler XML document via f-string interpolation:

```python
user = getpass.getuser()
return f"""<?xml ...>
  ...
      <UserId>{user}</UserId>
  ...
      <Command>{exe_path}</Command>
"""
```

Neither `user` nor `exe_path` is escaped via `xml.sax.saxutils.escape()`. If either contains `&`, `<`, or `>`, the resulting XML is malformed and `schtasks /Create /XML` rejects it. Verified: `xml.etree.ElementTree.ParseError: not well-formed (invalid token)` on an unescaped `&`.

Realistic worst case: a directory like `C:\Users\Tom & Jerry\` in the install path — uncommon but legal. Windows usernames containing `&` are technically allowed but rare. AD environments with `O'Brien`-style apostrophes are XML-safe but the bug-shape is the same: special chars eaten by the parser.

**Fix:** import `xml.sax.saxutils.escape` and wrap both interpolations:
```python
from xml.sax.saxutils import escape
user = escape(getpass.getuser())
exe_str = escape(str(exe_path))
```
Plus a regression test in `tests/test_install.py` that fuzzes `user` and `exe_path` with `&<>` and asserts `xml.etree.ElementTree.fromstring` succeeds.

**Urgency:** low. Hits only users with special characters in their username or install path; manifests as a confusing `schtasks` error rather than silent corruption. Worth fixing because the failure mode is hard to diagnose ("daemon won't install") and the fix is small.

Touches: `src/dbxignore/install/windows_task.py:40-92`; new regression in `tests/test_install.py`.

## 45. `_applicable` docstring says "Yield" but the function returns a list

`src/dbxignore/rules.py:254-264`:

```python
def _applicable(
    self, root: Path, path: Path
) -> list[tuple[Path, _LoadedRules]]:
    """Yield (ancestor, loaded_rules) for each applicable .dropboxignore
    in shallow-to-deep order."""
    result: list[tuple[Path, _LoadedRules]] = []
    for ancestor in self._ancestors(root, path):
        ...
    return result
```

The return-type annotation is `list[tuple[Path, _LoadedRules]]` (correct), but the docstring opens with "Yield" — generator language. No `yield` keyword in the function body. A reader scanning the docstring in an IDE tooltip sees the wrong contract.

**Fix:** change "Yield" to "Return" in the docstring. One-word fix.

**Urgency:** very low (cosmetic). Filed for tracker hygiene; bundle with a future `rules.py` touch.

Touches: `src/dbxignore/rules.py:257`.

**Status: RESOLVED 2026-05-02 (PR #90).** "Yield" → "Return" in the docstring. One-word fix as filed.

## 46. `windows_ads.set_ignored` docstring names `reconcile_subtree` as the catcher; should be `reconcile._reconcile_path`

`src/dbxignore/_backends/windows_ads.py:44-50`:

```python
def set_ignored(path: Path) -> None:
    """Mark ``path`` as ignored by Dropbox.

    Raises ``FileNotFoundError`` if ``path`` vanished before the write;
    raises ``PermissionError`` if the stream cannot be written. Callers
    (notably ``reconcile_subtree``) catch and log both per the design's
    failure-mode contract.
    """
```

The actual catcher is `reconcile._reconcile_path` (`src/dbxignore/reconcile.py:99-114`), not `reconcile_subtree`. The Linux backend (`linux_xattr.py:64-68`) and the macOS backend both correctly cite `reconcile._reconcile_path`. CLAUDE.md also names `reconcile._reconcile_path` as the canonical exception-handling layer. The Windows backend is the lone outlier across three otherwise-symmetric backend modules.

Likely an artifact of pre-extraction wording when the catch-all sat in `reconcile_subtree`; the Linux/macOS backends were written after the extraction and cite the post-refactor location.

**Fix:** change `reconcile_subtree` to `reconcile._reconcile_path` in the docstring. One-word fix.

**Urgency:** very low (cosmetic). Filed for cross-backend doc-symmetry; bundle with a future Windows-backend touch.

Touches: `src/dbxignore/_backends/windows_ads.py:49`.

**Status: RESOLVED 2026-05-02 (PR #90).** `reconcile_subtree` → `reconcile._reconcile_path` in the docstring. Brings the Windows backend into citation-symmetry with `linux_xattr.py` and `macos_xattr.py`.

## 47. `reconcile.py` module docstring describes "ADS markers" — Windows-only term in cross-platform module

`src/dbxignore/reconcile.py:1`:

```python
"""Reconcile the filesystem's ADS markers with the current rule set."""
```

ADS = NTFS Alternate Data Streams (Windows-only). The module is fully cross-platform — on Linux/macOS it dispatches to xattr-based backends via `markers.is_ignored`/`set_ignored`/`clear_ignored`. CLAUDE.md describes the marker-set as *"NTFS alternate data streams on Windows; `user.com.dropbox.ignored` xattrs on Linux; `com.dropbox.ignored` xattrs on macOS"*.

The same Windows-flavored phrasing recurs in `_reconcile_path`'s own docstring (line 56: "Reconcile one path's ADS marker") and in inline comments (line 100: "Path vanished before ADS write"). All three predate the v0.2 Linux port and the v0.4 macOS port — missed by both rename sweeps.

**Fix:** s/ADS marker/ignore marker/ in the three locations, or use the more neutral "ignore-marker" terminology already used throughout CLAUDE.md.

**Urgency:** very low (cosmetic, but newly visible to readers approaching the module from the Linux/macOS side). Bundle with items 46 and 48 as a doc-currency mini-sweep.

Touches: `src/dbxignore/reconcile.py:1`, `:56`, `:100`.

**Status: RESOLVED 2026-05-02 (PR #90).** s/ADS/marker/ across **five** locations, not the three the item enumerated — the Windows-flavored phrasing also recurred in the `PermissionError` write-side log (line 103: "Permission denied writing ADS on") and in the inline comment two lines below (line 105: "the ADS state is still whatever we read"). The item author's grep matched only "ADS marker" / "ADS write" exact phrases; the broader sweep covered all five. Verified zero "ADS" mentions remaining via `grep -n ADS reconcile.py`. Repeats the lesson from items 21 and 22: a tracker item's prescribed scope can be narrower than the underlying drift — always verify against current code before executing.

## 48. `state.py` module docstring lists Windows + Linux paths but omits macOS

`src/dbxignore/state.py:1-5`:

```python
"""Persist daemon state under the platform's per-user state directory.

Windows: ``%LOCALAPPDATA%\\dbxignore\\state.json``.
Linux: ``$XDG_STATE_HOME/dbxignore/state.json`` (fallback ``~/.local/state/...``).
"""
```

`user_state_dir()` has a `darwin` branch returning `~/Library/Application Support/dbxignore/`, but the module header documents only two of three platforms. Same shape as item 47 — pre-v0.4 wording missed by the macOS port's doc sweep.

CLAUDE.md's gotchas section explicitly names `state.user_state_dir()` as the *"single source of truth"* for the per-user path; the docstring should match what the function actually returns across all supported platforms.

**Fix:** add a third bullet:
```
macOS: ``~/Library/Application Support/dbxignore/state.json``
       (logs split off to ``~/Library/Logs/dbxignore/`` per Apple's app-data conventions).
```

**Urgency:** very low (cosmetic). Bundle with items 46-47 as a doc-currency mini-sweep.

Touches: `src/dbxignore/state.py:1-5`.

**Status: RESOLVED 2026-05-02 (PR #90).** Added the macOS bullet verbatim from the item's prescribed fix (path + `~/Library/Logs/dbxignore/` log split note). Module docstring now matches the three-platform shape of `user_state_dir()` and `user_log_dir()`.

## 49. `_require_absolute` duplicated byte-identically across `linux_xattr.py` and `macos_xattr.py`

The two xattr backends each defined an identical helper:

```python
def _require_absolute(path: Path) -> None:
    if not path.is_absolute():
        raise ValueError(f"markers requires an absolute path; got {path!r}")
```

`linux_xattr.py:36-38` and `macos_xattr.py:244-246` were byte-identical, called from three sites in each module (`is_ignored`, `set_ignored`, `clear_ignored`). The Windows backend (`windows_ads.py`) folds the same check into `_stream_path` because it also has to construct the `\\?\path:streamname` string — different shape, not a dedup candidate.

Surfaced 2026-05-02 in the same `/simplify` whole-codebase review pass that filed items 50-51.

**Fix:** moved to `_backends/__init__.py:require_absolute` (no underscore prefix — the module is the privacy boundary, the name is package-internal). Both xattr backends now `from . import require_absolute as _require_absolute` to keep the local-name `_require_absolute` at all six callsites — zero churn at the call layer.

**Status: RESOLVED 2026-05-02 (PR #89).** Net −10 lines, one source of truth for the validator. All 209 portable + 6 Windows-only tests pass post-change; ruff clean.

Touches: `src/dbxignore/_backends/__init__.py` (new helper, replaces 1-line stub); `src/dbxignore/_backends/linux_xattr.py` (helper deleted, import added); `src/dbxignore/_backends/macos_xattr.py` (helper deleted, import added).

## 50. `windows_task.detect_invocation` partially overlaps `_common.detect_invocation` but diverges in non-frozen branch

`install/windows_task.py:17-37` defines its own `detect_invocation()` rather than importing the shared one from `install/_common.py:16-67`. The two functions:

- **Frozen branch (PyInstaller bundle):** functionally identical. Both prefer the `dbxignored[.exe]` sibling, fall back to `(sys.executable, "daemon")`. The cross-platform `_common.py` already handles Windows via `daemon_name = "dbxignored.exe" if sys.platform == "win32" else "dbxignored"` (line 51).
- **Non-frozen branch:** genuinely diverges. `_common.py` first tries `shutil.which("dbxignored")` (the `uv tool install` PATH-shim case) and falls back to `python3 -m dbxignore daemon`. `windows_task.py` skips the PATH lookup entirely and goes straight to `pythonw.exe -m dbxignore daemon` — `pythonw.exe` is the windowless Python interpreter, important for a daemon launched by Task Scheduler at logon (no console flash, no orphaned `conhost.exe`). The `_common.py` shape would land `python.exe` (with a console window) on Windows.

So a naive consolidation would regress the Task Scheduler UX. The clean unification path is to teach `_common.py`'s non-frozen branch to prefer `pythonw.exe` over `python.exe` on Windows (or to look up a `dbxignored` PATH shim first on Windows too — currently skipped). Either is reachable but neither is a one-line refactor; both touch behavior that was load-bearing for the v0.1 Windows install layer's first ship and warrant explicit Windows-leg tests.

Surfaced 2026-05-02 in a `/simplify` pass on the whole `src/dbxignore/` package. The reuse-review agent flagged the duplication; verification showed the divergence is real, not accidental.

**Fix candidates:**

- **Extend `_common.detect_invocation` to handle Windows in the non-frozen branch** (preferred path). Add `pythonw.exe` selection on `sys.platform == "win32"`; decide whether to keep or skip the `shutil.which("dbxignored")` lookup (current Windows behavior skips it, so the conservative answer is "skip on Windows"). Then collapse `windows_task.detect_invocation` to `from ._common import detect_invocation`. ~15 lines net deletion plus a Windows-specific test asserting `pythonw.exe` selection.
- **Status quo.** The duplication is a maintenance footgun (any frozen-branch fix has to be made in two places), but it's not a correctness issue today. Defensible if the Task Scheduler invocation is considered stable enough that the cost of touching it outweighs the dedup.

**Urgency:** low. The frozen-branch logic is what users hit in v0.4 (PyInstaller bundle on Windows); the non-frozen branch is the editable-install developer path. Companion to item #26 (the unreachable `RuntimeError` in `_common.detect_invocation`). Bundle with the next install-layer touch.

Touches: `src/dbxignore/install/_common.py` (Windows branch in non-frozen path); `src/dbxignore/install/windows_task.py` (delete local `detect_invocation`, import from `_common`); new test in `tests/test_install.py` or `tests/test_install_common.py` asserting `pythonw.exe` selection on Windows.

## 51. `install/__init__.py` platform dispatch duplicated across `install_service` and `uninstall_service`

`src/dbxignore/install/__init__.py` has two near-identical 14-line if-elif-else dispatchers (`install_service` and `uninstall_service`), each branching `sys.platform` against `win32` / `linux*` / `darwin` and importing+calling the matching backend's `install_*` / `uninstall_*` function. The two functions differ only in the imported function name and the call.

A `/simplify` quality-review agent (2026-05-02) proposed extracting a `_dispatch_platform_action(action: str) -> Callable` helper that takes `"install"` or `"uninstall"` and returns the matching backend function, eliminating the duplicate branching.

**Counterargument** (chosen direction): the current shape is six trivial blocks (3 platforms × 2 ops), each block is two lines (lazy import + call), and the structure makes it trivial to add a fourth platform — touch one place per op. A factored-out dispatcher would (a) introduce a stringly-typed `action` parameter, (b) couple install and uninstall behind one indirection so a reader has to walk through the helper to see what each op does, and (c) violate the project's "Three similar lines is better than a premature abstraction" rule from CLAUDE.md's `# Doing tasks` section. The duplication is *intentional clarity*, not accidental copy-paste.

This is the same shape as item #40 — filed for the design-tension record so future readers see "this was considered and explicitly rejected" rather than re-discovering the pattern in another `/simplify` pass.

Surfaced 2026-05-02 in the same pass that filed items #49 (resolved in PR #89) and #50.

**Fix candidates:**

- **Status quo** (recommended). Current shape is the right balance for 3 platforms × 2 ops. Re-evaluate if a fourth platform lands or if a third op (e.g. `enable_service` / `disable_service`) is added — at that point the rule-of-three trigger fires and extraction becomes proportionate.
- **Extract `_dispatch_platform_action(action)`.** Saves ~10 lines but adds a layer of indirection. Defensible if the maintainer prioritizes line-count over branching-structure-clarity.

**Urgency:** very low. Code-quality observation only; current shape is defensible.

Touches: `src/dbxignore/install/__init__.py` (would touch all 14 lines if the extract path is chosen).

## 52. Watchdog `OSError(ENOSPC)` at observer startup surfaces as an opaque traceback

The daemon's call to `observer.start()` (via `Observer().schedule(handler, root, recursive=True)` in `daemon.run`) propagates `OSError(errno.ENOSPC, "inotify watch limit reached")` unhandled when `fs.inotify.max_user_watches` is exhausted. systemd marks the unit failed; the user sees only a Python traceback in `journalctl --user -u dbxignore.service`, with no hint at the kernel-side root cause or remediation.

Default `fs.inotify.max_user_watches` varies across kernels and distros — common values range from 8192 (older kernels, some VPS images) up to 524,288 (Dropbox/VS-Code-recommended setups). On Linux, dbxignore's recursive watch on `~/Dropbox` consumes ~1 watch per directory; for trees larger than the kernel's per-user limit, the daemon crashes immediately at startup. A user without sudo (shared host, locked-down VPS) cannot self-remediate, and even users who can need to know the right sysctl key — currently not documented anywhere in dbxignore.

Surfaced 2026-05-03 during VPS testing (`scripts/manual-test-ubuntu-vps.sh` against a personal Dropbox account). Beta tester observed the failure mode directly: opaque Python traceback in journalctl, daemon unit in `failed` state, no actionable signal until the test script's diagnostic dump surfaced the underlying error line.

**Fix candidates:**

- **Trap and exit cleanly** (preferred). Wrap the observer setup in `daemon.run` in `try/except OSError as exc: if exc.errno == errno.ENOSPC: ...`. Log a WARNING with the literal sysctl commands. Exit with `os.EX_TEMPFAIL` (75) — systemd marks failed but does not loop-restart. ~10 LOC plus a unit test that mocks `Observer.start` to raise ENOSPC and asserts the daemon logs the message and exits 75. Pair with a README §"Linux daemon prerequisites" subsection that lists the sysctl commands as a one-time setup step.

- **Fall back to `PollingObserver`.** Watchdog ships a polling implementation that doesn't use inotify. Daemon would stay functional but at significant CPU cost (polling tens of thousands of dirs at watchdog's default ~1s rate is brutal) and worse latency. Plausible only as a last-resort fallback gated on user opt-in (env var); not a default. Probably worse than failing fast on the trees that actually hit the limit.

**Urgency:** medium-high. Default-config kernels are the failure mode; users without sudo cannot fix it themselves. Companion to items #53 (sweep cost on large trees) and #54 (the deeper "don't watch ignored subtrees" architectural fix). Cheap to ship and immediately better UX.

Touches: `src/dbxignore/daemon.py` (ENOSPC trap around observer setup); new `tests/test_daemon_inotify_enospc.py` (mock observer start, assert clean exit + log); `README.md` (new "Linux daemon prerequisites" subsection citing the sysctl commands).

**Status: RESOLVED 2026-05-07 (PR #125).** Trap added to `daemon.py` via `_start_observer_or_exit`; covers both ENOSPC (`fs.inotify.max_user_watches`) and EMFILE (`fs.inotify.max_user_instances`). Logs ERROR with the matching sysctl runbook and `sys.exit(75)` so systemd marks the unit `failed`. Other `OSError` shapes still propagate. README's `## Install (Linux)` gained a `### Linux daemon prerequisites` subsection. (B) scope decision per #52's body: `PollingObserver` fallback declined as worse than failing fast on the trees that hit the limit.

## 53. `_sweep_once` walks every directory regardless of marker state — expensive on large trees

`reconcile.reconcile_subtree` (called by `daemon._sweep_once`) traverses every directory under each root via `os.walk(followlinks=False)`. On a 27,000-directory personal Dropbox tree the initial sweep took 49.62s on a 2026-era VPS (Ubuntu 24.04, Python 3.14, observed in journalctl 2026-05-03). Cost is dominated by stat() + xattr query per directory, not by the reconcile match logic — many dirs have no rule effects.

Subtree pruning already fires on NEW matches: when `cache.match(dir)` returns True, `_reconcile_path` sets the marker and the walk skips descendants (covered by test 4b in `scripts/manual-test-ubuntu-vps.sh`). What's missing is the steady-state case: when the walk reaches a directory that is ALREADY marked AND `match()` still confirms it should be, descent into descendants is pure waste — no rule change can have happened since the marker was set, no clears are needed.

The correctness risk is the rule-removal case. If a `.dropboxignore` rule that previously matched `cache/` is removed, the marker on `cache/` becomes stale, and descendants need their (also-stale) markers cleared. The current "always descend" policy makes this self-healing on the next sweep. The natural shape of a pruning optimization: rule-change events already force a re-walk of the affected subtree (the `RULES` event triggers `cache.reload_file` + `reconcile_subtree`), so the steady-state sweep can safely prune on "marker present + match confirms" — the invariant holds as long as any rule mutation reaches the marker-bearing subtree's reconcile before the next steady-state sweep, which the watchdog event-driven path already satisfies.

Surfaced 2026-05-03 during VPS testing. The manual test's daemon-startup poll initially timed out at 30s because the user's tree took ~50s to sweep. Test fixed by raising the poll to 180s; the underlying app cost is the real fix.

**Fix candidates:**

- **Skip on marker-present + match-still-positive.** In `reconcile_subtree`'s walk, before recursing into a subdirectory, read its marker xattr. If present AND `cache.match(dir)` still returns True, prune (no descent, no per-descendant cost). If present AND match returns False, descend (rule removed; clear stale markers). If absent, descend (normal walk). Adds one xattr read per directory but eliminates descent into entire ignored subtrees. ~50 LOC plus invariant tests covering the three cases.

- **Track per-subtree rule-changed flag in `RuleCache`.** Maintain a "subtree X has dirty rules" bitmap; sweep prunes if marker present AND subtree clean. More accurate but more state and more failure modes. Probably over-engineering vs. the marker-read approach.

- **Defer.** Initial sweep cost is one-time per daemon launch. Acceptable for users who restart the daemon rarely. The 50s wall-clock is annoying but not blocking.

**Urgency:** medium. Affects users with large Dropbox trees — exactly the personal-account installs that v0.4+ serves once it leaves alpha. Companion to items #52 (UX) and #54 (watch-budget). Bundle with the next daemon-touching change.

Touches: `src/dbxignore/reconcile.py` (`reconcile_subtree` walk: read marker before descent, conditional prune); `tests/test_reconcile_subtree.py` (new tests: prune-on-match, descend-on-rule-removal, descend-on-marker-absent).

## 54. Watchdog observer schedules one inotify watch per directory; doesn't skip ignored subtrees

`daemon.run` passes `recursive=True` to `observer.schedule(handler, root, recursive=True)`. Watchdog's inotify backend adds one watch per directory in the recursive subtree. Marked-ignored subtrees consume watch slots even though dbxignore has nothing to react to inside them — Dropbox isn't syncing them, and any user changes inside e.g. a `node_modules/` shouldn't trigger reconcile.

For a 27,000-dir Dropbox tree this consumes ~27k watch slots out of the per-user `fs.inotify.max_user_watches` budget. Default 8192 is exceeded out of the box (item #52). Bumped to the standard 524,288 it works fine, but ~95% of the budget is allocated to subtrees the daemon doesn't care about — only really matters at much larger scales (~500k+ dirs).

Architectural shape of the fix:

1. Walk the tree at startup (or piggyback on `_sweep_once`'s walk) and identify directories WITHOUT the ignored marker.
2. For each unmarked directory, call `observer.schedule(handler, dir, recursive=False)` — N independent non-recursive watches instead of one recursive one.
3. Maintain watch lifecycle: when a directory is newly marked during reconcile, `observer.unschedule` its watch and any descendants. When a directory is unmarked, schedule a new watch and walk it to catch any newly-unmarked descendants.
4. Handle delete and move events for watched directories — `unschedule` is required to avoid stale-state warnings from watchdog.

Race conditions to design against: a directory event arriving for a path that was just unscheduled; a `.dropboxignore` change firing reconcile mid-walk; the observer's internal handlers seeing events for paths the daemon thinks aren't watched. The `RuleCache._rules` RLock pattern (per CLAUDE.md "If you add cross-root shared state to RuleCache or reconcile, revisit this") is the existing precedent — a similar invariant would have to hold for watch state.

Surfaced 2026-05-03 alongside items #52 and #53. The deepest scalability fix in the trio but also the most invasive — race-condition-prone state machine work that is easy to get subtly wrong.

**Fix candidates:**

- **Per-directory watches with full mark/unmark lifecycle.** The architecture above — ~200+ LOC plus extensive race-condition tests and large-tree perf benchmarks. Worth the cost only if a beta tester actually hits the watch ceiling on a system with `max_user_watches` already raised to 524,288.

- **Per-directory watches without dynamic lifecycle** (simpler subset). Walk once at startup, schedule non-recursive on unmarked dirs, accept that newly-marked dirs continue to consume their watch slot until daemon restart. ~50 LOC. Catches the static-state savings (~80% of the budget) without the lifecycle complexity. Trade-off: a user who marks a 10,000-file dir doesn't see the watch budget recover until daemon restart, AND changes inside a dir that was previously ignored but had its rule removed won't be caught until restart (a real correctness regression vs. status quo).

- **Defer.** Status quo. Items #52 and #53 cover the immediate UX and perf wins; a sysctl bump to 524,288 is sufficient for any plausibly-sized Dropbox account in 2026.

**Urgency:** low. No production hit yet — `max_user_watches=524288` (standard recommendation) is sufficient for any plausibly-sized tree. Defer until a beta tester observes the watch budget exceeded after raising it; until then, the architectural complexity is unjustified.

Touches: `src/dbxignore/daemon.py` (observer setup + new watch-lifecycle helper); `src/dbxignore/reconcile.py` (callback hook for "directory just marked/unmarked"); new `tests/test_daemon_watch_lifecycle.py` (per-dir scheduling, mark/unmark transitions, race scenarios).

## 55. `state.write()` does not parse-back validate before atomic replace

**Status: RESOLVED 2026-05-04 (PR #95).**

`src/dbxignore/state.py:write()` writes `state.json` atomically by serializing to `state.json.tmp` via `path.write_text(json.dumps(...))` and then `os.replace`-ing it into place (per CLAUDE.md's `state.write()` gotcha). There's no validation step between the temp write and the rename. If a future serializer change ever produced malformed JSON, `os.replace` would commit it to disk; the next `state._read_at` would return `None` (per its `JSONDecodeError` arm), and `daemon.run`'s singleton check would read that as "no prior daemon," allowing two concurrent daemons.

A defensive read-back step between `write_text` and `os.replace` — read the temp file, `json.loads` it, raise on failure and unlink the temp — costs one extra read of a small file (typically <1KB) and catches a future serializer regression before it becomes durable.

**Fix candidates:**

- **Add a parse-back validation step in `state.write()`** (preferred). After `tmp.write_text(payload)`, call `json.loads(tmp.read_text())` inside a try/except that unlinks the temp file and re-raises on failure, before the `os.replace`. ~5 LOC. Pair with a test that monkeypatches `json.dumps` to return a known-invalid string and asserts `state.write()` raises without leaving `state.json` modified.
- **Defer.** No production hit — `json.dumps` on the project's current state shape never produces invalid output. The defense is purely against a future serializer change going subtly wrong. Acceptable to wait for a real failure mode.

**Urgency:** low. Cheap insurance, no observed pain. Bundle with the next state-layer touch.

Touches: `src/dbxignore/state.py` (`write` function: insert parse-back step between `write_text` and `os.replace`); new test in `tests/test_state_*.py` (monkeypatch `json.dumps` to inject invalid output, assert `write` raises before commit).

## 56. No `dbxignore generate` / `apply --from-gitignore` to reuse `.gitignore` rules

**Status: RESOLVED 2026-05-04 (PR #94).**

Users with a populated `.gitignore` who want the same exclusions for Dropbox sync currently have to author a parallel `.dropboxignore` by hand. dbxignore's `pathspec`-based rule engine in `src/dbxignore/rules.py` accepts the same gitignore syntax, so there's no engine-side blocker — the gap is purely at the CLI/file-source layer.

Two related shapes:

1. **`dbxignore generate <path>`** — produce a `.dropboxignore` next to a `.gitignore`, copying the rule lines verbatim. Lets the user diverge afterwards (the `.dropboxignore` becomes the durable rule source).
2. **`dbxignore apply --from-gitignore <path>`** — load rules from `.gitignore` (or any nominated file) into an in-memory `_LoadedRules` and run reconcile, with no `.dropboxignore` written. One-shot, ephemeral.

`pathspec` already handles negations correctly, so dbxignore can pass `.gitignore` lines through without filtering them — though the user should be warned that negations under ignored ancestors are inert (`_dropped` semantics, see `rules_conflicts.py`). Documentation should also note the semantic divergence between the two ignore models: gitignore rules say "git doesn't track this"; dbxignore rules say "Dropbox should not sync this", which means matched files get *removed from cloud sync*. Users transplanting `.gitignore` rules verbatim need that warning.

**Fix candidates:**

- **Ship both shapes as one feature.** `generate` is a ~30-LOC subcommand; `apply --from-gitignore <p>` reuses the existing apply pipeline with a swapped rule-source. ~80 LOC + tests + README §"Using `.gitignore` rules". Document the divergence-warning in `--help` text.
- **Ship just `generate`.** Half the value at half the cost — leaves users to `cp .gitignore .dropboxignore` themselves. Defensible if the file-on-disk path is the only one with real demand.
- **Ship just `apply --from-gitignore`.** Skips file-on-disk creation. Useful for one-off cleanups; doesn't help users who want a durable rule file.
- **Defer.** No request signal yet. Filed against the day a user asks "why do I have to maintain two ignore files?"

**Urgency:** low-medium. Ergonomic gap; zero pain reports.

Touches: `src/dbxignore/cli.py` (new `generate` command + `--from-gitignore` flag on `apply`); `src/dbxignore/rules.py` (factor `_LoadedRules` construction so it can take an arbitrary source path); new tests in `tests/test_cli_generate.py`; README §"Using `.gitignore` rules".

## 57. `EventKind.DIR_CREATE` events that already match a cached rule wait the full debounce window

**Status: RESOLVED 2026-05-04 (PR #96).**

`src/dbxignore/daemon.py:_classify` distinguishes `EventKind.{RULES,DIR_CREATE,OTHER}` and the `Debouncer` applies per-kind timeouts (`DEFAULT_TIMEOUTS_MS`). The shape exists because rule reloads benefit from coalescing burst events and bulk OTHER events should batch. There's a third case the current shape doesn't optimize for: a single `DIR_CREATE` event whose path *already matches* a cached rule. For that path, the marker write should land before Dropbox's own watcher sees the directory and starts ingesting its contents — every millisecond of debounce extends the window where Dropbox can upload a child file before the parent's marker is set.

The natural shape: in `_dispatch`, before queueing into `Debouncer`, check `cache.match(resolved_path, is_dir=True)` for a `DIR_CREATE` event. If True, apply the marker synchronously and skip the debouncer. Other event kinds and unmatched DIR_CREATEs go through the existing per-kind debounce. This is per-event-kind bypass, not a global "no debounce" — `RULES` events keep their debounce (they should coalesce), OTHER events keep theirs. Only the matched-create case fast-paths.

Risk: per CLAUDE.md's note on `_applicable`, the cache lookup is lock-free and "between `.get()` calls a debouncer-thread mutation can change which ancestor's rules apply." For `_dispatch`-time matching the risk is that a rule was just deleted and the bypass marks a path that should no longer be marked — caught by the next sweep, but a transient false-positive marker exists in the gap. Acceptable trade if the debounce is the bigger latency sink; the worst-case staleness is bounded by the watchdog event delay, which is exactly what the existing design relies on.

**Fix candidates:**

- **Bypass debouncer for matched DIR_CREATE.** ~30 LOC: in `_dispatch`, branch on `kind == DIR_CREATE and cache.match(resolved_src, is_dir=True)` and call `_reconcile_path` synchronously. Tests: simulate a Create event for a path matching a cached rule, assert marker present immediately (before the debouncer would have fired); stale-rule race test (delete rule, fire Create, assert subsequent sweep clears the transiently-set marker).
- **Add an env-var to enable the bypass** (`DBXIGNORE_FAST_DIR_CREATE=1`). Conservative rollout — opt-in, observable, removable. No behavior change for existing users.
- **Defer.** No measured race-window pain reported. Status quo trades latency for transactional correctness in the cache-lookup, which is defensible.

**Urgency:** low until measured. Bundle with the next daemon-touching change if it lands; otherwise defer until a user reports the create-race symptom (a child file syncs before the parent's marker arrives).

Touches: `src/dbxignore/daemon.py` (`_dispatch`: bypass arm); `tests/test_daemon_dispatch.py` (matched-create-fast-path test, stale-rule-race test); CLAUDE.md daemon section (document the bypass + the transient-mismarked-path trade-off).

## 58. `_pluginkit_extension_state() == "unknown"` arm in `_detected_attr_name()` falls through to legacy attribute

**Status: RESOLVED 2026-05-04 (PR #97).**

`src/dbxignore/_backends/macos_xattr.py:_detected_attr_name()` resolves the marker name via path-primary (info.json) + pluginkit-disambiguating logic per CLAUDE.md's macOS section. When `_pluginkit_extension_state()` returns `"unknown"` (subprocess error, missing `pluginkit` binary, hung query — the test-host case in the existing tests), the current logic falls back to legacy `com.dropbox.ignored`. On a File-Provider-mode user where detection misfires for environmental reasons, the legacy attribute is silently no-op and Dropbox keeps syncing the marked path.

Two failure shapes converge here: (a) detection-uncertainty due to environmental issues (genuinely don't know which mode), and (b) detection-incorrectness due to a bug in the path/pluginkit logic. Today both fall through to legacy. A defensive fallback for case (a): when detection genuinely returns unknown, write both `com.dropbox.ignored` AND `com.apple.fileprovider.ignore#P`. The active sync stack reads its own; the inactive one ignores the stray attribute. Trade-off: a stray attribute on disk vs. a silent no-op for users on File Provider whose detection misfires. The user-visible outcome is correctly ignored either way; the cost is metadata cleanliness.

This is *only* the "unknown" path — the well-understood path-detection arms keep their current single-attribute-write behavior because they're definitive.

**Fix candidates:**

- **Write both attributes when `_pluginkit_extension_state() == "unknown"`** (preferred). Modify `_detected_attr_name()` to return a sentinel (`"both"` or a tuple) for the unknown arm; `set_ignored`/`is_ignored`/`clear_ignored` branch on the sentinel and iterate both attrs. ~20 LOC + a test that mocks `_pluginkit_extension_state` to return `"unknown"` and asserts both attributes are written. Pair with a CLAUDE.md addition documenting the always-write-both-on-unknown rule and citing the metadata-stray vs. silent-failure trade.
- **Surface a WARNING when detection returns unknown** (companion). Current behavior is silent; users have no signal that their setup is in the uncertain branch. INFO/WARNING log at first detection cycle, deduplicated. Pairs with item #37 (sync-mode result observability).
- **Defer.** No reported pain — the current "unknown → legacy" arm fires only on test hosts and edge environments. Acceptable until a user reports the silent-no-op symptom.

**Urgency:** low. Speculative defensive coding, no observed user hit. Bundle with the next macOS-backend touch (item #37 already pending).

Touches: `src/dbxignore/_backends/macos_xattr.py` (`_detected_attr_name`: sentinel for unknown; `set_ignored`/`is_ignored`/`clear_ignored`: branch on sentinel); `tests/test_macos_xattr_unit.py` (mock `_pluginkit_extension_state` to `"unknown"`, assert dual-attribute write); CLAUDE.md macOS section (document the unknown-arm behavior).

## 59. `dbxignore status` doesn't report daemon liveness

**Status: RESOLVED 2026-05-04 (PR #98). Note: framing correction — the headline ("daemon liveness in `status`") was already implemented in v0.3.0 (commit 604ff07): `cli.py:_process_is_alive(pid)` existed and `status()` already printed `daemon: running (pid=X)` / `daemon: not running (pid=X)`. The remaining gaps were (a) PID-reuse false positives — `_process_is_alive` did only `psutil.pid_exists`, missing the process-name guard the daemon's own `_is_other_live_daemon` already had — and (b) the "not running" message couldn't distinguish a cleanly-stopped daemon from a stale state.json. PR #98 fixes both via a shared `state.is_daemon_alive(pid)` helper used by both CLI and daemon, with a clarified message reading "last pid=X — state.json may be stale" in the not-running branch.**

`src/dbxignore/cli.py:status` prints rule-evaluation summary (configured roots, marker counts, conflicts) but says nothing about whether the daemon is currently running. To check liveness today, users read `daemon.log` or query the platform service manager (`schtasks /Query`, `systemctl --user status`, `launchctl list`). `state.json` already records the daemon PID atomically per `state.write()`; a signal-0 (POSIX) or `tasklist /FI "PID eq …"` (Windows) check would give a definitive liveness answer in O(1).

The piece is small, the UX gap is concrete: a user running `dbxignore status` after `dbxignore install` could see at a glance whether the service started successfully.

**Fix candidates:**

- **Add a daemon-status block to the `status` output.** ~30 LOC: read `state.json`, check the recorded PID via `os.kill(pid, 0)` on POSIX or `subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True)` on Windows. Print one of "running (PID X)" / "not running" / "stale state.json (PID X recorded but process gone)". Test by writing a fake `state.json` and monkeypatching `os.kill` / `subprocess.run`.
- **Defer.** Users can use platform service tools; not a critical gap.

**Urgency:** low-medium. UX shortfall, not a bug. Bundle with the next CLI-touching change.

Touches: `src/dbxignore/cli.py` (`status` command: append daemon-liveness block); `src/dbxignore/state.py` (helper for "is recorded PID alive?" if not already present); `tests/test_cli_status.py` (running / not-running / stale-PID cases).

## 60. `dbxignore status` has no machine-readable single-line summary

**Status: RESOLVED 2026-05-04 (PR #99). Two framing decisions worth flagging for future readers: (a) **stdout, not stderr.** The body specified "stable one-line representation to stderr"; the implementation emits on stdout because stderr is the conventional channel for warnings/errors and status-bar widgets capture stdout (`some-widget | parse`). The body's cron-friendly motivation ("redirect stdout to /dev/null, capture stderr") only applies if `--summary` outputs both human and machine on different streams, which would be more complex than the chosen "summary replaces human output" semantic. (b) **All-key=value, not the body's count-noun mix.** The body suggested `<roots> roots, <ignored> ignored, <conflicts> conflicts, daemon=<state>`; the implementation uses uniform `key=value` (`state=N pid=N marked=N cleared=N errors=N conflicts=N`) for parser uniformity. Format is documented in README §"Status-bar integration" and treated as public API per SemVer.**

`dbxignore status` prints multi-line human output. Users wanting to wire dbxignore into a status-bar widget (polybar / tmux right-status / i3blocks / sketchybar) currently have to grep/awk it themselves, which is fragile across releases. A `--summary` flag emitting a stable one-line representation to stderr — e.g., `<roots> roots, <ignored> ignored, <conflicts> conflicts, daemon=<state>` — would give status-bar tooling a contract to parse. Single-line output is also cron-friendly: redirect stdout to `/dev/null`, capture stderr, alert on changes.

**Fix candidates:**

- **Add `dbxignore status --summary`** producing a fixed-format single line on stderr. ~15 LOC plus a test that asserts the format string. Document the format under a README §"Status integrations" subsection. Treat the format as part of the public API for SemVer purposes (changes bump MINOR pre-1.0, MAJOR post-1.0).
- **Add `dbxignore status --json`** for fully-structured machine consumption. More flexible but invites maintenance churn (every status field change is a JSON-schema change). The single-line `--summary` is the cheaper contract.
- **Defer.** No reported pain.

**Urgency:** low. Polish, not a gap. Bundle with the next CLI-touching change.

Touches: `src/dbxignore/cli.py` (`status` command: `--summary` flag, stable-format emission); `tests/test_cli_status.py` (`--summary` format assertion); README §"Status integrations" subsection.

## 61. No verb to clear all markers without removing rule files or per-user state

**Status: RESOLVED 2026-05-04 (PR #100). One framing addition vs. the body's spec: a daemon-alive guard. `clear` refuses to run when `state.is_daemon_alive(s.daemon_pid)` is True because the daemon's next sweep — within seconds for rule-reload events, within the hour for the recovery sweep — would re-apply rule-driven markers and silently undo the clear. The body didn't anticipate this interaction; the implementation surfaces it as a hard refusal with `--force` as the conscious override. Plus a confirmation prompt (Dropbox starts syncing previously-ignored paths immediately, potentially gigabytes of upload — `--yes` skips for scripted use) and `--dry-run` per the body. Total ~120 LOC including the safety scaffolding, vs. the body's estimated 40 LOC for the bare walk-and-clear.**

`dbxignore uninstall --purge` (in `src/dbxignore/cli.py`) clears every marker and deletes per-user state. There's no intermediate verb that says "clear all markers under the watched roots, but keep `.dropboxignore` files and `state.json` in place." A user who wants to temporarily unsync everything (e.g., to stage a manual sync change, or to test that Dropbox re-syncs from cloud as expected) has to choose between the heavy `--purge` (loses the rule files and state) and a manual recursive `xattr -d` / `Remove-Item -Stream` walk.

The natural verb shape: `dbxignore clear` — walks each watched root, clears every marker, exits. Leaves `.dropboxignore` and `state.json` untouched. Symmetric inverse of `dbxignore apply` against the existing rules: `apply` sets every marker the rules dictate; `clear` unsets every marker.

**Fix candidates:**

- **Add `dbxignore clear`** (preferred). ~40 LOC: walk roots via existing `reconcile_subtree` infrastructure but with a "force unset all" rule predicate. Pair with a `--dry-run` flag that prints what would be cleared. Tests: mark some files, run clear, assert all clean; verify `.dropboxignore` files untouched; verify state.json untouched.
- **Add `dbxignore apply --reset` / `--invert`** as a flag on the existing apply command. Slightly less discoverable but reuses more code.
- **Defer.** Users can `dbxignore uninstall --purge && reauthor .dropboxignore`. Workaround exists; verb is a polish.

**Urgency:** low. Adds a discoverable middle-ground operation; no current pain.

Touches: `src/dbxignore/cli.py` (new `clear` command); `src/dbxignore/reconcile.py` (potentially: a "clear-all" mode of `reconcile_subtree` that ignores rules and just unsets markers); `tests/test_cli_clear.py`; README §"CLI reference".

## 62. `dbxignore apply --recent N` for cron-driven incremental sweeps

**Status: RESOLVED 2026-05-04 (PR #101). Closed without code change — model mismatch with dbxignore's architecture. The source idea is from a stateless pattern-driven shell script (invocation shape `./script --pattern "*build*" --recent 60`) where `--recent` *is* the scoping architecture for a one-shot ad-hoc run. dbxignore is stateful and rule-driven: persistent `.dropboxignore` files → watchdog real-time observer → hourly recovery sweep. The hourly sweep being full-tree is load-bearing: its purpose is exactly to catch events the watchdog dropped, including events older than any reasonable `--recent N` horizon. Limiting any sweep to `--recent N` defeats the recovery semantic by definition. On the user-facing `apply` side, a rule-edit re-application needs to evaluate the *whole* tree against new rules — `--recent` would miss old subtrees that should now be marked under the freshly-edited rule. Item #53 (skip-on-(marker-present + match-still-positive) subtree pruning) remains the architecture-aligned perf direction; this item retired as wrong-fit. Same shape as #59's framing-correction precedent — record the rationale inline so future readers see why the resolution was "no code change" rather than the body's prescribed implementation.**

`daemon._sweep_once` and `cli.apply` call `reconcile_subtree`, which walks every directory under each root via `os.walk`. For a 27k-dir tree this is the 49.62s cost item #53 already documents under "skip-on-(marker-present + match-still-positive)". A complementary perf path for cron-driven users: limit a given sweep to directories with `mtime` newer than a horizon. `--recent N` (minutes) or `--since <timestamp>` filters the walk to recently-mutated subtrees. For a user running `dbxignore apply --recent 5` from a 5-minute cron, the walk skips ~99% of an unchanged tree.

Caveat: `mtime` granularity. A directory's `mtime` updates when an immediate child is added or removed, but not when a grandchild changes. So `--recent N` catches *structural* changes (new dir, removed dir, new top-level file in a dir) but misses content changes inside untouched directory frames. For dbxignore's use case (rule-driven marker reconciliation, where rules apply at the directory level), structural-change tracking is what matters — content changes don't change which subtree should be marked. Document the caveat in `--help` and README so users with content-change-driven workflows know the recovery sweep is the right place for them.

**Fix candidates:**

- **Add `dbxignore apply --recent N`** (`N` in minutes) that filters `reconcile_subtree`'s walk to dirs with `mtime` newer than `now - N min`. ~30 LOC plus a test that creates a tree, touches one subdir, runs `apply --recent 1`, asserts only that subdir was reconciled. Document the mtime-granularity caveat.
- **Generalize as `dbxignore apply --since <ISO8601>`** for absolute-timestamp use. Marginally more flexible; `--recent N` covers the common case. Could ship both behind one flag (autodetect by string format).
- **Per-event-kind incremental sweep in the daemon** — separate scope; defer.
- **Defer.** Users with large trees can already mitigate via item #53's marker-present pruning. `--recent` is a complementary user-controlled mode, not strictly necessary.

**Urgency:** low. Bundles naturally with item #53's `reconcile_subtree` work.

Touches: `src/dbxignore/cli.py` (new `--recent` / `--since` flags on `apply`); `src/dbxignore/reconcile.py` (`reconcile_subtree`: optional `mtime_horizon` parameter, walk-filter); `tests/test_reconcile_subtree.py` (mtime-filtered walk test); `--help` text + README.

## 63. No `dbxignore init` to scaffold a starter `.dropboxignore`

**Status: RESOLVED 2026-05-04 (PR #102). Two scope choices vs. the body's sketch: (a) **Strategy 1 emission, not detection-driven.** The body's wording ("seed a starter file with one rule per detected pattern, plus comments noting which directories triggered each") suggested detection-driven content; the implementation always writes the full template content with detection only annotating the header. Reasons: file patterns (`*.pyc`, `.DS_Store`, etc.) can't be cheaply detection-driven (would require deep walks); init users want hand-holding (the strong-defaults-then-edit-down UX is easier than the sparse-then-edit-up alternative); future-proofing — a freshly-cloned repo has no `node_modules` yet, but the user wants the pattern ready when the dev environment lands. (b) **Comprehensive ecosystem template, not the body's 8-pattern sketch.** The packaged template at `src/dbxignore/templates/default.dropboxignore` covers Node.js (10 patterns), Python (15), Rust, JVM, .NET, frontend frameworks (8), build/dist outputs, OS detritus (Win/macOS/vim), and generic catch-alls — ~40 patterns total, ecosystem-categorized with section headers. Sourced from a real-world-tested example file the user maintained at their Dropbox root.**

A user running `dbxignore` for the first time on a Dropbox folder containing a development tree authors `.dropboxignore` from scratch. The most common patterns (`node_modules/`, `__pycache__/`, `.venv/`, `target/`, `build/`, `dist/`, `.pytest_cache/`, `.ruff_cache/`) are common across the dev-in-Dropbox use case but currently have to be copy-pasted from documentation or memory.

A `dbxignore init [<path>]` subcommand could:

1. Detect an existing `.dropboxignore` and refuse to overwrite (or merge with `--force`).
2. Walk the target dir for marker-bait directories — `node_modules/`, `__pycache__/`, `.venv/`, `target/` — and seed a starter file with one rule per detected pattern, plus comments noting which directories triggered each.
3. Optionally include a curated default set even when nothing is detected (`build/`, `dist/`, `.pytest_cache/`, `.ruff_cache/`) so the file isn't empty.

The walk should be capped at a shallow depth (e.g., 3) so `init` doesn't recurse into existing `node_modules` chains.

**Fix candidates:**

- **Ship `dbxignore init` with detection + curated defaults** (preferred). ~80 LOC: subcommand, detection scan capped at depth 3, template emission. Tests: scaffold against a synthetic tree, assert generated file content matches expected; assert refuse-to-overwrite without `--force`.
- **Ship `dbxignore init --no-scan`** (curated defaults only). Simpler; no detection latency. Worse first-run UX.
- **Ship a packaged template only** (no subcommand), documented as `cp <pkg-data-path>/default.dropboxignore .dropboxignore`. Passive; no auto-detection.
- **Defer.** No demand signal.

**Urgency:** low. Quality-of-life feature, no observed pain.

Touches: `src/dbxignore/cli.py` (new `init` command); new `src/dbxignore/templates/default.dropboxignore` (curated default rules); detection helper (could go in `cli.py` or a new `init.py`); `tests/test_cli_init.py`; `pyproject.toml` (add template dir to package data); README §"First-time setup".

## 64. `dbxignore daemon` has no `--dry-run` preview mode

**Status: RESOLVED 2026-05-04 (PR #103). Two framing corrections vs. the body. (1) **`apply --dry-run` did NOT actually exist** when the body was filed — the body's premise ("`dbxignore apply --dry-run` prints what *would* be marked/cleared and exits without mutating") was wrong; only `clear --dry-run` existed (added in PR #100). PR #103 implements `apply --dry-run` for the first time, threading a `dry_run: bool` keyword-only parameter through `reconcile_subtree` → `_reconcile_path` and adding `would_mark` / `would_clear` lists to `Report` (populated only in dry-run mode so steady-state daemon sweeps pay nothing). (2) **`daemon --dry-run` declined** as low-value polish. Body's stated use case ("evaluate what the daemon would do over the next 5 minutes") under-delivers in practice: the hourly sweep tick won't fire in any reasonable preview window, and watchdog events only show up when the user actively changes the tree (in which case they could just run `apply --dry-run` after the change). The only deterministic part of the daemon is the initial sweep, which `apply --dry-run` covers identically with a bounded, terminating walk. Same framing-correction shape as #59 and #62.**

`dbxignore apply --dry-run` prints what *would* be marked/cleared and exits without mutating. `dbxignore daemon` has no equivalent — it always starts the watchdog observer and applies markers. A user evaluating "what would the daemon do over the next 5 minutes if I started it?" has no way to check without committing.

A `dbxignore daemon --dry-run` mode would:

- Run the initial sweep in dry-run, printing intended marker mutations.
- Keep the watcher running but route every reconcile call through the dry-run path (print, don't mutate).
- Exit on Ctrl-C as normal.

This is a parity feature with `apply --dry-run`, useful for new users who want to see daemon behavior without commitment.

**Fix candidates:**

- **Add `dbxignore daemon --dry-run`** (preferred). ~30 LOC: thread a `dry_run` bool through `daemon.run`, `_sweep_once`, `_dispatch`, `_reconcile_path`. The dry-run path likely already exists in reconcile (apply uses it); the daemon would just need to plumb it through and ensure all mutation paths respect it. Tests: start a dry-run daemon against a fake tree, fire a Create event, assert the matcher was called but no marker was set.
- **Document an existing `DBXIGNORE_DRY_RUN=1` env var** if one exists; otherwise this is the only path.
- **Defer.** Users can read `apply --dry-run` output and trust the daemon to do the same.

**Urgency:** low. Polish, no correctness gap.

Touches: `src/dbxignore/daemon.py` (`run`, `_sweep_once`, `_dispatch`: thread `dry_run`); `src/dbxignore/reconcile.py` (verify `reconcile_subtree` already accepts dry-run; if not, add); `tests/test_daemon_dispatch.py`.

## 65. No Windows Explorer right-click context-menu integration

dbxignore is CLI-and-daemon only on Windows; users wanting to ignore a single folder ad-hoc must `dbxignore apply` from a terminal or update `.dropboxignore` and wait for the daemon to react. A right-click context-menu verb in Explorer ("Ignore from Dropbox", "Un-ignore from Dropbox") would close that gap. Windows shell-extension verbs registered under `HKEY_CLASSES_ROOT\Directory\shell\…\command` (or per-user equivalents under `HKEY_CURRENT_USER\Software\Classes\…`) are a no-DLL way to add custom Explorer actions, calling out to a tool with `%1` substituted.

The shape worth shipping:

- An optional install arm: `dbxignore install --shell-integration` writes per-user registry keys (per-user avoids UAC), invoking `dbxignore.exe ignore "%1"` and `dbxignore.exe clear "%1"` (or whatever verbs land per items #61).
- An `AppliesTo` filter scoped to discovered Dropbox roots from `roots.discover()` results — generated at install time, not a substring match on path strings (substring filters match false positives on any folder named "Dropbox" anywhere on disk).
- A symmetric uninstall arm: `dbxignore uninstall --shell-integration`.

Routing the actual marker write through `dbxignore.exe` rather than re-implementing the ADS write inline in the registry value gets the `\\?\` long-path correctness of `_backends/windows_ads.py` for free.

**Fix candidates:**

- **Ship `dbxignore install --shell-integration`** as an optional install arm. ~150 LOC: registry-write helper, `AppliesTo` filter from `roots.discover()`, both ignore and unignore verbs, uninstall companion. Tests on Windows-only legs: assert registry keys present after install, assert correct invocation string, assert per-user-vs-per-machine path selection mirrors `_info_json_paths()` precedent. Add `tests/test_install_windows_shell.py` with `windows_only` marker.
- **Ship a static `.reg` file** in `docs/windows-shell-integration.reg` for users to download and double-click. Cheap, less correct (icon path will be wrong for some installs, `AppliesTo` substring filter is unsound).
- **Defer.** Headless CLI is enough for most users; this is power-user UX.

**Urgency:** very low. Feature gap; no demand signal.

Touches: `src/dbxignore/install/windows_shell.py` (new module); `src/dbxignore/install/__init__.py` (optional dispatcher hook); `src/dbxignore/cli.py` (`install --shell-integration` flag, `uninstall --shell-integration`); new `tests/test_install_windows_shell.py` (windows_only); README §"Windows Explorer integration".

## 66. `dbxignore generate` skips out-of-root warning when no Dropbox roots are discovered

`src/dbxignore/cli.py`'s `generate` command emits a stderr warning when the resolved target sits outside any discovered Dropbox root — but the guard is `if discovered and find_containing(target_resolved, discovered) is None:`. When `_discover_roots()` returns `[]` (no `info.json`, `DBXIGNORE_ROOT` unset, Dropbox not installed), the entire warning branch is short-circuited away. The user gets no signal that the produced `.dropboxignore` will not be observed by reconcile or the daemon. Empty-roots is the degenerate case of "all roots are somewhere else"; the warning's whole purpose ("your file won't be observed") applies even harder when no roots exist at all.

The spec error matrix (PR #94's spec at `docs/superpowers/specs/2026-05-04-gitignore-import.md`) lists "target outside all Dropbox roots → exit 0 + stderr warning" but doesn't have an explicit "no-roots" row, so the current code is not strictly spec-incorrect — just inconsistent with the warning's stated rationale.

**Fix candidates:**

- **Drop the `if discovered and ...` guard, keep the inner check.** When `discovered` is `[]`, `find_containing` returns `None` for any path, so the warning fires with the same wording. Single-line change.
- **Tailor the message for the no-roots case.** Distinguish "outside all roots" from "no roots discovered at all" with a different stderr line (e.g. `warning: no Dropbox roots discovered; reconcile will not see <target>`). More informative; one extra branch.
- **Defer.** Users running `generate` on a machine without Dropbox arguably know there's no Dropbox. The asymmetry with `apply` (which exits 2 on no-roots) is intentional — `generate` is documented as not requiring Dropbox.

**Urgency:** low. Cosmetic-leaning; the user could be confused by the silence but the file IS produced correctly.

Touches: `src/dbxignore/cli.py` (`generate` body — adjust the out-of-root warning guard); `tests/test_cli_generate.py` (extend `test_generate_target_outside_roots_warns_but_writes` or add a `test_generate_no_roots_still_warns`).

## 67. `apply --from-gitignore` does not suppress conflict warnings to stderr

In PR #92's `_load_cache` extraction (commit `a6fb74b`), every CLI command was routed through a helper that calls `RuleCache.load_root(..., log_warnings=False)` to suppress per-mutation conflict WARNINGs that would be a stderr duplicate of the structured stdout (`status` already prints conflict rows in a column-aligned table; the per-mutation log line is noise).

PR #94's `_apply_from_gitignore` bypasses `_load_cache` (for good reason — it constructs a fresh single-rule-source cache rather than discovering rules from the tree) but calls `cache.load_external(source, mount_at)` with the default `log_warnings=True`. If the user's gitignore contains a conflicted negation (e.g. `build/` + `!build/keep/`), the WARNING fires to stderr — inconsistent with the regular `apply` path, which suppresses it.

The damage is "stderr noise that the regular `apply` path does not produce." Not a functional defect; not visible unless the user redirects stderr or the test suite asserts against `caplog`.

**Fix candidates:**

- **Pass `log_warnings=False` explicitly.** One-line change in `_apply_from_gitignore`: `cache.load_external(source, mount_at, log_warnings=False)`. Restores stderr parity with `apply`.
- **Make `log_warnings=False` the default.** Cache-layer methods would all become quiet by default; daemon callers (which DO want the warnings logged) would have to pass `log_warnings=True`. Larger ripple; not worth the inversion for one new caller.
- **Defer.** No user-visible bug; only matters for stderr-watching scripts.

**Urgency:** low. Behavioral inconsistency, not a defect.

Touches: `src/dbxignore/cli.py` (`_apply_from_gitignore` — add `log_warnings=False` arg). Optional regression test in `tests/test_cli_apply.py` (assert no stderr WARNING from a conflict-bearing gitignore via `caplog`).

## 68. `dbxignore status --summary` runs the full conflict-walk on every poll

`cli.status` (PR #99) hoisted the rule-cache walk upfront so the human path and the new `--summary` path share the work: `discovered = _discover_roots(); conflicts = _load_cache(discovered).conflicts() if discovered else []`. The `_load_cache` call walks the watched roots via `RuleCache.load_root` → `root.rglob(IGNORE_FILENAME)` to find every `.dropboxignore` file, then re-reads the ones whose `mtime_ns + size` changed (per `rules._load_if_changed`). For status-bar widgets polling `dbxignore status --summary` every few seconds (the documented use case from item #60), this is a full subtree rglob per tick — measurable on a 27k-dir tree (item #53 measured 49.62s for a different walk; conflicts walk is bounded but still proportional). The pre-PR-#99 status command paid this cost too, but only when invoked interactively, not on a polling cadence.

**Fix candidates:**

- **Skip the conflict walk when `--summary` is set; emit `conflicts=?` with a documented caveat.** Drops a public-API field's authoritative value but caps the per-poll cost at O(read state.json). Requires updating README §"Status-bar integration" to note that `conflicts` is only authoritative on the full `dbxignore status` invocation.
- **Cache the conflict count in `state.json` after each daemon sweep.** The daemon already runs `cache.conflicts()` at sweep time (initial sweep + hourly). Persist the count + the timestamp it was computed; `--summary` reads from state without walking. Adds a schema field; `_decode` needs to tolerate missing field for backwards compat.
- **Gate the rebuild via mtime checks against a recent state read.** `--summary` reads state, checks if any `.dropboxignore` mtime is newer than `state.last_sweep`, walks only if drift detected. More complex than option 2 but doesn't change the schema.
- **Defer.** No reported pain; status-bar polling at conservative cadence (30s+) on small Dropbox trees is fine. Wait for a user report.

**Urgency:** low. Speculative perf concern; surfaced by `/simplify` review of PR #99's hoist. Bundle with the next CLI-touching change if a user reports lag, or with a daemon-state-schema change for option 2.

Touches: `src/dbxignore/cli.py` (`status --summary` branch); `src/dbxignore/state.py` (option 2: add `last_conflicts_count` field + decode tolerance); README §"Status-bar integration" (document whichever shape lands).

## 69. No real-pathspec regression test for glob-prefix negations through the post-PR-#108 detector branch

**Surfaced by:** `pr-test-analyzer` review of PR #108 (deferred suggestion, severity 5).

PR #108's detector fix added a new branch in `rules_conflicts._detect_conflicts`: `is_directory_negation = raw.rstrip() == prefix` selects between `_ancestors_of(strict=True)` for directory negations and `_ancestors_of(strict=False)` for broader negations. `tests/test_rules_conflicts.py::test_detect_skips_glob_prefix_negation` covers the `literal_prefix() == None` early-exit for `!**/foo/bar/`, but it uses the `_FakePattern` shim and doesn't exercise the new branch with a real pathspec pattern.

The contract being verified: a glob-prefix negation like `!**/foo/` should still be skipped (no conflict, no false positive) regardless of which strict-ancestor branch the detector enters. Today this is implicit — `literal_prefix()` returns `None` and the function `continue`s before either branch runs — but a future refactor of `literal_prefix()` could quietly start returning a prefix for patterns that begin with a glob, and the strict-ancestor logic would then fire on a wrong prefix. A real-pathspec test in `test_rules_reload_explain.py` (parallel to the post-fix tests already there) would pin the contract end-to-end.

**Fix candidates:**

- **Add a `test_rulecache_glob_prefix_negation_skipped` test** in `test_rules_reload_explain.py`: write `.dropboxignore` with `build/` + `!**/keep/`, assert `cache.conflicts() == []` (negation skipped, not flagged). Repeat with `build/*` + `!**/keep/` for symmetry. ~20 LOC.
- **Defer.** No observed user report; the existing shim test covers the behavior at the unit level.

**Urgency:** low. Polish — the contract holds today; this would lock it down against future refactors of `literal_prefix()` semantics.

Touches: `tests/test_rules_reload_explain.py` (new test).

## 70. `dbxignore explain` lacks verdict-driven exit codes for shell scripting

`dbxignore explain <path>` is the diagnostic counterpart to `git check-ignore -v <path>` — both answer "which rule decides this path's ignored state, and where does it live?" — but the two diverge on shell-scriptability. `git check-ignore` sets exit codes by verdict (`0` = ignored, `1` = not ignored, `128` = fatal), so `if git check-ignore X; then ...` works in scripts. `dbxignore explain` always exits `0` on success, regardless of whether the path is ignored, has only dropped matches, or has no matches at all. Callers must parse stdout text ("no match for X" vs. annotated rules) to extract the verdict, which is awkward for cron / status-bar / pre-commit-style integrations.

The existing primitive `RuleCache.match(path)` already returns the post-drops final verdict (last-match-wins on non-dropped rules), so the implementation can read the verdict directly rather than re-deriving from explain output.

**Fix candidates:**

- **Parity with `git check-ignore`** (preferred): exit `0` if `cache.match(path)` is True, exit `1` otherwise. Single boolean verdict, scriptable via `if dbxignore explain X; then ...`. ~5 LOC in `cli.explain` plus a couple of test cases in `test_cli_status_list_explain.py` covering ignored / not-ignored / no-match paths.
- **Three-way split** that surfaces the dbxignore-specific dropped case: exit `0` = ignored, `1` = not ignored with no dropped negations, `2` = not ignored but had dropped negations (signal: "your rule didn't take effect"). Richer than git's shape but distinguishes "expected not ignored" from "negation was inert here." Slight risk of over-engineering — the dropped case is already signalled in stdout via the `[dropped]` annotation.
- **Defer.** Stdout text is parseable today; no observed scripting demand. Filed for the day someone wants to wire `dbxignore explain` into a pre-commit hook, status-bar widget condition, or cron sanity check.

**Urgency:** low. Polish; preserves CLI consistency with the broader gitignore-tooling ecosystem.

Touches: `src/dbxignore/cli.py` (`explain` exit-code branches); `tests/test_cli_status_list_explain.py` (verdict-driven exit code coverage); README §"Commands" row for `explain` (document exit codes if changed).

## 71. `dbxignore check-ignore` alias for `explain` (git-parity discoverability)

Users coming from git look for the diagnostic-equivalent verb at the name they already know — `git check-ignore -v <path>`. Today they have to discover that dbxignore calls the same operation `explain`. Adding `check-ignore` as an additional name for the existing `explain` command (not a rename) gives those users a faster path in without breaking anyone who already has `explain` in muscle memory or scripts.

The aliasing is additive and reversible. No semantics change; both names invoke the same callback. The output format and exit codes (see #70) are inherited unchanged.

**Fix candidates:**

- **Decorator-based dual registration**: factor `explain`'s implementation into a helper, register two thin command wrappers (`@main.command(name="explain")` and `@main.command(name="check-ignore")`) that both delegate to it. ~10 LOC. Help text on both can cross-reference each other.
- **Click `add_command` with second name**: register `explain_cmd` once via the decorator, then `main.add_command(explain_cmd, name="check-ignore")`. Simpler diff but the help-text de-duplication is implicit — `--help` may show one entry or two depending on click's introspection. Verify before settling.
- **Custom `Group` subclass with alias resolution**: heavier; only worth it if more aliases follow. Filed under YAGNI.
- **Defer.** Discoverability gap is real but mild; users find `explain` via `dbxignore --help` either way.

**Urgency:** low. Polish; gentle nudge toward CLI parity with the broader gitignore-tooling ecosystem (the same nudge that motivates the `.dropboxignore`-uses-gitignore-syntax design choice).

Touches: `src/dbxignore/cli.py` (alias registration); `tests/test_cli_status_list_explain.py` (assert both names work and produce identical output); README §"Commands" row for `explain` (mention the alias).

## 72. README "Command parity with git" subsection

Users coming from git form expectations about what dbxignore's commands do based on the verb. Some align cleanly (`init`, `status`); some are dbxignore-specific (`apply`, `clear`, `daemon`); and some have a deceptively close git counterpart with materially different consequences. The strongest example is `dbxignore clear` — its closest analogue is `git rm --cached`, but the consequences are inverted: git's command removes from index (cheap, local-only), dbxignore's clears markers and triggers Dropbox to upload to cloud (potentially gigabytes, propagates to other devices). A user assuming parallel semantics could trigger an unintended large upload.

A short subsection in README §"Commands" — a small parity table mapping each dbxignore command to its closest git counterpart (or "none"), with a one-line note on intentional divergences — makes the design choices visible. Three benefits: (1) git-fluent users find the right verb faster, (2) users get a heads-up where the semantics diverge despite a similar verb, (3) future contributors have a stable rationale for why some commands match git's names and others don't.

**Fix candidates:**

- **Add the table** as a sub-section under README §"Commands". ~30 lines of markdown. Companion to #71 (the `check-ignore` alias) — the table can document the aliasing and the deliberate non-mappings in one place.
- **Defer.** Current `--help` output suffices for command discovery; this is for design-rationale visibility, not for getting users unstuck.

**Urgency:** low. Pure docs polish. Bundle naturally with #71 (one PR can land both: introduce the alias, document the parity table).

Touches: `README.md` §"Commands" (new sub-section after the command table).

## 73. Local code-review hook (`.claude/settings.json`) over-fires on Bash commands that don't match the `if` filter

**Surfaced 2026-05-05 in PR #111 work; multiple fired triggers in one session.**

The local PreToolUse hook in `.claude/settings.json` is configured with `matcher: "Bash"` and `if: "Bash(gh pr create*)"` — intent: fire only when Claude is about to invoke `gh pr create`, requiring a `code-reviewer` pass + per-HEAD marker file before allowing the call. In practice the hook fires on Bash commands that contain neither `gh pr create` literally nor any obvious near-match.

**Observed firings during PR #111 (all blocked despite no `gh pr create` in the literal command):**
- `for subj in "..."; do printf ...; commit-check ...; done` — pre-flighting commit subjects.
- `git log --pretty=format:'%s' origin/main..HEAD | while IFS= read -r msg; do ...; commit-check ...; done` — same intent, different shell idiom.

**Observed non-firings in the same session** (these passed through cleanly):
- `touch .git/.code-review-passed-<sha>` — single command, no compounding.
- `git log --oneline origin/main..HEAD` — diagnostic.
- `git push --force-with-lease` — no `gh pr` substring.
- `gh pr edit 111 --title "..." --body "$(cat <<EOF ... EOF)"` — confirms `pr edit` is not affected, only `pr create`-adjacent invocations.

The pattern of firings doesn't correlate cleanly with literal `gh pr create` substring presence. Best hypothesis: Claude Code's permission-rule matching has unexpected semantics when both `matcher` and `if` are set on the same hook entry (possibly the `if` becomes advisory / ignored, or matches more loosely than the documented prefix-glob spec). Could also be the harness doing token-shape analysis on commands that include identifiers like `commit` or shell constructs the rule engine treats as "PR-creation-adjacent."

**Fix candidates:**

- **Investigate Claude Code's permission-rule semantics + hook-firing source.** Read the source for how `if` is matched against tool input when `matcher` is also set. Possibly file an upstream issue if the matching is genuinely buggy. This is a research task, not a fix yet.
- **Drop the `if` filter; check inside the script.** Replace the `if: "Bash(gh pr create*)"` field with a script-level guard: `case "$BASH_COMMAND" in *"gh pr create"*) ... ;; *) exit 0 ;; esac`. Loses the early-exit before the hook process spawns, but explicit and predictable. Trade ~1ms-per-Bash for correctness.
- **Accept the over-firing and broaden the contract.** Treat the hook as "every code-modifying Bash needs a per-HEAD review marker," not just `gh pr create`. Higher friction but uniform — and arguably more conservative (catches rogue scripts that bypass `gh pr create`). Document in the hook comment.
- **Defer.** Friction is real but bounded; current workaround is "touch the marker after each new commit." If multi-PR cadences increase the friction, revisit.

**Urgency:** low-medium. Hook works as a safety net; the over-firing is annoying but not blocking. Bundle with the next `.claude/`-touching change.

Touches: `.claude/settings.json` (hook config); investigative — possibly Claude Code source / docs.

## 74. GitHub Actions pinned to mutable tags rather than SHAs

**Surfaced 2026-05-05 in `code-reviewer` review of PR #111.**

Every GitHub Action invocation in `.github/workflows/*.yml` pins to a major-version tag rather than a 40-char commit SHA: `actions/checkout@v4`, `commit-check/commit-check-action@v2.6.0`, `anthropics/claude-code-action@v1`, `pypa/gh-action-pypi-publish@release/v1`. Tags are mutable — the action repository owner can re-point a tag at new code without anyone consenting. SHAs are content-addressed and immutable. For a release-publishing pipeline (`pypa/gh-action-pypi-publish` runs on tag push and uploads to PyPI), the trust boundary is meaningful.

The current convention is consistent (tags everywhere) — switching to SHAs would be a project-wide shift, not a one-off, and would require a maintenance practice for keeping pins current (Dependabot is the standard answer).

**Fix candidates:**

- **SHA-pin every action across all workflows; add a Dependabot config to keep pins current.** Mechanical change — for each `uses: foo/bar@vN` line, look up the SHA the tag points to today and replace. ~30 LOC of YAML edits + ~10 LOC of `.github/dependabot.yml`. Reviewers should sanity-check each SHA against the action's release-notes page. Once landed, contributing-docs should note the SHA-pin convention.
- **SHA-pin only release-critical actions** (`pypa/gh-action-pypi-publish`); leave the rest on tags. Lower mechanical cost, narrower trust boundary improvement. Defensible if PyPI publish is the only consequential trust surface.
- **Defer.** No observed compromise; tag pinning is the project's existing convention and matches most open-source practice. Filed for the day Anthropic, GitHub, or PyPa publishes a security advisory affecting one of the pinned actions.

**Urgency:** low. Speculative security hardening; no observed incident or specific pressure.

Touches: every file in `.github/workflows/` that uses third-party actions (currently `release.yml`, `test.yml`, `commit-check.yml`, plus the new `claude.yml` and `claude-code-review.yml` from PR #111); new `.github/dependabot.yml` for option 1.

## 75. Cross-script Phase 4.5 extraction in manual-test-{ubuntu-vps,macos}.sh

**Surfaced 2026-05-05 in `/simplify` review of PRs #114 + #115 (confidence 45).**

`scripts/manual-test-ubuntu-vps.sh` and `scripts/manual-test-macos.sh` both define `phase_extended_cli()` covering Phase 4.5's eight test cases (4g–4n). Bodies are byte-identical except 4i (file-content-invariant comment is shorter on macOS). ~120 LOC duplicated between the two files. Could extract to `scripts/_phase_extended_cli.sh` and `source` it from both — saves ~120 LOC, keeps the two top-level scripts as thin per-platform wrappers.

The reason this isn't urgent: when the platforms diverge in Phase 4.5 (e.g., a darwin-only `pluginkit` smoke check, or a Linux-only inotify-limit-aware fixture), the shared file becomes awkward — either it grows platform conditionals, or the divergence forces re-duplicating the case in one script. The Windows PowerShell script already can't share (different shell), so the extraction would never reach three-way reuse. With only two scripts sharing, the duplication-vs-conditional tradeoff is roughly even.

**Fix candidates:**

- **Extract Phase 4.5 to `scripts/_phase_extended_cli.sh`** sourced from both bash scripts at the right point in `main`. ~120 LOC saved at the cost of one shared file. Helpers (`assert_grep`, `assert_xattr_*`) need to be defined before the source line; that's already true today.
- **Extract only the helpers into `scripts/_test_helpers.sh`** (less aggressive — extracts `assert_grep`, the color codes, the `pass`/`fail`/`abort` block, etc. without touching test cases). Saves ~30 LOC per script. Lower risk of platform-divergence pain.
- **Defer.** Current duplication is bounded (~120 LOC across two files) and easy to grep across; no observed pain from the duplication itself.

**Urgency:** low. Polish surfaced from `/simplify` review; defer until either (a) a third bash script lands (e.g., a FreeBSD/BSD variant) raising the duplication-cost ratio, or (b) the manual-test scripts become a more-frequently-edited surface where the duplication starts costing per-PR effort.

Touches: `scripts/manual-test-ubuntu-vps.sh`, `scripts/manual-test-macos.sh`, new `scripts/_phase_extended_cli.sh` (option 1) or `scripts/_test_helpers.sh` (option 2).

## 76. Conflict detector skips negations whose pattern starts with a glob

**Surfaced 2026-05-05 in code review of the daemon classification path.**

`literal_prefix()` in `rules_conflicts.py` returns `None` for any pattern whose first segment contains a glob metacharacter (`**/foo/bar/`, `foo*/bar/`, `[ab]/c/`). `_detect_conflicts` skips such negations at the `prefix is None` early-exit, and `RuleCache.match()` only suppresses entries listed in `_dropped`. So a sequence like

```
**/foo/
!**/foo/bar/
```

reports `a/foo/bar` as not-ignored (last-match-wins on the negation), even though Dropbox's directory inheritance from the marked `a/foo/` makes the negation inert on disk regardless. `dbxignore status` shows zero conflicts; `explain` returns the negation without the `[dropped]` annotation.

The on-disk marker behavior is correct — `reconcile._reconcile_path` evaluates `match()` per file, and every file under `a/foo/` inherits the include verdict from the matched ancestor. The bug surface is the diagnostic layer: anything that introspects rule semantics through `match()` / `explain()` (status output, third-party tooling, the conflict warning log) is misled. Pinned today as a documented limitation by `tests/test_rules_conflicts.py::test_detect_skips_glob_prefix_negation`.

**Fix candidates:**

- **Conservative drop.** Treat any negation whose `literal_prefix()` returns `None` as inert when an earlier rule in the same root could mark a `**`-reachable directory. Mirrors the "inheritance is inescapable" stance already encoded for literal-prefix patterns. Flips the existing test pin from documenting a limitation to verifying the fix; cheapest and matches the detector's existing posture.
- **Targeted detection.** For each glob-prefix negation, walk the filesystem under the relevant ancestor and run earlier includes against discovered candidate ancestors. Accurate but turns a static analysis into an I/O-bound one.
- **Warn-only.** Keep current `match()` behavior, but log a WARNING and surface in `dbxignore status` whenever a glob-prefix negation is present. Cheapest possible; punts the inert-or-not call to the user.

**Urgency:** medium-low. Real rule sets do exhibit this — `**/foo/` patterns are idiomatic for "anywhere in the tree" — but the user-facing impact is constrained to diagnostic output, not marker correctness. Worth fixing the next time the rules-conflict layer is touched.

Touches: `src/dbxignore/rules_conflicts.py` (`literal_prefix`, `_detect_conflicts`); possibly `src/dbxignore/rules.py` (only if conservative-drop needs additional cache state); `tests/test_rules_conflicts.py::test_detect_skips_glob_prefix_negation` (flip the assertion or rewrite as a fix-verification test).

## 77. Debouncer key disambiguation relies on string prefixing rather than a structured shape

**Surfaced 2026-05-05 in Codex review of PR #120 (P2 finding) and the follow-up fix in commit `0c8a748`.**

`Debouncer._pending` is keyed on `(EventKind, key: str)`. `_classify` currently produces three distinct semantic key shapes for `EventKind.RULES`:

- single-path events (created / modified / deleted on a `.dropboxignore`): `str(src).lower()`
- moved events with src=rule (move-out): `str(src).lower()` — same shape as above
- moved events with dest=rule, src=non-rule (move-into / atomic save): `f"moved-into:{str(dest_path).lower()}"`

PR #120 added the third shape and prefixed it with `moved-into:` specifically to prevent collision with the second shape (Codex's exact concern). The remaining cross-shape collision potential is between the first two: a move-out `A/.dropboxignore` -> `B/...` and a created/modified event for `A/.dropboxignore` within the 100ms RULES debounce window both key on `str(A/.dropboxignore).lower()`. The Debouncer's last-wins overwrite then drops one event's dispatch. In practice this needs a scripted sequence (`mv A/.dropboxignore B/ && touch A/.dropboxignore`) — editor save patterns don't naturally produce it — so it's rare, but it is a real correctness gap.

The deeper issue is the keying model itself: stringly-typed disambiguation doesn't scale. Each new dispatch path needing distinct semantics requires another prefix and another cross-branch collision audit. The next refactor that introduces a fourth key shape will face the same review-driven catch-up that PR #120 went through.

**Fix candidates:**

- **Structured tuple key.** Change Debouncer's key type to `tuple[str, ...]` with a leading `role` discriminator: `("single", path)`, `("moved-out", path)`, `("moved-into", path)`. Type-checker enforces the shape; no string-prefix encoding; new dispatch paths add a new role token rather than picking a non-colliding string. Touches `Debouncer.submit` / `_pending` types and every `_classify` return; the `_key` discard at `daemon._dispatch` line 100 is unaffected since the dispatch never inspects the key. ~30 LOC plus test updates.
- **Per-role debounce queue.** Split `_pending` into separate dicts indexed by role (each keyed only on path). Mechanically equivalent to the tuple shape but spreads state across more fields.
- **Status quo + audit comment.** Keep the prefix scheme; add an inline comment near `_classify` enumerating every key shape and which cross-shape collisions are acceptable vs. shielded. Cheapest — documents the design weakness without fixing it. The remaining first-vs-second-shape collision still ships.

Candidate 1 is the most direct fit for the failure mode: the bug surfaces because two distinct semantic operations produce the same string by accident, and tuples naturally prevent that. The migration is bounded — the Debouncer's key type is the only public-ish surface, and its sole external producer is `_classify`.

**Urgency:** low. Rare in practice, no observed regression, current code's `moved-into:` prefix addresses the only collision a reviewer flagged. Worth picking up the next time the debounce/classify layer needs another dispatch path or another keying concern surfaces.

Touches: `src/dbxignore/debounce.py` (`Debouncer._pending` type, `submit` signature, `_run` emit signature); `src/dbxignore/daemon.py` (`_classify` return shape, possibly `_WatchdogHandler.on_any_event` if it inspects the key — currently it doesn't); `tests/test_daemon_dispatch.py` (the four key-equality tests would switch from string comparisons to tuple comparisons).

## 78. Daemon singleton check is not atomic between read and first state write

**Surfaced 2026-05-06 in an external code review.**

`daemon.run()` reads `state.json` (line ~260), checks `_is_other_live_daemon(prior.daemon_pid)`, and only writes its own state after the first sweep finishes. Two concurrent daemon launches in that window can both pass the check and start watchers + sweeps. Service managers (Task Scheduler, systemd user unit, launchd LaunchAgent) prevent double-start in the typical install, so the at-risk path is manual `dbxignored` invocation while the installed daemon is also running — uncommon but possible during dev or migration.

**Fix candidates:**

- **OS lock file via `fcntl.flock` / `msvcrt.locking`.** Acquire a non-blocking exclusive lock on a path under `state.user_state_dir()` immediately after `_configured_logging()` enters and before root discovery. Hold for the daemon's lifetime; release on `stop_event`. Real bug-class fix — atomic across processes.
- **Atomic exclusive-create on the lock path.** `open(lockfile, "x")` fails if it exists. Cheaper than fcntl but doesn't release on crashed-daemon SIGKILL — would need an mtime + PID-liveness check on stale locks (effectively re-introducing the race).
- **Status quo.** Document the limitation and rely on service managers.

Candidate 1 is the right shape. The lock file path can live next to `state.json`. Test: spawn two `run()` instances against the same state dir, assert one returns early, the other holds the lock.

**Urgency:** medium. Real but mitigated by service managers in the typical install. Worth doing the next time daemon singleton-check code is touched.

Touches: `src/dbxignore/daemon.py` (`run()` startup), `src/dbxignore/state.py` (lock-path helper alongside `default_path()`), `tests/test_daemon_singleton.py` (race assertion).

## 79. PID stale-detection treats any Python process as a dbxignore daemon

**Surfaced 2026-05-06 in an external code review.**

`state.is_daemon_alive(pid)` matches process names containing `"python"` or `"dbxignored"`. If the recorded PID gets recycled by an unrelated Python process (another user-space tool, a shell-side `python -c`), the check returns True — daemon startup refuses with "another daemon is running", and `dbxignore clear` refuses with the same guard. The user sees a phantom-daemon scenario.

The current `python` substring guard is documented in `CLAUDE.md`'s `state.is_daemon_alive` section as a deliberate trade: better to be cautious than to allow a recycled PID claimed by an unrelated process to register as alive in the OPPOSITE direction (the original v0.1 bug). The reviewer's "ideally persist process create time in state" is the proper fix.

**Fix candidates:**

- **Persist `daemon_started_pid_create_time` in state.** Capture `psutil.Process(os.getpid()).create_time()` at daemon startup, store alongside `daemon_pid`. Liveness check: PID exists AND its current `create_time()` matches the stored value. Defeats PID reuse cleanly; one extra `psutil` field on `State`.
- **Inspect `proc.cmdline()` / `proc.exe()` for `dbxignore daemon` / `python -m dbxignore daemon` / `dbxignored`.** Tighter than substring on `name`, but still approximate — a user running `python -m dbxignore daemon` from a venv would match. Doesn't solve PID reuse for OUR own command form.
- **Both.** Persist create-time as the authoritative match; keep the cmdline inspection as a fallback if create-time is unavailable.

Candidate 1 is the bug-class fix.

**Urgency:** low. Practical impact: rare. The current cautious-bias means the false positive blocks a daemon-or-clear operation rather than corrupting state.

Touches: `src/dbxignore/state.py` (`State` dataclass, `_encode`/`_decode`, `is_daemon_alive`), `src/dbxignore/daemon.py` (capture create-time on `run()` startup), `tests/test_state.py` + `tests/test_daemon_singleton.py` (recycled-PID-with-different-create-time returns False).

## 80. `_build_entries` drops indented `#` patterns despite pathspec accepting them

**Surfaced 2026-05-06 in an external code review.**

`rules._build_entries` filters out lines via `s := raw.strip(); not s.startswith("#")` — which classifies `"   #foo"` (whitespace before `#`) as a comment and drops it. But pathspec's gitignore semantics treat such lines as active patterns. The CLAUDE.md gotcha bullet for this case claims "the count-mismatch fallback handles it" — but the fallback at the bottom of `_build_entries` re-iterates `active_line_indices`, which already excludes the indented-`#` line, so the pattern is silently dropped in the fallback path too.

User-facing impact: a `.dropboxignore` line like `   #literal_filename` is silently inert — pathspec parsed it as a literal pattern, but the cache filter dropped it. Rare in practice since most users don't write indented `#` lines deliberately, but possible if a YAML/JSON-style indented rule list gets pasted.

**Fix candidates:**

- **Mirror gitignore comment semantics exactly.** A line is a comment iff it begins with `#` (no leading whitespace). Adjust the filter to `not raw.startswith("#")` (or after just-leading-whitespace trim, depending on what gitignore actually does — verify with pathspec's source). The count-mismatch fallback's premise (use active_line_indices) needs the same correction.
- **Drop the count-mismatch heuristic entirely** and always use per-line reparse. Simpler but slower for the common case where the bulk parse and per-line parse agree.

Candidate 1 is the lower-impact fix.

**Urgency:** low. Edge case in practice; the misleading CLAUDE.md note is the more harmful artifact (it convinced a recent reviewer to skip checking this case). Worth fixing when the rules layer is next touched, alongside a CLAUDE.md correction.

Touches: `src/dbxignore/rules.py` (`_build_entries`), `tests/test_rules_basic.py` or new file (fixture: `.dropboxignore` with a `   #foo` line that should match a literal `   #foo` path), `CLAUDE.md` (Gotcha bullet correction).

## 81. Write-side marker OSError narrow arm too brittle for transient EIO

**Surfaced 2026-05-06 in an external code review.**

`reconcile._reconcile_path` catches a broad `OSError` on the read side (item #21 — covers `ENOTSUP`/`EOPNOTSUPP` from xattr backends and unexpected I/O like `EIO` on flaky network drives) but keeps a narrow `errno.ENOTSUP|EOPNOTSUPP` arm on the write side. This is documented as an asymmetric-by-design choice in CLAUDE.md's Architecture section: write failures are exceptional and should bubble up.

The reviewer's argument: on real-world filesystems (network drives, USB sticks, full disks, corrupted xattr storage), a transient `EIO` or quota error on `set_ignored` will kill the entire dispatch or sweep — not just the one path. A persistent network blip means the daemon keeps crashing until the network settles. The read-side already counts these into `Report.errors` and continues; the write-side could do the same.

**Fix candidates:**

- **Symmetric error-arm widening.** Catch broad `OSError` on the write side, log + count into `Report.errors`, return `currently_ignored` (the existing pattern from item #41). The write arm would still propagate non-OSError exceptions (real bugs).
- **Status quo + better error messaging.** Keep the narrow arm but improve the user-facing log when an unexpected write OSError reaches the daemon's top-level handler — currently surfaces as a stack trace in `daemon.log`.

Candidate 1 changes the documented asymmetric-by-design invariant in CLAUDE.md, so it's a real architectural decision rather than a mechanical fix. Worth a brief discussion of which class of error is more harmful: a sweep that silently swallows a real backend bug (the current concern motivating the narrow arm), vs. a daemon that dies on transient FS unreliability (the reviewer's concern).

**Urgency:** medium. No user reports yet, but the failure mode (full network-drive Dropbox sweep killed by a single transient EIO) is real and would be hard to debug without log spelunking.

Touches: `src/dbxignore/reconcile.py` (`_reconcile_path` write arm), `CLAUDE.md` (Architecture section asymmetric-error-arms paragraph), `tests/test_reconcile_enotsup.py` (parameterize over a wider error set; assert continued sweep + Report.errors entry).

## 82. systemd unit ExecStart does not escape executable path with whitespace

**Surfaced 2026-05-06 in an external code review.**

`install/linux_systemd.py` writes `ExecStart={exe_path.as_posix()} {arguments}` with raw f-string interpolation. systemd parses ExecStart by splitting on whitespace; an executable path containing a space (e.g. `/home/user/My Tools/dbxignored`) would be split into multiple tokens, breaking the unit. Same issue for special characters that systemd treats as escapes (`\`, `"`, `'`, `$`).

systemd's official escape rule: enclose paths with whitespace in double quotes, escape internal `"` and `\` with backslashes. Companion to the existing BACKLOG #44 (Windows Task XML escaping) and the `windows_task.py` getuser interpolation surfaced in the same review.

**Fix candidates:**

- **Quote + escape.** `ExecStart="{exe_path.as_posix().replace('\\', '\\\\').replace('"', '\\"')}" {arguments}`. Ugly but correct.
- **Validate at install time.** If the exe_path contains characters systemd can't represent in a single-token ExecStart, raise `RuntimeError` with an actionable message ("relocate dbxignored or run from a path without spaces").
- **Both.** Quote in the common case; refuse on the rare unrepresentable case.

**Urgency:** low. CI runners and standard distro installs land dbxignored in PATH-friendly directories. A user installing into `~/My Tools/` is unusual.

Touches: `src/dbxignore/install/linux_systemd.py` (ExecStart construction), `tests/test_linux_systemd.py` (path with whitespace produces a unit file systemd would parse correctly — round-trip via the systemd parser if we add a test dep, otherwise structural assertion on the rendered unit text).

---

## Status

### Open

Thirty-four items. Thirty-two are passive (no concrete trigger requires action); item #34 is a recurrence of an already-resolved flake (item #18); item #73 had multiple fired triggers in one session (the local PR-review hook over-fired on Bash commands that didn't match its declared `if` filter — friction not blocking). Item #34's third recurrence fired 2026-05-04 during PR #95 pre-flight; widening 5.0s → 7.0s → 10.0s all failed under full-suite load (different polls exhausted on each run), so the suggested band-aid fix shape was abandoned and #34 stays open pending root-cause diagnosis (the test passes in 0.27s in isolation but >7s in the full suite, so the cause lives in test-order interaction with an earlier test).

- **#14** — Flaky `test_run_refuses_when_another_pid_is_alive`. Single observation 2026-04-24 during PR #22 pre-flight (passed on rerun and in isolation). Awaits 2nd observation; per project flake-handling policy, fix only after recurrence.
- **#26** — `install._common.detect_invocation` has an unreachable `RuntimeError` branch (preexisting from `linux_systemd._detect_invocation`, faithfully extracted in PR #57). Doc-vs-code inconsistency, no production hit. Fix when next touching the install layer.
- **#27** — Intel Mac (x86_64) Mach-O binary build leg. v0.4 ships arm64-only; Intel users install via PyPI. Awaits demand signal.
- **#28** — Universal2 macOS binary as the single artifact. Quality-of-life cleanup; mutually exclusive with #27. Defer until item #27 actually triggers.
- **#29** — Codesigning + notarization for macOS binaries. Smooths Gatekeeper UX but requires $99/yr Apple Developer membership. Awaits concrete pain signal.
- **#30** — Windows-aware single binary via `AttachConsole(ATTACH_PARENT_PROCESS)`. Collapses `dbxignore.exe` + `dbxignored.exe` to one. Three-context UX tradeoff (terminal / Task Scheduler / double-click) is load-bearing today; ctypes path is the implementation route. Awaits binary-size or build-time pain signal.
- **#34** — `test_daemon_reacts_to_dropboxignore_and_directory_creation` flaked again 2026-05-01 in PR #74's post-rebase Windows leg, post-PR #40 timeout fix (item #18). Reran the failed leg, second run passed in 27s. Per project policy, single post-resolution recurrence is logged but not actioned; third recurrence triggers either further timeout widening or actual root-cause diagnosis. Fourth observation 2026-05-07 in PR #124 — full body of #34 updated with the recurrence-vs-PR-changes diagnosis (PR #124 doesn't touch the daemon dispatch path).
- **#38** — info.json parsing duplicated between `roots.py` and `_backends/macos_xattr.py`. Both modules read `~/.dropbox/info.json` and extract per-account `path` fields with subtly different shapes. Real refactor candidate (~30 lines deduplicated) but the semantic differences (DBXIGNORE_ROOT override, account-type strictness) are intentional. Bundle with the next info.json-touching change.
- **#39** — `_pluginkit_extension_state()` returns stringly-typed state (`"allowed"`/`"disabled"`/`"not_registered"`/`"unknown"`). Cheap fix is a `Literal[...]` return annotation so type-checkers catch caller-side typos. Single callsite limits blast radius; bundle with a future macos-backend-touching change.
- **#40** — Dual `paths` for-loops in `_detected_attr_name()` could share a `_first_match` helper. Reviewers disagreed: one proposed extraction, another argued the dual structure correctly documents priority semantics. Filed for the design-tension record; current shape is defensible. Awaits a third predicate (rule-of-three trigger).
- **#42** — `_timeouts_from_env` crashes the daemon if a debounce env var is non-integer (no try/except, no fallback). In-module precedent (`DBXIGNORE_LOG_LEVEL` validation) makes the inconsistency stand out. User-error-triggered; bundle with next daemon-logging touch.
- **#44** — `build_task_xml` interpolates `getpass.getuser()` and `exe_path` into XML without `xml.sax.saxutils.escape`. Hits users with `&` in install path; manifests as confusing `schtasks` error. Bundle with next install-layer touch. Re-surfaced 2026-05-06 in an external code review (companion item #82 added for the systemd ExecStart sibling issue).
- **#50** — `windows_task.detect_invocation` partially overlaps `_common.detect_invocation` but diverges in the non-frozen branch (Windows uses `pythonw.exe` for windowless Task Scheduler launch and skips `shutil.which("dbxignored")` PATH lookup). Companion to item #26. Bundle with next install-layer touch.
- **#51** — `install/__init__.py` platform dispatch duplicated across `install_service`/`uninstall_service`. Filed for the design-tension record (precedent: #40); current 6-block shape is defensible vs a factored-out helper that would introduce stringly-typed action coupling.
- **#53** — `_sweep_once` walks every directory regardless of marker state — measured 49.62s on a 27k-dir tree. Skip-on-(marker-present + match-still-positive) collapses descent into already-ignored subtrees; rule-mutation events already force a re-walk, so the steady-state invariant holds. ~50 LOC. Bundle with the next daemon-touching change.
- **#54** — Watchdog observer's recursive watch schedules one inotify watch per directory under `~/Dropbox`, including marked-ignored subtrees. Architectural fix (per-directory watches with mark/unmark lifecycle) is ~200 LOC of race-condition-prone state-machine work; deferred until a beta tester hits the watch ceiling on a system with limits already raised.
- **#65** — Windows Explorer right-click context-menu integration. Optional install arm (`dbxignore install --shell-integration`) writes per-user registry keys under `HKEY_CURRENT_USER\Software\Classes\Directory\shell\…\command`, invoking `dbxignore.exe ignore "%1"`. `AppliesTo` filter scoped to discovered Dropbox roots from `roots.discover()`. Routes through `_backends/windows_ads.py` so `\\?\` long-path correctness comes for free. ~150 LOC + Windows-only tests + symmetric uninstall.
- **#66** — `dbxignore generate` skips its out-of-root warning when `_discover_roots()` returns `[]`. The `if discovered and ...` short-circuit defeats the warning's "your file won't be observed" purpose precisely when no roots exist at all. One-line guard fix.
- **#67** — `apply --from-gitignore` does not pass `log_warnings=False` to `RuleCache.load_external`, so conflict WARNINGs land on stderr — inconsistent with the regular `apply` path that routes through `_load_cache` (PR #92's `a6fb74b` extracted that helper specifically to suppress per-mutation conflict WARNINGs). One-line fix.
- **#68** — `dbxignore status --summary` runs the full `_load_cache(discovered).conflicts()` walk every poll. Status-bar widgets polling at high cadence pay an rglob over `.dropboxignore` files per tick. Three fix candidates filed in the body (skip conflict walk in summary mode / cache count in state.json / mtime-gated rebuild). Surfaced by `/simplify` review of PR #99's hoist, no user report yet.
- **#69** — No real-pathspec regression test for glob-prefix negations through the post-PR-#108 detector branch. `tests/test_rules_conflicts.py::test_detect_skips_glob_prefix_negation` covers the `literal_prefix() == None` early-exit via the shim, but doesn't pin that the new `is_directory_negation` / strict-ancestor branch is correctly bypassed for `!**/foo/`. Defensive lock-down against future `literal_prefix()` refactors. Surfaced by `pr-test-analyzer` review of PR #108.
- **#70** — `dbxignore explain` always exits `0` regardless of verdict, so shell scripts can't branch on "is X ignored?" the way they can with `git check-ignore -v` (`0`/`1`/`128`). Stdout text is parseable today but awkward for cron / status-bar / pre-commit integrations. Body offers parity-with-git or a three-way split surfacing the dropped-negation case. Surfaced 2026-05-05 in conversation comparing the two diagnostic CLIs.
- **#71** — `dbxignore check-ignore` alias for `explain`. Additive (no rename); gives git-fluent users the verb they expect without breaking existing `explain` callers. Click supports dual registration via decorator + `add_command`. Surfaced 2026-05-05 in a CLI-naming discussion. Bundles naturally with #72.
- **#72** — README §"Command parity with git" subsection mapping each dbxignore command to its closest git counterpart (or "none"), with notes on deliberate non-mappings. Most consequential gap to call out: `dbxignore clear` is *not* `git rm --cached`-shaped — clearing markers triggers Dropbox to upload to cloud. Surfaced 2026-05-05 alongside #71. One PR can land both.
- **#73** — Local code-review hook in `.claude/settings.json` over-fires: declared `if: "Bash(gh pr create*)"` filter is matching Bash commands that contain neither `gh pr create` literally nor any obvious near-match. Multiple fired triggers in PR #111 work (commit-check pre-flight loops blocked; simple `touch` / `git push` / `gh pr edit` passed through). Friction is real but bounded. Body lists empirical observations and three fix candidates (investigate Click semantics; replace `if` with in-script guard; broaden the contract). Surfaced 2026-05-05.
- **#74** — GitHub Actions pinned to mutable major-version tags (`@v4`, `@v1`, `@v2.6.0`, `@release/v1`) rather than 40-char SHAs across all workflow files. Speculative security hardening — switching to SHAs would be a project-wide convention shift requiring a Dependabot maintenance practice. Surfaced 2026-05-05 in `code-reviewer` review of PR #111. No observed incident or specific pressure.
- **#75** — `phase_extended_cli()` body byte-identical between `manual-test-{ubuntu-vps,macos}.sh` (~120 LOC duplicated). Could extract to `scripts/_phase_extended_cli.sh` and `source` from both. Trade-off is duplication-vs-platform-conditional balance; only two scripts share (Windows is PowerShell). Surfaced 2026-05-05 in `/simplify` review of PRs #114 + #115.
- **#76** — Conflict detector skips negations whose pattern starts with a glob (`**/foo/bar/`, `foo*/bar/`); `RuleCache.match()` then reports such paths as not-ignored even though Dropbox inheritance makes them ignored on disk. Marker behavior is correct (reconcile evaluates per-file `match()`); the bug surface is `status` / `explain` diagnostics. Three fix candidates filed in the body (conservative drop / targeted detection / warn-only). Surfaced 2026-05-05 in code review of the daemon classification path.
- **#77** — Debouncer key disambiguation in `_classify` relies on string prefixing (`moved-into:` added in PR #120) rather than a structured tuple shape. The remaining first-vs-second-shape collision (move-out + created/modified on the same `.dropboxignore` within 100ms) still ships. Three fix candidates: structured tuple key (preferred), per-role queues, status-quo-plus-audit-comment. Surfaced 2026-05-05 in Codex review of PR #120.
- **#78** — `daemon.run()` reads `state.json` for the singleton check, then writes its own state only after the first sweep — non-atomic between read and first write. Two concurrent launches in that window can both proceed. Service managers mitigate; the at-risk path is manual `dbxignored` invocation. Fix shape: OS lock file via `fcntl.flock` / `msvcrt.locking` held for daemon lifetime. Surfaced 2026-05-06 in an external code review.
- **#79** — `state.is_daemon_alive` matches `python` / `dbxignored` substrings on process name; a recycled PID claimed by an unrelated Python process registers as alive, blocking daemon-or-clear operations. The cautious-bias is documented in CLAUDE.md as deliberate (preferred over the OPPOSITE direction's recycled-PID-claimed-by-non-Python-process false negative). Proper fix: persist process create-time alongside `daemon_pid` in `state.json`. Surfaced 2026-05-06 in an external code review.
- **#80** — `rules._build_entries` drops indented-`#` lines (`"   #foo"`) as comments, but pathspec accepts them as active patterns. The CLAUDE.md gotcha bullet claims the count-mismatch fallback handles this — verified misleading: the fallback re-iterates `active_line_indices` which already excludes the indented-`#` line. Rare in practice; user impact is silently inert rules. Fix: align comment-detection with gitignore semantics (only strip leading `\t`, not arbitrary whitespace, before checking for `#`); correct the CLAUDE.md note. Surfaced 2026-05-06 in an external code review.
- **#81** — `reconcile._reconcile_path`'s write arm catches only `errno.ENOTSUP|EOPNOTSUPP`; broader `OSError` propagates and can kill a daemon dispatch or sweep. The asymmetric arms are documented as deliberate in CLAUDE.md's Architecture section, but a transient `EIO` on a network-drive Dropbox tree would crash the sweep where the read arm logs+continues. Fix candidates: widen the write arm to the same broad `OSError` shape (revising the CLAUDE.md asymmetry rationale), or improve top-level error logging. Surfaced 2026-05-06 in an external code review.
- **#82** — `install/linux_systemd.py` writes `ExecStart={exe_path.as_posix()} {arguments}` with raw f-string interpolation. An executable path with whitespace breaks the unit (systemd splits on whitespace). Companion to the existing #44 (Windows Task XML escaping). Fix: emit a quoted+escaped ExecStart, or validate-and-refuse unrepresentable paths. Surfaced 2026-05-06 in an external code review.

### Resolved (reverse chronological)

#### 2026-05-07

- **#52** in PR #125 — `daemon._start_observer_or_exit` traps `OSError(ENOSPC)` and `OSError(EMFILE)` from `Observer.start()`; logs ERROR with the matching sysctl runbook (`fs.inotify.max_user_watches=524288` / `max_user_instances=1024`) and `sys.exit(75)` so systemd marks the unit `failed`. Hoists `observer.start()` out of the inner `try/finally` to avoid `Observer.stop()`/`join()` on a never-started Thread when `SystemExit` fires. Three unit tests in `tests/test_daemon_inotify_enospc.py` pin the ENOSPC trap, the EMFILE trap, and the unknown-errno propagation contract. README `## Install (Linux)` gained a `### Linux daemon prerequisites` subsection. Surfaced 2026-05-03 by a VPS tester on a default-`max_user_watches=8192` kernel.

#### 2026-05-04

- **#64** in PR #103 — implements `dbxignore apply --dry-run` (the body's premise that `apply --dry-run` already existed was wrong — framing correction). Threads `dry_run: bool` keyword-only through `reconcile_subtree` → `_reconcile_path`; when set, marker mutations are skipped and would-be paths land in new `Report.would_mark` / `Report.would_clear` lists. CLI emits per-path `would mark: <p>` / `would clear: <p>` lines (sorted for determinism) plus an `apply --dry-run: would_mark=N would_clear=N errors=N (no changes made)` summary; works with `--from-gitignore`. Daemon callers continue to use `dry_run=False` (the keyword-only default), so steady-state hourly sweeps pay zero cost from the new lists. **`daemon --dry-run` declined** as low-value polish: the hourly sweep tick won't fire in any reasonable preview window, watchdog events require the user to actively poke the tree (at which point they could just `apply --dry-run` after), and the only deterministic part is the initial sweep — which `apply --dry-run` covers identically. 6 new tests in `tests/test_cli_apply.py` cover does-not-mutate, would-mark/would-clear lines, summary token, --from-gitignore combination, and dry-run-then-real-apply state-cleanliness. README apply command row updated.
- **#63** in PR #102 — `dbxignore init [PATH]` scaffolds a starter `.dropboxignore` from a packaged template (`src/dbxignore/templates/default.dropboxignore`) covering Node.js / Python / Rust / JVM / .NET / frontend frameworks / build outputs / OS detritus — ~40 patterns, ecosystem-categorized with section headers. Walks the target tree to depth 3 looking for known marker-bait dirs (`node_modules`, `__pycache__`, `.venv`, `target`, etc., 23-name detection set) and prepends a `# Detected in this tree at depth <= 3: ...` annotation to the output. Two scope choices documented in the inline RESOLVED marker: (1) Strategy 1 emission — full template always written, detection is informational only (file patterns like `*.pyc` can't be detection-driven, future-proofs freshly-cloned repos, edit-down UX); (2) comprehensive ecosystem template, not the body's 8-pattern sketch (sourced from a real-world `example.dropboxignore` the user maintained). Template loaded via `importlib.resources` — hatchling packages it automatically under the existing `packages = ["src/dbxignore"]`. `--force` overwrites existing files; `--stdout` previews without writing. 12 new tests in `tests/test_cli_init.py` cover the template-load, detection-at-depth-3, no-descent-into-matched-dir, header-detected-vs-empty, force-overwrite, stdout-no-write, default-cwd, not-a-directory, and helper-format paths. README §"First-time setup" added.
- **#62** in PR #101 — closed without code change as a model-mismatch framing correction. The source idea fit a stateless pattern-driven shell script where `--recent` was the entire scoping architecture; dbxignore is stateful and rule-driven (persistent .dropboxignore + watchdog real-time observer + hourly recovery sweep). The hourly sweep being full-tree is load-bearing — its job is exactly to catch events the watchdog dropped, which by definition includes events older than any `--recent N` horizon. On the user-facing `apply` side, rule-edit re-applications need to evaluate the whole tree against new rules. Item #53's marker-present-and-match-still-positive subtree pruning remains the architecture-aligned perf direction; #62 retired as wrong-fit. Same shape as #59's framing-correction precedent.
- **#61** in PR #100 — `dbxignore clear [PATH]` walks the watched roots (or a scoped subtree) and clears every ignore marker, the inverse of `apply`. Leaves `.dropboxignore` rule files and `state.json` untouched. Three safety knobs not anticipated in the body: (1) **daemon-alive guard** — refuses to run when `state.is_daemon_alive(s.daemon_pid)` because the daemon's next sweep would silently re-apply rule-driven markers; `--force` overrides; (2) **confirmation prompt** — Dropbox starts syncing previously-ignored paths immediately after the clear (potentially gigabytes for a `node_modules`-shaped marker); `--yes` skips for scripted use; (3) **--dry-run** preview matching the body's suggestion. The walk reuses `list_ignored`'s pruning shape (don't descend into marked subtrees) extracted as a new `_walk_marked_paths(target)` helper in cli.py. 10 new tests in `tests/test_cli_clear.py` cover the happy path, daemon-alive refusal + force override, dry-run preview, path-arg scoping, out-of-root error, no-markers message, and both confirmation-prompt branches. README §"Clearing all markers" added with usage examples.
- **#60** in PR #99 — `dbxignore status --summary` emits a stable single-line summary on stdout: `state=<token> [pid=N] marked=N cleared=N errors=N conflicts=N` with state tokens `running` / `not_running` / `no_state`. Two framing decisions in the inline RESOLVED marker (stdout vs. stderr; key=value uniform vs. count-noun mix). `_format_summary(state_obj, alive, conflicts_count)` extracted as the testable seam; 6 new tests in `tests/test_cli_status_list_explain.py` cover the helper's branches and the end-to-end flag (CliRunner: single-line emission, no_state behavior). README §"Status-bar integration" added documenting the format as part of the public API per SemVer (additions non-breaking; renames/removals bump version).
- **#59** in PR #98 — framing correction: the headline ("daemon liveness in `status`") was already implemented in v0.3.0 (commit 604ff07). The actual remaining gap was the bare-PID-existence check in `cli._process_is_alive` missing the process-name guard the daemon's `_is_other_live_daemon` already had (PID reuse → false positive "running"). Fixed by extracting a shared `state.is_daemon_alive(pid)` helper that both consumers now use; CLI's `_process_is_alive` removed entirely, daemon's wrapper reduced to the singleton-check-specific `pid == os.getpid()` self-exclusion + delegate. The "not running" status branch now reads `daemon: not running (last pid=X — state.json may be stale)` instead of just `daemon: not running (pid=X)`, distinguishing a cleanly-stopped daemon from stale state. 6 new unit tests in `tests/test_state.py` cover the new helper's None-pid / dead-pid / recycled-pid / python-process / dbxignored-process / psutil-error branches; the existing daemon-singleton parametrized test continues to cover the wrapper end-to-end.
- **#37 + #58** in PR #97 — macOS xattr backend refactored: `_detected_attr_name() -> str` (single name) replaced by `_detect() -> tuple[list[str], str]` returning `(attr_names, summary)`, with helpers `_detected_attr_names() -> list[str]` for the iterating callers and `detection_summary() -> str` for the human-readable `<mode>: <reason>` line. **#58:** detection now returns `[ATTR_LEGACY, ATTR_FILEPROVIDER]` when pluginkit is unavailable AND no info.json path is decisive, replacing the prior silent-fall-through-to-legacy default; `set_ignored` writes both, `is_ignored` short-circuits True on first non-empty, `clear_ignored` removes both with per-attribute ENOATTR no-op. **#37:** the daemon logs `sync mode detection: ...` at INFO at startup, and `dbxignore status` echoes a `sync mode: ...` line on darwin (Windows/Linux return `None` from the new `markers.detection_summary()` facade). 7 new tests in `tests/test_macos_xattr_unit.py` covering the dual-attr write/read/clear matrix and the summary string shape; 2 existing "unknown→legacy" tests renamed and updated to assert the new dual-attr return. CLAUDE.md macOS section updated. Companion `scripts/manual-test-macos.sh` (zsh-compatible bash) ships in the same PR for end-to-end validation against a beta tester's live Dropbox install.
- **#57** in PR #96 — `_WatchdogHandler.on_any_event` fast-paths `DIR_CREATE` events whose path already matches a cached rule by calling `reconcile_subtree` synchronously and skipping `Debouncer.submit`. Narrows the race window where Dropbox sees the freshly-created directory and starts ingesting children before its marker lands; even at the default `DEFAULT_TIMEOUTS_MS[DIR_CREATE]=0`, the queue + worker-thread context-switch had measurable overhead, and users overriding `DBXIGNORE_DEBOUNCE_DIRS_MS` to nonzero see the full debounce delay. Other event kinds and unmatched DIR_CREATEs still debounce. Trade-off documented in the body and in CLAUDE.md's daemon section: a queued-but-unprocessed RULES event that would invalidate the match means the bypass marks a path the next `reconcile_subtree` (driven by that RULES event) clears — bounded transient false-positive. `_WatchdogHandler.__init__` grew a `cache: RuleCache` parameter; `run()` updated to pass it. Three new tests in `tests/test_daemon_dispatch.py` cover the matched-bypass / unmatched-fall-through / RULES-still-debounces matrix.
- **#55** in PR #95 — `state.write()` parse-back validates the temp file via `json.loads` between `tmp.write_text` and `os.replace`. On `JSONDecodeError`, the temp is unlinked and the exception re-raised, leaving any prior `state.json` intact. Defends the same singleton-bypass mode item #20 already addressed (torn JSON → `_read_at` returns `None` → `daemon.run` thinks no prior daemon → two concurrent daemons), via a different cause: a future serializer regression producing malformed output. Test in `tests/test_state.py::test_write_parse_back_rejects_invalid_json` monkeypatches `json.dumps` to inject `"{not valid json"` and asserts (a) the call raises, (b) no `.tmp` is left behind, (c) prior `state.json` content is unchanged.
- **#56** in PR #94 — adds `dbxignore generate <path>` (file-or-dir source; flags `-o`, `--stdout`, `--force`) and `dbxignore apply --from-gitignore <path>` (one-shot reconcile, rules mount at `dirname(<path>)`, existing `.dropboxignore` files in tree don't participate). New `RuleCache.load_external(source, mount_at)` is the shared seam; its docstring warns against mixing with `load_root` on the same mount because `_load_if_changed`'s stat-key would shadow. Both verbs share a `_read_and_validate_rule_source` helper for parse-before-write / parse-before-reconcile (extracted as a post-review refactor commit). README §"Using `.gitignore` rules" added. 19 new tests (3 `load_external` unit + 11 generate + 5 `apply --from-gitignore`). Filed-and-resolved in the same PR (filing was part of the items 55-65 batch in the working tree at PR-creation time).

#### 2026-05-02

- **#32** in PR #92 — CLI polish: `daemon_main` rewritten as a standalone `@click.command` with its own `--verbose` and `--version`; `_run_daemon` helper extracted; `@click.version_option(package_name="dbxignore")` added to the `main` group. All three symptoms (missing `--version`, unreachable `dbxignored --verbose`, bogus `Usage: dbxignored daemon`) gone; pinned by `tests/test_cli_entrypoints.py`. `install/_common.detect_invocation` unaffected as the risk note predicted.
- **#41 + #43** in PR #91 — two functional fixes from the items 41-48 batch. #41: `_reconcile_path` write-side ENOTSUP arm returns `currently_ignored` instead of `None`, restoring subtree pruning on xattr-unsupported filesystems for the clear-arm case (set-arm is unchanged because both `None` and `False` are falsy). Regression test in `test_reconcile_enotsup.py` verified to fail on un-fixed code. #43: hoist `Path.resolve()` to the CLI/daemon boundary per CLAUDE.md's stated contract — `cli._discover_roots` now resolves once for all six CLI commands; `daemon.run` resolves once at startup; `daemon._classify` returns the resolved src as a 4th tuple element (mirrors item #25's tuple expansion in PR #50); `reconcile_subtree`'s two redundant resolves dropped with the contract documented in its expanded docstring. Test churn limited to `test_daemon_dispatch.py` (4-tuple unpacks + pre-resolve `tmp_path` for macOS CI robustness).
- **#45-48** in PR #90 — doc-currency mini-sweep across four files. #45: `_applicable` docstring "Yield" → "Return" (no generator in body). #46: `windows_ads.set_ignored` docstring catcher reference `reconcile_subtree` → `reconcile._reconcile_path` (matches the Linux/macOS backend wording and the actual code). #47: s/ADS/marker/ across **five** locations in `reconcile.py` (the item enumerated three; the broader sweep also caught two `PermissionError`-arm log/comment lines using "ADS write" / "ADS state"). #48: added the macOS bullet to `state.py`'s module docstring so it matches the three-platform shape of `user_state_dir()`. Zero behavior change; ruff clean.
- **#49** in PR #89 — extract `_require_absolute` from both xattr backends (`linux_xattr.py`, `macos_xattr.py`) to `_backends/__init__.py:require_absolute`. Two callsite-preserving imports (`from . import require_absolute as _require_absolute`) keep the local-name semantics at all six call sites. Net −10 lines, one source of truth for the validator. Filed-and-resolved in the same PR per project convention.
- **#33** validated by v0.4.0a5 beta-tester pass — beta tester confirmed Dropbox actually stops syncing the marked folder after `dbxignore apply` on a macOS Tahoe 26.4 / Dropbox 250.4 File Provider install. All three open implementation questions in #33's body resolved affirmatively (no entitlements needed, stub files behave the same, `#P` alone is sufficient). The (B) scope decision pays off — v0.4 ships with full File Provider support rather than a documented legacy-only limitation.

#### 2026-05-01

- **#36** in PR #79 — macOS sync-mode detection rewritten as path-primary (info.json) with pluginkit-disambiguation. Fixes a v0.4.0a4 bug where users with Dropbox.app installed but declined the File Provider migration got the wrong attribute name written. Ships in v0.4.0a5.
- **#33** in PR #77 — macOS xattr backend now auto-detects Dropbox sync mode (legacy vs. File Provider) and selects the matching attribute name (`com.dropbox.ignored` vs. `com.apple.fileprovider.ignore#P`). Resolves the silent-failure mode where v0.4.0a3 wrote the wrong attribute on every File Provider install. (B) scope decision: absorbed into v0.4 before final tag. (Detection logic refined in PR #79 — see item #36; validated end-to-end on 2026-05-02 — see entry above.)
- **#35** in PR #76 — macOS launchd plist + Windows Task Scheduler XML now invoke the `dbxignored` sibling binary (with empty args) rather than the long-form `dbxignore` binary (which exits with status 2 on no-subcommand). Frozen branches of `_common.detect_invocation` + `windows_task.detect_invocation` now resolve via a three-step "self-as-dbxignored / sibling-search / fallback-to-daemon-subcommand" rule.
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
- **Item 33** added 2026-05-01 from continued v0.4.0a3 beta-testing on the same macOS machine — once the macOS binary actually launched (post-PR #71), the next layer of testing surfaced that the tester's Dropbox install is in File Provider mode (the modern default) rather than legacy mode, and the macOS xattr backend is hardcoded to the legacy attribute name. Documented `fileproviderctl dump` output + `~/.dropbox/info.json` content from the tester confirmed the mode and narrowed the architectural scope (root discovery is unaffected; only the xattr-backend layer needs work). Companion docs-only PR ships the README disambiguation independently of the architectural decision.
- **Item 34** added 2026-05-01 from a CI flake observed during PR #74's post-rebase run on `windows-latest`. Same test that prompted item #18 (originally filed 2026-04-24), resolved in PR #40 by widening the `_poll_until` timeout from 3.0s to 5.0s. Filed as a separate item rather than re-opening #18 because the Resolved-section history is reverse-chronological-only and re-opening would muddy that contract; #34's body explicitly references #18's resolution path and prior timeline.
- **Item 35** added 2026-05-01 from the same v0.4.0a3 macOS beta-test session — `launchctl print` revealed the launchd plist invoked the long-form `dbxignore` binary with no subcommand, hitting Click's "no subcommand → exit 2" arm and never reaching `daemon_mod.run()`. Filed and resolved in the same PR because the fix is small (~50 lines), the regression class is well-bounded (the frozen branches of `detect_invocation` in both `_common.py` and `windows_task.py`), and the tracker entry's value is mostly historical/educational rather than awaiting-action. Linux was unaffected — Linux installs don't ship a frozen binary, so they reached the non-frozen branch which already routed correctly through `shutil.which("dbxignored")`.
- **Item 36** added 2026-05-01 immediately after v0.4.0a4 shipped — the maintainer recognized that v0.4.0a4's pluginkit-primary detection conflated the *system-level* signal (is the extension registered?) with the *user-level* signal (which mode is this account in?), and that the user-level fact lives in info.json's `path` field. PluginKit registration says nothing about whether *this user's account* migrated; the path does. Filed and resolved in PR #79; ships in v0.4.0a5 before any beta tester ran v0.4.0a4 in the wild — caught by reasoning about the signal layers, not by a tester report. Detection logic reframed as path-primary with pluginkit-disambiguation; covers the missed case (extension installed + user in legacy mode → legacy) plus the external-drive File Provider case Dropbox docs document.
- **Item 37** added 2026-05-02 from the v0.4.0a5 ship cycle — the proposed-code snippet shared during the detection-design discussion returned a structured `{mode, confidence, reason, ...}` dict; we collapsed to a binary attr-name return for the call-site's needs but kept the diagnostic richness scope (mode + reason at INFO log; mode line in `dbxignore status` on darwin) as a v0.5 follow-up. Filed for the next macos-backend-touching change to bundle with.
- **Items 38–40** added 2026-05-02 from a `/simplify` review pass on PR #79's path-primary mode-detection code. Three parallel review agents (code-reuse, code-quality, efficiency) flagged opportunities the PR-#85 simplify cleanup deliberately deferred: #38 is the substantive duplication between `roots.py` and `_backends/macos_xattr.py` that wants a shared parsing helper but has real semantic differences blocking a one-line refactor; #39 is the stringly-typed `_pluginkit_extension_state()` return that wants a `Literal[...]` annotation; #40 is the dual-for-loops pattern where the two reviewers disagreed about whether unification helps or hurts clarity. All three are passive code-quality items awaiting a relevant code-touch trigger.
- **Item #33 validation note** added 2026-05-02 after the beta tester's v0.4.0a5 pass confirmed the File Provider attribute write actually causes Dropbox to stop syncing — closes the three open implementation questions in #33's body that had been left for end-to-end validation. The `Validated 2026-05-02` paragraph now lives inline in #33's body for future readers walking the trail.
- **Items 49–51** added 2026-05-02 from a `/simplify` whole-codebase review pass on `main` (no diff — explicit `the whole codebase` argument). Three parallel agents (reuse, quality, efficiency) ran against `src/dbxignore/` with all twenty open backlog items passed as exclusions to suppress rediscovery. Two solid findings emerged beyond the existing backlog: #49 (a clear duplication, fixed in the same PR) and #50 (a partial overlap that diverges by design in one branch — filed for future install-layer bundling). #51 documents an explicitly-rejected refactor for the design-tension record, mirroring item #40's precedent. Efficiency review found nothing new beyond items already tracked (#41, #43, #45-48).
- **Items 41-48** added 2026-05-02 from a whole-codebase local code-review pass against `0e6a285` (eight 75-confidence advisories — below the ≥80 ship-bar but verified-real, filed for backlog). Same shape and provenance as the items 20-23 batch from 2026-04-25. Five parallel reviewer agents (CLAUDE.md adherence, shallow bug scan, git-history regressions, prior PR comments, code-comment-vs-code consistency) → 11 findings → 11 parallel scoring agents → 8 cleared as verified-real, 3 filtered out: a flagged race in `RuleCache._applicable` (already filed as item #23, design-accepted) and a dead `RuntimeError` branch in `_common.detect_invocation` (already filed as item #26) both scored 0 as duplicates; a `pythonw.exe`/`detect_invocation` duplication concern scored 50 as defensible architectural inconsistency. Items #41 (functional ENOTSUP arm asymmetry) and #43 (resolve-at-boundary regression) are the two functional gaps; the remaining six are docstring/comment drift surfaced primarily by the consistency-pass agent.
- **Items 55-65** added 2026-05-04 from a design-review pass over the daemon, CLI, install, rules, and macOS-backend layers. Eleven items spanning defensive-coding gaps (#55, #58), ergonomic feature gaps (#56, #59-#64), one perf optimization companion to item #53 (#62), and one Windows UX feature (#65). All filed at low or low-medium urgency — no fired triggers; bundle each with the next code-touch in its respective layer.
- **Items 66-67** added 2026-05-04 from a `/code-review` pass over PR #94 itself. Five parallel reviewer agents (CLAUDE.md adherence, shallow bug scan, git-history regressions, prior PR comments, code-comment-vs-code consistency) → 8 findings → 8 parallel scoring agents → both items came in at score 75, one shy of the ≥80 ship-bar but verified-real, filed for backlog. Same shape as the items 41-48 batch from 2026-05-02. Both are one-line behavioral consistency tweaks; bundle with the next CLI-touching change.
- **Item 68** added 2026-05-04 from a `/simplify` review pass over PRs #95-#103 (the full session arc). Three parallel reviewer agents (reuse, quality, efficiency); the efficiency agent surfaced the conflict-walk-on-every-poll concern as the only finding above the noise floor. Three reuse/quality findings (the same `/simplify` pass) were small enough to fix inline in the same PR — see the chore commit's diff. Filed at low urgency; speculative perf concern, no user report yet.

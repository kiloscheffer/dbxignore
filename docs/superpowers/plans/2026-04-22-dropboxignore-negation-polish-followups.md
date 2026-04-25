# dropboxignore — post-v0.2 polish follow-ups

Items surfaced during PR #11's end-of-branch review (negation-semantics, item 10) and adjacent v0.2-maturation PRs. All are polish-scope — none block the features as shipped — but worth tracking so they don't accumulate into systemic drift.

Carry into a v0.3 polish PR or address as standalone small PRs whenever the file in question is next touched.

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

## 5. `_ancestors_of` calls `Path.resolve()` on every rule mutation

CLAUDE.md's Gotchas section flags `Path.resolve()` as a Windows perf hazard (per-call syscall). `_detect_conflicts` invokes `_ancestors_of` once per negation rule, each call doing one `.resolve()`. The cost fires only during rule mutations (rare — `load_root` on daemon start, `reload_file` on watchdog events, manual CLI invocations), and resolves exactly one path per negation. Negligible in practice.

The note here is about documentation, not optimization: add a comment in `_ancestors_of` explaining that the `.resolve()` cost is bounded to mutation events so a future reader doesn't "optimize" it out for the wrong reason (and break the path-equality invariant that downstream `is_relative_to` checks depend on).

Touches: `src/dropboxignore/rules.py` `_ancestors_of` docstring.

**Status: RESOLVED 2026-04-24.** Added a multi-line `NOTE:` comment at the `.resolve()` call in `_ancestors_of` (not the docstring — at the call site, where the temptation to "optimize the syscall" would strike). Captures both facts: (1) cost is bounded to mutation events (`load_root` / `reload_file` / `remove_file`), not the steady-state sweep, and resolves exactly one path per negation rule; (2) removing the resolution would break the downstream `is_relative_to(root)` and equality checks that assume canonical paths — a symlink or `..` component in `target` could fool both into disagreeing on path identity and missing valid ancestors.

## 6. `rules.py` has grown to ~530 lines; detection layer could extract

The detection layer (`literal_prefix`, `_ancestors_of`, `_find_masking_include`, `_detect_conflicts`, `Conflict`) is ~120 lines and has no coupling to `RuleCache` internals beyond the input-sequence shape. It could live in `rules_conflicts.py` or `conflicts.py` alongside `rules.py`; `RuleCache._recompute_conflicts` would import and call.

Not pressing — the file is still single-responsibility at a stretch, and splitting costs a sibling file plus one import edit. Worth revisiting in v0.3 if any further detection logic lands (e.g., cross-root conflicts, conflicts across installs) or if another feature pushes `rules.py` past ~650 lines.

Touches: `src/dropboxignore/rules.py` → `src/dropboxignore/rules_conflicts.py` (new); one import.

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

## 10. Publish releases as the repo owner, not `github-actions[bot]`

v0.2.0 was published by `github-actions[bot]` because `softprops/action-gh-release` authenticates via the default `GITHUB_TOKEN`. Visible in `gh release view v0.2.0` → `author: github-actions[bot]`. The release is still authoritative and tied to the repo's audit trail, but the UI-facing attribution reads as machine-authored rather than owner-authored.

Two mechanisms to fix:

- **Personal access token (PAT)** with `contents: write` + `actions: write` scopes. Store as a repo secret (`GH_RELEASE_TOKEN` or similar); pass to the action via `token: ${{ secrets.GH_RELEASE_TOKEN }}`. Simplest. Cost: secret management + periodic rotation.
- **GitHub App** with identity. More complex setup; justified if the token needs organization-wide reach or the PAT's personal scope would be too broad.

PAT is the standard solo-dev choice. Requires a one-time setup (generate PAT → add secret → update workflow), then releases surface under your GitHub identity.

Touches: `.github/workflows/release.yml` (add `token:` input to the `softprops/action-gh-release` step); repo secrets (one-time, outside of the repo tree).

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

**Urgency:** low until second observation on the same test. Note in CHANGELOG if it recurs on a user-visible CI run (not a PR retry).

Touches: `tests/test_daemon_smoke.py` (scope depends on chosen fix).

---

## Status

Items 1–3, 5, 7–13, 15–17 resolved (1, 2, 7 in PR #33; 3 + 5 in PR #34; 13 in this PR; 8–10 in v0.2.1 via PRs #15/#18/#19; 11–12 in v0.3.0 via PRs #22/#23; 15 + 17 in PR #30; 16 in PR #32). Items 4, 6, 14, 18 still open — none deadline-bound now that item 13's Node 20 hold-outs are off the runner. Items 4 and 6 are explicitly deferred-by-design ("not worth doing without a user report" and "not pressing — single-responsibility at a stretch" respectively); items 14 and 18 are flaky-test observations awaiting second occurrences. Items 14–16 added 2026-04-24 from v0.3.0 post-ship observations; item 17 added 2026-04-24 from a CLAUDE.md currency audit; item 18 added 2026-04-24 from a CI flake observed during PR #30's initial run (passed on rerun).

# dropboxignore — post-v0.2 polish follow-ups

Items surfaced during PR #11's end-of-branch review (negation-semantics, item 10) and adjacent v0.2-maturation PRs. All are polish-scope — none block the features as shipped — but worth tracking so they don't accumulate into systemic drift.

Carry into a v0.3 polish PR or address as standalone small PRs whenever the file in question is next touched.

## 1. Stale `# Task 3` banner in `tests/test_rules_conflicts.py`

Left over from the task-by-task execution of the implementation plan. The other tests in the file don't carry similar banners — it reads as an orphan comment now that the feature is integrated. Delete the comment line.

Touches: `tests/test_rules_conflicts.py:51` (one-line removal).

## 2. Redundant inline imports in new test functions

`tests/test_cli_status_list_explain.py` has several new test functions with in-body imports like `from dropboxignore import cli, state` even though those modules are already imported at the top of the file. Copied verbatim from the implementation plan's self-contained snippets; works but adds visual noise.

Fix: consolidate to module-level imports; remove the duplicates. Same cleanup applies to `tests/test_rules_reload_explain.py` where a handful of tests have `from dropboxignore.rules import RuleCache` inside the function body.

Touches: `tests/test_cli_status_list_explain.py`, `tests/test_rules_reload_explain.py`.

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

## 6. `rules.py` has grown to ~530 lines; detection layer could extract

The detection layer (`literal_prefix`, `_ancestors_of`, `_find_masking_include`, `_detect_conflicts`, `Conflict`) is ~120 lines and has no coupling to `RuleCache` internals beyond the input-sequence shape. It could live in `rules_conflicts.py` or `conflicts.py` alongside `rules.py`; `RuleCache._recompute_conflicts` would import and call.

Not pressing — the file is still single-responsibility at a stretch, and splitting costs a sibling file plus one import edit. Worth revisiting in v0.3 if any further detection logic lands (e.g., cross-root conflicts, conflicts across installs) or if another feature pushes `rules.py` past ~650 lines.

Touches: `src/dropboxignore/rules.py` → `src/dropboxignore/rules_conflicts.py` (new); one import.

## 7. No test for the "sandwich" ordering `include → negation → another_include`

By inspection of `_detect_conflicts`, the algorithm only looks at `sequence[:i]` (entries before the current negation), so a later include can't retroactively affect an earlier negation's conflict state. The `include → !negation → another_include` shape therefore works correctly — the `another_include` is invisible to the detector.

But there's no explicit test pinning this. If a future refactor accidentally changed the slice to `sequence[i + 1:]` or iterated the full sequence, the bug would only surface in real-world `.dropboxignore` files, not in the test suite.

Fix: a three-entry test in `tests/test_rules_conflicts.py` with `build/` + `!build/keep/` + `src/`, asserting exactly one conflict and that the presence of `src/` didn't change detection.

Touches: `tests/test_rules_conflicts.py` (one new test).

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

---

## Status

Items 8–12 resolved (8–10 in v0.2.1 via PRs #15/#18/#19; 11–12 in v0.3.0 via PRs #22/#23). Items 1–7 and 13 still open. Item 13 (Node.js 20 → 24 action bump) has a hard stop September 2026 when the runner removes Node 20.

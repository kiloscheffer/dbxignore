# Inaugural-release cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the tracked state of the repo read as a first public release — no documentation, comment, or test refers to an earlier version, backlog item, commit, or pull request.

**Architecture:** Mechanical de-anchoring pass partitioned by area (`src`, `tests`, `scripts`, workflows+config, `README`+`active-gotchas`, `AGENTS.md`). Each area task: enumerate references with a grep, apply the shared rewrite rules, re-grep to confirm zero residue, commit. A final task runs the full check suite and produces the untouched-file list.

**Tech Stack:** Python project; `uv` for tooling (`ruff`, `mypy`, `pytest`); git topic branch `chore/inaugural-release-cleanup`.

---

## Shared rewrite rules (apply in every task)

Sort each reference into one class and transform it. Guiding principle: **preserve the engineering rationale, erase the timeline.**

| Class | Before | After |
|---|---|---|
| Anchor inside live rationale | `pre-#79 state.json files lack the field` | `older state.json files lack the field` |
| Trailing provenance parenthetical | `…on disjoint paths (item #53 candidate 3)` | `…on disjoint paths` |
| `BACKLOG #N:` / `item #N —` prefix | `BACKLOG #122: fail-closed gate against…` | `Fail-closed gate against…` |
| Pure attribution, no surviving rationale | `Surfaced by Codex on PR #240.` | *(sentence removed)* |
| Version identifier | `v0.4.0a4 conflated the two` | `an earlier implementation conflated the two` |
| Version-relative phrasing | `Pre-PR-#108 behavior considered only…` | `Earlier behavior considered only…` |
| Script case marker | `# 4j — apply --dry-run… (PR #103)` | `# 4j — apply --dry-run…` |
| Review-process attribution | `Codex P2 regression: a negation rule…` | `Regression: a negation rule…` |
| Review-process phrasing | `the Codex P2 ingestion race` / `round-9 added…` | `the ingestion race` / `… was added` |

Review-process attribution (owner-approved scope addition): strip "Codex" in
every form, "round-N" iteration markers, "external review", "Bot reproducer",
and bare "P1"/"P2" severity labels when they are review-process residue. Keep
the useful signal — `Codex P2 regression:` becomes `Regression:`, not nothing.

**Kept deliberately** (not residue — do not remove):
- File-level pointers naming `BACKLOG.md` or `CHANGELOG.md` as files.
- The README link to the Semantic Versioning spec (`semver.org/spec/v2.0.0.html`).

**The reference-detection greps** (used to enumerate and to verify, per area):

```bash
git grep -n -E '#[0-9]+|PR #|pull request|BACKLOG|backlog|\bv0\.[0-9]|\bv[0-9]+\.[0-9]+|item #' -- <area paths>
git grep -n -i -E 'codex|round-?[0-9]|external review|bot reproducer' -- <area paths>
```

---

## Task 1: Establish baseline and commit planning docs

**Files:**
- Create (already written, uncommitted): `docs/superpowers/specs/2026-05-14-inaugural-release-cleanup-design.md`
- Create (already written, uncommitted): `docs/superpowers/plans/2026-05-14-inaugural-release-cleanup.md`

- [ ] **Step 1: Confirm branch**

Run: `git branch --show-current`
Expected: `chore/inaugural-release-cleanup`

- [ ] **Step 2: Capture the baseline reference count**

Run:
```bash
git grep -c -E '#[0-9]+|PR #|pull request|BACKLOG|backlog|\bv0\.[0-9]|\bv[0-9]+\.[0-9]+|item #' -- src tests scripts .github pyinstaller README.md docs/internals/active-gotchas.md AGENTS.md | wc -l
```
Expected: a non-zero count of files (the work backlog). Record it; Task 8 expects this to drop to only the deliberately-kept lines.

- [ ] **Step 3: Commit the planning docs**

```bash
git add docs/superpowers/specs/2026-05-14-inaugural-release-cleanup-design.md docs/superpowers/plans/2026-05-14-inaugural-release-cleanup.md
git commit -m "docs: add inaugural-release cleanup spec and plan"
```

---

## Task 2: De-anchor `src/**` comments and docstrings

**Files:**
- Modify: every file under `src/` flagged by the grep below. Known hotspots: `src/dbxignore/daemon.py`, `src/dbxignore/reconcile.py`, `src/dbxignore/rules.py`, `src/dbxignore/cli.py`, `src/dbxignore/state.py`, `src/dbxignore/rules_conflicts.py`, `src/dbxignore/debounce.py`, `src/dbxignore/_logging.py`, `src/dbxignore/_testing.py`, `src/dbxignore/_backends/macos_xattr.py`, `src/dbxignore/install/__init__.py`, `src/dbxignore/install/_common.py`, `src/dbxignore/install/windows_shell.py`, `src/dbxignore/install/windows_task.py`, `src/dbxignore/install/macos_launchd.py`.

- [ ] **Step 1: Enumerate the references**

Run: `git grep -n -E '#[0-9]+|PR #|pull request|BACKLOG|backlog|\bv0\.[0-9]|\bv[0-9]+\.[0-9]+|item #' -- src`
Expected: ~50 lines. This is the work list for this task.

- [ ] **Step 2: Apply the rewrite rules to each line**

Edit comments and docstrings only — never executable code. Worked examples from this area:

- `src/dbxignore/state.py`: `# compat with state.json files written before #79.` → `# compat with older state.json files that predate the field.`
- `src/dbxignore/state.py`: `# Decode-tolerant: pre-#68 state.json files lack this field and` → `# Decode-tolerant: older state.json files lack this field and`
- `src/dbxignore/daemon.py`: `# meantime. Surfaced by Codex on PR #240.` → *(remove the sentence; keep the preceding rationale)*
- `src/dbxignore/daemon.py`: `singleton gate that backlog item #78 introduces — the prior` → `singleton gate — the prior`
- `src/dbxignore/_backends/macos_xattr.py`: ```info.json``'s path field.  v0.4.0a4 conflated the two and` → `` `info.json` ``'s path field.  An earlier implementation conflated the two and`
- `src/dbxignore/cli.py`: `# BACKLOG #122: fail-closed gate against a silent tug-of-war. By this` → `# Fail-closed gate against a silent tug-of-war. By this`
- `src/dbxignore/install/_common.py`: `lookup logic for the daemon and CLI entry points after PR #30. Frozen` → `lookup logic for the daemon and CLI entry points. Frozen`
- `src/dbxignore/install/macos_launchd.py`: ``unload -w` is intentionally not used — see the v0.4 spec for rationale.` → ``unload -w` is intentionally not used; see the macOS launchd backend notes for rationale.`

- [ ] **Step 3: Verify zero residue in `src/`**

Run: `git grep -n -E '#[0-9]+|PR #|pull request|BACKLOG|backlog|\bv0\.[0-9]|\bv[0-9]+\.[0-9]+|item #' -- src`
Expected: no output. (If a line legitimately names `BACKLOG.md` as a file, that is allowed — none are currently expected in `src/`.)

- [ ] **Step 4: Confirm nothing structural broke**

Run: `uv run ruff check src && uv run mypy src`
Expected: both pass (comment edits do not affect either, but this catches an accidental code edit).

- [ ] **Step 5: Commit**

```bash
git add src
git commit -m "docs: de-anchor historical references in source comments"
```

---

## Task 3: De-anchor `tests/**` docstrings and comments

**Files:**
- Modify: every file under `tests/` flagged by the grep below (~110 lines across ~25 files; hotspots include `tests/test_state.py`, `tests/test_rules_basic.py`, `tests/test_install.py`, `tests/test_cli_ignore.py`, `tests/test_daemon_*.py`, `tests/test_rules_conflicts.py`).

- [ ] **Step 1: Enumerate the references**

Run: `git grep -n -E '#[0-9]+|PR #|pull request|BACKLOG|backlog|\bv0\.[0-9]|\bv[0-9]+\.[0-9]+|item #' -- tests`
Expected: ~110 lines. Work list for this task.

- [ ] **Step 2: Check for identifier-level references (the one rename class)**

Run: `git grep -n -E 'def test_[a-z0-9_]*(pre_|post_)[0-9]|_v0_[0-9]' -- tests`
Expected: a (likely short or empty) list of test function or variable names embedding a version/PR token. If non-empty, **stop and surface this list for review before renaming** — renames touch test selection, not just prose.

- [ ] **Step 3: Apply the rewrite rules — docstrings and comments only**

Never alter assertions, fixture bodies, parametrization, or control flow. Worked examples from this area:

- `tests/test_state.py`: `"""state.json files written before #68 lack last_sweep_conflicts. Decode` → `"""Older state.json files lack last_sweep_conflicts. Decode`
- `tests/test_state.py`: `"""Pre-#30 frozen install: process is dbxignored.exe. After #30` → `"""Older frozen install: the process is named dbxignored.exe. The current`
- `tests/test_daemon_initial_sweep.py`: `"""Tests for the worker-thread initial-sweep design (BACKLOG #53).` → `"""Tests for the worker-thread initial-sweep design.`
- `tests/test_rules_basic.py`: `"""Item #86 / Codex P2 catch on PR #184: load_root must NOT pre-stat` → `"""load_root must NOT pre-stat`
- `tests/test_install.py`: `"""Backlog item #98: when `markers.clear_ignored` raises OSError on one` → `"""When `markers.clear_ignored` raises OSError on one`
- `tests/test_cli_ignore.py`: `# Validator GUI-dialog routing (PR #238 fix)` → `# Validator GUI-dialog routing`
- `tests/test_linux_xattr_integration.py`: `pytest.skip("user.* xattrs are Linux-only in v0.2", …)` → `pytest.skip("user.* xattrs are Linux-only", …)` (string is a skip *reason*, not logic — safe to edit)

- [ ] **Step 4: Verify zero residue in `tests/`**

Run: `git grep -n -E '#[0-9]+|PR #|pull request|BACKLOG|backlog|\bv0\.[0-9]|\bv[0-9]+\.[0-9]+|item #' -- tests`
Expected: no output.

- [ ] **Step 5: Run the test suite — confirm no docstring edit leaked into logic**

Run: `uv run python -m pytest`
Expected: same pass/skip counts as before the task (baseline: 712 passed, 33 skipped on the portable subset; `windows_only` tier adds a few on Windows).

- [ ] **Step 6: Commit**

```bash
git add tests
git commit -m "test: de-anchor historical references in test docstrings"
```

---

## Task 4: De-anchor `scripts/**` comments

**Files:**
- Modify: `scripts/_phase_extended_cli.sh`, `scripts/manual-test-macos.sh`, `scripts/manual-test-ubuntu-vps.sh`, `scripts/manual-test-windows.ps1`.

- [ ] **Step 1: Enumerate the references**

Run: `git grep -n -E '#[0-9]+|PR #|pull request|BACKLOG|backlog|\bv0\.[0-9]|\bv[0-9]+\.[0-9]+|item #' -- scripts`
Expected: ~190 lines. Work list for this task.

- [ ] **Step 2: Apply the rewrite rules**

Two dominant patterns in this area:

1. **Case markers** — strip the trailing PR/item parenthetical, keep the `# 4X — <description>`:
   - `# 4j — apply --dry-run does not mutate (PR #103)` → `# 4j — apply --dry-run does not mutate`
   - `# 4r — clear/list exit 2 on nonexistent path (PR #195, item #95)` → `# 4r — clear/list exit 2 on nonexistent path`
   - `# 6g - uninstall --purge exits 2 on injected daemon-alive guard (PR #249, item #129)` → `# 6g - uninstall --purge exits 2 on injected daemon-alive guard`

2. **Rationale comments and header blocks** — de-anchor in place; delete pure-history sentences:
   - `#   added across PRs #100, #102, #103, #107, #108, #195, #203, #205.` → *(remove the line; the helper's purpose is described by the surrounding block)*
   - `# (~120 LOC each) and was being maintained twice; backlog item #75 is the` → `# (~120 LOC each) and was being maintained twice; this shared body is the`
   - `# Before BACKLOG #30 this tested `dbxignored --help`; post-#30 the daemon` → *(remove — describes only a past state; keep the line(s) that state current behavior)*
   - `# Slow-sweep determinism (BACKLOG #89). Seed a 15s pad so 5a's 5-iteration` → `# Slow-sweep determinism. Seed a 15s pad so 5a's 5-iteration`
   - The `manual-test-macos.sh` / `manual-test-ubuntu-vps.sh` header blocks listing `# - PR #102: dbxignore init scaffolds…` → keep the capability descriptions, drop the `PR #NNN:` prefix on each line.
   - `note "5 — slow-sweep marker seeded: 15s pad on initial sweep (item #89)"` → `note "5 — slow-sweep marker seeded: 15s pad on initial sweep"` (status string, safe to edit)

- [ ] **Step 3: Verify zero residue in `scripts/`**

Run: `git grep -n -E '#[0-9]+|PR #|pull request|BACKLOG|backlog|\bv0\.[0-9]|\bv[0-9]+\.[0-9]+|item #' -- scripts`
Expected: no output.

- [ ] **Step 4: Shell sanity check**

Run: `bash -n scripts/_phase_extended_cli.sh scripts/manual-test-macos.sh scripts/manual-test-ubuntu-vps.sh`
Expected: no output (syntax OK). For the PowerShell script: `pwsh -NoProfile -Command "$null = [System.Management.Automation.Language.Parser]::ParseFile('scripts/manual-test-windows.ps1', [ref]$null, [ref]$null)"` — expected: no parse errors.

- [ ] **Step 5: Commit**

```bash
git add scripts
git commit -m "chore: de-anchor historical references in manual-test scripts"
```

---

## Task 5: De-anchor `.github/**`, `pyinstaller/**`, and config files

**Files:**
- Modify: `.github/dependabot.yml`, `.github/workflows/commit-check.yml`, `.github/workflows/claude-code-review.yml`, `.github/workflows/release.yml`, `pyinstaller/dbxignore-macos.spec`.
- Scan only (no change expected): `pyproject.toml`, `cchk.toml`.

- [ ] **Step 1: Enumerate the references**

Run: `git grep -n -E '#[0-9]+|PR #|pull request|BACKLOG|backlog|\bv0\.[0-9]|\bv[0-9]+\.[0-9]+|item #' -- .github pyinstaller pyproject.toml cchk.toml`
Expected: ~6 lines, all in `.github/` and `pyinstaller/`. The SHA-pin comments like `# v6.0.2` are version tags of third-party actions, **not** dbxignore versions — leave those (they are functional pin annotations). Only dbxignore-version / PR / backlog references are in scope.

- [ ] **Step 2: Apply the rewrite rules**

- `.github/dependabot.yml`: `# Workflows are pinned to 40-char commit SHAs (per backlog item #74) with a` → `# Workflows are pinned to 40-char commit SHAs with a`
- `.github/workflows/commit-check.yml`: `# on PR #160.` → *(remove the trailing reference; keep the rule it annotates)*
- `.github/workflows/claude-code-review.yml`: `# without enumeration. Surfaced on PR #152 — originally driven by the` → `# without enumeration. Originally driven by the`
- `.github/workflows/release.yml`: `# v0.4.0a1 macOS _cffi_backend shape). --version fails-fast on` → `# an earlier macOS _cffi_backend shape). --version fails-fast on` — and similarly for the other two `v0.4.0a1` mentions and the `PR #62 root cause` mention (de-anchor: `# is wrong — that earlier misfire (root cause).`).
- `pyinstaller/dbxignore-macos.spec`: `# cffi normally bundles it, but the v0.4.0a1 macOS build shipped` → `# cffi normally bundles it, but an earlier macOS build shipped`

- [ ] **Step 3: Verify zero residue (excluding third-party action SHA-pin tags)**

Run: `git grep -n -E 'PR #|pull request|BACKLOG|backlog|\bv0\.[0-9]|item #' -- .github pyinstaller pyproject.toml cchk.toml`
Expected: no output. (This narrower pattern omits the bare `#[0-9]+` / `v[0-9]+\.[0-9]+` forms that match the legitimate `# v6.0.2` action-pin comments.)

- [ ] **Step 4: Commit**

```bash
git add .github pyinstaller
git commit -m "ci: de-anchor historical references in workflow comments"
```

---

## Task 6: De-anchor `README.md` and `docs/internals/active-gotchas.md`

**Files:**
- Modify: `docs/internals/active-gotchas.md`.
- Scan and confirm-no-change: `README.md`.

- [ ] **Step 1: Enumerate the references**

Run: `git grep -n -E '#[0-9]+|PR #|pull request|BACKLOG|backlog|\bv0\.[0-9]|\bv[0-9]+\.[0-9]+|item #' -- README.md docs/internals/active-gotchas.md`
Expected: README hits are the `[Backlog](#backlog)` table-of-contents anchor, the `## Backlog` section linking to `BACKLOG.md`, and the SemVer spec link — **all deliberately kept** (file-pointers and a spec link, not version/PR/item references). `active-gotchas.md` has 2 hits.

- [ ] **Step 2: Confirm README needs no edits**

Re-read the three README hits. Each must be either a `BACKLOG.md` file-pointer or the `semver.org` link. If so, README is left unchanged. If any hit is an actual version/PR/item reference, apply the rewrite rules to it.

- [ ] **Step 3: Apply the rewrite rules to `active-gotchas.md`**

- Line ~64: `Observed empirically during the v0.5.0 manual-test validation pass: identical code, two consecutive runs, three Phase 5 failures vs zero.` → `Observed empirically during a manual-test validation pass: identical code, two consecutive runs, three Phase 5 failures vs zero.`
- Line ~78: re-read the omitted long line and de-anchor per the rules (the surrounding context is the "delete branch on merge" / `git push` recreates-branch gotcha — keep the gotcha, drop any version/PR anchor).

- [ ] **Step 4: Verify zero residue (excluding deliberate keeps)**

Run: `git grep -n -E '#[0-9]+|PR #|pull request|\bv0\.[0-9]|\bv[0-9]+\.[0-9]+|item #' -- docs/internals/active-gotchas.md`
Expected: no output.
Run: `git grep -n -E 'PR #|pull request|\bv0\.[0-9]|item #' -- README.md`
Expected: no output (the remaining `backlog`/`BACKLOG` hits are the kept file-pointers).

- [ ] **Step 5: Commit**

```bash
git add README.md docs/internals/active-gotchas.md
git commit -m "docs: de-anchor historical references in README and active-gotchas"
```

---

## Task 7: Rewrite `AGENTS.md`

**Files:**
- Modify: `AGENTS.md` (full pass). `CLAUDE.md` is a one-line `@AGENTS.md` include — not touched.

- [ ] **Step 1: Enumerate the references**

Run: `git grep -n -E '#[0-9]+|PR #|pull request|BACKLOG|backlog|\bv0\.[0-9]|\bv[0-9]+\.[0-9]+|item #' -- AGENTS.md`
Expected: ~21 lines spanning the Commands, Architecture, Git-workflow, Release, Manual-test-scripts, and Gotchas sections.

- [ ] **Step 2: Rewrite section by section — keep every actionable rule, drop every anchor**

- **Commands section:** the sentence describing the pre-unification separate `dbxignored` console script describes only a past state — remove it; keep the current `dbxignore daemon` description.
- **Architecture section:** de-anchor `BACKLOG #30`, `PR #127`, `(item #116)` etc. in place — e.g. `(#30 merged them into a single entry-point)` → *(remove the parenthetical)*.
- **Git-workflow section:** keep every rule; drop PR-citation examples. `PR #4 is the template — one feat commit…` → `Split a code change and a doc-only update into separate commits.` `Hit by PRs #228/#229/#230 (2026-05-12).` → *(remove)*. `(PR #12: a commit description starting with --…)` → *(remove the parenthetical; keep the rule)*. The `<THIS_PR>` placeholder convention paragraph references PRs structurally — reword to describe the placeholder mechanism without the PR-number framing, or remove if it no longer applies to a first release.
- **Release section:** drop `v0.4.0a1`, `PR #62`, `PR #163`, `v0.2.0 introduced two` call-outs; keep the mechanism descriptions (classify-tag job, PyPI gate, SHA-pinning, etc.).
- **Manual-test-scripts section:** reword so per-case provenance no longer mandates a `(PR #NNN)` comment — the case markers stay as `# 4X — <description>`. Drop `backfilled in PR #114`, `extracted in PR #143, resolving backlog item #75`, `PRs #203/#205`, etc.
- **Gotchas section:** de-anchor every `(PR #NNN)`, `(BACKLOG #NN)`, `item #NN` parenthetical and inline reference; keep the gotcha text.
- **Docs section:** keep file-level pointers to `BACKLOG.md` / `CHANGELOG.md`; the `Current: v0.4 macOS port — specs/...` line becomes a version-neutral pointer (e.g. `Specs and plans live under docs/superpowers/{specs,plans}/`).

- [ ] **Step 3: Verify zero residue (excluding deliberate `BACKLOG.md`/`CHANGELOG.md` file-pointers)**

Run: `git grep -n -E 'PR #|pull request|\bv0\.[0-9]|\bv[0-9]+\.[0-9]+|item #|#[0-9]+' -- AGENTS.md`
Expected: only lines that name `BACKLOG.md` or `CHANGELOG.md` as files (if any remain). Manually confirm each survivor is a file-pointer, not an item/version/PR/commit reference.

- [ ] **Step 4: Read-through review**

Read the rewritten `AGENTS.md` start to finish. Confirm every actionable rule from the original survives — only historical anchoring should be gone.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md
git commit -m "docs: rewrite AGENTS.md free of version and PR history"
```

---

## Task 8: Final verification and deliverable

**Files:** none modified.

- [ ] **Step 1: Repo-wide residue grep across the in-scope set**

Run both:
```bash
git grep -n -E '#[0-9]+|PR #|pull request|BACKLOG|backlog|\bv0\.[0-9]|\bv[0-9]+\.[0-9]+|item #' -- src tests scripts .github pyinstaller README.md docs/internals/active-gotchas.md AGENTS.md pyproject.toml cchk.toml
git grep -n -i -E 'codex|round-?[0-9]|external review|bot reproducer' -- src tests scripts .github pyinstaller README.md docs/internals/active-gotchas.md AGENTS.md
```
Expected: only deliberately-kept lines — `BACKLOG.md`/`CHANGELOG.md` file-pointers (README, AGENTS.md), the `semver.org` link (README), and third-party action SHA-pin `# vX.Y.Z` tags (`.github/`). Inspect every surviving line and confirm it is on the keep-list. Anything else is residue: fix it and land it as a new follow-up commit on the appropriate area (the per-task commits are already made — do not amend them).

- [ ] **Step 2: Full check suite**

Run:
```bash
uv run ruff check .
uv run mypy .
uv run python -m pytest
```
Expected: ruff clean, mypy clean, pytest pass/skip counts unchanged from baseline.

- [ ] **Step 3: Pre-flight commit-check over the branch**

For every commit in `origin/main..HEAD`, confirm the subject satisfies Conventional Commits (`cchk.toml` `allow_commit_types`) and the branch name satisfies Conventional Branch. Run `git log --format='%s' origin/main..HEAD` and check each subject.
Expected: all subjects use an allowed type (`docs`, `test`, `chore`, `ci` used in this plan); branch `chore/inaugural-release-cleanup` is valid.

- [ ] **Step 4: Produce the standalone-history-file list for the owner**

Present this list (the "can stay, listed for review" set — these files were intentionally **not** edited):

```
BACKLOG.md
CHANGELOG.md
docs/internals/historical-gotchas.md
docs/release-notes/v0.2.0.md
docs/release-notes/v0.2.1.md
docs/release-notes/v0.3.0.md
docs/release-notes/v0.3.1.md
docs/release-notes/v0.3.2.md
docs/release-notes/v0.4.0.md
docs/release-notes/v0.5.0.md
docs/release-notes/v0.5.1.md
docs/superpowers/specs/   (all dated design docs, including this task's design doc)
docs/superpowers/plans/   (all dated implementation plans, including this plan)
```

- [ ] **Step 5: Post-task memory update**

The stored memory `feedback_manual_test_scripts.md` currently records that each manual-test case carries a `(PR #NNN)` provenance comment. That convention was removed in this work. Update the memory so the per-case PR-provenance requirement is no longer asserted.

- [ ] **Step 6: Final summary to owner**

Report: number of files changed, the commit list, the verification results, and the standalone-file list from Step 4. Do not push or open a PR unless the owner explicitly asks.

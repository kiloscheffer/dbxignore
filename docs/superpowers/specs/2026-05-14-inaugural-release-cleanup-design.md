# Inaugural-release cleanup — design

## Goal

Make the tracked state of the repository read as if it were the project's
first public release. No documentation, comment, or test may refer to an
earlier version, a backlog item, a commit, or a pull request. Standalone
history files (backlog, changelog, release notes, dated specs and plans,
historical-gotchas) are permitted to remain — but are listed explicitly so
the project owner can review them separately.

## Scope

### In scope (edited)

Every tracked file outside the standalone-history set that contains a
reference to a version, backlog item, commit, or pull request:

- `src/**` — code comments and docstrings
- `tests/**` — test docstrings and comments (test logic is never touched)
- `scripts/**` — manual-test-script comments, including the per-case markers
- `.github/**` — workflow-file comments
- `pyinstaller/**` — spec-file comments
- `README.md`
- `docs/internals/active-gotchas.md`
- `AGENTS.md` — full rewrite (see below); `CLAUDE.md` is a one-line
  `@AGENTS.md` include and is left untouched
- `pyproject.toml`, `cchk.toml` — scanned for stray references; none
  expected, since the version is derived from VCS rather than hardcoded

### Out of scope (left untouched, listed for owner review)

These standalone history files are not edited:

- `BACKLOG.md`
- `CHANGELOG.md`
- `docs/internals/historical-gotchas.md`
- `docs/release-notes/` — 8 files
- `docs/superpowers/specs/` — dated design docs
- `docs/superpowers/plans/` — dated implementation plans

Generated files (`uv.lock`) and `LICENSE` are not in scope. Machine-local
gitignored files are not in scope.

## Rewrite rules

Each reference is sorted into one class and transformed accordingly. The
guiding principle: preserve the engineering rationale, erase the timeline.

| Class | Before | After |
|---|---|---|
| Anchor inside live rationale | `pre-#79 state.json files lack the field` | `older state.json files lack the field` |
| Trailing provenance parenthetical | `…on disjoint paths (item #53 candidate 3)` | `…on disjoint paths` |
| `BACKLOG #N:` / `item #N —` prefix | `BACKLOG #122: fail-closed gate against…` | `Fail-closed gate against…` |
| Pure attribution, no surviving rationale | `Surfaced by Codex on PR #240.` | *(sentence removed)* |
| Version identifier | `v0.4.0a4 conflated the two` | `an earlier implementation conflated the two` |
| Version-relative phrasing | `Pre-PR-#108 behavior considered only…` | `Earlier behavior considered only…` |
| Script case marker | `# 4j — apply --dry-run… (PR #103)` | `# 4j — apply --dry-run…` |

### Constraints

- **`tests/` — comments and docstrings only.** Assertions, fixture bodies,
  parametrization, and control flow are never altered. If an *identifier*
  (a test function name, a variable) embeds a token such as `pre_30`, it is
  renamed too — this is the single identifier-level change class, and the
  full list of such renames is surfaced for review before any name changes.
- **File-level pointers survive.** Naming the `BACKLOG.md` or `CHANGELOG.md`
  file (for example, the README's "Backlog" section linking to
  `BACKLOG.md`, or AGENTS.md noting where the backlog lives) is not a
  reference to a version, item, commit, or PR. Those pointers are kept.
- **SemVer reference survives.** The README's link to the Semantic
  Versioning spec is a specification pointer, not a project-version
  reference.

## AGENTS.md full rewrite

AGENTS.md retains every actionable rule; only historical anchoring is
removed.

- The `Current: v0.4 macOS port` status line becomes a version-neutral
  pointer to `docs/superpowers/`.
- The Git-workflow section keeps every rule but drops PR-citation examples
  ("PR #N is the template"-style call-outs).
- The Backlog-conventions section keeps the filing process but drops
  specific-item examples.
- The Release section keeps the mechanism descriptions but drops
  version-specific and PR-specific call-outs.
- The Manual-test-scripts section is reworded so per-case provenance no
  longer mandates a `(PR #NNN)` comment.
- The Architecture, Commands, and Gotchas sections are de-anchored line by
  line. Sentences that describe only a past state ("Before X this was a
  separate console script") are removed rather than rewritten.

## Execution

Work happens on branch `chore/inaugural-release-cleanup`. The edits
partition cleanly by area — `src`, `tests`, `scripts`,
`.github`+`pyinstaller`, and `AGENTS.md`+`README.md`+`active-gotchas.md` —
and may be done in parallel under a shared copy of the rewrite rules above.
The implementation plan decides the parallel-versus-sequential split.

Commits are split along revertability lines: code comments, test
docstrings, docs and workflow comments, and the AGENTS.md rewrite are
separable changes and land as separate commits.

## Verification

Before the work is considered complete:

1. Re-grep the reference patterns across the in-scope set. Expect zero
   hits except the deliberate `BACKLOG.md` / `CHANGELOG.md` file-pointers
   and the SemVer specification link.
2. `uv run ruff check .` and `uv run mypy .` — confirm nothing structural
   broke.
3. `uv run python -m pytest` (portable subset plus the `windows_only`
   tier) — confirm no test docstring edit leaked into test logic.
4. Manual diff review of AGENTS.md, the largest single rewrite.

## Deliverables

1. The edited in-scope files.
2. The explicit list of untouched standalone-history files (Scope section
   above), handed to the owner for separate review.

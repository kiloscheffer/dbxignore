# dbxignore

Cross-platform Python utility: keeps Dropbox ignore markers (NTFS alternate data streams on Windows; `user.com.dropbox.ignored` xattrs on Linux; `com.dropbox.ignored` xattrs on macOS) in sync with hierarchical `.dropboxignore` files.

## Commands

- `uv sync --all-extras` — install
- `uv run python -m pytest` — full suite (canonical form, also listed under "How to run checks"). Plain `uv run pytest` works on a fresh `uv sync` but can fail with `ModuleNotFoundError` or `uv trampoline failed to canonicalize` on stale environments — see Gotchas. Windows adds a few ADS-integration tests via `@pytest.mark.windows_only`.
- `uv run pytest -m "not windows_only"` — portable subset (what Ubuntu CI runs)
- `uv run pytest -W error::DeprecationWarning` — local strict mode (not enforced in CI)
- `uv run ruff check` — lint; rule families per `pyproject.toml` `[tool.ruff.lint] select` (don't restate the list here — pyproject is the source of truth); line length 100
- `dbxignore <apply|status|clear|list|explain|check-ignore|daemon|install|uninstall|init|generate>` — CLI console script (`cli:main`). `install` / `uninstall` register / remove the daemon with the platform's user-scoped service manager (Task Scheduler on Windows, systemd user unit on Linux, launchd LaunchAgent on macOS). `uninstall --purge` also clears every ignore marker.
- `dbxignored` — daemon entry point (`cli:daemon_main`, a standalone `@click.command` with its own `--verbose`/`--version`), launched by the platform's installed service (Scheduled Task / systemd unit / LaunchAgent)
- `python -m dbxignore <subcommand>` — equivalent to the console script, via `src/dbxignore/__main__.py`. Useful when the wheel isn't installed (e.g. `uv run python -m dbxignore status`).

@docs/internals/architecture.md

@docs/internals/gotchas.md

## Git workflow

- Never commit directly to `main`. Work on a topic branch and open a PR — that's what triggers `.github/workflows/test.yml` (the platform-gated test tiers `pytest -m windows_only`, `pytest -m linux_only`, and `pytest -m macos_only` **only run in CI**) and `.github/workflows/commit-check.yml` (commit-message + branch-name validation). A local `uv run pytest` can only exercise one platform, so a single green local run is not a merge gate. The PR matrix is.
- **`cchk.toml` at repo root is the single source of truth** for allowed commit types (Conventional Commits, see `allow_commit_types`), branch types (Conventional Branch, see `allow_branch_types`), and the subject-length cap. Don't restate those lists here or elsewhere — reference the file so it can't drift. Local enforcement is optional but encouraged: `uv tool install pre-commit && pre-commit install --hook-type commit-msg --hook-type pre-push` wires the same rules at commit/push time. CI re-runs them on every PR via `commit-check/commit-check-action@v2.6.0`.
- **Pre-flight commit-check against every commit, not just HEAD.** CI runs `commit-check` over the full `origin/main..HEAD` range — a check that only validates the planned PR title can pass locally while an intermediate commit fails CI (PR #12: a commit description starting with `--` tripped commit-check's regex). Loop over each subject before pushing.
- **Commit subjects starting with `#` immediately after the type/scope colon are rejected by commit-check** as if the whole message were a comment. `docs(backlog): #34 sixth observation` fails; `docs(backlog): log item #34 sixth observation` passes. Anchor the description with a non-`#` token first when the natural phrasing would put a backlog reference at the start.
- Branch names follow `<type>/<slug>` where `<type>` is from `cchk.toml`'s `allow_branch_types` and `<slug>` is lowercase-alphanumeric + hyphens. Note two asymmetries with commit subjects: (1) the branch prefix `feature/` is the long form while the Conventional Commits subject tag `feat:` is the short form; (2) `allow_branch_types` is a **strict subset** of `allow_commit_types` — `docs/`, `style/`, `refactor/`, `perf/`, `test/`, `build/`, `ci/`, `revert/` are valid commit types but NOT valid branch prefixes. Use `chore/` for those categories of work. Examples: `feature/v0.2-linux`, `fix/v0.2-followups-2-5`, `fix/v0.2-followup-1-linux-xdg-paths`.
- Commit subjects follow Conventional Commits: `<type>(<scope>): <description>` where `<type>` is from `cchk.toml`'s `allow_commit_types`. Scope tags mirror package names or doc categories, not ticket numbers. `!` before the colon — or a `BREAKING CHANGE:` footer — signals a breaking change.
- **commit-check rejects multi-element scopes** (comma-separated): `fix(rules,daemon): ...` fails. Use a single scope (the most-affected module) or omit the scope.
- Split commits along revertability lines: a code change and a doc-only backlog update belong in separate commits because they could plausibly be reverted at different times. PR #4 is the template — one `feat` commit for the behavior change, one `docs` commit for the new follow-up entries.

## Release

- `.github/workflows/test.yml` runs ruff + the portable pytest subset on `ubuntu-latest`, `windows-latest`, and `macos-latest` for every push/PR. Each platform leg additionally runs its own `pytest -m <platform>_only` step.
- Push tag `v*` → `.github/workflows/release.yml` builds wheel + `dbxignore.exe` / `dbxignored.exe` (Windows, via `pyinstaller/dbxignore.spec`) + `dbxignore` / `dbxignored` arm64 Mach-O (macOS, via `pyinstaller/dbxignore-macos.spec`) and publishes to two destinations: GitHub Release (auto, tag-gated; pre-release flag sourced from the `classify-tag` job) and PyPI (only wheel/sdist ship to PyPI — Mach-O and `.exe` binaries are GitHub-Release-only). `hatch-vcs` derives the version from the tag — no manual `pyproject.toml` bump needed. PyPI uses Trusted Publishing; no API token secret stored.
- A `classify-tag` job is the single source of truth for "is this a pre-release tag?" — it parses the tag in shell against PEP 440 patterns (`aN`, `bN`, `rcN`, `.devN`) and emits an `is_prerelease` boolean output. `publish-github`'s `prerelease:` flag and `publish-pypi`'s `if:` clause both read from that output, so they cannot disagree. **Do NOT use `contains(github.ref, '-a')`-style checks** — PEP 440 has no hyphen separator (alphas are `0.4.0a1`, not `0.4.0-a1`), so substring checks against hyphenated SemVer-style markers always evaluate False against the tags `hatch-vcs` produces.
- PyPI publish has two safety layers: (1) PEP 440 pre-release tags skip the `publish-pypi` job entirely via the `classify-tag`-driven `if:` clause — they're never queued, so there's nothing to accidentally approve; (2) release tags enter the job and pause at the `pypi` GitHub environment gate, which requires a maintainer approval click in the GitHub UI before the OIDC upload fires. The two layers cover different threat models: the `if:` exclusion prevents pre-releases from hitting PyPI even if someone clicks approve; the environment gate prevents release tags from auto-publishing without human review. To pre-release without PyPI: tag with PEP 440 pre-release suffix (`v<X.Y.Z>a<N>`, `v<X.Y.Z>rc<N>`, `v<X.Y.Z>.dev<N>`). To release: tag `v<X.Y.Z>` and approve at the gate. Note: `.postN` (post-release) is intentionally NOT a pre-release — post-release tags publish to PyPI.
- Before tagging, sanity-check wheel metadata: `uv build && unzip -p dist/*.whl '*.dist-info/METADATA' | head` — confirms `Name: dbxignore` and the expected `Version:` are in the wheel before a misnamed upload hits PyPI (immutable; yank-only recovery).
- `CHANGELOG.md` at repo root follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/): new entries accrue under an `[Unreleased]` heading (add it at the top when the first post-release change lands) and roll into a version heading with its release date when the tag goes out. Hand-crafted per-version release bodies live under `docs/release-notes/v<X.Y.Z>.md` for use with `gh release edit v<X.Y.Z> --notes-file docs/release-notes/v<X.Y.Z>.md` after the workflow publishes.
- This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Pre-1.0, breaking changes ride MINOR bumps with explicit **Breaking** callouts in the CHANGELOG — v0.2.0 introduced two (broadened `--purge`, changed `explain` format). Post-1.0, breaking changes will bump MAJOR.
- Watch a long-running release.yml run via `gh run watch <run-id> --exit-status` invoked with `run_in_background: true`. The agent gets a notification when the run hits a terminal state (success/failure/cancelled). Don't poll via Bash sleep loops — the harness blocks long leading sleeps for exactly this reason.
- Third-party GitHub Actions in `.github/workflows/*.yml` are pinned to **40-char commit SHAs** with a trailing `# v<X>` comment naming the resolved tag — `uses: actions/checkout@93cb6efe18208431cddfb8368fd83d5badbf9bfd # v5`. Tags are mutable (the action's owner can re-point them at any commit); SHAs are content-addressed. Resolve a tag's SHA via `gh api repos/<owner>/<repo>/commits/<tag> --jq .sha`. `.github/dependabot.yml` runs `package-ecosystem: github-actions` weekly with `commit-message.prefix: "ci"` + `include: "scope"` so auto-PRs land as `ci(deps): bump …` and pass `cchk.toml`'s Conventional Commits gate; minor+patch bumps are grouped into one weekly PR via `actions-minor-patch`, major bumps stay individual for review. New workflows MUST follow the SHA-pin convention from day one — a tag-pinned action in a new file silently degrades the trust posture across the project.
- Workflow `if:` filters that gate on "is this a bot's PR?" must use **`github.event.pull_request.user.login`**, not `github.actor`. `github.actor` is whoever triggered the *current* run — when a human clicks "Update branch" on a Dependabot PR, `github.actor` becomes the human and a `!endsWith(github.actor, '[bot]')` filter no longer fires, so the gated step runs anyway. `pull_request.user.login` is the PR *author* and stays `dependabot[bot]` across re-runs. Different intent than "did a bot push the latest commit?" (which `claude-code-review.yml:25` correctly answers via `github.actor`); use the field that matches the question.
- **`workflows` is NOT a valid `GITHUB_TOKEN` permissions scope.** GitHub refuses any `git push` from the default `GITHUB_TOKEN` that modifies files under `.github/workflows/**`, regardless of what's in the workflow's `permissions:` block. Workflow-file pushes need either a PAT with `workflow` scope or a GitHub App installation token with `workflows` write set at the App level.
- `codex-followup` auto-fix workflow retired in PR #163 (2026-05-08). Manual `@codex review` summon still triggers a review pass; replies to review comments are maintainer-driven now.

## Manual test scripts

`scripts/manual-test-{ubuntu-vps,macos,windows}.{sh,ps1}` are end-to-end smoke tests requiring a live Dropbox install. CI does not run them — they're for release-prep verification of behavior the unit tests can't reach (real markers on a real synced tree, real service-manager registration, real cross-process daemon interaction). All three follow the same phase numbering: 0 (preflight), 1 (verify Dropbox), 2 (install dbxignore), 3 (CLI surface), 4a–4f (reconcile), 4.5 / 4g–4n (extended CLI), 5 (daemon), 6 (uninstall), 7 (cleanup).

- **When a PR adds or changes user-visible CLI surface** — new subcommand, new flag with marker/Dropbox side effects (`--yes`, `--dry-run`, `--purge`, `--force`), new stderr warning, or behavior change unit tests can't fully cover — extend all three scripts under Phase 4.5 (`phase_extended_cli` in bash, `Test-ExtendedCli` in PowerShell). Each case carries an inline `# 4X — <description> (PR #NNN)` provenance comment so future contributors can map test cases back to their PRs. Phase 4.5 was backfilled in PR #114 (~190 LOC per bash script + a 734 LOC new Windows script) after coverage gaps from PRs #100, #102, #103, #107, #108 surfaced — catching at PR-time is ~30 LOC per script per case; backlog catch-up is significantly more expensive. The two bash scripts source `scripts/_phase_extended_cli.sh` for the shared Phase 4.5 body (extracted in PR #143, resolving backlog item #75); the PowerShell script can't share the helper and is still hand-synced. New Phase 4.5 cases land in the bash helper plus `manual-test-windows.ps1`'s `Test-ExtendedCli`.
- **Cross-platform behavioral divergences** branch per-script with inline comments. Canonical examples: 4e symlinks (Linux refuses `user.*` xattrs with EPERM → WARNING; macOS allows via the NOFOLLOW path; Windows attaches ADS to the reparse point → marks silently). Don't paper over these — the manual scripts are where the divergences are surfaced for testers, and each platform script inverts the assertion accordingly.
- **PowerShell scripts** (`scripts/manual-test-windows.ps1`) start with `#requires -Version 7.0`. See the related Gotchas-section bullet about `Set-Content -Encoding utf8` BOM divergence between PS 5.1 and PS 7+.

## Docs

Specs and plans are kept side-by-side under `docs/superpowers/{specs,plans}/`, named `<YYYY-MM-DD>-<slug>.md`. Per-version release bodies live under `docs/release-notes/v<X.Y.Z>.md`. Internal architectural deep-dives and archived gotchas live under `docs/internals/`. The central backlog and resolved-items log lives at `BACKLOG.md` at the repo root.

- Current: v0.4 macOS port — `specs/2026-04-26-v0.4-macos-design.md` + `plans/2026-04-27-v0.4-macos-implementation.md`. Prior version specs/plans live alongside in `docs/superpowers/` (find via `ls docs/superpowers/{specs,plans}/`).
- **Backlog conventions:** open items, planned work, and resolved-item history live in `BACKLOG.md`. New items append at the bottom (`## <N>. <title>`) with body, fix candidates, urgency, and a `Touches:` file list. Resolved items get an inline `**Status: RESOLVED <date> (PR #<N>).**` marker AND an entry in the bottom `## Status > Resolved` section. The Status section also maintains an at-a-glance Open list and Provenance notes (how items were sourced).
- A `**Validated <date> (v<X.Y.Z>).**` paragraph in a backlog item's body is distinct from the `Status: RESOLVED` line: RESOLVED = code merged; Validated = user-observable effect confirmed in the wild (e.g. beta-tester pass against a specific tag). Useful for items that ship across alpha cycles where merge and validation happen in different versions. Item #33 is the canonical example.

## How to run checks

Use these commands before claiming a change is safe:

```bash
uv run mypy .
uv run ruff check . --fix
uv run ruff check .
uv run ruff format .
uv run python -m pytest
```

If a tool is not installed, say so and continue with the available checks.

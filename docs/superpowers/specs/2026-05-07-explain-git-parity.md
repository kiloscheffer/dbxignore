# dbxignore — `explain` exit codes, `check-ignore` alias, README parity table

**Date:** 2026-05-07
**Status:** Accepted. Implementation plan to follow.
**Resolves:** [BACKLOG.md items #70, #71, #72](../../../BACKLOG.md).

## Problem

Three small CLI/docs gaps for users coming from `git`:

1. **#70 — `dbxignore explain` exits `0` regardless of verdict.** The diagnostic counterpart to `git check-ignore -v` doesn't set its exit code by ignored/not-ignored verdict, so shell scripts can't write `if dbxignore explain X; then ...` the way `if git check-ignore X; then ...` works. Callers must parse stdout text to extract the verdict.
2. **#71 — Discoverability gap on the verb name.** Users coming from git look for the diagnostic-equivalent verb at `check-ignore`. dbxignore calls it `explain`. New users must discover the rename via `--help` rather than recognize the verb directly.
3. **#72 — Deceptively-similar verbs without parity documentation.** `dbxignore clear` is the most consequential example: shaped like `git rm --cached` (remove from index) but inverted in consequence (clears Dropbox markers → cloud upload of previously-ignored paths, propagated cross-device).

The three are tightly bundled. Adding the `check-ignore` alias (#71) without verdict-driven exit codes (#70) ships only half of git's `check-ignore` shape; documenting parity (#72) without the alias misses the discoverability win the table earns. One PR lands all three.

## Scope

**In scope:**

- Verdict-driven exit codes on `explain`: `0` (ignored), `1` (not ignored), `2` (fatal — no Dropbox roots discovered, preserving project convention).
- New `--quiet`/`-q` flag on `explain` matching `git check-ignore -q`: suppresses stdout (the rule listing AND the `no match for X` line); stderr stays for fatal errors; exit code unchanged.
- New `dbxignore check-ignore` command: an additive alias for `explain`. Identical behavior, output, and exit codes; distinct docstring framing it as a git-parity verb.
- New `### Command parity with git` README subsection inside `## Commands`, after the existing detail subsections. Markdown table mapping all 10 dbxignore commands to their closest git counterparts (or "(none)"); a callout for the `clear` ↔ `git rm --cached` semantic-inversion warning.
- Test additions in `tests/test_cli_status_list_explain.py`: 8 cases covering the verdict / quiet / alias surface.

**Out of scope:**

- **The other 24 `sys.exit(2)` callsites in `cli.py`.** Project convention is "exit 2 for any user-facing fatal error"; only the `explain` and `check-ignore` paths gain verdict-driven exit codes. A future "exit-code modernization" PR could renumber the project's fatal code (e.g. to git's `128`); not in this scope.
- **`--verbose`/`-v` flag.** git's `check-ignore -v` adds rule provenance to the output; dbxignore's `explain` already shows that by default. The parity-table notes column documents this equivalence.
- **`--non-matching`/`--no-index`/`--stdin` modes from git.** YAGNI — no observed demand.
- **CHANGELOG entry, version bump, or deprecation shim.** The behavior change (`explain` now exits `1` for not-ignored where it exited `0` before) is observable but the prior exit codes were never documented as a contract. Pre-1.0 (currently 0.4.x) per CLAUDE.md's SemVer note: breaking changes ride MINOR bumps; the maintainer adds an `[Unreleased] > Breaking` CHANGELOG entry at the next tag-bump PR, not speculatively here.
- **Backwards-compat shim.** No alternative way to suppress the new exit-code behavior. Users scripting against the old "always 0" need to update their checks (a script that did `dbxignore explain X` and relied on it always exiting 0 was probably broken anyway — there was no way to distinguish ignored from not-ignored).

## User contract

Before:

```
$ dbxignore explain node_modules/foo
.dropboxignore:1  node_modules/
$ echo $?
0
$ dbxignore explain readme.md
no match for readme.md
$ echo $?
0
```

The two cases are indistinguishable by exit code; scripts must grep stdout.

After:

```
$ dbxignore explain node_modules/foo
.dropboxignore:1  node_modules/
$ echo $?
0
$ dbxignore explain readme.md
no match for readme.md
$ echo $?
1
$ dbxignore check-ignore --quiet node_modules/foo
$ echo $?
0
$ dbxignore check-ignore --quiet readme.md
$ echo $?
1
```

The verdict-driven exit codes match `git check-ignore`'s shape; `if dbxignore check-ignore --quiet X; then ...` works in scripts.

## Design

### Architecture

One new private helper, two thin wrappers, one new flag, one README subsection.

```
src/dbxignore/cli.py
  + _explain(path, *, quiet) -> int       # shared body returning exit code
  ~ explain(path, quiet)                   # thin wrapper, adds --quiet flag, sys.exit(_explain(...))
  + check_ignore(path, quiet)              # new wrapper with distinct docstring

tests/test_cli_status_list_explain.py
  + 8 new test cases (verdict / quiet / alias / help-text)

README.md
  + ### Command parity with git            # new subsection inside ## Commands
```

### `_explain` — behavior

```python
def _explain(path: Path, *, quiet: bool) -> int:
    """Shared body for `explain` and `check-ignore`. Returns exit code (0/1/2).

    Exit codes:
      0 — path is ignored (cache.match returns True)
      1 — path is not ignored (cache.match returns False; covers no-match
          AND only-dropped-matches cases)
      2 — fatal: no Dropbox roots discovered (preserves project convention
          for fatal errors; see other cli.py callsites)

    `quiet` suppresses stdout (the rule listing and the `no match for X`
    line). stderr is preserved for the fatal "No Dropbox roots found." line —
    matches `git check-ignore -q` semantics.
    """
    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found.", err=True)
        return 2

    cache = _load_cache(discovered)
    resolved = path.resolve()
    is_ignored = cache.match(resolved)

    if not quiet:
        matches = cache.explain(resolved)
        if not matches:
            click.echo(f"no match for {path}")
        else:
            conflicts_by_drop = {(c.dropped_source, c.dropped_line): c for c in cache.conflicts()}
            for m in matches:
                loc = _format_ignore_file_loc(m.ignore_file, discovered)
                prefix = "[dropped]  " if m.is_dropped else ""
                raw = m.pattern.strip()
                suffix = ""
                if m.is_dropped:
                    c = conflicts_by_drop.get((m.ignore_file, m.line))
                    if c is not None:
                        masking_loc = _format_ignore_file_loc(c.masking_source, discovered)
                        suffix = f"  (masked by {masking_loc}:{c.masking_line})"
                click.echo(f"{loc}:{m.line}  {prefix}{raw}{suffix}")

    return 0 if is_ignored else 1
```

**Verdict source decision**: `cache.match(resolved)` is the canonical post-drops final boolean. The implementation does NOT re-derive the verdict from `cache.explain()`'s match list because last-match-wins with negations is fragile to derive correctly from a list (you'd have to filter dropped, then take the last non-dropped match's polarity, then handle the no-non-dropped case). Calling `cache.match()` adds one extra rule-walk per `explain` invocation; cost is negligible (single path, not a sweep).

**Quiet semantics**: matches `git check-ignore -q` exactly. stdout suppressed; stderr unchanged. The `is_ignored` boolean is computed regardless of quiet, so the exit code is always correct.

### `cli.explain` — wrapper

```python
@main.command()
@click.argument("path", type=click.Path(exists=False, path_type=Path))
@click.option(
    "--quiet", "-q", is_flag=True,
    help="Suppress stdout; only set exit code (parity with `git check-ignore -q`).",
)
def explain(path: Path, quiet: bool) -> None:
    """Show which .dropboxignore rule (if any) matches the path.

    Dropped negations (rules that can't take effect because an ancestor
    directory is ignored) appear prefixed with `[dropped]` and a pointer
    to the masking rule. See README §"Negations and Dropbox's ignore
    inheritance" for why.

    Exit codes:
      0 — path is ignored
      1 — path is not ignored (no matching rule, or only dropped negations)
      2 — fatal (no Dropbox roots discovered)
    """
    sys.exit(_explain(path, quiet=quiet))
```

### `cli.check_ignore` — alias wrapper

```python
@main.command(name="check-ignore")
@click.argument("path", type=click.Path(exists=False, path_type=Path))
@click.option(
    "--quiet", "-q", is_flag=True,
    help="Suppress stdout; only set exit code (parity with `git check-ignore -q`).",
)
def check_ignore(path: Path, quiet: bool) -> None:
    """Alias of `explain`, named for git-fluent users (parity with `git check-ignore -v`).

    Identical behavior, output, and exit codes to `explain`. The output format
    follows dbxignore's annotated-rule shape (each match shows ignore_file:line
    + pattern, with `[dropped]` annotations). Use `dbxignore explain` if you
    want the verb dbxignore documents in its own README; use `check-ignore`
    if your muscle memory is git's.
    """
    sys.exit(_explain(path, quiet=quiet))
```

The two functions are deliberately separate (per Q2=B) rather than `main.add_command(explain, name="check-ignore")`. Cost is ~6 lines of decorator-block duplication; benefit is a more deliberate `--help` for git-fluent users (the alias's docstring frames it explicitly as the alias-of and the git-parity verb, where a single shared docstring would have to be neutral on which is the "primary" name).

### README subsection

New `### Command parity with git` subsection inside `## Commands`, placed after the existing verb-detail subsections (Applying rules / Clearing all markers / Status-bar integration) and before `## Behaviour`. Content:

````markdown
### Command parity with git

For users coming from `git`, this table maps each `dbxignore` command to its
closest git counterpart. Some align cleanly; others have a deceptively-similar
git verb with materially different consequences.

| `dbxignore`     | git counterpart            | Notes                                                          |
| --------------- | -------------------------- | -------------------------------------------------------------- |
| `apply`         | (none)                     | Reconciles markers from `.dropboxignore`.                      |
| `check-ignore`  | `git check-ignore -v`      | Alias of `explain`. `--quiet` matches git's flag.              |
| `clear`         | (see callout below)        | **NOT** `git rm --cached`-shaped.                              |
| `daemon`        | (none)                     | dbxignore-specific watcher + hourly sweep.                     |
| `explain`       | `git check-ignore -v`      | Same diagnostic question; `--quiet` and exit codes match.      |
| `generate`      | (none)                     | Translates a `.gitignore` into a `.dropboxignore`.             |
| `init`          | `git init` (loosely)       | Scaffolds a starter `.dropboxignore`, not a repository.        |
| `install`       | (none)                     | Registers the daemon with the platform service manager.        |
| `list`          | (none)                     | Lists every path currently bearing the Dropbox ignore marker.  |
| `status`        | `git status` (loosely)     | Shows daemon state, last sweep, marker counts, conflicts.      |
| `uninstall`     | (none)                     | Removes the daemon registration; `--purge` also clears markers.|

> **`clear` is NOT `git rm --cached`-shaped.** `git rm --cached` removes a path
> from the git index without touching the working tree (cheap, local-only).
> `dbxignore clear` removes the Dropbox ignore markers, which causes Dropbox
> to **upload previously-ignored paths to the cloud** (potentially gigabytes
> for a `node_modules`-class subtree) and propagate them to other linked
> devices. The `--yes` confirmation prompt and `--dry-run` preview exist
> specifically because of this divergence.
````

The callout for `clear` is the load-bearing safety note. Other "(loosely)" mappings (`init`, `status`) don't get callouts — the divergence is mild, not destructive.

## Test plan

Eight new tests in `tests/test_cli_status_list_explain.py`. Most are CliRunner one-liners; the dropped-negation case needs a small `.dropboxignore` setup that follows the existing fixture pattern in the file.

### Verdict tests

1. **`test_explain_exits_0_when_ignored`** — tree with `.dropboxignore` containing `node_modules/`, create `node_modules/foo`. Call `explain node_modules/foo`. Assert `result.exit_code == 0`, the matching rule line in `result.stdout`.

2. **`test_explain_exits_1_when_not_ignored`** — same tree, call `explain readme.md`. Assert `result.exit_code == 1`, `no match for ` in `result.stdout`.

3. **`test_explain_exits_1_when_only_dropped_matches`** — tree where a `!node_modules/keep/` negation is dropped under an ancestor's `node_modules/`. Call `explain node_modules/keep/foo`. Assert `result.exit_code == 1`, AND the dropped match line WITH `[dropped]` annotation in `result.stdout`. Pins that `[dropped]`-only matches still report not-ignored AND that the `[dropped]` annotation is preserved on stdout (the dbxignore-specific signal stays on stdout, just doesn't change the exit code).

4. **`test_explain_exits_2_when_no_dropbox_roots`** — `monkeypatch.setattr(cli, "_discover_roots", lambda: [])`. Call `explain anything`. Assert `result.exit_code == 2`, `No Dropbox roots found.` in `result.stderr`. Pins the project convention that fatal errors stay at `2`.

### Quiet tests

5. **`test_explain_quiet_suppresses_stdout`** — ignored path with `--quiet`. Assert `result.exit_code == 0`, `result.stdout == ""`.

6. **`test_explain_quiet_keeps_stderr_for_fatal`** — no-roots case with `--quiet`. Assert `result.exit_code == 2`, `result.stdout == ""`, `No Dropbox roots found.` STILL in `result.stderr`. Pins git-shape `--quiet` semantics (stderr-on-fatal preserved).

### Alias tests

7. **`test_check_ignore_alias_identical_to_explain`** — same path, run both via CliRunner. Assert `explain.stdout == check_ignore.stdout` AND `explain.exit_code == check_ignore.exit_code`. Pins the alias's identical-behavior contract for both ignored and not-ignored cases (parametrize over both).

8. **`test_check_ignore_help_distinguishes_from_explain`** — CliRunner `dbxignore explain --help` vs `dbxignore check-ignore --help`; assert the alias-framing line (`Alias of \`explain\`,`) appears in `check-ignore --help` but NOT in `explain --help`. Pins Q2=B's deliberate-distinct-docstring decision against a future `add_command`-style refactor that would silently collapse them.

### Coverage gap acknowledged

These tests pin the verdict, the quiet semantics, and the alias's behavioral parity. They do NOT pin every existing assertion in the existing `test_cli_status_list_explain.py` continuing to pass — that's a regression check, run via the full daemon test suite at commit time.

## Risks and edge cases

- **Behavior change for any caller scripting against `explain`'s exit code today.** The not-ignored case now exits `1` where it exited `0` before. Pre-1.0 per CLAUDE.md's SemVer note; breaking changes ride MINOR bumps. The maintainer adds an `[Unreleased] > Breaking` CHANGELOG entry at the next tag-bump PR. The implementation does not include a backwards-compat shim — there's no clean way to express "old behavior" without a flag, and any flag added now would need a deprecation cycle to remove later.

- **`cache.match()` and `cache.explain()` independent walks.** Non-quiet invocations call both (`cache.match` for the verdict + `cache.explain` for the rule listing) — one extra walk vs. before. Quiet invocations call only `cache.match`. For a single path against an in-memory `RuleCache`, cost is in the microseconds — verified by reading `rules.RuleCache.match`'s body (it's a list-of-pattern-Pattern walk, not a filesystem operation). Not worth deriving the verdict from explain's list to save the walk; last-match-wins with negations is fragile to derive from a list.

- **The `[dropped]` annotation case.** A path with ONLY dropped negations matches `cache.explain` (returns the dropped rules) but `cache.match` returns False. The exit code is `1` (not ignored). This is correct: `[dropped]` annotation is the dbxignore-specific signal, lives on stdout, doesn't influence the exit code. Test 3 pins this.

- **Quiet + fatal interaction.** `git check-ignore -q` keeps stderr; we match. The fatal `click.echo("...", err=True)` runs unconditionally before the `quiet` branch, which is intentional (caller still gets the error message even in scripted contexts). Test 6 pins this.

- **`check-ignore`'s help-text stability.** Tests 7 and 8 jointly pin the contract: identical exit codes (test 7), distinct docstrings (test 8). A future `add_command` refactor that collapsed them would fail test 8 loudly.

- **README table maintenance.** Adding a new dbxignore command in the future requires a new row in the parity table. The existing `## Commands` section already enumerates each verb in detail subsections, so the table maintenance is mechanically forced (a missing row is visible in the diff). Filed as a soft convention, not enforced by tests.

## Backlog interactions

- **Resolves #70, #71, #72.** Three inline `**Status: RESOLVED <date> (PR #<N>).**` markers in each item's body, three entries in the `## Status > Resolved > #### 2026-05-07` section. PR number predicted at backlog-update time via `gh pr list --state all --limit 1` plus `gh issue list --state all --limit 1`.

- **No companion items.** Unlike #52's resolution which deferred companion items #53 / #54, this PR's three items have no architectural follow-ups.

## Implementation notes

- `click.option("--quiet", "-q", is_flag=True)` is the standard click flag declaration; `-q` and `--quiet` are interchangeable on the command line.
- `sys` is already imported in `cli.py`. No new imports required.
- The `cache.match()` API takes a resolved absolute path; `path.resolve()` runs at the boundary (consistent with the existing pattern).
- Click registers `check-ignore` (with hyphen) as the command name; the Python function name `check_ignore` (with underscore) is unrelated to the CLI surface (Click decouples the two).

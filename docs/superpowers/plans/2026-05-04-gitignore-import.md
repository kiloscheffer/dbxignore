# Gitignore Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `dbxignore generate <path>` and `dbxignore apply --from-gitignore <path>` so users can translate or apply a `.gitignore` (or any nominated file) without hand-authoring a parallel `.dropboxignore`.

**Architecture:** Two new entry points share one new internal seam — `RuleCache.load_external(source, mount_at)` plus an `as_path` kwarg on the existing private `RuleCache._load_file`. The CLI commands sit thin on top, delegating reading/parsing/caching to `rules.py` and walking to the existing `reconcile_subtree`. No new modules.

**Tech Stack:** Python 3.12+, Click (CLI), pathspec (rule parser), pytest (CliRunner), uv (toolchain), ruff (lint).

**Spec:** [`docs/superpowers/specs/2026-05-04-gitignore-import.md`](../specs/2026-05-04-gitignore-import.md).

---

## File Structure

**Files created:**

- `tests/test_rules_load_external.py` — unit tests for the `RuleCache.load_external` seam (3 tests).
- `tests/test_cli_generate.py` — integration tests for `dbxignore generate` (10 tests).

**Files modified:**

- `src/dbxignore/rules.py` — add `as_path` kwarg to `_load_file`; add `load_external` method to `RuleCache`.
- `src/dbxignore/cli.py` — add `generate` Click subcommand; add `--from-gitignore` option to `apply`; add `_resolve_gitignore_arg` and `_validate_rule_source` helpers.
- `tests/test_cli_apply.py` — add 5 tests for `apply --from-gitignore`.
- `README.md` — add §"Using `.gitignore` rules" section.

**Files NOT touched (and why):**

- `src/dbxignore/reconcile.py` — `apply --from-gitignore` reuses `reconcile_subtree` unchanged.
- `src/dbxignore/markers.py`, `src/dbxignore/_backends/*` — no platform-specific behavior.
- `src/dbxignore/daemon.py` — `generate` produces a `.dropboxignore` the daemon already knows how to react to via watchdog. `apply --from-gitignore` is a one-shot CLI run.
- `src/dbxignore/state.py` — neither verb writes state.

---

## Task 1: `rules.py` seam — `_load_file` `as_path` kwarg + `RuleCache.load_external`

**Files:**

- Create: `tests/test_rules_load_external.py`
- Modify: `src/dbxignore/rules.py:97-252` (RuleCache class — add `load_external` method; modify `_load_file` signature)

**Why this task first:** Both new CLI commands depend on `RuleCache.load_external`. Building the seam test-first lets us verify the cache-key-rewrite contract in isolation before any CLI surface depends on it.

- [ ] **Step 1.1: Write the three failing tests for `load_external`.**

Create `tests/test_rules_load_external.py`:

```python
"""Unit tests for RuleCache.load_external — the seam used by
``dbxignore apply --from-gitignore``. The seam loads an arbitrary file's
lines as if it were a .dropboxignore at a specified mount directory.
"""
from __future__ import annotations

import logging

from dbxignore.rules import IGNORE_FILENAME, RuleCache


def test_load_external_match_succeeds(tmp_path):
    """Rules from a non-.dropboxignore source still drive match() correctly."""
    source = tmp_path / "my.gitignore"
    source.write_text("build/\n", encoding="utf-8")
    mount_at = tmp_path
    (mount_at / "build").mkdir()

    cache = RuleCache()
    cache.load_external(source, mount_at)

    assert cache.match((mount_at / "build").resolve()) is True


def test_load_external_cache_key_is_mount_path(tmp_path):
    """The cache stores rules under <mount_at>/.dropboxignore, not the source path."""
    source = tmp_path / "elsewhere.gitignore"
    source.write_text("*.log\n", encoding="utf-8")

    mount_at = tmp_path / "project"
    mount_at.mkdir()

    cache = RuleCache()
    cache.load_external(source, mount_at)

    expected_key = (mount_at / IGNORE_FILENAME).resolve()
    assert expected_key in cache._rules
    assert source.resolve() not in cache._rules


def test_load_external_unreadable_source_logs_warning_no_raise(tmp_path, caplog):
    """Missing/unreadable source surfaces as a logged warning per
    _load_file's existing contract — load_external itself does not raise."""
    source = tmp_path / "does-not-exist"  # never created
    mount_at = tmp_path

    cache = RuleCache()
    with caplog.at_level(logging.WARNING, logger="dbxignore.rules"):
        cache.load_external(source, mount_at)  # must not raise

    assert "Could not read" in caplog.text
```

- [ ] **Step 1.2: Run the tests — they must fail.**

```
uv run pytest tests/test_rules_load_external.py -v
```

Expected: all three fail with `AttributeError: 'RuleCache' object has no attribute 'load_external'`.

- [ ] **Step 1.3: Modify `_load_file` to accept `as_path` kwarg.**

In `src/dbxignore/rules.py`, change `_load_file`'s signature and the cache-key line. Locate the existing method (around line 213):

```python
    def _load_file(
        self, ignore_file: Path, *, st: os.stat_result | None = None
    ) -> None:
        try:
            lines = ignore_file.read_text(encoding="utf-8").splitlines()
            if st is None:
                st = ignore_file.stat()
        except OSError as exc:
            logger.warning("Could not read %s: %s", ignore_file, exc)
            return
        try:
            spec = _build_spec(lines)
        except (ValueError, TypeError, re.error) as exc:
            logger.warning("Invalid .dropboxignore at %s: %s", ignore_file, exc)
            return
        self._rules[ignore_file.resolve()] = _LoadedRules(
            lines=lines,
            entries=_build_entries(lines, spec),
            mtime_ns=st.st_mtime_ns,
            size=st.st_size,
        )
```

Replace with:

```python
    def _load_file(
        self,
        ignore_file: Path,
        *,
        st: os.stat_result | None = None,
        as_path: Path | None = None,
    ) -> None:
        """Read and parse ``ignore_file`` into the cache.

        ``as_path`` overrides the cache key. When set, the parsed rules are
        stored as if they came from ``as_path`` rather than ``ignore_file``.
        Used by ``load_external`` to mount a non-``.dropboxignore`` source
        at an arbitrary directory; pass ``None`` for the discovery code path
        and the source location is the cache key.
        """
        try:
            lines = ignore_file.read_text(encoding="utf-8").splitlines()
            if st is None:
                st = ignore_file.stat()
        except OSError as exc:
            logger.warning("Could not read %s: %s", ignore_file, exc)
            return
        try:
            spec = _build_spec(lines)
        except (ValueError, TypeError, re.error) as exc:
            logger.warning("Invalid .dropboxignore at %s: %s", ignore_file, exc)
            return
        cache_key = (as_path or ignore_file).resolve()
        self._rules[cache_key] = _LoadedRules(
            lines=lines,
            entries=_build_entries(lines, spec),
            mtime_ns=st.st_mtime_ns,
            size=st.st_size,
        )
```

- [ ] **Step 1.4: Add the `load_external` method to `RuleCache`.**

In `src/dbxignore/rules.py`, insert this method after `remove_file` (around line 144) and before `match` (around line 145). Use the existing module-level `IGNORE_FILENAME` constant:

```python
    def load_external(
        self, source: Path, mount_at: Path, *, log_warnings: bool = True
    ) -> None:
        """Load ``source``'s lines as if it were a .dropboxignore at ``mount_at``.

        Used by ``dbxignore apply --from-gitignore``: rules in ``source`` are
        mounted at ``mount_at`` (which becomes a tracked root for this cache).
        The cache treats them indistinguishably from rules discovered at
        ``mount_at/.dropboxignore``.

        Errors during read or parse log a warning per ``_load_file``'s
        contract and do not raise; callers that need failure to surface as
        a CLI error must validate ``source`` themselves before calling.
        """
        mount_at = mount_at.resolve()
        synthetic_path = mount_at / IGNORE_FILENAME
        with self._lock:
            if mount_at not in self._roots:
                self._roots.append(mount_at)
            self._load_file(source, as_path=synthetic_path)
            self._recompute_conflicts(log_warnings=log_warnings)
```

- [ ] **Step 1.5: Run the tests — they must pass.**

```
uv run pytest tests/test_rules_load_external.py -v
```

Expected: all three pass.

- [ ] **Step 1.6: Run the full rules test suite to confirm nothing regressed.**

```
uv run pytest tests/test_rules.py tests/test_rules_conflicts.py tests/test_reconcile_edges.py -v
```

Expected: all pre-existing tests still pass.

- [ ] **Step 1.7: Run ruff.**

```
uv run ruff check src/dbxignore/rules.py tests/test_rules_load_external.py
```

Expected: no errors.

- [ ] **Step 1.8: Commit.**

```
git add src/dbxignore/rules.py tests/test_rules_load_external.py
git commit -m "feat(rules): add load_external for non-discovery rule sources"
```

---

## Task 2: `cli.generate` — basic command (file/dir args, success message)

**Files:**

- Create: `tests/test_cli_generate.py`
- Modify: `src/dbxignore/cli.py:1-17` (imports), end of file (new command)

**Why this task next:** `generate` is the simpler of the two new verbs (no reconcile, no roots). Building the basic command first establishes the CLI shape and the test pattern; later tasks layer flags on top.

- [ ] **Step 2.1: Write the three failing tests.**

Create `tests/test_cli_generate.py`:

```python
"""Tests for `dbxignore generate` — translates a .gitignore to a
.dropboxignore."""
from __future__ import annotations

from click.testing import CliRunner

from dbxignore import cli


def test_generate_file_arg_writes_sibling(tmp_path):
    source = tmp_path / ".gitignore"
    source.write_text("build/\n*.log\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 0, result.output
    target = tmp_path / ".dropboxignore"
    assert target.read_text(encoding="utf-8") == "build/\n*.log\n"
    assert "wrote 2 rules" in result.output


def test_generate_directory_arg_finds_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("dist/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".dropboxignore").read_text(encoding="utf-8") == "dist/\n"


def test_generate_non_gitignore_filename_works(tmp_path):
    """File arg with a non-.gitignore name is accepted (e.g. .npmignore)."""
    source = tmp_path / ".npmignore"
    source.write_text("dist/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".dropboxignore").exists()
```

- [ ] **Step 2.2: Run the tests — they must fail.**

```
uv run pytest tests/test_cli_generate.py -v
```

Expected: all three fail with `Error: No such command 'generate'.` (Click's not-found message; exit code 2).

- [ ] **Step 2.3: Add the `_resolve_gitignore_arg` helper and `generate` command to `cli.py`.**

In `src/dbxignore/cli.py`, modify the existing imports (at top of file, around line 13):

```python
from dbxignore import markers, reconcile, roots, state
from dbxignore.roots import find_containing
from dbxignore.rules import IGNORE_FILENAME, RuleCache
```

becomes:

```python
from dbxignore import markers, reconcile, roots, rules, state
from dbxignore.roots import find_containing
from dbxignore.rules import IGNORE_FILENAME, RuleCache
```

Add the helper function (place it near the other module-level helpers, after `_load_cache` around line 100):

```python
def _resolve_gitignore_arg(path: Path) -> Path:
    """Resolve a generate/--from-gitignore argument to an actual file.

    Directory → look for ``.gitignore`` inside; file → use as-is. Raises
    ``click.UsageError`` (exit 2) with a CLI-formatted message if the
    resolved path does not exist.
    """
    if path.is_dir():
        candidate = path / ".gitignore"
        if not candidate.exists():
            raise click.UsageError(f"no .gitignore in {path}")
        return candidate
    if not path.exists():
        raise click.UsageError(f"{path} not found")
    return path
```

Add the `generate` command (place it at the end of the file, before `daemon_main` around line 370):

```python
@main.command()
@click.argument("path", type=click.Path(exists=False, path_type=Path))
def generate(path: Path) -> None:
    """Translate a .gitignore (or any nominated file) to a .dropboxignore.

    PATH may be a file or a directory. Directory: looks for .gitignore
    inside. File: used as-is regardless of filename. By default the
    output is written to <dir>/.dropboxignore. See README §"Using
    .gitignore rules" for the gitignore-vs-dbxignore semantic divergence.
    """
    try:
        source = _resolve_gitignore_arg(path)
    except click.UsageError as exc:
        click.echo(f"error: {exc.message}", err=True)
        sys.exit(2)

    text = source.read_text(encoding="utf-8")
    lines = text.splitlines()

    target = source.parent / IGNORE_FILENAME
    target.write_text(text, encoding="utf-8")

    rule_count = sum(
        1 for line in lines
        if line.strip() and not line.strip().startswith("#")
    )
    click.echo(f"wrote {rule_count} rules to {target}")
```

(The minimal version — no `--stdout`, `-o`, `--force`, validation, or out-of-root warning yet. Those layer on in tasks 3-5.)

- [ ] **Step 2.4: Run the tests — they must pass.**

```
uv run pytest tests/test_cli_generate.py -v
```

Expected: all three pass.

- [ ] **Step 2.5: Run ruff.**

```
uv run ruff check src/dbxignore/cli.py tests/test_cli_generate.py
```

Expected: no errors.

- [ ] **Step 2.6: Commit.**

```
git add src/dbxignore/cli.py tests/test_cli_generate.py
git commit -m "feat(cli): add 'generate' subcommand for translating gitignore"
```

---

## Task 3: `cli.generate` — `--stdout`, `-o`, mutex check

**Files:**

- Modify: `tests/test_cli_generate.py` (append 3 tests)
- Modify: `src/dbxignore/cli.py` (`generate` command — add options + branching)

- [ ] **Step 3.1: Append the three failing tests.**

Append to `tests/test_cli_generate.py`:

```python
def test_generate_stdout_writes_no_file(tmp_path):
    source = tmp_path / ".gitignore"
    source.write_text("node_modules/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source), "--stdout"])

    assert result.exit_code == 0, result.output
    assert "node_modules/" in result.output
    assert not (tmp_path / ".dropboxignore").exists()


def test_generate_output_path_redirects(tmp_path):
    source = tmp_path / ".gitignore"
    source.write_text("target/\n", encoding="utf-8")
    custom = tmp_path / "custom" / ".dropboxignore"
    custom.parent.mkdir()

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source), "-o", str(custom)])

    assert result.exit_code == 0, result.output
    assert custom.read_text(encoding="utf-8") == "target/\n"
    assert not (tmp_path / ".dropboxignore").exists()


def test_generate_mutex_stdout_and_o_errors(tmp_path):
    source = tmp_path / ".gitignore"
    source.write_text("*.tmp\n", encoding="utf-8")
    bogus_out = tmp_path / "out"

    runner = CliRunner()
    result = runner.invoke(
        cli.main, ["generate", str(source), "-o", str(bogus_out), "--stdout"]
    )

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output
```

- [ ] **Step 3.2: Run the tests — they must fail.**

```
uv run pytest tests/test_cli_generate.py::test_generate_stdout_writes_no_file tests/test_cli_generate.py::test_generate_output_path_redirects tests/test_cli_generate.py::test_generate_mutex_stdout_and_o_errors -v
```

Expected: all three fail (current `generate` has no `--stdout`/`-o` options; Click rejects unknown options with exit 2).

- [ ] **Step 3.3: Extend the `generate` command with the new options.**

In `src/dbxignore/cli.py`, replace the entire `generate` command with:

```python
@main.command()
@click.argument("path", type=click.Path(exists=False, path_type=Path))
@click.option(
    "-o", "--output", "output",
    type=click.Path(path_type=Path), default=None,
    help="Write to this path instead of <dir>/.dropboxignore.",
)
@click.option(
    "--stdout", is_flag=True,
    help="Write to stdout instead of a file.",
)
def generate(path: Path, output: Path | None, stdout: bool) -> None:
    """Translate a .gitignore (or any nominated file) to a .dropboxignore.

    PATH may be a file or a directory. Directory: looks for .gitignore
    inside. File: used as-is regardless of filename. By default the
    output is written to <dir>/.dropboxignore. See README §"Using
    .gitignore rules" for the gitignore-vs-dbxignore semantic divergence.
    """
    if output is not None and stdout:
        click.echo("error: -o and --stdout are mutually exclusive", err=True)
        sys.exit(2)

    try:
        source = _resolve_gitignore_arg(path)
    except click.UsageError as exc:
        click.echo(f"error: {exc.message}", err=True)
        sys.exit(2)

    text = source.read_text(encoding="utf-8")
    lines = text.splitlines()

    if stdout:
        click.echo(text, nl=False)
        return

    target = output if output is not None else (source.parent / IGNORE_FILENAME)
    target.write_text(text, encoding="utf-8")

    rule_count = sum(
        1 for line in lines
        if line.strip() and not line.strip().startswith("#")
    )
    click.echo(f"wrote {rule_count} rules to {target}")
```

- [ ] **Step 3.4: Run the tests — they must pass.**

```
uv run pytest tests/test_cli_generate.py -v
```

Expected: all six tests pass (3 from Task 2 + 3 from Task 3).

- [ ] **Step 3.5: Run ruff.**

```
uv run ruff check src/dbxignore/cli.py tests/test_cli_generate.py
```

Expected: no errors.

- [ ] **Step 3.6: Commit.**

```
git add src/dbxignore/cli.py tests/test_cli_generate.py
git commit -m "feat(cli): add -o/--stdout output flags to 'generate'"
```

---

## Task 4: `cli.generate` — collision policy (`--force`)

**Files:**

- Modify: `tests/test_cli_generate.py` (append 2 tests)
- Modify: `src/dbxignore/cli.py` (`generate` — add `--force` option + collision check)

- [ ] **Step 4.1: Append the two failing tests.**

Append to `tests/test_cli_generate.py`:

```python
def test_generate_collision_without_force_refuses(tmp_path):
    source = tmp_path / ".gitignore"
    source.write_text("new/\n", encoding="utf-8")
    target = tmp_path / ".dropboxignore"
    target.write_text("existing/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 2
    assert target.read_text(encoding="utf-8") == "existing/\n"  # unchanged
    assert "--force" in result.output


def test_generate_collision_with_force_overwrites(tmp_path):
    source = tmp_path / ".gitignore"
    source.write_text("new/\n", encoding="utf-8")
    target = tmp_path / ".dropboxignore"
    target.write_text("existing/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source), "--force"])

    assert result.exit_code == 0, result.output
    assert target.read_text(encoding="utf-8") == "new/\n"
```

- [ ] **Step 4.2: Run the tests — `without_force` must fail (currently overwrites silently); `with_force` must fail (no `--force` option).**

```
uv run pytest tests/test_cli_generate.py::test_generate_collision_without_force_refuses tests/test_cli_generate.py::test_generate_collision_with_force_overwrites -v
```

Expected: both fail.

- [ ] **Step 4.3: Add the `--force` option and collision check.**

In `src/dbxignore/cli.py`, modify the `generate` command. Add the `--force` decorator after `--stdout`:

```python
@click.option(
    "--force", is_flag=True,
    help="Overwrite an existing .dropboxignore at the target location.",
)
```

Update the function signature:

```python
def generate(path: Path, output: Path | None, stdout: bool, force: bool) -> None:
```

Insert the collision check after the `target = ...` line and before `target.write_text(...)`:

```python
    target = output if output is not None else (source.parent / IGNORE_FILENAME)
    if target.exists() and not force:
        click.echo(
            f"error: {target} exists; pass --force to overwrite or "
            "--stdout to preview",
            err=True,
        )
        sys.exit(2)
    target.write_text(text, encoding="utf-8")
```

- [ ] **Step 4.4: Run the tests — they must pass.**

```
uv run pytest tests/test_cli_generate.py -v
```

Expected: all eight tests pass (3 + 3 + 2).

- [ ] **Step 4.5: Run ruff.**

```
uv run ruff check src/dbxignore/cli.py tests/test_cli_generate.py
```

Expected: no errors.

- [ ] **Step 4.6: Commit.**

```
git add src/dbxignore/cli.py tests/test_cli_generate.py
git commit -m "feat(cli): add --force collision override to 'generate'"
```

---

## Task 5: `cli.generate` — input validation, read-error handling, out-of-root warning

**Files:**

- Modify: `tests/test_cli_generate.py` (append 3 tests)
- Modify: `src/dbxignore/cli.py` (`generate` — read-error handling, validate via `rules._build_spec` before writing, stderr warning when target lives outside Dropbox roots)

**Why this task is separate:** Three closely-related additions that all live in `generate`'s edge-case handling — validation lands BEFORE the write (mirroring `state.write`'s parse-back posture, item #20), read errors get user-facing messages instead of stack traces, and the out-of-root warning helps users notice when the produced file will be invisible to reconcile.

- [ ] **Step 5.1: Append the three failing tests.**

Append to `tests/test_cli_generate.py`:

```python
def test_generate_invalid_pattern_writes_nothing(tmp_path, monkeypatch):
    """If the parser rejects the source, the target file is not created."""
    source = tmp_path / ".gitignore"
    source.write_text("anything\n", encoding="utf-8")

    def fail_build(_lines):
        raise ValueError("test-induced parse failure")

    monkeypatch.setattr(rules_module, "_build_spec", fail_build)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 2
    assert not (tmp_path / ".dropboxignore").exists()
    assert "invalid pattern" in result.output


def test_generate_missing_source_errors(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        cli.main, ["generate", str(tmp_path / "nonexistent.gitignore")]
    )

    assert result.exit_code == 2
    assert "not found" in result.output


def test_generate_target_outside_roots_warns_but_writes(tmp_path, monkeypatch):
    """Resolved target outside any Dropbox root → stderr warning, write proceeds."""
    inside = tmp_path / "dropbox"
    inside.mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [inside])

    outside = tmp_path / "outside"
    outside.mkdir()
    source = outside / ".gitignore"
    source.write_text("build/\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["generate", str(source)])

    assert result.exit_code == 0, result.output
    assert (outside / ".dropboxignore").exists()
    # CliRunner merges stderr into output by default.
    assert "not under any discovered Dropbox root" in result.output
```

Add the `rules_module` import at the top of `tests/test_cli_generate.py`:

```python
from dbxignore import cli
from dbxignore import rules as rules_module
```

- [ ] **Step 5.2: Run the tests — `invalid_pattern` and `target_outside_roots_warns` must fail; `missing_source` passes already.**

```
uv run pytest tests/test_cli_generate.py::test_generate_invalid_pattern_writes_nothing tests/test_cli_generate.py::test_generate_missing_source_errors tests/test_cli_generate.py::test_generate_target_outside_roots_warns_but_writes -v
```

Expected: `invalid_pattern` fails (no validation runs, target IS created); `target_outside_roots_warns` fails (no warning emitted); `missing_source` passes (Task 2's `_resolve_gitignore_arg` already raises UsageError → exit 2 → message contains "not found").

- [ ] **Step 5.3: Add `import re` to `cli.py` if not already present.**

Inspect the import block at the top of `src/dbxignore/cli.py`. The existing block is:

```python
import contextlib
import logging
import os
import sys
from pathlib import Path
```

Add `import re` between `os` and `sys` (alphabetical):

```python
import contextlib
import logging
import os
import re
import sys
from pathlib import Path
```

- [ ] **Step 5.4: Update the `generate` command body with validation, read-error handling, and out-of-root warning.**

In `src/dbxignore/cli.py`, replace the `generate` command body. The decorators stay the same. Replace the function body so it reads:

```python
def generate(path: Path, output: Path | None, stdout: bool, force: bool) -> None:
    """Translate a .gitignore (or any nominated file) to a .dropboxignore.

    PATH may be a file or a directory. Directory: looks for .gitignore
    inside. File: used as-is regardless of filename. By default the
    output is written to <dir>/.dropboxignore. See README §"Using
    .gitignore rules" for the gitignore-vs-dbxignore semantic divergence.
    """
    if output is not None and stdout:
        click.echo("error: -o and --stdout are mutually exclusive", err=True)
        sys.exit(2)

    try:
        source = _resolve_gitignore_arg(path)
    except click.UsageError as exc:
        click.echo(f"error: {exc.message}", err=True)
        sys.exit(2)

    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        click.echo(f"error: {source} is not valid UTF-8", err=True)
        sys.exit(2)
    except OSError as exc:
        click.echo(f"error: cannot read {source}: {exc.strerror}", err=True)
        sys.exit(2)
    lines = text.splitlines()

    try:
        rules._build_spec(lines)
    except (ValueError, TypeError, re.error) as exc:
        click.echo(
            f"error: {source} contains invalid pattern: {exc}",
            err=True,
        )
        sys.exit(2)

    if stdout:
        click.echo(text, nl=False)
        return

    target = output if output is not None else (source.parent / IGNORE_FILENAME)
    if target.exists() and not force:
        click.echo(
            f"error: {target} exists; pass --force to overwrite or "
            "--stdout to preview",
            err=True,
        )
        sys.exit(2)
    try:
        target.write_text(text, encoding="utf-8")
    except OSError as exc:
        click.echo(f"error: cannot write {target}: {exc.strerror}", err=True)
        sys.exit(2)

    discovered = _discover_roots()
    target_resolved = target.resolve()
    if discovered and find_containing(target_resolved, discovered) is None:
        click.echo(
            f"warning: {target} is not under any discovered Dropbox root; "
            "reconcile will not see it",
            err=True,
        )

    rule_count = sum(
        1 for line in lines
        if line.strip() and not line.strip().startswith("#")
    )
    click.echo(f"wrote {rule_count} rules to {target}")
```

- [ ] **Step 5.5: Run the tests — they must pass.**

```
uv run pytest tests/test_cli_generate.py -v
```

Expected: all eleven tests pass (3 + 3 + 2 + 3).

- [ ] **Step 5.6: Run ruff.**

```
uv run ruff check src/dbxignore/cli.py tests/test_cli_generate.py
```

Expected: no errors.

- [ ] **Step 5.7: Commit.**

```
git add src/dbxignore/cli.py tests/test_cli_generate.py
git commit -m "feat(cli): harden 'generate' (parse-validate, read errors, warning)"
```

---

## Task 6: `cli.apply` — `--from-gitignore` flag

**Files:**

- Modify: `tests/test_cli_apply.py` (append 5 tests)
- Modify: `src/dbxignore/cli.py` (`apply` — add `--from-gitignore` option + dispatch helper)

- [ ] **Step 6.1: Append the five failing tests.**

Append to `tests/test_cli_apply.py`:

```python
def test_apply_from_gitignore_mounts_at_dirname(tmp_path, fake_markers, monkeypatch):
    """Rules from gitignore at <root>/sub/.gitignore mount at <root>/sub;
    only paths under <root>/sub are reconciled."""
    sub = tmp_path / "sub"
    sub.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (sub / "build").mkdir()
    (other / "build").mkdir()

    gitignore = sub / ".gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--from-gitignore", str(gitignore)])

    assert result.exit_code == 0, result.output
    assert (sub / "build").resolve() in fake_markers._ignored
    assert (other / "build").resolve() not in fake_markers._ignored


def test_apply_from_gitignore_ignores_existing_dropboxignore(
    tmp_path, fake_markers, monkeypatch
):
    """Existing .dropboxignore in tree does NOT participate in --from-gitignore."""
    (tmp_path / ".dropboxignore").write_text("other/\n", encoding="utf-8")
    (tmp_path / "other").mkdir()
    (tmp_path / "build").mkdir()

    gitignore = tmp_path / "my.gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--from-gitignore", str(gitignore)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "build").resolve() in fake_markers._ignored
    # 'other' rule from existing .dropboxignore is NOT applied
    assert (tmp_path / "other").resolve() not in fake_markers._ignored


def test_apply_from_gitignore_out_of_root_errors(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    gitignore = outside / ".gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")

    inside = tmp_path / "dropbox"
    inside.mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [inside])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--from-gitignore", str(gitignore)])

    assert result.exit_code == 2
    assert "not under any Dropbox root" in result.output


def test_apply_from_gitignore_mutex_with_positional_path(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["apply", str(tmp_path), "--from-gitignore", str(gitignore)],
    )

    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_apply_from_gitignore_directory_arg_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", "--from-gitignore", str(tmp_path)])

    assert result.exit_code == 2
    assert "file path, not a directory" in result.output
```

- [ ] **Step 6.2: Run the tests — they must fail.**

```
uv run pytest tests/test_cli_apply.py -v -k from_gitignore
```

Expected: all five fail with `Error: No such option: --from-gitignore` (Click exit 2 with that message).

- [ ] **Step 6.3: Add `--from-gitignore` option and the `_apply_from_gitignore` helper to `cli.py`.**

In `src/dbxignore/cli.py`, replace the existing `apply` command (around line 116-149) with:

```python
@main.command()
@click.argument("path", required=False, type=click.Path(path_type=Path))
@click.option(
    "--from-gitignore", "from_gitignore",
    type=click.Path(exists=False, path_type=Path), default=None,
    help=(
        "Apply rules loaded from <path> instead of from .dropboxignore "
        "files in the tree. The directory containing <path> must be under "
        "a discovered Dropbox root. See README §\"Using .gitignore rules\"."
    ),
)
def apply(path: Path | None, from_gitignore: Path | None) -> None:
    """Run one reconcile pass (whole Dropbox, or a subtree)."""
    if from_gitignore is not None and path is not None:
        click.echo(
            "error: --from-gitignore and the positional path argument "
            "are mutually exclusive",
            err=True,
        )
        sys.exit(2)

    if from_gitignore is not None:
        _apply_from_gitignore(from_gitignore)
        return

    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found. Is Dropbox installed?", err=True)
        sys.exit(2)

    cache = _load_cache(discovered)

    if path is None:
        targets: list[tuple[Path, Path]] = [(r, r) for r in discovered]
    else:
        resolved = path.resolve()
        matched_root = find_containing(resolved, discovered)
        if matched_root is None:
            click.echo(f"Path {path} is not under any Dropbox root.", err=True)
            sys.exit(2)
        targets = [(matched_root, resolved)]

    total_marked = total_cleared = total_errors = 0
    total_duration = 0.0
    for r, subdir in targets:
        report = reconcile.reconcile_subtree(r, subdir, cache)
        total_marked += report.marked
        total_cleared += report.cleared
        total_errors += len(report.errors)
        total_duration += report.duration_s

    click.echo(
        f"apply: marked={total_marked} cleared={total_cleared} "
        f"errors={total_errors} duration={total_duration:.2f}s"
    )
```

Add the `_apply_from_gitignore` helper. Place it adjacent to `_load_cache`, before the `apply` command (around line 100):

```python
def _apply_from_gitignore(source: Path) -> None:
    """Run a one-shot reconcile using rules loaded from ``source``.

    Rules are mounted at ``dirname(source).resolve()`` and applied only to
    that subtree. Existing .dropboxignore files in the tree do not
    participate in this run. Errors from the source file (missing,
    unreadable, invalid syntax) surface as user-facing CLI errors with
    exit code 2.
    """
    if source.is_dir():
        click.echo(
            "error: --from-gitignore requires a file path, not a directory",
            err=True,
        )
        sys.exit(2)
    if not source.exists():
        click.echo(f"error: {source} not found", err=True)
        sys.exit(2)

    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found. Is Dropbox installed?", err=True)
        sys.exit(2)

    mount_at = source.parent.resolve()
    if find_containing(mount_at, discovered) is None:
        click.echo(
            f"error: {source}'s directory {mount_at} is not under any Dropbox root",
            err=True,
        )
        sys.exit(2)

    # Validate the source can be read + parsed BEFORE running reconcile.
    # load_external swallows OSError/parse failures into log warnings;
    # users running an interactive command want failures to surface here.
    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        click.echo(f"error: {source} is not valid UTF-8", err=True)
        sys.exit(2)
    except OSError as exc:
        click.echo(f"error: cannot read {source}: {exc.strerror}", err=True)
        sys.exit(2)
    try:
        rules._build_spec(text.splitlines())
    except (ValueError, TypeError, re.error) as exc:
        click.echo(f"error: {source} contains invalid pattern: {exc}", err=True)
        sys.exit(2)

    cache = RuleCache()
    cache.load_external(source, mount_at)

    report = reconcile.reconcile_subtree(mount_at, mount_at, cache)
    click.echo(
        f"apply: marked={report.marked} cleared={report.cleared} "
        f"errors={len(report.errors)} duration={report.duration_s:.2f}s"
    )
```

- [ ] **Step 6.4: Run the tests — they must pass.**

```
uv run pytest tests/test_cli_apply.py -v
```

Expected: all seven tests pass (2 pre-existing + 5 new).

- [ ] **Step 6.5: Run ruff.**

```
uv run ruff check src/dbxignore/cli.py tests/test_cli_apply.py
```

Expected: no errors.

- [ ] **Step 6.6: Commit.**

```
git add src/dbxignore/cli.py tests/test_cli_apply.py
git commit -m "feat(cli): add --from-gitignore flag to 'apply'"
```

---

## Task 7: README documentation

**Files:**

- Modify: `README.md` — add a row for `generate` to the `## Commands` table; mention `--from-gitignore` in the `apply` row; add a new `## Using \`.gitignore\` rules` section after the existing `### Negations and Dropbox's ignore inheritance` subsection (which sits inside `## Behaviour`) and before `## Configuration`.

- [ ] **Step 7.1: Update the `## Commands` table.**

In `README.md`, locate the table at the `## Commands` section. Two row changes:

(a) Replace the `dbxignore apply [PATH]` row:

```
| `dbxignore apply [PATH]` | One-shot reconcile of the whole Dropbox (or a subtree). |
```

with:

```
| `dbxignore apply [PATH]` | One-shot reconcile of the whole Dropbox (or a subtree). Pass `--from-gitignore <path>` to load rules from a `.gitignore` instead of `.dropboxignore` files in the tree. |
```

(b) Insert a new row immediately after the `apply` row (before `dbxignore status`):

```
| `dbxignore generate <PATH>` | Translate a `.gitignore` (or any nominated file) to a `.dropboxignore`. `<PATH>` is a file or a directory; default output is `<dir>/.dropboxignore`. Flags: `-o <path>`, `--stdout`, `--force`. |
```

- [ ] **Step 7.2: Add the new top-level section after the `### Negations and Dropbox's ignore inheritance` subsection.**

The new section sits at top-level (`##`), AFTER the `## Behaviour` section's `### Negations...` subsection ends, and BEFORE `## Configuration`. Insert into `README.md`:

```markdown
## Using `.gitignore` rules

A `.gitignore` and a `.dropboxignore` use the same pattern grammar (the same `pathspec` parser handles both). Two CLI verbs let you reuse `.gitignore` rules without hand-copying.

**`dbxignore generate <path>`** writes a `.dropboxignore` derived byte-for-byte from a source file. `<path>` may be a file or a directory; if a directory, `.gitignore` inside it is the source.

```
dbxignore generate ~/Dropbox/myproject/.gitignore
# wrote 4 rules to /home/me/Dropbox/myproject/.dropboxignore

dbxignore generate ~/Dropbox/myproject
# (same — auto-finds .gitignore in the directory)

dbxignore generate ~/Dropbox/myproject/.gitignore --stdout | less
# previews without writing

dbxignore generate ~/Dropbox/myproject/.gitignore --force
# overwrites an existing .dropboxignore
```

The destination path is `<dir>/.dropboxignore` by default; use `-o <path>` to redirect. Without `--force`, an existing `.dropboxignore` at the target is left in place and the command exits non-zero.

**`dbxignore apply --from-gitignore <path>`** runs a one-shot reconcile using rules loaded from `<path>` (without writing a `.dropboxignore`). Rules are mounted at `dirname(<path>)`, which must be under a discovered Dropbox root. Existing `.dropboxignore` files in the tree do not participate in this run.

```
dbxignore apply --from-gitignore ~/Dropbox/myproject/.gitignore
# apply: marked=12 cleared=0 errors=0 duration=0.34s
```

### Semantic divergence between the two files

A `.gitignore` says "git doesn't track this file." A `.dropboxignore` marker tells Dropbox to **stop syncing the path and remove it from cloud sync**. Most rules transfer cleanly (build outputs, dependency caches, IDE state) — but transplanting a `.gitignore` verbatim can mark files for cloud removal that you didn't intend to remove. Review the source file before running `apply --from-gitignore`, or run `generate --stdout` to preview.

### Interaction with the running daemon

If `dbxignored` is running, writing a `.dropboxignore` (whether by `generate`, by hand, or by any other means) triggers a watchdog event. The daemon classifies it as a `RULES` event, debounces, and reconciles the affected root. End state: the markers are written and Dropbox starts removing matched paths from cloud sync. `generate` is therefore not a "preview-only" verb when the daemon is running — use `--stdout` to preview without committing the file.

### Negations

A pattern like `!build/keep/` (re-include a path under an ignored ancestor) is dropped silently; Dropbox's ignored-folder model does not support negation through ignored ancestors. Use `dbxignore explain <path>` to see which rule masked a dropped negation.
```

- [ ] **Step 7.3: Verify the README has the new content in the expected places.**

```
uv run python -c "
import re
t = open('README.md', encoding='utf-8').read()
assert re.search(r'^## Using `\.gitignore` rules', t, re.M), 'top-level section missing'
assert 'dbxignore generate <PATH>' in t, 'commands table row missing'
assert '--from-gitignore' in t, 'apply row mention missing'
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 7.4: Commit.**

```
git add README.md
git commit -m "docs(readme): document gitignore import workflow"
```

---

## Task 8: Final verification

**Files:** none modified; this task is verification only.

- [ ] **Step 8.1: Run the full portable test suite.**

```
uv run pytest -m "not windows_only and not linux_only and not macos_only" -v
```

Expected: every test passes. If any pre-existing test regressed, diagnose before proceeding.

- [ ] **Step 8.2: Run ruff over the whole repo.**

```
uv run ruff check
```

Expected: no errors. If any are new, fix inline (do not commit yet).

- [ ] **Step 8.3: Verify the commit history is clean.**

```
git log --oneline origin/main..HEAD
```

Expected: 8 commits in this order (the spec commit at #1 was made before this plan started executing):
1. `docs(spec): add design for gitignore import (item #56)`
2. `feat(rules): add load_external for non-discovery rule sources`
3. `feat(cli): add 'generate' subcommand for translating gitignore`
4. `feat(cli): add -o/--stdout output flags to 'generate'`
5. `feat(cli): add --force collision override to 'generate'`
6. `feat(cli): harden 'generate' (parse-validate, read errors, warning)`
7. `feat(cli): add --from-gitignore flag to 'apply'`
8. `docs(readme): document gitignore import workflow`

- [ ] **Step 8.4: Verify each commit subject passes commit-check.**

```
git log --pretty=format:'%s%n' origin/main..HEAD | while IFS= read -r msg; do
  [ -z "$msg" ] && continue
  printf '%s\n' "$msg" > /tmp/m.txt
  uv run commit-check --message --no-banner --compact /tmp/m.txt 2>&1 || echo "FAIL: $msg"
done
```

Expected: no `FAIL:` lines. If any subject is too long or has a wrong type tag, amend that commit (or use a soft reset and recommit) BEFORE pushing.

(If `commit-check` is not installed locally, this step is optional — CI will catch issues. Install via `uv tool install pre-commit && pre-commit install --hook-type commit-msg`.)

- [ ] **Step 8.5: File a closing note in BACKLOG.md.**

The `BACKLOG.md` working-tree changes already contain item #56's full body (lines 1202-1222 per the gitStatus snapshot). After the PR merges (NOT in this branch — merge will run last), the marker `**Status: RESOLVED <date> (PR #<N>).**` should be added inline at the top of item #56's body, AND a corresponding entry should be added to the "Resolved (reverse chronological)" section.

Predict the PR number using:

```
gh pr list --state all --limit 1
```

Add 1 to the number returned. (Verify after `gh pr create`.)

For now, this step is a NOTE — do not modify BACKLOG.md until the PR is created (since the PR number is part of the marker text).

- [ ] **Step 8.6: Ready-for-PR summary.**

Verify all 8 commits are present, all tests pass, ruff is clean, then this branch is ready for `gh pr create`. Do NOT push or open the PR in this session unless explicitly asked.

---

## Out-of-scope items (filed for follow-up, not included)

Per the spec's "Out of scope" section — for reference if a reviewer asks "why not also...":

- Auto-discovery of every `.gitignore` under a Dropbox tree (`generate` with no args).
- `--scope <subtree>` flag to narrow `apply --from-gitignore` below the gitignore's directory.
- Live two-way linkage between `.gitignore` and `.dropboxignore` (daemon-watched).
- Filtering or rewriting gitignore patterns on translation.
- Runtime warning banners on either verb (docs-only by deliberate posture choice).
- Daemon-aware advisory output (rejected during brainstorming as fragile inference).

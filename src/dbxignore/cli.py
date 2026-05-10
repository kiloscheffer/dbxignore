"""Command-line interface for dbxignore."""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import os
import re
import sys
from importlib.resources import files
from pathlib import Path

import rich_click as click

from dbxignore import markers, reconcile, roots, rules, state
from dbxignore.roots import find_containing
from dbxignore.rules import IGNORE_FILENAME, RuleCache, is_ignore_filename

click.rich_click.TEXT_MARKUP = "markdown"

logger = logging.getLogger(__name__)


def _discover_roots() -> list[Path]:
    """Resolve roots at the CLI boundary; indirection allows test monkeypatching."""
    return [r.resolve() for r in roots.discover()]


def _format_ignore_file_loc(path: Path, roots: list[Path]) -> str:
    """Return path relative to the nearest root, or absolute if none matches.

    Used by `status` and `explain` to show compact source locations for
    conflicted rules.
    """
    for r in roots:
        try:
            rel = path.relative_to(r)
            return str(rel)
        except ValueError:
            continue
    return str(path)


def _purge_dir(dir_path: Path, patterns: list[str]) -> None:
    """Delete files matching any glob in patterns within dir_path; rmdir if empty."""
    if not dir_path.exists():
        return
    for pattern in patterns:
        for f in dir_path.glob(pattern):
            with contextlib.suppress(FileNotFoundError):
                f.unlink()
    with contextlib.suppress(OSError):
        # Non-empty (user dropped something else in there) — preserve it.
        dir_path.rmdir()


def _purge_local_state() -> None:
    """Delete state.json + daemon.log + rotated backups; rmdir empty dirs.

    Called by `uninstall --purge` after the ignore markers are cleared.
    On Windows + Linux, state and log live in the same dir. On macOS, the
    log dir (~/Library/Logs/dbxignore/) is separate from the state dir
    (~/Library/Application Support/dbxignore/), so we clean both.
    """
    from dbxignore import daemon as daemon_mod

    state_dir = state.user_state_dir()
    if state_dir.exists():
        _purge_dir(
            state_dir,
            patterns=[
                "state.json",
                "state.json.tmp",
                "daemon.lock",
                "daemon.log",
                "daemon.log.*",
                daemon_mod.SLOW_SWEEP_MARKER_NAME,
            ],
        )
        click.echo(f"Cleaned {state_dir}.")

    if sys.platform == "darwin":
        log_dir = state.user_log_dir()
        if log_dir.exists() and log_dir != state_dir:
            _purge_dir(
                log_dir,
                patterns=["daemon.log", "daemon.log.*", "launchd.log"],
            )
            click.echo(f"Cleaned {log_dir}.")


def _load_cache(roots: list[Path]) -> RuleCache:
    """Build a RuleCache loaded from every root, with conflict warnings muted.

    The CLI surfaces conflicts via structured stdout (`status`, `explain`)
    so the per-mutation WARNING records would be a stderr duplicate.
    """
    cache = RuleCache()
    for r in roots:
        cache.load_root(r, log_warnings=False)
    return cache


def _validate_target_under_root(path: Path) -> tuple[Path, Path, list[Path]]:
    """Normalize ``path`` and verify it exists under a discovered Dropbox root.

    Exits 2 with a user-friendly message if any check fails. Returns
    ``(target, root, discovered)`` where ``target`` is the normalized path,
    ``root`` is the Dropbox root containing it, and ``discovered`` is the
    full list of roots. Used by ``ignore`` and ``unignore``; ``apply`` and
    ``clear`` accept paths but don't pre-check existence so they don't share
    this helper.

    ``path.absolute()`` is used instead of ``path.resolve()`` so that symlinks
    are preserved — markers and rules apply to the link itself, not the link's
    target (per the project's symlink invariant). ``os.path.normpath`` folds
    ``..`` and ``.`` segments without following symlinks.

    If the unresolved path is not under any Dropbox root, the validation
    falls back to the resolved path — this handles out-of-Dropbox symlink
    aliases that reach into Dropbox. For example, ``/alias/Dropbox/file``
    where ``/alias`` symlinks to the actual Dropbox root will fail the
    unresolved containment check (lexical prefix mismatch), then succeed
    using the canonical resolved path.

    Symlinked ancestors between ``target`` and ``root`` are rejected: the daemon
    walks with ``followlinks=False`` and would never reconcile a path whose
    ancestor resolves through a symlink, leaving the marker permanently orphaned.
    Operating on the symlink itself is fine; only intermediate symlinks are
    problematic.
    """
    # Path.absolute() preserves symlinks (round-4: the new verbs operate
    # on the link itself, not its target). os.path.normpath folds `..`/`.`.
    target_unresolved = Path(os.path.normpath(path.absolute()))
    # `exists()` follows symlinks — a broken symlink would be rejected even
    # though the link object itself exists and is what dbxignore manages.
    # `os.path.lexists` checks the link itself, so broken symlinks pass.
    # macOS xattrs attach via NOFOLLOW (work on link regardless of target);
    # Linux's ignore-side rejection in cli.ignore catches symlinks anyway.
    if not os.path.lexists(target_unresolved):
        click.echo(f"Path {path} does not exist.", err=True)
        sys.exit(2)
    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found. Is Dropbox installed?", err=True)
        sys.exit(2)
    # Try unresolved first (in-Dropbox symlink-as-target semantic). If that's
    # not under any root, fall back to resolved (handles out-of-Dropbox
    # symlink aliases that reach into Dropbox — `_discover_roots` returns
    # resolved roots, so the unresolved-path lexical-prefix check would
    # reject them otherwise).
    root = find_containing(target_unresolved, discovered)
    if root is not None:
        target = target_unresolved
    else:
        target_resolved = path.resolve()
        root = find_containing(target_resolved, discovered)
        if root is None:
            click.echo(f"Path {path} is not under any Dropbox root.", err=True)
            sys.exit(2)
        target = target_resolved
    # Reject paths whose ancestors (between target and root) are symlinks. The
    # daemon walks with followlinks=False and would never reconcile such a path,
    # leaving the marker permanently orphaned. The target itself being a symlink
    # is fine — that's the intended use case (round-4 fix).
    for ancestor in target.parents:
        if ancestor == root:
            break
        if ancestor.is_symlink():
            click.echo(
                f"error: {path} has a symlinked ancestor {ancestor}; "
                f"the daemon walks with followlinks=False and would never "
                f"reconcile this path. Operate on the symlink itself instead.",
                err=True,
            )
            sys.exit(2)
    return target, root, discovered


def _select_rule_file(target: Path, root: Path) -> Path:
    """Return the closest ``.dropboxignore`` ancestor of ``target`` under ``root``.

    Walks from ``target.parent`` up to (and including) ``root``. At each level,
    scans ``iterdir()`` case-insensitively (per ``is_ignore_filename``) — a
    mixed-case rule file like ``.DropboxIgnore`` is treated as the same rule
    file the ``RuleCache`` already loaded under its canonical lowercase key.
    Prefers the canonical name when both exist (mirrors ``RuleCache.load_root``).
    Returns ``root / IGNORE_FILENAME`` as a fallback if no rule file exists
    along the ancestor chain.

    Caller is responsible for verifying ``target`` is under ``root``.
    """
    current = target.parent
    while current != root.parent:
        canonical: Path | None = None
        mixed_case: Path | None = None
        try:
            entries = list(current.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            if not is_ignore_filename(entry.name):
                continue
            if not entry.is_file():
                continue
            if entry.name == IGNORE_FILENAME:
                canonical = entry
            elif mixed_case is None:
                mixed_case = entry
        if canonical is not None:
            return canonical
        if mixed_case is not None:
            return mixed_case
        if current == root:
            break
        current = current.parent
    return root / IGNORE_FILENAME


def _check_rule_file_parses(rule_file: Path) -> str | None:
    """Return None if ``rule_file`` parses cleanly (or doesn't exist yet),
    otherwise the parse-error message.

    Used by ``ignore``/``unignore`` to refuse mutating a `.dropboxignore`
    that has invalid syntax (e.g., unterminated character class). Without
    this guard, the verb's append/remove leaves the broken line in place
    and the daemon's next reconcile drops the whole file from its cache,
    silently undoing the verb's apparent success.
    """
    if not rule_file.is_file():
        return None  # Will be created on append; not a parse error.
    try:
        lines = rule_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return f"cannot read: {exc}"
    try:
        rules._build_spec(lines)
    except (ValueError, TypeError, re.error) as exc:
        return str(exc)
    return None


def _resolve_canonical_to_disk(canonical_path: Path) -> Path:
    """Return the actual on-disk rule file matching ``canonical_path``.

    ``canonical_path`` uses the lowercase ``IGNORE_FILENAME`` (per
    ``RuleCache``'s cache-key normalization). On case-sensitive filesystems
    with a mixed-case rule file (e.g. ``.DropboxIgnore``), ``canonical_path``
    does not exist on disk; this helper scans the parent dir for any
    rule-file casing and returns the matching path. If no rule file is found
    (e.g. the file vanished), returns ``canonical_path`` unchanged so the
    caller's file-not-found handling still fires.
    """
    if canonical_path.is_file():
        return canonical_path
    try:
        for entry in canonical_path.parent.iterdir():
            if is_ignore_filename(entry.name) and entry.is_file():
                return entry
    except OSError:
        pass
    return canonical_path


def _matches_target_directly(target: Path, matches: list[rules.Match]) -> bool:
    """Return True if at least one non-negation rule's pattern matches the
    target path directly (not via subtree pruning from an ancestor match).

    For the half-state recovery decision: ancestor coverage (rule matches a
    parent directory and Dropbox prunes the subtree) means the daemon would
    NOT create a child marker — we shouldn't either. Direct match (rule
    pattern matches the target path itself) means the daemon WOULD set the
    marker on next reconcile — we should mirror that synchronously.

    The distinction: if a rule matches some strict ancestor of target (between
    ignore_file.parent and target.parent), the match on target is via subtree
    inheritance — ancestor coverage. If no strict ancestor is matched, the rule
    directly targets the path.
    """
    import pathspec as _pathspec

    for m in matches:
        if m.negation:
            continue
        try:
            relative = target.relative_to(m.ignore_file.parent)
        except ValueError:
            continue  # match's ignore_file isn't an ancestor; skip
        spec = _pathspec.PathSpec.from_lines(rules._CaseInsensitiveGitIgnorePattern, [m.pattern])
        # Check if any strict ancestor (between ignore_file.parent and target.parent)
        # is matched by this rule. If so, the match on target is via subtree pruning.
        # Walk from the immediate child of ignore_file.parent up to (but not including)
        # target itself. relative.parts gives e.g. ("parent", "child") for parent/child;
        # we iterate prefixes ("parent",) to test ancestor paths.
        ancestor_matched = False
        parts = relative.parts
        for i in range(1, len(parts)):
            ancestor_rel_str = "/".join(parts[:i]) + "/"
            if spec.match_file(ancestor_rel_str):
                ancestor_matched = True
                break
        if ancestor_matched:
            continue  # ancestor coverage only; daemon prunes, we skip
        # No strict ancestor is matched — the rule targets the path directly.
        rel_str = str(relative).replace("\\", "/")
        if target.is_dir() and not target.is_symlink():
            rel_str += "/"
        if spec.match_file(rel_str):
            return True
    return False


def _resolve_gitignore_arg(path: Path) -> Path:
    """Resolve a `generate` argument to an actual file.

    Directory → look for `.gitignore` inside; file → use as-is. Exits 2
    with a CLI-formatted stderr message if the resolved path does not exist.
    Single-caller helper; `_apply_from_gitignore` deliberately does its
    own (stricter) resolution and does not call this.
    """
    if path.is_dir():
        candidate = path / ".gitignore"
        if not candidate.exists():
            click.echo(f"error: no .gitignore in {path}", err=True)
            sys.exit(2)
        return candidate
    if not path.exists():
        click.echo(f"error: {path} not found", err=True)
        sys.exit(2)
    return path


def _compute_source_conflicts(source: Path) -> list[rules.Conflict]:
    """Run the static conflict detector against a single rule-source file.

    The source is mounted at its own directory so the detector can operate
    on a self-contained rule sequence. ``log_warnings=False`` suppresses the
    `rules.py` per-conflict WARNING — `generate` emits its own user-facing
    message via ``_emit_generate_conflict_warning``. Used by `cli.generate`
    to flag dropped negations at authoring time. Caller is responsible for
    catching `OSError` from ``source.parent.resolve()`` — conflict detection
    is informational, so generate must not break the byte-for-byte invariant
    if a transient I/O issue (permission, symlink loop) arises here.
    """
    cache = RuleCache()
    cache.load_external(source, source.parent.resolve(), log_warnings=False)
    return cache.conflicts()


def _emit_generate_conflict_warning(source: Path, conflicts: list[rules.Conflict]) -> None:
    """Echo dropped-negation warnings to stderr.

    The byte-for-byte invariant of `generate` is preserved — this is purely
    informational. The user can edit the source if they want the negations
    to apply. The common fix is to switch a directory rule like ``parent/``
    to the children-only form ``parent/*``: the children-only form does not
    mark ``parent`` itself, so ``parent`` doesn't propagate inheritance, and
    pathspec's last-match-wins then lets a later ``!parent/keep/`` override
    the include for that one child.
    """
    n = len(conflicts)
    click.echo(
        f"warning: {source} contains {n} dropped negation"
        f"{'' if n == 1 else 's'} that will not take effect at runtime:",
        err=True,
    )
    for c in conflicts:
        click.echo(
            f"  line {c.dropped_line}: {c.dropped_pattern}  "
            f"-- masked by line {c.masking_line}: {c.masking_pattern}",
            err=True,
        )
    click.echo(
        "Negations whose target lives under a directory matched by an "
        "earlier include cannot be re-included (Dropbox inheritance).",
        err=True,
    )


def _read_and_validate_rule_source(source: Path) -> str:
    """Read `source` as UTF-8 and verify it parses as a pathspec.

    Returns the raw text on success. Exits with code 2 (and a CLI-formatted
    stderr message) if the file can't be read, isn't valid UTF-8, or
    contains a pattern the parser rejects. Used by both `generate` and
    `apply --from-gitignore` — the two interactive entry points where
    rule-source failures should surface as user-facing errors rather than
    being swallowed into log warnings the way `RuleCache._load_file` does.
    """
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
    return text


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable DEBUG-level logging.")
@click.version_option(package_name="dbxignore")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """Manage hierarchical .dropboxignore rules for Dropbox."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    ctx.ensure_object(dict)


def _emit_dry_run_lines(would_mark: list[Path], would_clear: list[Path]) -> None:
    """Print the per-path `would mark` / `would clear` preview lines.

    Stable ordering: marks first, then clears, each in path-string order so
    the output is deterministic across platforms (matters for tests + diffing
    successive dry-runs as the user iterates on rules).
    """
    for p in sorted(would_mark, key=str):
        click.echo(f"would mark: {p}")
    for p in sorted(would_clear, key=str):
        click.echo(f"would clear: {p}")


def _confirm_apply(would_mark: int, would_clear: int) -> bool:
    """Echo the apply confirmation copy and return the user's choice.

    Both directions are footguns: marking a previously-synced path causes
    Dropbox to remove it from cloud and from every linked device; clearing
    a marker causes Dropbox to upload the local copy back. Three branches
    so the wording matches the actual situation.
    """
    if would_mark > 0 and would_clear > 0:
        click.echo(f"This will mark {would_mark} paths and clear {would_clear} existing markers.")
        click.echo(
            "Marking removes paths from cloud Dropbox and other linked "
            "devices (local copies on this device are preserved)."
        )
        click.echo("Clearing causes Dropbox to upload the local copies back to cloud.")
    elif would_mark > 0:
        click.echo(f"This will mark {would_mark} paths as ignored.")
        click.echo(
            "Dropbox will remove them from cloud Dropbox and from every "
            "other linked device. Local copies on this device are preserved."
        )
    else:
        click.echo(f"This will clear {would_clear} existing markers.")
        click.echo("Dropbox will then start syncing previously-ignored paths.")
    return click.confirm("Continue?")


def _run_apply_pass(
    targets: list[tuple[Path, Path]], cache: RuleCache, *, dry_run: bool
) -> reconcile.Report:
    """Run reconcile_subtree across all (root, subdir) targets and aggregate.

    Used by `apply` for both the dry-run pre-walk (driving the confirmation
    prompt) and the real reconcile pass.
    """
    aggregate = reconcile.Report()
    for r, subdir in targets:
        rep = reconcile.reconcile_subtree(r, subdir, cache, dry_run=dry_run)
        aggregate.marked += rep.marked
        aggregate.cleared += rep.cleared
        aggregate.errors.extend(rep.errors)
        aggregate.duration_s += rep.duration_s
        if dry_run:
            aggregate.would_mark.extend(rep.would_mark)
            aggregate.would_clear.extend(rep.would_clear)
    return aggregate


def _apply_from_gitignore(source: Path, *, dry_run: bool = False, yes: bool = False) -> None:
    """Run a one-shot reconcile using rules loaded from `source`.

    Rules are mounted at `dirname(source).resolve()` and applied only to
    that subtree. Existing .dropboxignore files in the tree do not
    participate in this run. Errors from the source file (missing,
    unreadable, invalid syntax) surface as user-facing CLI errors with
    exit code 2.

    Confirmation flow mirrors `apply`: under `--dry-run` or `--yes` no
    prompt fires; otherwise a dry-run pre-walk decides whether to prompt
    (skipping the prompt entirely when nothing would change).
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

    _read_and_validate_rule_source(source)

    cache = RuleCache()
    cache.load_external(source, mount_at, log_warnings=False)

    if dry_run:
        report = reconcile.reconcile_subtree(mount_at, mount_at, cache, dry_run=True)
        _emit_dry_run_lines(report.would_mark, report.would_clear)
        click.echo(
            f"apply --dry-run: would_mark={report.marked} "
            f"would_clear={report.cleared} errors={len(report.errors)} "
            f"duration={report.duration_s:.2f}s (no changes made)"
        )
        return

    if not yes:
        preview = reconcile.reconcile_subtree(mount_at, mount_at, cache, dry_run=True)
        if preview.marked == 0 and preview.cleared == 0:
            click.echo("Nothing to apply (rules already in sync).")
            return
        if not _confirm_apply(preview.marked, preview.cleared):
            click.echo("Aborted.")
            return

    report = reconcile.reconcile_subtree(mount_at, mount_at, cache, dry_run=False)
    click.echo(
        f"apply: marked={report.marked} cleared={report.cleared} "
        f"errors={len(report.errors)} duration={report.duration_s:.2f}s"
    )


@main.command()
@click.argument("path", required=False, type=click.Path(path_type=Path))
@click.option(
    "--from-gitignore",
    "from_gitignore",
    type=click.Path(exists=False, path_type=Path),
    default=None,
    help=(
        "Apply rules loaded from `<path>` instead of from .dropboxignore "
        "files in the tree. The directory containing `<path>` must be under "
        'a discovered Dropbox root. See README §"Using .gitignore rules".'
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be marked/cleared without changing anything.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Skip the confirmation prompt (for scripted use). Without --yes "
    "and outside --dry-run, apply previews changes and asks before "
    "mutating any marker — marking a previously-synced path causes "
    "Dropbox to remove it from cloud and from every linked device.",
)
def apply(
    path: Path | None,
    from_gitignore: Path | None,
    dry_run: bool,
    yes: bool,
) -> None:
    """Run one reconcile pass (whole Dropbox, or a subtree).

    Pass `--from-gitignore <path>` to load rules from a nominated file
    instead of the .dropboxignore files in the tree. Pass `--dry-run` to
    preview what would be marked/cleared without touching any markers.
    Pass `--yes` to skip the confirmation prompt (scripted use).
    """
    if from_gitignore is not None and path is not None:
        click.echo(
            "error: --from-gitignore and the positional path argument are mutually exclusive",
            err=True,
        )
        sys.exit(2)

    if from_gitignore is not None:
        _apply_from_gitignore(from_gitignore, dry_run=dry_run, yes=yes)
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
        if not resolved.exists():
            click.echo(f"Path {path} does not exist.", err=True)
            sys.exit(2)
        matched_root = find_containing(resolved, discovered)
        if matched_root is None:
            click.echo(f"Path {path} is not under any Dropbox root.", err=True)
            sys.exit(2)
        targets = [(matched_root, resolved)]

    if dry_run:
        report = _run_apply_pass(targets, cache, dry_run=True)
        _emit_dry_run_lines(report.would_mark, report.would_clear)
        click.echo(
            f"apply --dry-run: would_mark={report.marked} "
            f"would_clear={report.cleared} errors={len(report.errors)} "
            f"duration={report.duration_s:.2f}s (no changes made)"
        )
        return

    if not yes:
        preview = _run_apply_pass(targets, cache, dry_run=True)
        if preview.marked == 0 and preview.cleared == 0:
            click.echo("Nothing to apply (rules already in sync).")
            return
        if not _confirm_apply(preview.marked, preview.cleared):
            click.echo("Aborted.")
            return

    report = _run_apply_pass(targets, cache, dry_run=False)
    click.echo(
        f"apply: marked={report.marked} cleared={report.cleared} "
        f"errors={len(report.errors)} duration={report.duration_s:.2f}s"
    )


def _format_summary(state_obj: state.State | None, alive: bool, conflicts_count: int) -> str:
    """Build the stable single-line summary emitted by `status --summary`.

    Format is part of the public API per SemVer (see README §"Status-bar
    integration"). Field additions are non-breaking; removals or renames
    bump MINOR pre-1.0 / MAJOR post-1.0. Adding a new VALUE for an
    existing field (the `state=starting` token added in item #53) is
    technically a breaking change for consumers branching on
    `state == "running"` exhaustively — README documents the addition.

        state=starting pid=12345
        state=running pid=12345 marked=7 cleared=1 errors=0 conflicts=0
        state=not_running pid=12345 marked=7 cleared=1 errors=0 conflicts=0
        state=no_state conflicts=0

    State token is `starting` (state.json present + daemon alive + initial
    sweep not yet complete: `last_sweep is None`), `running` (state.json
    present + daemon alive + at least one sweep complete), `not_running`
    (state.json present, no live daemon — pid may be stale), or `no_state`
    (no state.json — daemon never ran).
    """
    if state_obj is None:
        return f"state=no_state conflicts={conflicts_count}"
    pid = state_obj.daemon_pid
    if pid is not None and alive and state_obj.last_sweep is None:
        # Alive but initial sweep hasn't completed yet. Emit the truncated
        # form: omit marked/cleared/errors/conflicts because they're all 0
        # and would falsely imply "swept and found nothing." Consumers
        # branching on the token need to handle 'starting' as distinct
        # from 'running'.
        return f"state=starting pid={pid}"
    state_token = "running" if (pid is not None and alive) else "not_running"
    parts = [f"state={state_token}"]
    if pid is not None:
        parts.append(f"pid={pid}")
    parts.append(f"marked={state_obj.last_sweep_marked}")
    parts.append(f"cleared={state_obj.last_sweep_cleared}")
    parts.append(f"errors={state_obj.last_sweep_errors}")
    parts.append(f"conflicts={conflicts_count}")
    return " ".join(parts)


@main.command()
@click.option(
    "--summary",
    is_flag=True,
    help="Emit a stable single-line summary on stdout suitable for "
    "status-bar widgets (polybar, tmux, i3blocks, sketchybar).",
)
def status(summary: bool) -> None:
    """Show daemon status and last sweep summary."""
    s = state.read()

    if summary:
        # Read the conflict count from state.json's `last_sweep_conflicts`
        # (item #68) rather than walking the rule cache: status-bar widgets
        # poll `--summary` at a high cadence and the rglob over every
        # `.dropboxignore` file in the watched tree was a per-tick cost.
        # Trade-off: the count is from the last daemon sweep (or 0 if no
        # state file), same staleness lineage as `last_sweep_marked` etc.
        conflicts_count = s.last_sweep_conflicts if s is not None else 0
        click.echo(_format_summary(s, state.daemon_is_running(s), conflicts_count))
        return

    # Human path: walk the cache so we can show the actual conflict details
    # below, not just the count. Skip the walk when there are no roots
    # (otherwise `status` pays for an rglob we don't need).
    discovered = _discover_roots()
    conflicts = _load_cache(discovered).conflicts() if discovered else []

    if s is None:
        click.echo("dbxignore: no state file found (daemon never ran).")
    else:
        if s.daemon_pid is None:
            click.echo("daemon: not running (no pid recorded)")
        elif state.daemon_is_running(s):
            if s.last_sweep is None:
                click.echo(f"daemon: starting (initial sweep in progress) (pid={s.daemon_pid})")
            else:
                click.echo(f"daemon: running (pid={s.daemon_pid})")
        else:
            click.echo(f"daemon: not running (last pid={s.daemon_pid} — state.json may be stale)")
        if s.daemon_started:
            click.echo(f"started: {s.daemon_started.isoformat()}")
        if s.last_sweep:
            click.echo(
                f"last sweep: {s.last_sweep.isoformat()}  "
                f"marked={s.last_sweep_marked} cleared={s.last_sweep_cleared} "
                f"errors={s.last_sweep_errors}  duration={s.last_sweep_duration_s:.2f}s"
            )
        if s.last_error:
            click.echo(f"last error: {s.last_error.path} — {s.last_error.message}")
        for r in s.watched_roots:
            click.echo(f"watching: {r}")

    # macOS sync-mode visibility (followup item 37). Returns None on
    # Windows/Linux where there's no detection step to report — those
    # platforms have a single attribute name fixed at module import.
    detection = markers.detection_summary()
    if detection is not None:
        click.echo(f"sync mode: {detection}")

    if discovered and conflicts:
        click.echo(f"rule conflicts ({len(conflicts)}):")
        # Pre-format and column-align so the "masked by" prefix and the
        # masking pattern land on consistent columns when dropped patterns
        # vary in length. Pads with f"{s:<width}" — only trailing spaces
        # are added, so substring-based test asserts continue to match.
        rows = [
            (
                f"{_format_ignore_file_loc(c.dropped_source, discovered)}:{c.dropped_line}",
                c.dropped_pattern,
                f"{_format_ignore_file_loc(c.masking_source, discovered)}:{c.masking_line}",
                c.masking_pattern,
            )
            for c in conflicts
        ]
        w_dloc, w_dpat, w_mloc = (max(len(r[i]) for r in rows) for i in (0, 1, 2))
        for d_loc, d_pat, m_loc, m_pat in rows:
            click.echo(
                f"  {d_loc:<{w_dloc}}  {d_pat:<{w_dpat}}  masked by {m_loc:<{w_mloc}}  {m_pat}"
            )


def _walk_marked_paths(target: Path) -> list[Path]:
    """Walk `target` and return every path currently bearing an ignore marker.

    Mirrors `list_ignored`'s pruning: once a directory is found marked,
    don't descend into it (its descendants are inheritance-ignored by
    Dropbox, and dbxignore itself doesn't write redundant child markers
    under a marked parent — the rare case of an individually-marked
    descendant under a marked parent is left to the next `apply` to
    reconcile, since `clear` with a pruning walk gets the same
    user-visible outcome at vastly lower walk cost on big trees).
    """
    found: list[Path] = []
    try:
        if markers.is_ignored(target):
            return [target]
    except OSError:
        pass
    for current, dirnames, filenames in os.walk(target, followlinks=False):
        current_path = Path(current)
        kept_dirs: list[str] = []
        for name in dirnames:
            p = current_path / name
            try:
                if markers.is_ignored(p):
                    found.append(p)
                else:
                    kept_dirs.append(name)
            except OSError:
                kept_dirs.append(name)
        dirnames[:] = kept_dirs
        for name in filenames:
            p = current_path / name
            try:
                if markers.is_ignored(p):
                    found.append(p)
            except OSError:
                continue
    return found


@main.command()
@click.argument("path", required=False, type=click.Path(path_type=Path))
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be cleared, don't change anything.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Run even if the daemon appears to be alive — its next sweep "
    "would re-apply markers, so use only for known short-window tests.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Skip the confirmation prompt (for scripted use).",
)
def clear(path: Path | None, dry_run: bool, force: bool, yes: bool) -> None:
    """Clear every Dropbox ignore marker under the watched roots (or under PATH).

    Inverse of `apply`: where `apply` sets every marker the rules
    dictate, `clear` unsets every marker regardless of rules. Leaves
    `.dropboxignore` files and per-user state.json untouched —
    `uninstall --purge` is the heavier verb that also wipes state.

    Refuses to run if the daemon is alive (the daemon's next sweep would
    re-apply rule-driven markers within seconds for rule-reload events
    or within an hour for the recovery sweep tick); pass `--force` to
    override. Prompts before clearing unless `--yes` is set.
    """
    s = state.read()
    if not force and s is not None and state.daemon_is_running(s):
        click.echo(
            f"error: daemon is running (pid={s.daemon_pid}). "
            f"The next sweep would re-apply markers.",
            err=True,
        )
        click.echo(
            "Stop the daemon first (`dbxignore uninstall`), or pass --force.",
            err=True,
        )
        sys.exit(2)

    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found.", err=True)
        sys.exit(2)

    if path is None:
        targets = discovered
    else:
        target = path.resolve()
        if find_containing(target, discovered) is None:
            click.echo(f"Path {path} is not under any Dropbox root.", err=True)
            sys.exit(2)
        targets = [target]

    to_clear: list[Path] = []
    for target in targets:
        to_clear.extend(_walk_marked_paths(target))

    if not to_clear:
        click.echo("No markers to clear.")
        return

    if dry_run:
        for p in to_clear:
            click.echo(f"would clear: {p}")
        click.echo(f"clear: would_clear={len(to_clear)} (dry-run)")
        return

    if not yes:
        click.echo(f"This will clear {len(to_clear)} markers.")
        click.echo("Dropbox will then start syncing previously-ignored paths.")
        if not click.confirm("Continue?"):
            click.echo("Aborted.")
            return

    cleared = 0
    errors: list[tuple[Path, str]] = []
    for p in to_clear:
        try:
            markers.clear_ignored(p)
            cleared += 1
        except OSError as exc:
            errors.append((p, str(exc)))

    click.echo(f"clear: cleared={cleared} errors={len(errors)}")
    for p, msg in errors[:10]:
        click.echo(f"  error: {p} - {msg}", err=True)


@main.command()
@click.argument("path", type=click.Path(exists=False, path_type=Path))
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be added/marked without changing anything.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Skip the confirmation prompt (for scripted use). Without --yes "
    "and outside --dry-run, ignore previews changes and asks before "
    "mutating — marking a previously-synced path causes Dropbox to "
    "remove it from cloud and from every linked device.",
)
def ignore(path: Path, dry_run: bool, yes: bool) -> None:
    """Mark <PATH> ignored persistently.

    Appends a literal-path rule to the nearest ancestor .dropboxignore
    (creating one at the Dropbox root if no ancestor exists) AND sets the
    ignore marker on <PATH> in one synchronous invocation. Idempotent —
    safe to re-call.
    """
    target, root, discovered = _validate_target_under_root(path)
    if is_ignore_filename(target.name):
        click.echo(
            f"error: {path} is a .dropboxignore rule file; these are never marked ignored.",
            err=True,
        )
        sys.exit(2)
    if target == root:
        click.echo(
            f"error: {path} is a Dropbox root; refusing to mark the entire root "
            f"ignored (Dropbox would remove the root from cloud and every linked device).",
            err=True,
        )
        sys.exit(2)
    # Linux's xattr backend cannot mark symlinks (kernel refuses user.* xattrs
    # on symlinks with EPERM). Reject before writing the rule, so we don't
    # leave an orphan rule with no marker. unignore doesn't need this guard
    # — clear_ignored is a no-op when no xattr exists.
    if sys.platform.startswith("linux") and target.is_symlink():
        click.echo(
            f"error: {path} is a symlink; Linux's xattr backend cannot mark "
            f"symlinks (kernel refuses user.* xattrs on symlinks with EPERM). "
            f"Mark the symlink's target directly, or use macOS/Windows where "
            f"the marker can attach to the link.",
            err=True,
        )
        sys.exit(2)
    cache = _load_cache(discovered)
    rule_file = _select_rule_file(target, root)
    parse_err = _check_rule_file_parses(rule_file)
    if parse_err is not None:
        click.echo(
            f"error: existing rule file {rule_file} has invalid syntax: {parse_err}. "
            f"Fix the file (or check the daemon log for context) before re-running.",
            err=True,
        )
        sys.exit(2)
    try:
        canonical = rules.format_literal_rule(target, rule_file)
    except ValueError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    # Idempotence + redundancy guards
    if cache.match(target):
        matches = [m for m in cache.explain(target) if not m.is_dropped]
        # Compute canonical for each candidate ancestor file (mirrors unignore).
        # The literal-target rule may live in any ancestor file, not just the
        # selected rule_file; we need to recognize it as via_us regardless of
        # which ancestor it lives in.
        canonical_per_file: dict[Path, str] = {}
        for m in matches:
            if m.ignore_file not in canonical_per_file:
                try:
                    canonical_per_file[m.ignore_file] = rules.format_literal_rule(
                        target, m.ignore_file
                    )
                except ValueError as exc:
                    click.echo(f"error: {exc}", err=True)
                    sys.exit(2)
        via_us_match = next(
            (
                m
                for m in matches
                if m.pattern.rstrip().casefold()
                == canonical_per_file[m.ignore_file].rstrip().casefold()
            ),
            None,
        )
        if via_us_match is not None:
            click.echo(f"{path} is already ignored.")
            file_with_rule = via_us_match.ignore_file
            should_recover_marker = True
        else:
            blocker = matches[0]
            click.echo(
                f"{path} is already covered by {blocker.pattern.rstrip()!r} "
                f"in {blocker.ignore_file}; not adding redundant rule."
            )
            file_with_rule = blocker.ignore_file
            # Distinguish direct match (rule pattern matches the target path
            # itself) from ancestor coverage (rule matches only a parent
            # directory; subtree pruning applies). For direct matches the
            # daemon would set the marker on the target via reconcile; we
            # set it synchronously here. For ancestor coverage the daemon
            # prunes below the parent and never creates a child marker; we
            # follow the same convention and skip.
            should_recover_marker = _matches_target_directly(target, matches)
        if should_recover_marker:
            # Half-state recovery: ensure marker is set even if rule was already
            # on disk.
            try:
                already_marked = markers.is_ignored(target)
            except OSError as exc:
                click.echo(
                    f"Could not read marker on {target}: {exc}. "
                    f"The rule is in {file_with_rule}; the daemon will set the marker "
                    f"when running on a filesystem that supports extended attributes.",
                    err=True,
                )
                sys.exit(2)
            if not already_marked:
                if dry_run:
                    click.echo(f"would set marker on {target}")
                else:
                    if not yes:
                        click.echo(
                            f"This will mark {target} ignored "
                            f"(rule is already in {file_with_rule}, but the marker "
                            f"is missing — daemon may not have run since rule was added)."
                        )
                        click.echo(
                            "Dropbox will remove it from cloud Dropbox and from every "
                            "other linked device. Local copies on this device are preserved."
                        )
                        if not click.confirm("Continue?"):
                            click.echo("Aborted.")
                            return
                    try:
                        markers.set_ignored(target)
                    except OSError as exc:
                        click.echo(
                            f"Marker write failed on {target}: {exc}. "
                            f"The rule was already in {file_with_rule}; the daemon will set the marker "
                            f"when running on a filesystem that supports extended attributes.",
                            err=True,
                        )
                        sys.exit(2)
                    click.echo(f"Set marker on {target}.")
        return

    # Confirmation
    if dry_run:
        click.echo(f"would append {canonical!r} to {rule_file}")
        click.echo(f"would set marker on {target}")
        return

    if not yes:
        click.echo(f"This will mark {target} ignored.")
        click.echo(
            "Dropbox will remove it from cloud Dropbox and from every "
            "other linked device. Local copies on this device are preserved."
        )
        if not click.confirm("Continue?"):
            click.echo("Aborted.")
            return

    # Rule first, then marker: a marker-first write would race the daemon's
    # OTHER-event 500ms debounce window (it'd see a marker the rules don't
    # justify and clear it spuriously).
    try:
        rules.append_rule(rule_file, canonical)
    except OSError as exc:
        click.echo(
            f"Failed to write {rule_file}: {exc}.",
            err=True,
        )
        sys.exit(2)
    try:
        markers.set_ignored(target)
    except OSError as exc:
        click.echo(
            f"Marker write failed on {target}: {exc}. "
            f"The rule was added to {rule_file}; the daemon will set the marker "
            f"when running on a filesystem that supports extended attributes.",
            err=True,
        )
        sys.exit(2)
    click.echo(f"ignore: rule added to {rule_file}; marker set on {target}")


@main.command()
@click.argument("path", type=click.Path(exists=False, path_type=Path))
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be removed/cleared without changing anything.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Skip the confirmation prompt (for scripted use). Without --yes "
    "and outside --dry-run, unignore previews changes and asks before "
    "mutating — clearing a marker causes Dropbox to start syncing the "
    "path again and re-upload its contents to cloud.",
)
def unignore(path: Path, dry_run: bool, yes: bool) -> None:
    """Remove the ignore marker and rule for <PATH>.

    Inverse of ``ignore``. Removes all literal-path rules in the relevant
    .dropboxignore file(s) that match <PATH> AND clears the marker. If
    <PATH> is also matched by a wildcard or non-literal rule, refuses
    to mutate and names the blocking rule.
    """
    target, root, discovered = _validate_target_under_root(path)
    if is_ignore_filename(target.name):
        click.echo(
            f"error: {path} is a .dropboxignore rule file; these are never marked ignored.",
            err=True,
        )
        sys.exit(2)
    if target == root:
        click.echo(
            f"error: {path} is a Dropbox root; refusing to mark the entire root "
            f"ignored (Dropbox would remove the root from cloud and every linked device).",
            err=True,
        )
        sys.exit(2)
    cache = _load_cache(discovered)

    if not cache.match(target):
        # Half-state recovery: marker may be set despite no rule (user manually
        # edited .dropboxignore while daemon was stopped, or daemon hasn't run
        # since rule removal). This command is the right place to clear the
        # orphan marker — symmetric to ignore's "rule on disk, marker missing"
        # half-state recovery.
        try:
            marker_set = markers.is_ignored(target)
        except OSError as exc:
            click.echo(
                f"Could not read marker on {target}: {exc}. "
                f"Re-run on a filesystem that supports extended attributes.",
                err=True,
            )
            sys.exit(2)
        if not marker_set:
            click.echo(f"{path} is not ignored; nothing to do.")
            return
        # Marker is set but no rule justifies it — orphan state.
        if dry_run:
            click.echo(f"would clear marker on {target} (no matching rule on disk)")
            return
        if not yes:
            click.echo(f"This will unignore {target} (no rule currently matches it).")
            click.echo("Dropbox will start syncing it again and upload local contents to cloud.")
            if not click.confirm("Continue?"):
                click.echo("Aborted.")
                return
        try:
            markers.clear_ignored(target)
        except OSError as exc:
            click.echo(
                f"Marker clear failed on {target}: {exc}. "
                f"No rule was on disk; the daemon will not re-set the marker.",
                err=True,
            )
            sys.exit(2)
        click.echo(f"unignore: marker cleared on {target} (no rule was on disk)")
        return

    # Non-dropped matches only — is_dropped rules are inert under an ignored ancestor.
    matches = [m for m in cache.explain(target) if not m.is_dropped]

    # Validate that each rule file containing a match parses cleanly. A
    # poisoned rule file would have been dropped from the cache, so its
    # matches wouldn't be in the list — but if a match IS present, the
    # file parsed successfully at cache-load time. The check below catches
    # the TOCTOU case where the file has since become invalid (manually
    # edited between cache load and mutation).
    for m in matches:
        parse_err = _check_rule_file_parses(m.ignore_file)
        if parse_err is not None:
            click.echo(
                f"error: rule file {m.ignore_file} has invalid syntax: {parse_err}. "
                f"Fix the file before re-running.",
                err=True,
            )
            sys.exit(2)

    canonical_per_file: dict[Path, str] = {}
    for m in matches:
        if m.ignore_file not in canonical_per_file:
            try:
                canonical_per_file[m.ignore_file] = rules.format_literal_rule(target, m.ignore_file)
            except ValueError as exc:
                click.echo(f"error: {exc}", err=True)
                sys.exit(2)

    removable = [
        m
        for m in matches
        if m.pattern.rstrip().casefold() == canonical_per_file[m.ignore_file].rstrip().casefold()
    ]
    # Simulate post-removal state via gitignore last-match-wins. cache.explain
    # returns matches in evaluation order (root .dropboxignore first, then
    # progressively-deeper files; within each file, top-to-bottom). After
    # removing the canonical-equal rules, if the LAST remaining match is a
    # negation (or no matches remain), the path becomes unignored — no
    # blocker. Otherwise the last-remaining positive rule still ignores the
    # path, so refuse and report the blockers for the user to fix manually.
    remaining = [m for m in matches if m not in removable]
    blockers = remaining if remaining and not remaining[-1].negation else []

    if blockers:
        click.echo(f"error: {path} is also matched by:", err=True)
        for m in blockers:
            click.echo(f"  line {m.line} of {m.ignore_file}: {m.pattern.rstrip()}", err=True)
        click.echo("Remove these manually if you want to unignore this path.", err=True)
        sys.exit(2)

    # Resolve canonical cache-key paths to actual on-disk paths once.  On a
    # case-sensitive FS where the rule file is named `.DropboxIgnore`, the
    # cache stores it under the lowercase canonical key, so `Match.ignore_file`
    # is `<parent>/.dropboxignore` — a path that does not exist on disk.
    # `_resolve_canonical_to_disk` scans the parent dir for any rule-file
    # casing and returns the real path so `remove_rule` can open the file.
    on_disk_per_match: dict[rules.Match, Path] = {
        m: _resolve_canonical_to_disk(m.ignore_file) for m in removable
    }

    # Confirmation
    if dry_run:
        files_to_preview: dict[Path, list[rules.Match]] = {}
        for m in removable:
            files_to_preview.setdefault(on_disk_per_match[m], []).append(m)
        for ignore_file, matches_in_file in files_to_preview.items():
            for m in matches_in_file:
                click.echo(f"would remove {m.pattern.rstrip()!r} from {ignore_file}")
            # Preview whether removing all rules in this file leaves it comment-only.
            try:
                content = ignore_file.read_text(encoding="utf-8")
            except OSError:
                continue
            target_norms = {m.pattern.rstrip() for m in matches_in_file}
            kept = [line for line in content.splitlines() if line.rstrip() not in target_norms]
            non_trivial = [
                line for line in kept if line.strip() and not line.strip().startswith("#")
            ]
            if not non_trivial:
                click.echo(f"  ({ignore_file} would contain only comments after removal)")
        click.echo(f"would clear marker on {target}")
        return

    if not yes:
        click.echo(f"This will unignore {target}.")
        click.echo("Dropbox will start syncing it again and upload local contents to cloud.")
        if not click.confirm("Continue?"):
            click.echo("Aborted.")
            return

    # Rules first, then marker: a marker-first clear would race the daemon's
    # OTHER-event 500ms debounce window — the daemon would see the still-present
    # rule and re-set the marker spuriously, producing visible marker-flap.
    expected_files = set(on_disk_per_match.values())
    affected_files: set[Path] = set()
    for m in removable:
        on_disk_path = on_disk_per_match[m]
        try:
            removed_count = rules.remove_rule(on_disk_path, m.pattern)
        except OSError as exc:
            click.echo(
                f"Failed to write {on_disk_path}: {exc}.",
                err=True,
            )
            sys.exit(2)
        if removed_count > 0:
            affected_files.add(on_disk_path)
    missing = expected_files - affected_files
    if missing:
        missing_str = ", ".join(str(f) for f in sorted(missing))
        click.echo(
            f"error: matched rules disappeared between read and write in "
            f"{missing_str}; re-run `dbxignore unignore {path}`.",
            err=True,
        )
        sys.exit(2)
    files_str = ", ".join(str(f) for f in sorted(affected_files))
    try:
        markers.clear_ignored(target)
    except OSError as exc:
        click.echo(
            f"Marker clear failed on {target}: {exc}. "
            f"The rule was removed from {files_str}; the daemon will clear the marker "
            f"when running on a filesystem that supports extended attributes.",
            err=True,
        )
        sys.exit(2)
    click.echo(f"unignore: rule removed from {files_str}; marker cleared on {target}")


@main.command("list")
@click.argument("path", required=False, type=click.Path(path_type=Path))
def list_ignored(path: Path | None) -> None:
    """List every path currently bearing the Dropbox ignore marker."""
    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found.", err=True)
        sys.exit(2)

    if path is None:
        targets = discovered
    else:
        target = path.resolve()
        if find_containing(target, discovered) is None:
            click.echo(f"Path {path} is not under any Dropbox root.", err=True)
            sys.exit(2)
        targets = [target]

    for target in targets:
        for p in _walk_marked_paths(target):
            click.echo(str(p))


def _explain(path: Path, *, quiet: bool) -> int:
    """Shared body for `explain` and `check-ignore`. Returns exit code.

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
            # Build lookup: (source, line) -> Conflict so we can annotate dropped rows.
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


@main.command()
@click.argument("path", type=click.Path(exists=False, path_type=Path))
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
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


@main.command(name="check-ignore")
@click.argument("path", type=click.Path(exists=False, path_type=Path))
@click.option(
    "--quiet",
    "-q",
    is_flag=True,
    help="Suppress stdout; only set exit code (parity with `git check-ignore -q`).",
)
def check_ignore(path: Path, quiet: bool) -> None:
    """Alias of \\`explain\\`, named for git-fluent users (parity with \\`git check-ignore -v\\`).

    Identical behavior, output, and exit codes to \\`explain\\`. The output format
    follows dbxignore's annotated-rule shape (each match shows ignore_file:line
    + pattern, with \\`[dropped]\\` annotations). Use \\`dbxignore explain\\` if you
    want the verb dbxignore documents in its own README; use \\`check-ignore\\`
    if your muscle memory is git's.
    """
    sys.exit(_explain(path, quiet=quiet))


def _run_daemon() -> None:
    from dbxignore import daemon as daemon_mod

    daemon_mod.run()


@main.command()
def daemon() -> None:
    """Run the watcher + hourly sweep daemon (foreground)."""
    _run_daemon()


@main.command()
def install() -> None:
    """Register the daemon with the platform's user-scoped service manager."""
    from dbxignore.install import install_service

    try:
        install_service()
    except RuntimeError as exc:
        click.echo(f"Failed to install daemon service: {exc}", err=True)
        sys.exit(2)
    click.echo("Installed dbxignore daemon service.")


@main.command()
@click.option(
    "--purge",
    is_flag=True,
    help=(
        "Also clear every ignore marker and remove local dbxignore state "
        "(state.json, daemon.log*, the state directory, and any systemd "
        "drop-in directory on Linux)."
    ),
)
def uninstall(purge: bool) -> None:
    """Remove the daemon service.

    With --purge, also clear every ignore marker under each discovered
    Dropbox root, delete `state.json` and `daemon.log*` from the
    per-user state directory, remove that directory if it's empty, and
    on Linux remove the systemd drop-in directory if it exists. The goal
    is to leave no dbxignore-authored artifacts on disk.
    """
    from dbxignore.install import uninstall_service

    try:
        uninstall_service()
    except RuntimeError as exc:
        click.echo(f"Failed to uninstall daemon service: {exc}", err=True)
        sys.exit(2)
    click.echo("Uninstalled dbxignore daemon service.")

    if purge:
        # (1) Clear xattr markers.
        discovered = _discover_roots()
        cleared = 0
        for r in discovered:
            try:
                if markers.is_ignored(r):
                    markers.clear_ignored(r)
                    cleared += 1
            except OSError:
                pass
            for current, dirnames, filenames in os.walk(r, followlinks=False):
                current_path = Path(current)
                for name in dirnames + filenames:
                    p = current_path / name
                    try:
                        if markers.is_ignored(p):
                            if is_ignore_filename(p.name):
                                logger.warning(
                                    ".dropboxignore at %s was marked ignored; "
                                    "overriding back to synced",
                                    p,
                                )
                            markers.clear_ignored(p)
                            cleared += 1
                    except OSError:
                        continue
        click.echo(f"Cleared {cleared} ignore markers.")

        # (2) Remove state.json, daemon.log*, state dir (cross-platform).
        _purge_local_state()

        # (3) Remove the systemd drop-in directory (Linux only).
        if sys.platform.startswith("linux"):
            from dbxignore.install import linux_systemd

            removed_dropin = linux_systemd.remove_dropin_directory()
            if removed_dropin is not None:
                click.echo(f"Removed systemd drop-in directory {removed_dropin}.")


_INIT_DETECTION_DIRS = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        "target",
        "build",
        "dist",
        "out",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".next",
        ".nuxt",
        ".svelte-kit",
        ".turbo",
        ".gradle",
        ".cache",
        "bin",
        "obj",
        "htmlcov",
    }
)


def _load_default_template() -> str:
    """Read the packaged default.dropboxignore template content.

    Lives at `src/dbxignore/templates/default.dropboxignore` and ships
    via `importlib.resources` (no special pyproject.toml package_data
    config — hatchling's `packages = ["src/dbxignore"]` includes the
    subdir's non-.py files automatically).
    """
    return (
        files("dbxignore.templates").joinpath("default.dropboxignore").read_text(encoding="utf-8")
    )


def _detect_marker_bait(target: Path, max_depth: int = 3) -> list[str]:
    """Walk `target` to depth `max_depth` and return matched dir names.

    Detection is purely informational — used to annotate the init output
    header. The file content is always the full template; a header line
    notes which dirs were found in this tree so the user knows which
    template patterns are immediately load-bearing.

    Pruning rules: don't descend into a dir that itself matched (avoids
    walking into a `node_modules` tree, which is the worst case); and
    don't descend below `max_depth - 1` since children of dirs at that
    level would be at depth `max_depth + 1`.
    """
    found: set[str] = set()
    target = target.resolve()
    for current, dirnames, _filenames in os.walk(target, followlinks=False):
        depth = len(Path(current).relative_to(target).parts)
        kept: list[str] = []
        for name in dirnames:
            if name in _INIT_DETECTION_DIRS:
                found.add(name)
            elif depth < max_depth - 1:
                kept.append(name)
        dirnames[:] = kept
    return sorted(found)


def _format_init_output(template: str, detected: list[str]) -> str:
    """Build the init output: generated header + verbatim template body."""
    today = dt.datetime.now(dt.UTC).date().isoformat()
    header = [f"# Generated by `dbxignore init` on {today}"]
    if detected:
        header.append(f"# Detected in this tree at depth <= 3: {', '.join(detected)}")
    else:
        header.append("# No common dev artifacts detected in this tree.")
    header.append("# (Edit this file to remove patterns that don't apply.)")
    header.append("")  # blank line separates header from template
    return "\n".join(header) + "\n" + template


@main.command()
@click.argument(
    "path",
    required=False,
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing .dropboxignore.",
)
@click.option(
    "--stdout",
    "to_stdout",
    is_flag=True,
    help="Print to stdout, don't write a file.",
)
def init(path: Path | None, force: bool, to_stdout: bool) -> None:
    """Scaffold a starter .dropboxignore in PATH (or cwd).

    Walks the target directory to depth 3 looking for marker-bait dirs
    (node_modules, __pycache__, .venv, target, build, etc.) and writes
    a .dropboxignore template, with a header noting which dirs were
    detected in this tree. The template covers Node.js, Python,
    Rust, JVM, .NET, frontend frameworks, build/dist outputs, and
    OS/editor detritus — edit afterward to remove patterns that don't
    apply to your setup.

    Refuses to overwrite an existing .dropboxignore unless --force is set.
    Pass --stdout to preview the template content without writing.
    """
    target = (path or Path.cwd()).resolve()
    if not target.is_dir():
        click.echo(f"error: {target} is not a directory", err=True)
        sys.exit(2)

    template = _load_default_template()
    detected = _detect_marker_bait(target)
    content = _format_init_output(template, detected)

    if to_stdout:
        click.echo(content, nl=False)
        return

    output = target / IGNORE_FILENAME
    if output.exists() and not force:
        click.echo(
            f"error: {output} already exists. Pass --force to overwrite.",
            err=True,
        )
        sys.exit(2)

    output.write_text(content, encoding="utf-8")
    if detected:
        click.echo(f"wrote {output} ({len(detected)} detected: {', '.join(detected)})")
    else:
        click.echo(f"wrote {output} (no marker-bait detected; template ships defaults anyway)")


@main.command()
@click.argument("path", type=click.Path(exists=False, path_type=Path))
@click.option(
    "-o",
    "--output",
    "output",
    type=click.Path(path_type=Path),
    default=None,
    help="Write to this path instead of `<dir>/.dropboxignore`.",
)
@click.option(
    "--stdout",
    is_flag=True,
    help="Write to stdout instead of a file.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing .dropboxignore at the target location.",
)
def generate(path: Path, output: Path | None, stdout: bool, force: bool) -> None:
    """Translate a .gitignore (or any nominated file) to a .dropboxignore.

    PATH may be a file or a directory. Directory: looks for .gitignore
    inside. File: used as-is regardless of filename. By default the
    output is written to `<dir>/.dropboxignore`. See README §"Using
    .gitignore rules" for the gitignore-vs-dbxignore semantic divergence.
    """
    if output is not None and stdout:
        click.echo("error: -o and --stdout are mutually exclusive", err=True)
        sys.exit(2)

    source = _resolve_gitignore_arg(path)

    text = _read_and_validate_rule_source(source)
    lines = text.splitlines()

    # Detect dropped negations against the source as a self-contained rule
    # set. Computed once and reused for both the --stdout and file-write
    # branches; the warning text always goes to stderr so stdout consumers
    # get clean verbatim content.
    # OSError on the resolve()/load path (permission denied, symlink loop)
    # must NOT break generate — conflict detection is informational, the
    # byte-for-byte file output is the load-bearing contract.
    try:
        conflicts = _compute_source_conflicts(source)
    except OSError as exc:
        click.echo(
            f"warning: could not run conflict check on {source}: {exc}; proceeding with generate",
            err=True,
        )
        conflicts = []

    if stdout:
        if conflicts:
            _emit_generate_conflict_warning(source, conflicts)
        click.echo(text, nl=False)
        return

    target = output if output is not None else (source.parent / IGNORE_FILENAME)
    if target.exists() and not force:
        click.echo(
            f"error: {target} exists; pass --force to overwrite or --stdout to preview",
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
    if find_containing(target_resolved, discovered) is None:
        click.echo(
            f"warning: {target} is not under any discovered Dropbox root; "
            "reconcile will not see it",
            err=True,
        )

    if conflicts:
        _emit_generate_conflict_warning(source, conflicts)

    rule_count = sum(1 for line in lines if line.strip() and not line.strip().startswith("#"))
    click.echo(f"wrote {rule_count} rules to {target}")


@click.command()
@click.option("--verbose", "-v", is_flag=True, help="Enable DEBUG-level logging.")
@click.version_option(package_name="dbxignore")
def daemon_main(verbose: bool) -> None:
    """Run the dbxignore watcher + hourly sweep daemon (foreground)."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    _run_daemon()

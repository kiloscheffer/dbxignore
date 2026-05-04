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

import click

from dbxignore import markers, reconcile, roots, rules, state
from dbxignore.roots import find_containing
from dbxignore.rules import IGNORE_FILENAME, RuleCache

logger = logging.getLogger(__name__)


def _discover_roots() -> list[Path]:
    """Resolve roots at the CLI boundary; indirection allows test monkeypatching."""
    return [r.resolve() for r in roots.discover()]


def _format_ignore_file_loc(path: Path, roots: list[Path]) -> str:
    """Return path relative to the nearest root, or absolute if none matches.

    Used by ``status`` and ``explain`` to show compact source locations for
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

    Called by ``uninstall --purge`` after the ignore markers are cleared.
    On Windows + Linux, state and log live in the same dir. On macOS, the
    log dir (~/Library/Logs/dbxignore/) is separate from the state dir
    (~/Library/Application Support/dbxignore/), so we clean both.
    """
    state_dir = state.user_state_dir()
    if state_dir.exists():
        _purge_dir(
            state_dir,
            patterns=["state.json", "state.json.tmp", "daemon.log", "daemon.log.*"],
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

    The CLI surfaces conflicts via structured stdout (``status``, ``explain``)
    so the per-mutation WARNING records would be a stderr duplicate.
    """
    cache = RuleCache()
    for r in roots:
        cache.load_root(r, log_warnings=False)
    return cache


def _resolve_gitignore_arg(path: Path) -> Path:
    """Resolve a ``generate`` argument to an actual file.

    Directory → look for ``.gitignore`` inside; file → use as-is. Exits 2
    with a CLI-formatted stderr message if the resolved path does not exist.
    Single-caller helper; ``_apply_from_gitignore`` deliberately does its
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


def _read_and_validate_rule_source(source: Path) -> str:
    """Read ``source`` as UTF-8 and verify it parses as a pathspec.

    Returns the raw text on success. Exits with code 2 (and a CLI-formatted
    stderr message) if the file can't be read, isn't valid UTF-8, or
    contains a pattern the parser rejects. Used by both ``generate`` and
    ``apply --from-gitignore`` — the two interactive entry points where
    rule-source failures should surface as user-facing errors rather than
    being swallowed into log warnings the way ``RuleCache._load_file`` does.
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


def _apply_from_gitignore(source: Path, *, dry_run: bool = False) -> None:
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

    _read_and_validate_rule_source(source)

    cache = RuleCache()
    cache.load_external(source, mount_at)

    report = reconcile.reconcile_subtree(mount_at, mount_at, cache, dry_run=dry_run)
    if dry_run:
        _emit_dry_run_lines(report.would_mark, report.would_clear)
        click.echo(
            f"apply --dry-run: would_mark={report.marked} "
            f"would_clear={report.cleared} errors={len(report.errors)} "
            f"duration={report.duration_s:.2f}s (no changes made)"
        )
    else:
        click.echo(
            f"apply: marked={report.marked} cleared={report.cleared} "
            f"errors={len(report.errors)} duration={report.duration_s:.2f}s"
        )


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
@click.option(
    "--dry-run", is_flag=True,
    help="Print what would be marked/cleared without changing anything.",
)
def apply(path: Path | None, from_gitignore: Path | None, dry_run: bool) -> None:
    """Run one reconcile pass (whole Dropbox, or a subtree).

    Pass ``--from-gitignore <path>`` to load rules from a nominated file
    instead of the .dropboxignore files in the tree. Pass ``--dry-run`` to
    preview what would be marked/cleared without touching any markers.
    """
    if from_gitignore is not None and path is not None:
        click.echo(
            "error: --from-gitignore and the positional path argument "
            "are mutually exclusive",
            err=True,
        )
        sys.exit(2)

    if from_gitignore is not None:
        _apply_from_gitignore(from_gitignore, dry_run=dry_run)
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
    aggregated_would_mark: list[Path] = []
    aggregated_would_clear: list[Path] = []
    for r, subdir in targets:
        report = reconcile.reconcile_subtree(r, subdir, cache, dry_run=dry_run)
        total_marked += report.marked
        total_cleared += report.cleared
        total_errors += len(report.errors)
        total_duration += report.duration_s
        if dry_run:
            aggregated_would_mark.extend(report.would_mark)
            aggregated_would_clear.extend(report.would_clear)

    if dry_run:
        _emit_dry_run_lines(aggregated_would_mark, aggregated_would_clear)
        click.echo(
            f"apply --dry-run: would_mark={total_marked} "
            f"would_clear={total_cleared} errors={total_errors} "
            f"duration={total_duration:.2f}s (no changes made)"
        )
    else:
        click.echo(
            f"apply: marked={total_marked} cleared={total_cleared} "
            f"errors={total_errors} duration={total_duration:.2f}s"
        )


def _format_summary(
    state_obj: state.State | None, alive: bool, conflicts_count: int
) -> str:
    """Build the stable single-line summary emitted by ``status --summary``.

    Format is part of the public API per SemVer (see README §"Status-bar
    integration"). Field additions are non-breaking; removals or renames
    bump MINOR pre-1.0 / MAJOR post-1.0.

        state=running pid=12345 marked=7 cleared=1 errors=0 conflicts=0
        state=not_running pid=12345 marked=7 cleared=1 errors=0 conflicts=0
        state=no_state conflicts=0

    State token is `running` (state.json present + daemon process alive),
    `not_running` (state.json present, no live daemon — pid may be stale),
    or `no_state` (no state.json — daemon never ran).
    """
    if state_obj is None:
        return f"state=no_state conflicts={conflicts_count}"
    pid = state_obj.daemon_pid
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

    # Compute conflicts upfront so summary and human paths share the work.
    # Skip the rule-cache walk entirely when there are no roots — otherwise
    # `status` pays for an rglob we don't need.
    discovered = _discover_roots()
    conflicts = _load_cache(discovered).conflicts() if discovered else []

    if summary:
        click.echo(_format_summary(s, state.daemon_is_running(s), len(conflicts)))
        return

    if s is None:
        click.echo("dbxignore: no state file found (daemon never ran).")
    else:
        if s.daemon_pid is None:
            click.echo("daemon: not running (no pid recorded)")
        elif state.is_daemon_alive(s.daemon_pid):
            click.echo(f"daemon: running (pid={s.daemon_pid})")
        else:
            click.echo(
                f"daemon: not running (last pid={s.daemon_pid} — state.json may be stale)"
            )
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
                f"  {d_loc:<{w_dloc}}  {d_pat:<{w_dpat}}  "
                f"masked by {m_loc:<{w_mloc}}  {m_pat}"
            )


def _walk_marked_paths(target: Path) -> list[Path]:
    """Walk ``target`` and return every path currently bearing an ignore marker.

    Mirrors ``list_ignored``'s pruning: once a directory is found marked,
    don't descend into it (its descendants are inheritance-ignored by
    Dropbox, and dbxignore itself doesn't write redundant child markers
    under a marked parent — the rare case of an individually-marked
    descendant under a marked parent is left to the next ``apply`` to
    reconcile, since `clear` with a pruning walk gets the same
    user-visible outcome at vastly lower walk cost on big trees).
    """
    found: list[Path] = []
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
    "--dry-run", is_flag=True,
    help="Print what would be cleared, don't change anything.",
)
@click.option(
    "--force", is_flag=True,
    help="Run even if the daemon appears to be alive — its next sweep "
    "would re-apply markers, so use only for known short-window tests.",
)
@click.option(
    "--yes", is_flag=True,
    help="Skip the confirmation prompt (for scripted use).",
)
def clear(path: Path | None, dry_run: bool, force: bool, yes: bool) -> None:
    """Clear every Dropbox ignore marker under the watched roots (or under PATH).

    Inverse of ``apply``: where ``apply`` sets every marker the rules
    dictate, ``clear`` unsets every marker regardless of rules. Leaves
    ``.dropboxignore`` files and per-user state.json untouched —
    ``uninstall --purge`` is the heavier verb that also wipes state.

    Refuses to run if the daemon is alive (the daemon's next sweep would
    re-apply rule-driven markers within seconds for rule-reload events
    or within an hour for the recovery sweep tick); pass ``--force`` to
    override. Prompts before clearing unless ``--yes`` is set.
    """
    s = state.read()
    if not force and state.daemon_is_running(s):
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


@main.command()
@click.argument("path", type=click.Path(exists=False, path_type=Path))
def explain(path: Path) -> None:
    """Show which .dropboxignore rule (if any) matches the path.

    Dropped negations (rules that can't take effect because an ancestor
    directory is ignored) appear prefixed with ``[dropped]`` and a pointer
    to the masking rule. See README §"Negations and Dropbox's ignore
    inheritance" for why.
    """
    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found.", err=True)
        sys.exit(2)

    cache = _load_cache(discovered)

    matches = cache.explain(path.resolve())
    if not matches:
        click.echo(f"no match for {path}")
        return

    # Build lookup: (source, line) -> Conflict so we can annotate dropped rows.
    conflicts_by_drop = {
        (c.dropped_source, c.dropped_line): c
        for c in cache.conflicts()
    }

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
    Dropbox root, delete ``state.json`` and ``daemon.log*`` from the
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
            for current, dirnames, filenames in os.walk(r, followlinks=False):
                current_path = Path(current)
                for name in dirnames + filenames:
                    p = current_path / name
                    try:
                        if markers.is_ignored(p):
                            if p.name == IGNORE_FILENAME:
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


_INIT_DETECTION_DIRS = frozenset({
    "node_modules", "__pycache__", ".venv", "venv", "env", "target",
    "build", "dist", "out", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".nox", ".next", ".nuxt", ".svelte-kit", ".turbo", ".gradle",
    ".cache", "bin", "obj", "htmlcov",
})


def _load_default_template() -> str:
    """Read the packaged default.dropboxignore template content.

    Lives at ``src/dbxignore/templates/default.dropboxignore`` and ships
    via ``importlib.resources`` (no special pyproject.toml package_data
    config — hatchling's ``packages = ["src/dbxignore"]`` includes the
    subdir's non-.py files automatically).
    """
    return files("dbxignore.templates").joinpath("default.dropboxignore").read_text(
        encoding="utf-8"
    )


def _detect_marker_bait(target: Path, max_depth: int = 3) -> list[str]:
    """Walk ``target`` to depth ``max_depth`` and return matched dir names.

    Detection is purely informational — used to annotate the init output
    header. The file content is always the full template; a header line
    notes which dirs were found in this tree so the user knows which
    template patterns are immediately load-bearing.

    Pruning rules: don't descend into a dir that itself matched (avoids
    walking into a `node_modules` tree, which is the worst case); and
    don't descend below ``max_depth - 1`` since children of dirs at that
    level would be at depth ``max_depth + 1``.
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
    "path", required=False,
    type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
)
@click.option(
    "--force", is_flag=True,
    help="Overwrite an existing .dropboxignore.",
)
@click.option(
    "--stdout", "to_stdout", is_flag=True,
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
    "-o", "--output", "output",
    type=click.Path(path_type=Path), default=None,
    help="Write to this path instead of <dir>/.dropboxignore.",
)
@click.option(
    "--stdout", is_flag=True,
    help="Write to stdout instead of a file.",
)
@click.option(
    "--force", is_flag=True,
    help="Overwrite an existing .dropboxignore at the target location.",
)
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

    source = _resolve_gitignore_arg(path)

    text = _read_and_validate_rule_source(source)
    lines = text.splitlines()

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


@click.command()
@click.option("--verbose", "-v", is_flag=True, help="Enable DEBUG-level logging.")
@click.version_option(package_name="dbxignore")
def daemon_main(verbose: bool) -> None:
    """Run the dbxignore watcher + hourly sweep daemon (foreground)."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    _run_daemon()

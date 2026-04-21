"""Command-line interface for dropboxignore."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import click

from dropboxignore import markers, reconcile, roots, state
from dropboxignore.roots import find_containing
from dropboxignore.rules import IGNORE_FILENAME, RuleCache

logger = logging.getLogger(__name__)


def _discover_roots() -> list[Path]:
    """Indirection so tests can monkeypatch root discovery."""
    return roots.discover()


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


def _process_is_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable DEBUG-level logging.")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """Manage hierarchical .dropboxignore rules for Dropbox."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    ctx.ensure_object(dict)


@main.command()
@click.argument("path", required=False, type=click.Path(path_type=Path))
def apply(path: Path | None) -> None:
    """Run one reconcile pass (whole Dropbox, or a subtree)."""
    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found. Is Dropbox installed?", err=True)
        sys.exit(2)

    cache = RuleCache()
    for r in discovered:
        cache.load_root(r)

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


@main.command()
def status() -> None:
    """Show daemon status and last sweep summary."""
    s = state.read()
    if s is None:
        click.echo("dropboxignore: no state file found (daemon never ran).")
    else:
        alive = _process_is_alive(s.daemon_pid)
        click.echo(f"daemon: {'running' if alive else 'not running'} (pid={s.daemon_pid})")
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

    # Conflicts section — present only when RuleCache has any.
    discovered = _discover_roots()
    cache = RuleCache()
    for r in discovered:
        cache.load_root(r)
    conflicts = cache.conflicts()
    if conflicts:
        click.echo(f"rule conflicts ({len(conflicts)}):")
        for c in conflicts:
            dropped_loc = _format_ignore_file_loc(c.dropped_source, discovered)
            masking_loc = _format_ignore_file_loc(c.masking_source, discovered)
            click.echo(
                f"  {dropped_loc}:{c.dropped_line}  {c.dropped_pattern}  "
                f"masked by {masking_loc}:{c.masking_line}  {c.masking_pattern}"
            )


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
        for current, dirnames, filenames in os.walk(target, followlinks=False):
            current_path = Path(current)
            kept_dirs: list[str] = []
            for name in dirnames:
                p = current_path / name
                try:
                    if markers.is_ignored(p):
                        click.echo(str(p))
                    else:
                        kept_dirs.append(name)
                except OSError:
                    kept_dirs.append(name)
            dirnames[:] = kept_dirs
            for name in filenames:
                p = current_path / name
                try:
                    if markers.is_ignored(p):
                        click.echo(str(p))
                except OSError:
                    continue


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

    cache = RuleCache()
    for r in discovered:
        cache.load_root(r)

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


@main.command()
def daemon() -> None:
    """Run the watcher + hourly sweep daemon (foreground)."""
    from dropboxignore import daemon as daemon_mod
    daemon_mod.run()


@main.command()
def install() -> None:
    """Register the daemon with the platform's user-scoped service manager."""
    from dropboxignore.install import install_service
    try:
        install_service()
    except RuntimeError as exc:
        click.echo(f"Failed to install daemon service: {exc}", err=True)
        sys.exit(2)
    click.echo("Installed dropboxignore daemon service.")


@main.command()
@click.option("--purge", is_flag=True, help="Also clear every ignore marker.")
def uninstall(purge: bool) -> None:
    """Remove the daemon service. With --purge, also clear all ignore markers."""
    from dropboxignore.install import uninstall_service
    try:
        uninstall_service()
    except RuntimeError as exc:
        click.echo(f"Failed to uninstall daemon service: {exc}", err=True)
        sys.exit(2)
    click.echo("Uninstalled dropboxignore daemon service.")

    if purge:
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


def daemon_main() -> None:
    """Entry point for the dropboxignored script shim."""
    sys.argv.insert(1, "daemon")
    main()

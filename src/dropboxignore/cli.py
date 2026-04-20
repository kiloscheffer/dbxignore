"""Command-line interface for dropboxignore."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import click

from dropboxignore import ads, reconcile, roots, state
from dropboxignore.rules import RuleCache

logger = logging.getLogger(__name__)


def _discover_roots() -> list[Path]:
    """Indirection so tests can monkeypatch root discovery."""
    return roots.discover()


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


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable DEBUG-level logging.")
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """Manage hierarchical .dropboxignore rules for Dropbox on Windows."""
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
        matched_root = next((r for r in discovered if _is_under(resolved, r)), None)
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
        return

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


@main.command("list")
@click.argument("path", required=False, type=click.Path(path_type=Path))
def list_ignored(path: Path | None) -> None:
    """List every path currently bearing the com.dropbox.ignored ADS marker."""
    discovered = _discover_roots()
    if not discovered:
        click.echo("No Dropbox roots found.", err=True)
        sys.exit(2)

    if path is None:
        targets = discovered
    else:
        target = path.resolve()
        matched = next((r for r in discovered if _is_under(target, r)), None)
        if matched is None:
            click.echo(f"Path {path} is not under any Dropbox root.", err=True)
            sys.exit(2)
        targets = [target]

    for target in targets:
        for current, dirnames, filenames in os.walk(target, followlinks=False):
            current_path = Path(current)
            for name in dirnames + filenames:
                p = current_path / name
                try:
                    if ads.is_ignored(p):
                        click.echo(str(p))
                except (FileNotFoundError, PermissionError):
                    continue


@main.command()
@click.argument("path", type=click.Path(exists=False, path_type=Path))
def explain(path: Path) -> None:
    """Show which .dropboxignore rule (if any) matches the path."""
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
    for m in matches:
        arrow = "!" if m.negation else "="
        click.echo(f"{m.ignore_file}:{m.line}: {arrow} {m.pattern}")


def daemon_main() -> None:
    """Entry point for the dropboxignored script shim."""
    sys.argv.insert(1, "daemon")
    main()

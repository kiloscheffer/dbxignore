"""Command-line interface for dropboxignore."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from dropboxignore import reconcile, roots
from dropboxignore.rules import RuleCache

logger = logging.getLogger(__name__)


def _discover_roots() -> list[Path]:
    """Indirection so tests can monkeypatch root discovery."""
    return roots.discover()


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


def daemon_main() -> None:
    """Entry point for the dropboxignored script shim."""
    sys.argv.insert(1, "daemon")
    main()

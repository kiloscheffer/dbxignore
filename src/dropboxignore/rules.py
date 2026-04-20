"""Hierarchical .dropboxignore rule cache (basic matching)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pathspec

logger = logging.getLogger(__name__)

IGNORE_FILENAME = ".dropboxignore"


@dataclass(frozen=True)
class Match:
    """A single matching rule for the ``explain`` diagnostic."""

    ignore_file: Path
    line: int
    pattern: str
    negation: bool


class RuleCache:
    """Maintains parsed rules from every .dropboxignore under the root(s)."""

    def __init__(self) -> None:
        # Map: .dropboxignore file path -> parsed GitIgnoreSpec
        self._specs: dict[Path, pathspec.GitIgnoreSpec] = {}
        # Known roots for relative-path resolution
        self._roots: list[Path] = []

    def load_root(self, root: Path) -> None:
        root = root.resolve()
        if root not in self._roots:
            self._roots.append(root)
        for ignore_file in root.rglob(IGNORE_FILENAME):
            self._load_file(ignore_file)

    def match(self, path: Path) -> bool:
        path = path.resolve()
        root = self._root_of(path)
        if root is None:
            return False

        # Walk from root toward path; any .dropboxignore along the way applies.
        # Apply each in order so later (deeper) rules can negate earlier ones.
        matched = False
        for ancestor in self._ancestors(root, path):
            ignore_file = ancestor / IGNORE_FILENAME
            spec = self._specs.get(ignore_file)
            if spec is None:
                continue
            rel = path.relative_to(ancestor)
            rel_str = rel.as_posix()
            if path.is_dir():
                rel_str += "/"
            if spec.match_file(rel_str):
                matched = True
        return matched

    # ---- internal helpers ------------------------------------------------

    def _load_file(self, ignore_file: Path) -> None:
        try:
            lines = ignore_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("Could not read %s: %s", ignore_file, exc)
            return
        try:
            spec = pathspec.GitIgnoreSpec.from_lines(lines)
        except Exception as exc:  # pathspec surfaces various parse errors
            logger.warning("Invalid .dropboxignore at %s: %s", ignore_file, exc)
            return
        self._specs[ignore_file.resolve()] = spec

    def _root_of(self, path: Path) -> Path | None:
        for root in self._roots:
            try:
                path.relative_to(root)
                return root
            except ValueError:
                continue
        return None

    def _ancestors(self, root: Path, path: Path) -> list[Path]:
        """Return [root, ...intermediate dirs..., path's parent] inclusive."""
        rel = path.relative_to(root)
        result = [root]
        current = root
        for part in rel.parts[:-1]:
            current = current / part
            result.append(current)
        return result

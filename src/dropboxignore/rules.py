"""Hierarchical .dropboxignore rule cache (basic matching)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pathspec
from pathspec.patterns.gitwildmatch import GitIgnoreSpecPattern

logger = logging.getLogger(__name__)

IGNORE_FILENAME = ".dropboxignore"


class _CaseInsensitiveGitWildMatchPattern(GitIgnoreSpecPattern):
    """GitWildMatch pattern that compiles regex with re.IGNORECASE.

    Windows NTFS is case-insensitive; a rule written as ``node_modules/`` must
    match a directory literally named ``Node_Modules`` on disk.
    """

    @classmethod
    def pattern_to_regex(cls, pattern: str) -> tuple[str | None, bool | None]:
        regex, include = super().pattern_to_regex(pattern)
        if regex is not None:
            regex = f"(?i){regex}"
        return regex, include


def _build_spec(lines: list[str]) -> pathspec.PathSpec:
    """Return a PathSpec whose patterns all match case-insensitively."""
    return pathspec.PathSpec.from_lines(_CaseInsensitiveGitWildMatchPattern, lines)


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
        self._specs: dict[Path, pathspec.PathSpec] = {}
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
        if path.name == IGNORE_FILENAME:
            return False
        root = self._root_of(path)
        if root is None:
            return False

        # Walk from root toward path; for each ancestor .dropboxignore, iterate
        # its patterns in order. Every matching pattern overwrites `matched`
        # with its include bit (True for positive, False for negation). Deeper
        # files come later in _ancestors, so their negations win over ancestors.
        matched = False
        for ancestor in self._ancestors(root, path):
            ignore_file = ancestor / IGNORE_FILENAME
            spec = self._specs.get(ignore_file)
            if spec is None:
                continue
            rel_str = path.relative_to(ancestor).as_posix()
            if path.is_dir():
                rel_str += "/"
            # If the path no longer exists, is_dir() returns False; callers
            # reconciling deleted paths should discard the result (design doc
            # §Failure modes: "deleted path → nothing to reconcile").
            for pattern in spec.patterns:
                if pattern.match_file(rel_str) is not None:
                    matched = bool(pattern.include)
        return matched

    # ---- internal helpers ------------------------------------------------

    def _load_file(self, ignore_file: Path) -> None:
        try:
            lines = ignore_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("Could not read %s: %s", ignore_file, exc)
            return
        try:
            spec = _build_spec(lines)
        except (ValueError, TypeError, re.error) as exc:
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

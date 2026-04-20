"""Hierarchical .dropboxignore rule cache (basic matching)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pathspec
from pathspec.patterns.gitwildmatch import GitIgnoreSpecPattern

from dropboxignore.roots import find_containing

logger = logging.getLogger(__name__)

IGNORE_FILENAME = ".dropboxignore"


class _CaseInsensitiveGitIgnorePattern(GitIgnoreSpecPattern):
    """GitIgnoreSpec pattern that compiles regex with re.IGNORECASE.

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
    return pathspec.PathSpec.from_lines(_CaseInsensitiveGitIgnorePattern, lines)


@dataclass(frozen=True)
class Match:
    """A single matching rule for the ``explain`` diagnostic."""

    ignore_file: Path
    line: int
    pattern: str
    negation: bool


@dataclass(frozen=True)
class _LoadedRules:
    """Parsed contents of one .dropboxignore file.

    ``entries`` is the single source of truth for both ``match()`` and
    ``explain()``: a list of ``(source_line_index, pattern)`` pairs, one per
    active rule (i.e. non-blank, non-comment, parses to a positive or negation
    pattern), in the order they appear in the file.
    """

    lines: list[str]
    entries: list[tuple[int, pathspec.Pattern]]


class RuleCache:
    """Maintains parsed rules from every .dropboxignore under the root(s)."""

    def __init__(self) -> None:
        self._rules: dict[Path, _LoadedRules] = {}
        self._roots: list[Path] = []

    def load_root(self, root: Path) -> None:
        root = root.resolve()
        if root not in self._roots:
            self._roots.append(root)
        for ignore_file in root.rglob(IGNORE_FILENAME):
            self._load_file(ignore_file)

    def reload_file(self, ignore_file: Path) -> None:
        """Re-read a single .dropboxignore file, replacing any cached version."""
        resolved = ignore_file.resolve()
        self._rules.pop(resolved, None)
        if resolved.exists():
            self._load_file(resolved)

    def remove_file(self, ignore_file: Path) -> None:
        """Drop all cached state for a .dropboxignore file (e.g. after deletion)."""
        self._rules.pop(ignore_file.resolve(), None)

    def match(self, path: Path) -> bool:
        path = path.resolve()
        if path.name == IGNORE_FILENAME:
            return False
        root = find_containing(path, self._roots)
        if root is None:
            return False

        # Walk root → path. For each ancestor .dropboxignore, iterate its
        # entries in source order; every matching pattern overwrites `matched`
        # with its include bit. Deeper ancestors come later, so their patterns
        # override shallower ones — gitignore's last-match-wins semantics.
        matched = False
        for ancestor, loaded in self._applicable(root, path):
            rel_str = self._rel_path_str(ancestor, path)
            for _line_idx, pattern in loaded.entries:
                if pattern.match_file(rel_str) is not None:
                    matched = bool(pattern.include)
        return matched

    def explain(self, path: Path) -> list[Match]:
        """Return the matching rules for ``path`` in rule-evaluation order.

        Each entry identifies which .dropboxignore file and which source line
        matched, plus whether the match was a negation. Useful for the
        ``dropboxignore explain`` CLI command.
        """
        path = path.resolve()
        if path.name == IGNORE_FILENAME:
            return []
        root = find_containing(path, self._roots)
        if root is None:
            return []

        results: list[Match] = []
        for ancestor, loaded in self._applicable(root, path):
            rel_str = self._rel_path_str(ancestor, path)
            for line_idx, pattern in loaded.entries:
                if pattern.match_file(rel_str) is not None:
                    raw_line = (
                        loaded.lines[line_idx]
                        if line_idx < len(loaded.lines) else ""
                    )
                    results.append(Match(
                        ignore_file=ancestor / IGNORE_FILENAME,
                        line=line_idx + 1,
                        pattern=raw_line,
                        negation=not bool(pattern.include),
                    ))
        return results

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
        self._rules[ignore_file.resolve()] = _LoadedRules(
            lines=lines,
            entries=_build_entries(lines, spec),
        )

    def _applicable(
        self, root: Path, path: Path
    ) -> list[tuple[Path, _LoadedRules]]:
        """Yield (ancestor, loaded_rules) for each applicable .dropboxignore
        in shallow-to-deep order."""
        result: list[tuple[Path, _LoadedRules]] = []
        for ancestor in self._ancestors(root, path):
            loaded = self._rules.get(ancestor / IGNORE_FILENAME)
            if loaded is not None:
                result.append((ancestor, loaded))
        return result

    @staticmethod
    def _rel_path_str(ancestor: Path, path: Path) -> str:
        rel_str = path.relative_to(ancestor).as_posix()
        # Directory-only rules (e.g. `node_modules/`) only fire when the
        # tested path string ends in `/`. If the path no longer exists
        # is_dir() returns False; callers reconciling deleted paths discard
        # the result (design doc §Failure modes).
        if path.is_dir():
            rel_str += "/"
        return rel_str

    def _ancestors(self, root: Path, path: Path) -> list[Path]:
        """Return [root, ...intermediate dirs..., path's parent] inclusive."""
        rel = path.relative_to(root)
        result = [root]
        current = root
        for part in rel.parts[:-1]:
            current = current / part
            result.append(current)
        return result


def _build_entries(
    lines: list[str], spec: pathspec.PathSpec
) -> list[tuple[int, pathspec.Pattern]]:
    """Pair each active source line with its compiled pattern.

    Fast path: filter ``spec.patterns`` to active entries (``include is not
    None``) and zip with source-line indices whose stripped content is
    non-blank and not a leading-``#`` comment. The two counts usually match.

    Fallback: if they don't (pathspec treating an edge case like a leading-
    whitespace ``#`` line as a pattern), reparse each source line individually
    to keep ``(source_line_index, pattern)`` pairing correct.
    """
    active_line_indices = [
        i for i, raw in enumerate(lines)
        if raw.strip() and not raw.strip().startswith("#")
    ]
    active_patterns = [p for p in spec.patterns if p.include is not None]
    if len(active_line_indices) == len(active_patterns):
        return list(zip(active_line_indices, active_patterns, strict=True))

    entries: list[tuple[int, pathspec.Pattern]] = []
    for i in active_line_indices:
        try:
            single = _build_spec([lines[i]])
        except (ValueError, TypeError, re.error):
            continue
        for p in single.patterns:
            if p.include is not None:
                entries.append((i, p))
                break
    return entries

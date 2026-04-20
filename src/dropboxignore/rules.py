"""Hierarchical .dropboxignore rule cache (basic matching)."""

from __future__ import annotations

import logging
import os
import re
import threading
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

    ``mtime_ns`` and ``size`` are the file's stat values at load time, used by
    ``load_root`` to skip reparsing files whose on-disk bytes are unchanged.
    """

    lines: list[str]
    entries: list[tuple[int, pathspec.Pattern]]
    mtime_ns: int
    size: int


class RuleCache:
    """Maintains parsed rules from every .dropboxignore under the root(s)."""

    def __init__(self) -> None:
        self._rules: dict[Path, _LoadedRules] = {}
        self._roots: list[Path] = []
        # Guards every mutation and every iteration of self._rules. Single-op
        # reads via self._rules.get(...) stay lock-free (GIL-atomic). Needed
        # because the daemon's sweep thread runs load_root() concurrently with
        # the debouncer worker's reload_file() / remove_file(), and load_root's
        # stale-purge iterates self._rules — a concurrent pop would raise
        # "dictionary changed size during iteration". RLock allows load_root
        # to nest into _load_if_changed → _load_file without deadlocking.
        self._lock = threading.RLock()

    def load_root(self, root: Path) -> None:
        root = root.resolve()
        with self._lock:
            if root not in self._roots:
                self._roots.append(root)
            seen: set[Path] = set()
            for ignore_file in root.rglob(IGNORE_FILENAME):
                seen.add(ignore_file.resolve())
                self._load_if_changed(ignore_file)
            # Drop cached entries for .dropboxignore files under this root that
            # rglob didn't find — they've been deleted since the last load and
            # their rules must stop applying.
            for stale in [
                p for p in self._rules
                if p not in seen and p.is_relative_to(root)
            ]:
                del self._rules[stale]

    def reload_file(self, ignore_file: Path) -> None:
        """Re-read a single .dropboxignore file, replacing any cached version."""
        with self._lock:
            self._rules.pop(ignore_file.resolve(), None)
            self._load_file(ignore_file)

    def remove_file(self, ignore_file: Path) -> None:
        """Drop all cached state for a .dropboxignore file (e.g. after deletion)."""
        with self._lock:
            self._rules.pop(ignore_file.resolve(), None)

    def match(self, path: Path) -> bool:
        if not path.is_absolute():
            raise ValueError(f"match() requires an absolute path; got {path!r}")
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
        if not path.is_absolute():
            raise ValueError(f"explain() requires an absolute path; got {path!r}")
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

    def _load_if_changed(self, ignore_file: Path) -> None:
        """Load ``ignore_file`` only if its on-disk bytes differ from the
        cached version (mtime or size mismatch). No-op if unchanged.

        Used by the sweep path (``load_root``) to avoid reparsing every
        .dropboxignore every hour. ``reload_file`` bypasses this check — a
        watchdog event is an explicit signal to reload regardless of stat.
        """
        try:
            st = ignore_file.stat()
        except OSError:
            # Can't stat — let _load_file's read path surface the same error.
            self._load_file(ignore_file)
            return
        cached = self._rules.get(ignore_file.resolve())
        if cached and cached.mtime_ns == st.st_mtime_ns and cached.size == st.st_size:
            return
        self._load_file(ignore_file, st=st)

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

    # _load_file already validated the bulk parse, and pathspec 1.0.4's
    # single-line parse is consistent with bulk — if bulk succeeded, every
    # line parses individually too. No try/except needed; a raise here
    # would signal a real pathspec-version regression worth surfacing.
    entries: list[tuple[int, pathspec.Pattern]] = []
    for i in active_line_indices:
        for p in _build_spec([lines[i]]).patterns:
            if p.include is not None:
                entries.append((i, p))
                break
    return entries

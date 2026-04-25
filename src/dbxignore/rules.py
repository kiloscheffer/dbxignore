"""Hierarchical .dropboxignore rule cache (basic matching)."""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pathspec
from pathspec.patterns.gitwildmatch import GitIgnoreSpecPattern

from dbxignore.roots import find_containing

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


def literal_prefix(pattern: str) -> str | None:
    """Return the leading literal path segments of a gitignore pattern.

    The returned value is the prefix up to (and including) the last ``/``
    before the first glob metacharacter (``*``, ``?``, ``[``), or the whole
    pattern if it contains no glob. A leading ``/`` anchor is stripped.

    Returns ``None`` when there is no literal anchor — e.g. patterns that
    begin with a glob (``**/cache/``), or that place a glob inside the first
    segment (``foo*/bar/``). The detection layer uses ``None`` to skip
    conflict analysis for that pattern (documented limitation).

    Input is the path portion of a gitignore pattern. Callers should pass
    the raw line with any leading ``!`` already stripped — pathspec
    already tracks include vs. negation via ``pattern.include``.
    """
    if not pattern:
        return None
    p = pattern.lstrip("/")
    if not p:
        return None
    boundary = next(
        (i for i, c in enumerate(p) if c in "*?["),
        len(p),
    )
    if boundary < len(p):
        last_sep = p[:boundary].rfind("/")
        if last_sep == -1:
            return None
        return p[:last_sep + 1]
    # No glob present: return whole pattern. If it ends in `/`, we keep the
    # trailing slash; otherwise we cut at the last `/` so the prefix is a
    # directory-shaped string (the detector walks directory ancestors).
    if "/" not in p:
        return p
    if p.endswith("/"):
        return p
    last_sep = p.rfind("/")
    return p[:last_sep + 1]


@dataclass(frozen=True)
class Match:
    """A single matching rule for the ``explain`` diagnostic."""

    ignore_file: Path
    line: int
    pattern: str
    negation: bool
    is_dropped: bool = False


@dataclass(frozen=True)
class Conflict:
    """A dropped negation rule and the earlier include rule that masks it.

    Emitted by ``RuleCache._recompute_conflicts`` when a negation's literal
    prefix lives under a directory matched by an earlier include rule —
    Dropbox's ignored-folder inheritance makes such negations inert. Used
    for the WARNING log, ``dbxignore status`` reporting, and the
    ``[dropped]`` annotation in ``explain()`` output.
    """

    dropped_source: Path      # the .dropboxignore file containing the negation
    dropped_line: int         # 1-based source line of the negation
    dropped_pattern: str      # raw pattern text (e.g. "!build/keep/")
    masking_source: Path      # the .dropboxignore file containing the include
    masking_line: int         # 1-based source line of the masking include
    masking_pattern: str      # raw pattern text (e.g. "build/")


def _ancestors_of(prefix: str, ancestor_dir: Path, root: Path) -> list[Path]:
    """Yield absolute ancestor directory paths for a negation's literal prefix.

    The negation's literal prefix is relative to its own ``.dropboxignore``
    file's directory (``ancestor_dir``). We produce absolute directory paths
    starting from the prefix itself (if it's a directory shape) and walking
    up to ``root``, inclusive.

    Example: prefix=``build/keep/``, ancestor_dir=``/root``, root=``/root``
    yields ``[/root/build/keep, /root/build, /root]``.
    """
    # Resolve the prefix against its scoping directory and strip the trailing
    # slash so we can navigate via Path.parent.
    # NOTE: .resolve() here is intentional — do not "optimize" it out. Two
    # reasons: (1) cost is bounded — _detect_conflicts fires only on rule
    # mutations (load_root / reload_file / remove_file), not the steady-state
    # sweep, and resolves exactly one path per negation rule; (2) downstream
    # is_relative_to(root) and equality checks below assume canonical paths,
    # so without resolution a symlink or `..` component could fool both into
    # disagreeing on path identity and missing valid ancestors.
    target = (ancestor_dir / prefix.rstrip("/")).resolve()
    results: list[Path] = []
    current = target
    while True:
        results.append(current)
        if current == root:
            break
        if not current.is_relative_to(root):
            # Target escapes the root (unusual; likely malformed rule). Stop.
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return results


def _find_masking_include(
    earlier_entries: list, ancestors: list[Path]
) -> object | None:
    """Return the first earlier include whose pattern matches any ancestor.

    The ancestor is expressed as a path relative to each include's
    ``ancestor_dir`` (directory-shaped, with trailing slash), so pathspec's
    directory-rule matching fires.
    """
    for earlier in earlier_entries:
        if not earlier.pattern.include:
            continue
        for anc in ancestors:
            try:
                rel = anc.relative_to(earlier.ancestor_dir).as_posix() + "/"
            except ValueError:
                # This ancestor isn't under the earlier rule's scope.
                continue
            if earlier.pattern.match_file(rel) is not None:
                return earlier
    return None


def _detect_conflicts(
    sequence: list, *, root: Path
) -> list[Conflict]:
    """Static rule-conflict detection.

    Input ``sequence`` is a list of entries in evaluation order. Each entry
    must expose ``source`` (Path), ``line`` (int, 1-based), ``raw`` (str,
    the source-line text), ``ancestor_dir`` (Path, the scoping directory
    of the pattern), and ``pattern`` (a pathspec pattern with ``.include``
    and ``.match_file``).

    Returns one ``Conflict`` per negation entry whose literal prefix is
    matched-as-ignored by any earlier include rule in the sequence.
    Skips negations whose pattern has no extractable literal prefix
    (documented limitation for glob-prefix patterns).
    """
    conflicts: list[Conflict] = []
    for i, entry in enumerate(sequence):
        if entry.pattern.include:
            continue  # include rules are potential masks, not subjects
        # Strip the leading `!` before extracting the literal prefix.
        raw = entry.raw.lstrip()
        if raw.startswith("!"):
            raw = raw[1:]
        prefix = literal_prefix(raw)
        if prefix is None:
            continue
        if not prefix.endswith("/"):
            # File-level target; Dropbox's ignored-folder inheritance only
            # applies to directories. A rule like `*.log` + `!important.log`
            # has no ancestor-inheritance conflict to flag.
            continue
        ancestors = _ancestors_of(prefix, entry.ancestor_dir, root)

        masking = _find_masking_include(sequence[:i], ancestors)
        if masking is None:
            continue
        conflicts.append(Conflict(
            dropped_source=entry.source,
            dropped_line=entry.line,
            dropped_pattern=entry.raw.strip(),
            masking_source=masking.source,
            masking_line=masking.line,
            masking_pattern=masking.raw.strip(),
        ))
    return conflicts


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


class _PatternLike(Protocol):
    """Structural type for pattern objects consumed by conflict detection
    and rule evaluation. Satisfied by ``GitIgnoreSpecPattern`` (production)
    and ``_FakePattern`` in ``tests/test_rules_conflicts.py`` (unit tests).
    Only the two attributes listed below are read; pattern objects may
    expose more without breaking the contract."""

    include: bool | None
    def match_file(self, path: str) -> bool | None: ...


@dataclass(frozen=True)
class _SequenceEntry:
    """One rule in the flattened evaluation-order sequence used by
    conflict detection. Internal to RuleCache."""

    source: Path           # the .dropboxignore file this rule came from
    line: int              # 1-based source line number
    raw: str               # source-line text (without trailing newline)
    ancestor_dir: Path     # directory the pattern is scoped to
    pattern: _PatternLike  # GitIgnoreSpecPattern at runtime; see _PatternLike


class RuleCache:
    """Maintains parsed rules from every .dropboxignore under the root(s)."""

    def __init__(self) -> None:
        self._rules: dict[Path, _LoadedRules] = {}
        self._roots: list[Path] = []
        # load_root's stale-purge iterates self._rules while the debouncer
        # thread may pop/insert; without this lock that's "dictionary changed
        # size during iteration". RLock so load_root can nest into _load_file.
        self._lock = threading.RLock()
        # Detection state — recomputed on every mutation. Keyed by
        # (ignore_file_path, line_idx) so match()/explain() can filter
        # without rebuilding per call.
        self._dropped: set[tuple[Path, int]] = set()
        self._conflicts: list[Conflict] = []

    def load_root(self, root: Path, *, log_warnings: bool = True) -> None:
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
            self._recompute_conflicts(log_warnings=log_warnings)

    def reload_file(self, ignore_file: Path, *, log_warnings: bool = True) -> None:
        """Re-read a single .dropboxignore file, replacing any cached version."""
        with self._lock:
            self._rules.pop(ignore_file.resolve(), None)
            self._load_file(ignore_file)
            self._recompute_conflicts(log_warnings=log_warnings)

    def remove_file(self, ignore_file: Path, *, log_warnings: bool = True) -> None:
        """Drop all cached state for a .dropboxignore file (e.g. after deletion)."""
        with self._lock:
            self._rules.pop(ignore_file.resolve(), None)
            self._recompute_conflicts(log_warnings=log_warnings)

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
            ignore_file = ancestor / IGNORE_FILENAME
            for line_idx, pattern in loaded.entries:
                if (ignore_file, line_idx) in self._dropped:
                    continue
                if pattern.match_file(rel_str) is not None:
                    matched = bool(pattern.include)
        return matched

    def explain(self, path: Path) -> list[Match]:
        """Return the matching rules for ``path`` in rule-evaluation order.

        Each entry identifies which .dropboxignore file and which source line
        matched, plus whether the match was a negation. Useful for the
        ``dbxignore explain`` CLI command.

        Unlike ``match()``, ``explain()`` includes rules that were dropped
        from the active rule set by conflict detection — each such entry
        has ``is_dropped=True`` so the CLI can annotate it with a
        ``[dropped]`` marker and a masked-by pointer.
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
            ignore_file = ancestor / IGNORE_FILENAME
            for line_idx, pattern in loaded.entries:
                if pattern.match_file(rel_str) is None:
                    continue
                raw_line = (
                    loaded.lines[line_idx]
                    if line_idx < len(loaded.lines) else ""
                )
                results.append(Match(
                    ignore_file=ignore_file,
                    line=line_idx + 1,
                    pattern=raw_line,
                    negation=not bool(pattern.include),
                    is_dropped=(ignore_file, line_idx) in self._dropped,
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

    def conflicts(self) -> list[Conflict]:
        """Current conflicts across all loaded roots, in detection order."""
        with self._lock:
            return list(self._conflicts)

    def _recompute_conflicts(self, *, log_warnings: bool = True) -> None:
        """Rebuild _dropped and _conflicts from the current _rules.

        Called after any mutation (load_root, reload_file, remove_file).
        Caller must hold self._lock.

        Writes new containers and swaps the attribute references atomically
        so lock-free readers (``match()``, ``explain()``) never see a
        torn intermediate state.

        When ``log_warnings`` is True (the default — appropriate for the
        daemon's reconcile path), each detected conflict emits a WARNING
        record. CLI one-shots that surface conflicts via structured stdout
        (``status``, ``explain``) should pass ``log_warnings=False`` to
        avoid stderr duplication.
        """
        new_dropped: set[tuple[Path, int]] = set()
        new_conflicts: list[Conflict] = []
        for root in self._roots:
            sequence = self._build_sequence(root)
            for c in _detect_conflicts(sequence, root=root):
                new_conflicts.append(c)
                # _build_sequence stores line=line_idx+1 (1-based); _dropped
                # is keyed by 0-based line_idx because that's what
                # `loaded.entries` yields and what match()/explain() iterate.
                line_idx = c.dropped_line - 1
                new_dropped.add((c.dropped_source, line_idx))
                if log_warnings:
                    logger.warning(
                        "negation `%s` at %s:%d is masked by include `%s` at %s:%d "
                        "(Dropbox inherits ignored state from ancestor directories). "
                        "Dropping the negation from the active rule set. "
                        "See README §Gotchas.",
                        c.dropped_pattern, c.dropped_source, c.dropped_line,
                        c.masking_pattern, c.masking_source, c.masking_line,
                    )
        self._dropped = new_dropped
        self._conflicts = new_conflicts

    def _build_sequence(self, root: Path) -> list[_SequenceEntry]:
        """Flatten all .dropboxignore rules under root into evaluation order.

        Shallower files first; within a file, source-line order. Caller
        must hold self._lock — this iterates self._rules.
        """
        files_under_root = sorted(
            (p for p in self._rules if p.is_relative_to(root)),
            key=lambda p: (len(p.parts), p.as_posix()),
        )
        sequence: list[_SequenceEntry] = []
        for ignore_file in files_under_root:
            loaded = self._rules[ignore_file]
            ancestor_dir = ignore_file.parent
            for line_idx, pattern in loaded.entries:
                raw = (
                    loaded.lines[line_idx]
                    if line_idx < len(loaded.lines) else ""
                )
                sequence.append(_SequenceEntry(
                    source=ignore_file,
                    line=line_idx + 1,
                    raw=raw,
                    ancestor_dir=ancestor_dir,
                    pattern=pattern,
                ))
        return sequence


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

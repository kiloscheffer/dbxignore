"""Hierarchical .dropboxignore rule cache (basic matching)."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import stat
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pathspec
from pathspec.patterns.gitwildmatch import GitIgnoreSpecPattern  # type: ignore[attr-defined]

from dbxignore._logging import timed_debug
from dbxignore.roots import find_containing
from dbxignore.rules_conflicts import Conflict as Conflict
from dbxignore.rules_conflicts import _detect_conflicts

logger = logging.getLogger(__name__)

IGNORE_FILENAME = ".dropboxignore"


def is_ignore_filename(name: str) -> bool:
    """Return True if ``name`` is the canonical rule filename in any casing.

    Used by ``RuleCache.match`` / ``explain``, ``daemon._classify`` /
    ``_moved_dest_under_root`` / ``_dispatch``, ``reconcile._reconcile_path``,
    and ``cli._walk_marked_paths`` to recognize ``.dropboxignore`` files
    consistently with how ``RuleCache.load_root`` discovers them
    (case-insensitively). A ``.DropboxIgnore`` is treated as a rule file
    across discovery, watchdog events, match queries, and walks — the
    project's "case-insensitive everywhere" posture (per
    ``_CaseInsensitiveGitIgnorePattern``) extended end-to-end.
    """
    return name.lower() == IGNORE_FILENAME


def _is_real_dir(path: Path) -> bool:
    """Return True iff ``path`` is a directory and not a symlink.

    Equivalent to ``path.is_dir() and not path.is_symlink()`` but costs
    one ``lstat()`` syscall instead of two. ``S_ISDIR`` on an ``lstat()``
    result is False for symlinks regardless of target type — `lstat`
    reports the link's own mode (`S_IFLNK`), not the target's. That
    matches the symlinks-are-leaves invariant from PR #191's
    ``format_literal_rule``. All three call sites use this helper:
    ``cache.match`` / ``cache.explain`` (daemon hot path) and
    ``format_literal_rule`` (cold path).

    On `OSError` (vanished path, permission denied), returns False.
    """
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def _canonical_cache_key(path: Path) -> Path:
    """Return the canonical cache key for a rule-file path.

    Always uses the lowercase basename ``IGNORE_FILENAME`` regardless of
    the input path's on-disk casing. This is critical on case-sensitive
    filesystems (Linux ext4, case-sensitive APFS) where ``PosixPath``
    equality is case-sensitive — without normalization, ``load_root``
    storing under canonical key while ``reload_file`` / ``remove_file``
    keying off the watchdog event's mixed-case path would create two
    distinct cache entries for the same logical rule file.

    The parent directory is resolved (handles symlinks, trailing
    separators, etc.) but the basename is replaced with the canonical
    spelling. Callers must hold the cache lock if mutating ``self._rules``
    using the returned key.
    """
    return path.parent.resolve() / IGNORE_FILENAME


def _resolve_to_canonical_sibling(ignore_file: Path) -> Path:
    """Return the canonical ``.dropboxignore`` sibling if it exists,
    else ``ignore_file`` unchanged. Identity is preserved when no
    redirect happens, so callers can detect "did we redirect?" via
    ``result is not ignore_file``.

    Mirrors ``load_root``'s prefer-exact-match selection at the
    watchdog seam (``reload_file`` / ``remove_file``) so a shadowed
    mixed-case sibling never overwrites or evicts the canonical's
    cache entry. The single ``is_file()`` stat may flap on transient
    I/O errors; the redirect is then skipped and the next watchdog
    event or hourly sweep recovers — bounded transient deviation,
    strictly better than the no-redirect shape's "always wrong when
    both files coexist" mode.
    """
    if ignore_file.name == IGNORE_FILENAME:
        return ignore_file
    canonical_path = ignore_file.parent / IGNORE_FILENAME
    if canonical_path.is_file():
        return canonical_path
    return ignore_file


# Why not pathspec's GitIgnoreSpecPattern.escape()? It escapes `!` and `#`
# everywhere, but gitignore only treats them specially at column 0 of the
# whole line. Per-segment use of escape() would over-escape (e.g.
# proj/!subdir/ would become proj/\!subdir/), correct-but-noisy. The
# split design here matches gitignore semantics exactly: per-segment
# escape for inline meta-chars (*, ?, [, ], \), then a separate
# leading-segment guard for ! and # that fires only on the first segment.
#
# gitignore meta-chars that need backslash-escaping when our rule generator
# encounters them as literal directory-name characters. The set tracks
# pathspec.GitIgnoreSpec's interpretation: `*` and `?` are wildcards, `[`
# and `]` delimit a character class, `\` is the escape char itself. `!` and
# `#` only matter when they're the first non-whitespace character of the line
# (negation marker / comment marker), so they're handled separately below.
_META_CHARS_INLINE = frozenset("*?[]\\")


def format_literal_rule(target: Path, rule_file: Path) -> str:
    """Return a gitignore-anchored literal-path rule for ``target``.

    The result is the rule line that, when written to ``rule_file``, matches
    exactly ``target`` and no other path. Used by ``cli.ignore`` to compute
    the rule to append, and by ``cli.unignore`` to compute the canonical
    rule to compare against existing rules for removal.

    Construction:

    1. Compute ``target.relative_to(rule_file.parent)`` — raises ``ValueError``
       if ``target`` is not under the rule file's directory (a caller bug;
       rule-file selection should always pick an ancestor).
    2. Escape gitignore inline meta-chars (``*``, ``?``, ``[``, ``]``, ``\\``)
       per segment with a leading backslash.
    3. If the FIRST segment starts with ``!`` (negation marker) or ``#``
       (column-0 comment marker), prepend a backslash so pathspec parses
       the line as an active pattern instead of a negation or comment.
    4. Re-join segments with ``/`` (gitignore separator, regardless of
       host OS) and prepend a leading ``/``.
    5. If ``target`` is a real directory (not a symlink), append ``/`` to
       make the rule directory-only (matches the directory itself, not all
       paths whose basename equals the directory name).

    The leading ``/`` anchors the rule to the rule file's directory. Without
    it, a single-segment rule like ``build/`` matches every ``build/``
    directory anywhere under the rule file's mount per gitignore's "no
    separator before/within the pattern" semantics — Dropbox would mark
    unrelated subtrees ignored. Multi-segment rules are already anchored by
    their mid-pattern slash; the leading ``/`` is redundant but harmless for
    them.
    """
    relative = target.relative_to(rule_file.parent)
    parts = relative.parts
    for p in parts:
        # Reject any whitespace character except space. Newline-class
        # separators (ASCII \r/\n/\v/\f, FS/GS/RS at \x1c-\x1e, NEL \x85,
        # Unicode line separators U+2028/U+2029) would split the rule line
        # via str.splitlines() at read-back time, effectively injecting
        # extra rules. Tabs/FF/VT are silently stripped by pathspec at
        # end-of-line with no reliable escape. Other Unicode whitespace
        # (NBSP, etc.) has the same end-of-line strip risk. Space is the
        # only whitespace gitignore can safely encode (via backslash escape
        # in _escape_segment).
        bad = sorted(c for c in set(p) if c.isspace() and c != " ")
        if bad:
            chars = ", ".join(repr(c) for c in bad)
            raise ValueError(
                f"path component {p!r} contains non-space whitespace ({chars}); "
                "cannot be safely encoded as a gitignore rule"
            )
    escaped = [_escape_segment(p) for p in parts]
    if escaped and escaped[0].startswith(("!", "#")):
        escaped[0] = "\\" + escaped[0]
    line = "/" + "/".join(escaped)
    # Append trailing `/` only for real directories — NOT for symlinks
    # (regardless of what they link to). Symlinks should produce rules
    # matching the link object itself per the project's "markers attach
    # to the link, not the target" invariant; gitignore's directory-only
    # patterns (with trailing `/`) follow the link and match the target,
    # which is the wrong semantic here.
    if _is_real_dir(target):
        line += "/"
    return line


def _escape_segment(segment: str) -> str:
    """Backslash-escape gitignore inline meta-chars in one path segment.

    Also escapes trailing whitespace per gitignore's "trailing spaces are
    ignored unless quoted with backslash" rule — without this, a file named
    ``foo `` would produce rule ``foo `` that pathspec parses as matching
    ``foo``, not ``foo ``. Applies to every segment uniformly: harmless for
    mid-path segments (the next ``/`` separator already prevents trailing-space
    strip) and load-bearing for the last segment when the target is a file.
    """
    escaped = "".join("\\" + c if c in _META_CHARS_INLINE else c for c in segment)
    stripped = escaped.rstrip(" ")
    if stripped != escaped:
        trailing = len(escaped) - len(stripped)
        escaped = stripped + "\\ " * trailing
    return escaped


_FILE_HEADER = "# .dropboxignore — managed by dbxignore\n"


def append_rule(rule_file: Path, rule_line: str) -> bool:
    """Atomic append of ``rule_line`` to ``rule_file``.

    Always appends — does NOT deduplicate against existing identical lines.
    A ``.dropboxignore`` may legitimately contain duplicate or masked rules
    (e.g., ``/build/`` followed by a later ``!/build/`` then a re-anchor),
    and gitignore's last-match-wins semantics depend on the order. Callers
    should gate this via ``cache.match`` upstream — the CLI's ``ignore``
    verb only calls this helper when the path is NOT currently ignored,
    so an extra duplicate at the end is exactly what makes the rule take
    effect (overriding any earlier negation).

    Atomic via temp-then-replace, mirroring ``state.write()``: writes the
    content to a unique sibling temp file, then ``os.replace`` into place.
    Survives SIGKILL or power loss mid-write — the file is either fully
    updated or unchanged. The temp name is unique (``mkstemp``-generated)
    rather than the fixed ``<rule_file>.tmp`` that previously could collide
    with a concurrent CLI mutation, an editor's atomic-save backup, or a
    user-created temp file (item #101). Still doesn't prevent lost updates
    when two writers race the read-modify-write itself; an advisory lock
    would be needed for that and is deferred until a concrete failure shows.

    Returns True. (The previous return-False idempotent-skip semantics were
    removed because they masked the override-via-duplicate behavior gitignore
    requires for negation-override.)
    """
    if rule_file.exists():
        content = rule_file.read_text(encoding="utf-8")
        existing_lines = content.splitlines()
        if existing_lines:
            # Ensure the existing content ends with a newline so our appended
            # line lands on its own line. ``splitlines()`` already ate a
            # trailing newline if present, so we always rebuild with explicit
            # \n joins.
            new_content = "\n".join(existing_lines) + "\n" + rule_line + "\n"
        else:
            # Empty file — treat like a missing file and write header + rule
            # so the output doesn't start with a leading blank line.
            new_content = _FILE_HEADER + rule_line + "\n"
    else:
        new_content = _FILE_HEADER + rule_line + "\n"

    _atomic_write_rule_file(rule_file, new_content)
    return True


def remove_rule(rule_file: Path, rule_line: str) -> int:
    """Atomic remove-all-rstrip-matches of ``rule_line`` from ``rule_file``.

    Returns the count of removed lines. Returns 0 (and does not error) if
    the file doesn't exist or the line is not present. Atomic via
    temp-then-replace; the file is either fully rewritten or untouched.
    Not safe against concurrent writers; intended for serial CLI invocation.

    rstrip-equality (rather than exact-string equality) tolerates manually-
    typed rules with trailing whitespace, mirroring pathspec's
    gitignore-trailing-whitespace semantics.
    """
    if not rule_file.exists():
        logger.warning(
            "remove_rule called against missing file %s; rule %r treated as already absent",
            rule_file,
            rule_line,
        )
        return 0
    target_norm = rule_line.rstrip()
    content = rule_file.read_text(encoding="utf-8")
    existing_lines = content.splitlines()
    kept = [line for line in existing_lines if line.rstrip() != target_norm]
    removed_count = len(existing_lines) - len(kept)
    if removed_count == 0:
        return 0
    new_content = "\n".join(kept) + ("\n" if kept else "")
    _atomic_write_rule_file(rule_file, new_content)
    return removed_count


def _atomic_write_rule_file(rule_file: Path, new_content: str) -> None:
    """Write ``new_content`` to ``rule_file`` atomically via a unique temp.

    Uses ``tempfile.mkstemp`` to pick a non-colliding name in ``rule_file``'s
    parent directory (same filesystem, so ``os.replace`` is atomic), then
    closes and ``os.replace``s into place. The unique temp name prevents
    the collisions item #101 documents: two concurrent CLI mutations, an
    editor's backup temp file, or a stray user-created
    ``.dropboxignore.tmp`` would all have raced the old fixed name.

    ``mkstemp`` creates its temp at mode ``0o600`` on POSIX (a sensible
    default for sensitive temp files), so the write happens, then
    ``os.chmod`` restores the intended rule-file mode before the replace.
    The intended mode is the existing file's current mode (preserving any
    group-readable / shared-workflow configuration the user set), or for a
    new file the umask-based default ``0o666 & ~umask`` that the prior
    ``Path.write_text`` shape produced. Without this restore step, every
    ``ignore`` / ``unignore`` invocation would silently relock the rule
    file to ``0o600`` — breaking shared workflows where another user or a
    different process account needs to read it.
    """
    try:
        target_mode = rule_file.stat().st_mode & 0o777
    except FileNotFoundError:
        # Read-then-restore the umask to compute the default mode a fresh
        # `open(O_CREAT, 0o666)` would have produced. Race-free under our
        # single-threaded CLI invocation; intentionally NOT thread-safe
        # against parallel `os.umask` callers (none exist here).
        current_umask = os.umask(0o022)
        os.umask(current_umask)
        target_mode = 0o666 & ~current_umask

    fd, tmp_str = tempfile.mkstemp(dir=rule_file.parent, prefix=f"{rule_file.name}.", suffix=".tmp")
    tmp = Path(tmp_str)
    try:
        # `newline=""` disables the default text-mode `\n` → `\r\n` translation
        # on Windows. Gitignore-style files are LF-canonical; the prior
        # `Path.write_text(...)` shape inherited Python's default text-mode
        # translation accidentally, producing CRLF on Windows and LF elsewhere
        # for byte-identical inputs. Pin LF everywhere now that the writer
        # is consolidated.
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as tmp_file:
            tmp_file.write(new_content)
        os.chmod(tmp, target_mode)
        os.replace(tmp, rule_file)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


class _CaseInsensitiveGitIgnorePattern(GitIgnoreSpecPattern):
    """GitIgnoreSpec pattern that compiles regex with re.IGNORECASE.

    Windows NTFS is case-insensitive; a rule written as ``node_modules/`` must
    match a directory literally named ``Node_Modules`` on disk.
    """

    @classmethod
    def pattern_to_regex(  # type: ignore[override]
        cls, pattern: str
    ) -> tuple[str | None, bool | None]:
        # The parent (`pathspec.pattern.RegexPattern`) types `pattern` as
        # `AnyStr` to allow both bytes and str patterns; the gitwildmatch
        # subclass we extend only ever receives str at runtime, so we narrow
        # the override to `str` for clarity and accept the type-checker
        # complaint about variance (`# type: ignore[override]` above).
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
    is_dropped: bool = False


@dataclass(frozen=True)
class _LoadedRules:
    """Parsed contents of one .dropboxignore file.

    ``entries`` is the single source of truth for both ``match()`` and
    ``explain()``: a list of ``(source_line_index, pattern)`` pairs, one per
    active rule (i.e. non-blank, non-comment, parses to a positive or negation
    pattern), in the order they appear in the file.

    ``content_hash`` is a blake2b-128 digest of the file's bytes at load
    time, used by ``_load_if_changed`` to skip reparsing files whose content
    is unchanged. Replaces the prior ``(mtime_ns, size)`` shortcut, which
    missed same-size edits with preserved mtimes (item #102 — common when
    editors or ``touch -r`` restore the original timestamp after a save).

    ``mtime_ns`` and ``size`` remain on the dataclass for diagnostic value
    and to keep the existing private-API touches in
    ``tests/test_rules_basic.py`` working; they no longer drive the
    cache-invalidation gate.
    """

    lines: list[str]
    entries: list[tuple[int, pathspec.Pattern]]
    content_hash: bytes
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

    source: Path  # the .dropboxignore file this rule came from
    line: int  # 1-based source line number
    raw: str  # source-line text (without trailing newline)
    ancestor_dir: Path  # directory the pattern is scoped to
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

    def load_root(
        self,
        root: Path,
        *,
        log_warnings: bool = True,
        stop_event: threading.Event | None = None,
    ) -> None:
        root = root.resolve()
        with self._lock:
            if root not in self._roots:
                self._roots.append(root)
            seen: set[Path] = set()
            for current_dir, _dirnames, filenames in os.walk(root, followlinks=False):
                # Cooperative cancellation per directory visited (item #86).
                # The previous shape used `root.rglob(IGNORE_FILENAME)` and
                # checked between yields — fine for trees with many rule
                # files, but coarse for trees with many directories and few
                # rule files (rglob's internal traversal between yields can
                # do thousands of stat calls before the next yield, blocking
                # SIGTERM observation for tens of seconds). os.walk yields
                # one tuple per directory regardless of whether any rule
                # file is present, so the check fires every directory.
                # Returning here skips the stale-purge step intentionally —
                # purging against an incomplete `seen` set would corrupt the
                # cache by dropping entries that simply weren't reached.
                if stop_event is not None and stop_event.is_set():
                    return
                # Detect rule files via the already-materialized filenames
                # list (one scandir per directory, no separate stat per
                # file). A fresh `Path.is_file()` stat would flap under the
                # same transient read errors `_load_file`'s OSError arm
                # explicitly preserves cached rules through, dropping the
                # cache entry to the stale-purge below.
                #
                # Match case-insensitively to recover `rglob`'s prior
                # discovery behavior on case-insensitive filesystems
                # (Windows NTFS, default macOS APFS/HFS+) where a
                # `.DropboxIgnore` would be found by a `.dropboxignore`
                # query. The exact-match check fires first so a canonical
                # file always wins over a mixed-case sibling on case-
                # sensitive filesystems where both could coexist. (PR #184)
                if IGNORE_FILENAME in filenames:
                    match_name = IGNORE_FILENAME
                else:
                    candidate = next(
                        (f for f in filenames if f.lower() == IGNORE_FILENAME),
                        None,
                    )
                    if candidate is None:
                        continue
                    match_name = candidate
                # Read uses on-disk casing; cache keys use canonical
                # lowercase so `PosixPath` equality (case-sensitive on
                # POSIX) doesn't split entries between `match()`'s
                # `ancestor / IGNORE_FILENAME` lookup and `load_root`'s
                # discovered-path storage (PR #184).
                ignore_file = Path(current_dir) / match_name
                canonical = Path(current_dir) / IGNORE_FILENAME
                # Skip stale-purge tracking on unresolvable paths (e.g.
                # symlink loops). `_load_if_changed`'s own resolve-failure
                # arm logs the underlying issue.
                try:
                    seen.add(canonical.resolve())
                except (OSError, RuntimeError) as exc:
                    logger.warning("Could not resolve %s during sweep: %s", ignore_file, exc)
                    continue
                self._load_if_changed(ignore_file, as_path=canonical)
            # Drop cached entries for .dropboxignore files under this root that
            # the walk didn't find — they've been deleted since the last load
            # and their rules must stop applying.
            for stale in [p for p in self._rules if p not in seen and p.is_relative_to(root)]:
                del self._rules[stale]
            self._recompute_conflicts(log_warnings=log_warnings)

    def reload_file(self, ignore_file: Path, *, log_warnings: bool = True) -> None:
        """Re-read a single .dropboxignore file, replacing any cached version.

        Mirrors ``load_root``'s prefer-exact-match precedence: a watchdog
        event for a mixed-case sibling redirects to the canonical
        ``.dropboxignore`` when one exists. Cache key is normalized to
        canonical lowercase so ``PosixPath`` equality (case-sensitive on
        POSIX) doesn't split entries between this method and ``match()``
        / ``load_root`` (item #92).
        """
        # DEBUG-level boundary log for backlog item #34 timing diagnostics.
        # Measures rule-cache reload + conflict-detector recompute under the
        # write lock. Lock contention against the watchdog thread's lock-free
        # `match()` reads can in principle delay this; the log makes that
        # observable. ``timed_debug`` no-ops when DEBUG isn't enabled.
        with timed_debug(logger, "reload_file path=%s", ignore_file), self._lock:
            ignore_file = _resolve_to_canonical_sibling(ignore_file)
            canonical = _canonical_cache_key(ignore_file)
            self._rules.pop(canonical, None)
            self._load_file(ignore_file, as_path=canonical)
            self._recompute_conflicts(log_warnings=log_warnings)

    def remove_file(self, ignore_file: Path, *, log_warnings: bool = True) -> None:
        """Drop all cached state for a .dropboxignore file (e.g. after deletion).

        Mirrors ``load_root``'s prefer-exact-match precedence: a deletion
        event for a mixed-case sibling is a no-op when the canonical
        ``.dropboxignore`` still exists, since the cache entry reflects
        the canonical file's still-valid rules. Cache key is normalized
        to canonical lowercase so the lookup hits on case-sensitive
        filesystems where ``PosixPath`` equality is case-sensitive
        (item #92).
        """
        with self._lock:
            if not is_ignore_filename(ignore_file.name):
                return
            if _resolve_to_canonical_sibling(ignore_file) is not ignore_file:
                # Canonical sibling still exists → its rules remain valid.
                return
            self._rules.pop(_canonical_cache_key(ignore_file), None)
            self._recompute_conflicts(log_warnings=log_warnings)

    def load_external(self, source: Path, mount_at: Path, *, log_warnings: bool = True) -> None:
        """Load ``source``'s lines as if it were a .dropboxignore at ``mount_at``.

        Used by ``dbxignore apply --from-gitignore``: rules in ``source`` are
        mounted at ``mount_at`` (which becomes a tracked root for this cache).
        The cache treats them indistinguishably from rules discovered at
        ``mount_at/.dropboxignore``.

        Errors during read or parse log a warning per ``_load_file``'s
        contract and do not raise; callers that need failure to surface as
        a CLI error must validate ``source`` themselves before calling.

        Callers should construct a fresh ``RuleCache`` and not subsequently
        call ``load_root`` on the same ``mount_at``: ``_load_if_changed``
        keys on stat values from ``source``, so a real ``.dropboxignore``
        at ``mount_at`` could be skipped if its mtime+size happen to match
        the synthesized entry's.
        """
        mount_at = mount_at.resolve()
        synthetic_path = mount_at / IGNORE_FILENAME
        with self._lock:
            if mount_at not in self._roots:
                self._roots.append(mount_at)
            self._load_file(source, as_path=synthetic_path)
            self._recompute_conflicts(log_warnings=log_warnings)

    def match(self, path: Path) -> bool:
        if not path.is_absolute():
            raise ValueError(f"match() requires an absolute path; got {path!r}")
        if is_ignore_filename(path.name):
            return False
        root = find_containing(path, self._roots)
        if root is None:
            return False

        # Walk root → path. For each ancestor .dropboxignore, iterate its
        # entries in source order; every matching pattern overwrites `matched`
        # with its include bit. Deeper ancestors come later, so their patterns
        # override shallower ones — gitignore's last-match-wins semantics.
        # Symlink-to-dir is treated as a LEAF here so a directory-only rule
        # (e.g. `build/`) does NOT match a symlink named `build`. Mirrors the
        # `format_literal_rule` invariant — round-trip via `ignore`/`unignore`
        # would otherwise break (ignore writes no-slash rules for symlinks).
        is_dir = _is_real_dir(path)
        matched = False
        for ancestor, loaded in self._applicable(root, path):
            rel_str = self._rel_path_str(ancestor, path, is_dir)
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
        if is_ignore_filename(path.name):
            return []
        root = find_containing(path, self._roots)
        if root is None:
            return []

        is_dir = _is_real_dir(path)
        results: list[Match] = []
        for ancestor, loaded in self._applicable(root, path):
            rel_str = self._rel_path_str(ancestor, path, is_dir)
            ignore_file = ancestor / IGNORE_FILENAME
            for line_idx, pattern in loaded.entries:
                if pattern.match_file(rel_str) is None:
                    continue
                raw_line = loaded.lines[line_idx] if line_idx < len(loaded.lines) else ""
                results.append(
                    Match(
                        ignore_file=ignore_file,
                        line=line_idx + 1,
                        pattern=raw_line,
                        negation=not bool(pattern.include),
                        is_dropped=(ignore_file, line_idx) in self._dropped,
                    )
                )
        return results

    # ---- internal helpers ------------------------------------------------

    def _load_file(
        self,
        ignore_file: Path,
        *,
        raw: bytes | None = None,
        content_hash: bytes | None = None,
        st: os.stat_result | None = None,
        as_path: Path | None = None,
    ) -> None:
        """Read and parse ``ignore_file`` into the cache.

        ``as_path`` overrides the cache key. When set, the parsed rules are
        stored as if they came from ``as_path`` rather than ``ignore_file``.
        Used by ``load_external`` to mount a non-``.dropboxignore`` source
        at an arbitrary directory; pass ``None`` for the discovery code path
        and the source location is the cache key.

        ``raw`` lets a caller (``_load_if_changed``) pass already-read bytes
        to avoid a second read. When ``None``, the bytes are read here.
        ``content_hash`` similarly lets the caller pass an already-computed
        blake2b-128 digest of those bytes; when ``None``, the hash is
        computed here. Both kwargs together avoid the double-read-and-hash
        on the miss path through ``_load_if_changed``.
        """
        # Resolve the cache key up front so failure arms can drop the
        # stale entry. Without that, an already-cached file that later
        # becomes unreadable or unparseable would keep its prior rules
        # active in `self._rules` — the daemon's reconcile would continue
        # marking paths the user already changed their mind about.
        #
        # Catch resolve failures (symlink loops raise `OSError(ELOOP)` on
        # POSIX and `RuntimeError` on Windows / older POSIX) — without
        # this, a `.dropboxignore` that later turns into a symlink loop
        # would crash the sweep before any of the read/parse error arms
        # could run.
        try:
            cache_key = _canonical_cache_key(as_path or ignore_file)
        except (OSError, RuntimeError) as exc:
            logger.warning("Could not resolve %s: %s", as_path or ignore_file, exc)
            return
        try:
            if raw is None:
                raw = ignore_file.read_bytes()
            if st is None:
                st = ignore_file.stat()
        except OSError as exc:
            # Read errors are usually transient: editor lock, antivirus scan,
            # backup process holding the file, brief EIO on a network drive.
            # Keep the prior cached entry — the next sweep retries and the
            # rules recover. Dropping on a transient error would clear the
            # cache, the next reconcile would treat previously-ignored paths
            # as un-rules-covered, and Dropbox would upload them to cloud
            # before the read recovered. A permanent read failure with the
            # file still on disk is unusual and the daemon's convergent
            # design tolerates it; a deleted file is handled by `load_root`'s
            # stale-purge instead.
            logger.warning("Could not read %s: %s", ignore_file, exc)
            return
        # `raw.decode("utf-8")` is strict — a non-UTF-8 file (the user saved
        # from an editor that defaulted to cp1252, say) is treated the same
        # way as a pathspec parse error below: the read succeeded but the
        # content is broken, so drop the cached entry and let the daemon
        # treat the file as empty until the next valid edit. Keeping stale
        # rules would let reconcile keep marking paths the user already
        # changed their mind about. `read_text("utf-8")` would have raised
        # uncaught and crashed the sweep — strictly worse than either arm.
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            logger.warning("Could not decode %s as utf-8: %s", ignore_file, exc)
            self._rules.pop(cache_key, None)
            return
        lines = text.splitlines()
        try:
            spec = _build_spec(lines)
        except (ValueError, TypeError, re.error) as exc:
            # Parse errors mean the read succeeded but the file's content is
            # genuinely broken — the user edited it into an invalid state.
            # Drop the cached entry so stale rules stop applying; the daemon
            # then treats the rule file as if it were empty until the next
            # valid edit. Without this, the daemon would keep applying the
            # last-known-good rules to a file the user already changed.
            logger.warning("Invalid .dropboxignore at %s: %s", ignore_file, exc)
            self._rules.pop(cache_key, None)
            return
        self._rules[cache_key] = _LoadedRules(
            lines=lines,
            entries=_build_entries(lines, spec),
            content_hash=content_hash
            if content_hash is not None
            else hashlib.blake2b(raw, digest_size=16).digest(),
            mtime_ns=st.st_mtime_ns,
            size=st.st_size,
        )

    def _load_if_changed(self, ignore_file: Path, *, as_path: Path | None = None) -> None:
        """Load ``ignore_file`` only if its bytes differ from the cached
        version (content hash mismatch). No-op if unchanged.

        Used by the sweep path (``load_root``) to avoid reparsing every
        .dropboxignore every hour. ``reload_file`` bypasses this check —
        a watchdog event is an explicit signal to reload regardless of
        content.

        Reads the file's bytes unconditionally and hashes them, then skips
        the (more expensive) pathspec compile only when the hash matches
        the cached digest. The prior shortcut compared
        ``(mtime_ns, size)`` from a stat call — cheaper, but missed
        same-size edits whose mtime was preserved by the editing tool
        (item #102). Read+hash on a small .dropboxignore is sub-millisecond;
        pathspec compile is the cost worth skipping.

        ``as_path`` overrides the cache-key derivation (mirrors
        ``_load_file``'s same-named kwarg). When ``as_path``'s basename
        differs from ``ignore_file``'s (the mixed-case fallback case in
        ``load_root``), the hash shortcut is skipped — the canonical-key
        entry may have been populated from a different source file, so its
        hash cannot be trusted to identify the current source. Name
        comparison rather than Path equality because ``WindowsPath``
        equality is case-insensitive on Windows and would falsely collapse
        the fallback case to a no-op.
        """
        try:
            raw = ignore_file.read_bytes()
        except OSError:
            # Can't read — let _load_file's arm surface the same error
            # (it will re-attempt the read and log on failure).
            self._load_file(ignore_file, as_path=as_path)
            return
        content_hash = hashlib.blake2b(raw, digest_size=16).digest()
        if as_path is None or as_path.name == ignore_file.name:
            cached = self._rules.get(_canonical_cache_key(as_path or ignore_file))
            if cached and cached.content_hash == content_hash:
                return
        self._load_file(ignore_file, raw=raw, content_hash=content_hash, as_path=as_path)

    def _applicable(self, root: Path, path: Path) -> list[tuple[Path, _LoadedRules]]:
        """Return (ancestor, loaded_rules) for each applicable .dropboxignore
        in shallow-to-deep order."""
        result: list[tuple[Path, _LoadedRules]] = []
        for ancestor in self._ancestors(root, path):
            loaded = self._rules.get(ancestor / IGNORE_FILENAME)
            if loaded is not None:
                result.append((ancestor, loaded))
        return result

    @staticmethod
    def _rel_path_str(ancestor: Path, path: Path, is_dir: bool) -> str:
        # Directory-only rules (e.g. `node_modules/`) only fire when the
        # tested path string ends in `/`. Callers compute is_dir once per
        # path so deep `.dropboxignore` chains don't repeat the syscall.
        rel_str = path.relative_to(ancestor).as_posix()
        if is_dir:
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
            # `_SequenceEntry` is structurally identical to the
            # `_SequenceEntryLike` Protocol that `_detect_conflicts` declares,
            # but mypy treats Protocols defined in another module as a
            # distinct nominal type for invariance purposes.
            for c in _detect_conflicts(sequence, root=root):  # type: ignore[arg-type]
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
                        c.dropped_pattern,
                        c.dropped_source,
                        c.dropped_line,
                        c.masking_pattern,
                        c.masking_source,
                        c.masking_line,
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
                raw = loaded.lines[line_idx] if line_idx < len(loaded.lines) else ""
                sequence.append(
                    _SequenceEntry(
                        source=ignore_file,
                        line=line_idx + 1,
                        raw=raw,
                        ancestor_dir=ancestor_dir,
                        pattern=pattern,
                    )
                )
        return sequence


def _build_entries(lines: list[str], spec: pathspec.PathSpec) -> list[tuple[int, pathspec.Pattern]]:
    """Pair each active source line with its compiled pattern.

    Fast path: filter ``spec.patterns`` to active entries (``include is not
    None``) and zip with source-line indices. A line is active iff it is
    non-blank after strip AND does not begin with ``#`` at column 0 — the
    gitignore-correct comment rule. Leading whitespace before ``#`` makes
    the line a literal pattern, not a comment (matching pathspec's parse).
    The two counts usually match.

    Fallback: defensive scaffolding for future pathspec-version drift. With
    the gitignore-correct filter above, fast-path counts match in practice;
    this fallback only fires if pathspec ever diverges from our filter
    (e.g. classifying some active line as a comment that we don't, or
    accepting a line as a pattern that our filter drops as blank).
    """
    active_line_indices = [
        i for i, raw in enumerate(lines) if raw.strip() and not raw.startswith("#")
    ]
    active_patterns = [p for p in spec.patterns if p.include is not None]
    if len(active_line_indices) == len(active_patterns):
        return list(zip(active_line_indices, active_patterns, strict=True))

    # Defensive: triggers only on pathspec-parse drift from our filter.
    entries: list[tuple[int, pathspec.Pattern]] = []
    for i in active_line_indices:
        for p in _build_spec([lines[i]]).patterns:
            if p.include is not None:
                entries.append((i, p))
                break
    return entries

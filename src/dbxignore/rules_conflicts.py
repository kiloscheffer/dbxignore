"""Static rule-conflict detection for `.dropboxignore` rule sequences.

Extracts the detection layer from ``rules.py`` per followup item 6. The
functions here are pure — they take an in-memory rule sequence and a
root path, and return a list of ``Conflict`` records. They have no
coupling to ``RuleCache`` internals beyond the duck-typed shape of the
sequence entries (each entry must expose ``source``, ``line``, ``raw``,
``ancestor_dir``, and ``pattern`` — see ``_detect_conflicts``'s
docstring for details).

``RuleCache._recompute_conflicts`` is the sole production caller;
``rules.py`` re-imports the public symbols (``Conflict``,
``_detect_conflicts``) so existing call sites are unaffected.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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


def _ancestors_of(
    prefix: str, ancestor_dir: Path, root: Path, *, strict: bool = False
) -> list[Path]:
    """Yield absolute ancestor directory paths for a negation's literal prefix.

    The negation's literal prefix is relative to its own ``.dropboxignore``
    file's directory (``ancestor_dir``). We produce absolute directory paths
    starting from the prefix itself (if it's a directory shape) and walking
    up to ``root``, inclusive.

    Example: prefix=``build/keep/``, ancestor_dir=``/root``, root=``/root``
    yields ``[/root/build/keep, /root/build, /root]``.

    With ``strict=True``, the target itself is omitted — only directories
    strictly above the prefix are returned (``[/root/build, /root]`` for the
    same example). Used for directory negations where the negation's own
    rule overrides any earlier include's effect on the target via pathspec
    last-match-wins; the conflict only exists if a *strict* ancestor is
    marked, since Dropbox's directory inheritance is the inescapable case.
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
    if strict:
        if target == root or not target.is_relative_to(root):
            return []
        current = target.parent
    else:
        current = target
    results: list[Path] = []
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
    """Return an earlier include that effectively marks any ancestor.

    For each ancestor, find the *last* earlier rule (include or negation)
    that matches it; if that last match is an include, the ancestor is
    marked at this point in the sequence and we report it as the masking
    rule. If a negation later in the earlier-sequence overrides the
    include for that specific ancestor, no conflict is reported from that
    ancestor — pathspec's last-match-wins semantics is what matters.

    Earlier behavior considered only includes; that produced false
    positives like `build/*` + `!build/keep/` + `!build/keep/**`, where
    the second rule keeps build/keep unmarked and the third rule's
    descendants are reachable.
    """
    for anc in ancestors:
        last_match = None
        for earlier in earlier_entries:
            try:
                rel = anc.relative_to(earlier.ancestor_dir).as_posix() + "/"
            except ValueError:
                # This ancestor isn't under the earlier rule's scope.
                continue
            if earlier.pattern.match_file(rel) is not None:
                last_match = earlier
        if last_match is not None and last_match.pattern.include:
            return last_match
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
        # Directory negation (`!build/keep/`): raw equals the literal prefix,
        # so the negation's own rule overrides earlier includes for the
        # target itself via pathspec last-match-wins. Only strict ancestors
        # (parents of the target) can mask. For broader negations like
        # `!build/keep/**` or `!build/keep/foo.txt`, raw extends past the
        # literal prefix; the prefix directory is itself a strict ancestor
        # of the negation's actual targets, so include it in the walk.
        is_directory_negation = raw.rstrip() == prefix
        ancestors = _ancestors_of(
            prefix, entry.ancestor_dir, root, strict=is_directory_negation
        )

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

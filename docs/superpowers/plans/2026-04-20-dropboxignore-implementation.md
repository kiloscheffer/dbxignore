# dropboxignore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Windows-only Python CLI and daemon that keeps the `com.dropbox.ignored` NTFS alternate data stream in sync with hierarchical `.dropboxignore` files under every configured Dropbox root.

**Architecture:** Single Python process exposing a `click`-based CLI (`dropboxignore`) and a daemon entry point (`dropboxignored`). The daemon combines a `watchdog` observer with an hourly safety-net sweep; both paths funnel into one `reconcile_subtree(root, subdir, cache)` function so the manual `apply` command and the daemon can never disagree. Rule discovery is hierarchical (`pathspec.GitIgnoreSpec` per `.dropboxignore` file, accumulated with gitignore negation semantics). ADS writes go through `open(r"\\?\path:com.dropbox.ignored")` directly — no subprocess.

**Tech Stack:** Python ≥ 3.11, `watchdog`, `pathspec`, `click`, `psutil`, `hatchling` + `hatch-vcs`, `pytest`, `ruff`. CI on GitHub Actions (Ubuntu + Windows). PyInstaller for release binaries.

**Design doc:** [`docs/superpowers/specs/2026-04-20-dropboxignore-design.md`](../specs/2026-04-20-dropboxignore-design.md)

**Conventions for this plan:**
- Working directory for every command is the repo root (`C:\Dropbox\git\dropboxignore`). `uv run` is assumed to pick up the project venv.
- Commits use Conventional Commit prefixes (`feat`, `test`, `chore`, `ci`, `docs`, `refactor`). Each task produces exactly one commit unless noted.
- Every code block is complete — paste-ready. No "…" elisions for already-written code unless a specific line range is called out.

---

## Task 1: Project scaffold

**Goal:** Establish the repo skeleton so `uv sync` and `uv run pytest` work against an empty package.

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/dropboxignore/__init__.py`
- Create: `src/dropboxignore/__main__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_smoke.py`

**Steps:**

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[project]
name = "dropboxignore"
description = "Hierarchical .dropboxignore for Dropbox on Windows via NTFS alternate data streams"
readme = "README.md"
requires-python = ">=3.11"
license = "MIT"
authors = [{ name = "Kilo Scheffer" }]
dynamic = ["version"]
dependencies = [
    "watchdog>=4.0",
    "pathspec>=0.12",
    "click>=8.1",
    "psutil>=5.9",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-timeout>=2.3",
    "ruff>=0.6",
]

[project.scripts]
dropboxignore = "dropboxignore.cli:main"
dropboxignored = "dropboxignore.cli:daemon_main"

[tool.hatch.version]
source = "vcs"

[tool.hatch.build.hooks.vcs]
version-file = "src/dropboxignore/_version.py"

[tool.hatch.build.targets.wheel]
packages = ["src/dropboxignore"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "windows_only: test requires NTFS alternate data streams",
]
timeout = 10

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]
```

- [ ] **Step 2: Write `.gitignore`**

```
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.venv/
venv/
build/
dist/
*.egg-info/
src/dropboxignore/_version.py
.uv/
```

- [ ] **Step 3: Write `src/dropboxignore/__init__.py`**

```python
try:
    from ._version import __version__
except ImportError:
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
```

- [ ] **Step 4: Write `src/dropboxignore/__main__.py`**

```python
from dropboxignore.cli import main

if __name__ == "__main__":
    main()
```

Note: `cli.py` doesn't exist yet — that's fine. This module is only imported when someone runs `python -m dropboxignore`, which won't happen until Task 11.

- [ ] **Step 5: Write `tests/__init__.py` (empty file)**

- [ ] **Step 6: Write `tests/test_smoke.py`**

```python
def test_package_importable():
    import dropboxignore

    assert dropboxignore.__version__
```

- [ ] **Step 7: Sync dependencies**

Run: `uv sync --all-extras`
Expected: a `.venv` is created, `uv.lock` appears, all dev deps install without error. If `hatch-vcs` warns about not finding a tag, that's expected at this point — `__version__` will resolve to a dev string.

- [ ] **Step 8: Run the smoke test**

Run: `uv run pytest tests/test_smoke.py -v`
Expected: `test_package_importable PASSED`.

- [ ] **Step 9: Run ruff**

Run: `uv run ruff check`
Expected: `All checks passed!` (with empty source tree + one import-style test, ruff should stay quiet).

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml .gitignore src tests
git commit -m "chore: scaffold Python package with hatchling + hatch-vcs"
```

---

## Task 2: `roots.discover()` — parse Dropbox info.json

**Goal:** Return a list of `Path`s to every configured Dropbox root by reading `%APPDATA%\Dropbox\info.json`. Handle personal-only, personal+business, missing file, and malformed JSON.

**Files:**
- Create: `src/dropboxignore/roots.py`
- Create: `tests/test_roots.py`
- Create: `tests/fixtures/info_personal.json`
- Create: `tests/fixtures/info_personal_business.json`
- Create: `tests/fixtures/info_malformed.json`

**Steps:**

- [ ] **Step 1: Write fixtures**

`tests/fixtures/info_personal.json`:
```json
{
  "personal": {
    "path": "C:\\Dropbox",
    "host": 1234567890,
    "is_team": false,
    "subscription_type": "Free"
  }
}
```

`tests/fixtures/info_personal_business.json`:
```json
{
  "personal": {
    "path": "C:\\Dropbox",
    "host": 1234567890,
    "is_team": false
  },
  "business": {
    "path": "C:\\Dropbox (Work)",
    "host": 9876543210,
    "is_team": true
  }
}
```

`tests/fixtures/info_malformed.json`:
```
{ this is not valid json
```

- [ ] **Step 2: Write failing tests**

`tests/test_roots.py`:
```python
from pathlib import Path

from dropboxignore import roots

FIXTURES = Path(__file__).parent / "fixtures"


def _monkeypatch_info(monkeypatch, tmp_path, fixture_name: str | None):
    """Stage a fake %APPDATA%\\Dropbox\\info.json and point APPDATA at it."""
    appdata = tmp_path / "AppData"
    dropbox_dir = appdata / "Dropbox"
    dropbox_dir.mkdir(parents=True)
    if fixture_name is not None:
        content = (FIXTURES / fixture_name).read_text(encoding="utf-8")
        (dropbox_dir / "info.json").write_text(content, encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))


def test_discover_personal_only(monkeypatch, tmp_path):
    _monkeypatch_info(monkeypatch, tmp_path, "info_personal.json")
    result = roots.discover()
    assert result == [Path(r"C:\Dropbox")]


def test_discover_personal_and_business(monkeypatch, tmp_path):
    _monkeypatch_info(monkeypatch, tmp_path, "info_personal_business.json")
    result = roots.discover()
    assert result == [Path(r"C:\Dropbox"), Path(r"C:\Dropbox (Work)")]


def test_discover_missing_info_file(monkeypatch, tmp_path):
    _monkeypatch_info(monkeypatch, tmp_path, fixture_name=None)
    assert roots.discover() == []


def test_discover_malformed_json(monkeypatch, tmp_path):
    _monkeypatch_info(monkeypatch, tmp_path, "info_malformed.json")
    assert roots.discover() == []


def test_discover_no_appdata_env(monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    assert roots.discover() == []
```

- [ ] **Step 3: Run tests, confirm they fail**

Run: `uv run pytest tests/test_roots.py -v`
Expected: All 5 fail with `ModuleNotFoundError: No module named 'dropboxignore.roots'`.

- [ ] **Step 4: Implement `roots.py`**

`src/dropboxignore/roots.py`:
```python
"""Discover configured Dropbox root paths from Dropbox's own info.json."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_ACCOUNT_TYPES = ("personal", "business")


def discover() -> list[Path]:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        logger.warning("APPDATA environment variable not set; cannot locate Dropbox info.json")
        return []

    info_path = Path(appdata) / "Dropbox" / "info.json"
    if not info_path.exists():
        logger.warning("Dropbox info.json not found at %s", info_path)
        return []

    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Malformed Dropbox info.json at %s: %s", info_path, exc)
        return []

    roots: list[Path] = []
    for account_type in _ACCOUNT_TYPES:
        account = data.get(account_type)
        if isinstance(account, dict) and isinstance(account.get("path"), str):
            roots.append(Path(account["path"]))
    return roots
```

- [ ] **Step 5: Run tests, confirm they pass**

Run: `uv run pytest tests/test_roots.py -v`
Expected: All 5 pass.

- [ ] **Step 6: Commit**

```bash
git add src/dropboxignore/roots.py tests/test_roots.py tests/fixtures
git commit -m "feat(roots): discover Dropbox roots from info.json"
```

---

## Task 3: `ads` module — read/write/clear `com.dropbox.ignored`

**Goal:** Provide three thin functions (`is_ignored`, `set_ignored`, `clear_ignored`) that read, write, and delete the `com.dropbox.ignored` NTFS alternate data stream on a path. Use the `\\?\` long-path prefix. Keep the module small enough that real behavior is exercised by the Windows integration tests in Task 18 — unit-test only what's portable (path construction).

**Files:**
- Create: `src/dropboxignore/ads.py`
- Create: `tests/test_ads_unit.py`

**Steps:**

- [ ] **Step 1: Write failing test**

`tests/test_ads_unit.py`:
```python
from pathlib import Path

from dropboxignore import ads


def test_stream_path_uses_long_path_prefix_and_stream_name():
    p = Path(r"C:\Dropbox\some\dir")
    result = ads._stream_path(p)
    assert result == r"\\?\C:\Dropbox\some\dir:com.dropbox.ignored"


def test_stream_path_resolves_relative_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = ads._stream_path(Path("foo"))
    expected = rf"\\?\{tmp_path / 'foo'}:com.dropbox.ignored"
    assert result == expected
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `uv run pytest tests/test_ads_unit.py -v`
Expected: `ModuleNotFoundError: No module named 'dropboxignore.ads'`.

- [ ] **Step 3: Implement `ads.py`**

`src/dropboxignore/ads.py`:
```python
"""Read/write the Dropbox 'ignore' NTFS alternate data stream.

Dropbox treats a file or directory as ignored if it has an NTFS alternate
data stream named ``com.dropbox.ignored`` containing any non-empty value.
This module exposes three operations via Python's built-in ``open()``,
which on Windows passes the ``path:streamname`` syntax through to
``CreateFileW`` at the kernel level — so no subprocess or pywin32 needed.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

STREAM_NAME = "com.dropbox.ignored"
_MARKER_VALUE = "1"
_LONG_PATH_PREFIX = "\\\\?\\"


def _stream_path(path: Path) -> str:
    """Return the absolute ``\\\\?\\…:streamname`` path for ``path``."""
    absolute = path.resolve()
    return f"{_LONG_PATH_PREFIX}{absolute}:{STREAM_NAME}"


def is_ignored(path: Path) -> bool:
    """Return True if ``path`` currently bears the ignore marker."""
    try:
        with open(_stream_path(path), "r", encoding="ascii") as f:
            return f.read(1) == _MARKER_VALUE
    except FileNotFoundError:
        return False


def set_ignored(path: Path) -> None:
    """Mark ``path`` as ignored by Dropbox."""
    with open(_stream_path(path), "w", encoding="ascii") as f:
        f.write(_MARKER_VALUE)


def clear_ignored(path: Path) -> None:
    """Remove the Dropbox ignore marker from ``path`` (no-op if absent)."""
    try:
        os.remove(_stream_path(path))
    except FileNotFoundError:
        pass
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `uv run pytest tests/test_ads_unit.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/dropboxignore/ads.py tests/test_ads_unit.py
git commit -m "feat(ads): add com.dropbox.ignored stream read/write/clear wrappers"
```

---

## Task 4: `rules.RuleCache` — basic load and match

**Goal:** Minimal `RuleCache` that finds every `.dropboxignore` under a root, parses each with `pathspec.GitIgnoreSpec`, and answers `match(path) -> bool` correctly for the single-file (flat) case. Hierarchy, negation, case-insensitivity, and `.dropboxignore` protection come in later tasks.

**Files:**
- Create: `src/dropboxignore/rules.py`
- Create: `tests/test_rules_basic.py`

**Steps:**

- [ ] **Step 1: Write failing test**

`tests/test_rules_basic.py`:
```python
from pathlib import Path

from dropboxignore.rules import RuleCache


def _write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_flat_match_sets_true_for_matching_directory(tmp_path):
    _write(tmp_path / ".dropboxignore", "node_modules/\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "src").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "node_modules") is True
    assert cache.match(tmp_path / "src") is False


def test_empty_dropboxignore_matches_nothing(tmp_path):
    _write(tmp_path / ".dropboxignore", "")
    (tmp_path / "foo").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "foo") is False


def test_comment_and_blank_lines_ignored(tmp_path):
    _write(tmp_path / ".dropboxignore", "# comment\n\nbuild/\n")
    (tmp_path / "build").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "build") is True


def test_no_dropboxignore_files_matches_nothing(tmp_path):
    (tmp_path / "anything").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "anything") is False
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `uv run pytest tests/test_rules_basic.py -v`
Expected: `ModuleNotFoundError: No module named 'dropboxignore.rules'`.

- [ ] **Step 3: Implement `rules.py`**

`src/dropboxignore/rules.py`:
```python
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
            if spec.match_file(str(rel).replace("\\", "/")):
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
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `uv run pytest tests/test_rules_basic.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/dropboxignore/rules.py tests/test_rules_basic.py
git commit -m "feat(rules): RuleCache with flat .dropboxignore matching"
```

---

## Task 5: `rules` — hierarchical matching with negation

**Goal:** Nested `.dropboxignore` files at different depths both apply; a deeper `!pattern` can negate an ancestor's match. This exercises the ancestor-walk and ordering logic already in `match()`.

**Files:**
- Create: `tests/test_rules_hierarchical.py`
- Modify: `src/dropboxignore/rules.py` (only if tests reveal a bug)

**Steps:**

- [ ] **Step 1: Write failing tests**

`tests/test_rules_hierarchical.py`:
```python
from pathlib import Path

from dropboxignore.rules import RuleCache


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_nested_dropboxignore_adds_rules(tmp_path):
    # Root-level ignores nothing; nested ignores 'build/'.
    _write(tmp_path / ".dropboxignore", "")
    (tmp_path / "proj").mkdir()
    _write(tmp_path / "proj" / ".dropboxignore", "build/\n")
    (tmp_path / "proj" / "build").mkdir()
    (tmp_path / "proj" / "src").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "proj" / "build") is True
    assert cache.match(tmp_path / "proj" / "src") is False


def test_child_can_negate_ancestor_match(tmp_path):
    _write(tmp_path / ".dropboxignore", "*.log\n")
    (tmp_path / "proj").mkdir()
    _write(tmp_path / "proj" / ".dropboxignore", "!important.log\n")
    (tmp_path / "proj" / "a.log").touch()
    (tmp_path / "proj" / "important.log").touch()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "proj" / "a.log") is True
    assert cache.match(tmp_path / "proj" / "important.log") is False


def test_ancestor_rule_applies_to_deep_descendant(tmp_path):
    _write(tmp_path / ".dropboxignore", "**/node_modules/\n")
    (tmp_path / "a" / "b" / "c" / "node_modules").mkdir(parents=True)

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "a" / "b" / "c" / "node_modules") is True
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_rules_hierarchical.py -v`
Expected: 3 passed. If a test fails, fix `rules.py`'s `_ancestors` / `match` logic. The current implementation should already handle these cases — the tests exist to lock the behavior in.

- [ ] **Step 3: Commit**

```bash
git add tests/test_rules_hierarchical.py
git commit -m "test(rules): cover hierarchical matching and child-level negation"
```

---

## Task 6: `rules` — case-insensitive matching & `.dropboxignore` protection

**Goal:** On Windows-style case-insensitive filesystems, `node_modules` in a rule should match a directory literally named `Node_Modules`. A file named `.dropboxignore` must never be reported as matching — the file has to sync between machines.

**Files:**
- Modify: `src/dropboxignore/rules.py`
- Create: `tests/test_rules_case_and_protection.py`

**Steps:**

- [ ] **Step 1: Write failing tests**

`tests/test_rules_case_and_protection.py`:
```python
from pathlib import Path

from dropboxignore.rules import RuleCache


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_case_insensitive_match(tmp_path):
    _write(tmp_path / ".dropboxignore", "node_modules/\n")
    (tmp_path / "Node_Modules").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / "Node_Modules") is True


def test_dropboxignore_file_itself_never_matches(tmp_path):
    # A greedy rule at root that would otherwise sweep up the .dropboxignore file.
    _write(tmp_path / ".dropboxignore", "*\n")
    (tmp_path / "proj").mkdir()
    _write(tmp_path / "proj" / ".dropboxignore", "build/\n")

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.match(tmp_path / ".dropboxignore") is False
    assert cache.match(tmp_path / "proj" / ".dropboxignore") is False
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `uv run pytest tests/test_rules_case_and_protection.py -v`
Expected: both fail (case-sensitive pathspec doesn't match `Node_Modules`; `*` sweeps up the .dropboxignore file).

- [ ] **Step 3: Modify `rules.py`**

In `rules.py`, replace the `_load_file` method and add an early-exit at the top of `match`. Full updated `rules.py`:

```python
"""Hierarchical .dropboxignore rule cache."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pathspec
from pathspec.patterns.gitwildmatch import GitWildMatchPattern

logger = logging.getLogger(__name__)

IGNORE_FILENAME = ".dropboxignore"


@dataclass(frozen=True)
class Match:
    ignore_file: Path
    line: int
    pattern: str
    negation: bool


class _CaseInsensitiveGitWildMatchPattern(GitWildMatchPattern):
    """GitWildMatch pattern with case-insensitive regex compilation."""

    @classmethod
    def pattern_to_regex(cls, pattern: str) -> tuple[str | None, bool | None]:
        regex, include = super().pattern_to_regex(pattern)
        if regex is not None:
            regex = f"(?i){regex}"
        return regex, include


def _build_spec(lines: list[str]) -> pathspec.PathSpec:
    return pathspec.PathSpec.from_lines(_CaseInsensitiveGitWildMatchPattern, lines)


class RuleCache:
    def __init__(self) -> None:
        self._specs: dict[Path, pathspec.PathSpec] = {}
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

        matched = False
        for ancestor in self._ancestors(root, path):
            ignore_file = ancestor / IGNORE_FILENAME
            spec = self._specs.get(ignore_file)
            if spec is None:
                continue
            rel = path.relative_to(ancestor)
            if spec.match_file(str(rel).replace("\\", "/")):
                matched = True
            # pathspec's negation is handled internally by match_file
            # against the accumulated ruleset of a single file. For negation
            # across files, we re-evaluate per ignore_file and let deeper
            # files overwrite the `matched` state below.
            # NOTE: for cross-file negation precedence, we rely on deeper
            # files being processed last in _ancestors ordering.
        return matched

    def _load_file(self, ignore_file: Path) -> None:
        try:
            lines = ignore_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("Could not read %s: %s", ignore_file, exc)
            return
        try:
            spec = _build_spec(lines)
        except Exception as exc:
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
        rel = path.relative_to(root)
        result = [root]
        current = root
        for part in rel.parts[:-1]:
            current = current / part
            result.append(current)
        return result
```

The key changes: (1) switch from `GitIgnoreSpec` to a `PathSpec` built from a case-insensitive pattern subclass, (2) add `if path.name == IGNORE_FILENAME: return False` at the top of `match`.

**Caveat for Task 5 tests:** cross-file negation (deeper file negating an ancestor's match) needs re-examination. Step 4 re-runs all rule tests together.

- [ ] **Step 4: Run all rule tests**

Run: `uv run pytest tests/test_rules_basic.py tests/test_rules_hierarchical.py tests/test_rules_case_and_protection.py -v`
Expected: all pass. If `test_child_can_negate_ancestor_match` now fails, the fix is to update `match()` to reset `matched` when a deeper `.dropboxignore` has an `!important.log` negation pattern that matches. Easiest path: modify `match()` to process each ancestor spec and apply pathspec's own positive/negative semantics to an accumulating `matched` flag:

```python
def match(self, path: Path) -> bool:
    path = path.resolve()
    if path.name == IGNORE_FILENAME:
        return False
    root = self._root_of(path)
    if root is None:
        return False

    matched = False
    for ancestor in self._ancestors(root, path):
        ignore_file = ancestor / IGNORE_FILENAME
        spec = self._specs.get(ignore_file)
        if spec is None:
            continue
        rel_str = str(path.relative_to(ancestor)).replace("\\", "/")
        # Walk patterns in order so later negations override earlier matches.
        for pattern in spec.patterns:
            if pattern.regex is None:
                continue
            if pattern.regex.match(rel_str):
                matched = bool(pattern.include)
    return matched
```

- [ ] **Step 5: Commit**

```bash
git add src/dropboxignore/rules.py tests/test_rules_case_and_protection.py
git commit -m "feat(rules): case-insensitive matching and never-match .dropboxignore itself"
```

---

## Task 7: `rules` — `reload_file`, `remove_file`, and `explain`

**Goal:** Support incremental updates (reload / remove a single `.dropboxignore`) and a diagnostic `explain(path)` that returns the list of matching `(ignore_file, line, pattern)` tuples for `dropboxignore explain`.

**Files:**
- Modify: `src/dropboxignore/rules.py`
- Create: `tests/test_rules_reload_explain.py`

**Steps:**

- [ ] **Step 1: Write failing tests**

`tests/test_rules_reload_explain.py`:
```python
from pathlib import Path

from dropboxignore.rules import RuleCache


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_reload_file_picks_up_new_pattern(tmp_path):
    _write(tmp_path / ".dropboxignore", "")
    (tmp_path / "build").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)
    assert cache.match(tmp_path / "build") is False

    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    cache.reload_file(tmp_path / ".dropboxignore")

    assert cache.match(tmp_path / "build") is True


def test_remove_file_drops_its_rules(tmp_path):
    _write(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)
    assert cache.match(tmp_path / "build") is True

    cache.remove_file(tmp_path / ".dropboxignore")
    assert cache.match(tmp_path / "build") is False


def test_explain_returns_matching_rule(tmp_path):
    _write(tmp_path / ".dropboxignore", "# header\nbuild/\n*.log\n")
    (tmp_path / "build").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    matches = cache.explain(tmp_path / "build")
    assert len(matches) == 1
    assert matches[0].ignore_file == (tmp_path / ".dropboxignore").resolve()
    assert matches[0].pattern == "build/"
    assert matches[0].line == 2
    assert matches[0].negation is False


def test_explain_empty_for_non_matching_path(tmp_path):
    _write(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "src").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    assert cache.explain(tmp_path / "src") == []
```

- [ ] **Step 2: Run tests, confirm failures**

Run: `uv run pytest tests/test_rules_reload_explain.py -v`
Expected: 4 failures (methods don't exist yet).

- [ ] **Step 3: Add methods to `rules.py`**

Add inside the `RuleCache` class (keep existing methods; also store raw lines for `explain`):

```python
    def __init__(self) -> None:
        self._specs: dict[Path, pathspec.PathSpec] = {}
        self._lines: dict[Path, list[str]] = {}  # for explain()
        self._roots: list[Path] = []

    def reload_file(self, ignore_file: Path) -> None:
        ignore_file = ignore_file.resolve()
        self._specs.pop(ignore_file, None)
        self._lines.pop(ignore_file, None)
        if ignore_file.exists():
            self._load_file(ignore_file)

    def remove_file(self, ignore_file: Path) -> None:
        ignore_file = ignore_file.resolve()
        self._specs.pop(ignore_file, None)
        self._lines.pop(ignore_file, None)

    def explain(self, path: Path) -> list[Match]:
        path = path.resolve()
        if path.name == IGNORE_FILENAME:
            return []
        root = self._root_of(path)
        if root is None:
            return []

        matches: list[Match] = []
        for ancestor in self._ancestors(root, path):
            ignore_file = ancestor / IGNORE_FILENAME
            spec = self._specs.get(ignore_file)
            lines = self._lines.get(ignore_file, [])
            if spec is None:
                continue
            rel_str = str(path.relative_to(ancestor)).replace("\\", "/")
            for idx, pattern in enumerate(spec.patterns):
                if pattern.regex is None:
                    continue
                if pattern.regex.match(rel_str):
                    raw_line_idx = _pattern_line_index(lines, idx)
                    raw = lines[raw_line_idx] if raw_line_idx is not None else ""
                    matches.append(
                        Match(
                            ignore_file=ignore_file,
                            line=(raw_line_idx + 1) if raw_line_idx is not None else 0,
                            pattern=raw,
                            negation=not bool(pattern.include),
                        )
                    )
        return matches
```

Also update `_load_file` to populate `self._lines`:

```python
    def _load_file(self, ignore_file: Path) -> None:
        try:
            lines = ignore_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("Could not read %s: %s", ignore_file, exc)
            return
        try:
            spec = _build_spec(lines)
        except Exception as exc:
            logger.warning("Invalid .dropboxignore at %s: %s", ignore_file, exc)
            return
        resolved = ignore_file.resolve()
        self._specs[resolved] = spec
        self._lines[resolved] = lines
```

And add this helper at module level (above the class):

```python
def _pattern_line_index(lines: list[str], pattern_index: int) -> int | None:
    """Map a pathspec pattern index back to a source line index."""
    count = -1
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        count += 1
        if count == pattern_index:
            return i
    return None
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_rules_reload_explain.py -v`
Expected: 4 passed. Run the full rule test suite to confirm no regression:
`uv run pytest tests -k rules -v` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/dropboxignore/rules.py tests/test_rules_reload_explain.py
git commit -m "feat(rules): add reload_file, remove_file, explain"
```

---

## Task 8: `reconcile.reconcile_subtree` — basic set/clear

**Goal:** A single function that walks a subtree and drives `ads.set_ignored` / `ads.clear_ignored` to match the `RuleCache`'s current verdict for each directory. Returns a `Report` with counts and errors. This task covers the happy path; the short-circuit optimization and error partitioning land in Task 9.

**Files:**
- Create: `src/dropboxignore/reconcile.py`
- Create: `tests/test_reconcile_basic.py`

**Steps:**

- [ ] **Step 1: Write failing tests**

`tests/test_reconcile_basic.py`:
```python
from pathlib import Path

import pytest

from dropboxignore import reconcile
from dropboxignore.rules import RuleCache


class FakeADS:
    """In-memory stand-in for the ads module."""

    def __init__(self) -> None:
        self._ignored: set[Path] = set()
        self.set_calls: list[Path] = []
        self.clear_calls: list[Path] = []

    def is_ignored(self, path: Path) -> bool:
        return path.resolve() in self._ignored

    def set_ignored(self, path: Path) -> None:
        p = path.resolve()
        self._ignored.add(p)
        self.set_calls.append(p)

    def clear_ignored(self, path: Path) -> None:
        p = path.resolve()
        self._ignored.discard(p)
        self.clear_calls.append(p)


@pytest.fixture
def fake_ads(monkeypatch):
    fake = FakeADS()
    monkeypatch.setattr(reconcile, "ads", fake)
    return fake


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_sets_ads_on_matching_directory(tmp_path, fake_ads):
    _write(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "src").mkdir()

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert (tmp_path / "build").resolve() in fake_ads._ignored
    assert (tmp_path / "src").resolve() not in fake_ads._ignored
    assert report.marked == 1
    assert report.cleared == 0
    assert report.errors == []


def test_clears_ads_when_no_longer_matching(tmp_path, fake_ads):
    (tmp_path / "build").mkdir()
    fake_ads.set_ignored(tmp_path / "build")  # pre-existing marker
    _write(tmp_path / ".dropboxignore", "")  # no rules

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert (tmp_path / "build").resolve() not in fake_ads._ignored
    assert report.cleared == 1


def test_no_ops_when_state_already_correct(tmp_path, fake_ads):
    _write(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    fake_ads.set_ignored(tmp_path / "build")

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    # No extra set or clear calls beyond the pre-seed.
    assert fake_ads.set_calls == [(tmp_path / "build").resolve()]
    assert fake_ads.clear_calls == []
    assert report.marked == 0
    assert report.cleared == 0


def test_matches_files_not_just_directories(tmp_path, fake_ads):
    _write(tmp_path / ".dropboxignore", "*.log\n")
    (tmp_path / "a.log").touch()
    (tmp_path / "b.txt").touch()

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert (tmp_path / "a.log").resolve() in fake_ads._ignored
    assert (tmp_path / "b.txt").resolve() not in fake_ads._ignored
    assert report.marked == 1
```

- [ ] **Step 2: Run tests, confirm failures**

Run: `uv run pytest tests/test_reconcile_basic.py -v`
Expected: `ModuleNotFoundError: No module named 'dropboxignore.reconcile'`.

- [ ] **Step 3: Implement `reconcile.py`**

`src/dropboxignore/reconcile.py`:
```python
"""Reconcile the filesystem's ADS markers with the current rule set."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from dropboxignore import ads
from dropboxignore.rules import RuleCache

logger = logging.getLogger(__name__)


@dataclass
class Report:
    marked: int = 0
    cleared: int = 0
    errors: list[tuple[Path, str]] = field(default_factory=list)
    duration_s: float = 0.0


def reconcile_subtree(root: Path, subdir: Path, cache: RuleCache) -> Report:
    start = time.perf_counter()
    report = Report()

    root = root.resolve()
    subdir = subdir.resolve()

    _reconcile_path(subdir, cache, report)
    for current, dirnames, filenames in os.walk(subdir, followlinks=False):
        current_path = Path(current)
        for name in filenames + dirnames:
            _reconcile_path(current_path / name, cache, report)

    report.duration_s = time.perf_counter() - start
    return report


def _reconcile_path(path: Path, cache: RuleCache, report: Report) -> None:
    should_ignore = cache.match(path)
    currently_ignored = ads.is_ignored(path)
    if should_ignore and not currently_ignored:
        ads.set_ignored(path)
        report.marked += 1
    elif currently_ignored and not should_ignore:
        ads.clear_ignored(path)
        report.cleared += 1
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_reconcile_basic.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/dropboxignore/reconcile.py tests/test_reconcile_basic.py
git commit -m "feat(reconcile): basic subtree reconcile with set/clear and Report"
```

---

## Task 9: `reconcile` — ancestor short-circuit & error handling

**Goal:** Don't descend into directories already marked ignored (Dropbox isn't syncing their contents either). Catch `FileNotFoundError` (path vanished) and `PermissionError` (file in use) per path, log at WARNING, record in `report.errors`, continue.

**Files:**
- Modify: `src/dropboxignore/reconcile.py`
- Create: `tests/test_reconcile_edges.py`

**Steps:**

- [ ] **Step 1: Write failing tests**

`tests/test_reconcile_edges.py`:
```python
from pathlib import Path

import pytest

from dropboxignore import reconcile
from dropboxignore.rules import RuleCache
from tests.test_reconcile_basic import FakeADS  # reuse fake


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def fake_ads(monkeypatch):
    fake = FakeADS()
    monkeypatch.setattr(reconcile, "ads", fake)
    return fake


def test_skips_descendants_of_already_ignored_directory(tmp_path, fake_ads):
    _write(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "deep").mkdir()
    (tmp_path / "build" / "a.o").touch()
    fake_ads.set_ignored(tmp_path / "build")  # pre-ignored

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    # 'deep' and 'a.o' must not be touched (we skipped into build/).
    assert (tmp_path / "build" / "deep").resolve() not in fake_ads._ignored
    # Report counts no new marks/clears — build/ was already correct.
    assert report.marked == 0
    assert report.cleared == 0


def test_permission_error_is_logged_and_counted_not_raised(tmp_path, monkeypatch, caplog):
    _write(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "other").mkdir()

    class FailingADS:
        def __init__(self):
            self._ignored = set()
        def is_ignored(self, path): return False
        def set_ignored(self, path):
            if path.name == "build":
                raise PermissionError("locked")
            self._ignored.add(path.resolve())
        def clear_ignored(self, path): self._ignored.discard(path.resolve())

    failing = FailingADS()
    monkeypatch.setattr(reconcile, "ads", failing)

    cache = RuleCache()
    cache.load_root(tmp_path)

    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)

    assert len(report.errors) == 1
    err_path, err_msg = report.errors[0]
    assert err_path.name == "build"
    assert "locked" in err_msg


def test_file_not_found_during_walk_is_silently_skipped(tmp_path, monkeypatch):
    _write(tmp_path / ".dropboxignore", "build/\n")
    (tmp_path / "build").mkdir()

    class DisappearingADS:
        def is_ignored(self, path):
            raise FileNotFoundError("gone")
        def set_ignored(self, path): pass
        def clear_ignored(self, path): pass

    monkeypatch.setattr(reconcile, "ads", DisappearingADS())

    cache = RuleCache()
    cache.load_root(tmp_path)

    # Must not raise.
    report = reconcile.reconcile_subtree(tmp_path, tmp_path, cache)
    # FileNotFoundError is expected traffic, not an error.
    assert report.errors == []
```

- [ ] **Step 2: Run tests, confirm failures**

Run: `uv run pytest tests/test_reconcile_edges.py -v`
Expected: 3 fails — current code walks into already-ignored dirs, lets exceptions bubble.

- [ ] **Step 3: Modify `reconcile.py`**

Replace the body of `reconcile_subtree` and `_reconcile_path`:

```python
def reconcile_subtree(root: Path, subdir: Path, cache: RuleCache) -> Report:
    start = time.perf_counter()
    report = Report()

    root = root.resolve()
    subdir = subdir.resolve()

    _reconcile_path(subdir, cache, report)
    # If subdir itself is now ignored, don't descend.
    if _safe_is_ignored(subdir):
        report.duration_s = time.perf_counter() - start
        return report

    for current, dirnames, filenames in os.walk(subdir, followlinks=False):
        current_path = Path(current)
        # Filter out directories that are now ignored; os.walk will then skip them.
        kept_dirs: list[str] = []
        for name in dirnames:
            child = current_path / name
            _reconcile_path(child, cache, report)
            if not _safe_is_ignored(child):
                kept_dirs.append(name)
        dirnames[:] = kept_dirs

        for name in filenames:
            _reconcile_path(current_path / name, cache, report)

    report.duration_s = time.perf_counter() - start
    return report


def _reconcile_path(path: Path, cache: RuleCache, report: Report) -> None:
    try:
        should_ignore = cache.match(path)
        currently_ignored = ads.is_ignored(path)
    except FileNotFoundError:
        logger.debug("Path vanished during reconcile: %s", path)
        return
    except PermissionError as exc:
        logger.warning("Permission denied reading %s: %s", path, exc)
        report.errors.append((path, f"read: {exc}"))
        return

    try:
        if should_ignore and not currently_ignored:
            ads.set_ignored(path)
            report.marked += 1
        elif currently_ignored and not should_ignore:
            ads.clear_ignored(path)
            report.cleared += 1
    except FileNotFoundError:
        logger.debug("Path vanished before ADS write: %s", path)
    except PermissionError as exc:
        logger.warning("Permission denied writing ADS on %s: %s", path, exc)
        report.errors.append((path, f"write: {exc}"))


def _safe_is_ignored(path: Path) -> bool:
    try:
        return ads.is_ignored(path)
    except (FileNotFoundError, PermissionError):
        return False
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_reconcile_basic.py tests/test_reconcile_edges.py -v`
Expected: all 7 pass.

- [ ] **Step 5: Commit**

```bash
git add src/dropboxignore/reconcile.py tests/test_reconcile_edges.py
git commit -m "feat(reconcile): skip ignored subtrees and partition per-path errors"
```

---

## Task 10: `state` module — read/write daemon state file

**Goal:** Serialise and deserialise `state.json` (location + schema per spec). Used by both the daemon (to write on each sweep) and the `status` CLI (to read).

**Files:**
- Create: `src/dropboxignore/state.py`
- Create: `tests/test_state.py`

**Steps:**

- [ ] **Step 1: Write failing tests**

`tests/test_state.py`:
```python
from datetime import datetime, timezone
from pathlib import Path

from dropboxignore import state


def test_roundtrip(tmp_path):
    s = state.State(
        daemon_pid=1234,
        daemon_started=datetime(2026, 4, 20, 9, 0, tzinfo=timezone.utc),
        last_sweep=datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc),
        last_sweep_duration_s=1.5,
        last_sweep_marked=5,
        last_sweep_cleared=2,
        last_sweep_errors=0,
        last_error=None,
        watched_roots=[Path(r"C:\Dropbox")],
    )
    path = tmp_path / "state.json"
    state.write(s, path)

    loaded = state.read(path)
    assert loaded == s


def test_read_missing_returns_none(tmp_path):
    assert state.read(tmp_path / "does_not_exist.json") is None


def test_read_corrupt_returns_none(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("not json", encoding="utf-8")
    assert state.read(p) is None


def test_default_path_under_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert state.default_path() == tmp_path / "dropboxignore" / "state.json"
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `uv run pytest tests/test_state.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `state.py`**

`src/dropboxignore/state.py`:
```python
"""Persist daemon state to LOCALAPPDATA\\dropboxignore\\state.json."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass
class LastError:
    time: datetime
    path: Path
    message: str


@dataclass
class State:
    daemon_pid: int | None = None
    daemon_started: datetime | None = None
    last_sweep: datetime | None = None
    last_sweep_duration_s: float = 0.0
    last_sweep_marked: int = 0
    last_sweep_cleared: int = 0
    last_sweep_errors: int = 0
    last_error: LastError | None = None
    watched_roots: list[Path] = field(default_factory=list)


def default_path() -> Path:
    localappdata = os.environ.get("LOCALAPPDATA")
    base = Path(localappdata) if localappdata else Path.home() / "AppData" / "Local"
    return base / "dropboxignore" / "state.json"


def write(state: State, path: Path | None = None) -> None:
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_encode(state), indent=2), encoding="utf-8")


def read(path: Path | None = None) -> State | None:
    path = path or default_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("State file %s corrupt: %s", path, exc)
        return None
    return _decode(raw)


def _encode(state: State) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "daemon_pid": state.daemon_pid,
        "daemon_started": state.daemon_started.isoformat() if state.daemon_started else None,
        "last_sweep": state.last_sweep.isoformat() if state.last_sweep else None,
        "last_sweep_duration_s": state.last_sweep_duration_s,
        "last_sweep_marked": state.last_sweep_marked,
        "last_sweep_cleared": state.last_sweep_cleared,
        "last_sweep_errors": state.last_sweep_errors,
        "last_error": {
            "time": state.last_error.time.isoformat(),
            "path": str(state.last_error.path),
            "message": state.last_error.message,
        } if state.last_error else None,
        "watched_roots": [str(p) for p in state.watched_roots],
    }


def _decode(raw: dict) -> State:
    return State(
        daemon_pid=raw.get("daemon_pid"),
        daemon_started=_parse_dt(raw.get("daemon_started")),
        last_sweep=_parse_dt(raw.get("last_sweep")),
        last_sweep_duration_s=raw.get("last_sweep_duration_s", 0.0),
        last_sweep_marked=raw.get("last_sweep_marked", 0),
        last_sweep_cleared=raw.get("last_sweep_cleared", 0),
        last_sweep_errors=raw.get("last_sweep_errors", 0),
        last_error=LastError(
            time=_parse_dt(raw["last_error"]["time"]),
            path=Path(raw["last_error"]["path"]),
            message=raw["last_error"]["message"],
        ) if raw.get("last_error") else None,
        watched_roots=[Path(p) for p in raw.get("watched_roots", [])],
    )


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_state.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/dropboxignore/state.py tests/test_state.py
git commit -m "feat(state): persist daemon state to LOCALAPPDATA\\dropboxignore\\state.json"
```

---

## Task 11: `cli` scaffold + `apply` command

**Goal:** Wire up the `click` entry points. The `apply` command drives `reconcile_subtree` against a given path (or all discovered roots).

**Files:**
- Create: `src/dropboxignore/cli.py`
- Create: `tests/test_cli_apply.py`

**Steps:**

- [ ] **Step 1: Write failing test**

`tests/test_cli_apply.py`:
```python
from pathlib import Path

import pytest
from click.testing import CliRunner

from dropboxignore import cli, reconcile
from tests.test_reconcile_basic import FakeADS


@pytest.fixture
def fake_ads(monkeypatch):
    fake = FakeADS()
    monkeypatch.setattr(reconcile, "ads", fake)
    return fake


def test_apply_marks_matching_paths(tmp_path, fake_ads, monkeypatch):
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "src").mkdir()

    # Force roots.discover() to return tmp_path.
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "build").resolve() in fake_ads._ignored
    assert (tmp_path / "src").resolve() not in fake_ads._ignored
    assert "marked=1" in result.output or "1 marked" in result.output


def test_apply_with_path_argument_scopes_reconcile(tmp_path, fake_ads, monkeypatch):
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "build").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "build").mkdir()

    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["apply", str(tmp_path / "a")])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "a" / "build").resolve() in fake_ads._ignored
    assert (tmp_path / "b" / "build").resolve() not in fake_ads._ignored
```

- [ ] **Step 2: Run tests, confirm failure**

Run: `uv run pytest tests/test_cli_apply.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `cli.py`**

`src/dropboxignore/cli.py`:
```python
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cli_apply.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/dropboxignore/cli.py tests/test_cli_apply.py
git commit -m "feat(cli): scaffold click CLI with apply subcommand"
```

---

## Task 12: `cli` — `status`, `list`, `explain` commands

**Goal:** Round out the informational CLI. `status` reads `state.json` and checks the PID; `list` walks the tree printing paths with the ADS set; `explain` uses `RuleCache.explain`.

**Files:**
- Modify: `src/dropboxignore/cli.py`
- Create: `tests/test_cli_status_list_explain.py`

**Steps:**

- [ ] **Step 1: Write failing tests**

`tests/test_cli_status_list_explain.py`:
```python
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from dropboxignore import ads, cli, reconcile, state
from tests.test_reconcile_basic import FakeADS


@pytest.fixture
def fake_ads(monkeypatch):
    fake = FakeADS()
    monkeypatch.setattr(reconcile, "ads", fake)
    monkeypatch.setattr(cli, "ads", fake, raising=False)  # cli.list uses ads too
    return fake


def test_status_reports_no_state_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "default_path", lambda: tmp_path / "missing.json")
    runner = CliRunner()
    result = runner.invoke(cli.main, ["status"])
    assert result.exit_code == 0
    assert "not running" in result.output.lower() or "no state" in result.output.lower()


def test_status_reports_running_daemon(tmp_path, monkeypatch):
    # Write a state file pointing at our own pid (definitely alive).
    import os
    s = state.State(
        daemon_pid=os.getpid(),
        daemon_started=datetime.now(timezone.utc),
        last_sweep=datetime.now(timezone.utc),
        last_sweep_duration_s=1.23,
        last_sweep_marked=7,
        last_sweep_cleared=1,
        last_sweep_errors=0,
        watched_roots=[Path(r"C:\Dropbox")],
    )
    path = tmp_path / "state.json"
    state.write(s, path)
    monkeypatch.setattr(state, "default_path", lambda: path)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["status"])
    assert result.exit_code == 0
    assert "running" in result.output.lower()
    assert "7" in result.output  # marked count


def test_list_prints_paths_with_ads_set(tmp_path, fake_ads, monkeypatch):
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    fake_ads.set_ignored(tmp_path / "a")

    runner = CliRunner()
    result = runner.invoke(cli.main, ["list"])
    assert result.exit_code == 0
    assert str(tmp_path / "a") in result.output
    assert str(tmp_path / "b") not in result.output


def test_explain_prints_matching_rule(tmp_path, monkeypatch):
    (tmp_path / ".dropboxignore").write_text("# h\nbuild/\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["explain", str(tmp_path / "build")])
    assert result.exit_code == 0
    assert "build/" in result.output
    assert ".dropboxignore:2" in result.output or "line 2" in result.output


def test_explain_no_match_output(tmp_path, monkeypatch):
    (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(cli, "_discover_roots", lambda: [tmp_path])

    runner = CliRunner()
    result = runner.invoke(cli.main, ["explain", str(tmp_path / "src")])
    assert result.exit_code == 0
    assert "no match" in result.output.lower()
```

- [ ] **Step 2: Run tests, confirm failures**

Run: `uv run pytest tests/test_cli_status_list_explain.py -v`
Expected: 5 fails (commands don't exist).

- [ ] **Step 3: Add commands to `cli.py`**

Add these imports at the top of `cli.py`:
```python
import os
from dropboxignore import ads, state
```

Add at module level:
```python
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
```

Add new commands to the `main` group:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cli_status_list_explain.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/dropboxignore/cli.py tests/test_cli_status_list_explain.py
git commit -m "feat(cli): add status, list, explain commands"
```

---

## Task 13: Debouncer — event coalescer

**Goal:** A tiny utility class that takes stream of `(kind, key, payload)` tuples and emits one coalesced payload per `(kind, key)` after `N` ms of quiet. Per-event-type timeouts: rules=100, dir=0, other=500.

**Files:**
- Create: `src/dropboxignore/debounce.py`
- Create: `tests/test_debounce.py`

**Steps:**

- [ ] **Step 1: Write failing tests**

`tests/test_debounce.py`:
```python
import threading
import time

import pytest

from dropboxignore.debounce import Debouncer, EventKind


def test_single_event_is_emitted_after_quiet_period():
    received: list[tuple] = []
    d = Debouncer(on_emit=received.append,
                  timeouts_ms={EventKind.DIR_CREATE: 10, EventKind.OTHER: 50, EventKind.RULES: 20})
    d.start()
    try:
        d.submit(EventKind.OTHER, "key1", "payload1")
        time.sleep(0.2)
        assert received == [(EventKind.OTHER, "key1", "payload1")]
    finally:
        d.stop()


def test_coalesces_repeated_events_for_same_key():
    received: list[tuple] = []
    d = Debouncer(on_emit=received.append,
                  timeouts_ms={EventKind.DIR_CREATE: 10, EventKind.OTHER: 100, EventKind.RULES: 20})
    d.start()
    try:
        for _ in range(5):
            d.submit(EventKind.OTHER, "samekey", "last")
            time.sleep(0.02)
        time.sleep(0.25)
        assert received == [(EventKind.OTHER, "samekey", "last")]
    finally:
        d.stop()


def test_different_keys_emit_independently():
    received: list[tuple] = []
    d = Debouncer(on_emit=received.append,
                  timeouts_ms={EventKind.DIR_CREATE: 10, EventKind.OTHER: 50, EventKind.RULES: 20})
    d.start()
    try:
        d.submit(EventKind.OTHER, "a", "aa")
        d.submit(EventKind.OTHER, "b", "bb")
        time.sleep(0.2)
        keys = sorted([r[1] for r in received])
        assert keys == ["a", "b"]
    finally:
        d.stop()


def test_dir_create_emits_immediately():
    received: list[tuple] = []
    d = Debouncer(on_emit=received.append,
                  timeouts_ms={EventKind.DIR_CREATE: 0, EventKind.OTHER: 500, EventKind.RULES: 100})
    d.start()
    try:
        d.submit(EventKind.DIR_CREATE, "newdir", "p")
        time.sleep(0.05)
        assert received == [(EventKind.DIR_CREATE, "newdir", "p")]
    finally:
        d.stop()
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `uv run pytest tests/test_debounce.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `debounce.py`**

`src/dropboxignore/debounce.py`:
```python
"""Per-(kind, key) debouncing queue with a background worker."""

from __future__ import annotations

import enum
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class EventKind(enum.Enum):
    RULES = "rules"         # .dropboxignore create/modify/delete
    DIR_CREATE = "dir"      # directory creation (react immediately)
    OTHER = "other"         # everything else worth reconciling


@dataclass
class _Pending:
    payload: object
    deadline: float  # monotonic time when this should fire


class Debouncer:
    """Coalesce events per (kind, key) and emit after a quiet period."""

    def __init__(
        self,
        on_emit: Callable[[tuple[EventKind, str, object]], None],
        timeouts_ms: dict[EventKind, int],
        tick_ms: int = 20,
    ) -> None:
        self._on_emit = on_emit
        self._timeouts = {k: v / 1000.0 for k, v in timeouts_ms.items()}
        self._tick = tick_ms / 1000.0
        self._pending: dict[tuple[EventKind, str], _Pending] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="debouncer")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def submit(self, kind: EventKind, key: str, payload: object) -> None:
        timeout = self._timeouts[kind]
        deadline = time.monotonic() + timeout
        with self._lock:
            self._pending[(kind, key)] = _Pending(payload=payload, deadline=deadline)

    def _run(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            due: list[tuple[EventKind, str, object]] = []
            with self._lock:
                for key, pending in list(self._pending.items()):
                    if pending.deadline <= now:
                        due.append((key[0], key[1], pending.payload))
                        del self._pending[key]
            for item in due:
                try:
                    self._on_emit(item)
                except Exception:
                    logger.exception("debouncer emit handler failed")
            time.sleep(self._tick)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_debounce.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/dropboxignore/debounce.py tests/test_debounce.py
git commit -m "feat(debounce): per-(kind,key) event coalescer with background worker"
```

---

## Task 14: `daemon._dispatch` — pure event-to-reconcile translation

**Goal:** Extract the watcher event dispatch as a pure function so we can test it without spinning up `watchdog`. Given a `watchdog` event (or stand-in) and a `cache`, call the right cache method and issue the right `reconcile_subtree` call.

**Files:**
- Create: `src/dropboxignore/daemon.py`
- Create: `tests/test_daemon_dispatch.py`

**Steps:**

- [ ] **Step 1: Write failing tests**

`tests/test_daemon_dispatch.py`:
```python
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dropboxignore import daemon
from dropboxignore.debounce import EventKind


def _stub_event(kind: str, src_path: str, is_directory: bool = False, dest_path: str | None = None):
    e = MagicMock()
    e.event_type = kind          # 'created' / 'modified' / 'deleted' / 'moved'
    e.src_path = src_path
    e.dest_path = dest_path
    e.is_directory = is_directory
    return e


def test_classify_rules_file_created():
    ev = _stub_event("created", r"C:\Dropbox\proj\.dropboxignore")
    kind, key = daemon._classify(ev, roots=[Path(r"C:\Dropbox")])
    assert kind == EventKind.RULES
    assert key == r"C:\Dropbox\proj\.dropboxignore".lower()


def test_classify_directory_created():
    ev = _stub_event("created", r"C:\Dropbox\proj\node_modules", is_directory=True)
    kind, key = daemon._classify(ev, roots=[Path(r"C:\Dropbox")])
    assert kind == EventKind.DIR_CREATE


def test_classify_file_modified_is_ignored():
    ev = _stub_event("modified", r"C:\Dropbox\proj\foo.txt", is_directory=False)
    assert daemon._classify(ev, roots=[Path(r"C:\Dropbox")]) is None


def test_classify_delete_is_ignored_for_non_rules_file():
    ev = _stub_event("deleted", r"C:\Dropbox\proj\foo.txt")
    assert daemon._classify(ev, roots=[Path(r"C:\Dropbox")]) is None


def test_classify_event_outside_any_root_is_ignored():
    ev = _stub_event("created", r"D:\Other\foo", is_directory=True)
    assert daemon._classify(ev, roots=[Path(r"C:\Dropbox")]) is None


def test_dispatch_rules_reloads_and_reconciles(tmp_path, monkeypatch):
    cache = MagicMock()
    reconcile_calls: list = []
    monkeypatch.setattr(daemon, "reconcile_subtree",
                        lambda root, sub, c: reconcile_calls.append((root, sub)))

    ignore_file = tmp_path / "proj" / ".dropboxignore"
    ignore_file.parent.mkdir()
    ignore_file.write_text("build/\n", encoding="utf-8")

    ev = _stub_event("modified", str(ignore_file))
    daemon._dispatch(ev, cache, roots=[tmp_path])

    cache.reload_file.assert_called_once_with(ignore_file)
    assert reconcile_calls == [(tmp_path, ignore_file.parent)]


def test_dispatch_dir_create_reconciles_that_dir(tmp_path, monkeypatch):
    cache = MagicMock()
    reconcile_calls: list = []
    monkeypatch.setattr(daemon, "reconcile_subtree",
                        lambda root, sub, c: reconcile_calls.append((root, sub)))

    new_dir = tmp_path / "proj" / "node_modules"
    new_dir.mkdir(parents=True)

    ev = _stub_event("created", str(new_dir), is_directory=True)
    daemon._dispatch(ev, cache, roots=[tmp_path])

    cache.reload_file.assert_not_called()
    assert reconcile_calls == [(tmp_path, new_dir)]


def test_dispatch_deleted_rules_file_removes_from_cache(tmp_path, monkeypatch):
    cache = MagicMock()
    reconcile_calls: list = []
    monkeypatch.setattr(daemon, "reconcile_subtree",
                        lambda root, sub, c: reconcile_calls.append((root, sub)))

    ignore_file = tmp_path / "proj" / ".dropboxignore"
    ignore_file.parent.mkdir()
    # Note: file doesn't exist on disk — simulates post-delete event.

    ev = _stub_event("deleted", str(ignore_file))
    daemon._dispatch(ev, cache, roots=[tmp_path])

    cache.remove_file.assert_called_once_with(ignore_file)
    assert reconcile_calls == [(tmp_path, ignore_file.parent)]
```

- [ ] **Step 2: Run tests, confirm failures**

Run: `uv run pytest tests/test_daemon_dispatch.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement daemon classifier and dispatcher**

`src/dropboxignore/daemon.py`:
```python
"""Long-running daemon: watchdog observer + hourly sweep + event dispatch."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from dropboxignore.debounce import EventKind
from dropboxignore.reconcile import reconcile_subtree
from dropboxignore.rules import IGNORE_FILENAME, RuleCache

logger = logging.getLogger(__name__)


def _root_of(path: Path, roots: list[Path]) -> Path | None:
    for r in roots:
        try:
            path.relative_to(r)
            return r
        except ValueError:
            continue
    return None


def _classify(event: Any, roots: list[Path]) -> tuple[EventKind, str] | None:
    src = Path(event.src_path)
    if _root_of(src, roots) is None:
        return None
    if src.name == IGNORE_FILENAME:
        # any CRUD on a .dropboxignore is an EventKind.RULES event
        return EventKind.RULES, str(src).lower()
    if event.event_type == "created" and event.is_directory:
        return EventKind.DIR_CREATE, str(src).lower()
    if event.event_type in ("created", "moved"):
        return EventKind.OTHER, str(src).lower()
    # Everything else (modified non-rules file, deleted non-rules file) — skip.
    return None


def _dispatch(event: Any, cache: RuleCache, roots: list[Path]) -> None:
    classification = _classify(event, roots)
    if classification is None:
        return
    kind, _key = classification
    src = Path(event.src_path)
    root = _root_of(src, roots)
    if root is None:
        return

    if kind is EventKind.RULES:
        if event.event_type == "deleted":
            cache.remove_file(src)
        else:
            cache.reload_file(src)
        reconcile_subtree(root, src.parent, cache)
    elif kind is EventKind.DIR_CREATE:
        reconcile_subtree(root, src, cache)
    else:
        target = src.parent if src.is_file() or not src.exists() else src
        reconcile_subtree(root, target, cache)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_daemon_dispatch.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/dropboxignore/daemon.py tests/test_daemon_dispatch.py
git commit -m "feat(daemon): pure _classify and _dispatch event handlers"
```

---

## Task 15: `daemon.run()` — observer + timer + signals

**Goal:** Glue the watchdog `Observer`, the `Debouncer`, and the hourly sweep timer into a runnable `run()` function. It blocks until stop signal, writes state on each sweep, logs startup/shutdown. The end-to-end smoke test in Task 19 exercises this; here we unit-test structure.

**Files:**
- Modify: `src/dropboxignore/daemon.py`

**Steps:**

- [ ] **Step 1: Extend `daemon.py`**

Append to `src/dropboxignore/daemon.py`:

```python
import logging.handlers
import os
import signal
import threading
from datetime import datetime, timezone

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from dropboxignore import roots as roots_module
from dropboxignore import state as state_module
from dropboxignore.debounce import Debouncer

SWEEP_INTERVAL_S = 3600

DEFAULT_TIMEOUTS_MS = {
    EventKind.RULES: 100,
    EventKind.DIR_CREATE: 0,
    EventKind.OTHER: 500,
}


def _timeouts_from_env() -> dict[EventKind, int]:
    return {
        EventKind.RULES: int(os.environ.get("DROPBOXIGNORE_DEBOUNCE_RULES_MS", "100")),
        EventKind.DIR_CREATE: int(os.environ.get("DROPBOXIGNORE_DEBOUNCE_DIRS_MS", "0")),
        EventKind.OTHER: int(os.environ.get("DROPBOXIGNORE_DEBOUNCE_OTHER_MS", "500")),
    }


def _log_dir() -> Path:
    localappdata = os.environ.get("LOCALAPPDATA")
    base = Path(localappdata) if localappdata else Path.home() / "AppData" / "Local"
    return base / "dropboxignore"


def _configure_logging() -> None:
    """Install a rotating file handler writing to %LOCALAPPDATA%\\dropboxignore\\daemon.log."""
    level_name = os.environ.get("DROPBOXIGNORE_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "daemon.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=4,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))

    root_logger = logging.getLogger("dropboxignore")
    root_logger.setLevel(level)
    # Replace any pre-existing handlers installed by the CLI group.
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)
    root_logger.addHandler(handler)
    root_logger.propagate = False


class _WatchdogHandler(FileSystemEventHandler):
    def __init__(self, debouncer: Debouncer, roots: list[Path]) -> None:
        self._debouncer = debouncer
        self._roots = roots

    def on_any_event(self, event):
        try:
            classification = _classify(event, self._roots)
            if classification is not None:
                kind, key = classification
                self._debouncer.submit(kind, key, event)
        except Exception:
            logger.exception("watchdog handler failed on event %r", event)


def run(stop_event: threading.Event | None = None) -> None:
    _configure_logging()
    stop_event = stop_event or threading.Event()

    # SIGINT / SIGTERM stop the daemon.
    def _signal_handler(signum, frame):
        logger.info("received signal %s, shutting down", signum)
        stop_event.set()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(s, _signal_handler)
        except (ValueError, AttributeError):
            pass  # not always supported in background threads / Windows

    configured_roots = roots_module.discover()
    if not configured_roots:
        logger.error("no Dropbox roots discovered; exiting")
        return

    cache = RuleCache()
    for r in configured_roots:
        cache.load_root(r)

    # Initial sweep catches anything changed while daemon was off.
    _sweep_once(configured_roots, cache)

    debouncer = Debouncer(
        on_emit=lambda item: _dispatch(item[2], cache, configured_roots),
        timeouts_ms=_timeouts_from_env(),
    )
    debouncer.start()

    handler = _WatchdogHandler(debouncer, configured_roots)
    observer = Observer()
    for r in configured_roots:
        observer.schedule(handler, str(r), recursive=True)
    observer.start()
    logger.info("watching roots: %s", [str(r) for r in configured_roots])

    # Hourly sweep timer — use Event.wait loop so stop_event interrupts it.
    try:
        while not stop_event.is_set():
            woke = stop_event.wait(SWEEP_INTERVAL_S)
            if woke:
                break
            _sweep_once(configured_roots, cache)
    finally:
        observer.stop()
        observer.join()
        debouncer.stop()
        logger.info("daemon stopped")


def _sweep_once(roots: list[Path], cache: RuleCache) -> None:
    start = datetime.now(timezone.utc)
    total_marked = 0
    total_cleared = 0
    total_errors = 0
    total_duration = 0.0

    for r in roots:
        cache.load_root(r)
        report = reconcile_subtree(r, r, cache)
        total_marked += report.marked
        total_cleared += report.cleared
        total_errors += len(report.errors)
        total_duration += report.duration_s

    logger.info(
        "sweep completed: marked=%d cleared=%d errors=%d duration=%.2fs",
        total_marked, total_cleared, total_errors, total_duration,
    )

    s = state_module.State(
        daemon_pid=os.getpid(),
        daemon_started=start,
        last_sweep=datetime.now(timezone.utc),
        last_sweep_duration_s=total_duration,
        last_sweep_marked=total_marked,
        last_sweep_cleared=total_cleared,
        last_sweep_errors=total_errors,
        watched_roots=roots,
    )
    try:
        state_module.write(s)
    except OSError as exc:
        logger.warning("could not write state file: %s", exc)
```

- [ ] **Step 2: Wire `daemon` subcommand to `cli.py`**

In `src/dropboxignore/cli.py`, add this command inside the `main` group:

```python
@main.command()
def daemon() -> None:
    """Run the watcher + hourly sweep daemon (foreground)."""
    from dropboxignore import daemon as daemon_mod
    daemon_mod.run()
```

- [ ] **Step 3: Verify compile + no regressions**

Run: `uv run python -c "import dropboxignore.daemon"`
Expected: no error.

Run: `uv run pytest`
Expected: all tests still pass.

- [ ] **Step 4: Commit**

```bash
git add src/dropboxignore/daemon.py src/dropboxignore/cli.py
git commit -m "feat(daemon): assemble Observer + Debouncer + hourly sweep loop"
```

---

## Task 16: PID singleton check in daemon

**Goal:** Refuse to start if another daemon (same PID recorded in state, still alive, and is a Python process) is running.

**Files:**
- Modify: `src/dropboxignore/daemon.py`
- Create: `tests/test_daemon_singleton.py`

**Steps:**

- [ ] **Step 1: Write failing test**

`tests/test_daemon_singleton.py`:
```python
import os
from datetime import datetime, timezone
from pathlib import Path

from dropboxignore import daemon, state


def test_run_refuses_when_another_pid_is_alive(monkeypatch, tmp_path, caplog):
    # Record our own pid (definitely alive & Python) in state.
    s = state.State(
        daemon_pid=os.getpid(),
        daemon_started=datetime.now(timezone.utc),
        watched_roots=[Path(r"C:\Dropbox")],
    )
    path = tmp_path / "state.json"
    state.write(s, path)
    monkeypatch.setattr(state, "default_path", lambda: path)

    # Make sure roots.discover returns something so the function gets to the
    # PID check; we want the singleton check to short-circuit before observer.
    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [tmp_path])

    caplog.set_level("ERROR", logger="dropboxignore.daemon")
    daemon.run()
    assert any("already running" in rec.message.lower() for rec in caplog.records)
```

- [ ] **Step 2: Run test, confirm failure**

Run: `uv run pytest tests/test_daemon_singleton.py -v`
Expected: fails — `run()` currently ignores `state` and proceeds.

- [ ] **Step 3: Add the PID check in `daemon.run()`**

Insert at the top of `daemon.run()`, right after the `stop_event` setup:

```python
    # Refuse to run if another daemon is already running.
    prior = state_module.read()
    if prior is not None and _is_other_live_daemon(prior.daemon_pid):
        logger.error("daemon already running (pid=%d); refusing to start", prior.daemon_pid)
        return
```

And add this helper:

```python
def _is_other_live_daemon(pid: int | None) -> bool:
    if pid is None or pid == os.getpid():
        return False
    try:
        import psutil
    except ImportError:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False
    if not psutil.pid_exists(pid):
        return False
    try:
        proc = psutil.Process(pid)
        return "python" in proc.name().lower()
    except psutil.Error:
        return False
```

The `pid == os.getpid()` short-circuit is important for the test: the stale state file happens to record our own pid, and we don't want to refuse to start against ourselves.

Wait — the test deliberately uses `os.getpid()` to simulate a *different* running daemon. That means the short-circuit above makes the test fail. Update the test to instead use a clearly-separate-but-alive pid:

Update `test_daemon_singleton.py` to spawn a short-lived subprocess and use its pid:

```python
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dropboxignore import daemon, state


def test_run_refuses_when_another_pid_is_alive(monkeypatch, tmp_path, caplog):
    # Spawn a sleeping Python subprocess; use its pid as the "other daemon".
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=0,
    )
    try:
        s = state.State(
            daemon_pid=proc.pid,
            daemon_started=datetime.now(timezone.utc),
            watched_roots=[Path(r"C:\Dropbox")],
        )
        path = tmp_path / "state.json"
        state.write(s, path)
        monkeypatch.setattr(state, "default_path", lambda: path)
        monkeypatch.setattr(daemon.roots_module, "discover", lambda: [tmp_path])

        caplog.set_level("ERROR", logger="dropboxignore.daemon")
        daemon.run()
        assert any("already running" in rec.message.lower() for rec in caplog.records)
    finally:
        proc.kill()
        proc.wait(timeout=5)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_daemon_singleton.py -v`
Expected: passes.

- [ ] **Step 5: Commit**

```bash
git add src/dropboxignore/daemon.py tests/test_daemon_singleton.py
git commit -m "feat(daemon): refuse to start if another python daemon holds the pid"
```

---

## Task 17: `install.py` — Task Scheduler XML + install/uninstall commands

**Goal:** `dropboxignore install` generates a Task Scheduler XML file and runs `schtasks /Create /XML`. `dropboxignore uninstall` runs `schtasks /Delete`. `--purge` also clears every ADS marker under every root.

**Files:**
- Create: `src/dropboxignore/install.py`
- Modify: `src/dropboxignore/cli.py`
- Create: `tests/test_install.py`

**Steps:**

- [ ] **Step 1: Write failing tests**

`tests/test_install.py`:
```python
import getpass
from pathlib import Path

from dropboxignore import install


def test_build_xml_contains_logon_trigger_and_action():
    xml = install.build_task_xml(exe_path=Path(r"C:\bin\dropboxignored.exe"))
    assert "<LogonTrigger>" in xml
    assert f"<UserId>{getpass.getuser()}</UserId>" in xml
    assert r"C:\bin\dropboxignored.exe" in xml
    assert "<RestartOnFailure>" in xml


def test_build_xml_uses_pythonw_when_source_install(tmp_path):
    pythonw = tmp_path / "pythonw.exe"
    xml = install.build_task_xml(
        exe_path=pythonw, arguments="-m dropboxignore daemon"
    )
    assert "pythonw.exe" in xml
    assert "-m dropboxignore daemon" in xml


def test_detect_invocation_returns_frozen_mode(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\bin\dropboxignored.exe")
    exe, args = install.detect_invocation()
    assert exe == Path(r"C:\bin\dropboxignored.exe")
    assert args == ""


def test_detect_invocation_returns_source_mode(monkeypatch):
    import sys
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\uv\tools\dropboxignore\Scripts\python.exe")
    exe, args = install.detect_invocation()
    # Should pick pythonw.exe in the same dir, with -m dropboxignore daemon.
    assert exe.name == "pythonw.exe"
    assert args == "-m dropboxignore daemon"
```

- [ ] **Step 2: Run tests, confirm failures**

Run: `uv run pytest tests/test_install.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `install.py`**

`src/dropboxignore/install.py`:
```python
"""Generate and install the Windows Task Scheduler entry for the daemon."""

from __future__ import annotations

import getpass
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

TASK_NAME = "dropboxignore"


def detect_invocation() -> tuple[Path, str]:
    """Return (executable, arguments) to run the daemon in the current install."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable), ""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return pythonw, "-m dropboxignore daemon"


def build_task_xml(exe_path: Path, arguments: str = "") -> str:
    """Return a Task Scheduler v1.2 XML document for a logon-trigger daemon."""
    user = getpass.getuser()
    args_element = f"<Arguments>{arguments}</Arguments>" if arguments else ""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>dropboxignore daemon: keeps com.dropbox.ignored in sync with .dropboxignore files</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{exe_path}</Command>
      {args_element}
    </Exec>
  </Actions>
</Task>
"""


def install_task() -> None:
    exe, args = detect_invocation()
    xml = build_task_xml(exe, args)
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as tmp:
        tmp.write(xml)
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(
            ["schtasks", "/Create", "/XML", str(tmp_path), "/TN", TASK_NAME, "/F"],
            check=True,
        )
        logger.info("Installed scheduled task %s", TASK_NAME)
    finally:
        tmp_path.unlink(missing_ok=True)


def uninstall_task() -> None:
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        logger.info("Uninstalled scheduled task %s", TASK_NAME)
    else:
        logger.warning("schtasks /Delete returned %d: %s", result.returncode, result.stderr)
```

- [ ] **Step 4: Add `install` / `uninstall` commands to `cli.py`**

Add to `cli.py`:

```python
@main.command()
def install() -> None:
    """Register the daemon as a Task Scheduler entry (logon trigger)."""
    from dropboxignore import install as install_mod
    install_mod.install_task()
    click.echo("Installed scheduled task 'dropboxignore'.")


@main.command()
@click.option("--purge", is_flag=True, help="Also clear every com.dropbox.ignored marker under every root.")
def uninstall(purge: bool) -> None:
    """Remove the scheduled task. With --purge, also clear all ADS markers."""
    from dropboxignore import install as install_mod
    install_mod.uninstall_task()
    click.echo("Uninstalled scheduled task 'dropboxignore'.")

    if purge:
        discovered = _discover_roots()
        cleared = 0
        for r in discovered:
            for current, dirnames, filenames in os.walk(r, followlinks=False):
                current_path = Path(current)
                for name in dirnames + filenames:
                    p = current_path / name
                    try:
                        if ads.is_ignored(p):
                            ads.clear_ignored(p)
                            cleared += 1
                    except (FileNotFoundError, PermissionError):
                        continue
        click.echo(f"Cleared {cleared} com.dropbox.ignored markers.")
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_install.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/dropboxignore/install.py src/dropboxignore/cli.py tests/test_install.py
git commit -m "feat(install): generate Task Scheduler XML and expose install/uninstall CLI"
```

---

## Task 18: Windows ADS integration tests

**Goal:** Exercise the real `ads` module against real NTFS. Marked `windows_only`; skipped on Linux.

**Files:**
- Create: `tests/test_ads_integration.py`

**Steps:**

- [ ] **Step 1: Write tests**

`tests/test_ads_integration.py`:
```python
import sys
from pathlib import Path

import pytest

from dropboxignore import ads

pytestmark = pytest.mark.windows_only

if sys.platform != "win32":
    pytest.skip("NTFS alternate data streams are Windows-only", allow_module_level=True)


def test_roundtrip_on_file(tmp_path):
    p = tmp_path / "file.txt"
    p.touch()
    assert ads.is_ignored(p) is False
    ads.set_ignored(p)
    assert ads.is_ignored(p) is True
    ads.clear_ignored(p)
    assert ads.is_ignored(p) is False


def test_roundtrip_on_directory(tmp_path):
    d = tmp_path / "subdir"
    d.mkdir()
    assert ads.is_ignored(d) is False
    ads.set_ignored(d)
    assert ads.is_ignored(d) is True
    ads.clear_ignored(d)
    assert ads.is_ignored(d) is False


def test_long_path_over_260_chars(tmp_path):
    # Build a nested path well past MAX_PATH.
    current = tmp_path
    for i in range(25):
        current = current / f"segment_{i:02d}_padding_text"
        current.mkdir()
    assert len(str(current)) > 260
    ads.set_ignored(current)
    assert ads.is_ignored(current) is True
    ads.clear_ignored(current)


def test_clear_is_idempotent_on_unmarked_path(tmp_path):
    p = tmp_path / "unmarked.txt"
    p.touch()
    # Must not raise.
    ads.clear_ignored(p)
    assert ads.is_ignored(p) is False
```

- [ ] **Step 2: Run tests on Windows**

Run: `uv run pytest tests/test_ads_integration.py -v -m windows_only`
Expected (on Windows): 4 passed. (On Linux: collected 0, 1 skipped.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_ads_integration.py
git commit -m "test(ads): add Windows-only NTFS alternate data stream integration tests"
```

---

## Task 19: End-to-end daemon smoke test

**Goal:** Drive the real `daemon.run()` loop against `tmp_path` and verify watcher events produce the expected ADS state within a bounded timeout.

**Files:**
- Create: `tests/test_daemon_smoke.py`

**Steps:**

- [ ] **Step 1: Write test**

`tests/test_daemon_smoke.py`:
```python
import sys
import threading
import time
from pathlib import Path

import pytest

from dropboxignore import ads, daemon

pytestmark = pytest.mark.windows_only

if sys.platform != "win32":
    pytest.skip("Daemon smoke test exercises real NTFS ADS; Windows-only",
                allow_module_level=True)


def _poll_until(fn, timeout_s: float = 2.0, interval_s: float = 0.05):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(interval_s)
    return False


def test_daemon_reacts_to_dropboxignore_and_directory_creation(tmp_path, monkeypatch):
    # Redirect roots.discover() to our fake dropbox root.
    monkeypatch.setattr(daemon.roots_module, "discover", lambda: [tmp_path])
    # Ensure the singleton check reads a fresh state path under tmp_path.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))

    stop = threading.Event()
    t = threading.Thread(target=daemon.run, args=(stop,), daemon=True)
    t.start()
    try:
        # Create .dropboxignore and matching directory; expect marker set.
        (tmp_path / ".dropboxignore").write_text("build/\n", encoding="utf-8")
        (tmp_path / "build").mkdir()

        assert _poll_until(lambda: ads.is_ignored(tmp_path / "build")), \
            "build/ was not marked ignored within 2s"

        # Append a negation; create a child; expect child NOT ignored.
        (tmp_path / ".dropboxignore").write_text(
            "build/\n!build/keep/\n", encoding="utf-8"
        )
        (tmp_path / "build" / "keep").mkdir()

        assert _poll_until(
            lambda: not ads.is_ignored(tmp_path / "build" / "keep"),
            timeout_s=3.0,
        ), "build/keep/ was still marked ignored after negation"
    finally:
        stop.set()
        t.join(timeout=5.0)
```

- [ ] **Step 2: Run on Windows**

Run: `uv run pytest tests/test_daemon_smoke.py -v -m windows_only`
Expected: passes on Windows (may take ~5s due to polling). If flaky, increase the polling timeouts before quarantining.

- [ ] **Step 3: Commit**

```bash
git add tests/test_daemon_smoke.py
git commit -m "test(daemon): end-to-end watcher smoke test against real NTFS"
```

---

## Task 20: PyInstaller spec (two binaries)

**Goal:** Build one console binary (`dropboxignore.exe`) and one windowless binary (`dropboxignored.exe`) from one spec file.

**Files:**
- Create: `pyinstaller/dropboxignore.spec`

**Steps:**

- [ ] **Step 1: Write the spec file**

`pyinstaller/dropboxignore.spec`:
```python
# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec building two Windows binaries from the same codebase.

- dropboxignore.exe   : console mode, used for interactive CLI.
- dropboxignored.exe  : no console, launched by Task Scheduler.
"""

from pathlib import Path

SRC = Path("src").resolve()
ENTRY = SRC / "dropboxignore" / "__main__.py"


def _analysis(name: str):
    return Analysis(
        [str(ENTRY)],
        pathex=[str(SRC)],
        binaries=[],
        datas=[],
        hiddenimports=["watchdog.observers.winapi", "watchdog.observers.read_directory_changes"],
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=[],
        win_no_prefer_redirects=False,
        win_private_assemblies=False,
        cipher=None,
        noarchive=False,
    )


# ---- Console variant ------------------------------------------------------
a_console = _analysis("dropboxignore")
pyz_console = PYZ(a_console.pure, a_console.zipped_data, cipher=None)
exe_console = EXE(
    pyz_console,
    a_console.scripts,
    a_console.binaries,
    a_console.zipfiles,
    a_console.datas,
    [],
    name="dropboxignore",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ---- Windowless daemon variant -------------------------------------------
a_daemon = _analysis("dropboxignored")
pyz_daemon = PYZ(a_daemon.pure, a_daemon.zipped_data, cipher=None)
exe_daemon = EXE(
    pyz_daemon,
    a_daemon.scripts,
    a_daemon.binaries,
    a_daemon.zipfiles,
    a_daemon.datas,
    [],
    name="dropboxignored",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
```

Note: `dropboxignored.exe` boots into `__main__.py`, which calls `cli.main()`. For it to auto-run the daemon subcommand, update `__main__.py`:

- [ ] **Step 2: Patch `__main__.py`**

`src/dropboxignore/__main__.py`:
```python
import sys
from pathlib import Path

from dropboxignore.cli import main

if __name__ == "__main__":
    # When invoked as dropboxignored.exe, default to the daemon subcommand.
    exe_name = Path(sys.argv[0]).stem.lower()
    if exe_name == "dropboxignored" and len(sys.argv) == 1:
        sys.argv.append("daemon")
    main()
```

- [ ] **Step 3: Build locally to verify (Windows only, optional for CI-first development)**

Run: `uv run pyinstaller pyinstaller/dropboxignore.spec`
Expected: `dist/dropboxignore.exe` and `dist/dropboxignored.exe` appear. Running `dist/dropboxignore.exe --help` prints click help.

- [ ] **Step 4: Commit**

```bash
git add pyinstaller/dropboxignore.spec src/dropboxignore/__main__.py
git commit -m "build: PyInstaller spec producing dropboxignore + dropboxignored binaries"
```

---

## Task 21: GitHub Actions — `test.yml`

**Goal:** On every push and PR, run ruff + pytest on Ubuntu and Windows. Windows additionally runs `windows_only` tests.

**Files:**
- Create: `.github/workflows/test.yml`

**Steps:**

- [ ] **Step 1: Write the workflow**

`.github/workflows/test.yml`:
```yaml
name: test

on:
  push:
    branches-ignore: []
  pull_request:

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Install uv
        uses: astral-sh/setup-uv@v5

      - name: Sync deps
        run: uv sync --all-extras

      - name: Lint
        run: uv run ruff check

      - name: Unit tests (non-Windows-only)
        run: uv run pytest -m "not windows_only" -v

      - name: Windows-only integration tests
        if: runner.os == 'Windows'
        run: uv run pytest -m windows_only -v
```

- [ ] **Step 2: Commit and push**

```bash
git add .github/workflows/test.yml
git commit -m "ci: run ruff + pytest on Ubuntu and Windows per PR"
```

Push and confirm the workflow runs green on GitHub. If anything fails, fix it and amend/commit; don't merge a red CI.

---

## Task 22: GitHub Actions — `release.yml`

**Goal:** On git tag `v*`, build wheel + sdist with `uv build`, build `.exe` pair with PyInstaller on `windows-latest`, create a GitHub Release with all four artifacts.

**Files:**
- Create: `.github/workflows/release.yml`

**Steps:**

- [ ] **Step 1: Write the workflow**

`.github/workflows/release.yml`:
```yaml
name: release

on:
  push:
    tags: ['v*']

permissions:
  contents: write

jobs:
  build:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Install uv
        uses: astral-sh/setup-uv@v5

      - name: Sync deps
        run: uv sync --all-extras

      - name: Build wheel + sdist
        run: uv build

      - name: Build Windows binaries
        run: uv run pyinstaller pyinstaller/dropboxignore.spec

      - name: Publish GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          files: |
            dist/*.whl
            dist/*.tar.gz
            dist/dropboxignore.exe
            dist/dropboxignored.exe
          generate_release_notes: true
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: release workflow builds wheel + Windows exes on tag"
```

Tagging happens in a separate step (`git tag v0.1.0 && git push --tags`) and is not part of this task.

---

## Task 23: README

**Goal:** A README that covers what it is, how to install (both paths), how to use each command, and the `.dropboxignore` syntax quick-reference.

**Files:**
- Create: `README.md`
- Create: `LICENSE`

**Steps:**

- [ ] **Step 1: Write `README.md`**

```markdown
# dropboxignore

Hierarchical `.dropboxignore` files for Dropbox on Windows. Drop a `.dropboxignore` into any folder under your Dropbox root and matching paths get the `com.dropbox.ignored` NTFS alternate data stream set automatically — no more `node_modules/` cluttering your sync.

## Requirements

- Windows 10 or 11 (NTFS)
- Dropbox desktop client installed
- Either Python ≥ 3.11 with [`uv`](https://docs.astral.sh/uv/) **or** the pre-built `.exe` from a GitHub Release

## Install (source)

```powershell
uv tool install git+https://github.com/<you>/dropboxignore
dropboxignore install
```

`dropboxignore install` registers a Task Scheduler entry that launches the daemon (`pythonw -m dropboxignore daemon`) at every user logon.

## Install (.exe)

1. Download `dropboxignore.exe` and `dropboxignored.exe` from the latest [Release](https://github.com/<you>/dropboxignore/releases).
2. Place both in a stable directory (e.g. `%LOCALAPPDATA%\dropboxignore\bin\`) and add it to your `PATH`.
3. Run `dropboxignore install`.

## `.dropboxignore` syntax

Full `.gitignore` syntax via [`pathspec`](https://github.com/cpburnz/python-pathspec). Matching is case-insensitive to accommodate NTFS. A file named `.dropboxignore` is never itself ignored — it needs to sync so your other machines see the same rules.

Example (put in a project root):

```
# everything javascripty
node_modules/

# Python
__pycache__/
.venv/
*.egg-info/

# Rust
target/

# build output
/dist/
/build/

# except this one specific artifact we want to share
!dist/release-notes.pdf
```

## Commands

| Command | Purpose |
|---|---|
| `dropboxignore install` / `uninstall` | Register / remove the Task Scheduler entry. `uninstall --purge` also clears every existing marker. |
| `dropboxignore daemon` | Run the watcher + hourly sweep in the foreground. Usually invoked by Task Scheduler. |
| `dropboxignore apply [PATH]` | One-shot reconcile of the whole Dropbox (or a subtree). |
| `dropboxignore status` | Is the daemon running? Last sweep counts, last error. |
| `dropboxignore list [PATH]` | Print every path currently bearing the ignore marker. |
| `dropboxignore explain PATH` | Which `.dropboxignore` rule (if any) matches the path? |

## Behaviour

- **Source of truth.** `.dropboxignore` files declare what is ignored. Removing a rule unignores the matching paths on the next reconcile. A path marked ignored via Dropbox's right-click menu but not matching any rule will be unignored.
- **Hybrid trigger.** The daemon reacts to filesystem events in real time *and* runs an hourly safety-net sweep. If the daemon is offline, an initial sweep at the next start catches any drift.
- **Multi-root.** Personal and Business Dropbox roots are discovered automatically from `%APPDATA%\Dropbox\info.json`.

## Configuration

Environment variables read at daemon startup:

| Variable | Default | Purpose |
|---|---|---|
| `DROPBOXIGNORE_DEBOUNCE_RULES_MS` | `100` | Debounce window for `.dropboxignore` file events. |
| `DROPBOXIGNORE_DEBOUNCE_DIRS_MS` | `0` | Debounce for directory-creation events. |
| `DROPBOXIGNORE_DEBOUNCE_OTHER_MS` | `500` | Debounce for other file events. |
| `DROPBOXIGNORE_LOG_LEVEL` | `INFO` | Daemon log level. |

Logs: `%LOCALAPPDATA%\dropboxignore\daemon.log` (rotated, 25 MB total).
State: `%LOCALAPPDATA%\dropboxignore\state.json`.

## License

MIT — see [LICENSE](LICENSE).
```

- [ ] **Step 2: Write `LICENSE`**

Standard MIT license. Fill in current year and name.

```
MIT License

Copyright (c) 2026 Kilo Scheffer

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 3: Commit**

```bash
git add README.md LICENSE
git commit -m "docs: README with install, usage, and syntax reference"
```

---

## Final verification

After all 23 tasks, run the entire suite once and confirm the workflow runs clean:

```bash
uv run ruff check
uv run pytest
```

On Windows:

```bash
uv run pytest -m windows_only
```

If everything passes, tag `v0.1.0` and push — the release workflow will build and publish.

```bash
git tag v0.1.0
git push --tags
```

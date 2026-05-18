import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.windows_only

if sys.platform != "win32":
    pytest.skip("NTFS alternate data streams are Windows-only", allow_module_level=True)

from dbxignore import markers  # noqa: E402  # must come after sys.platform skip guard


def test_roundtrip_on_file(tmp_path: Path) -> None:
    p = tmp_path / "file.txt"
    p.touch()
    assert markers.is_ignored(p) is False
    markers.set_ignored(p)
    assert markers.is_ignored(p) is True
    markers.clear_ignored(p)
    assert markers.is_ignored(p) is False


def test_roundtrip_on_directory(tmp_path: Path) -> None:
    d = tmp_path / "subdir"
    d.mkdir()
    assert markers.is_ignored(d) is False
    markers.set_ignored(d)
    assert markers.is_ignored(d) is True
    markers.clear_ignored(d)
    assert markers.is_ignored(d) is False


def test_long_path_over_260_chars(tmp_path: Path) -> None:
    # Build a nested path well past MAX_PATH.
    current = tmp_path
    for i in range(25):
        current = current / f"segment_{i:02d}_padding_text"
        current.mkdir()
    assert len(str(current)) > 260
    markers.set_ignored(current)
    assert markers.is_ignored(current) is True
    markers.clear_ignored(current)


def test_clear_is_idempotent_on_unmarked_path(tmp_path: Path) -> None:
    p = tmp_path / "unmarked.txt"
    p.touch()
    markers.clear_ignored(p)
    assert markers.is_ignored(p) is False

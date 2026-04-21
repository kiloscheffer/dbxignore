import sys

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
    ads.clear_ignored(p)
    assert ads.is_ignored(p) is False

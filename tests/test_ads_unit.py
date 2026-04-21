from pathlib import Path

import pytest

from dropboxignore import ads


@pytest.mark.windows_only
def test_stream_path_uses_long_path_prefix_and_stream_name():
    p = Path(r"C:\Dropbox\some\dir")
    result = ads._stream_path(p)
    assert result == r"\\?\C:\Dropbox\some\dir:com.dropbox.ignored"


def test_stream_path_rejects_relative_path():
    """Caller contract: ads operates on absolute paths only. The \\\\?\\
    long-path prefix is meaningless before a relative path, so resolving
    silently would mask a bug at the call site."""
    with pytest.raises(ValueError, match="absolute"):
        ads._stream_path(Path("foo"))

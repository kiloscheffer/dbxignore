import sys
from pathlib import Path

import pytest

from dbxignore._backends import windows_ads


@pytest.mark.windows_only
@pytest.mark.skipif(
    sys.platform != "win32",
    reason=r"Path(r'C:\...') is only absolute on Windows",
)
def test_stream_path_uses_long_path_prefix_and_stream_name():
    p = Path(r"C:\Dropbox\some\dir")
    result = windows_ads._stream_path(p)
    assert result == r"\\?\C:\Dropbox\some\dir:com.dropbox.ignored"


def test_stream_path_rejects_relative_path():
    """Caller contract: markers requires an absolute path. The \\\\?\\
    long-path prefix is meaningless before a relative path, so resolving
    silently would mask a bug at the call site."""
    with pytest.raises(ValueError, match="absolute"):
        windows_ads._stream_path(Path("foo"))

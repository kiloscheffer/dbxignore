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

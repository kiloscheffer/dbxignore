"""cli.list and cli.uninstall --purge must survive ENOTSUP from the xattr backend."""

from __future__ import annotations

import errno

from click.testing import CliRunner

from dbxignore import cli


def test_list_survives_enotsup(tmp_path, fake_markers, monkeypatch, write_file):
    """A file whose is_ignored raises OSError(ENOTSUP) must be skipped, not crash the walk."""
    root = tmp_path
    good = write_file(root / "good.txt")
    bad = write_file(root / "bad.txt")

    real_is_ignored = fake_markers.is_ignored

    def selective_raise(path):
        if path.resolve() == bad.resolve():
            raise OSError(errno.ENOTSUP, "Operation not supported")
        return real_is_ignored(path)

    monkeypatch.setattr(fake_markers, "is_ignored", selective_raise)

    # list_ignored discovers roots via cli._discover_roots; monkeypatch that.
    monkeypatch.setattr(cli, "_discover_roots", lambda: [root])

    # Mark good.txt so list has something to print.
    fake_markers.set_ignored(good)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["list"])

    assert result.exit_code == 0, result.output
    # good.txt should be listed; bad.txt was an ENOTSUP error, skipped.
    assert "good.txt" in result.output
    assert "bad.txt" not in result.output

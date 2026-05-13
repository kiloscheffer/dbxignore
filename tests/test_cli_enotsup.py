"""cli.list and cli.uninstall --purge must survive ENOTSUP from the xattr backend."""

from __future__ import annotations

import errno
from typing import TYPE_CHECKING

from click.testing import CliRunner

from dbxignore import cli

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from tests.conftest import FakeMarkers, WriteFile


def test_list_survives_enotsup(
    tmp_path: Path,
    fake_markers: FakeMarkers,
    monkeypatch: pytest.MonkeyPatch,
    write_file: WriteFile,
) -> None:
    """A file whose is_ignored raises OSError(ENOTSUP) does not crash the walk.

    The marked good.txt is still listed on stdout; the unreadable bad.txt is
    surfaced via stderr (scan errors: 1) and the command exits 2 — previously
    the read error was swallowed, hiding partial-failure scans from scripted
    callers. Pins the post-item-7 contract.
    """
    root = tmp_path
    good = write_file(root / "good.txt")
    bad = write_file(root / "bad.txt")

    real_is_ignored = fake_markers.is_ignored

    def selective_raise(path: Path) -> bool:
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

    assert result.exit_code == 2, result.output
    # good.txt should be listed on stdout; bad.txt surfaces as a scan error.
    assert "good.txt" in result.output
    assert "scan errors: 1" in result.output
    assert "bad.txt" in result.output

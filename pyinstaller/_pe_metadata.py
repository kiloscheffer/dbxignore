"""VSVersionInfo factory for the PyInstaller-built Windows binaries.

Used by ``pyinstaller/dbxignore.spec`` to keep the PE VERSIONINFO resource
fields (FileDescription, CompanyName, LegalCopyright, etc.) in one place.
The spec calls ``make_version_info()`` twice — once per binary — with the
three values that differ between the console and GUI binaries
(``internal_name``, ``file_description``, ``original_filename``);
everything else is a project-wide constant defined here so the two
binaries can't drift.

The numeric ``FixedFileInfo`` tuple takes only the PEP 440 release
segment (major.minor.patch) plus a fixed build=0; dev/pre/post suffixes
are dropped because Windows' VERSIONINFO numeric fields are 16-bit
unsigned ints with no room for qualifier semantics. The full PEP 440
string still lands in the human-visible ``FileVersion`` /
``ProductVersion`` ``StringStruct`` entries, so users see (e.g.)
``1.0.4.dev5+gabc123`` in Explorer's Details pane while Windows compares
numerically as ``(1, 0, 4, 0)``.
"""

from __future__ import annotations

import re

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)


def _release_4tuple(version: str) -> tuple[int, int, int, int]:
    """Parse the PEP 440 release segment into a PE-compatible 4-tuple.

    Examples: ``"1.0.4"`` -> ``(1, 0, 4, 0)``; ``"1.0.4.dev5+gabc123"`` ->
    ``(1, 0, 4, 0)``; ``"0.0.0+unknown"`` -> ``(0, 0, 0, 0)``. The build
    component is always zero because hatch-vcs doesn't produce one.

    Raises ``ValueError`` on input that doesn't carry a leading
    ``major.minor`` release segment. The only realistic ways to reach
    that branch are environmental breakage (hatch-vcs misconfiguration,
    truncated CI checkout) — in those cases failing the build loudly is
    safer than silently shipping a binary whose Windows-numeric version
    is ``0.0.0.0``, which Windows treats as "older than everything" and
    interacts badly with installer upgrade logic.
    """
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", version)
    if not match:
        raise ValueError(
            f"Cannot derive PE version 4-tuple from {version!r}; "
            "expected a PEP 440 release segment (e.g. '1.0.4' or '1.0.4.dev5+gabc123')"
        )
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3) or 0),
        0,
    )


def make_version_info(
    *,
    version: str,
    internal_name: str,
    file_description: str,
    original_filename: str,
) -> VSVersionInfo:
    """Build the VSVersionInfo passed to PyInstaller's ``EXE(version=...)``.

    Translation "040904B0" = en-US (0x0409) + Unicode codepage (0x04B0/1200).
    """
    release_tuple = _release_4tuple(version)
    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=release_tuple,
            prodvers=release_tuple,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "Kilo Scheffer"),
                            StringStruct("FileDescription", file_description),
                            StringStruct("FileVersion", version),
                            StringStruct("InternalName", internal_name),
                            StringStruct("LegalCopyright", "Copyright © Kilo Scheffer"),
                            StringStruct("OriginalFilename", original_filename),
                            StringStruct("ProductName", "dbxignore"),
                            StringStruct("ProductVersion", version),
                        ],
                    ),
                ]
            ),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

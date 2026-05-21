# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec building the Linux onedir bundle.

- dbxignore : entry point for all subcommands (CLI + daemon dispatch).

Produces dist/dbxignore/ — the executable plus a _internal/ dependency
tree. --onedir (not --onefile): each launch skips the per-invocation
temp-directory extraction a onefile bundle pays. install.sh and the
Homebrew tap both place this directory.

Sibling to dbxignore.spec (Windows) and dbxignore-macos.spec (macOS). The
Linux xattr backend uses Python's stdlib (os.getxattr / os.setxattr), so no
cffi backend is needed unlike the macOS sibling.
"""

from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

SRC = Path("src").resolve()
ENTRY = SRC / "dbxignore" / "__main__.py"


a = Analysis(
    [str(ENTRY)],
    pathex=[str(SRC)],
    binaries=[],
    datas=copy_metadata("dbxignore"),
    # inotify is the Linux watchdog backend. PyInstaller's analyzer
    # doesn't see the dynamic import via watchdog's platform-detection
    # layer, so force the bundle.
    hiddenimports=["watchdog.observers.inotify"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=None,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=None)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dbxignore",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX is skipped on Linux for the same reason as macOS — the size win
    # is marginal and the compression step adds a runner-side dependency
    # (upx-ucl) that ubuntu-latest may or may not have preinstalled.
    upx=False,
    upx_exclude=[],
    disable_windowed_traceback=False,
    argv_emulation=False,
    # arch follows the runner (x86_64 on ubuntu-22.04). An aarch64 Linux
    # build leg can be added on ubuntu-22.04-arm if demand surfaces.
    target_arch=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="dbxignore",
)

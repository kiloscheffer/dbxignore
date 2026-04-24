# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec building two Windows binaries from the same codebase.

- dbxignore.exe   : console mode, used for interactive CLI.
- dbxignored.exe  : no console, launched by Task Scheduler.
"""

from pathlib import Path

SRC = Path("src").resolve()
ENTRY = SRC / "dbxignore" / "__main__.py"


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
a_console = _analysis("dbxignore")
pyz_console = PYZ(a_console.pure, a_console.zipped_data, cipher=None)
exe_console = EXE(
    pyz_console,
    a_console.scripts,
    a_console.binaries,
    a_console.zipfiles,
    a_console.datas,
    [],
    name="dbxignore",
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
a_daemon = _analysis("dbxignored")
pyz_daemon = PYZ(a_daemon.pure, a_daemon.zipped_data, cipher=None)
exe_daemon = EXE(
    pyz_daemon,
    a_daemon.scripts,
    a_daemon.binaries,
    a_daemon.zipfiles,
    a_daemon.datas,
    [],
    name="dbxignored",
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

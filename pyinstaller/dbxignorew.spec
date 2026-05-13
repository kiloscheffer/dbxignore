"""PyInstaller spec building the GUI-subsystem dbxignorew helper binary.

- dbxignorew.exe : console=False. Never has a console at startup.
                   Used by:
                   * Windows Task Scheduler (daemon entry at logon — no
                     console flash, no orphaned conhost.exe).
                   * Explorer shell-verb registry entries
                     ("Ignore from Dropbox" / "Restore to Dropbox") — the
                     verb invocations route output through MessageBox via
                     src/dbxignore/_windows_dialogs.py.
                   * Explorer double-click — pops a MessageBox saying
                     "dbxignore is a command-line tool" then exits.

Same __main__.py entry as dbxignore.exe; the console-presence probe in
_windows_dialogs.should_use_gui_dialogs() (GetConsoleWindow() == 0)
routes the no-console invocations to MessageBox output.

copy_metadata("dbxignore") bundles the dist-info directory so that
click's --version callback can resolve the version via
importlib.metadata at runtime. (Mirror of dbxignore.spec.)
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
pyz = PYZ(a.pure, a.zipped_data, cipher=None)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="dbxignorew",
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

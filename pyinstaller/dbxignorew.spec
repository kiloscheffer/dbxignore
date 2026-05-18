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

Same __main__.py entry as dbxignore.exe; the no-console-detection check
in _windows_dialogs.should_use_gui_dialogs() routes the no-console
invocations to MessageBox output.

copy_metadata("dbxignore") bundles the dist-info directory so that
click's --version callback can resolve the version via
importlib.metadata at runtime. (Mirror of dbxignore.spec.)
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

SRC = Path("src").resolve()
ENTRY = SRC / "dbxignore" / "__main__.py"

# Shared VERSIONINFO factory; see the matching block in dbxignore.spec.
sys.path.insert(0, SPECPATH)
from _pe_metadata import make_version_info  # noqa: E402

from dbxignore import __version__  # noqa: E402


a = Analysis(
    [str(ENTRY)],
    pathex=[str(SRC)],
    binaries=[],
    # context-menu.ico ships inside the bundle so install_shell_integration
    # (when invoked through this GUI helper — e.g. an Explorer-launched
    # "dbxignorew install" wrapper) can copy it to %LOCALAPPDATA%\dbxignore\
    # icons\. Mirrors dbxignore.spec; see that spec's comment for the
    # hiddenimport rationale.
    datas=copy_metadata("dbxignore") + [
        (str(SRC / "dbxignore" / "_resources" / "context-menu.ico"), "dbxignore/_resources"),
    ],
    hiddenimports=[
        "watchdog.observers.winapi",
        "watchdog.observers.read_directory_changes",
        "dbxignore._resources",
    ],
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
    icon=str(Path("pyinstaller/dbxignore-app.ico").resolve()),
    version=make_version_info(
        version=__version__,
        internal_name="dbxignorew",
        file_description="Hierarchical .dropboxignore for Dropbox (GUI helper)",
        original_filename="dbxignorew.exe",
    ),
)

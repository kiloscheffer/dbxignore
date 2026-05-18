"""PyInstaller spec building the console-subsystem dbxignore binary.

- dbxignore.exe : console=True. The CLI surface. Click + rich-click work
                  normally — pipe, redirect, and ANSI-colour rendering all
                  function as on any console-subsystem Python program.
                  Used by all interactive terminal invocations.

The GUI-subsystem helper (dbxignorew.exe) is built from a separate spec
(pyinstaller/dbxignorew.spec) and shipped alongside this binary.

copy_metadata("dbxignore") bundles the dist-info directory so that
click's `@click.version_option(package_name="dbxignore")` callback can
resolve the version via importlib.metadata at runtime.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

SRC = Path("src").resolve()
ENTRY = SRC / "dbxignore" / "__main__.py"

# Import the shared VERSIONINFO factory from the sibling helper. SPECPATH
# is PyInstaller's injected variable for the spec file's directory; adding
# it to sys.path lets the helper live alongside the specs rather than
# inside the wheel-shipped src/ tree.
sys.path.insert(0, SPECPATH)
from _pe_metadata import make_version_info  # noqa: E402

from dbxignore import __version__  # noqa: E402


a = Analysis(
    [str(ENTRY)],
    pathex=[str(SRC)],
    binaries=[],
    # context-menu.ico ships inside the bundle so install_shell_integration
    # can copy it to %LOCALAPPDATA%\dbxignore\icons\ at install time. The
    # hiddenimport for dbxignore._resources is needed because nothing
    # statically imports the package — it is reached only via
    # importlib.resources.files("dbxignore._resources") at runtime, which
    # PyInstaller's modulegraph can't see.
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
    icon=str(Path("pyinstaller/dbxignore-app.ico").resolve()),
    version=make_version_info(
        version=__version__,
        internal_name="dbxignore",
        file_description="Hierarchical .dropboxignore for Dropbox",
        original_filename="dbxignore.exe",
    ),
)

"""PyInstaller spec building the dbxignore onedir bundle.

Produces a single dist/dbxignore/ directory containing both Windows
executables plus one shared _internal/ dependency tree:

- dbxignore.exe  : console=True. The CLI surface. Click + rich-click
                   render normally on the console subsystem.
- dbxignorew.exe : console=False. The GUI-subsystem helper used by Task
                   Scheduler (daemon entry at logon — no console flash)
                   and the Explorer shell-verb registry entries.

Both executables run the same __main__.py entry point; the only
difference is the PE subsystem bit. Building them from one Analysis and
one COLLECT bundles the interpreter and dependency tree once, not twice.

--onedir (not --onefile): the dependencies are unpacked on disk in
_internal/ next to the .exe, so each launch skips the per-invocation
temp-directory extraction a onefile bundle pays. The installer, the
Scoop bucket, and winget all place this directory permanently.

copy_metadata("dbxignore") bundles the dist-info directory so that
click's @click.version_option(package_name="dbxignore") callback can
resolve the version via importlib.metadata at runtime.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

SRC = Path("src").resolve()
ENTRY = SRC / "dbxignore" / "__main__.py"
ICON = str(Path("pyinstaller/dbxignore-app.ico").resolve())

# Import the shared VERSIONINFO factory. SPECPATH is PyInstaller's
# injected variable for the spec file's directory; adding it to sys.path
# lets the helper live alongside the spec rather than inside src/.
sys.path.insert(0, SPECPATH)
from _pe_metadata import make_version_info  # noqa: E402

from dbxignore import __version__  # noqa: E402


a = Analysis(
    [str(ENTRY)],
    pathex=[str(SRC)],
    binaries=[],
    # context-menu.ico ships inside the bundle so install_shell_integration
    # can copy it to the per-user state dir at install time. The
    # hiddenimport for dbxignore._resources is needed because nothing
    # statically imports the package — it is reached only via
    # importlib.resources at runtime, which modulegraph can't see.
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

# Two EXE targets off one Analysis. exclude_binaries=True keeps each EXE
# a bare bootstrap exe; the shared binaries/datas go into COLLECT below.
exe_console = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dbxignore",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
    version=make_version_info(
        version=__version__,
        internal_name="dbxignore",
        file_description="Hierarchical .dropboxignore for Dropbox",
        original_filename="dbxignore.exe",
    ),
)

exe_gui = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dbxignorew",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
    version=make_version_info(
        version=__version__,
        internal_name="dbxignorew",
        file_description="Hierarchical .dropboxignore for Dropbox (GUI helper)",
        original_filename="dbxignorew.exe",
    ),
)

coll = COLLECT(
    exe_console,
    exe_gui,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="dbxignore",
)

"""PyInstaller spec building a single Windows binary.

- dbxignore.exe   : GUI subsystem; AttachConsole in _windows_console handles
                    the three launch contexts (interactive console, Task
                    Scheduler, Windows shell verbs).
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


# ---- Single binary --------------------------------------------------------
a = _analysis("dbxignore")
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
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)


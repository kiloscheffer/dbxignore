# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec building two macOS Mach-O binaries from the same codebase.

- dbxignore   : the CLI entry point.
- dbxignored  : the daemon shim, launched by launchd.

Sibling to dbxignore.spec (Windows). The platform-specific bits (hiddenimports,
upx, binary names) differ enough to warrant a sibling rather than a single
parameterized spec — branching sys.platform inside a .spec file hides
platform-specific assumptions from the maintainer.
"""

from pathlib import Path

SRC = Path("src").resolve()
ENTRY = SRC / "dbxignore" / "__main__.py"


def _analysis():
    return Analysis(
        [str(ENTRY)],
        pathex=[str(SRC)],
        binaries=[],
        datas=[],
        # FSEvents is the macOS watchdog backend. PyInstaller's analyzer
        # doesn't see the dynamic import via watchdog's platform-detection
        # layer, so force the bundle.
        hiddenimports=["watchdog.observers.fsevents"],
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=[],
        cipher=None,
        noarchive=False,
    )


# ---- CLI variant ---------------------------------------------------------
a_cli = _analysis()
pyz_cli = PYZ(a_cli.pure, a_cli.zipped_data, cipher=None)
exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    a_cli.binaries,
    a_cli.zipfiles,
    a_cli.datas,
    [],
    name="dbxignore",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX-compressed Mach-O confuses Gatekeeper into "the binary is damaged"
    # dialogs even on unsigned binaries; size win is marginal vs the
    # diagnostic cost of debugging that remotely.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    disable_windowed_traceback=False,
    argv_emulation=False,
    # arch follows the runner (arm64 on macos-latest); Intel users install
    # via PyPI per the README.
    target_arch=None,
    # Unsigned binaries — Gatekeeper bypass via `xattr -d com.apple.quarantine`
    # is documented in the README.
    codesign_identity=None,
    entitlements_file=None,
)

# ---- Daemon variant ------------------------------------------------------
a_daemon = _analysis()
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
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

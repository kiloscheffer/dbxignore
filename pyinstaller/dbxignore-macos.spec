# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec building a single macOS Mach-O binary.

- dbxignore : entry point for all subcommands (CLI + daemon dispatch).

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
        #
        # `_cffi_backend` is a top-level C extension that ships alongside
        # `cffi` (sibling on disk, not a submodule). The macOS xattr backend
        # imports `xattr` → `cffi` → `_cffi_backend`; the contrib hook for
        # cffi normally bundles it, but the v0.4.0a1 macOS build shipped
        # without it ("ModuleNotFoundError: No module named '_cffi_backend'"
        # on first launch). Listing it explicitly belts-and-suspenders the
        # contrib hook so a future version drift can't silently re-introduce
        # the regression.
        hiddenimports=[
            "watchdog.observers.fsevents",
            "_cffi_backend",
        ],
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=[],
        cipher=None,
        noarchive=False,
    )


# ---- Single binary -------------------------------------------------------
a = _analysis()
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


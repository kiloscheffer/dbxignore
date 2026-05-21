# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec building the macOS onedir bundle.

- dbxignore : entry point for all subcommands (CLI + daemon dispatch).

Produces dist/dbxignore/ — the executable plus a _internal/ dependency
tree. --onedir (not --onefile): the dependencies are unpacked on disk, so
each launch skips the per-invocation temp-directory extraction a onefile
bundle pays. install.sh and the Homebrew tap both place this directory.

Sibling to dbxignore.spec (Windows). The platform-specific bits
(hiddenimports, upx, binary names) differ enough to warrant a sibling
rather than a single parameterized spec.
"""

from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

SRC = Path("src").resolve()
ENTRY = SRC / "dbxignore" / "__main__.py"


def _analysis():
    return Analysis(
        [str(ENTRY)],
        pathex=[str(SRC)],
        binaries=[],
        datas=copy_metadata("dbxignore"),
        # FSEvents is the macOS watchdog backend. PyInstaller's analyzer
        # doesn't see the dynamic import via watchdog's platform-detection
        # layer, so force the bundle.
        #
        # `_cffi_backend` is a top-level C extension that ships alongside
        # `cffi` (sibling on disk, not a submodule). The macOS xattr backend
        # imports `xattr` → `cffi` → `_cffi_backend`; the contrib hook for
        # cffi normally bundles it, but the hook can miss this top-level
        # sibling and the bundle ships without it
        # ("ModuleNotFoundError: No module named '_cffi_backend'" on first
        # launch). Listing it explicitly belts-and-suspenders the contrib
        # hook so a future version drift can't silently drop it.
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


# ---- onedir bundle -------------------------------------------------------
a = _analysis()
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
    # UPX-compressed Mach-O confuses Gatekeeper into "the binary is damaged"
    # dialogs even on unsigned binaries; size win is marginal vs the
    # diagnostic cost of debugging that remotely.
    upx=False,
    upx_exclude=[],
    disable_windowed_traceback=False,
    argv_emulation=False,
    # arch follows the runner (arm64 on macos-latest); Intel users install
    # via PyPI per the README.
    target_arch=None,
    # Unsigned binaries — install.sh fetches via curl, which does not set
    # the com.apple.quarantine attribute, so Gatekeeper does not block them.
    codesign_identity=None,
    entitlements_file=None,
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

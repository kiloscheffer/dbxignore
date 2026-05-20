#requires -Version 7.0
<#
.SYNOPSIS
    Build the dbxignore Windows installer locally.
.DESCRIPTION
    Runs the PyInstaller onedir build, then compiles the Inno Setup
    installer. The local equivalent of the release.yml "Build Windows
    binaries" + "Build Windows installer" steps; CI runs those inline.

    Requires Inno Setup 6 (ISCC.exe):
        winget install JRSoftware.InnoSetup
.NOTES
    Run from anywhere; the script locates the repository root itself.
#>
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    Write-Host '==> Building the onedir bundle (PyInstaller)...'
    # --noconfirm: replace an existing dist/dbxignore/ without prompting,
    # so repeated local runs don't stall on PyInstaller's overwrite guard.
    uv run --with pyinstaller pyinstaller --noconfirm pyinstaller/dbxignore.spec
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed (exit $LASTEXITCODE)" }

    $versionLine = & dist\dbxignore\dbxignore.exe --version
    $ver = ($versionLine -replace '.*version\s+', '').Trim()
    Write-Host "==> Installer AppVersion: $ver"

    $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (-not (Test-Path $iscc)) {
        throw "ISCC.exe not found at $iscc. Install Inno Setup 6: winget install JRSoftware.InnoSetup"
    }

    Write-Host '==> Compiling the installer (Inno Setup)...'
    & $iscc "/DAppVersion=$ver" installer\dbxignore.iss
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed (exit $LASTEXITCODE)" }

    Write-Host '==> Done: dist\dbxignore-setup.exe'
}
finally {
    Pop-Location
}

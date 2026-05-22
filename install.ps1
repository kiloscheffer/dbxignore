# Keep this file ASCII-only: Windows PowerShell 5.1 reads a BOM-less .ps1
# as the system ANSI codepage, which corrupts non-ASCII bytes (a mangled
# em-dash, for one, parses as a smart-quote string delimiter).
<#
.SYNOPSIS
dbxignore installer for Windows.

.DESCRIPTION
Downloads the dbxignore Windows bundle, installs it under
%LOCALAPPDATA%\Programs\dbxignore, adds that directory to your PATH, and
registers the daemon by running 'dbxignore install'.

.PARAMETER Uninstall
Remove the daemon registration, the installed files, and the PATH entry.

.PARAMETER NoDaemon
Install the binaries only; skip 'dbxignore install'.

.PARAMETER NoModifyPath
Do not modify PATH; print the directory to add instead.

.PARAMETER Help
Print usage and exit.

.NOTES
Usage:
  powershell -c "irm https://dbxignore.com/install.ps1 | iex"

The irm | iex one-liner cannot pass -switches, so each switch has an
environment-variable equivalent:
  DBXIGNORE_VERSION           pin a release, e.g. 1.2.3 (default: latest)
  DBXIGNORE_INSTALL_ARCHIVE   install from a local .zip instead of downloading
  DBXIGNORE_UNINSTALL=1       same as -Uninstall
  DBXIGNORE_NO_DAEMON=1       same as -NoDaemon
  DBXIGNORE_NO_MODIFY_PATH=1  same as -NoModifyPath
#>
param(
    [switch]$Uninstall,
    [switch]$NoDaemon,
    [switch]$NoModifyPath,
    [switch]$Help
)

$ErrorActionPreference = 'Stop'
# Keep native-command (dbxignore.exe) non-zero exits non-fatal so the
# $LASTEXITCODE checks below behave the same on PowerShell 7 as on 5.1.
$PSNativeCommandUseErrorActionPreference = $false

$Repo       = 'kiloscheffer/dbxignore'
$Asset      = 'dbxignore-windows-x86_64.zip'
$InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\dbxignore'
$EnvRegPath = 'registry::HKEY_CURRENT_USER\Environment'

function info($m) { Write-Host "dbxignore: $m" }
function warn($m) { Write-Host "dbxignore: $m" -ForegroundColor Yellow }
function die($m)  { throw "dbxignore: error: $m" }

function Show-Usage {
    Write-Host 'dbxignore installer for Windows.'
    Write-Host ''
    Write-Host '  powershell -c "irm https://dbxignore.com/install.ps1 | iex"'
    Write-Host ''
    Write-Host 'Options:  -Uninstall  -NoDaemon  -NoModifyPath  -Help'
    Write-Host 'Env vars: DBXIGNORE_VERSION  DBXIGNORE_INSTALL_ARCHIVE'
    Write-Host '          DBXIGNORE_UNINSTALL  DBXIGNORE_NO_DAEMON  DBXIGNORE_NO_MODIFY_PATH'
}

# The irm | iex one-liner can't pass -switches; honor env-var equivalents.
if ($env:DBXIGNORE_UNINSTALL)      { $Uninstall = $true }
if ($env:DBXIGNORE_NO_DAEMON)      { $NoDaemon = $true }
if ($env:DBXIGNORE_NO_MODIFY_PATH) { $NoModifyPath = $true }

# Nudge the environment so newly-opened shells pick up a PATH change.
function Send-SettingChange {
    $dummy = 'dbxignore-' + [guid]::NewGuid().ToString()
    [Environment]::SetEnvironmentVariable($dummy, '1', 'User')
    [Environment]::SetEnvironmentVariable($dummy, [NullString]::Value, 'User')
}

# Read the user PATH entries from the registry without expanding %VARS%.
function Get-PathEntries {
    (Get-Item -LiteralPath $EnvRegPath).GetValue('Path', '', 'DoNotExpandEnvironmentNames') -split ';' -ne ''
}

function Add-ToPath($dir) {
    $entries = Get-PathEntries
    if ($dir -in $entries) {
        info "$dir is already on PATH."
        return
    }
    if ($NoModifyPath) {
        info "PATH not modified (-NoModifyPath); add $dir to your PATH."
        return
    }
    Set-ItemProperty -Type ExpandString -LiteralPath $EnvRegPath -Name Path -Value ((,$dir + $entries) -join ';')
    Send-SettingChange
    info "added $dir to PATH - open a new terminal for it to take effect."
}

function Remove-FromPath($dir) {
    $entries = Get-PathEntries
    if ($dir -notin $entries) { return }
    Set-ItemProperty -Type ExpandString -LiteralPath $EnvRegPath -Name Path -Value (($entries | Where-Object { $_ -ne $dir }) -join ';')
    Send-SettingChange
    info "removed $dir from PATH."
}

# Inno Setup (dbxignore-setup.exe) leaves unins###.exe in the install dir.
function Test-InnoInstall {
    if (-not (Test-Path -LiteralPath $InstallDir)) { return $false }
    [bool](Get-ChildItem -LiteralPath $InstallDir -Filter 'unins*.exe' -ErrorAction SilentlyContinue)
}

# Remove the install directory, retrying briefly. After 'dbxignore uninstall'
# the daemon process exits asynchronously and can hold its .exe / _internal
# files open for a moment longer.
function Remove-InstallDir {
    if (-not (Test-Path -LiteralPath $InstallDir)) { return }
    $deadline = (Get-Date).AddSeconds(30)
    while ($true) {
        try {
            Remove-Item -LiteralPath $InstallDir -Recurse -Force
            return
        } catch {
            if ((Get-Date) -ge $deadline) {
                die "could not remove $InstallDir - files still in use. Stop the dbxignore daemon and retry."
            }
            Start-Sleep -Milliseconds 500
        }
    }
}

function Get-Archive($dest) {
    if ($env:DBXIGNORE_INSTALL_ARCHIVE) {
        info "installing from local archive: $env:DBXIGNORE_INSTALL_ARCHIVE"
        Copy-Item -LiteralPath $env:DBXIGNORE_INSTALL_ARCHIVE -Destination $dest
        return
    }
    if ($env:DBXIGNORE_VERSION) {
        $url = "https://github.com/$Repo/releases/download/v$($env:DBXIGNORE_VERSION)/$Asset"
    } else {
        $url = "https://github.com/$Repo/releases/latest/download/$Asset"
    }
    info "downloading $url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    # Invoke-WebRequest uses the Windows system proxy by default.
    $req = @{ Uri = $url; OutFile = $dest; UseBasicParsing = $true }
    $oldProgress = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'
    try {
        Invoke-WebRequest @req
    } catch {
        die "download failed: $url"
    } finally {
        $ProgressPreference = $oldProgress
    }
}

function Invoke-Install {
    if (-not [Environment]::Is64BitOperatingSystem) {
        die "dbxignore ships a 64-bit Windows build; this is 32-bit Windows. Install with: pip install dbxignore"
    }
    if (Test-InnoInstall) {
        die "a setup.exe-based dbxignore install is present in $InstallDir. Uninstall it via Settings > Apps, then re-run."
    }

    # Windows cannot overwrite a running daemon's .exe / _internal files, so
    # stop an existing install.ps1 install before replacing it.
    $existing = Join-Path $InstallDir 'dbxignore.exe'
    if (Test-Path -LiteralPath $existing) {
        info "stopping the existing install ($existing uninstall)"
        & $existing uninstall
        if ($LASTEXITCODE -ne 0) { warn "dbxignore uninstall reported an error; continuing" }
    }
    Remove-InstallDir

    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('dbxignore-' + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        $zip = Join-Path $tmp $Asset
        Get-Archive $zip
        info "installing to $InstallDir"
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
        # The Windows zip holds its contents at the archive root (no wrapper
        # directory), so it expands straight into the install directory.
        Expand-Archive -LiteralPath $zip -DestinationPath $InstallDir -Force
    } finally {
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }

    $exe = Join-Path $InstallDir 'dbxignore.exe'
    if (-not (Test-Path -LiteralPath $exe)) { die "extracted bundle has no dbxignore.exe" }

    Add-ToPath $InstallDir

    if (-not $NoDaemon) {
        info "registering the daemon (dbxignore install)"
        & $exe install
        if ($LASTEXITCODE -ne 0) { warn "dbxignore install reported an error; re-run 'dbxignore install' later" }
    }

    Write-Host ''
    info "done - dbxignore is installed."
    if ($NoDaemon) { info "daemon not registered (-NoDaemon); run 'dbxignore install' when ready." }
    info "verify with: dbxignore status"
}

function Invoke-Uninstall {
    # A setup.exe install must be removed by its own uninstaller, or its
    # Apps & Features entry is orphaned. Refuse rather than delete it.
    if (Test-InnoInstall) {
        die "$InstallDir holds a setup.exe-based install; uninstall it via Settings > Apps."
    }
    # Locate the installed executable to deregister the daemon. Assumes a
    # non-tampered install; if the install directory was removed by hand,
    # daemon deregistration is skipped.
    $exe = $null
    $local = Join-Path $InstallDir 'dbxignore.exe'
    if (Test-Path -LiteralPath $local) {
        $exe = $local
    } else {
        $cmd = Get-Command dbxignore -ErrorAction SilentlyContinue
        if ($cmd) { $exe = $cmd.Source }
    }
    if ($exe) {
        info "removing the daemon ($exe uninstall)"
        & $exe uninstall
        if ($LASTEXITCODE -ne 0) { warn "dbxignore uninstall reported an error; continuing" }
    }
    Remove-InstallDir
    Remove-FromPath $InstallDir
    info "uninstalled. Ignore markers and state are untouched (run 'dbxignore uninstall --purge' before uninstalling for a full wipe)."
}

if ($Help) { Show-Usage; exit 0 }

if ($PSVersionTable.PSVersion.Major -lt 5) {
    Write-Host 'dbxignore: error: Windows PowerShell 5.1 or later is required.'
    exit 1
}

try {
    if ($Uninstall) { Invoke-Uninstall } else { Invoke-Install }
} catch {
    Write-Host "$_" -ForegroundColor Red
    exit 1
}

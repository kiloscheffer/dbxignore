#requires -Version 7.0
<#
.SYNOPSIS
    End-to-end smoke test for dbxignore on Windows with a live Dropbox install.

.DESCRIPTION
    Mirrors the Linux + macOS manual-test scripts. Assumes the tester already
    has Dropbox.exe installed and signed in (info.json present, sync folder
    created). Skips the Dropbox-install phase the Linux VPS script handles,
    same convention as manual-test-macos.sh. Runs as a regular user.

    Phase structure mirrors the bash scripts: pre-flight, verify Dropbox,
    install dbxignore, CLI surface, reconcile/apply, extended CLI, daemon
    (Task Scheduler), uninstall, cleanup. Markers are NTFS Alternate Data
    Streams read via `Get-Content -Stream com.dropbox.ignored`.

    Exits non-zero if any check fails. Prints a PASS/FAIL summary.

.PARAMETER InstallSpec
    The dbxignore package spec to install via `uv tool install`. Defaults
    to "dbxignore" (latest from PyPI). Accepts version pins
    ("dbxignore==0.4.1") or git refs ("git+https://github.com/.../@v0.4.1").
    Equivalent to DBXIGNORE_INSTALL_SPEC in the bash scripts.

.EXAMPLE
    .\manual-test-windows.ps1
    .\manual-test-windows.ps1 -InstallSpec "dbxignore==0.4.1"
    .\manual-test-windows.ps1 -InstallSpec "git+https://github.com/kiloscheffer/dbxignore.git@main"

.NOTES
    Run from a non-elevated PowerShell prompt. Dropbox + dbxignore both
    refuse to operate as Administrator (the per-user Task Scheduler entry
    targets the interactive user, not SYSTEM).
#>

[CmdletBinding()]
param(
    [string]$InstallSpec = "dbxignore"
)

# Treat all errors as terminating so simple cmdlet calls fail loudly.
# Equivalent to bash's `set -euo pipefail`. Wrap explicitly with try/catch
# where errors should not halt the test (e.g. assertion-style checks below).
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

$TestSubdir = "dbxignore-test"
$AdsName    = "com.dropbox.ignored"
$DropboxDir = $null   # discovered in phase_verify_dropbox

# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

$Script:PassCount = 0
$Script:FailCount = 0
$Script:FailNames = New-Object System.Collections.Generic.List[string]

$IsAnsi = $Host.UI.SupportsVirtualTerminal
$R = if ($IsAnsi) { "`e[31m" } else { "" }
$G = if ($IsAnsi) { "`e[32m" } else { "" }
$Y = if ($IsAnsi) { "`e[33m" } else { "" }
$B = if ($IsAnsi) { "`e[34m" } else { "" }
$D = if ($IsAnsi) { "`e[2m"  } else { "" }
$X = if ($IsAnsi) { "`e[0m"  } else { "" }

function Write-Phase  { param($Msg) Write-Host ""; Write-Host "${B}=== $Msg ===${X}" }
function Write-Note   { param($Msg) Write-Host "${D}  $Msg${X}" }
function Write-Pass   { param($Msg) $Script:PassCount++; Write-Host "  ${G}PASS${X} $Msg" }
function Write-Fail   { param($Msg) $Script:FailCount++; $Script:FailNames.Add($Msg); Write-Host "  ${R}FAIL${X} $Msg" }
function Stop-Abort   { param($Msg) Write-Host "${R}ABORT:${X} $Msg" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------------------
# NTFS ADS helpers
# ---------------------------------------------------------------------------
# Read/test the dbxignore marker stream on a path. Equivalent to xattr_get
# in the bash scripts. Get-Content -Stream returns the stream content as a
# string array; missing-stream raises ItemNotFoundException (caught here).

function Get-Ads {
    param([string]$Path)
    try {
        $value = Get-Content -Path $Path -Stream $AdsName -ErrorAction Stop -Raw
        if ([string]::IsNullOrEmpty($value)) { return "1" }
        return $value.Trim()
    } catch [System.Management.Automation.ItemNotFoundException] {
        return "missing"
    } catch {
        return "missing"
    }
}

function Assert-AdsSet {
    param([string]$Path, [string]$Name)
    $v = Get-Ads -Path $Path
    if ($v -eq "1") {
        Write-Pass "$Name (ADS=$v)"
    } else {
        Write-Fail "$Name (ADS=$v on $Path)"
    }
}

function Assert-AdsUnset {
    param([string]$Path, [string]$Name)
    $v = Get-Ads -Path $Path
    if ($v -eq "missing") {
        Write-Pass $Name
    } else {
        Write-Fail "$Name (unexpected ADS=$v on $Path)"
    }
}

# Run a command, return $true if its exit code is 0.
# Use for assertion-style checks where we don't want $ErrorActionPreference="Stop"
# to halt on a non-zero exit. The trailing $LASTEXITCODE check handles native
# executables; PowerShell's own cmdlet errors are still caught via try/catch.

function Test-Exit0 {
    param([scriptblock]$Block)
    try {
        & $Block 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# ---------------------------------------------------------------------------
# Phase 0 — pre-flight
# ---------------------------------------------------------------------------

function Test-Preflight {
    Write-Phase "Phase 0 - pre-flight"

    # Refuse to run as Administrator. dbxignore's Task Scheduler entry
    # targets the interactive user; running this script elevated would
    # install the daemon for the wrong principal.
    $isAdmin = ([Security.Principal.WindowsPrincipal]::new(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($isAdmin) {
        Stop-Abort "must run as a regular (non-elevated) user"
    }

    Write-Note ("Windows: " + [System.Environment]::OSVersion.VersionString)
    Write-Note ("PowerShell: " + $PSVersionTable.PSVersion)

    # Python >= 3.11 (matches bash scripts' guard).
    try {
        $pyVer = & python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>&1
    } catch {
        Stop-Abort "python required (install from python.org or via winget)"
    }
    Write-Note "Python $pyVer"
    $pyOk = & python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
    if ($LASTEXITCODE -ne 0) { Stop-Abort "Python >= 3.11 required (got $pyVer)" }

    # uv on PATH; install if missing via the official Astral installer.
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Note "installing uv via astral installer..."
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
    }
    Write-Note ("uv: " + (uv --version))

    # OneDrive-on-AppData detection. Per CLAUDE.md gotcha: if %AppData% is
    # OneDrive-synced with Files-On-Demand, uv's hardlink-from-cache install
    # fails with ERROR_CLOUD_FILE_INCOMPATIBLE_HARDLINKS (os error 396).
    # Force --link-mode=copy unconditionally to dodge the issue — same cost
    # on a non-OneDrive setup, just slightly slower install.
    $env:UV_LINK_MODE = "copy"
    Write-Note "set UV_LINK_MODE=copy (avoids OneDrive hardlink failures; harmless otherwise)"
}

# ---------------------------------------------------------------------------
# Phase 1 — verify Dropbox install (no install/auth — assume already done)
# ---------------------------------------------------------------------------

function Test-VerifyDropbox {
    Write-Phase "Phase 1 - verify Dropbox install"

    # info.json lives under %AppData%\Dropbox on per-user installs (default)
    # and under %LocalAppData%\Dropbox on per-machine "install for all users"
    # installs. roots.discover() checks both; we mirror that.
    $infoCandidates = @(
        Join-Path $env:APPDATA      "Dropbox\info.json"
        Join-Path $env:LOCALAPPDATA "Dropbox\info.json"
    )
    $infoPath = $infoCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $infoPath) {
        Stop-Abort ("Dropbox info.json not found at any of: " + ($infoCandidates -join '; '))
    }
    Write-Pass "Dropbox device linked (info.json at $infoPath)"

    # Parse info.json. Schema mirrors Linux/macOS — `personal` and/or
    # `business` accounts each carry a `path` field. Use the first one.
    $info = Get-Content $infoPath -Raw | ConvertFrom-Json
    $acct = $info.personal
    if (-not $acct) { $acct = $info.business }
    if (-not $acct) {
        $firstKey = ($info | Get-Member -MemberType NoteProperty | Select-Object -First 1).Name
        $acct = $info.$firstKey
    }
    $script:DropboxDir = $acct.path
    Write-Note "Dropbox folder: $script:DropboxDir"

    if (-not (Test-Path $script:DropboxDir)) {
        Stop-Abort "Dropbox folder $script:DropboxDir does not exist"
    }
    Write-Pass "Dropbox folder present at $script:DropboxDir"
}

# ---------------------------------------------------------------------------
# Phase 2 — install dbxignore
# ---------------------------------------------------------------------------

function Test-InstallDbxignore {
    Write-Phase "Phase 2 - install dbxignore (spec: $InstallSpec)"

    $existing = uv tool list 2>$null | Select-String '^dbxignore '
    if ($existing) {
        Write-Note "dbxignore already installed via uv tool - uninstalling first for a clean test"
        uv tool uninstall dbxignore 2>$null | Out-Null
    }

    uv tool install $InstallSpec
    if ($LASTEXITCODE -ne 0) { Stop-Abort "uv tool install failed" }

    if (Get-Command dbxignore  -ErrorAction SilentlyContinue) { Write-Pass "dbxignore on PATH" }  else { Write-Fail "dbxignore on PATH" }
    if (Get-Command dbxignored -ErrorAction SilentlyContinue) { Write-Pass "dbxignored on PATH" } else { Write-Fail "dbxignored on PATH" }
}

# ---------------------------------------------------------------------------
# Phase 3 — CLI surface
# ---------------------------------------------------------------------------

function Test-CliSurface {
    Write-Phase "Phase 3 - CLI surface"

    $verOut = (dbxignore --version 2>&1) -join "`n"
    if ($verOut -match '^dbxignore, version ') { Write-Pass "dbxignore --version" } else { Write-Fail "dbxignore --version (got: $verOut)" }

    $verdOut = (dbxignored --version 2>&1) -join "`n"
    if ($verdOut -match '^dbxignored, version ') { Write-Pass "dbxignored --version" } else { Write-Fail "dbxignored --version (got: $verdOut)" }

    $first = (dbxignored --help 2>&1)[0]
    if ($first -eq "Usage: dbxignored [OPTIONS]") {
        Write-Pass "dbxignored --help has clean Usage line"
    } else {
        Write-Fail "dbxignored --help first line: $first"
    }

    $helpOut = (dbxignore --help 2>&1) -join "`n"
    if ($helpOut -match 'apply') { Write-Pass "dbxignore --help lists subcommands" } else { Write-Fail "dbxignore --help missing subcommands" }

    $statusOut = (dbxignore status 2>&1) -join "`n"
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "dbxignore status (rc=0)"
        ($statusOut -split "`n" | Select-Object -First 3) | ForEach-Object { Write-Note $_ }
    } else {
        Write-Fail "dbxignore status (rc=$LASTEXITCODE)"
        $statusOut -split "`n" | ForEach-Object { Write-Note "    $_" }
    }

    dbxignore list 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Pass "dbxignore list (rc=0)" } else { Write-Fail "dbxignore list" }
}

# ---------------------------------------------------------------------------
# Phase 4 — reconcile (apply)
# ---------------------------------------------------------------------------

function Test-Reconcile {
    Write-Phase "Phase 4 - reconcile / apply"

    $T = Join-Path $script:DropboxDir $TestSubdir
    if (Test-Path $T) { Remove-Item -Path $T -Recurse -Force }
    New-Item -ItemType Directory -Path $T -Force | Out-Null

    # 4a. simple file rule
    Write-Note "4a - simple file rule (*.tmp)"
    Set-Content -Path "$T\.dropboxignore" -Value "*.tmp" -Encoding utf8
    New-Item -ItemType File -Path "$T\foo.tmp" -Force | Out-Null
    New-Item -ItemType File -Path "$T\bar.txt" -Force | Out-Null
    dbxignore apply "$T" --yes 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Pass "apply 4a (rc=0)" } else { Write-Fail "apply 4a" }
    Assert-AdsSet   -Path "$T\foo.tmp" -Name "4a - foo.tmp marked"
    Assert-AdsUnset -Path "$T\bar.txt" -Name "4a - bar.txt unmarked"
    Assert-AdsUnset -Path "$T\.dropboxignore" -Name "4a - .dropboxignore never marked"

    # 4b. dir rule + subtree pruning
    Write-Note "4b - dir rule + subtree pruning (cache/)"
    Set-Content -Path "$T\.dropboxignore" -Value "*.tmp`ncache/" -Encoding utf8
    New-Item -ItemType Directory -Path "$T\cache\sub" -Force | Out-Null
    New-Item -ItemType File -Path "$T\cache\sub\file.txt" -Force | Out-Null
    dbxignore apply "$T" --yes 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Pass "apply 4b" } else { Write-Fail "apply 4b" }
    Assert-AdsSet   -Path "$T\cache" -Name "4b - cache/ marked"
    Assert-AdsUnset -Path "$T\cache\sub\file.txt" -Name "4b - descendant unmarked (subtree pruned)"

    # 4c. rule removal clears markers
    Write-Note "4c - rule removal clears markers"
    Set-Content -Path "$T\.dropboxignore" -Value "cache/" -Encoding utf8
    dbxignore apply "$T" --yes 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Pass "apply 4c" } else { Write-Fail "apply 4c" }
    Assert-AdsUnset -Path "$T\foo.tmp" -Name "4c - foo.tmp cleared after rule removed"
    Assert-AdsSet   -Path "$T\cache"   -Name "4c - cache/ still marked"

    # 4d. dropped negation: dir rule + descendant negation
    Write-Note "4d - dropped negation (dir rule + descendant negation)"
    if (Test-Path $T) { Remove-Item -Path $T -Recurse -Force }
    New-Item -ItemType Directory -Path "$T\build\keep" -Force | Out-Null
    Set-Content -Path "$T\.dropboxignore" -Value "build/`n!build/keep/" -Encoding utf8
    New-Item -ItemType File -Path "$T\build\keep\inside.txt" -Force | Out-Null
    dbxignore apply "$T" --yes 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Pass "apply 4d" } else { Write-Fail "apply 4d" }
    Assert-AdsSet   -Path "$T\build"      -Name "4d - build/ marked (parent dir rule wins)"
    Assert-AdsUnset -Path "$T\build\keep" -Name "4d - descendant not visited (subtree pruned)"
    $explainOut = (dbxignore explain "$T\build\keep" 2>&1) -join "`n"
    if ($explainOut -match '\[dropped\]') {
        Write-Pass "4d - explain annotates dropped negation on build/keep/"
    } else {
        Write-Note "explain output:"
        $explainOut -split "`n" | ForEach-Object { Write-Note "    $_" }
        Write-Fail "4d - explain did not annotate dropped negation"
    }

    # 4e. symlink — Windows attaches ADS to the reparse point, so the
    # symlink is marked silently and successfully (mirrors macOS, NOT
    # Linux). Requires Developer Mode or admin to create symlinks; if the
    # user lacks privilege, fall back to a copy and skip the assertion.
    Write-Note "4e - symlink ADS (Windows attaches ADS to the reparse point)"
    $TS = Join-Path $T "sym"
    New-Item -ItemType Directory -Path $TS -Force | Out-Null
    Set-Content -Path "$TS\.dropboxignore" -Value "*.log" -Encoding utf8
    New-Item -ItemType File -Path "$TS\real.log" -Force | Out-Null

    $symMade = $false
    try {
        New-Item -ItemType SymbolicLink -Path "$TS\link.log" -Target "real.log" -ErrorAction Stop | Out-Null
        $symMade = $true
    } catch {
        Write-Note "skipping symlink creation (requires Developer Mode or admin); 4e link.log assertion skipped"
    }

    dbxignore apply "$T" --yes 2>$null | Tee-Object -FilePath "$env:TEMP\dbxignore-apply.out" | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Pass "apply 4e completes" } else { Write-Fail "apply 4e crashed" }
    Assert-AdsSet -Path "$TS\real.log" -Name "4e - real file marked"
    if ($symMade) {
        Assert-AdsSet -Path "$TS\link.log" -Name "4e - symlink itself marked (ADS on reparse point)"
    }

    # 4f. explain on a marked file returns the matching rule
    Write-Note "4f - explain returns matching rule"
    $explainOut = (dbxignore explain "$TS\real.log" 2>&1) -join "`n"
    if ($explainOut -match '\*\.log') {
        Write-Pass "4f - explain cites *.log"
    } else {
        Write-Fail "4f - explain did not cite *.log"
    }
}

# ---------------------------------------------------------------------------
# Phase 4.5 — extended CLI surface (init, generate, apply variants, clear)
# ---------------------------------------------------------------------------

function Test-ExtendedCli {
    Write-Phase "Phase 4.5 - extended CLI surface (init, generate, apply variants, clear)"

    $T = Join-Path $script:DropboxDir $TestSubdir
    if (Test-Path $T) { Remove-Item -Path $T -Recurse -Force }
    New-Item -ItemType Directory -Path $T -Force | Out-Null

    # 4g — dbxignore init
    Write-Note "4g - dbxignore init"
    dbxignore init "$T" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Pass "4g - init (rc=0)" } else { Write-Fail "4g - init" }
    if ((Test-Path "$T\.dropboxignore") -and ((Get-Content "$T\.dropboxignore" -Raw) -match 'Generated by .dbxignore init.')) {
        Write-Pass "4g - init wrote header"
    } else {
        Write-Fail "4g - init did not write expected header"
    }

    # 4h — dbxignore generate (byte-for-byte)
    Write-Note "4h - dbxignore generate (byte-for-byte)"
    Remove-Item -Path $T -Recurse -Force; New-Item -ItemType Directory -Path $T -Force | Out-Null
    Set-Content -Path "$T\source.gitignore" -Value "node_modules/`n*.log" -Encoding utf8
    dbxignore generate "$T\source.gitignore" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Pass "4h - generate (rc=0)" } else { Write-Fail "4h - generate" }
    if ((Test-Path "$T\.dropboxignore") -and
        ((Get-FileHash "$T\.dropboxignore").Hash -eq (Get-FileHash "$T\source.gitignore").Hash)) {
        Write-Pass "4h - generate produced byte-for-byte copy"
    } else {
        Write-Fail "4h - generate output differs from source"
    }

    # 4i — generate warns on dropped negation (PR #108)
    Write-Note "4i - generate emits stderr warning on dropped negation"
    Remove-Item -Path $T -Recurse -Force; New-Item -ItemType Directory -Path $T -Force | Out-Null
    Set-Content -Path "$T\source.gitignore" -Value "build/`n!build/keep/" -Encoding utf8
    $genErr = "$env:TEMP\dbxignore-gen-warn.err"
    & dbxignore generate "$T\source.gitignore" --force 2> $genErr | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Pass "4i - generate exits 0 even with conflict warning" } else { Write-Fail "4i - generate" }
    $errContent = if (Test-Path $genErr) { Get-Content $genErr -Raw } else { "" }
    if (($errContent -match 'dropped negation') -and ($errContent -match '!build/keep/')) {
        Write-Pass "4i - generate stderr lists dropped negation"
    } else {
        Write-Note "stderr: $errContent"
        Write-Fail "4i - generate stderr did not flag the conflict"
    }
    if ((Get-FileHash "$T\.dropboxignore").Hash -eq (Get-FileHash "$T\source.gitignore").Hash) {
        Write-Pass "4i - file content unchanged despite warning"
    } else {
        Write-Fail "4i - file content differs from source despite warning"
    }

    # 4j — apply --dry-run does not mutate (PR #103)
    Write-Note "4j - apply --dry-run"
    Remove-Item -Path $T -Recurse -Force; New-Item -ItemType Directory -Path $T -Force | Out-Null
    Set-Content -Path "$T\.dropboxignore" -Value "*.tmp" -Encoding utf8
    New-Item -ItemType File -Path "$T\foo.tmp" -Force | Out-Null
    $dryOut = "$env:TEMP\dbxignore-dry.out"
    dbxignore apply "$T" --dry-run *> $dryOut
    if ($LASTEXITCODE -eq 0) { Write-Pass "4j - apply --dry-run (rc=0)" } else { Write-Fail "4j - apply --dry-run" }
    $dryContent = Get-Content $dryOut -Raw
    if (($dryContent -match 'would mark:') -and ($dryContent -match 'would_mark=1')) {
        Write-Pass "4j - dry-run output shape (would mark + would_mark=N)"
    } else {
        Write-Note $dryContent
        Write-Fail "4j - dry-run output unexpected"
    }
    Assert-AdsUnset -Path "$T\foo.tmp" -Name "4j - dry-run did not mutate marker"

    # 4k — apply --yes runs without prompting (PR #107)
    Write-Note "4k - apply --yes skips the prompt"
    $yesOut = "$env:TEMP\dbxignore-yes.out"
    dbxignore apply "$T" --yes *> $yesOut
    if ($LASTEXITCODE -eq 0) { Write-Pass "4k - apply --yes (rc=0)" } else { Write-Fail "4k - apply --yes" }
    Assert-AdsSet -Path "$T\foo.tmp" -Name "4k - apply --yes set marker"
    if ((Get-Content $yesOut -Raw) -notmatch 'Continue\?') {
        Write-Pass "4k - --yes skipped the prompt"
    } else {
        Write-Note (Get-Content $yesOut -Raw)
        Write-Fail "4k - --yes did not skip the prompt"
    }

    # 4l — apply on already-converged state says "Nothing to apply" (PR #107)
    Write-Note "4l - apply on no-op state"
    $noopOut = "$env:TEMP\dbxignore-noop.out"
    # Pipe an empty $null into stdin so any prompt fails fast (we expect no prompt).
    $null | & dbxignore apply "$T" *> $noopOut
    if ($LASTEXITCODE -eq 0) { Write-Pass "4l - apply on no-op state (rc=0)" } else { Write-Fail "4l - apply on no-op state" }
    if ((Get-Content $noopOut -Raw) -match 'Nothing to apply') {
        Write-Pass "4l - emits 'Nothing to apply (rules already in sync)'"
    } else {
        Write-Note (Get-Content $noopOut -Raw)
        Write-Fail "4l - did not emit 'Nothing to apply'"
    }

    # 4m — detector regression: build/* + !build/keep/ no conflict (PR #108)
    Write-Note "4m - detector fix: build/* + !build/keep/ no conflict"
    Remove-Item -Path $T -Recurse -Force
    New-Item -ItemType Directory -Path "$T\build\keep" -Force | Out-Null
    Set-Content -Path "$T\.dropboxignore" -Value "build/*`n!build/keep/" -Encoding utf8
    New-Item -ItemType File -Path "$T\build\keep\inside.txt" -Force | Out-Null
    New-Item -ItemType File -Path "$T\build\foo.tmp" -Force | Out-Null
    dbxignore apply "$T" --yes 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Pass "4m - apply (rc=0)" } else { Write-Fail "4m - apply" }
    Assert-AdsSet   -Path "$T\build\foo.tmp" -Name "4m - build/foo.tmp marked (build/* matches)"
    Assert-AdsUnset -Path "$T\build\keep"    -Name "4m - build/keep NOT marked (negation now effective post-fix)"
    Assert-AdsUnset -Path "$T\build"         -Name "4m - build/ NOT marked (children-only rule)"
    $statusOut = (dbxignore status 2>&1) -join "`n"
    if ($statusOut -match 'rule conflicts \([1-9]') {
        Write-Note ($statusOut -split "`n" | Select-String -SimpleMatch 'rule conflicts' -Context 0,5 | Out-String)
        Write-Fail "4m - status reports >=1 conflicts (regression: detector fix didn't apply)"
    } else {
        Write-Pass "4m - status reports no conflicts (detector fix applied)"
    }

    # 4n — dbxignore clear basic (PR #100); daemon-alive guard tested in phase 5
    Write-Note "4n - dbxignore clear (basic, daemon not alive)"
    $clearOut = "$env:TEMP\dbxignore-clear.out"
    dbxignore clear "$T" --yes *> $clearOut
    if ($LASTEXITCODE -eq 0) { Write-Pass "4n - clear (rc=0)" } else { Write-Fail "4n - clear"; Get-Content $clearOut | ForEach-Object { Write-Note "    $_" } }
    Assert-AdsUnset -Path "$T\build\foo.tmp" -Name "4n - clear removed build/foo.tmp marker"
}

# ---------------------------------------------------------------------------
# Phase 5 — daemon (Task Scheduler + watchdog)
# ---------------------------------------------------------------------------

function _Dump-DaemonDiagnostics {
    param([string]$T)
    $logPath = Join-Path $env:LOCALAPPDATA "dbxignore\daemon.log"
    if (Test-Path $logPath) {
        Write-Note "tail of daemon.log (last 40 lines):"
        Get-Content -Tail 40 $logPath | ForEach-Object { Write-Note "    $_" }
    }
    Write-Note "schtasks state for dbxignore:"
    schtasks /Query /TN dbxignore /FO LIST 2>$null | ForEach-Object { Write-Note "    $_" }
    if (Test-Path $T) {
        Write-Note "test-dir state:"
        Get-ChildItem -Force $T 2>$null | ForEach-Object { Write-Note "    $_" }
    }
}

function Test-Daemon {
    Write-Phase "Phase 5 - daemon (Task Scheduler + watchdog)"

    # Reset to a clean test dir BEFORE installing the daemon, so the
    # daemon's initial cache.load_root() reads a known rule set with no
    # leftover phase-4 conflicts. Same pattern as Linux/macOS.
    $T = Join-Path $script:DropboxDir $TestSubdir
    if (Test-Path $T) { Remove-Item -Path $T -Recurse -Force }
    New-Item -ItemType Directory -Path $T -Force | Out-Null
    Set-Content -Path "$T\.dropboxignore" -Value "*.tmp" -Encoding utf8

    $installOut = "$env:TEMP\dbxignore-install.out"
    dbxignore install *> $installOut
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "dbxignore install (rc=0)"
    } else {
        Write-Fail "dbxignore install"
        Get-Content $installOut | ForEach-Object { Write-Note "    $_" }
        return
    }

    Start-Sleep -Seconds 2

    # Task Scheduler entry should exist + be in Ready or Running state.
    $tasks = schtasks /Query /TN dbxignore /FO CSV /NH 2>$null
    if ($LASTEXITCODE -eq 0 -and $tasks) {
        Write-Pass "Task Scheduler entry registered"
        $state = ($tasks -split ',')[2].Trim('"')
        Write-Note "task state: $state"
    } else {
        Write-Fail "Task Scheduler entry missing"
        return
    }

    # Wait for the daemon to bring its watchdog observer online. Same
    # poll-for-sentinel approach as Linux/macOS.
    $logPath = Join-Path $env:LOCALAPPDATA "dbxignore\daemon.log"
    $dirCount = (Get-ChildItem -Recurse -Directory $script:DropboxDir -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Note "watched subtree: $dirCount dirs in $script:DropboxDir"
    Write-Note "waiting up to 180s for daemon initial sweep + observer ready..."
    $ready = $false
    for ($i = 0; $i -lt 180; $i++) {
        if ((Test-Path $logPath) -and ((Get-Content $logPath -Raw) -match 'watching roots')) {
            $ready = $true; break
        }
        Start-Sleep -Seconds 1
    }
    if ($ready) {
        Write-Pass "daemon observer online (watching roots logged)"
    } else {
        Write-Fail "daemon never logged 'watching roots' within 180s"
        _Dump-DaemonDiagnostics -T $T
        return
    }

    # 5b — watchdog reacts to a new file (created AFTER observer is live)
    Write-Note "5b - watchdog reacts to new file"
    New-Item -ItemType File -Path "$T\watch-me.tmp" -Force | Out-Null
    Start-Sleep -Seconds 6                            # OTHER debounce 500ms + reconcile + slack
    $v = Get-Ads -Path "$T\watch-me.tmp"
    if ($v -eq "1") {
        Write-Pass "5b - daemon marked new *.tmp file via watchdog"
    } else {
        Write-Fail "5b - daemon did not mark new *.tmp file (ADS=$v)"
        _Dump-DaemonDiagnostics -T $T
    }

    # 5c — .dropboxignore reload picks up new rule
    Write-Note "5c - .dropboxignore reload"
    New-Item -ItemType File -Path "$T\freshrule.dat" -Force | Out-Null
    Start-Sleep -Seconds 1
    Set-Content -Path "$T\.dropboxignore" -Value "*.tmp`n*.dat" -Encoding utf8
    Start-Sleep -Seconds 6                            # RULES debounce 100ms + reload + reconcile
    $v = Get-Ads -Path "$T\freshrule.dat"
    if ($v -eq "1") {
        Write-Pass "5c - daemon picked up new rule and marked existing file"
    } else {
        Write-Fail "5c - daemon did not mark file under reloaded rule (ADS=$v)"
        _Dump-DaemonDiagnostics -T $T
    }

    # 5d — DIR_CREATE bypass (item 57) — newly created dir matching a rule
    # should be marked synchronously without waiting the OTHER debounce.
    Write-Note "5d - DIR_CREATE bypass for matched directory (item 57)"
    Set-Content -Path "$T\.dropboxignore" -Value "*.tmp`n*.dat`nbuild_*/" -Encoding utf8
    Start-Sleep -Seconds 6                            # let the rule reload settle
    New-Item -ItemType Directory -Path "$T\build_x" -Force | Out-Null
    Start-Sleep -Seconds 2                            # short wait — bypass shouldn't need OTHER debounce
    $v = Get-Ads -Path "$T\build_x"
    if ($v -eq "1") {
        Write-Pass "5d - DIR_CREATE bypass marked build_x/ within 2s"
    } else {
        Write-Fail "5d - DIR_CREATE bypass did not mark build_x/ within 2s (ADS=$v)"
        _Dump-DaemonDiagnostics -T $T
    }

    # 5e — clear refuses while daemon alive (PR #100); --force overrides.
    Write-Note "5e - clear refuses while daemon alive"
    $clearAliveOut = "$env:TEMP\dbxignore-clear-alive.out"
    dbxignore clear "$T" --yes *> $clearAliveOut
    if ($LASTEXITCODE -eq 0) {
        Write-Fail "5e - clear should have refused while daemon alive"
        Get-Content $clearAliveOut | ForEach-Object { Write-Note "    $_" }
    } else {
        Write-Pass "5e - clear exited non-zero (refused)"
    }
    if ((Get-Content $clearAliveOut -Raw) -match 'daemon is running') {
        Write-Pass "5e - refusal message names the daemon"
    } else {
        Write-Note (Get-Content $clearAliveOut -Raw)
        Write-Fail "5e - refusal message unexpected"
    }
    dbxignore clear "$T" --force --yes 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "5e - clear --force overrides daemon-alive guard"
    } else {
        Write-Fail "5e - clear --force did not override the guard"
    }
}

# ---------------------------------------------------------------------------
# Phase 6 — uninstall
# ---------------------------------------------------------------------------

function Test-Uninstall {
    Write-Phase "Phase 6 - uninstall"

    $T = Join-Path $script:DropboxDir $TestSubdir

    # plain uninstall: scheduled task removed, markers retained
    $uninstOut = "$env:TEMP\dbxignore-uninst.out"
    dbxignore uninstall *> $uninstOut
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "dbxignore uninstall (rc=0)"
    } else {
        Write-Fail "dbxignore uninstall"
        Get-Content $uninstOut | ForEach-Object { Write-Note "    $_" }
    }

    schtasks /Query /TN dbxignore 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Fail "scheduled task still present after uninstall"
    } else {
        Write-Pass "scheduled task removed"
    }

    if (Test-Path "$T\watch-me.tmp") {
        Assert-AdsSet -Path "$T\watch-me.tmp" -Name "uninstall - markers retained on watch-me.tmp"
    }

    # re-install briefly, then --purge
    Write-Note "re-installing for --purge test..."
    dbxignore install 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Stop-Abort "re-install failed" }
    Start-Sleep -Seconds 2

    $purgeOut = "$env:TEMP\dbxignore-purge.out"
    dbxignore uninstall --purge *> $purgeOut
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "dbxignore uninstall --purge (rc=0)"
    } else {
        Write-Fail "dbxignore uninstall --purge"
        Get-Content $purgeOut | ForEach-Object { Write-Note "    $_" }
    }

    if (Test-Path "$T\watch-me.tmp") { Assert-AdsUnset -Path "$T\watch-me.tmp" -Name "purge - watch-me.tmp marker cleared" }
    if (Test-Path "$T\cache")        { Assert-AdsUnset -Path "$T\cache"        -Name "purge - cache/ marker cleared" }

    $stateDir = Join-Path $env:LOCALAPPDATA "dbxignore"
    $stateFile = Join-Path $stateDir "state.json"
    $logFile   = Join-Path $stateDir "daemon.log"
    if (-not (Test-Path $stateFile) -and -not (Test-Path $logFile)) {
        Write-Pass "purge - state.json + daemon.log removed"
    } else {
        Write-Fail "purge - state files remain"
        if (Test-Path $stateDir) {
            Get-ChildItem -Force $stateDir | ForEach-Object { Write-Note "    $_" }
        }
    }
}

# ---------------------------------------------------------------------------
# Phase 7 — final cleanup
# ---------------------------------------------------------------------------

function Test-Cleanup {
    Write-Phase "Phase 7 - cleanup"

    $T = Join-Path $script:DropboxDir $TestSubdir
    if (Test-Path $T) {
        Remove-Item -Path $T -Recurse -Force -ErrorAction SilentlyContinue
        Write-Note "test fixtures removed from Dropbox folder"
    }

    uv tool uninstall dbxignore 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "uv tool uninstall dbxignore"
    } else {
        Write-Fail "uv tool uninstall dbxignore"
    }

    Write-Note "Dropbox itself is left running and signed in (no equivalent of --cleanup-dropbox needed)"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

Test-Preflight
Test-VerifyDropbox
Test-InstallDbxignore
Test-CliSurface
Test-Reconcile
Test-ExtendedCli
Test-Daemon
Test-Uninstall
Test-Cleanup

Write-Phase "Summary"
Write-Host "  ${G}PASS:${X} $Script:PassCount"
Write-Host "  ${R}FAIL:${X} $Script:FailCount"
if ($Script:FailCount -gt 0) {
    foreach ($n in $Script:FailNames) { Write-Host "    ${R}-${X} $n" }
    exit 1
}

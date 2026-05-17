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
    ("dbxignore==<version>"), git refs ("git+https://github.com/.../@<tag>"),
    or a local directory ("." for current checkout). When InstallSpec is
    a local directory, Phase 2 also runs `uv cache clean dbxignore` first
    to force a fresh build (uv's path-keyed sdist cache doesn't
    change-detect against git state).
    Equivalent to DBXIGNORE_INSTALL_SPEC in the bash scripts.

.EXAMPLE
    .\manual-test-windows.ps1
    .\manual-test-windows.ps1 -InstallSpec "dbxignore==<version>"
    .\manual-test-windows.ps1 -InstallSpec "git+https://github.com/kiloscheffer/dbxignore.git@main"

.NOTES
    Run from a non-elevated PowerShell prompt. Dropbox + dbxignore both
    refuse to operate as Administrator (the per-user Task Scheduler entry
    targets the interactive user, not SYSTEM).

    Manual visual verification (dual-binary): after all phases pass,
    manually double-click `dbxignorew.exe` (the GUI helper, NOT the
    `dbxignore.exe` CLI) from File Explorer; expect a MessageBox dialog
    with title "dbxignore" and body containing "dbxignore is a
    command-line tool". Click OK to dismiss. This verifies the GUI-subsystem
    + no-argv -> MessageBox path that no scripted UI test can reliably
    reach. (Double-clicking the console-subsystem `dbxignore.exe` instead
    will briefly flash a console window and exit — that's expected for the
    CLI binary.)

    Manual visual verification for shell-verb GUI dialogs:
    After Phase 7 cleanup, manually:
    1. Re-install dbxignore (`dbxignore install`) so the shell verbs register.
    2. In Explorer, right-click any test file in your Dropbox folder.
    3. Select "Ignore from Dropbox" — expect a MessageBox with a yellow warning
       triangle, body asking to confirm marking the path ignored with a note that
       Dropbox will remove the cloud copy from all linked devices, Yes/No buttons.
    4. Click Yes -> operation runs silently. Verify the file's ADS marker via
       `dir /R` or `dbxignore list`.
    5. Click No on a fresh right-click -> operation cancels silently, no marker.
    6. Right-click a file OUTSIDE the Dropbox folder -> "Ignore from Dropbox"
       should not appear in the menu (verb's AppliesTo filter excludes non-Dropbox
       paths).
    7. Right-click an already-ignored file and select "Restore to Dropbox" ->
       one-click, no confirmation dialog; marker cleared and Dropbox starts
       re-syncing.

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

# Reset-TestDir <path> [<dropboxignore-content>] — removes and recreates a
# test directory; optionally writes a .dropboxignore. The Phase 4.5 cases
# share this setup; helper keeps the per-case body focused on what's
# actually being tested.
#
# DropboxignoreContent is gated by $PSBoundParameters.ContainsKey, NOT a
# null/empty check: PowerShell's [string] coerces $null → "" on parameter
# binding, so a `Reset-TestDir -Path $T` call without the content arg
# would otherwise still write a (zero-byte) .dropboxignore — and `init` /
# `generate` then refuse with "already exists, pass --force." Verify the
# parameter was actually supplied at the call site.

function Reset-TestDir {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [AllowEmptyString()] [string]$DropboxignoreContent
    )
    if (Test-Path $Path) {
        # Dropbox's file watcher routinely holds short-lived handles on
        # newly-created files in the sync tree, and Windows blocks
        # `Remove-Item` on any open handle. The bash scripts don't hit this
        # because POSIX unlink-while-open releases the directory entry
        # immediately. Retry on the two typical Windows lock exceptions
        # (`IOException` for "file in use"; `UnauthorizedAccessException`
        # when Windows reports the same condition as a permission failure).
        # Budget: 20 × 500 ms = 10 s — comfortably above Dropbox's typical
        # scan window (sub-second to ~2 s).
        $attempts = 0
        while ($true) {
            try {
                Remove-Item -Path $Path -Recurse -Force -ErrorAction Stop
                break
            } catch [System.IO.IOException], [System.UnauthorizedAccessException] {
                $attempts++
                if ($attempts -ge 20) {
                    Write-Note "Reset-TestDir: gave up after 10s waiting for handles on $Path to release"
                    throw
                }
                Start-Sleep -Milliseconds 500
            }
        }
    }
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    if ($PSBoundParameters.ContainsKey('DropboxignoreContent')) {
        Set-Content -Path (Join-Path $Path ".dropboxignore") -Value $DropboxignoreContent -Encoding utf8
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

    # OneDrive-on-AppData detection: if %AppData% is OneDrive-synced with
    # Files-On-Demand, uv's hardlink-from-cache install fails with
    # ERROR_CLOUD_FILE_INCOMPATIBLE_HARDLINKS (os error 396).
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
        # Best-effort CLI teardown. `dbxignore uninstall` runs `schtasks
        # /End` + waits for the daemon to exit
        # (install/windows_task.py:uninstall_task), which is the canonical
        # path for releasing the daemon's file lock on dbxignorew.exe.
        # Plain `uninstall` (not `--purge`) preserves any existing ignore
        # markers the tester has set outside this script's test subdir.
        # The schtasks lines below cover the broken-CLI case (interrupted
        # earlier install).
        dbxignore uninstall 2>$null | Out-Null
        schtasks /End /TN dbxignore 2>$null | Out-Null
        schtasks /Delete /TN dbxignore /F 2>$null | Out-Null

        # Independently verify the daemon is dead before proceeding. The
        # in-CLI wait can be defeated by a stale state.json (daemon_pid
        # pointing at a different/non-existent PID makes the poll break
        # immediately while schtasks /End's signal is still propagating to
        # the real daemon). Without this guard, `uv tool uninstall` races
        # the daemon's actual death and silently leaves dbxignorew.exe +
        # pythonw.exe locked in the venv, which then trips the next
        # `uv tool install` with "Invalid environment".
        $deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $deadline) {
            if (-not (Get-Process -Name dbxignore, dbxignorew -ErrorAction SilentlyContinue)) { break }
            Start-Sleep -Milliseconds 500
        }
        $lingering = Get-Process -Name dbxignore, dbxignorew -ErrorAction SilentlyContinue
        if ($lingering) {
            Write-Note "daemon did not exit within 30s; force-killing PID(s) $($lingering.Id -join ', ')"
            $lingering | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
            Start-Sleep -Milliseconds 500
        }

        uv tool uninstall dbxignore 2>$null | Out-Null

        # Force-remove venv residue. With the daemon confirmed dead above,
        # the only remaining lock source is transient (Dropbox/AV indexing);
        # retry briefly. Abort loudly on persistent failure rather than
        # silently corrupting the next `uv tool install`.
        $venvDir = Join-Path $env:APPDATA 'uv\tools\dbxignore'
        if (Test-Path $venvDir) {
            $attempts = 0
            while ((Test-Path $venvDir) -and ($attempts -lt 10)) {
                try {
                    Remove-Item -Recurse -Force $venvDir -ErrorAction Stop
                } catch {
                    Start-Sleep -Milliseconds 500
                    $attempts++
                }
            }
            if (Test-Path $venvDir) {
                Stop-Abort "could not remove $venvDir after 5s of retries; file may still be locked"
            }
        }

        # `uv tool uninstall` removes the trampoline shims at $(uv tool dir
        # --bin), but only if uv still recognizes the tool. Removing the
        # venv out from under uv orphans the shims and the next
        # `uv tool install` errors with "Executables already exist".
        $binDir = (uv tool dir --bin 2>$null).Trim()
        if ($binDir -and (Test-Path $binDir)) {
            foreach ($exe in @('dbxignore.exe', 'dbxignorew.exe')) {
                $shim = Join-Path $binDir $exe
                if (Test-Path $shim) { Remove-Item -Force $shim -ErrorAction SilentlyContinue }
            }
        }
    }

    # Invalidate uv's path-keyed sdist cache for local-source installs.
    # Without this, `uv tool install .` from a directory that's been built
    # before reuses the cached wheel at `sdists-v9/path/<dir-hash>/` — the
    # cache key is the source dir path, not the git SHA, so commits don't
    # invalidate it. Excludes PyPI names and git URLs (which aren't existing
    # directories).
    if (Test-Path -PathType Container $InstallSpec -ErrorAction SilentlyContinue) {
        Write-Note "local-source InstallSpec - cleaning uv cache for dbxignore"
        uv cache clean dbxignore 2>$null | Out-Null
    }

    uv tool install $InstallSpec
    if ($LASTEXITCODE -ne 0) { Stop-Abort "uv tool install failed" }

    if (Get-Command dbxignore  -ErrorAction SilentlyContinue) { Write-Pass "dbxignore on PATH" }  else { Write-Fail "dbxignore on PATH" }
}

# ---------------------------------------------------------------------------
# Phase 3 — CLI surface
# ---------------------------------------------------------------------------

function Test-CliSurface {
    Write-Phase "Phase 3 - CLI surface"

    $verOut = (dbxignore --version 2>&1) -join "`n"
    if ($verOut -match '^dbxignore, version ') { Write-Pass "dbxignore --version" } else { Write-Fail "dbxignore --version (got: $verOut)" }

    # Strip ANSI escapes — rich-click decorates the Usage line; PS 7+'s
    # `e regex literal handles it. Mirror the substring shape from
    # tests/test_cli_entrypoints.py: "daemon" + "[OPTIONS]" present,
    # "COMMAND" / "[ARGS]" absent (a regression that accidentally adds
    # subcommands to the daemon subcommand surfaces here).
    # The daemon is reached via `dbxignore daemon`.
    $rawHelp   = (dbxignore daemon --help 2>&1) -join "`n"
    $plainHelp = $rawHelp -replace "`e\[[0-9;]*m", ""
    $usageLine = ($plainHelp -split "`r?`n" | Where-Object { $_ -match 'Usage:' } | Select-Object -First 1)
    if (($usageLine -match 'daemon') -and
        ($usageLine -match '\[OPTIONS\]') -and
        ($usageLine -notmatch 'COMMAND') -and
        ($usageLine -notmatch '\[ARGS\]')) {
        Write-Pass "dbxignore daemon --help has clean Usage line"
    } else {
        Write-Fail "dbxignore daemon --help Usage line: $usageLine"
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
    Reset-TestDir -Path $T -DropboxignoreContent "*.tmp"

    # 4a. simple file rule
    Write-Note "4a - simple file rule (*.tmp)"
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
    Reset-TestDir -Path $T

    # 4g — dbxignore init
    Write-Note "4g - dbxignore init"
    dbxignore init "$T" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Pass "4g - init (rc=0)" } else { Write-Fail "4g - init" }
    if ((Test-Path "$T\.dropboxignore") -and ((Get-Content "$T\.dropboxignore" -Raw) -match 'Generated by .dbxignore init.')) {
        Write-Pass "4g - init wrote header"
    } else {
        Write-Fail "4g - init did not write expected header"
    }

    # 4g — init refuses an unwritable target dir with exit 2 + a clean
    # "cannot write" message rather than an unhandled OSError traceback.
    Write-Note "4g - dbxignore init on an unwritable directory"
    $roDir = Join-Path $T "init-readonly"
    New-Item -ItemType Directory -Force -Path $roDir | Out-Null
    # Deny Write Data + Append Data so the .dropboxignore write inside fails;
    # Read/Execute stay intact so init's detection walk still runs normally.
    icacls $roDir /deny "${env:USERNAME}:(WD,AD)" *> $null
    $initRoErrFile = Join-Path $env:TEMP "dbx-init-ro.err"
    & dbxignore init $roDir *> $initRoErrFile
    $initRoExitCode = $LASTEXITCODE
    # Drop the deny ACE so Reset-TestDir can clean $roDir up later.
    icacls $roDir /remove:d "${env:USERNAME}" *> $null
    if ($initRoExitCode -eq 2) {
        Write-Pass "4g - init exits 2 on an unwritable dir"
    } else {
        Write-Fail "4g - init exited $initRoExitCode instead of 2"
    }
    $initRoErr = if (Test-Path $initRoErrFile) { Get-Content $initRoErrFile -Raw } else { "" }
    if ($initRoErr -match 'cannot write') {
        Write-Pass "4g - init stderr says 'cannot write'"
    } else {
        Write-Note "init stderr: $initRoErr"
        Write-Fail "4g - init stderr missing 'cannot write'"
    }

    # 4h — dbxignore generate (byte-for-byte)
    # `-NoNewline` + explicit trailing `` `n `` writes pure LF (matches the
    # bash version's `printf 'X\n' >` output). Without `-NoNewline`,
    # Set-Content appends a trailing CRLF that breaks the byte-for-byte
    # assertion even with the cli.generate LF-pin in place.
    Write-Note "4h - dbxignore generate (byte-for-byte)"
    Reset-TestDir -Path $T
    Set-Content -Path "$T\source.gitignore" -Value "node_modules/`n*.log`n" -Encoding utf8 -NoNewline
    dbxignore generate "$T\source.gitignore" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Pass "4h - generate (rc=0)" } else { Write-Fail "4h - generate" }
    if ((Test-Path "$T\.dropboxignore") -and
        ((Get-FileHash "$T\.dropboxignore").Hash -eq (Get-FileHash "$T\source.gitignore").Hash)) {
        Write-Pass "4h - generate produced byte-for-byte copy"
    } else {
        Write-Fail "4h - generate output differs from source"
    }

    # 4i — generate warns on dropped negation
    # `-NoNewline` + explicit trailing `` `n `` writes pure LF (matches the bash
    # version's `printf 'X\n' >` output). The LF-pin in cli.generate preserves
    # the byte-for-byte invariant despite the conflict warning.
    Write-Note "4i - generate emits stderr warning on dropped negation"
    Reset-TestDir -Path $T
    Set-Content -Path "$T\source.gitignore" -Value "build/`n!build/keep/`n" -Encoding utf8 -NoNewline
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

    # 4j — apply --dry-run does not mutate
    Write-Note "4j - apply --dry-run"
    Reset-TestDir -Path $T -DropboxignoreContent "*.tmp"
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

    # 4k — apply --yes runs without prompting
    Write-Note "4k - apply --yes skips the prompt"
    $yesOut = "$env:TEMP\dbxignore-yes.out"
    dbxignore apply "$T" --yes *> $yesOut
    if ($LASTEXITCODE -eq 0) { Write-Pass "4k - apply --yes (rc=0)" } else { Write-Fail "4k - apply --yes" }
    Assert-AdsSet -Path "$T\foo.tmp" -Name "4k - apply --yes set marker"
    $yesContent = Get-Content $yesOut -Raw
    if ($yesContent -notmatch 'Continue\?') {
        Write-Pass "4k - --yes skipped the prompt"
    } else {
        Write-Note $yesContent
        Write-Fail "4k - --yes did not skip the prompt"
    }

    # 4l — apply on already-converged state says "Nothing to apply"
    Write-Note "4l - apply on no-op state"
    $noopOut = "$env:TEMP\dbxignore-noop.out"
    # PowerShell can't redirect stdin from a file like bash's `< /dev/null`,
    # and `$null | <native>` doesn't actually close stdin (PS pipes one $null
    # object). Workaround: feed an empty string. If a regression makes the
    # prompt fire, Click's confirm() consumes the trailing newline as the
    # default ('n') → "Aborted" lands in stdout — we detect that explicitly
    # below so the test fails with a clear reason rather than hanging.
    '' | & dbxignore apply "$T" *> $noopOut
    if ($LASTEXITCODE -eq 0) { Write-Pass "4l - apply on no-op state (rc=0)" } else { Write-Fail "4l - apply on no-op state" }
    $noopContent = Get-Content $noopOut -Raw
    if ($noopContent -match 'Aborted') {
        Write-Note $noopContent
        Write-Fail "4l - prompt fired unexpectedly (got 'Aborted' from default-False prompt)"
    } elseif ($noopContent -match 'Nothing to apply') {
        Write-Pass "4l - emits 'Nothing to apply (rules already in sync)'"
    } else {
        Write-Note $noopContent
        Write-Fail "4l - did not emit 'Nothing to apply'"
    }

    # 4m — conflict detector: case4m_target/* + !case4m_target/keep/ no conflict
    # Uses `case4m_target` instead of the generic `build` because testers who
    # have run `dbxignore init` at their Dropbox root carry a `build/` rule
    # at the ancestor `.dropboxignore`. Dropbox's directory-inheritance
    # semantic would then mark the test's `build/` via the ancestor rule and
    # mask the local `!build/keep/` negation — a real but unrelated effect
    # that the case 4m assertion would mis-attribute as a detector failure.
    Write-Note "4m - conflict detector: case4m_target/* + !case4m_target/keep/ no conflict"
    Remove-Item -Path $T -Recurse -Force
    New-Item -ItemType Directory -Path "$T\case4m_target\keep" -Force | Out-Null
    Set-Content -Path "$T\.dropboxignore" -Value "case4m_target/*`n!case4m_target/keep/" -Encoding utf8
    New-Item -ItemType File -Path "$T\case4m_target\keep\inside.txt" -Force | Out-Null
    New-Item -ItemType File -Path "$T\case4m_target\foo.tmp" -Force | Out-Null
    dbxignore apply "$T" --yes 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Pass "4m - apply (rc=0)" } else { Write-Fail "4m - apply" }
    Assert-AdsSet   -Path "$T\case4m_target\foo.tmp" -Name "4m - case4m_target/foo.tmp marked (case4m_target/* matches)"
    Assert-AdsUnset -Path "$T\case4m_target\keep"    -Name "4m - case4m_target/keep NOT marked (negation effective)"
    Assert-AdsUnset -Path "$T\case4m_target"         -Name "4m - case4m_target/ NOT marked (children-only rule)"
    $statusOut = (dbxignore status 2>&1) -join "`n"
    if ($statusOut -match 'rule conflicts \([1-9]') {
        Write-Note ($statusOut -split "`n" | Select-String -SimpleMatch 'rule conflicts' -Context 0,5 | Out-String)
        Write-Fail "4m - status reports >=1 conflicts (detector should report none)"
    } else {
        Write-Pass "4m - status reports no conflicts"
    }

    # 4n — dbxignore clear basic; daemon-alive guard tested in phase 5
    Write-Note "4n - dbxignore clear (basic, daemon not alive)"
    $clearOut = "$env:TEMP\dbxignore-clear.out"
    dbxignore clear "$T" --yes *> $clearOut
    if ($LASTEXITCODE -eq 0) { Write-Pass "4n - clear (rc=0)" } else { Write-Fail "4n - clear"; Get-Content $clearOut | ForEach-Object { Write-Note "    $_" } }
    Assert-AdsUnset -Path "$T\case4m_target\foo.tmp" -Name "4n - clear removed case4m_target/foo.tmp marker"

    # 4o — dbxignore ignore <path> happy path
    Write-Note "4o - dbxignore ignore (basic)"
    $target4o = Join-Path $script:DropboxDir "dbxignore_test_4o"
    if (Test-Path $target4o) { Remove-Item -Path $target4o -Recurse -Force }
    New-Item -ItemType Directory -Path $target4o -Force | Out-Null
    $ignoreOut = "$env:TEMP\dbxignore-ignore.out"
    dbxignore ignore $target4o --yes *> $ignoreOut
    if ($LASTEXITCODE -eq 0) { Write-Pass "4o - ignore (rc=0)" } else { Write-Fail "4o - ignore"; Get-Content $ignoreOut | ForEach-Object { Write-Note "    $_" } }
    $rootIgnoreFile = Join-Path $script:DropboxDir ".dropboxignore"
    if ((Test-Path $rootIgnoreFile) -and ((Get-Content $rootIgnoreFile -Raw) -match 'dbxignore_test_4o/')) {
        Write-Pass "4o - rule appended to $rootIgnoreFile"
    } else {
        Write-Fail "4o - rule not appended to $rootIgnoreFile"
    }
    Assert-AdsSet -Path $target4o -Name "4o - marker set on target"

    # 4p — dbxignore unignore <path> happy path
    Write-Note "4p - dbxignore unignore (basic)"
    $unignoreOut = "$env:TEMP\dbxignore-unignore.out"
    dbxignore unignore $target4o --yes *> $unignoreOut
    if ($LASTEXITCODE -eq 0) { Write-Pass "4p - unignore (rc=0)" } else { Write-Fail "4p - unignore"; Get-Content $unignoreOut | ForEach-Object { Write-Note "    $_" } }
    if ((Test-Path $rootIgnoreFile) -and ((Get-Content $rootIgnoreFile -Raw) -match 'dbxignore_test_4o/')) {
        Write-Fail "4p - rule still present in $rootIgnoreFile"
    } else {
        Write-Pass "4p - rule removed from $rootIgnoreFile"
    }
    Assert-AdsUnset -Path $target4o -Name "4p - marker cleared on target"
    Remove-Item -Path $target4o -Recurse -Force

    # 4q — dbxignore unignore wildcard collision
    Write-Note "4q - dbxignore unignore refuses wildcard blocker"
    $target4q = Join-Path $script:DropboxDir "dbxignore_test_4q"
    if (Test-Path $target4q) { Remove-Item -Path $target4q -Recurse -Force }
    New-Item -ItemType Directory -Path $target4q -Force | Out-Null
    Add-Content -Path $rootIgnoreFile -Value "dbxignore_test_4q/" -Encoding utf8
    Add-Content -Path $rootIgnoreFile -Value "**/dbxignore_test_4q/" -Encoding utf8
    $collision4qOut = "$env:TEMP\dbxignore-4q.out"
    dbxignore unignore $target4q --yes *> $collision4qOut
    if ($LASTEXITCODE -ne 0) {
        Write-Pass "4q - unignore refused with wildcard blocker (rc=$LASTEXITCODE)"
    } else {
        Write-Fail "4q - unignore should have refused (wildcard blocker present)"
    }
    # Cleanup: remove the two test rules from .dropboxignore.
    $cleaned = Get-Content $rootIgnoreFile | Where-Object { $_ -notmatch 'dbxignore_test_4q/' }
    Set-Content -Path $rootIgnoreFile -Value $cleaned -Encoding utf8
    # Clean up the root .dropboxignore if Phase 4.5 was its only contents.
    if (Test-Path $rootIgnoreFile) {
        $nonTrivialLines = Get-Content $rootIgnoreFile | Where-Object { $_ -and $_ -notmatch '^\s*#' -and $_ -notmatch '^\s*$' }
        if (-not $nonTrivialLines) {
            Remove-Item $rootIgnoreFile -Force
        }
    }
    Remove-Item -Path $target4q -Recurse -Force

    # 4r — clear/list exit 2 on nonexistent path
    Write-Note "4r - clear/list error on nonexistent path"
    $nonexist = Join-Path $script:DropboxDir "dbxignore-test-nonexistent-$PID"

    # clear on nonexistent path should exit 2
    $clearErrFile = "$env:TEMP\dbxignore-4r-clear.err"
    & dbxignore clear $nonexist --yes *> $clearErrFile
    $clearExitCode = $LASTEXITCODE
    if ($clearExitCode -eq 2) {
        Write-Pass "4r - clear exits 2 on nonexistent path"
    } else {
        Write-Fail "4r - clear exited $clearExitCode instead of 2"
    }
    $clearErr = if (Test-Path $clearErrFile) { Get-Content $clearErrFile -Raw } else { "" }
    if ($clearErr -match 'does not exist') {
        Write-Pass "4r - clear stderr says 'does not exist'"
    } else {
        Write-Note "clear stderr: $clearErr"
        Write-Fail "4r - clear stderr missing 'does not exist'"
    }

    # list on nonexistent path should exit 2
    $listErrFile = "$env:TEMP\dbxignore-4r-list.err"
    & dbxignore list $nonexist *> $listErrFile
    $listExitCode = $LASTEXITCODE
    if ($listExitCode -eq 2) {
        Write-Pass "4r - list exits 2 on nonexistent path"
    } else {
        Write-Fail "4r - list exited $listExitCode instead of 2"
    }
    $listErr = if (Test-Path $listErrFile) { Get-Content $listErrFile -Raw } else { "" }
    if ($listErr -match 'does not exist') {
        Write-Pass "4r - list stderr says 'does not exist'"
    } else {
        Write-Note "list stderr: $listErr"
        Write-Fail "4r - list stderr missing 'does not exist'"
    }

    # 4s — clear fail-closed on unreadable state.json
    # state.json exists but `state.read()` returns None (deny ACL → PermissionError
    # → _read_at returns None). cli.clear refuses to proceed because daemon
    # liveness is unknown; --force overrides.
    #
    # The destructive setup (deny ACE on state.json) is wrapped in try/finally so an
    # `$ErrorActionPreference = 'Stop'` abort during the test still restores
    # state.json via Move-Item (or Remove-Item if the test created it). Without
    # the finally, an aborted run could leave the user's state.json with a deny
    # ACE in effect, blocking later dbxignore commands until manual repair.
    Write-Note "4s - clear fail-closed on unreadable state.json"
    $stateDir4s = Join-Path $env:LOCALAPPDATA "dbxignore"
    New-Item -ItemType Directory -Force -Path $stateDir4s | Out-Null
    $stateJson4s = Join-Path $stateDir4s "state.json"
    $stateJson4sExisted = Test-Path $stateJson4s
    $stateJson4sBackup = Join-Path $env:TEMP "dbx-4s-state-backup.json"
    # Back up the real state.json BEFORE any destructive op. Copy-Item itself
    # is non-destructive (read-only on $stateJson4s), so any failure here
    # aborts the script with the user's state.json untouched.
    if ($stateJson4sExisted) {
        Copy-Item $stateJson4s $stateJson4sBackup -Force
    }

    try {
        # All destructive ops live in the try block so the finally unconditionally
        # restores state.json regardless of where the abort fires — Set-Content,
        # tree setup, dbxignore apply, the deny ACE, or any of the test commands.
        Set-Content -Path $stateJson4s -Value "{}" -Encoding utf8 -NoNewline

        Remove-Item -Recurse -Force $T -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force -Path $T | Out-Null
        Set-Content -Path "$T\.dropboxignore" -Value "*.tmp" -Encoding utf8
        New-Item -ItemType File -Force -Path "$T\foo.tmp" | Out-Null
        & dbxignore apply $T --yes *> $null

        # Deny only Read Data (RD) — NOT the generic R, which includes RA
        # (Read Attributes). `state.default_path().exists()` in cli.clear
        # queries file attributes; if RA were denied, exists() could return
        # False, the fail-closed arm would skip, and 4s would fail for the
        # wrong reason. RD blocks `Path.read_bytes()` (PermissionError) but
        # leaves attribute reads intact.
        icacls $stateJson4s /deny "${env:USERNAME}:(RD)" *> $null

        $clear4sErrFile = Join-Path $env:TEMP "dbx-4s-clear.err"
        & dbxignore clear $T --yes *> $clear4sErrFile
        $clear4sExitCode = $LASTEXITCODE
        if ($clear4sExitCode -eq 2) {
            Write-Pass "4s - clear exits 2 on unreadable state.json"
        } else {
            Write-Fail "4s - clear exited $clear4sExitCode instead of 2"
        }
        $clear4sErr = if (Test-Path $clear4sErrFile) { Get-Content $clear4sErrFile -Raw } else { "" }
        if ($clear4sErr -match 'unreadable') {
            Write-Pass "4s - clear stderr names 'unreadable' state file"
        } else {
            Write-Note "clear stderr: $clear4sErr"
            Write-Fail "4s - clear stderr missing 'unreadable'"
        }
        Assert-AdsSet -Path "$T\foo.tmp" -Name "4s - clear did not clear marker (refused)"

        # --force overrides the unreadable-state guard.
        & dbxignore clear $T --yes --force *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Pass "4s - clear --force overrides the unreadable-state guard"
        } else {
            Write-Fail "4s - clear --force should succeed"
        }
        Assert-AdsUnset -Path "$T\foo.tmp" -Name "4s - clear --force cleared the marker"
    } finally {
        # Restore state.json unconditionally — Move-Item replaces both content
        # and ACL, so the deny ACE goes with the test file regardless of how the
        # try block exited. No explicit `icacls /remove:d` needed (and it would
        # be over-broad — removes all deny ACEs for the user, not just (RD)).
        if ($stateJson4sExisted) {
            Move-Item $stateJson4sBackup $stateJson4s -Force
        } else {
            Remove-Item $stateJson4s -Force -ErrorAction SilentlyContinue
        }
    }

    # 4t — path-taking verbs refuse `..` after a symlinked component
    # Lexical normalization of `link\..` differs from filesystem-true resolution;
    # `_normalize_under_root` rejects the path up-front. One verb (explain) suffices —
    # the guard lives in the shared validator and unit tests cover all 5 verbs.
    Write-Note "4t - explain refuses '..-after-symlink' path"
    Remove-Item -Recurse -Force $T -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $T | Out-Null
    $linkTarget4t = Join-Path $T "target-dir"
    New-Item -ItemType Directory -Force -Path $linkTarget4t | Out-Null
    New-Item -ItemType File -Force -Path (Join-Path $linkTarget4t "file.txt") | Out-Null
    $link4t = Join-Path $T "link"
    try {
        New-Item -ItemType SymbolicLink -Path $link4t -Target $linkTarget4t -ErrorAction Stop | Out-Null
        $link4tCreated = $true
    } catch {
        $link4tCreated = $false
        Write-Note "skipping 4t (requires Developer Mode or admin to create symlinks)"
    }
    if ($link4tCreated) {
        $linkDotdotArg = Join-Path $link4t "..\file.txt"
        $explain4tErrFile = Join-Path $env:TEMP "dbx-4t-explain.err"
        & dbxignore explain $linkDotdotArg *> $explain4tErrFile
        $explain4tExitCode = $LASTEXITCODE
        if ($explain4tExitCode -eq 2) {
            Write-Pass "4t - explain exits 2 on '..-after-symlink' path"
        } else {
            Write-Fail "4t - explain exited $explain4tExitCode instead of 2"
        }
        $explain4tErr = if (Test-Path $explain4tErrFile) { Get-Content $explain4tErrFile -Raw } else { "" }
        if ($explain4tErr -match 'symlinked component') {
            Write-Pass "4t - explain stderr names 'symlinked component'"
        } else {
            Write-Note "explain stderr: $explain4tErr"
            Write-Fail "4t - explain stderr missing 'symlinked component'"
        }
    }

    # 4u — dbxignore --help prints synchronously to PowerShell
    # The console-subsystem CLI binary lets PowerShell wait synchronously —
    # no pipe or capture trick needed to see the output.
    $helpOut = (dbxignore --help 2>&1) -join "`n"
    if ($helpOut -match "Usage:") {
        Write-Pass "4u - dbxignore --help prints synchronously (Usage: line visible)"
    } else {
        Write-Fail "4u - dbxignore --help did not surface Usage: line in plain invocation"
    }

    # 4v - clear/list exit 2 on injected marker-read failure
    # DBXIGNORE_TEST_FAIL_MARKER_READ makes markers.is_ignored raise OSError
    # inside _walk_marked_paths, exercising the scan_errors exit-2 path that
    # unit tests pin but a healthy filesystem can't otherwise trigger. PowerShell
    # has no inline env-var prefix, so the var is set then removed around each
    # invocation. The injected runs mutate nothing (clear refuses once the scan
    # fails), so recovery is a plain clear.
    Write-Note "4v - clear/list exit 2 on injected marker-read failure"
    Remove-Item -Recurse -Force $T -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $T | Out-Null
    Set-Content -Path (Join-Path $T ".dropboxignore") -Value "*.tmp" -Encoding utf8 -NoNewline
    New-Item -ItemType File -Force -Path (Join-Path $T "foo.tmp") | Out-Null
    dbxignore apply $T --yes *> $null

    $clear4vErr = Join-Path $env:TEMP "dbx-4v-clear.err"
    $env:DBXIGNORE_TEST_FAIL_MARKER_READ = "1"
    & dbxignore clear $T --yes *> $clear4vErr
    $clear4vRc = $LASTEXITCODE
    Remove-Item Env:\DBXIGNORE_TEST_FAIL_MARKER_READ
    if ($clear4vRc -eq 2) {
        Write-Pass "4v - clear exits 2 on injected marker-read failure"
    } else {
        Write-Fail "4v - clear exited $clear4vRc instead of 2"
        if (Test-Path $clear4vErr) { Get-Content $clear4vErr | ForEach-Object { Write-Note "    $_" } }
    }
    $clear4vText = if (Test-Path $clear4vErr) { Get-Content $clear4vErr -Raw } else { "" }
    if ($clear4vText -match 'scan error') {
        Write-Pass "4v - clear stderr reports scan errors"
    } else {
        Write-Fail "4v - clear stderr missing 'scan error'"
    }

    $list4vErr = Join-Path $env:TEMP "dbx-4v-list.err"
    $env:DBXIGNORE_TEST_FAIL_MARKER_READ = "1"
    & dbxignore list $T *> $list4vErr
    $list4vRc = $LASTEXITCODE
    Remove-Item Env:\DBXIGNORE_TEST_FAIL_MARKER_READ
    if ($list4vRc -eq 2) {
        Write-Pass "4v - list exits 2 on injected marker-read failure"
    } else {
        Write-Fail "4v - list exited $list4vRc instead of 2"
        if (Test-Path $list4vErr) { Get-Content $list4vErr | ForEach-Object { Write-Note "    $_" } }
    }
    $list4vText = if (Test-Path $list4vErr) { Get-Content $list4vErr -Raw } else { "" }
    if ($list4vText -match 'scan error') {
        Write-Pass "4v - list stderr reports scan errors"
    } else {
        Write-Fail "4v - list stderr missing 'scan error'"
    }

    # Recovery: clear the marker without the fail point so later phases start clean.
    dbxignore clear $T --yes *> $null
    Remove-Item -Recurse -Force $T -ErrorAction SilentlyContinue
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

# Phase 5g verb-visibility probe helper. Returns a PSCustomObject with
# `Present` (bool — "Ignore from Dropbox" appears in Verbs()) and
# `VerbList` (the stripped verb-name array, for diagnostic logging on
# mismatch). Returns $null AND emits Write-Fail when Shell.Application
# can't see the folder or probe file (a COM-side failure, not a
# verb-state observation). Factored out of 5g.5 / 5g.6 probe duplication.
function Get-Verb5gState {
    param(
        [Parameter(Mandatory)][string]$FolderPath,
        [Parameter(Mandatory)][string]$ProbeFileName,
        [Parameter(Mandatory)][string]$ContextLabel
    )
    $shell = New-Object -ComObject Shell.Application
    $folder = $shell.NameSpace($FolderPath)
    if ($null -eq $folder) {
        Write-Fail "5g - Shell.Application.NameSpace returned null for $ContextLabel"
        return $null
    }
    $item = $folder.ParseName($ProbeFileName)
    if ($null -eq $item) {
        Write-Fail "5g - Shell.Application.ParseName returned null for $ContextLabel"
        return $null
    }
    # Verbs().Name surfaces the MUIVerb display string; Shell may inject
    # an `&` accelerator marker, so strip before comparing.
    $verbNames = @($item.Verbs() | ForEach-Object { ($_.Name -replace '&', '') })
    [PSCustomObject]@{
        Present  = $verbNames -contains "Ignore from Dropbox"
        VerbList = $verbNames
    }
}


function Test-Daemon {
    Write-Phase "Phase 5 - daemon (Task Scheduler + watchdog)"

    # 5-pre — uv tool venv ships a GUI-subsystem pythonw.exe
    # The daemon's Task Scheduler entry is dbxignorew.exe, a GUI-script
    # trampoline that re-execs the sibling pythonw.exe. The chain is only
    # windowless when that pythonw.exe is itself GUI-subsystem. uv *project*
    # venvs (`uv sync` / `uv run`) ship a console-subsystem pythonw.exe — for
    # those, detect_invocation rejects dbxignorew.exe and emits the honest
    # fallback WARNING (covered by the _common unit tests; forcing it here
    # would also need dbxignorew.exe removed from the PATH bin dir). A
    # `uv tool install` venv — what this script exercises — ships a genuine
    # GUI-subsystem pythonw.exe; verify that here so a future uv layout
    # change that silently reintroduces the console window is caught.
    #
    # Static PE-header check, so no file manipulation and no restore needed.
    # The uv tool dir respects UV_TOOL_DIR and varies across uv versions,
    # so derive it from `uv tool dir`.
    $toolDir = $null
    try {
        $toolDir = (uv tool dir 2>$null | Out-String).Trim()
    } catch {
        # uv tool dir failed; skip the test.
    }
    if ([string]::IsNullOrWhiteSpace($toolDir)) {
        Write-Note "5-pre - uv tool dir failed or empty; skipping pythonw.exe subsystem check."
    } else {
        $toolPythonw = Join-Path $toolDir "dbxignore\Scripts\pythonw.exe"
        if (-not (Test-Path $toolPythonw)) {
            Write-Note "5-pre - pythonw.exe missing from tool venv at $toolPythonw (unexpected; uv tool install normally creates it). Skipping subsystem check."
        } else {
            # PE Subsystem field: MZ -> e_lfanew@0x3C -> PE\0\0 -> +0x5C. 2=GUI, 3=console.
            $bytes = [System.IO.File]::ReadAllBytes($toolPythonw)
            $peOff = [BitConverter]::ToInt32($bytes, 0x3C)
            $subsystem = [BitConverter]::ToUInt16($bytes, $peOff + 0x5C)
            if ($subsystem -eq 2) {
                Write-Pass "5-pre - tool venv pythonw.exe is GUI-subsystem (dbxignorew.exe chain is windowless)"
            } else {
                Write-Fail "5-pre - tool venv pythonw.exe subsystem=$subsystem (expected 2=GUI); daemon would show a console window"
            }
        }
    }

    # Reset to a clean test dir BEFORE installing the daemon, so the
    # daemon's initial cache.load_root() reads a known rule set with no
    # leftover phase-4 conflicts. Same pattern as Linux/macOS.
    $T = Join-Path $script:DropboxDir $TestSubdir
    Reset-TestDir -Path $T -DropboxignoreContent "*.tmp"

    # Slow-sweep determinism. Seed a 15s pad so 5a's 5-iteration state=starting
    # poll deterministically catches the transient state and
    # 5f's 180s poll deterministically observes the transition to running,
    # regardless of the watched-tree size. The daemon logs WARNING when it
    # honors this; cleanup at the end of phase 5 removes it before phase 6.
    $slowSweepDir = Join-Path $env:LOCALAPPDATA "dbxignore"
    $slowSweepMarker = Join-Path $slowSweepDir "_test_slow_sweep"
    if (-not (Test-Path $slowSweepDir)) {
        New-Item -ItemType Directory -Path $slowSweepDir -Force | Out-Null
    }
    Set-Content -Path $slowSweepMarker -Value "15" -Encoding ascii -NoNewline
    Write-Note "5 - slow-sweep marker seeded: 15s pad on initial sweep"

    $installOut = "$env:TEMP\dbxignore-install.out"
    dbxignore install *> $installOut
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "dbxignore install (rc=0)"
    } else {
        Write-Fail "dbxignore install"
        Get-Content $installOut | ForEach-Object { Write-Note "    $_" }
        return
    }

    # install verbosity defaults - default WARNING quiets install-backend
    # INFO chatter; the click.echo summary line still surfaces.
    $installContents = Get-Content $installOut -Raw
    if ($installContents -match "Installed dbxignore daemon service") {
        Write-Pass "install - click.echo summary present"
    } else {
        Write-Fail "install - click.echo summary missing"
    }
    if ($installContents -notmatch "(?m)^INFO ") {
        Write-Pass "install - no INFO chatter at default level"
    } else {
        Write-Fail "install - INFO chatter leaked at default level"
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

    # Task XML <Command> element targets the windowless daemon launcher.
    # Both install paths resolve to dbxignorew.exe: the frozen install
    # ships it as a GUI-subsystem PyInstaller binary; the non-frozen
    # `uv tool install` path gets it as the [project.gui-scripts] trampoline
    # pip/uv generate. pythonw.exe is not a daemon launcher — a uv venv's
    # pythonw.exe is a console-subsystem copy of python.exe and would
    # allocate a visible console at logon.
    $xml = schtasks /Query /TN dbxignore /XML 2>$null
    if ($xml -match "<Arguments>.*daemon.*</Arguments>") {
        Write-Pass "Task scheduled with 'daemon' argument"
    } else {
        Write-Fail "Task scheduled command does not include 'daemon' argument"
    }
    if ($xml -match "<Command>[^<]*dbxignorew\.exe[^<]*</Command>") {
        Write-Pass "Task <Command> targets the windowless dbxignorew.exe launcher"
    } else {
        # Extract just the Command line for a clearer FAIL note.
        $cmdMatch = [regex]::Match($xml, "<Command>([^<]*)</Command>")
        $cmdValue = if ($cmdMatch.Success) { $cmdMatch.Groups[1].Value } else { "(no <Command> element)" }
        Write-Fail "Task <Command> targets unexpected exe: $cmdValue"
    }

    # Wait for the daemon to bring its watchdog observer online. Same
    # poll-for-sentinel approach as Linux/macOS.
    $logPath = Join-Path $env:LOCALAPPDATA "dbxignore\daemon.log"
    Write-Note "watched root: $script:DropboxDir"
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

    # 5a - opportunistic state=starting capture. Probed AFTER the
    # watching-roots break: daemon.run logs 'watching roots' BEFORE writing
    # the early state.json (daemon.py:663 then :678), so an in-loop probe
    # races state.write and almost always misses. Post-readiness, state.json
    # appears within microseconds; on a real Dropbox tree state=starting is
    # observable for the ~50s sweep window. On a small test tree the worker
    # can finish before we probe - that's the small-tree caveat the note
    # path covers.
    $sawStarting = $false
    for ($i = 0; $i -lt 5; $i++) {
        $probe = (dbxignore status --summary 2>$null | Select-Object -First 1)
        if ($probe -and ($probe -match '^state=starting pid=')) {
            $sawStarting = $true; break
        }
        Start-Sleep -Seconds 1
    }
    if ($sawStarting) {
        Write-Pass "5a - observed state=starting via --summary post-readiness"
    } else {
        Write-Note "5a - state=starting not observed within 5s post-readiness (small tree where sweep finished, or state.json not yet written); 5f still pins state=running"
    }

    # 5a-post - gate watchdog tests on state=running (cache populated).
    # cache.load_root runs in _initial_sweep_worker, NOT the main thread
    # (daemon.py:638). When the slow-sweep marker pads the worker, RuleCache
    # stays empty until the pad expires AND load_root finishes - watchdog
    # events arriving during that window dispatch against match()=False, so
    # 5b would observe an unmarked file even though the rule applies. Even
    # without the marker, a slow sweep on a real Dropbox tree could race
    # 5b's 8-second create-and-check window - this gate makes the test
    # deterministic in both cases.
    Write-Note "5a-post - waiting up to 180s for state=running (cache populated)"
    $cacheReady = $false
    for ($i = 0; $i -lt 180; $i++) {
        $probe = (dbxignore status --summary 2>$null | Select-Object -First 1)
        if ($probe -and ($probe -match '^state=running pid=')) {
            $cacheReady = $true; break
        }
        Start-Sleep -Seconds 1
    }
    if ($cacheReady) {
        Write-Pass "5a-post - cache populated; safe to exercise watchdog events"
    } else {
        Write-Fail "5a-post - state=running never reached within 180s"
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

    # 5d — DIR_CREATE bypass — newly created dir matching a rule
    # should be marked synchronously without waiting the OTHER debounce.
    Write-Note "5d - DIR_CREATE bypass for matched directory"
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

    # 5e — clear refuses while daemon alive; --force overrides.
    Write-Note "5e - clear refuses while daemon alive"
    $clearAliveOut = "$env:TEMP\dbxignore-clear-alive.out"
    dbxignore clear "$T" --yes *> $clearAliveOut
    $clearAliveContent = Get-Content $clearAliveOut -Raw
    if ($LASTEXITCODE -eq 0) {
        Write-Fail "5e - clear should have refused while daemon alive"
        # `r?`n so CRLF endings on Windows don't leave stray \r on each line.
        $clearAliveContent -split "`r?`n" | ForEach-Object { Write-Note "    $_" }
    } else {
        Write-Pass "5e - clear exited non-zero (refused)"
    }
    if ($clearAliveContent -match 'daemon is running') {
        Write-Pass "5e - refusal message names the daemon"
    } else {
        Write-Note $clearAliveContent
        Write-Fail "5e - refusal message unexpected"
    }
    # Scope the --force clear to a single file (freshrule.dat, marked in 5c)
    # rather than the whole tree: a tree-wide clear here would also clear
    # watch-me.tmp's marker, and Phase 6's "uninstall — markers retained on
    # watch-me.tmp" assertion would then fail vacuously (the marker is gone
    # before uninstall even runs). The override behavior is demonstrated
    # identically on a single-file target.
    dbxignore clear "$T\freshrule.dat" --force --yes 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "5e - clear --force overrides daemon-alive guard"
    } else {
        Write-Fail "5e - clear --force did not override the guard"
    }

    # 5f - post-sweep status surface. --summary returns the full state=running
    # field set; human path emits the 'daemon: running' line distinct from
    # the 'daemon: starting (initial sweep in progress)' branch.
    #
    # The daemon marks itself ready (and logs 'watching roots') BEFORE the
    # initial sweep completes - so the watching-roots poll above is NOT a
    # sweep-complete sentinel. On a real Dropbox tree the sweep can still be
    # running when 5f probes, in which case --summary correctly emits
    # 'state=starting pid=N' (truncated form). Poll for state=running for up
    # to 180s to absorb the transition - matches the watching-roots-wait
    # headroom above. Each iteration also spawns dbxignore.exe (~300-500ms
    # shim startup on Windows), so the actual wall-clock window can extend
    # past 180s; acceptable for a manual smoke test.
    Write-Note "5f - status --summary post-sweep + human 'daemon: running' line"
    $sumLate = ""
    $sumPattern = '^state=running pid=\d+ marked=\d+ cleared=\d+ errors=\d+ conflicts=\d+$'
    for ($i = 0; $i -lt 180; $i++) {
        $sumLate = (dbxignore status --summary 2>&1 | Select-Object -First 1)
        if ($sumLate -match $sumPattern) { break }
        Start-Sleep -Seconds 1
    }
    if ($sumLate -match $sumPattern) {
        Write-Pass "5f - --summary post-sweep: $sumLate"
    } else {
        Write-Fail "5f - --summary did not advance to state=running within 180s (last: $sumLate)"
    }
    # Once --summary reports state=running, the same state.json drives the
    # human path: last_sweep is not None, so the 'daemon: running' branch
    # fires synchronously. Single-shot is safe here.
    $humanOut = ((dbxignore status 2>&1) -join "`n")
    if ($humanOut -match '(?m)^daemon: running \(pid=\d+\)$') {
        Write-Pass "5f - human status reports 'daemon: running'"
    } else {
        Write-Note "    human status output:"
        $humanOut -split "`r?`n" | ForEach-Object { Write-Note "    $_" }
        Write-Fail "5f - human status did not report 'daemon: running'"
    }

    # 5g — registry keys after default install
    # Read-only: doesn't mutate state. Phase 5 ends in installed-daemon
    # state, exactly as today, so Phase 6's `dbxignore uninstall` precondition
    # is preserved.
    Write-Note "5g - HKCU verb keys present after default install"
    $regBase = "HKCU:\Software\Classes\AllFilesystemObjects\shell"
    $ignoreKey = "$regBase\DbxignoreIgnore"
    $restoreKey = "$regBase\DbxignoreRestore"

    # Dependent registry-property assertions are guarded on Test-Path —
    # `$ErrorActionPreference = "Stop"` (script line 44) would otherwise
    # cause `Get-ItemProperty` against a missing key to hard-crash and
    # abort the rest of Phase 5g (and Phases 6 + 7 cleanup). Applies when
    # running against a version that pre-dates shell-integration.
    if ((Test-Path $ignoreKey) -and (Test-Path $restoreKey)) {
        Write-Pass "5g - both verb keys present"

        # MUIVerb labels.
        $ignoreLabel = (Get-ItemProperty -Path $ignoreKey -Name "MUIVerb").MUIVerb
        $restoreLabel = (Get-ItemProperty -Path $restoreKey -Name "MUIVerb").MUIVerb
        if ($ignoreLabel -eq "Ignore from Dropbox" -and $restoreLabel -eq "Restore to Dropbox") {
            Write-Pass "5g - MUIVerb labels correct"
        } else {
            Write-Fail "5g - MUIVerb labels wrong: ignore='$ignoreLabel' restore='$restoreLabel'"
        }

        # AppliesTo includes the Dropbox root.
        # AQS uses single literal backslashes — no doubling — so match the path as-is.
        $appliesTo = (Get-ItemProperty -Path $ignoreKey -Name "AppliesTo").AppliesTo
        if ($appliesTo -like "*$($script:DropboxDir)*") {
            Write-Pass "5g - AppliesTo contains Dropbox root"
        } else {
            Write-Fail "5g - AppliesTo missing Dropbox root: $appliesTo"
        }

        # Command strings — asymmetric --yes policy.
        $ignoreCmd = (Get-ItemProperty -Path "$ignoreKey\command" -Name "(default)").'(default)'
        $restoreCmd = (Get-ItemProperty -Path "$restoreKey\command" -Name "(default)").'(default)'
        if ($ignoreCmd -match '\bignore "%1"$' -and $ignoreCmd -notmatch "--yes") {
            Write-Pass "5g - ignore command lacks --yes (confirms in console)"
        } else {
            Write-Fail "5g - ignore command shape unexpected: $ignoreCmd"
        }
        if ($restoreCmd -match '\bunignore --yes "%1"$') {
            Write-Pass "5g - restore command has --yes (one-click safe)"
        } else {
            Write-Fail "5g - restore command shape unexpected: $restoreCmd"
        }
    } else {
        Write-Fail "5g - verb keys missing after default install (skipping registry-property assertions)"
    }

    # 5g - Explorer-verb fidelity probes
    # The registry-string assertions above check what we wrote but not
    # whether Windows Explorer's AQS evaluator agrees. These two sub-cases
    # drive Shell.Application (the same COM surface Explorer uses) against
    # real files: one inside the Dropbox root (verb must surface) and one in
    # a sibling folder whose name starts with the Dropbox basename (verb must
    # NOT surface — exercises the trailing-`\` invariant in the `:~<` prefix
    # clause).
    Write-Note "5g - Shell.Application surfaces 'Ignore from Dropbox' inside Dropbox root"
    $probeFile = Join-Path $T "_5g_probe.tmp"
    Set-Content -Path $probeFile -Value "probe" -Encoding ascii -NoNewline
    try {
        $state = Get-Verb5gState `
            -FolderPath $T `
            -ProbeFileName (Split-Path -Leaf $probeFile) `
            -ContextLabel "probe file at $T"
        if ($null -ne $state) {
            if ($state.Present) {
                Write-Pass "5g - 'Ignore from Dropbox' visible on Dropbox-root file"
            } else {
                Write-Note "    verbs surfaced: $($state.VerbList -join '; ')"
                Write-Fail "5g - 'Ignore from Dropbox' NOT visible inside Dropbox (AppliesTo may not evaluate)"
            }
        }
    } finally {
        Remove-Item -Force -ErrorAction SilentlyContinue -Path $probeFile
    }

    # Negative probe — sibling folder name STARTS WITH the Dropbox basename
    # so a regression that drops the trailing `\` in `:~<"<root>\"` would
    # make the prefix `C:\Dropbox` match `C:\Dropbox-dbxignore-5g-sibling`
    # too, surfacing the verb here.
    #
    # The sibling-dir name is fixed and lives outside `$T`. A tester with a
    # pre-existing directory at that path would have their data wiped by the
    # `finally` block. Refuse to proceed when the path already exists so the
    # tester can rename/remove it manually, rather than silently overwriting
    # user data.
    Write-Note "5g - Shell.Application does NOT surface 'Ignore from Dropbox' outside Dropbox root"
    $siblingDir = "$($script:DropboxDir)-dbxignore-5g-sibling"
    if (Test-Path $siblingDir) {
        Write-Fail "5g - sibling probe dir '$siblingDir' already exists; refusing to overwrite. Rename or remove it before re-running."
    } else {
        $siblingProbe = Join-Path $siblingDir "probe.tmp"
        New-Item -ItemType Directory -Path $siblingDir | Out-Null
        Set-Content -Path $siblingProbe -Value "probe" -Encoding ascii -NoNewline
        try {
            $state = Get-Verb5gState `
                -FolderPath $siblingDir `
                -ProbeFileName (Split-Path -Leaf $siblingProbe) `
                -ContextLabel "sibling probe file at $siblingDir"
            if ($null -ne $state) {
                if (-not $state.Present) {
                    Write-Pass "5g - 'Ignore from Dropbox' correctly absent on sibling folder"
                } else {
                    Write-Note "    verbs surfaced on sibling: $($state.VerbList -join '; ')"
                    Write-Fail "5g - 'Ignore from Dropbox' visible outside Dropbox (AppliesTo too broad)"
                }
            }
        } finally {
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue -Path $siblingDir
        }
    }

    # Remove slow-sweep marker so phase 6's re-install + uninstall cycles
    # run with normal sweep timing. Phase 7 also removes it as a defensive
    # backstop if this point is never reached.
    if (Test-Path $slowSweepMarker) {
        Remove-Item $slowSweepMarker -Force -ErrorAction SilentlyContinue
        Write-Note "5 - slow-sweep marker removed before phase 6"
    }
}

# ---------------------------------------------------------------------------
# Phase 6 — uninstall
# ---------------------------------------------------------------------------

function Test-Uninstall {
    Write-Phase "Phase 6 - uninstall"

    $T = Join-Path $script:DropboxDir $TestSubdir
    $stateDir = Join-Path $env:LOCALAPPDATA "dbxignore"
    $stateFile = Join-Path $stateDir "state.json"

    # plain uninstall: scheduled task removed, markers retained.
    # install/windows_task.py:uninstall_task runs schtasks /End + waits for
    # the daemon to exit + schtasks /Delete /F. By the time `dbxignore
    # uninstall` returns, the daemon process is gone — same
    # synchronous-shutdown contract as Linux's `systemctl --user disable
    # --now` and macOS's `launchctl bootout`. Single-shot probes for
    # 6a/6b are sufficient.
    #
    # Capture the pre-uninstall daemon_pid so the post-reinstall poll below
    # can wait for state.json to advance to the NEW daemon's pid before
    # --purge fires. Without that gate, --purge reads the stale daemon_pid
    # from state.json (retained by plain uninstall), routes
    # uninstall_task's synchronous wait at the long-dead pid, and /Delete
    # reverts to fire-and-forget against the live re-installed daemon.
    $oldPid = $null
    if (Test-Path $stateFile) {
        try {
            $oldPid = (Get-Content $stateFile -Raw | ConvertFrom-Json).daemon_pid
        } catch {
            # leave $oldPid as $null — best-effort
        }
    }

    # -v added to verify the verbosity flag surfaces install-backend INFO
    # chatter end-to-end. Default-quiet side is verified in Phase 5.
    $uninstOut = "$env:TEMP\dbxignore-uninst.out"
    dbxignore -v uninstall *> $uninstOut
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "dbxignore -v uninstall (rc=0)"
    } else {
        Write-Fail "dbxignore -v uninstall"
        Get-Content $uninstOut | ForEach-Object { Write-Note "    $_" }
    }

    $uninstContents = Get-Content $uninstOut -Raw
    if ($uninstContents -match "(?m)^INFO ") {
        Write-Pass "uninstall -v - INFO surfaces under verbose"
    } else {
        Write-Fail "uninstall -v - verbose did not surface INFO"
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

    # 6a - status --summary returns state=not_running post-uninstall.
    # state.json is retained by plain uninstall; the daemon process is gone
    # (synchronous teardown), so daemon_is_running(s) is False on the first
    # probe. Single-shot.
    Write-Note "6a - status --summary post-uninstall"
    $sumUninst = (dbxignore status --summary 2>&1 | Select-Object -First 1)
    if ($sumUninst -match '^state=not_running pid=\d+ marked=\d+ cleared=\d+ errors=\d+ conflicts=\d+$') {
        Write-Pass "6a - --summary post-uninstall: $sumUninst"
    } else {
        Write-Fail "6a - --summary post-uninstall did not match expected pattern: $sumUninst"
    }

    # 6c — registry keys gone after plain uninstall
    Write-Note "6c - HKCU verb keys removed by default uninstall"
    $regBase = "HKCU:\Software\Classes\AllFilesystemObjects\shell"
    if (-not (Test-Path "$regBase\DbxignoreIgnore") -and -not (Test-Path "$regBase\DbxignoreRestore")) {
        Write-Pass "6c - both verb keys removed"
    } else {
        Write-Fail "6c - verb keys persisted after plain uninstall"
    }

    # re-install briefly, then --purge
    Write-Note "re-installing for --purge test..."
    dbxignore install 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Stop-Abort "re-install failed" }

    # Wait for state.json's daemon_pid to ADVANCE from the pre-uninstall
    # value to the new daemon's pid. state.json is retained by plain
    # uninstall, so a Test-Path-only check passes on the stale file
    # immediately and --purge ends up reading the dead pre-uninstall
    # daemon_pid — uninstall_task's synchronous wait would then target a
    # long-dead pid and /Delete would revert to fire-and-forget against the
    # actually-live re-installed daemon. Polling for daemon_pid != $oldPid
    # pins the test to the realistic "purge against a running daemon" race.
    # PID reuse in a 10s window is vanishingly rare on Windows.
    $reinstallPid = $null
    for ($i = 0; $i -lt 10; $i++) {
        if (Test-Path $stateFile) {
            try {
                $candidatePid = (Get-Content $stateFile -Raw | ConvertFrom-Json).daemon_pid
                if ($candidatePid -and ($candidatePid -ne $oldPid)) {
                    $reinstallPid = $candidatePid
                    break
                }
            } catch {
                # keep polling
            }
        }
        Start-Sleep -Seconds 1
    }
    if ($reinstallPid) {
        Write-Pass "post-reinstall state.json advanced to new daemon pid=$reinstallPid (was $oldPid)"
    } else {
        Write-Fail "state.json daemon_pid did not advance from old=$oldPid within 10s post-reinstall — --purge below would test against stale state and miss the race"
    }

    $purgeOut = "$env:TEMP\dbxignore-purge.out"
    dbxignore uninstall --purge *> $purgeOut
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "dbxignore uninstall --purge (rc=0)"
    } else {
        Write-Fail "dbxignore uninstall --purge"
        Get-Content $purgeOut | ForEach-Object { Write-Note "    $_" }
    }
    # Happy-path purge regression guard: purge emits the "Cleared N" line but
    # must NOT emit the partial-failure error report. Forcing a real marker
    # OSError requires platform-specific FS contortions; the unit tests cover
    # the partial-failure assertion tightly. This guard pins the happy path.
    $purgeText = if (Test-Path $purgeOut) { Get-Content $purgeOut -Raw } else { "" }
    if ($purgeText -notmatch 'Could not fully clear') {
        Write-Pass "purge - no spurious 'Could not fully clear' on happy path"
    } else {
        Write-Fail "purge - emitted 'Could not fully clear' on happy path"
        Get-Content $purgeOut | ForEach-Object { Write-Note "    $_" }
    }
    # Happy-path state-files partial-failure guard. Same trade-off as the
    # marker guard above: forcing a state-dir OSError end-to-end needs
    # platform-specific FS contortions (the Windows daemon.lock cascade
    # requires manufacturing a hung daemon), and the unit tests pin the
    # partial-failure assertion tightly. This guard pins the happy path
    # against an accidental regression that would emit the report on every
    # clean uninstall.
    if ($purgeText -notmatch 'Could not fully purge state files') {
        Write-Pass "purge - no spurious 'Could not fully purge state files' on happy path"
    } else {
        Write-Fail "purge - emitted 'Could not fully purge state files' on happy path"
        Get-Content $purgeOut | ForEach-Object { Write-Note "    $_" }
    }
    # Daemon-alive purge-refusal guard. On a clean uninstall the guard
    # returns False - the two stderr phrases below must not appear.
    # Failure-path coverage requires platform-specific stuck-process
    # simulation; on Windows the schtasks /End 30s-timeout case can't be
    # reproduced without a stuck filesystem operation.
    if ($purgeText -notmatch 'daemon is running' -and $purgeText -notmatch 'liveness is unknown') {
        Write-Pass "purge - no spurious daemon-alive guard fire on happy path"
    } else {
        Write-Fail "purge - daemon-alive guard fired on happy path"
        Get-Content $purgeOut | ForEach-Object { Write-Note "    $_" }
    }

    if (Test-Path "$T\watch-me.tmp") { Assert-AdsUnset -Path "$T\watch-me.tmp" -Name "purge - watch-me.tmp marker cleared" }
    if (Test-Path "$T\cache")        { Assert-AdsUnset -Path "$T\cache"        -Name "purge - cache/ marker cleared" }

    $logFile = Join-Path $stateDir "daemon.log"
    if (-not (Test-Path $stateFile) -and -not (Test-Path $logFile)) {
        Write-Pass "purge - state.json + daemon.log removed"
    } else {
        Write-Fail "purge - state files remain"
        if (Test-Path $stateDir) {
            Get-ChildItem -Force $stateDir | ForEach-Object { Write-Note "    $_" }
        }
    }

    # 6b - status --summary returns state=no_state post-purge.
    # Truncated form: 'state=no_state conflicts=N' with no pid/marked/etc.
    Write-Note "6b - status --summary post-purge"
    $sumPurge = (dbxignore status --summary 2>&1 | Select-Object -First 1)
    if ($sumPurge -match '^state=no_state conflicts=\d+$') {
        Write-Pass "6b - --summary post-purge: $sumPurge"
    } else {
        Write-Fail "6b - --summary post-purge did not match expected pattern: $sumPurge"
    }

    # 6d / 6e — --no-shell-integration preservation + --purge override
    # Phase 6's existing flow ended with `dbxignore uninstall --purge` →
    # daemon gone, state gone, registry gone. We now exercise the
    # --no-shell-integration contrast cycle: re-install, plain uninstall
    # with the flag (keys preserved), re-install, --purge with the flag
    # (keys gone — purge override).

    Write-Note "6d setup - re-install for --no-shell-integration test"
    dbxignore install 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Stop-Abort "6d setup re-install failed" }
    # Wait briefly for the new daemon to write state.json.
    for ($i = 0; $i -lt 10; $i++) {
        if (Test-Path $stateFile) { break }
        Start-Sleep -Seconds 1
    }

    # 6d - plain uninstall --no-shell-integration: daemon gone, keys preserved.
    Write-Note "6d - uninstall --no-shell-integration preserves verb keys"
    dbxignore uninstall --no-shell-integration 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "6d - uninstall --no-shell-integration failed"
    } else {
        $regBase = "HKCU:\Software\Classes\AllFilesystemObjects\shell"
        if ((Test-Path "$regBase\DbxignoreIgnore") -and (Test-Path "$regBase\DbxignoreRestore")) {
            Write-Pass "6d - verb keys preserved after --no-shell-integration uninstall"
        } else {
            Write-Fail "6d - verb keys removed despite --no-shell-integration"
        }
    }

    Write-Note "6e setup - re-install for --purge --no-shell-integration test"
    # Capture the 6d-daemon's pid from the retained state.json (plain uninstall
    # retains it, leaving the stale pid in place). 6e's `--purge` must wait
    # until the *new* daemon's pid lands in state.json — otherwise `/Delete`
    # fires against the live re-installed daemon without the synchronous
    # shutdown wait.
    $sixDpid = $null
    if (Test-Path $stateFile) {
        try {
            $sixDpid = (Get-Content $stateFile -Raw | ConvertFrom-Json).daemon_pid
        } catch {
            # leave $sixDpid as $null — best-effort
        }
    }

    dbxignore install 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Stop-Abort "6e setup re-install failed" }

    $sixEpid = $null
    for ($i = 0; $i -lt 10; $i++) {
        if (Test-Path $stateFile) {
            try {
                $candidatePid = (Get-Content $stateFile -Raw | ConvertFrom-Json).daemon_pid
                if ($candidatePid -and ($candidatePid -ne $sixDpid)) {
                    $sixEpid = $candidatePid
                    break
                }
            } catch {
                # keep polling
            }
        }
        Start-Sleep -Seconds 1
    }
    if ($sixEpid) {
        Write-Pass "6e setup - state.json advanced to new daemon pid=$sixEpid (was $sixDpid)"
    } else {
        Write-Fail "6e setup - state.json daemon_pid did not advance from old=$sixDpid within 10s; --purge below would test against stale state"
    }

    # 6e - --purge --no-shell-integration: --purge overrides the preserve flag.
    Write-Note "6e - uninstall --purge --no-shell-integration removes verb keys"
    dbxignore uninstall --purge --no-shell-integration 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "6e - uninstall --purge --no-shell-integration failed"
    } else {
        $regBase = "HKCU:\Software\Classes\AllFilesystemObjects\shell"
        if (-not (Test-Path "$regBase\DbxignoreIgnore") -and -not (Test-Path "$regBase\DbxignoreRestore")) {
            Write-Pass "6e - verb keys removed by --purge override"
        } else {
            Write-Fail "6e - verb keys persisted despite --purge"
        }
    }

    # 6f - --purge proceeds when state.json is unreadable AND no daemon
    # process holds daemon.lock. Force the scenario: re-install (daemon
    # starts), Stop-Process -Force the daemon (Task Scheduler
    # RestartOnFailure Interval is PT1M = 60s, wide window for our uninstall
    # to run), corrupt state.json so state.read() returns None, then run
    # `dbxignore uninstall --purge`. uninstall_service removes the task
    # registration before any restart fires.
    Write-Note "6f - --purge recovers from corrupt state.json + dead daemon"
    $installOut6f = & dbxignore install 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "6f - re-install failed: $installOut6f"
        return
    }
    Start-Sleep -Seconds 2
    $statePath6f = Join-Path $stateDir "state.json"
    $daemonPid6f = $null
    try {
        $stateObj6f = Get-Content $statePath6f -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $daemonPid6f = $stateObj6f.daemon_pid
    } catch {
        Write-Note "6f - could not read daemon PID from state.json: $_"
    }
    if ($null -ne $daemonPid6f) {
        Stop-Process -Id $daemonPid6f -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
    Set-Content -Path $statePath6f -Value "corrupt {{{ not valid json" -Encoding utf8 -NoNewline
    $recoveryOut = & dbxignore uninstall --purge 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "6f - uninstall --purge succeeded with corrupt state.json + dead daemon"
    } else {
        Write-Fail "6f - uninstall --purge failed; expected exit 0"
        $recoveryOut | ForEach-Object { Write-Note "    $_" }
    }
    if (-not (Test-Path $statePath6f)) {
        Write-Pass "6f - corrupt state.json cleaned up by recovery purge"
    } else {
        Write-Fail "6f - corrupt state.json still present after recovery purge"
    }

    # 6g - uninstall --purge exits 2 on injected state-file purge failure
    # DBXIGNORE_TEST_FAIL_STATE_PURGE makes _purge_dir's unlink loop raise
    # OSError, exercising the state_errors exit-2 path. Re-install first so the
    # daemon writes state.json / daemon.lock. Markers ARE cleared (the failure
    # is in the later state-dir step); recovery is a clean --purge re-run.
    Write-Note "6g - uninstall --purge exits 2 on injected state-purge failure"
    dbxignore install *> $null
    if ($LASTEXITCODE -ne 0) { Stop-Abort "6g re-install failed" }
    Start-Sleep -Seconds 2
    $purge6gOut = Join-Path $env:TEMP "dbx-6g-purge.out"
    $env:DBXIGNORE_TEST_FAIL_STATE_PURGE = "1"
    & dbxignore uninstall --purge *> $purge6gOut
    $purge6gRc = $LASTEXITCODE
    Remove-Item Env:\DBXIGNORE_TEST_FAIL_STATE_PURGE
    if ($purge6gRc -eq 2) {
        Write-Pass "6g - uninstall --purge exits 2 on injected state-purge failure"
    } else {
        Write-Fail "6g - uninstall --purge exited $purge6gRc instead of 2"
        if (Test-Path $purge6gOut) { Get-Content $purge6gOut | ForEach-Object { Write-Note "    $_" } }
    }
    $purge6gText = if (Test-Path $purge6gOut) { Get-Content $purge6gOut -Raw } else { "" }
    if ($purge6gText -match 'Could not fully purge state files') {
        Write-Pass "6g - purge stderr reports the state-file failure"
    } else {
        Write-Fail "6g - purge stderr missing 'Could not fully purge state files'"
    }
    # Recovery: clean --purge to remove the state files the injected run left.
    dbxignore uninstall --purge *> $null

    # 6h - uninstall --purge exits 2 on injected daemon-alive guard
    # DBXIGNORE_TEST_FAIL_DAEMON_ALIVE fires the --purge daemon-alive gate as if
    # a daemon survived service removal. The gate fires BEFORE the purge body,
    # so nothing is cleared; recovery is a clean --purge re-run.
    Write-Note "6h - uninstall --purge exits 2 on injected daemon-alive guard"
    dbxignore install *> $null
    if ($LASTEXITCODE -ne 0) { Stop-Abort "6h re-install failed" }
    Start-Sleep -Seconds 2
    $purge6hOut = Join-Path $env:TEMP "dbx-6h-purge.out"
    $env:DBXIGNORE_TEST_FAIL_DAEMON_ALIVE = "1"
    & dbxignore uninstall --purge *> $purge6hOut
    $purge6hRc = $LASTEXITCODE
    Remove-Item Env:\DBXIGNORE_TEST_FAIL_DAEMON_ALIVE
    if ($purge6hRc -eq 2) {
        Write-Pass "6h - uninstall --purge exits 2 on injected daemon-alive guard"
    } else {
        Write-Fail "6h - uninstall --purge exited $purge6hRc instead of 2"
        if (Test-Path $purge6hOut) { Get-Content $purge6hOut | ForEach-Object { Write-Note "    $_" } }
    }
    $purge6hText = if (Test-Path $purge6hOut) { Get-Content $purge6hOut -Raw } else { "" }
    if ($purge6hText -match 'daemon is running') {
        Write-Pass "6h - purge stderr reports the daemon-alive refusal"
    } else {
        Write-Fail "6h - purge stderr missing 'daemon is running'"
    }
    # Recovery: clean --purge (the gate fired before any cleanup ran).
    dbxignore uninstall --purge *> $null
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

    # (Phase 5-pre is now a static PE-header check — no file manipulation,
    # so it needs no Phase 7 restore backstop.)

    # Defensive backstop for the slow-sweep marker. Honoring a stale marker
    # on a future install would silently pad every initial sweep, so make
    # sure phase 7 cleans it up even when phase 5 returned early.
    $slowSweepMarker = Join-Path $env:LOCALAPPDATA "dbxignore\_test_slow_sweep"
    if (Test-Path $slowSweepMarker) {
        Remove-Item $slowSweepMarker -Force -ErrorAction SilentlyContinue
    }

    # Defensive backstop for the 5g sibling probe dir. The `try/finally` in
    # phase 5g removes it on the happy path; a mid-phase-5 abort could leave
    # it behind, and it lives outside `$T` so the recursive cleanup above
    # doesn't reach it.
    #
    # Only remove if the contents match what 5g.6 would have left behind —
    # exactly one `probe.tmp` file and nothing else. A user-owned directory
    # that happens to share the name (with their own contents) gets left
    # untouched, with a warning.
    $sibling5g = "$($script:DropboxDir)-dbxignore-5g-sibling"
    if (Test-Path $sibling5g) {
        $contents = @(Get-ChildItem -Path $sibling5g -Force)
        if ($contents.Count -eq 1 -and $contents[0].Name -eq "probe.tmp" -and -not $contents[0].PSIsContainer) {
            Remove-Item -Path $sibling5g -Recurse -Force -ErrorAction SilentlyContinue
            Write-Note "5g sibling probe dir removed (defensive)"
        } else {
            Write-Note "5g sibling probe dir at '$sibling5g' has unexpected contents; leaving for manual inspection"
        }
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

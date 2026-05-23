# dbxignore

dbxignore applies the Dropbox ignore marker to paths that match a `.dropboxignore` file. `.dropboxignore` files use gitignore syntax and can be placed in any folder under a Dropbox root; matching paths are excluded from Dropbox sync. Markers are written using NTFS alternate data streams on Windows and extended attributes on Linux and macOS.

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Windows Explorer integration](#windows-explorer-integration)
- [Platform support](#platform-support)
- [`.dropboxignore` syntax](#dropboxignore-syntax)
- [Commands](#commands)
- [Behaviour](#behaviour)
- [Using `.gitignore` rules](#using-gitignore-rules)
- [Configuration](#configuration)
- [Backlog](#backlog)
- [License](#license)

## Requirements

- Windows 10/11 (NTFS), **or** a modern Linux distro with a systemd user session, **or** macOS (Apple Silicon for pre-built binaries; Intel via PyPI)
- Dropbox desktop client installed
- For the [PyPI install](#python-package), Python ≥ 3.11.

## Installation

Pick any method below. Every one ends the same way: `dbxignore install` registers the background daemon with your platform's service manager, and `dbxignore status` verifies it. The one-line scripts and the Windows installer run `dbxignore install` for you — see [Registering the daemon](#registering-the-daemon).

### Quick start

**macOS / Linux**

```bash
curl -fsSL https://dbxignore.com/install.sh | sh
```

**Windows**

```powershell
powershell -c "irm https://dbxignore.com/install.ps1 | iex"
```

These one-line scripts download the pre-built bundle, install it, and register the daemon — no Python required. The methods below cover package managers, Python environments, and platforms without a pre-built bundle.

### Windows installer

Download `dbxignore-setup.exe` from the latest [Release](https://github.com/kiloscheffer/dbxignore/releases) and run it. The installer is per-user — it installs to `%LOCALAPPDATA%\Programs\dbxignore` and needs no administrator rights.

On the "Select Additional Tasks" page, the "Register the dbxignore background daemon and Explorer right-click menu" checkbox is ticked by default — leave it ticked to run `dbxignore install` at the end of setup; untick it to install the binaries and `PATH` entry only. On an upgrade, leave it ticked so the daemon restarts on the new binaries immediately.

`dbxignore-setup.exe` is not code-signed, so Windows SmartScreen shows a "Windows protected your PC" prompt on first run — click "More info", then "Run anyway".

### One-line script

On **macOS / Linux**:

```bash
curl -fsSL https://dbxignore.com/install.sh | sh
```

Downloads the pre-built bundle for your platform (macOS arm64 or Linux x86_64), installs it under `~/.local/share/dbxignore/`, symlinks `~/.local/bin/dbxignore`, adds `~/.local/bin` to your `PATH` if it is not already there, and runs `dbxignore install`. Open a new shell afterwards so the `PATH` change takes effect.

Flags, passed after `sh -s --`:

- `--no-daemon` — install the binary only; skip `dbxignore install`.
- `--no-modify-path` — do not edit your shell profile; print the `PATH` line instead.
- `--uninstall` — remove the daemon, the installed files, the symlink, and the `PATH` entry.

On **Windows**:

```powershell
powershell -c "irm https://dbxignore.com/install.ps1 | iex"
```

Downloads `dbxignore-windows-x86_64.zip`, installs it under `%LOCALAPPDATA%\Programs\dbxignore`, adds that directory to your `PATH`, and runs `dbxignore install`. Open a new terminal afterwards so the `PATH` change takes effect. x86_64 is the only Windows build; it runs on ARM64 Windows under emulation.

To pass a switch — `-Uninstall`, `-NoDaemon`, or `-NoModifyPath` — build a scriptblock from the downloaded script; a bare `irm | iex` cannot take arguments:

```powershell
powershell -c "& ([scriptblock]::Create((irm https://dbxignore.com/install.ps1))) -Uninstall"
```

To pin a release, set `$env:DBXIGNORE_VERSION` in your shell before running the install.

### Package managers

**Scoop** (Windows):

```powershell
scoop bucket add dbxignore https://github.com/kiloscheffer/scoop-dbxignore
scoop install dbxignore/dbxignore
dbxignore install
```

**Homebrew** (macOS arm64 and Linux x86_64):

```bash
brew tap kiloscheffer/dbxignore
brew install dbxignore
dbxignore install
```

The bucket and tap repos are [`kiloscheffer/scoop-dbxignore`](https://github.com/kiloscheffer/scoop-dbxignore) and [`kiloscheffer/homebrew-dbxignore`](https://github.com/kiloscheffer/homebrew-dbxignore). With either manager, run `dbxignore uninstall` before the manager's own uninstall — see [Uninstalling](#uninstalling).

### Python package

Requires Python ≥ 3.11. Use this for a release from PyPI or on a platform with no pre-built bundle (Intel Macs, non-x86_64 Linux):

```bash
uv tool install dbxignore            # or: pip install dbxignore
```

Then run `dbxignore install`.

<details>
<summary>If a Windows install fails with "ERROR_CLOUD_FILE_INCOMPATIBLE_HARDLINKS"</summary>

Windows users whose `AppData` is OneDrive-synced (Files On-Demand) can hit:

```
error: Failed to install: psutil-...whl
  Caused by: failed to hardlink file from
  C:\Users\<user>\AppData\Roaming\uv\tools\... to
  C:\Users\<user>\AppData\Local\uv\cache\...:
  The cloud operation cannot be performed on a file with incompatible hardlinks. (os error 396)
```

uv hardlinks files from its cache into the tool's site-packages by default; the Cloud Files API rejects hardlinks on placeholder files (those backed by cloud storage but not yet fully materialized locally). Force uv to copy instead:

```powershell
uv tool install --link-mode=copy git+https://github.com/kiloscheffer/dbxignore
```

Or set `$env:UV_LINK_MODE = "copy"` before the install. Either form works for `uv tool upgrade` too.

</details>

### Manual install

<details>
<summary>Install the pre-built bundle by hand</summary>

**Windows** — download `dbxignore-windows-x86_64.zip` from the latest [Release](https://github.com/kiloscheffer/dbxignore/releases) and extract it to a stable directory (e.g. `%LOCALAPPDATA%\Programs\dbxignore\`). The archive contains `dbxignore.exe` (the CLI you run from a terminal), `dbxignorew.exe` (the GUI helper that Task Scheduler invokes for the daemon and that the Explorer right-click verbs target), and an `_internal\` folder of bundled dependencies — keep the three together; you run `dbxignore`, never `dbxignorew.exe` directly. Add the extraction directory to your `PATH`, then run `dbxignore install`.

**macOS / Linux** — download the tarball for your platform from the latest Release:

```bash
curl -L -o dbxignore-macos-arm64.tar.gz \
  https://github.com/kiloscheffer/dbxignore/releases/latest/download/dbxignore-macos-arm64.tar.gz
tar -xzf dbxignore-macos-arm64.tar.gz
```

The archive extracts to a `dbxignore/` directory containing the `dbxignore` executable and an `_internal/` folder of bundled dependencies; keep them together. Move the directory somewhere stable, put the executable on your `PATH`, and run `dbxignore install`. `curl` does not set the `com.apple.quarantine` attribute, so Gatekeeper does not block a binary fetched this way.

</details>

### Registering the daemon

Every install method finishes with:

```
dbxignore install                    # register the background daemon
dbxignore status                     # verify: daemon running and watching Dropbox
```

`dbxignore install` registers the daemon with your platform's service manager so it starts at login:

- **Windows** — a Task Scheduler entry that launches the daemon at every user logon. It also registers the Explorer right-click verbs (see [Windows Explorer integration](#windows-explorer-integration)).
- **Linux** — writes `~/.config/systemd/user/dbxignore.service` and runs `systemctl --user enable --now`. Check unit state or logs with `systemctl --user status dbxignore.service` or `journalctl --user -u dbxignore.service`. Requires a systemd user session (standard on most modern distros; on WSL2, set `systemd=true` in `/etc/wsl.conf`). For a non-stock Dropbox location, export `DBXIGNORE_ROOT` before running `dbxignore install` — the value is written into the unit's `[Service]` block as `Environment="DBXIGNORE_ROOT=..."` so it reaches the daemon under systemd; re-run `dbxignore install` if the location changes.
- **macOS** — writes `~/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist` and bootstraps it into your GUI session. This requires that you have logged into the macOS GUI at least once since the last reboot — an SSH-on-fresh-boot install fails with `Bootstrap failed: 5: Input/output error` until you do.

### Uninstalling

```
dbxignore uninstall                  # deregister the daemon; scrub state.json and logs
dbxignore uninstall --purge          # also clear every ignore marker
```

Run `dbxignore uninstall` *before* removing the program itself, so the service entry is deregistered cleanly.

**One-line script** — removes the daemon, the installed files, and the `PATH` entry in one step.

On **macOS / Linux**:

```bash
curl -fsSL https://dbxignore.com/install.sh | sh -s -- --uninstall
```

On **Windows**:

```powershell
powershell -c "& ([scriptblock]::Create((irm https://dbxignore.com/install.ps1))) -Uninstall"
```

**Windows installer** — uninstall from Settings → Apps (or "Add or remove programs"). The uninstaller asks whether to also clear your ignore markers; choose "No" to keep them.

**Package managers** — run `dbxignore uninstall` before the manager's own uninstall:

```bash
dbxignore uninstall
scoop uninstall dbxignore        # or: brew uninstall dbxignore
```

**Python package / manual install**:

```bash
dbxignore uninstall
uv tool uninstall dbxignore      # or: pip uninstall dbxignore
```

For the manual install, also delete the directory you extracted along with its `PATH` entry.

## Windows Explorer integration

On Windows, `dbxignore install` registers two right-click verbs in Explorer:

- **Ignore from Dropbox** — shows a MessageBox confirmation dialog (yellow warning
  triangle, Yes/No buttons) before running `dbxignore.exe ignore <path>`. Marking a
  path ignored causes Dropbox to delete it from the cloud and propagate the deletion
  to every linked device, so a confirmation is required.
- **Restore to Dropbox** — one-click; runs `dbxignore.exe unignore --yes <path>`.
  Safe direction (Dropbox re-syncs the path); no confirmation dialog.

The verbs only appear under discovered Dropbox roots — the `AppliesTo` filter is
generated at install time from `~/.dropbox/info.json`. To skip the registry write,
pass `--no-shell-integration` to `install` (also accepted on Linux/macOS as a no-op
for portable scripts). To preserve the verbs across a daemon reinstall, pass
`--no-shell-integration` to `uninstall`. `uninstall --purge` always removes them.

If you move your Dropbox folder, re-run `dbxignore install` to refresh the
`AppliesTo` filter.

## Platform support

| Platform | Marker mechanism                  | Daemon mechanism                |
|----------|-----------------------------------|---------------------------------|
| Windows 10 / 11 | NTFS Alternate Data Streams | Task Scheduler (user task)      |
| Linux (modern distros with a systemd user session; the pre-built binary needs glibc ≥ 2.35) | `user.com.dropbox.ignored` xattr | systemd user unit |
| macOS (Apple Silicon; Intel via PyPI) | `com.dropbox.ignored` xattr (legacy mode) or `com.apple.fileprovider.ignore#P` (File Provider mode — default since 2023; auto-detected) | launchd User Agent |

### macOS sync modes

dbxignore on macOS supports both Dropbox sync modes and auto-detects which one is active:

- **File Provider mode** — Dropbox folder at `~/Library/CloudStorage/Dropbox/`, ignored files marked via the `com.apple.fileprovider.ignore#P` extended attribute (per [Dropbox's docs](https://help.dropbox.com/sync/ignored-files)). Synced by Apple's File Provider extension; default for installs since 2023.
- **Legacy mode** — Dropbox folder at `~/Dropbox`, ignored files marked via the `com.dropbox.ignored` extended attribute. Synced by Dropbox's own daemon.

The macOS xattr backend auto-detects sync mode at first use. It reads the configured sync paths from `~/.dropbox/info.json` (one entry per Dropbox account) and queries `pluginkit` for the File Provider extension's user-toggled state. The decision: any path under `~/Library/CloudStorage/` (or under `/Volumes/...` with the extension allowed) → File Provider mode; extension explicitly disabled → legacy; `pluginkit` unavailable and no path is decisive → write both attribute names; otherwise → legacy. The result is cached for the rest of the process. `dbxignore status` echoes the decision; the daemon also logs it at startup.

<details>
<summary>Verify your sync mode manually</summary>

`dbxignore status` echoes a `sync mode:` line on macOS. To query Apple's PluginKit registry directly:

```bash
pluginkit -m -A -i com.getdropbox.dropbox.fileprovider
```

The prefix character indicates the user-toggled state: leading whitespace = registered, untoggled (default); `+` = explicitly enabled; `-` = explicitly disabled. No matching line means the extension isn't registered.

</details>

A symlink matched by a `.dropboxignore` rule is marked on the **link itself**, not its target. `dbxignore install` and the daemon write:

```
~/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist   # launchd unit
~/Library/Application Support/dbxignore/state.json        # daemon state
~/Library/Logs/dbxignore/daemon.log                       # daemon log (rotated)
~/Library/Logs/dbxignore/launchd.log                      # launchd-captured stdout/stderr
```

### Linux notes

- Dropbox on Linux marks ignored paths with the xattr `user.com.dropbox.ignored=1`. Files on filesystems that don't support `user.*` xattrs (tmpfs without `user_xattr`, vfat, some FUSE mounts) are skipped with a `WARNING` in the daemon log — not a fatal error.
- Several common operations strip xattrs silently: `cp` without `-a`, `mv` across filesystems, most archivers, `vim`'s default save-via-rename. The watchdog plus hourly sweep re-apply markers automatically; no action needed.
- Linux symlinks cannot carry `user.*` xattrs (kernel restriction). A symlink matched by a rule logs one `WARNING` per sweep and is skipped; its target is not affected.

### Linux daemon prerequisites

The daemon uses inotify to watch the Dropbox tree recursively. The kernel
caps the number of watches per user (`fs.inotify.max_user_watches`); on
default-config kernels this is often 8192, which a typical Dropbox tree
exceeds. The daemon refuses to start (exit code 75) when the limit is hit.

Raise the limit (one-time, persistent across reboots):

    echo 'fs.inotify.max_user_watches=524288' | sudo tee /etc/sysctl.d/99-dbxignore.conf
    sudo sysctl --system

If the daemon won't start, check `journalctl --user -u dbxignore.service`
for the exact errno (ENOSPC = watch count, EMFILE = instance count) and the
sysctl command to run.

## `.dropboxignore` syntax

Full `.gitignore` syntax via [`pathspec`](https://github.com/cpburnz/python-pathspec). Matching is case-insensitive to accommodate NTFS. A file named `.dropboxignore` is never itself ignored — it needs to sync so your other machines see the same rules.

Example (put in a project root):

```
# everything javascripty
node_modules/

# Python
__pycache__/
.venv/
*.egg-info/

# Rust
target/

# build output
/dist/*
/build/

# except this one specific artifact we want to share
!dist/release-notes.pdf
```

## Commands

| Command | Purpose |
|---|---|
| `dbxignore init [PATH]` | Scaffold a starter `.dropboxignore` in `PATH` (or cwd) with a template covering Node.js / Python / Rust / JVM / .NET / frontend frameworks / build outputs / OS detritus. Walks the tree to depth 3 and annotates the header with which marker-bait dirs were detected. See [First-time setup](#first-time-setup). |
| `dbxignore install` / `uninstall` | Register / remove the daemon with the platform's user-scoped service manager (Task Scheduler on Windows, systemd user unit on Linux, launchd LaunchAgent on macOS). `uninstall --purge` also clears every existing marker, removes local dbxignore state (`state.json`, `daemon.log*`, the state directory; on macOS also `~/Library/Logs/dbxignore/`), and on Linux removes any systemd drop-in directory. Any stray marker on a `.dropboxignore` file itself is logged at `WARNING` before being cleared. |
| `dbxignore daemon` | Run the watcher + hourly sweep in the foreground. Usually invoked by the platform's service manager (Task Scheduler on Windows, systemd on Linux, launchd on macOS). |
| `dbxignore apply [PATH]` | One-shot reconcile of the whole Dropbox (or a subtree). Pass `--from-gitignore <path>` to load rules from a `.gitignore` instead of `.dropboxignore` files in the tree. Pass `--dry-run` to preview what would be marked/cleared without changing anything. Prompts before mutating any marker; pass `--yes` to skip — see [Applying rules](#applying-rules). |
| `dbxignore generate <PATH>` | Translate a `.gitignore` (or any nominated file) to a `.dropboxignore`. `<PATH>` is a file or a directory; default output is `<dir>/.dropboxignore`. Flags: `-o <path>`, `--stdout`, `--force`. |
| `dbxignore status` | Is the daemon running? Last sweep counts, last error. Pass `--summary` for a stable single-line summary suitable for status-bar widgets — see [Status-bar integration](#status-bar-integration). |
| `dbxignore clear [PATH]` | Clear every ignore marker under the watched roots (or under `PATH`). Inverse of `apply`. Leaves `.dropboxignore` files and `state.json` untouched — see [Clearing all markers](#clearing-all-markers). |
| `dbxignore ignore <path>` | Append a literal-path rule to the nearest ancestor `.dropboxignore` and set the ignore marker on `<path>`. |
| `dbxignore unignore <path>` | Remove the rule and clear the marker. Refuses if `<path>` is also matched by a wildcard rule. |
| `dbxignore list [PATH]` | Print every path currently bearing the ignore marker. |
| `dbxignore explain PATH` | Which `.dropboxignore` rule (if any) matches the path? |

### First-time setup

`dbxignore init [PATH]` writes a starter `.dropboxignore` into `PATH` (or the current directory). The packaged template covers common dev artifacts across ecosystems — Node.js (`node_modules`, npm/yarn/pnpm caches and logs), Python (virtualenvs, bytecode, tool caches), Rust (`target/`), JVM (`.gradle/`), .NET (`bin/`, `obj/`), frontend frameworks (`.next/`, `.nuxt/`, `.svelte-kit/`, `.turbo/`, etc.), generic build/dist outputs, and OS detritus (`.DS_Store`, `Thumbs.db`, vim swap files).

```
dbxignore init                       # writes ./.dropboxignore
dbxignore init ~/Dropbox/proj        # writes ~/Dropbox/proj/.dropboxignore
dbxignore init --stdout              # preview without writing
dbxignore init --force               # overwrite an existing file
```

The header of the generated file lists which marker-bait directories were detected in your tree at depth ≤ 3 (e.g., `# Detected in this tree at depth <= 3: node_modules, __pycache__`). All template patterns are emitted as active rules; the header is the cue for which ones are immediately load-bearing. Edit the file afterward to remove patterns that don't apply to your tree.

### Applying rules

`dbxignore apply` runs one reconcile pass — the same operation the daemon performs on every `.dropboxignore` save and on its hourly recovery sweep. Useful for forcing a one-shot run without waiting for the daemon (or when no daemon is installed).

```
dbxignore apply --dry-run            # preview what would be marked/cleared
dbxignore apply --yes                # skip the confirmation prompt
dbxignore apply ~/Dropbox/proj       # scope to a subtree
dbxignore apply --from-gitignore ~/Dropbox/proj/.gitignore --yes
```

A confirmation prompt fires by default and summarizes how many paths will be marked or cleared. Both directions are destructive — see [Behaviour](#behaviour) for what marker mutations do to cloud sync. Pass `--yes` for scripted use.

If `apply` finds nothing to mark and nothing to clear (the steady-state case where the daemon has already converged the tree), it exits with `Nothing to apply (rules already in sync).` and skips the prompt.

Unlike `clear`, `apply` does **not** refuse to run while the daemon is alive — the daemon performs the same operation continuously, so racing it is normal usage.

### Clearing all markers

`dbxignore clear` walks the watched roots and clears every ignore marker, the inverse of `apply`. Useful for staging a manual sync change or testing that Dropbox re-syncs previously-ignored content from the cloud. Unlike `uninstall --purge`, it leaves `.dropboxignore` rule files and `state.json` untouched.

```
dbxignore clear --dry-run            # preview what would be cleared
dbxignore clear --yes                # skip the confirmation prompt
dbxignore clear ~/Dropbox/proj       # scope to a subtree
dbxignore clear --force --yes        # override daemon-alive guard
```

`clear` refuses to run when the daemon is alive — the daemon's next sweep would re-apply rule-driven markers within seconds (rule-reload events) or within the hour (recovery sweep tick). Stop the daemon first (`dbxignore uninstall`) or pass `--force` for known short-window tests where you'll restart the daemon yourself.

A confirmation prompt fires by default. After the clear, Dropbox starts syncing previously-ignored paths — for a `node_modules` previously kept out of sync, that's potentially gigabytes of upload, so the prompt is a footgun guard. Pass `--yes` for scripted use.

### Status-bar integration

`dbxignore status --summary` emits a stable single-line summary on stdout, suitable for status-bar widgets (polybar, tmux, i3blocks, sketchybar) and cron-friendly polling.

The format is part of the public API per [SemVer](https://semver.org/): adding new fields is non-breaking, but renaming or removing existing fields bumps MINOR pre-1.0 / MAJOR post-1.0.

```
state=<token> [pid=N] marked=N cleared=N errors=N conflicts=N
```

State tokens:

- `running` — `state.json` present and the recorded PID corresponds to a live dbxignore daemon process.
- `not_running` — `state.json` present but the recorded PID is no longer a live daemon (cleanly stopped, or stale state).
- `no_state` — no `state.json` (daemon never ran). Only `state` and `conflicts` are emitted in this case.
- `starting` — daemon is alive but the initial sweep has not yet completed.

**`state=starting`** is emitted when the daemon is alive but the initial sweep has not yet completed. During this window, the summary contains only `state` and `pid` — `marked`, `cleared`, `errors`, and `conflicts` are omitted because they would all be 0 and would falsely imply the daemon swept and found nothing. The transition to `state=running` happens when the initial sweep completes (a fresh install of a 27,000-directory Dropbox tree took ~50s in testing).

`pid=N` is omitted when no PID was recorded (rare partial-write case). The remaining fields (`marked`, `cleared`, `errors`) are present whenever a `state.json` exists, even if the daemon never finished a sweep — they default to zero.

Examples:

```
state=running pid=12345 marked=7 cleared=1 errors=0 conflicts=0
state=not_running pid=12345 marked=7 cleared=1 errors=0 conflicts=0
state=no_state conflicts=0
```

A polybar module reading the daemon state could grep `state=\S+` for the at-a-glance indicator and `errors=\S+` for an error-count badge.

### Command parity with git

For users coming from `git`, this table maps each `dbxignore` command to its
closest git counterpart. Some align cleanly; others have a deceptively-similar
git verb with materially different consequences.

| `dbxignore`     | git counterpart            | Notes                                                          |
| --------------- | -------------------------- | -------------------------------------------------------------- |
| `apply`         | (none)                     | Reconciles markers from `.dropboxignore`.                      |
| `check-ignore`  | `git check-ignore -v`      | Alias of `explain`. `--quiet` matches git's flag.              |
| `clear`         | (see callout below)        | **NOT** `git rm --cached`-shaped.                              |
| `daemon`        | (none)                     | dbxignore-specific watcher + hourly sweep.                     |
| `explain`       | `git check-ignore -v`      | Same diagnostic question; `--quiet` and exit codes match.      |
| `generate`      | (none)                     | Translates a `.gitignore` into a `.dropboxignore`.             |
| `ignore`        | (none)                     | Append a path-anchored rule and set the marker in one step.    |
| `init`          | `git init` (loosely)       | Scaffolds a starter `.dropboxignore`, not a repository.        |
| `install`       | (none)                     | Registers the daemon with the platform service manager.        |
| `list`          | (none)                     | Lists every path currently bearing the Dropbox ignore marker.  |
| `status`        | `git status` (loosely)     | Shows daemon state, last sweep, marker counts, conflicts.      |
| `unignore`      | (see callout below)        | Inverse of `ignore`; refuses on wildcard-rule collision.       |
| `uninstall`     | (none)                     | Removes the daemon registration; `--purge` also clears markers.|

> **`clear` and `unignore` are NOT `git rm --cached`-shaped.** `git rm --cached`
> removes a path from the git index without touching the working tree (cheap,
> local-only). Both `dbxignore clear` (whole tree) and `dbxignore unignore <path>`
> (single path) remove Dropbox ignore markers, which causes Dropbox to
> **upload previously-ignored paths to the cloud** (potentially gigabytes
> for a `node_modules`-class subtree) and propagate them to other linked
> devices. The `--yes` confirmation prompt and `--dry-run` preview exist on
> both verbs specifically because of this divergence.

## Behaviour

- **What "ignored" means in Dropbox.** Setting the ignore marker on a file or folder removes it from your cloud Dropbox and from every other linked device. The local copy on the device that set the marker is preserved. Removing the marker (by deleting the matching rule, or running `dbxignore clear`) restores the path to sync — the local copy is uploaded back to Dropbox and propagated to other devices. So `.dropboxignore` is **not** a `.gitignore`-style "leave this file untracked here" rule; it's an instruction to delete the path from cloud sync, with the local copy as the only surviving copy until the marker is cleared.
- **Source of truth.** `.dropboxignore` files declare what is ignored. Removing a rule unignores the matching paths on the next reconcile. A path marked ignored via Dropbox's right-click menu but not matching any rule will be unignored.
- **Hybrid trigger.** The daemon reacts to filesystem events in real time *and* runs an hourly safety-net sweep. If the daemon is offline, an initial sweep at the next start catches any drift.
- **Multi-root.** Personal and Business Dropbox roots are discovered automatically from `%APPDATA%\Dropbox\info.json` (with `%LOCALAPPDATA%\Dropbox\info.json` as a fallback for "install for all users") on Windows, and from `~/.dropbox/info.json` on Linux and macOS.

### Negations and Dropbox's ignore inheritance

Dropbox marks files and folders as ignored using xattrs. When a folder carries the ignore marker, Dropbox does not sync that folder or anything inside it — children inherit the ignored state regardless of whether they individually carry the marker. This matters for gitignore-style negation rules in your `.dropboxignore`.

A negation can only re-include a path if no strict ancestor directory of that path is marked ignored. The case dbxignore drops is when an earlier rule marks a directory and a later negation tries to re-include something inside that directory:

```
build/                               # marks the directory build/ itself
!build/keep/                         # ← dropped: build/ is already ignored, inheritance wins
```

dbxignore detects this at the moment you save the `.dropboxignore`, logs a WARNING naming both rules, and drops the conflicted negation from the active rule set.

The git-canonical pattern works because it marks only the *children* of `build/`, not `build/` itself:

```
build/*                              # marks immediate children
!build/keep/                         # except this one
!build/keep/**                       # re-include everything under it
```

If you wrote `build/` only to except a child, switch the trailing `/` to `/*` — the two forms differ (`build/` marks the directory itself; `build/*` does not), so only switch when the negation is the load-bearing reason for the rule.

Other negations that don't conflict with an ignored ancestor work normally. For example:

```
*.log
!important.log
```

Here nothing marks a parent directory as ignored (`*.log` matches files, not dirs), so the negation works — `important.log` gets synced, the other `.log` files don't.

**Detection limitations:**
- Static analysis uses the rule's literal path prefix. Negations that begin with a glob (`!**/keep/`, `!*/cache/`) have no literal anchor to analyze and are accepted without conflict-check; if they land under an ignored ancestor at runtime, they silently fail to take effect. If you need guaranteed semantics, prefer negations with a literal prefix.
- `dbxignore generate` runs the same conflict check on the source file at write time and emits a stderr warning listing any dropped negations. The check is scoped to the source file alone — cross-file conflicts (a `.dropboxignore` higher up the tree masking a negation in this one) only surface at runtime via `dbxignore status` and `dbxignore explain`.

## Using `.gitignore` rules

A `.gitignore` and a `.dropboxignore` use the same pattern grammar (the same `pathspec` parser handles both). Two CLI verbs let you reuse `.gitignore` rules without hand-copying.

**`dbxignore generate <path>`** writes a `.dropboxignore` derived byte-for-byte from a source file. `<path>` may be a file or a directory; if a directory, `.gitignore` inside it is the source.

```
dbxignore generate ~/Dropbox/proj/.gitignore            # writes ~/Dropbox/proj/.dropboxignore
dbxignore generate ~/Dropbox/proj                       # same — auto-finds .gitignore
dbxignore generate ~/Dropbox/proj/.gitignore --stdout   # preview without writing
dbxignore generate ~/Dropbox/proj/.gitignore --force    # overwrite an existing .dropboxignore
```

The destination path is `<dir>/.dropboxignore` by default; use `-o <path>` to redirect. Without `--force`, an existing `.dropboxignore` at the target is left in place and the command exits non-zero.

**`dbxignore apply --from-gitignore <path>`** runs a one-shot reconcile using rules loaded from `<path>` (without writing a `.dropboxignore`). Rules are mounted at `dirname(<path>)`, which must be under a discovered Dropbox root. Existing `.dropboxignore` files in the tree do not participate in this run.

```
dbxignore apply --from-gitignore ~/Dropbox/myproject/.gitignore --yes
# apply: marked=12 cleared=0 errors=0 duration=0.34s
```

The `--yes` flag skips the confirmation prompt; without it `apply` previews the change set and asks before mutating any marker. See [Applying rules](#applying-rules) for the prompt's exact wording.

### Semantic divergence between the two files

A `.gitignore` says "git doesn't track this file." A `.dropboxignore` marker tells Dropbox to **stop syncing the path and remove it from cloud sync**. Most rules transfer cleanly (build outputs, dependency caches, IDE state) — but transplanting a `.gitignore` verbatim can mark files for cloud removal that you didn't intend to remove. Review the source file before running `apply --from-gitignore`, or run `generate --stdout` to preview.

### Interaction with the running daemon

If `dbxignore daemon` is running, writing a `.dropboxignore` (whether by `generate`, by hand, or by any other means) triggers a watchdog event. The daemon classifies it as a `RULES` event, debounces, and reconciles the affected root. End state: the markers are written and Dropbox starts removing matched paths from cloud sync. `generate` is therefore not a "preview-only" verb when the daemon is running — use `--stdout` to preview without committing the file.

### Negations

A pattern like `!build/keep/` (re-include a path under an ignored ancestor) is dropped silently; Dropbox's ignored-folder model does not support negation through ignored ancestors. Use `dbxignore explain <path>` to see which rule masked a dropped negation.

## Configuration

Environment variables read at daemon startup:

| Variable | Default | Purpose |
|---|---|---|
| `DBXIGNORE_DEBOUNCE_RULES_MS` | `100` | Debounce window for `.dropboxignore` file events. |
| `DBXIGNORE_DEBOUNCE_DIRS_MS` | `0` | Debounce for directory-creation events (`0` = react immediately, no coalescing). |
| `DBXIGNORE_DEBOUNCE_OTHER_MS` | `500` | Debounce for other file events. |
| `DBXIGNORE_LOG_LEVEL` | `INFO` | Daemon log level. Accepts `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` (case-insensitive). Unknown values fall back to `INFO`. Affects `dbxignore daemon` only — CLI commands use the top-level `-v` / `-vv` counted flag (default WARNING; `-v` INFO; `-vv` DEBUG). See **Log levels** below for what each level surfaces. |
| `DBXIGNORE_ROOT` | *(unset)* | Escape hatch for non-stock Dropbox installs: overrides `info.json` discovery and treats the given absolute path as the sole Dropbox root. If the path doesn't exist, a WARNING is logged and no roots are returned (so `dbxignore apply` exits with "No Dropbox roots found"). |

<details>
<summary>Log levels</summary>

The daemon and CLI have separate log-config knobs:

- **Daemon (`dbxignore daemon`)** reads `DBXIGNORE_LOG_LEVEL` from the environment at startup. Output goes to the rotating file (and stderr on Linux for journald).
- **CLI commands (`apply`, `list`, `status`, `explain`, `install`, `uninstall`)** use the top-level `-v` / `-vv` counted flag: default WARNING (intentional `click.echo` summaries only); `-v` INFO (also surfaces install-backend chatter and similar operator-level diagnostics); `-vv` DEBUG. The env var is **not** consulted here. Output goes to stderr.

What each level surfaces:

| Level | What you see |
|---|---|
| `DEBUG` | Per-operation traces — individual marker reads/writes, watchdog event payloads, debouncer ticks, "xattr absent" / "path gone" race-condition skips on `clear_ignored`. Useful when debugging a specific reconcile decision or a marker-API edge case. |
| `INFO` (daemon default) | Daemon start/stop banners, sweep summaries (paths marked / cleared per sweep), install/uninstall confirmations, environment-forwarding diagnostics. The "what's the daemon doing right now" baseline. |
| `WARNING` (CLI default) | Recoverable conditions — filesystems that don't support markers (`ENOTSUP`/`EOPNOTSUPP`), missing `info.json`, dropped negations under ignored ancestors, symlink `EPERM` on Linux, `schtasks /Run` failure post-install, corrupt or shape-mismatched `state.json`. None of these stop the daemon. |
| `ERROR` | Conditions that prevent progress on a specific concern — "No Dropbox roots discovered; exiting", sweep-startup failures, watchdog or debouncer handler crashes (with traceback). The daemon either continues with reduced scope or shuts down cleanly. |
| `CRITICAL` | Accepted by the env var but no production code path emits at this level — the project tops out at `ERROR`. |

Ad-hoc debugging — bump the daemon's verbosity for one run:

```bash
# Linux / macOS
systemctl --user stop dbxignore.service                     # Linux: stop the running daemon
launchctl bootout gui/$(id -u)/com.kiloscheffer.dbxignore   # macOS: same idea

DBXIGNORE_LOG_LEVEL=DEBUG dbxignore daemon                  # foreground; output streams to terminal
```

```powershell
# Windows
schtasks /End /TN dbxignore          # stop the running task instance
$env:DBXIGNORE_LOG_LEVEL = "DEBUG"
dbxignore daemon                     # foreground in this shell
```

Re-enable the managed daemon (`systemctl --user start dbxignore.service`, `launchctl bootstrap`, or wait for next logon on Windows) when you're done.

CLI-side debugging — pass `--verbose` to any command:

```bash
dbxignore --verbose status
dbxignore -v apply ~/Dropbox/some/subtree
dbxignore -v explain ~/Dropbox/build/keep
```

Persisting a non-default level across managed-daemon restarts requires a platform-specific override and is not covered here — it's rarely the right move (DEBUG floods the daemon log fast). For one-off investigations, the foreground-run pattern above is the recommended path.

</details>

<details>
<summary>Log and state file locations</summary>

Logs (rotated, 25 MB total):
- Windows — `%LOCALAPPDATA%\dbxignore\daemon.log`.
- Linux — two sinks, same records. The rotating file at `$XDG_STATE_HOME/dbxignore/daemon.log` (fallback `~/.local/state/dbxignore/daemon.log`) is authoritative for offline debugging and bug-report bundling; `journalctl --user -u dbxignore.service` surfaces the same records via systemd-journald for live tailing and cross-service filtering.
- macOS — `~/Library/Logs/dbxignore/daemon.log` (rotated). `~/Library/Logs/dbxignore/launchd.log` captures launchd-time stdout/stderr (near-empty unless the daemon crashes during startup before its own log handler initializes).

State:
- Windows — `%LOCALAPPDATA%\dbxignore\state.json`.
- Linux — `$XDG_STATE_HOME/dbxignore/state.json` (fallback `~/.local/state/dbxignore/state.json`).
- macOS — `~/Library/Application Support/dbxignore/state.json` (split from the log dir to match Apple's app-data conventions).

</details>

## Backlog

Open items and planned work are tracked in [BACKLOG.md](BACKLOG.md).

## License

MIT — see [LICENSE](LICENSE).

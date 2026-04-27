# dbxignore

Hierarchical `.dropboxignore` files for Dropbox. Drop a `.dropboxignore` into any folder under your Dropbox root and matching paths get the Dropbox ignore marker set automatically — no more `node_modules/` cluttering your sync. Windows (NTFS alternate data streams), Linux (`user.*` xattrs), and macOS (xattrs) supported.

## Upgrading from v0.2.x

The project was renamed from `dropboxignore` to `dbxignore` in v0.3.0
(the old name collides with an unrelated 2019 PyPI project). Upgrade is
a one-time manual step:

```bash
dropboxignore uninstall --purge   # on v0.2.x — removes state, logs, service
pip install dbxignore              # or: uv pip install dbxignore
dbxignore install                  # registers the new service under new names
```

Your `.dropboxignore` rule files carry over untouched — they're never
modified by install/uninstall.

## Requirements

- **Windows 10/11** (NTFS), **or** a modern Linux distro with a systemd user session, **or** macOS (Apple Silicon for pre-built binaries; Intel via PyPI)
- Dropbox desktop client installed
- Python ≥ 3.11 with [`uv`](https://docs.astral.sh/uv/). Pre-built binaries (Windows `.exe`, macOS arm64 Mach-O) are alternatives.

## Install (Windows, from source)

```powershell
uv tool install git+https://github.com/kiloscheffer/dbxignore
dbxignore install
```

`dbxignore install` registers a Task Scheduler entry that launches the daemon (`pythonw -m dbxignore daemon`) at every user logon.

## Install (Linux)

Requires a systemd user session (standard on Ubuntu, Fedora, Debian, Arch, and most modern distros; WSL2 requires `systemd=true` in `/etc/wsl.conf`).

```bash
uv tool install git+https://github.com/kiloscheffer/dbxignore
dbxignore install                    # writes systemd user unit, enables it
systemctl --user status dbxignore.service
```

`dbxignore install` writes `~/.config/systemd/user/dbxignore.service` and runs `systemctl --user enable --now` so the daemon starts at login.

For non-stock Dropbox installs, export `DBXIGNORE_ROOT` before running `dbxignore install` — the install step will read the variable from your shell environment and write a corresponding `Environment="DBXIGNORE_ROOT=..."` line into the generated unit's `[Service]` block. Without this, a shell-exported value won't reach the daemon when systemd launches it. If your Dropbox location ever changes, re-run `dbxignore install` after updating the export.

To uninstall:

```bash
dbxignore uninstall                  # disables unit, removes the file
dbxignore uninstall --purge          # clears markers, state files, logs, systemd drop-in
```

Notes:
- Dropbox on Linux marks ignored paths with the xattr `user.com.dropbox.ignored=1`. Files on filesystems that don't support `user.*` xattrs (tmpfs without `user_xattr`, vfat, some FUSE mounts) are skipped with a `WARNING` in the daemon log — not a fatal error.
- Several common operations strip xattrs silently: `cp` without `-a`, `mv` across filesystems, most archivers, `vim`'s default save-via-rename. The watchdog plus hourly sweep re-apply markers automatically; no action needed.
- Linux symlinks cannot carry `user.*` xattrs (kernel restriction). A symlink matched by a rule logs one `WARNING` per sweep and is skipped. Its target is not affected.

## Install (macOS)

dbxignore on macOS uses the `com.dropbox.ignored` extended attribute (the same xattr Dropbox itself reads) and registers the daemon as a launchd User Agent.

```bash
pip install dbxignore                # or: uv tool install dbxignore
dbxignore install                    # writes ~/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist
                                     # and bootstraps it into your GUI session
```

`dbxignore install` requires that you've logged into the macOS GUI at least once since the last reboot — the GUI domain that LaunchAgents bootstrap into isn't initialized until a graphical login. SSH-on-fresh-boot installs fail with `Bootstrap failed: 5: Input/output error`. Log into the GUI, then retry.

### Pre-built binaries (arm64 only)

Pre-built Mach-O binaries are arm64 (Apple Silicon). Intel Mac users: install via PyPI — the wheel is universal Python.

```bash
curl -L -o dbxignore  https://github.com/kiloscheffer/dbxignore/releases/latest/download/dbxignore
curl -L -o dbxignored https://github.com/kiloscheffer/dbxignore/releases/latest/download/dbxignored
chmod +x dbxignore dbxignored
sudo mv dbxignore dbxignored /usr/local/bin/
```

The binaries are unsigned — Gatekeeper refuses them on first launch with "cannot be opened because it is from an unidentified developer." Either right-click → Open in Finder (macOS remembers the override), or strip the quarantine xattr explicitly:

```bash
xattr -d com.apple.quarantine /usr/local/bin/dbxignore
xattr -d com.apple.quarantine /usr/local/bin/dbxignored
dbxignore install
```

To uninstall:

```bash
dbxignore uninstall                  # bootouts the agent, removes the plist
dbxignore uninstall --purge          # also clears markers, state files, logs
```

Files written:

```
~/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist   # launchd unit
~/Library/Application Support/dbxignore/state.json        # daemon state
~/Library/Logs/dbxignore/daemon.log                       # daemon log (rotated)
~/Library/Logs/dbxignore/launchd.log                      # launchd-captured stdout/stderr
```

Notes:
- A symlink matched by a `.dropboxignore` rule is marked on the **link itself**, not its target. macOS allows xattrs on symlinks; Linux refuses with `EPERM` and emits a WARNING. So on macOS the marker lands silently and successfully — matching the design intent better than the Linux behavior.
- macOS support is new in v0.4. If you hit anything unexpected, please file an issue.

## Install (.exe)

1. Download `dbxignore.exe` and `dbxignored.exe` from the latest [Release](https://github.com/kiloscheffer/dbxignore/releases).
2. Place both in a stable directory (e.g. `%LOCALAPPDATA%\dbxignore\bin\`) and add it to your `PATH`.
3. Run `dbxignore install`.

## Platform support

| Platform | Marker mechanism                  | Daemon mechanism                | Tested |
|----------|-----------------------------------|---------------------------------|--------|
| Windows 10 / 11 | NTFS Alternate Data Streams | Task Scheduler (user task)      | yes (since v0.1) |
| Linux (Ubuntu 22.04 / 24.04 + most modern distros with systemd user session) | `user.com.dropbox.ignored` xattr | systemd user unit | yes (since v0.2) |
| macOS (Apple Silicon; Intel via PyPI) | `com.dropbox.ignored` xattr | launchd User Agent | new in v0.4 — please report issues |

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
/dist/
/build/

# except this one specific artifact we want to share
!dist/release-notes.pdf
```

## Commands

| Command | Purpose |
|---|---|
| `dbxignore install` / `uninstall` | Register / remove the daemon with the platform's user-scoped service manager (Task Scheduler on Windows, systemd user unit on Linux). `uninstall --purge` also clears every existing marker, removes local dbxignore state (`state.json`, `daemon.log*`, the state directory), and on Linux removes any systemd drop-in directory. Any stray marker on a `.dropboxignore` file itself is logged at `WARNING` before being cleared. |
| `dbxignore daemon` | Run the watcher + hourly sweep in the foreground. Usually invoked by Task Scheduler. |
| `dbxignore apply [PATH]` | One-shot reconcile of the whole Dropbox (or a subtree). |
| `dbxignore status` | Is the daemon running? Last sweep counts, last error. |
| `dbxignore list [PATH]` | Print every path currently bearing the ignore marker. |
| `dbxignore explain PATH` | Which `.dropboxignore` rule (if any) matches the path? |

## Behaviour

- **Source of truth.** `.dropboxignore` files declare what is ignored. Removing a rule unignores the matching paths on the next reconcile. A path marked ignored via Dropbox's right-click menu but not matching any rule will be unignored.
- **Hybrid trigger.** The daemon reacts to filesystem events in real time *and* runs an hourly safety-net sweep. If the daemon is offline, an initial sweep at the next start catches any drift.
- **Multi-root.** Personal and Business Dropbox roots are discovered automatically from `%APPDATA%\Dropbox\info.json` (Windows) or `~/.dropbox/info.json` (Linux).

### Negations and Dropbox's ignore inheritance

Dropbox marks files and folders as ignored using xattrs. When a folder carries the ignore marker, Dropbox does not sync that folder or anything inside it — children inherit the ignored state regardless of whether they individually carry the marker. This matters for gitignore-style negation rules in your `.dropboxignore`.

If you write a negation whose target lives under a directory ignored by an earlier rule — the canonical case is `build/` followed by `!build/keep/` — the negation cannot take effect. Dropbox will ignore `build/keep/` because `build/` is ignored, no matter what xattr we put on the child. dbxignore detects this at the moment you save the `.dropboxignore`, logs a WARNING naming both rules, and drops the conflicted negation from the active rule set.

Negations that don't conflict with an ignored ancestor work normally. For example:

```
*.log
!important.log
```

Here nothing marks a parent directory as ignored (`*.log` matches files, not dirs), so the negation works — `important.log` gets synced, the other `.log` files don't.

**Limitation.** Detection uses static analysis on the rule's literal path prefix. Negations that begin with a glob (`!**/keep/`, `!*/cache/`) have no literal anchor to analyze and are accepted without conflict-check — if they land under an ignored ancestor at runtime, they silently fail to take effect. If you need guaranteed semantics, prefer negations with a literal prefix.

## Configuration

Environment variables read at daemon startup:

| Variable | Default | Purpose |
|---|---|---|
| `DBXIGNORE_DEBOUNCE_RULES_MS` | `100` | Debounce window for `.dropboxignore` file events. |
| `DBXIGNORE_DEBOUNCE_DIRS_MS` | `0` | Debounce for directory-creation events (`0` = react immediately, no coalescing). |
| `DBXIGNORE_DEBOUNCE_OTHER_MS` | `500` | Debounce for other file events. |
| `DBXIGNORE_LOG_LEVEL` | `INFO` | Daemon log level. |
| `DBXIGNORE_ROOT` | *(unset)* | Escape hatch for non-stock Dropbox installs: overrides `info.json` discovery and treats the given absolute path as the sole Dropbox root. If the path doesn't exist, a WARNING is logged and no roots are returned (so `dbxignore apply` exits with "No Dropbox roots found"). |

Logs (rotated, 25 MB total):
- Windows — `%LOCALAPPDATA%\dbxignore\daemon.log`.
- Linux — two sinks, same records. The rotating file at `$XDG_STATE_HOME/dbxignore/daemon.log` (fallback `~/.local/state/dbxignore/daemon.log`) is authoritative for offline debugging and bug-report bundling; `journalctl --user -u dbxignore.service` surfaces the same records via systemd-journald for live tailing and cross-service filtering.
- macOS — `~/Library/Logs/dbxignore/daemon.log` (rotated). `~/Library/Logs/dbxignore/launchd.log` captures launchd-time stdout/stderr (near-empty unless the daemon crashes during startup before its own log handler initializes).

State:
- Windows — `%LOCALAPPDATA%\dbxignore\state.json`.
- Linux — `$XDG_STATE_HOME/dbxignore/state.json` (fallback `~/.local/state/dbxignore/state.json`).
- macOS — `~/Library/Application Support/dbxignore/state.json` (split from the log dir to match Apple's app-data conventions).

## Backlog

Open items, planned work, and the historical record of fixes are tracked in [BACKLOG.md](BACKLOG.md).

## License

MIT — see [LICENSE](LICENSE).

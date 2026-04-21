# dropboxignore

Hierarchical `.dropboxignore` files for Dropbox. Drop a `.dropboxignore` into any folder under your Dropbox root and matching paths get the Dropbox ignore marker set automatically — no more `node_modules/` cluttering your sync. Windows (NTFS alternate data streams) and Linux (`user.*` xattrs) supported.

## Requirements

- **Windows 10/11** (NTFS), **or** a modern Linux distro with a systemd user session
- Dropbox desktop client installed
- Python ≥ 3.11 with [`uv`](https://docs.astral.sh/uv/). The pre-built `.exe` (Windows only) is an alternative on Windows.

## Install (Windows, from source)

```powershell
uv tool install git+https://github.com/kiloscheffer/dropboxignore
dropboxignore install
```

`dropboxignore install` registers a Task Scheduler entry that launches the daemon (`pythonw -m dropboxignore daemon`) at every user logon.

## Install (Linux)

Requires a systemd user session (standard on Ubuntu, Fedora, Debian, Arch, and most modern distros; WSL2 requires `systemd=true` in `/etc/wsl.conf`).

```bash
uv tool install git+https://github.com/kiloscheffer/dropboxignore
dropboxignore install                    # writes systemd user unit, enables it
systemctl --user status dropboxignore.service
```

`dropboxignore install` writes `~/.config/systemd/user/dropboxignore.service` and runs `systemctl --user enable --now` so the daemon starts at login.

For non-stock Dropbox installs, export `DROPBOXIGNORE_ROOT` before running `dropboxignore install` — the install step will read the variable from your shell environment and write a corresponding `Environment="DROPBOXIGNORE_ROOT=..."` line into the generated unit's `[Service]` block. Without this, a shell-exported value won't reach the daemon when systemd launches it. If your Dropbox location ever changes, re-run `dropboxignore install` after updating the export.

To uninstall:

```bash
dropboxignore uninstall                  # disables unit, removes the file
dropboxignore uninstall --purge          # also clears every xattr marker
```

Notes:
- Dropbox on Linux marks ignored paths with the xattr `user.com.dropbox.ignored=1`. Files on filesystems that don't support `user.*` xattrs (tmpfs without `user_xattr`, vfat, some FUSE mounts) are skipped with a `WARNING` in the daemon log — not a fatal error.
- Several common operations strip xattrs silently: `cp` without `-a`, `mv` across filesystems, most archivers, `vim`'s default save-via-rename. The watchdog plus hourly sweep re-apply markers automatically; no action needed.
- Linux symlinks cannot carry `user.*` xattrs (kernel restriction). A symlink matched by a rule logs one `WARNING` per sweep and is skipped. Its target is not affected.

## Install (.exe)

1. Download `dropboxignore.exe` and `dropboxignored.exe` from the latest [Release](https://github.com/kiloscheffer/dropboxignore/releases).
2. Place both in a stable directory (e.g. `%LOCALAPPDATA%\dropboxignore\bin\`) and add it to your `PATH`.
3. Run `dropboxignore install`.

## Platform support

- **Windows 10 / 11** — first-class (v0.1). Uses NTFS Alternate Data Streams.
- **Linux** — first-class (v0.2). Uses `user.com.dropbox.ignored` xattrs. Tested on Ubuntu 22.04 / 24.04. Requires a systemd user session.
- **macOS** — planned for v0.3. Dropbox on macOS uses a different attribute mechanism (Apple File Provider) that requires runtime detection — not yet implemented.

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
| `dropboxignore install` / `uninstall` | Register / remove the daemon with the platform's user-scoped service manager (Task Scheduler on Windows, systemd user unit on Linux). `uninstall --purge` also clears every existing marker (any stray marker on a `.dropboxignore` file itself is logged at `WARNING` before being cleared). |
| `dropboxignore daemon` | Run the watcher + hourly sweep in the foreground. Usually invoked by Task Scheduler. |
| `dropboxignore apply [PATH]` | One-shot reconcile of the whole Dropbox (or a subtree). |
| `dropboxignore status` | Is the daemon running? Last sweep counts, last error. |
| `dropboxignore list [PATH]` | Print every path currently bearing the ignore marker. |
| `dropboxignore explain PATH` | Which `.dropboxignore` rule (if any) matches the path? |

## Behaviour

- **Source of truth.** `.dropboxignore` files declare what is ignored. Removing a rule unignores the matching paths on the next reconcile. A path marked ignored via Dropbox's right-click menu but not matching any rule will be unignored.
- **Hybrid trigger.** The daemon reacts to filesystem events in real time *and* runs an hourly safety-net sweep. If the daemon is offline, an initial sweep at the next start catches any drift.
- **Multi-root.** Personal and Business Dropbox roots are discovered automatically from `%APPDATA%\Dropbox\info.json` (Windows) or `~/.dropbox/info.json` (Linux).

### Negations and Dropbox's ignore inheritance

Dropbox marks files and folders as ignored using xattrs. When a folder carries the ignore marker, Dropbox does not sync that folder or anything inside it — children inherit the ignored state regardless of whether they individually carry the marker. This matters for gitignore-style negation rules in your `.dropboxignore`.

If you write a negation whose target lives under a directory ignored by an earlier rule — the canonical case is `build/` followed by `!build/keep/` — the negation cannot take effect. Dropbox will ignore `build/keep/` because `build/` is ignored, no matter what xattr we put on the child. dropboxignore detects this at the moment you save the `.dropboxignore`, logs a WARNING naming both rules, and drops the conflicted negation from the active rule set.

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
| `DROPBOXIGNORE_DEBOUNCE_RULES_MS` | `100` | Debounce window for `.dropboxignore` file events. |
| `DROPBOXIGNORE_DEBOUNCE_DIRS_MS` | `0` | Debounce for directory-creation events (`0` = react immediately, no coalescing). |
| `DROPBOXIGNORE_DEBOUNCE_OTHER_MS` | `500` | Debounce for other file events. |
| `DROPBOXIGNORE_LOG_LEVEL` | `INFO` | Daemon log level. |
| `DROPBOXIGNORE_ROOT` | *(unset)* | Escape hatch for non-stock Dropbox installs: overrides `info.json` discovery and treats the given absolute path as the sole Dropbox root. If the path doesn't exist, a WARNING is logged and no roots are returned (so `dropboxignore apply` exits with "No Dropbox roots found"). |

Logs (rotated, 25 MB total):
- Windows — `%LOCALAPPDATA%\dropboxignore\daemon.log`.
- Linux — two sinks, same records. The rotating file at `$XDG_STATE_HOME/dropboxignore/daemon.log` (fallback `~/.local/state/dropboxignore/daemon.log`) is authoritative for offline debugging and bug-report bundling; `journalctl --user -u dropboxignore.service` surfaces the same records via systemd-journald for live tailing and cross-service filtering.

State:
- Windows — `%LOCALAPPDATA%\dropboxignore\state.json`.
- Linux — `$XDG_STATE_HOME/dropboxignore/state.json` (fallback `~/.local/state/dropboxignore/state.json`). Installs that pre-date the XDG move are read transparently from the legacy `~/AppData/Local/dropboxignore/state.json` for one release, with a WARNING; the next daemon write persists to the XDG path.

## License

MIT — see [LICENSE](LICENSE).

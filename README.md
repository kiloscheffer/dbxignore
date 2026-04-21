# dropboxignore

Hierarchical `.dropboxignore` files for Dropbox on Windows. Drop a `.dropboxignore` into any folder under your Dropbox root and matching paths get the `com.dropbox.ignored` NTFS alternate data stream set automatically — no more `node_modules/` cluttering your sync.

## Requirements

- Windows 10 or 11 (NTFS)
- Dropbox desktop client installed
- Either Python ≥ 3.11 with [`uv`](https://docs.astral.sh/uv/) **or** the pre-built `.exe` from a GitHub Release

## Install (source)

```powershell
uv tool install git+https://github.com/kiloscheffer/dropboxignore
dropboxignore install
```

`dropboxignore install` registers a Task Scheduler entry that launches the daemon (`pythonw -m dropboxignore daemon`) at every user logon.

## Install (.exe)

1. Download `dropboxignore.exe` and `dropboxignored.exe` from the latest [Release](https://github.com/kiloscheffer/dropboxignore/releases).
2. Place both in a stable directory (e.g. `%LOCALAPPDATA%\dropboxignore\bin\`) and add it to your `PATH`.
3. Run `dropboxignore install`.

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
| `dropboxignore install` / `uninstall` | Register / remove the Task Scheduler entry. `uninstall --purge` also clears every existing marker (any stray marker on a `.dropboxignore` file itself is logged at `WARNING` before being cleared). |
| `dropboxignore daemon` | Run the watcher + hourly sweep in the foreground. Usually invoked by Task Scheduler. |
| `dropboxignore apply [PATH]` | One-shot reconcile of the whole Dropbox (or a subtree). |
| `dropboxignore status` | Is the daemon running? Last sweep counts, last error. |
| `dropboxignore list [PATH]` | Print every path currently bearing the ignore marker. |
| `dropboxignore explain PATH` | Which `.dropboxignore` rule (if any) matches the path? |

## Behaviour

- **Source of truth.** `.dropboxignore` files declare what is ignored. Removing a rule unignores the matching paths on the next reconcile. A path marked ignored via Dropbox's right-click menu but not matching any rule will be unignored.
- **Hybrid trigger.** The daemon reacts to filesystem events in real time *and* runs an hourly safety-net sweep. If the daemon is offline, an initial sweep at the next start catches any drift.
- **Multi-root.** Personal and Business Dropbox roots are discovered automatically from `%APPDATA%\Dropbox\info.json`.

## Configuration

Environment variables read at daemon startup:

| Variable | Default | Purpose |
|---|---|---|
| `DROPBOXIGNORE_DEBOUNCE_RULES_MS` | `100` | Debounce window for `.dropboxignore` file events. |
| `DROPBOXIGNORE_DEBOUNCE_DIRS_MS` | `0` | Debounce for directory-creation events (`0` = react immediately, no coalescing). |
| `DROPBOXIGNORE_DEBOUNCE_OTHER_MS` | `500` | Debounce for other file events. |
| `DROPBOXIGNORE_LOG_LEVEL` | `INFO` | Daemon log level. |

Logs: `%LOCALAPPDATA%\dropboxignore\daemon.log` (rotated, 25 MB total).
State: `%LOCALAPPDATA%\dropboxignore\state.json`.

## License

MIT — see [LICENSE](LICENSE).

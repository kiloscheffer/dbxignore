#!/usr/bin/env bash
# End-to-end smoke test for dbxignore on a headless Ubuntu VPS.
#
# Runs as a regular user (not root). Installs Dropbox headlessly, pauses for
# the tester to authorize the device in a browser, installs dbxignore from
# PyPI (or DBXIGNORE_INSTALL_SPEC), exercises CLI + reconcile + daemon
# surface, then uninstalls dbxignore (and optionally Dropbox).
#
# Usage:
#   bash manual-test-ubuntu-vps.sh                  # default: PyPI, keep Dropbox
#   bash manual-test-ubuntu-vps.sh --cleanup-dropbox
#   DBXIGNORE_INSTALL_SPEC='dbxignore==<version>' bash manual-test-ubuntu-vps.sh
#   DBXIGNORE_INSTALL_SPEC='git+https://github.com/kiloscheffer/dbxignore.git@<tag>' bash manual-test-ubuntu-vps.sh
#
# Exits non-zero if any check fails. Prints a PASS/FAIL summary.

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DBXIGNORE_INSTALL_SPEC="${DBXIGNORE_INSTALL_SPEC:-dbxignore}"
CLEANUP_DROPBOX=0
TEST_SUBDIR="dbxignore-test"
DROPBOXD_LOG="$(mktemp -t dropboxd.XXXXXX.log)"
DROPBOXD_PID=""
# Mirror src/dbxignore/state.py::user_state_dir on Linux: XDG_STATE_HOME
# wins, with $HOME/.local/state as fallback. Hardcoding the fallback (as
# elsewhere in this script for state.json/daemon.log probes) silently
# misses when the tester has XDG_STATE_HOME set — slow-sweep marker
# would seed at one path and the daemon would read from another.
DBXIGNORE_STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/dbxignore"

for arg in "$@"; do
    case "$arg" in
        --cleanup-dropbox) CLEANUP_DROPBOX=1 ;;
        -h|--help)
            sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

if [ -t 1 ]; then
    R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[34m'; D=$'\033[2m'; X=$'\033[0m'
else
    R=""; G=""; Y=""; B=""; D=""; X=""
fi

PASS_COUNT=0
FAIL_COUNT=0
FAIL_NAMES=()

phase()  { echo; echo "${B}=== $* ===${X}"; }
note()   { echo "${D}  $*${X}"; }
pass()   { PASS_COUNT=$((PASS_COUNT+1)); echo "  ${G}PASS${X} $*"; }
fail()   { FAIL_COUNT=$((FAIL_COUNT+1)); FAIL_NAMES+=("$*"); echo "  ${R}FAIL${X} $*"; }
abort()  { echo "${R}ABORT:${X} $*" >&2; exit 1; }

check() {
    # check "name" "command..." — runs command, PASS on exit 0 else FAIL
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then pass "$name"; else fail "$name"; fi
}

cleanup() {
    if [ -n "$DROPBOXD_PID" ] && kill -0 "$DROPBOXD_PID" 2>/dev/null; then
        echo
        note "stopping dropboxd (pid $DROPBOXD_PID)..."
        kill "$DROPBOXD_PID" 2>/dev/null || true
        wait "$DROPBOXD_PID" 2>/dev/null || true
    fi
    if [ "$CLEANUP_DROPBOX" -eq 1 ]; then
        note "removing Dropbox state per --cleanup-dropbox..."
        rm -rf ~/.dropbox ~/.dropbox-dist 2>/dev/null || true
        if [ -n "${DROPBOX_DIR:-}" ] && [ -d "$DROPBOX_DIR" ]; then
            rm -rf "$DROPBOX_DIR" 2>/dev/null || true
        fi
    fi
    rm -f "$DROPBOXD_LOG" 2>/dev/null || true
    # Belt-and-suspenders: remove the slow-sweep test marker if a script
    # crash skipped the in-phase cleanup. Honoring a stale marker on a future
    # install would silently pad every initial sweep.
    rm -f "$DBXIGNORE_STATE_DIR/_test_slow_sweep" 2>/dev/null || true
    # Phase 4.5 case 4s leaves recovery sentinels set across its destructive
    # section; honor them on abort. No-op when sentinels are unset (the
    # in-phase restore ran successfully). Function is defined in
    # `_phase_extended_cli.sh`, sourced below before this trap fires.
    if declare -F _phase_4s_recover_state_json >/dev/null 2>&1; then
        _phase_4s_recover_state_json
    fi
}
trap cleanup EXIT

xattr_get() {
    # xattr_get <path> -> "1" / "0" / "missing" via Python stdlib
    python3 - "$1" <<'PY'
import os, sys
p = sys.argv[1]
try:
    v = os.getxattr(p, "user.com.dropbox.ignored")
    print(v.decode().strip() or "1")
except OSError:
    print("missing")
PY
}

assert_xattr_set() {
    local p="$1" name="$2"
    local v; v="$(xattr_get "$p")"
    if [ "$v" = "1" ]; then pass "$name (xattr=$v)"; else fail "$name (xattr=$v on $p)"; fi
}

assert_xattr_unset() {
    local p="$1" name="$2"
    local v; v="$(xattr_get "$p")"
    if [ "$v" = "missing" ]; then pass "$name"; else fail "$name (unexpected xattr=$v on $p)"; fi
}

# assert_grep <file> <pattern> <name> — PASS if pattern is in file, else FAIL
# and dump the file content as a note. Used by Phase 4.5's many "did the
# command emit the expected stderr/stdout text?" assertions.
assert_grep() {
    local file="$1" pattern="$2" name="$3"
    if grep -q -- "$pattern" "$file" 2>/dev/null; then
        pass "$name"
    else
        note "$(cat "$file" 2>/dev/null || echo '(file missing)')"
        fail "$name"
    fi
}

# ---------------------------------------------------------------------------
# Phase 0 — pre-flight
# ---------------------------------------------------------------------------

phase_preflight() {
    phase "Phase 0 — pre-flight"

    [ "$EUID" -ne 0 ] || abort "must run as a regular user, not root (Dropbox refuses to run as root)"

    if [ -r /etc/os-release ]; then
        . /etc/os-release
        note "OS: ${PRETTY_NAME:-unknown}"
        case "${ID:-}" in
            ubuntu|debian) ;;
            *) note "${Y}WARNING:${X} not Ubuntu/Debian — may need adjustments" ;;
        esac
    fi

    command -v wget >/dev/null || abort "wget required"
    command -v python3 >/dev/null || abort "python3 required"

    local pyver; pyver="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
    note "Python $pyver"
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' \
        || abort "Python >= 3.11 required (got $pyver)"

    if ! command -v uv >/dev/null; then
        note "installing uv via astral installer..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
    note "uv: $(uv --version)"

    if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
        note "${Y}WARNING:${X} XDG_RUNTIME_DIR unset — systemctl --user may not work over this session"
    fi

    # Inotify watch budget — the daemon's watchdog observer needs one watch
    # per directory under ~/Dropbox. VPS kernels sometimes ship with the
    # 8192 default, which is easily exhausted by a synced Dropbox tree.
    local watches; watches="$(cat /proc/sys/fs/inotify/max_user_watches 2>/dev/null || echo 0)"
    local instances; instances="$(cat /proc/sys/fs/inotify/max_user_instances 2>/dev/null || echo 0)"
    note "inotify limits: max_user_watches=$watches max_user_instances=$instances"
    if [ "$watches" -lt 65536 ] || [ "$instances" -lt 256 ]; then
        echo
        echo "${Y}WARNING:${X} inotify limits are low — phase 5 (daemon) will likely fail with 'inotify watch limit reached'."
        echo "  Recommended (one-shot):"
        echo "    sudo sysctl fs.inotify.max_user_watches=524288"
        echo "    sudo sysctl fs.inotify.max_user_instances=512"
        echo "  Persist across reboots:"
        echo "    echo 'fs.inotify.max_user_watches=524288'   | sudo tee /etc/sysctl.d/40-inotify.conf"
        echo "    echo 'fs.inotify.max_user_instances=512'    | sudo tee -a /etc/sysctl.d/40-inotify.conf"
        echo
        read -r -p "  Continue anyway? [y/N] " yn
        case "$yn" in
            y|Y|yes|YES) ;;
            *) abort "aborted by user — bump inotify limits and re-run" ;;
        esac
    fi
}

# ---------------------------------------------------------------------------
# Phase 1 — Dropbox headless install
# ---------------------------------------------------------------------------

phase_dropbox_install() {
    phase "Phase 1 — Dropbox headless install"

    if [ ! -d ~/.dropbox-dist ]; then
        note "downloading Dropbox tarball (~50 MB)..."
        ( cd ~ && wget -qO- "https://www.dropbox.com/download?plat=lnx.x86_64" | tar xzf - )
    else
        note "~/.dropbox-dist already exists, skipping download"
    fi
    [ -x ~/.dropbox-dist/dropboxd ] || abort "dropboxd binary missing after extract"

    note "starting dropboxd (logging to $DROPBOXD_LOG)..."
    ( ~/.dropbox-dist/dropboxd >"$DROPBOXD_LOG" 2>&1 ) &
    DROPBOXD_PID=$!

    if [ -f ~/.dropbox/info.json ]; then
        note "info.json already present — device appears linked"
    else
        echo
        echo "${Y}>>> ACTION REQUIRED <<<${X}"
        echo "Watching dropboxd output for the linking URL..."
        local url=""
        for _ in $(seq 1 30); do
            url="$(grep -oE 'https://www\.dropbox\.com/cli_link_nonce\?[^ ]*' "$DROPBOXD_LOG" | head -1 || true)"
            [ -n "$url" ] && break
            sleep 1
        done
        if [ -z "$url" ]; then
            note "no linking URL in dropboxd output yet — printing log so far:"
            sed 's/^/    /' "$DROPBOXD_LOG"
            abort "could not find linking URL within 30s"
        fi
        echo
        echo "    Open this URL in a browser, sign in, and authorize the device:"
        echo
        echo "    ${B}${url}${X}"
        echo
        read -r -p "    Press ENTER once you've completed the browser authorization... " _
    fi

    note "waiting for ~/.dropbox/info.json (up to 120s)..."
    for _ in $(seq 1 120); do
        [ -f ~/.dropbox/info.json ] && break
        sleep 1
    done
    [ -f ~/.dropbox/info.json ] || abort "info.json never appeared — auth probably did not complete"
    pass "Dropbox device linked (info.json present)"

    DROPBOX_DIR="$(python3 -c "
import json, os, sys
with open(os.path.expanduser('~/.dropbox/info.json')) as f:
    d = json.load(f)
acct = d.get('personal') or d.get('business') or next(iter(d.values()))
print(acct['path'])
")"
    note "Dropbox folder: $DROPBOX_DIR"

    note "waiting for Dropbox folder to appear (up to 60s)..."
    for _ in $(seq 1 60); do
        [ -d "$DROPBOX_DIR" ] && break
        sleep 1
    done
    [ -d "$DROPBOX_DIR" ] || abort "Dropbox folder $DROPBOX_DIR never created"
    pass "Dropbox folder present at $DROPBOX_DIR"

    # Stop dropboxd now that we have info.json + the folder. Subsequent phases
    # don't need active sync; running dropboxd interferes by:
    #   - syncing old test fixtures back from cloud while we write new ones
    #   - extending the daemon's initial-walk wall-clock as it competes for I/O
    # Pre-existing test fixtures may already exist in the cloud from prior runs;
    # killing dropboxd here freezes them server-side until the user restarts it.
    note "stopping dropboxd (sync interference; restart manually after the script if desired)..."
    if [ -n "$DROPBOXD_PID" ] && kill -0 "$DROPBOXD_PID" 2>/dev/null; then
        kill "$DROPBOXD_PID" 2>/dev/null || true
        wait "$DROPBOXD_PID" 2>/dev/null || true
        DROPBOXD_PID=""
    fi
    # Also catch any sibling dropbox processes the tarball spawned.
    pkill -f "$HOME/.dropbox-dist/dropbox" 2>/dev/null || true
    sleep 1
    pass "dropboxd stopped"
}

# ---------------------------------------------------------------------------
# Phase 2 — install dbxignore
# ---------------------------------------------------------------------------

phase_dbxignore_install() {
    phase "Phase 2 — install dbxignore (spec: $DBXIGNORE_INSTALL_SPEC)"

    local uv_tool_list
    local uv_tool_list_rc=0
    uv_tool_list="$(uv tool list 2>/dev/null)" || uv_tool_list_rc=$?
    if [ "$uv_tool_list_rc" -eq 0 ] && printf '%s\n' "$uv_tool_list" | grep -q '^dbxignore '; then
        note "dbxignore already installed via uv tool — uninstalling first for a clean test"
        # Best-effort CLI teardown. `dbxignore uninstall` stops the
        # systemd user unit and waits for the daemon to exit
        # (install/linux_systemd.py:remove_service). Plain `uninstall`
        # (not `--purge`) preserves ignore markers outside this script's
        # test subdir. The systemctl + rm lines below cover the
        # broken-CLI case (interrupted earlier install).
        dbxignore uninstall >/dev/null 2>&1 || true
        systemctl --user disable --now dbxignore.service >/dev/null 2>&1 || true
        rm -f "$HOME/.config/systemd/user/dbxignore.service"
        systemctl --user daemon-reload >/dev/null 2>&1 || true

        # Independently verify the daemon is dead before proceeding. The
        # in-CLI wait can be defeated by a stale state.json (daemon_pid
        # pointing at a different/non-existent PID makes the poll break
        # immediately). On POSIX the orphaned-daemon doesn't lock files
        # (unlink-while-open is fine), but it will continue writing to
        # state.json and reacting to filesystem events during the test.
        # Match `dbxignore daemon` specifically to avoid hitting the test
        # script's own process (which contains "dbxignore" in its path).
        deadline=$(( $(date +%s) + 30 ))
        while [ "$(date +%s)" -lt "$deadline" ]; do
            if ! pgrep -f 'dbxignore daemon' >/dev/null 2>&1; then break; fi
            sleep 0.5
        done
        if pgrep -f 'dbxignore daemon' >/dev/null 2>&1; then
            note "daemon did not exit within 30s; force-killing"
            pkill -9 -f 'dbxignore daemon' >/dev/null 2>&1 || true
            sleep 0.5
        fi

        uv tool uninstall dbxignore >/dev/null 2>&1 || true

        # Force-remove venv residue (defensive; rare on POSIX). Guard
        # against empty `uv tool dir` (would become `rm -rf /dbxignore`).
        if tool_dir="$(uv tool dir 2>/dev/null)" && [ -n "$tool_dir" ] && [ -d "$tool_dir/dbxignore" ]; then
            rm -rf "$tool_dir/dbxignore"
        fi

        # `uv tool uninstall` removes the trampoline shims at $(uv tool
        # dir --bin), but only if uv still recognizes the tool. Removing
        # the venv out from under uv orphans the shims and the next
        # `uv tool install` errors with "Executables already exist".
        if bin_dir="$(uv tool dir --bin 2>/dev/null)" && [ -n "$bin_dir" ] && [ -d "$bin_dir" ]; then
            rm -f "$bin_dir/dbxignore" "$bin_dir/dbxignorew"
        fi
    else
        # Orphan-install detection. A prior `uv tool uninstall` that failed
        # mid-cleanup can leave the venv at $(uv tool dir)/dbxignore behind
        # even though `uv tool list` no longer shows dbxignore. The next
        # `uv tool install` then does an incremental update (only changed
        # packages reinstall; others survive, producing a hybrid venv with
        # subtly broken C-extension state). Detect the orphan venv here
        # and clean it up so the next install is fresh.
        #
        # State machine:
        #
        # - venv exists AND no shims at $(uv tool dir --bin)
        #     → Auto-recoverable case. Daemon-kill + service-unit
        #       teardown + venv removal. Mirrors the known-install
        #       teardown above, minus the `dbxignore uninstall` CLI
        #       call. Daemon-kill is defensive: POSIX unlink-while-open
        #       lets a daemon process predating the partial uninstall
        #       survive venv removal, so it'd keep writing state.json /
        #       holding the singleton lock during the rest of the test.
        #
        # - shims exist (with or without venv)
        #     → Ambiguous origin: `uv tool dir --bin` commonly resolves
        #       to $HOME/.local/bin (or another shared user bin dir) that
        #       also hosts pip/pipx binaries. Even when the venv is uv's,
        #       the shims could be from a competing install that landed
        #       at the same paths before the uv venv was created (or
        #       after a partial uv uninstall). Auto-removing would
        #       silently break the tester's other install; leaving them
        #       in place lets `uv tool install` fail later with
        #       "Executables already exist" (uv doesn't overwrite shims
        #       it doesn't recognize). Abort here with a diagnostic
        #       listing the paths and resolution options instead.
        #
        # - nothing exists → no-op (fresh install path).
        local orphan_tool_dir orphan_bin_dir orphan_venv
        local -a orphan_shims=()
        orphan_tool_dir="$(uv tool dir 2>/dev/null)" || orphan_tool_dir=""
        orphan_bin_dir="$(uv tool dir --bin 2>/dev/null)" || orphan_bin_dir=""
        orphan_venv="${orphan_tool_dir:+$orphan_tool_dir/dbxignore}"
        if [ -n "$orphan_bin_dir" ]; then
            for exe in dbxignore dbxignorew; do
                if [ -e "$orphan_bin_dir/$exe" ]; then
                    orphan_shims+=("$orphan_bin_dir/$exe")
                fi
            done
        fi
        local venv_exists=0
        if [ -n "$orphan_venv" ] && [ -d "$orphan_venv" ]; then venv_exists=1; fi
        if [ "${#orphan_shims[@]}" -gt 0 ]; then
            # Ambiguous origin in shared bin dir. Don't auto-act.
            local shim_list venv_clause
            shim_list="$(printf '  %s\n' "${orphan_shims[@]}")"
            if [ "$venv_exists" -eq 1 ]; then
                venv_clause="An orphan uv tool venv ALSO exists at $orphan_venv — if the shims are confirmed uv's (option (a) below), remove the venv too with \`rm -rf $orphan_venv\`."
            else
                venv_clause="The matching uv tool venv at $orphan_venv does not exist, so the shims may have outlived it."
            fi
            abort "$(cat <<EOF
found dbxignore/dbxignorew shim(s) in the uv tool bin dir:

$shim_list

$venv_clause

Possible origins:

  (a) a previous uv tool install/uninstall that left shims uv didn't track
      anymore (e.g. uv tool install created the venv but failed at shim-write
      because something was already there; or uv tool uninstall failed
      partway through). Safe to remove the shims if confirmed.
  (b) a competing dbxignore install via pip, pipx, or another tool that writes
      to the same bin dir ($orphan_bin_dir).

Auto-removing would silently break case (b). Manually verify and clean up:

  pipx uninstall dbxignore           # if pipx-installed (case b)
  pip uninstall dbxignore            # if pip-installed (case b)
  rm -f ${orphan_shims[*]}    # if confirmed uv (case a)

then re-run this script.
EOF
)"
        fi
        if [ "$venv_exists" -eq 1 ]; then
            # venv-only orphan: auto-recoverable case.
            note "${Y}WARNING:${X} orphan install detected — prior uv tool uninstall partially failed"

            # Service-unit teardown (best-effort: no-ops when nothing exists,
            # which is the common case in the orphan state since `dbxignore
            # uninstall` may have run earlier and removed the unit before the
            # venv cleanup failed).
            systemctl --user disable --now dbxignore.service >/dev/null 2>&1 || true
            rm -f "$HOME/.config/systemd/user/dbxignore.service"
            systemctl --user daemon-reload >/dev/null 2>&1 || true

            # Poll-for-exit then force-kill — same shape as the known-install
            # branch above. Without this, the orphan daemon keeps running
            # against a deleted venv (loaded modules survive) and interferes
            # with the rest of the test by writing state.json and holding
            # the singleton lock at $DBXIGNORE_STATE_DIR/daemon.lock.
            if pgrep -f 'dbxignore daemon' >/dev/null 2>&1; then
                note "  found running daemon process; waiting up to 30s for exit"
                deadline=$(( $(date +%s) + 30 ))
                while [ "$(date +%s)" -lt "$deadline" ]; do
                    if ! pgrep -f 'dbxignore daemon' >/dev/null 2>&1; then break; fi
                    sleep 0.5
                done
                if pgrep -f 'dbxignore daemon' >/dev/null 2>&1; then
                    note "  daemon did not exit within 30s; force-killing"
                    pkill -9 -f 'dbxignore daemon' >/dev/null 2>&1 || true
                    sleep 0.5
                fi
            fi

            note "  removing orphan venv at $orphan_venv"
            rm -rf "$orphan_venv"
            note "orphan cleanup complete; proceeding with fresh install"
        fi
    fi

    clean_uv_cache_for_dbxignore_if_local

    uv tool install --link-mode=copy "$DBXIGNORE_INSTALL_SPEC"
    export PATH="$HOME/.local/bin:$PATH"

    command -v dbxignore  >/dev/null && pass "dbxignore on PATH"  || fail "dbxignore on PATH"
}

# ---------------------------------------------------------------------------
# Phase 3 — CLI surface
# ---------------------------------------------------------------------------

phase_cli_surface() {
    phase "Phase 3 — CLI surface"

    dbxignore  --version 2>&1 | grep -qE '^dbxignore, version '  && pass "dbxignore --version"  || fail "dbxignore --version"

    # Strip ANSI: rich-click colorizes the Usage line on POSIX TTYs (TERM
    # is set), but not on Windows. Mirror the Python test's substring shape
    # in tests/test_cli_entrypoints.py — assert "daemon" + "[OPTIONS]"
    # are present and "COMMAND" / "[ARGS]" are absent (so a regression that
    # accidentally adds subcommands to the daemon subcommand surfaces here).
    # The daemon is reached via `dbxignore daemon`.
    local plain usage_line
    plain="$(dbxignore daemon --help 2>&1 | sed $'s/\e\\[[0-9;]*m//g')"
    usage_line="$(printf '%s\n' "$plain" | grep -m1 'Usage:' || true)"
    if [[ "$usage_line" == *"daemon"* ]] \
       && [[ "$usage_line" == *"[OPTIONS]"* ]] \
       && [[ "$usage_line" != *"COMMAND"* ]] \
       && [[ "$usage_line" != *"[ARGS]"* ]]; then
        pass "dbxignore daemon --help has clean Usage line"
    else
        fail "dbxignore daemon --help Usage line: $usage_line"
    fi

    local help_out
    local help_rc=0
    help_out="$(dbxignore --help 2>&1)" || help_rc=$?
    if [ "$help_rc" -eq 0 ] && printf '%s\n' "$help_out" | grep -q 'apply'; then
        pass "dbxignore --help lists subcommands"
    else
        fail "dbxignore --help missing subcommands (rc=$help_rc)"
    fi

    if dbxignore status >/tmp/dbxignore-status.out 2>&1; then
        pass "dbxignore status (rc=0)"
        note "$(head -3 /tmp/dbxignore-status.out)"
    else
        fail "dbxignore status (rc=$?)"
        sed 's/^/    /' /tmp/dbxignore-status.out
    fi

    dbxignore list >/dev/null 2>&1 && pass "dbxignore list (rc=0)" || fail "dbxignore list"
}

# ---------------------------------------------------------------------------
# Phase 4 — reconcile (apply)
# ---------------------------------------------------------------------------

phase_reconcile() {
    phase "Phase 4 — reconcile / apply"

    local T="$DROPBOX_DIR/$TEST_SUBDIR"
    rm -rf "$T"; mkdir -p "$T"

    # 4a. simple file rule
    note "4a — simple file rule (*.tmp)"
    echo '*.tmp' > "$T/.dropboxignore"
    : > "$T/foo.tmp"
    : > "$T/bar.txt"
    dbxignore apply "$T" --yes >/dev/null 2>&1 && pass "apply 4a (rc=0)" || fail "apply 4a"
    assert_xattr_set   "$T/foo.tmp" "4a — foo.tmp marked"
    assert_xattr_unset "$T/bar.txt" "4a — bar.txt unmarked"
    assert_xattr_unset "$T/.dropboxignore" "4a — .dropboxignore never marked"

    # 4b. dir rule + subtree pruning
    note "4b — dir rule + subtree pruning (cache/)"
    printf '*.tmp\ncache/\n' > "$T/.dropboxignore"
    mkdir -p "$T/cache/sub"
    : > "$T/cache/sub/file.txt"
    dbxignore apply "$T" --yes >/dev/null 2>&1 && pass "apply 4b" || fail "apply 4b"
    assert_xattr_set   "$T/cache"              "4b — cache/ marked"
    assert_xattr_unset "$T/cache/sub/file.txt" "4b — descendant unmarked (subtree pruned)"

    # 4c. rule removal clears markers
    note "4c — rule removal clears markers"
    printf 'cache/\n' > "$T/.dropboxignore"   # removed *.tmp
    dbxignore apply "$T" --yes >/dev/null 2>&1 && pass "apply 4c" || fail "apply 4c"
    assert_xattr_unset "$T/foo.tmp" "4c — foo.tmp cleared after rule removed"
    assert_xattr_set   "$T/cache"   "4c — cache/ still marked"

    # 4d. dropped negation: dir rule + descendant negation (the conflict the
    # detector flags — see rules_conflicts.py:168 for why file-glob negations
    # like *.log + !keep.log are NOT flagged).
    note "4d — dropped negation (dir rule + descendant negation)"
    rm -rf "$T"; mkdir -p "$T/build/keep"
    printf 'build/\n!build/keep/\n' > "$T/.dropboxignore"
    : > "$T/build/keep/inside.txt"
    dbxignore apply "$T" --yes >/dev/null 2>&1 && pass "apply 4d" || fail "apply 4d"
    assert_xattr_set   "$T/build"      "4d — build/ marked (parent dir rule wins)"
    assert_xattr_unset "$T/build/keep" "4d — descendant not visited (subtree pruned)"
    # Both rc=0 AND `[dropped]` matter: `cli.explain` for an ignored path
    # whose ancestor's negation got dropped is contracted to exit 0
    # (pinned by `test_explain_dropped_negation_path_still_exits_0`). Capture
    # rc separately from stdout so a regression to exit 1 + correct output
    # doesn't silently slip past.
    local explain_4d_out
    local explain_4d_rc=0
    explain_4d_out="$(dbxignore explain "$T/build/keep" 2>&1)" || explain_4d_rc=$?
    if [ "$explain_4d_rc" -eq 0 ] && printf '%s\n' "$explain_4d_out" | grep -qF '[dropped]'; then
        pass "4d — explain annotates dropped negation on build/keep/ (rc=0)"
    else
        note "explain rc=$explain_4d_rc, output:"
        printf '%s\n' "$explain_4d_out" | sed 's/^/    /'
        fail "4d — explain did not annotate dropped negation (rc=$explain_4d_rc)"
    fi

    # 4e. symlink WARNING (Linux refuses user.* xattrs on symlinks).
    # Use a sibling subdir so the parent .dropboxignore doesn't interfere.
    note "4e — symlink WARNING"
    local TS="$T/sym"
    mkdir -p "$TS"
    echo '*.log' > "$TS/.dropboxignore"
    : > "$TS/real.log"
    ln -sfn real.log "$TS/link.log"
    dbxignore apply "$T" --yes >/tmp/dbxignore-apply.out 2>&1 \
        && pass "apply 4e completes despite symlink" \
        || fail "apply 4e crashed"
    if grep -qiE 'WARN|symlink|permission|enotsup' /tmp/dbxignore-apply.out; then
        pass "4e — symlink path produced WARNING"
    else
        note "$(cat /tmp/dbxignore-apply.out)"
        note "(no warning emitted — kernel may have allowed the xattr; not a hard failure on all kernels)"
    fi
    assert_xattr_set "$TS/real.log" "4e — real file marked under sym/.dropboxignore *.log"

    # 4f. explain on a marked file returns the matching rule
    note "4f — explain returns matching rule"
    # rc=0 AND the matching rule both matter — pinned by
    # `test_explain_exits_0_when_ignored`.
    local explain_4f_out
    local explain_4f_rc=0
    explain_4f_out="$(dbxignore explain "$TS/real.log" 2>&1)" || explain_4f_rc=$?
    if [ "$explain_4f_rc" -eq 0 ] && printf '%s\n' "$explain_4f_out" | grep -q '\*\.log'; then
        pass "4f — explain cites *.log (rc=0)"
    else
        note "explain rc=$explain_4f_rc, output:"
        printf '%s\n' "$explain_4f_out" | sed 's/^/    /'
        fail "4f — explain did not cite *.log (rc=$explain_4f_rc)"
    fi
}

# ---------------------------------------------------------------------------
# Phase 4.5 — extended CLI surface (init, generate, apply variants, clear)
#
# Covers user-facing commands:
#   - dbxignore init scaffolds a starter .dropboxignore
#   - dbxignore generate translates a .gitignore byte-for-byte
#   - generate emits a stderr warning on dropped negations
#   - apply --dry-run previews without mutating
#   - apply prompts before mutating; --yes skips the prompt
#   - apply on already-converged state says "Nothing to apply"
#   - conflict detector — build/* + !build/keep/ not flagged
#   - dbxignore clear (basic; daemon-alive guard tested in phase 5)
# ---------------------------------------------------------------------------

# Sourced from scripts/_phase_extended_cli.sh — body byte-near-identical
# to manual-test-macos.sh's; extracted so Phase 4.5 additions only have to
# land in one place.
source "$(dirname "$0")/_phase_extended_cli.sh"

# ---------------------------------------------------------------------------
# Phase 5 — daemon
# ---------------------------------------------------------------------------

_dump_daemon_diagnostics() {
    note "tail of daemon.log (last 40 lines):"
    tail -n 40 "$DBXIGNORE_STATE_DIR/daemon.log" 2>/dev/null | sed 's/^/    /' || true
    note "journalctl for the unit (last 5 minutes):"
    journalctl --user -u dbxignore.service --since "5 minutes ago" --no-pager 2>/dev/null \
        | sed 's/^/    /' || true
    note "test-dir state:"
    ls -la "$1" 2>/dev/null | sed 's/^/    /' || true
}

phase_daemon() {
    phase "Phase 5 — daemon (systemd user unit + watchdog)"

    if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
        note "${Y}skipping phase 5 — XDG_RUNTIME_DIR not set${X}"
        return
    fi

    # Reset to a clean test dir BEFORE installing the daemon, so the daemon's
    # initial cache.load_root() reads a known rule set with no leftover phase-4
    # conflicts. (The watchdog observer comes online only AFTER the initial
    # sweep completes — events fired before that are missed, which is why
    # phase 5 has to start from a stable on-disk state.)
    local T="$DROPBOX_DIR/$TEST_SUBDIR"
    rm -rf "$T"; mkdir -p "$T"
    printf '*.tmp\n' > "$T/.dropboxignore"

    # Sanity-check inotify capacity vs the actual Dropbox subtree size.
    # The watchdog observer needs ~1 watch per directory under DROPBOX_DIR.
    local watches dir_count
    watches="$(cat /proc/sys/fs/inotify/max_user_watches 2>/dev/null || echo 0)"
    dir_count="$(find "$DROPBOX_DIR" -type d 2>/dev/null | wc -l)"
    note "watch budget: $dir_count dirs in $DROPBOX_DIR vs $watches available"
    if [ "$watches" -lt "$dir_count" ]; then
        echo "${Y}WARNING:${X} watch limit ($watches) is below dir count ($dir_count) — phase 5 will fail with ENOSPC."
        echo "  Run: sudo sysctl fs.inotify.max_user_watches=524288"
    fi

    # Slow-sweep determinism. Seed a 15s pad so 5a's 5-iteration state=starting
    # poll deterministically catches the transient state and 5f's 180s poll
    # deterministically observes the transition to running, regardless of the
    # watched-tree size. The daemon logs WARNING when it honors this; cleanup
    # at the end of phase 5 removes it before phase 6.
    mkdir -p "$DBXIGNORE_STATE_DIR"
    printf '15\n' > "$DBXIGNORE_STATE_DIR/_test_slow_sweep"
    note "5 — slow-sweep marker seeded: 15s pad on initial sweep"

    dbxignore install >/tmp/dbxignore-install.out 2>&1 \
        && pass "dbxignore install (rc=0)" \
        || { fail "dbxignore install"; sed 's/^/    /' /tmp/dbxignore-install.out; return; }

    # install verbosity defaults — default WARNING quiets install-backend INFO
    # chatter; the click.echo summary line still surfaces.
    grep -q "Installed dbxignore daemon service" /tmp/dbxignore-install.out \
        && pass "install — click.echo summary present" \
        || fail "install — click.echo summary missing"
    if ! grep -q "^INFO " /tmp/dbxignore-install.out; then
        pass "install — no INFO chatter at default level"
    else
        fail "install — INFO chatter leaked at default level"
    fi

    [ -f "$HOME/.config/systemd/user/dbxignore.service" ] \
        && pass "service unit file written" \
        || fail "service unit file missing"

    # install verb-form — unit invokes `dbxignore daemon`
    grep -q "^ExecStart=.*dbxignore daemon" \
        "$HOME/.config/systemd/user/dbxignore.service" \
        && pass "ExecStart uses unified 'dbxignore daemon'" \
        || fail "ExecStart does not invoke 'dbxignore daemon'"

    sleep 2
    if systemctl --user is-active dbxignore >/dev/null 2>&1; then
        pass "systemd unit active"
    else
        fail "systemd unit not active ($(systemctl --user is-active dbxignore 2>&1))"
    fi

    # Wait for the daemon to finish initial setup and bring its watchdog
    # observer online. We poll for a sentinel INFO log line ("watching roots")
    # rather than sleeping for a fixed duration — daemon startup time scales
    # with the size of the watched tree (~50s for ~27k dirs in testing on
    # a personal Dropbox account; 180s is enough headroom for ~100k dirs).
    note "waiting up to 180s for daemon initial sweep to complete and observer to come online..."
    note "  (sweep cost is proportional to ~/Dropbox subdir count: $dir_count)"
    local ready=0
    for _ in $(seq 1 180); do
        if grep -q 'watching roots' "$DBXIGNORE_STATE_DIR/daemon.log" 2>/dev/null; then
            ready=1; break
        fi
        sleep 1
    done
    if [ "$ready" -eq 1 ]; then
        pass "daemon observer online (watching roots logged)"
    else
        fail "daemon never logged 'watching roots' within 180s"
        _dump_daemon_diagnostics "$T"
        return
    fi

    # 5a — opportunistic state=starting capture. Probed AFTER the
    # watching-roots break: daemon.run logs 'watching roots' BEFORE writing
    # the early state.json (daemon.py:663 then :678), so an in-loop probe
    # races state.write and almost always misses. Post-readiness, state.json
    # appears within microseconds; on a real ~/Dropbox tree state=starting is
    # observable for the ~50s sweep window. On a small test tree the worker
    # can finish before we probe — that's the small-tree caveat the note
    # path covers.
    local saw_starting=0
    for _ in 1 2 3 4 5; do
        if dbxignore status --summary 2>/dev/null | grep -q '^state=starting pid='; then
            saw_starting=1; break
        fi
        sleep 1
    done
    if [ "$saw_starting" -eq 1 ]; then
        pass "5a — observed state=starting via --summary post-readiness"
    else
        note "5a — state=starting not observed within 5s post-readiness (small tree where sweep finished, or state.json not yet written); 5f still pins state=running"
    fi

    # 5a-post — gate watchdog tests on state=running (cache populated).
    # cache.load_root runs in _initial_sweep_worker, NOT the main thread
    # (daemon.py:638). When the slow-sweep marker pads the worker, RuleCache
    # stays empty until the pad expires AND load_root finishes — watchdog
    # events arriving during that window dispatch against match()=False, so
    # 5b would observe an unmarked file even though the rule applies. Even
    # without the marker, a slow sweep on a real ~/Dropbox tree could race
    # 5b's 8-second create-and-check window — this gate makes the test
    # deterministic in both cases.
    note "5a-post — waiting up to 180s for state=running (cache populated)"
    local cache_ready=0
    for _ in $(seq 1 180); do
        if dbxignore status --summary 2>/dev/null | grep -qE '^state=running pid='; then
            cache_ready=1; break
        fi
        sleep 1
    done
    if [ "$cache_ready" -eq 1 ]; then
        pass "5a-post — cache populated; safe to exercise watchdog events"
    else
        fail "5a-post — state=running never reached within 180s"
        _dump_daemon_diagnostics "$T"
        return
    fi

    # 5b — watchdog reacts to a new file (created AFTER observer is live)
    note "5b — watchdog reacts to new file"
    : > "$T/watch-me.tmp"
    sleep 6                                           # OTHER debounce 500ms + reconcile + slack
    local v; v="$(xattr_get "$T/watch-me.tmp")"
    if [ "$v" = "1" ]; then
        pass "5b — daemon marked new *.tmp file via watchdog"
    else
        fail "5b — daemon did not mark new *.tmp file (xattr=$v)"
        _dump_daemon_diagnostics "$T"
    fi

    # 5c — .dropboxignore reload picks up new rule
    note "5c — .dropboxignore reload"
    : > "$T/freshrule.dat"
    sleep 1
    printf '*.tmp\n*.dat\n' > "$T/.dropboxignore"
    sleep 6                                           # RULES debounce 100ms + reload + reconcile
    v="$(xattr_get "$T/freshrule.dat")"
    if [ "$v" = "1" ]; then
        pass "5c — daemon picked up new rule and marked existing file"
    else
        fail "5c — daemon did not mark file under reloaded rule (xattr=$v)"
        _dump_daemon_diagnostics "$T"
    fi

    # 5d — DIR_CREATE bypass — newly created dir matching a rule
    # should be marked synchronously without waiting the OTHER debounce.
    # The bypass calls reconcile_subtree directly from the watchdog handler,
    # so even a tight poll (sub-second) should see the marker. Mirrored from
    # the macOS script for cross-platform parity.
    note "5d — DIR_CREATE bypass for matched directory"
    printf '*.tmp\n*.dat\nbuild_*/\n' > "$T/.dropboxignore"
    sleep 6                                           # let the rule reload settle
    mkdir -p "$T/build_x"
    sleep 2                                           # short wait — bypass shouldn't need OTHER debounce
    v="$(xattr_get "$T/build_x")"
    if [ "$v" = "1" ]; then
        pass "5d — DIR_CREATE bypass marked build_x/ within 2s"
    else
        fail "5d — DIR_CREATE bypass did not mark build_x/ within 2s (xattr=$v)"
        _dump_daemon_diagnostics "$T"
    fi

    # 5e — clear refuses while daemon is alive; --force overrides.
    note "5e — clear refuses while daemon alive"
    if dbxignore clear "$T" --yes >/tmp/dbx-clear-alive.out 2>&1; then
        fail "5e — clear should have refused while daemon alive"
        sed 's/^/    /' /tmp/dbx-clear-alive.out
    else
        pass "5e — clear exited non-zero (refused)"
    fi
    assert_grep /tmp/dbx-clear-alive.out 'daemon is running' "5e — refusal message names the daemon"
    # Scope the --force clear to a single file (freshrule.dat, marked in 5c)
    # rather than the whole tree: a tree-wide clear here would also clear
    # watch-me.tmp's marker, and Phase 6's "uninstall — markers retained on
    # watch-me.tmp" assertion would then fail vacuously (the marker is gone
    # before uninstall even runs). The override behavior is demonstrated
    # identically on a single-file target.
    if dbxignore clear "$T/freshrule.dat" --force --yes >/dev/null 2>&1; then
        pass "5e — clear --force overrides daemon-alive guard"
    else
        fail "5e — clear --force did not override the guard"
    fi

    # 5f — post-sweep status surface. --summary returns the full state=running
    # field set; human path emits the 'daemon: running' line distinct from
    # the 'daemon: starting (initial sweep in progress)' branch.
    #
    # The daemon marks itself ready (and logs 'watching roots') BEFORE the
    # initial sweep completes — so the watching-roots poll above is NOT a
    # sweep-complete sentinel. On a real ~/Dropbox tree the sweep can still
    # be running when 5f probes, in which case --summary correctly emits
    # 'state=starting pid=N' (truncated form). Poll for state=running for up
    # to 180s to absorb the transition — matches the watching-roots-wait
    # headroom above (~50s for 27k dirs; 180s sized for ~100k dirs). Each
    # iteration also pays one --summary subprocess invocation, so wall-clock
    # can drift somewhat past 180s on slow hosts; that's acceptable for a
    # manual smoke test.
    note "5f — status --summary post-sweep + human 'daemon: running' line"
    local sum_late=""
    local sum_pattern='^state=running pid=[0-9]+ marked=[0-9]+ cleared=[0-9]+ errors=[0-9]+ conflicts=[0-9]+$'
    for _ in $(seq 1 180); do
        sum_late="$(dbxignore status --summary 2>&1 | head -n 1)"
        if printf '%s\n' "$sum_late" | grep -qE "$sum_pattern"; then
            break
        fi
        sleep 1
    done
    if printf '%s\n' "$sum_late" | grep -qE "$sum_pattern"; then
        pass "5f — --summary post-sweep: $sum_late"
    else
        fail "5f — --summary did not advance to state=running within 180s (last: $sum_late)"
    fi
    # Once --summary reports state=running, the same state.json drives the
    # human path: last_sweep is not None, so the 'daemon: running' branch
    # fires synchronously. Capture into a variable first, then grep — under
    # `set -o pipefail`, piping a multi-line Python producer directly into
    # `grep -q` flips the if-branch on a successful match: grep exits 0 on
    # line 1 and closes the pipe; the producer's NEXT click.echo writes to
    # the closed reader and BrokenPipeError makes Python exit 1; pipefail
    # propagates that 1 to the overall pipe exit. Mirrors the `--summary`
    # poll pattern just above.
    local human_out
    human_out="$(dbxignore status 2>&1 || true)"
    if printf '%s\n' "$human_out" | grep -qE '^daemon: running \(pid=[0-9]+\)$'; then
        pass "5f — human status reports 'daemon: running'"
    else
        note "    human status output:"
        printf '%s\n' "$human_out" | sed 's/^/    /'
        fail "5f — human status did not report 'daemon: running'"
    fi

    # Remove slow-sweep marker so phase 6's re-install + uninstall cycles
    # run with normal sweep timing. The cleanup() trap removes it too if
    # this point is never reached.
    rm -f "$DBXIGNORE_STATE_DIR/_test_slow_sweep"
    note "5 — slow-sweep marker removed before phase 6"
}

# ---------------------------------------------------------------------------
# Phase 6 — uninstall
# ---------------------------------------------------------------------------

phase_uninstall() {
    phase "Phase 6 — uninstall"

    local T="$DROPBOX_DIR/$TEST_SUBDIR"

    # plain uninstall: unit removed, markers retained
    # -v added to verify the verbosity flag surfaces install-backend INFO
    # chatter end-to-end. Default-quiet side is verified in Phase 5.
    if dbxignore -v uninstall >/tmp/dbxignore-uninst.out 2>&1; then
        pass "dbxignore -v uninstall (rc=0)"
    else
        fail "dbxignore -v uninstall"; sed 's/^/    /' /tmp/dbxignore-uninst.out
    fi

    grep -q "^INFO " /tmp/dbxignore-uninst.out \
        && pass "uninstall -v — INFO surfaces under verbose" \
        || fail "uninstall -v — verbose did not surface INFO"
    if systemctl --user is-active dbxignore >/dev/null 2>&1; then
        fail "unit still active after uninstall"
    else
        pass "unit no longer active"
    fi
    [ ! -f "$HOME/.config/systemd/user/dbxignore.service" ] \
        && pass "service unit file removed" \
        || fail "service unit file still present"

    [ -f "$T/watch-me.tmp" ] && assert_xattr_set "$T/watch-me.tmp" "uninstall — markers retained on watch-me.tmp"

    # Plain uninstall now scrubs state.json + daemon.log* by default (PR #283).
    # --keep-logs preserves daemon.log* (see 6f); --purge additionally clears
    # markers (see existing flow below).
    [ ! -f "$DBXIGNORE_STATE_DIR/state.json" ] \
        && pass "uninstall — state.json scrubbed" \
        || fail "uninstall — state.json still present"
    if ls "$DBXIGNORE_STATE_DIR"/daemon.log* >/dev/null 2>&1; then
        fail "uninstall — daemon.log* not scrubbed"
    else
        pass "uninstall — daemon.log* scrubbed"
    fi

    # 6a — status --summary returns state=no_state post-uninstall.
    # Plain uninstall scrubs state.json (PR #283), so state.read() returns
    # None and _format_summary emits the truncated 'state=no_state conflicts=N'
    # line. _purge_local_state runs in-process inside the uninstall CLI on
    # every platform, so state.json is gone before `dbxignore uninstall`
    # returns regardless of how the platform service manager tears the
    # daemon down. The poll is kept for robustness on slow filesystems —
    # the first probe typically succeeds.
    note "6a — status --summary post-uninstall"
    local sum_uninst=""
    local sum_uninst_pattern='^state=no_state conflicts=[0-9]+$'
    for _ in $(seq 1 30); do
        sum_uninst="$(dbxignore status --summary 2>&1 | head -n 1)"
        if printf '%s\n' "$sum_uninst" | grep -qE "$sum_uninst_pattern"; then
            break
        fi
        sleep 1
    done
    if printf '%s\n' "$sum_uninst" | grep -qE "$sum_uninst_pattern"; then
        pass "6a — --summary post-uninstall: $sum_uninst"
    else
        fail "6a — --summary did not advance to state=no_state within 30s (last: $sum_uninst)"
    fi

    # re-install briefly, then --purge
    note "re-installing for --purge test..."
    dbxignore install >/dev/null 2>&1 || abort "re-install failed"
    sleep 2

    if dbxignore uninstall --purge >/tmp/dbxignore-purge.out 2>&1; then
        pass "dbxignore uninstall --purge (rc=0)"
    else
        fail "dbxignore uninstall --purge"; sed 's/^/    /' /tmp/dbxignore-purge.out
    fi
    # Happy-path purge regression guard: purge emits the "Cleared N" line but
    # must NOT emit the partial-failure error report. (Forcing a real
    # marker-clear OSError for an end-to-end test requires platform-specific
    # FS contortions; the unit tests in test_install.py cover the assertion
    # tightly. This guard pins that the happy path stays clean.)
    if ! grep -q 'Could not fully clear' /tmp/dbxignore-purge.out; then
        pass "purge — no spurious 'Could not fully clear' on happy path"
    else
        fail "purge — emitted 'Could not fully clear' on happy path"
        sed 's/^/    /' /tmp/dbxignore-purge.out
    fi
    # Happy-path state-files partial-failure guard. Same trade-off as the
    # marker guard above: forcing a state-dir OSError end-to-end needs
    # platform-specific FS contortions, and the unit tests pin the
    # partial-failure assertion tightly. This guard pins the happy path
    # against an accidental regression that would emit the report on every
    # clean uninstall.
    if ! grep -q 'Could not fully purge state files' /tmp/dbxignore-purge.out; then
        pass "purge — no spurious 'Could not fully purge state files' on happy path"
    else
        fail "purge — emitted 'Could not fully purge state files' on happy path"
        sed 's/^/    /' /tmp/dbxignore-purge.out
    fi
    # Daemon-alive purge-refusal guard. On a clean uninstall the guard
    # returns False — the two stderr phrases below must not appear.
    # Failure-path coverage requires platform-specific stuck-process
    # simulation, which can't be scripted reliably.
    if ! grep -q 'daemon is running' /tmp/dbxignore-purge.out \
       && ! grep -q 'liveness is unknown' /tmp/dbxignore-purge.out; then
        pass "purge — no spurious daemon-alive guard fire on happy path"
    else
        fail "purge — daemon-alive guard fired on happy path"
        sed 's/^/    /' /tmp/dbxignore-purge.out
    fi

    [ -f "$T/watch-me.tmp" ] && assert_xattr_unset "$T/watch-me.tmp" "purge — watch-me.tmp marker cleared"
    [ -d "$T/cache" ]        && assert_xattr_unset "$T/cache"        "purge — cache/ marker cleared"

    if [ ! -f "$DBXIGNORE_STATE_DIR/state.json" ] && [ ! -f "$DBXIGNORE_STATE_DIR/daemon.log" ]; then
        pass "purge — state.json + daemon.log removed"
    else
        fail "purge — state files remain"
        ls -la "$DBXIGNORE_STATE_DIR/" 2>/dev/null | sed 's/^/    /'
    fi

    # 6b — status --summary returns state=no_state post-purge.
    # Truncated form: 'state=no_state conflicts=N' with no pid/marked/etc.
    note "6b — status --summary post-purge"
    local sum_purge; sum_purge="$(dbxignore status --summary 2>&1 | head -n 1)"
    if printf '%s\n' "$sum_purge" | grep -qE '^state=no_state conflicts=[0-9]+$'; then
        pass "6b — --summary post-purge: $sum_purge"
    else
        fail "6b — --summary post-purge did not match expected pattern: $sum_purge"
    fi

    # 6c — `--purge` proceeds when state.json is unreadable AND no daemon
    # process holds daemon.lock. Force the scenario: re-install (daemon
    # starts), kill -KILL the daemon directly (service registration survives
    # but the daemon process is dead; systemd's Restart=on-failure RestartSec
    # is 60s, giving us a wide window), corrupt state.json so
    # `state.read()` returns None, then run `dbxignore uninstall --purge`.
    # uninstall_service removes the service registration before any restart
    # can fire; the lock probe sees no contender and proceeds.
    note "6c — --purge recovers from corrupt state.json + dead daemon"
    dbxignore install >/dev/null 2>&1 || abort "6c re-install failed"
    sleep 2
    local daemon_pid_6c
    daemon_pid_6c="$(python3 -c "import json; print(json.load(open('$DBXIGNORE_STATE_DIR/state.json'))['daemon_pid'])" 2>/dev/null || echo "")"
    if [ -n "$daemon_pid_6c" ] && [ "$daemon_pid_6c" != "None" ]; then
        kill -KILL "$daemon_pid_6c" 2>/dev/null || true
    fi
    sleep 1
    printf '%s\n' 'corrupt {{{ not valid json' > "$DBXIGNORE_STATE_DIR/state.json"
    if dbxignore uninstall --purge >/tmp/dbxignore-recovery.out 2>&1; then
        pass "6c — uninstall --purge succeeded with corrupt state.json + dead daemon"
    else
        fail "6c — uninstall --purge failed; expected exit 0"
        sed 's/^/    /' /tmp/dbxignore-recovery.out
    fi
    [ ! -f "$DBXIGNORE_STATE_DIR/state.json" ] \
        && pass "6c — corrupt state.json cleaned up by recovery purge" \
        || fail "6c — corrupt state.json still present after recovery purge"

    # 6d — uninstall --purge exits 2 on injected state-file purge failure
    # DBXIGNORE_TEST_FAIL_STATE_PURGE makes _purge_dir's unlink loop raise
    # OSError, exercising the state_errors exit-2 path. Re-install first so the
    # daemon writes state.json / daemon.lock — _purge_dir only injects inside
    # its f.unlink() loop, so with an empty state dir there'd be nothing to
    # fail on. Markers ARE cleared (the failure is in the later state-dir step);
    # recovery is a clean --purge re-run.
    note "6d — uninstall --purge exits 2 on injected state-purge failure"
    dbxignore install >/dev/null 2>&1 || abort "6d re-install failed"
    sleep 2
    local purge_fail_rc
    if DBXIGNORE_TEST_FAIL_STATE_PURGE=1 dbxignore uninstall --purge \
        >/tmp/dbx-6d-purge.out 2>&1; then
        purge_fail_rc=0
    else
        purge_fail_rc=$?
    fi
    if [ "$purge_fail_rc" -eq 2 ]; then
        pass "6d — uninstall --purge exits 2 on injected state-purge failure"
    else
        fail "6d — uninstall --purge exited $purge_fail_rc instead of 2"
        sed 's/^/    /' /tmp/dbx-6d-purge.out
    fi
    assert_grep /tmp/dbx-6d-purge.out 'Could not fully purge state files' \
        "6d — purge stderr reports the state-file failure"
    # Recovery: clean --purge to remove the state files the injected run left.
    dbxignore uninstall --purge >/dev/null 2>&1 || true

    # 6e — uninstall --purge exits 2 on injected daemon-alive guard
    # DBXIGNORE_TEST_FAIL_DAEMON_ALIVE fires the --purge daemon-alive gate as if
    # a daemon survived service removal. The gate fires BEFORE the purge body,
    # so nothing is cleared; recovery is a clean --purge re-run.
    note "6e — uninstall --purge exits 2 on injected daemon-alive guard"
    dbxignore install >/dev/null 2>&1 || abort "6e re-install failed"
    sleep 2
    local alive_fail_rc
    if DBXIGNORE_TEST_FAIL_DAEMON_ALIVE=1 dbxignore uninstall --purge \
        >/tmp/dbx-6e-purge.out 2>&1; then
        alive_fail_rc=0
    else
        alive_fail_rc=$?
    fi
    if [ "$alive_fail_rc" -eq 2 ]; then
        pass "6e — uninstall --purge exits 2 on injected daemon-alive guard"
    else
        fail "6e — uninstall --purge exited $alive_fail_rc instead of 2"
        sed 's/^/    /' /tmp/dbx-6e-purge.out
    fi
    assert_grep /tmp/dbx-6e-purge.out 'daemon is running' \
        "6e — purge stderr reports the daemon-alive refusal"
    # Recovery: clean --purge (the gate fired before any cleanup ran).
    dbxignore uninstall --purge >/dev/null 2>&1 || true

    # 6f — uninstall --keep-logs preserves daemon.log* but still scrubs state.json
    # The asymmetric opt-out for users who want to retain the diagnostic log
    # across a re-install (install.ps1 uses it for its stop-then-replace step;
    # see install.ps1's Invoke-Install call to `dbxignore uninstall --keep-logs`).
    # Re-install, sleep so the daemon writes at least the initial-sweep banner
    # to daemon.log, then uninstall --keep-logs and check the asymmetry.
    note "6f — uninstall --keep-logs preserves daemon.log*"
    dbxignore install >/dev/null 2>&1 || abort "6f re-install failed"
    sleep 3
    if dbxignore uninstall --keep-logs >/tmp/dbxignore-keeplogs.out 2>&1; then
        pass "6f — dbxignore uninstall --keep-logs (rc=0)"
    else
        fail "6f — dbxignore uninstall --keep-logs"
        sed 's/^/    /' /tmp/dbxignore-keeplogs.out
    fi
    [ ! -f "$DBXIGNORE_STATE_DIR/state.json" ] \
        && pass "6f — --keep-logs: state.json scrubbed" \
        || fail "6f — --keep-logs: state.json still present"
    if ls "$DBXIGNORE_STATE_DIR"/daemon.log* >/dev/null 2>&1; then
        pass "6f — --keep-logs: daemon.log* preserved"
    else
        fail "6f — --keep-logs: daemon.log* not preserved"
    fi
    # Recovery: scrub the preserved logs so Phase 7 leaves no dbxignore-authored
    # artifacts behind.
    rm -f "$DBXIGNORE_STATE_DIR"/daemon.log* 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Phase 7 — final cleanup
# ---------------------------------------------------------------------------

phase_cleanup() {
    phase "Phase 7 — cleanup"

    rm -rf "${DROPBOX_DIR:?}/$TEST_SUBDIR" 2>/dev/null || true
    note "test fixtures removed from Dropbox"

    uv tool uninstall dbxignore >/dev/null 2>&1 \
        && pass "uv tool uninstall dbxignore" \
        || fail "uv tool uninstall dbxignore"

    if [ "$CLEANUP_DROPBOX" -eq 1 ]; then
        note "Dropbox teardown will run via EXIT trap (--cleanup-dropbox set)"
    else
        note "leaving Dropbox installed; rerun with --cleanup-dropbox to wipe ~/.dropbox{,-dist} and the Dropbox folder"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

phase_preflight
phase_dropbox_install
phase_dbxignore_install
phase_cli_surface
phase_reconcile
phase_extended_cli
phase_daemon
phase_uninstall
phase_cleanup

phase "Summary"
echo "  ${G}PASS:${X} $PASS_COUNT"
echo "  ${R}FAIL:${X} $FAIL_COUNT"
if [ "$FAIL_COUNT" -gt 0 ]; then
    for n in "${FAIL_NAMES[@]}"; do echo "    ${R}-${X} $n"; done
    exit 1
fi

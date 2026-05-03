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
#   DBXIGNORE_INSTALL_SPEC='dbxignore==0.4.0' bash manual-test-ubuntu-vps.sh
#   DBXIGNORE_INSTALL_SPEC='git+https://github.com/kiloscheffer/dbxignore.git@v0.4.0' bash manual-test-ubuntu-vps.sh
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

    if uv tool list 2>/dev/null | grep -q '^dbxignore '; then
        note "dbxignore already installed via uv tool — uninstalling first for a clean test"
        uv tool uninstall dbxignore >/dev/null 2>&1 || true
    fi

    uv tool install --link-mode=copy "$DBXIGNORE_INSTALL_SPEC"
    export PATH="$HOME/.local/bin:$PATH"

    command -v dbxignore  >/dev/null && pass "dbxignore on PATH"  || fail "dbxignore on PATH"
    command -v dbxignored >/dev/null && pass "dbxignored on PATH" || fail "dbxignored on PATH"
}

# ---------------------------------------------------------------------------
# Phase 3 — CLI surface
# ---------------------------------------------------------------------------

phase_cli_surface() {
    phase "Phase 3 — CLI surface"

    # PR #92 fixes
    dbxignore  --version 2>&1 | grep -qE '^dbxignore, version '  && pass "dbxignore --version"  || fail "dbxignore --version"
    dbxignored --version 2>&1 | grep -qE '^dbxignored, version ' && pass "dbxignored --version" || fail "dbxignored --version"

    local first; first="$(dbxignored --help 2>&1 | head -n1)"
    if [ "$first" = "Usage: dbxignored [OPTIONS]" ]; then
        pass "dbxignored --help has clean Usage line"
    else
        fail "dbxignored --help first line: $first"
    fi

    dbxignore --help 2>&1 | grep -q 'apply' && pass "dbxignore --help lists subcommands" || fail "dbxignore --help missing subcommands"

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
    dbxignore apply "$T" >/dev/null 2>&1 && pass "apply 4a (rc=0)" || fail "apply 4a"
    assert_xattr_set   "$T/foo.tmp" "4a — foo.tmp marked"
    assert_xattr_unset "$T/bar.txt" "4a — bar.txt unmarked"
    assert_xattr_unset "$T/.dropboxignore" "4a — .dropboxignore never marked"

    # 4b. dir rule + subtree pruning
    note "4b — dir rule + subtree pruning (cache/)"
    printf '*.tmp\ncache/\n' > "$T/.dropboxignore"
    mkdir -p "$T/cache/sub"
    : > "$T/cache/sub/file.txt"
    dbxignore apply "$T" >/dev/null 2>&1 && pass "apply 4b" || fail "apply 4b"
    assert_xattr_set   "$T/cache"              "4b — cache/ marked"
    assert_xattr_unset "$T/cache/sub/file.txt" "4b — descendant unmarked (subtree pruned)"

    # 4c. rule removal clears markers
    note "4c — rule removal clears markers"
    printf 'cache/\n' > "$T/.dropboxignore"   # removed *.tmp
    dbxignore apply "$T" >/dev/null 2>&1 && pass "apply 4c" || fail "apply 4c"
    assert_xattr_unset "$T/foo.tmp" "4c — foo.tmp cleared after rule removed"
    assert_xattr_set   "$T/cache"   "4c — cache/ still marked"

    # 4d. dropped negation: dir rule + descendant negation (the conflict the
    # detector flags — see rules_conflicts.py:168 for why file-glob negations
    # like *.log + !keep.log are NOT flagged).
    note "4d — dropped negation (dir rule + descendant negation)"
    rm -rf "$T"; mkdir -p "$T/build/keep"
    printf 'build/\n!build/keep/\n' > "$T/.dropboxignore"
    : > "$T/build/keep/inside.txt"
    dbxignore apply "$T" >/dev/null 2>&1 && pass "apply 4d" || fail "apply 4d"
    assert_xattr_set   "$T/build"      "4d — build/ marked (parent dir rule wins)"
    assert_xattr_unset "$T/build/keep" "4d — descendant not visited (subtree pruned)"
    if dbxignore explain "$T/build/keep" 2>&1 | grep -qF '[dropped]'; then
        pass "4d — explain annotates dropped negation on build/keep/"
    else
        note "explain output:"
        dbxignore explain "$T/build/keep" 2>&1 | sed 's/^/    /'
        fail "4d — explain did not annotate dropped negation"
    fi

    # 4e. symlink WARNING (Linux refuses user.* xattrs on symlinks).
    # Use a sibling subdir so the parent .dropboxignore doesn't interfere.
    note "4e — symlink WARNING"
    local TS="$T/sym"
    mkdir -p "$TS"
    echo '*.log' > "$TS/.dropboxignore"
    : > "$TS/real.log"
    ln -sfn real.log "$TS/link.log"
    dbxignore apply "$T" >/tmp/dbxignore-apply.out 2>&1 \
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
    if dbxignore explain "$TS/real.log" 2>&1 | grep -q '\*\.log'; then
        pass "4f — explain cites *.log"
    else
        fail "4f — explain did not cite *.log"
    fi
}

# ---------------------------------------------------------------------------
# Phase 5 — daemon
# ---------------------------------------------------------------------------

_dump_daemon_diagnostics() {
    note "tail of daemon.log (last 40 lines):"
    tail -n 40 "$HOME/.local/state/dbxignore/daemon.log" 2>/dev/null | sed 's/^/    /' || true
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

    dbxignore install >/tmp/dbxignore-install.out 2>&1 \
        && pass "dbxignore install (rc=0)" \
        || { fail "dbxignore install"; sed 's/^/    /' /tmp/dbxignore-install.out; return; }

    [ -f "$HOME/.config/systemd/user/dbxignore.service" ] \
        && pass "service unit file written" \
        || fail "service unit file missing"

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
        if grep -q 'watching roots' "$HOME/.local/state/dbxignore/daemon.log" 2>/dev/null; then
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
}

# ---------------------------------------------------------------------------
# Phase 6 — uninstall
# ---------------------------------------------------------------------------

phase_uninstall() {
    phase "Phase 6 — uninstall"

    local T="$DROPBOX_DIR/$TEST_SUBDIR"

    # plain uninstall: unit removed, markers retained
    if dbxignore uninstall >/tmp/dbxignore-uninst.out 2>&1; then
        pass "dbxignore uninstall (rc=0)"
    else
        fail "dbxignore uninstall"; sed 's/^/    /' /tmp/dbxignore-uninst.out
    fi
    if systemctl --user is-active dbxignore >/dev/null 2>&1; then
        fail "unit still active after uninstall"
    else
        pass "unit no longer active"
    fi
    [ ! -f "$HOME/.config/systemd/user/dbxignore.service" ] \
        && pass "service unit file removed" \
        || fail "service unit file still present"

    [ -f "$T/watch-me.tmp" ] && assert_xattr_set "$T/watch-me.tmp" "uninstall — markers retained on watch-me.tmp"

    # re-install briefly, then --purge
    note "re-installing for --purge test..."
    dbxignore install >/dev/null 2>&1 || abort "re-install failed"
    sleep 2

    if dbxignore uninstall --purge >/tmp/dbxignore-purge.out 2>&1; then
        pass "dbxignore uninstall --purge (rc=0)"
    else
        fail "dbxignore uninstall --purge"; sed 's/^/    /' /tmp/dbxignore-purge.out
    fi

    [ -f "$T/watch-me.tmp" ] && assert_xattr_unset "$T/watch-me.tmp" "purge — watch-me.tmp marker cleared"
    [ -d "$T/cache" ]        && assert_xattr_unset "$T/cache"        "purge — cache/ marker cleared"

    if [ ! -f "$HOME/.local/state/dbxignore/state.json" ] && [ ! -f "$HOME/.local/state/dbxignore/daemon.log" ]; then
        pass "purge — state.json + daemon.log removed"
    else
        fail "purge — state files remain"
        ls -la "$HOME/.local/state/dbxignore/" 2>/dev/null | sed 's/^/    /'
    fi
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

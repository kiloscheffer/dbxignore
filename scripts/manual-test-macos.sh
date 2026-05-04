#!/usr/bin/env bash
# End-to-end smoke test for dbxignore on macOS with a live Dropbox install.
#
# Assumes the tester already has Dropbox.app installed and signed in (info.json
# present, sync folder created). Skips the Dropbox-install phase the Linux VPS
# script needs, but otherwise mirrors its phase structure: pre-flight, install
# dbxignore, CLI surface, reconcile/apply, daemon (launchd), uninstall, cleanup.
#
# Compatible with both bash and zsh; the tester's default shell is zsh on macOS,
# but ``#!/usr/bin/env bash`` ensures consistent behavior with the Linux script.
#
# Usage:
#   bash manual-test-macos.sh                        # default: PyPI
#   DBXIGNORE_INSTALL_SPEC='dbxignore==0.5.0' bash manual-test-macos.sh
#   DBXIGNORE_INSTALL_SPEC='git+https://github.com/kiloscheffer/dbxignore.git@main' bash manual-test-macos.sh
#
# Exits non-zero if any check fails. Prints a PASS/FAIL summary.

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DBXIGNORE_INSTALL_SPEC="${DBXIGNORE_INSTALL_SPEC:-dbxignore}"
TEST_SUBDIR="dbxignore-test"
ATTR_LEGACY="com.dropbox.ignored"
ATTR_FILEPROVIDER="com.apple.fileprovider.ignore#P"

for arg in "$@"; do
    case "$arg" in
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

# ---- xattr helpers ---------------------------------------------------------
# macOS picks the active attribute name from sync mode (legacy ↔
# com.dropbox.ignored, File Provider ↔ com.apple.fileprovider.ignore#P,
# or both names in the genuinely-uncertain dual-attr case from item 58).
# These helpers read both names and report which is present, so a single
# assertion works regardless of the active mode.

xattr_get_any() {
    # xattr_get_any <path>
    # → "<attr-name>=<value>" if any dbxignore attr is set on <path>
    # → "missing" if no dbxignore attr is set
    # Errors from `xattr -p` (path missing, no permission) print "missing".
    local path="$1"
    local v
    if v="$(xattr -p "$ATTR_LEGACY" "$path" 2>/dev/null)" && [ -n "$v" ]; then
        echo "${ATTR_LEGACY}=${v}"
        return 0
    fi
    if v="$(xattr -p "$ATTR_FILEPROVIDER" "$path" 2>/dev/null)" && [ -n "$v" ]; then
        echo "${ATTR_FILEPROVIDER}=${v}"
        return 0
    fi
    echo "missing"
}

assert_xattr_set() {
    local p="$1" name="$2"
    local v; v="$(xattr_get_any "$p")"
    if [ "$v" != "missing" ]; then pass "$name (${v})"; else fail "$name (no dbxignore xattr on $p)"; fi
}

assert_xattr_unset() {
    local p="$1" name="$2"
    local v; v="$(xattr_get_any "$p")"
    if [ "$v" = "missing" ]; then pass "$name"; else fail "$name (unexpected ${v} on $p)"; fi
}

# ---------------------------------------------------------------------------
# Phase 0 — pre-flight
# ---------------------------------------------------------------------------

phase_preflight() {
    phase "Phase 0 — pre-flight"

    [ "$EUID" -ne 0 ] || abort "must run as a regular user, not root (Dropbox refuses to run as root)"

    if [ "$(uname -s)" != "Darwin" ]; then
        abort "this script is macOS-only; on Linux use scripts/manual-test-ubuntu-vps.sh"
    fi
    note "macOS: $(sw_vers -productVersion 2>/dev/null || echo unknown)"

    command -v python3 >/dev/null || abort "python3 required (install via xcode-select --install or Homebrew)"
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

    command -v xattr >/dev/null || abort "xattr command required (ships with macOS by default)"

    # GUI-login signal: launchctl bootstrap requires a user GUI session, so
    # SSH-on-fresh-boot installs fail. SecurityAgent or Dock running implies
    # the user has logged in; otherwise warn but don't abort (the install
    # may still succeed, e.g. if loginwindow is up but Dock hasn't started).
    if pgrep -qx Dock || pgrep -qx loginwindow; then
        note "GUI session detected (Dock/loginwindow running)"
    else
        echo "${Y}WARNING:${X} no GUI session detected — launchctl bootstrap may fail with"
        echo "  'Bootstrap failed: 5: Input/output error'. Log into the GUI at least once"
        echo "  since the last reboot, then re-run."
    fi
}

# ---------------------------------------------------------------------------
# Phase 1 — verify Dropbox install (no install/auth — assume already done)
# ---------------------------------------------------------------------------

phase_verify_dropbox() {
    phase "Phase 1 — verify Dropbox install"

    [ -f ~/.dropbox/info.json ] || abort "~/.dropbox/info.json missing — sign into Dropbox.app first"
    pass "Dropbox device linked (info.json present)"

    DROPBOX_DIR="$(python3 -c "
import json, os
with open(os.path.expanduser('~/.dropbox/info.json')) as f:
    d = json.load(f)
acct = d.get('personal') or d.get('business') or next(iter(d.values()))
print(acct['path'])
")"
    note "Dropbox folder: $DROPBOX_DIR"
    [ -d "$DROPBOX_DIR" ] || abort "Dropbox folder $DROPBOX_DIR does not exist"
    pass "Dropbox folder present at $DROPBOX_DIR"

    # Surface which sync mode dbxignore would detect, *before* installing it,
    # by inspecting the same signals the backend uses. Useful sanity check
    # that the path Dropbox configured matches the tester's expectation.
    case "$DROPBOX_DIR" in
        *"/Library/CloudStorage/"*) note "path is under ~/Library/CloudStorage/ → expect File Provider mode" ;;
        */Volumes/*)                note "path is under /Volumes/ → expect File Provider (external drive)" ;;
        *)                          note "path is elsewhere → expect legacy mode" ;;
    esac
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

    uv tool install "$DBXIGNORE_INSTALL_SPEC"
    export PATH="$HOME/.local/bin:$PATH"

    command -v dbxignore  >/dev/null && pass "dbxignore on PATH"  || fail "dbxignore on PATH"
    command -v dbxignored >/dev/null && pass "dbxignored on PATH" || fail "dbxignored on PATH"
}

# ---------------------------------------------------------------------------
# Phase 3 — CLI surface (including the new `sync mode:` line on darwin)
# ---------------------------------------------------------------------------

phase_cli_surface() {
    phase "Phase 3 — CLI surface"

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
        head -5 /tmp/dbxignore-status.out | sed 's/^/    /'
    else
        fail "dbxignore status (rc=$?)"
        sed 's/^/    /' /tmp/dbxignore-status.out
    fi

    # Item 37 — sync mode line is darwin-only and prints regardless of
    # whether the daemon ever ran (it's derived from on-disk state, not
    # state.json). Format: `sync mode: <mode>: <reason>` where <mode> is
    # one of legacy / file_provider / both.
    if grep -qE '^sync mode: (legacy|file_provider|both):' /tmp/dbxignore-status.out; then
        local mode_line; mode_line="$(grep -E '^sync mode:' /tmp/dbxignore-status.out)"
        pass "3 — status shows sync mode line (item 37)"
        note "$mode_line"
    else
        fail "3 — status missing sync mode line (item 37)"
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
    # detector flags — see rules_conflicts.py for why file-glob negations
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

    # 4e. symlink — INVERTED from Linux: macOS allows xattr on symlinks via
    # the NOFOLLOW path (the `xattr` PyPI package's `symlink=True` kwarg),
    # so a matched symlink gets marked silently and successfully. No
    # PermissionError, no WARNING — the symlink itself is marked, not its
    # target. This is the intentional macOS-vs-Linux behavioral divergence
    # the v0.4 spec documents.
    note "4e — symlink marked silently (macOS allows xattr on symlinks)"
    local TS="$T/sym"
    mkdir -p "$TS"
    echo '*.log' > "$TS/.dropboxignore"
    : > "$TS/real.log"
    ln -sfn real.log "$TS/link.log"
    dbxignore apply "$T" >/tmp/dbxignore-apply.out 2>&1 \
        && pass "apply 4e completes" \
        || fail "apply 4e crashed"
    if grep -qiE 'WARN|symlink|permission|enotsup' /tmp/dbxignore-apply.out; then
        note "$(cat /tmp/dbxignore-apply.out)"
        fail "4e — unexpected WARNING on macOS (symlinks should mark silently)"
    else
        pass "4e — no symlink WARNING (matches macOS divergence)"
    fi
    assert_xattr_set "$TS/real.log" "4e — real file marked"
    assert_xattr_set "$TS/link.log" "4e — symlink itself marked (NOFOLLOW path)"

    # 4f. explain on a marked file returns the matching rule
    note "4f — explain returns matching rule"
    if dbxignore explain "$TS/real.log" 2>&1 | grep -q '\*\.log'; then
        pass "4f — explain cites *.log"
    else
        fail "4f — explain did not cite *.log"
    fi
}

# ---------------------------------------------------------------------------
# Phase 5 — daemon (launchd User Agent + watchdog)
# ---------------------------------------------------------------------------

_dump_daemon_diagnostics() {
    note "tail of daemon.log (last 40 lines):"
    tail -n 40 "$HOME/Library/Logs/dbxignore/daemon.log" 2>/dev/null | sed 's/^/    /' || true
    note "launchctl print:"
    local uid; uid="$(id -u)"
    launchctl print "gui/${uid}/com.kiloscheffer.dbxignore" 2>/dev/null \
        | head -n 30 | sed 's/^/    /' || true
    note "test-dir state:"
    ls -la "$1" 2>/dev/null | sed 's/^/    /' || true
}

phase_daemon() {
    phase "Phase 5 — daemon (launchd User Agent + watchdog)"

    # Reset to a clean test dir BEFORE installing the daemon, so the daemon's
    # initial cache.load_root() reads a known rule set with no leftover phase-4
    # conflicts. Same shape as the Linux script's phase 5 — events fired before
    # the watchdog observer is online are missed, so phase 5 has to start from
    # a stable on-disk state.
    local T="$DROPBOX_DIR/$TEST_SUBDIR"
    rm -rf "$T"; mkdir -p "$T"
    printf '*.tmp\n' > "$T/.dropboxignore"

    dbxignore install >/tmp/dbxignore-install.out 2>&1 \
        && pass "dbxignore install (rc=0)" \
        || { fail "dbxignore install"; sed 's/^/    /' /tmp/dbxignore-install.out; return; }

    [ -f "$HOME/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist" ] \
        && pass "LaunchAgent plist written" \
        || fail "LaunchAgent plist missing"

    local uid; uid="$(id -u)"
    sleep 2
    if launchctl print "gui/${uid}/com.kiloscheffer.dbxignore" >/dev/null 2>&1; then
        pass "launchd job bootstrapped"
    else
        fail "launchd job not bootstrapped (gui/${uid}/com.kiloscheffer.dbxignore)"
    fi

    # Wait for the daemon to bring its watchdog observer online. Same poll-
    # for-sentinel approach as the Linux script — daemon startup time scales
    # with watched-tree size; macOS uses FSEvents so there's no inotify watch
    # ceiling but the initial sweep still runs over the whole subtree.
    local dir_count; dir_count="$(find "$DROPBOX_DIR" -type d 2>/dev/null | wc -l | tr -d ' ')"
    note "watched subtree: $dir_count dirs in $DROPBOX_DIR"
    note "waiting up to 180s for daemon initial sweep + observer ready..."
    local ready=0
    for _ in $(seq 1 180); do
        if grep -q 'watching roots' "$HOME/Library/Logs/dbxignore/daemon.log" 2>/dev/null; then
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

    # Item 37 — verify the daemon also logged the sync mode at startup.
    if grep -qE 'sync mode detection: (legacy|file_provider|both):' "$HOME/Library/Logs/dbxignore/daemon.log"; then
        local log_line; log_line="$(grep -E 'sync mode detection:' "$HOME/Library/Logs/dbxignore/daemon.log" | head -1)"
        pass "5 — daemon logged sync mode at startup (item 37)"
        note "$log_line"
    else
        fail "5 — daemon did not log sync mode (item 37)"
    fi

    # 5b — watchdog reacts to a new file (created AFTER observer is live)
    note "5b — watchdog reacts to new file"
    : > "$T/watch-me.tmp"
    sleep 6                                           # OTHER debounce 500ms + reconcile + slack
    local v; v="$(xattr_get_any "$T/watch-me.tmp")"
    if [ "$v" != "missing" ]; then
        pass "5b — daemon marked new *.tmp file via watchdog (${v})"
    else
        fail "5b — daemon did not mark new *.tmp file"
        _dump_daemon_diagnostics "$T"
    fi

    # 5c — .dropboxignore reload picks up new rule
    note "5c — .dropboxignore reload"
    : > "$T/freshrule.dat"
    sleep 1
    printf '*.tmp\n*.dat\n' > "$T/.dropboxignore"
    sleep 6                                           # RULES debounce 100ms + reload + reconcile
    v="$(xattr_get_any "$T/freshrule.dat")"
    if [ "$v" != "missing" ]; then
        pass "5c — daemon picked up new rule and marked existing file (${v})"
    else
        fail "5c — daemon did not mark file under reloaded rule"
        _dump_daemon_diagnostics "$T"
    fi

    # 5d — DIR_CREATE bypass (item 57) — newly created dir matching a rule
    # should be marked synchronously without waiting the OTHER debounce.
    # The bypass calls reconcile_subtree directly from the watchdog handler,
    # so even a tight poll (sub-second) should see the marker.
    note "5d — DIR_CREATE bypass for matched directory (item 57)"
    printf '*.tmp\n*.dat\nbuild_*/\n' > "$T/.dropboxignore"
    sleep 6                                           # let the rule reload settle
    mkdir -p "$T/build_x"
    sleep 2                                           # short wait — bypass shouldn't need OTHER debounce
    v="$(xattr_get_any "$T/build_x")"
    if [ "$v" != "missing" ]; then
        pass "5d — DIR_CREATE bypass marked build_x/ within 2s (${v})"
    else
        fail "5d — DIR_CREATE bypass did not mark build_x/ within 2s"
        _dump_daemon_diagnostics "$T"
    fi
}

# ---------------------------------------------------------------------------
# Phase 6 — uninstall
# ---------------------------------------------------------------------------

phase_uninstall() {
    phase "Phase 6 — uninstall"

    local T="$DROPBOX_DIR/$TEST_SUBDIR"
    local uid; uid="$(id -u)"

    # plain uninstall: launchd job removed, markers retained
    if dbxignore uninstall >/tmp/dbxignore-uninst.out 2>&1; then
        pass "dbxignore uninstall (rc=0)"
    else
        fail "dbxignore uninstall"; sed 's/^/    /' /tmp/dbxignore-uninst.out
    fi
    if launchctl print "gui/${uid}/com.kiloscheffer.dbxignore" >/dev/null 2>&1; then
        fail "launchd job still bootstrapped after uninstall"
    else
        pass "launchd job no longer bootstrapped"
    fi
    [ ! -f "$HOME/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist" ] \
        && pass "LaunchAgent plist removed" \
        || fail "LaunchAgent plist still present"

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

    # macOS splits state vs. log dirs (~/Library/Application Support vs.
    # ~/Library/Logs); --purge should clean both.
    local state_dir="$HOME/Library/Application Support/dbxignore"
    local log_dir="$HOME/Library/Logs/dbxignore"
    if [ ! -f "$state_dir/state.json" ] && [ ! -f "$log_dir/daemon.log" ]; then
        pass "purge — state.json + daemon.log removed"
    else
        fail "purge — state files remain"
        ls -la "$state_dir/" 2>/dev/null | sed 's/^/    /'
        ls -la "$log_dir/" 2>/dev/null | sed 's/^/    /'
    fi
}

# ---------------------------------------------------------------------------
# Phase 7 — final cleanup
# ---------------------------------------------------------------------------

phase_cleanup() {
    phase "Phase 7 — cleanup"

    rm -rf "${DROPBOX_DIR:?}/$TEST_SUBDIR" 2>/dev/null || true
    note "test fixtures removed from Dropbox folder"

    uv tool uninstall dbxignore >/dev/null 2>&1 \
        && pass "uv tool uninstall dbxignore" \
        || fail "uv tool uninstall dbxignore"

    note "Dropbox itself is left running and signed in (no equivalent of --cleanup-dropbox needed)"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

phase_preflight
phase_verify_dropbox
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

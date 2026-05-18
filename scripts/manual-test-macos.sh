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
#   DBXIGNORE_INSTALL_SPEC='dbxignore==<version>' bash manual-test-macos.sh
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
# macOS goes straight to ~/Library/Application Support per Apple's
# app-data conventions; no XDG-equivalent. Mirrors the variable shape in
# manual-test-ubuntu-vps.sh for parity.
DBXIGNORE_STATE_DIR="$HOME/Library/Application Support/dbxignore"

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

# EXIT trap — honor Phase 4.5 case 4s's recovery sentinels if a `set -e` abort
# fired mid-test. No-op when sentinels are unset (the in-phase restore ran
# successfully). The recovery function is defined in `_phase_extended_cli.sh`,
# sourced below before this trap fires. Unlike `manual-test-ubuntu-vps.sh`,
# macOS doesn't run a long-lived `dropboxd` foreground process during tests
# (the .app launches the daemon out-of-band), so this cleanup is currently
# 4s-only; expand to additional recovery hooks alongside any future
# destructive-setup cases.
cleanup() {
    if declare -F _phase_4s_recover_state_json >/dev/null 2>&1; then
        _phase_4s_recover_state_json
    fi
}
trap cleanup EXIT

# ---- xattr helpers ---------------------------------------------------------
# macOS picks the active attribute name from sync mode (legacy ↔
# com.dropbox.ignored, File Provider ↔ com.apple.fileprovider.ignore#P,
# or both names in the genuinely-uncertain dual-attr case).
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

    local uv_tool_list
    local uv_tool_list_rc=0
    uv_tool_list="$(uv tool list 2>/dev/null)" || uv_tool_list_rc=$?
    if [ "$uv_tool_list_rc" -eq 0 ] && printf '%s\n' "$uv_tool_list" | grep -q '^dbxignore '; then
        note "dbxignore already installed via uv tool — uninstalling first for a clean test"
        # Best-effort CLI teardown. `dbxignore uninstall` runs `launchctl
        # bootout` and waits for the daemon to exit
        # (install/macos_launchd.py:remove_service). Plain `uninstall`
        # (not `--purge`) preserves ignore markers outside this script's
        # test subdir. The launchctl + rm lines below cover the
        # broken-CLI case (interrupted earlier install).
        dbxignore uninstall >/dev/null 2>&1 || true
        launchctl bootout "gui/$(id -u)/com.kiloscheffer.dbxignore" >/dev/null 2>&1 || true
        rm -f "$HOME/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist"

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
        # mid-cleanup can leave the venv at $(uv tool dir)/dbxignore AND/OR
        # the shims at $(uv tool dir --bin) behind even though `uv tool list`
        # no longer shows dbxignore. The next `uv tool install` then either
        # does an incremental update (venv-orphan: only changed packages
        # reinstall; others survive, producing a hybrid venv with subtly
        # broken C-extension state) or refuses outright with "Executables
        # already exist" (shim-orphan). Detect both shapes and clean them
        # up here so the next install is fresh. Mirrors the known-install
        # teardown above, minus the `dbxignore uninstall` CLI call (orphan
        # state means dbxignore isn't on PATH). The daemon may or may not
        # still be running: POSIX unlink-while-open lets a daemon process
        # started before the partial uninstall survive venv/shim removal as
        # long as its loaded modules + open file descriptors stay alive,
        # and it'll continue writing state.json / holding the singleton
        # lock during the rest of the test — so the daemon-kill block below
        # has to run defensively. See PR #266.
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
        if { [ -n "$orphan_venv" ] && [ -d "$orphan_venv" ]; } || [ "${#orphan_shims[@]}" -gt 0 ]; then
            note "${Y}WARNING:${X} orphan install detected — prior uv tool uninstall partially failed"

            # Service-unit teardown (best-effort: no-ops when nothing exists,
            # which is the common case in the orphan state since `dbxignore
            # uninstall` may have run earlier and removed the agent before
            # the venv/shim cleanup failed).
            launchctl bootout "gui/$(id -u)/com.kiloscheffer.dbxignore" >/dev/null 2>&1 || true
            rm -f "$HOME/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist"

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

            if [ -n "$orphan_venv" ] && [ -d "$orphan_venv" ]; then
                note "  removing orphan venv at $orphan_venv"
                rm -rf "$orphan_venv"
            fi
            if [ "${#orphan_shims[@]}" -gt 0 ]; then
                for shim in "${orphan_shims[@]}"; do
                    note "  removing orphan shim at $shim"
                    rm -f "$shim"
                done
            fi
            note "orphan cleanup complete; proceeding with fresh install"
        fi
    fi

    clean_uv_cache_for_dbxignore_if_local

    uv tool install "$DBXIGNORE_INSTALL_SPEC"
    export PATH="$HOME/.local/bin:$PATH"

    command -v dbxignore  >/dev/null && pass "dbxignore on PATH"  || fail "dbxignore on PATH"
}

# ---------------------------------------------------------------------------
# Phase 3 — CLI surface (including the new `sync mode:` line on darwin)
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
        head -5 /tmp/dbxignore-status.out | sed 's/^/    /'
    else
        fail "dbxignore status (rc=$?)"
        sed 's/^/    /' /tmp/dbxignore-status.out
    fi

    # The sync mode line is darwin-only and prints regardless of
    # whether the daemon ever ran (it's derived from on-disk state, not
    # state.json). Format: `sync mode: <mode>: <reason>` where <mode> is
    # one of legacy / file_provider / both.
    if grep -qE '^sync mode: (legacy|file_provider|both):' /tmp/dbxignore-status.out; then
        local mode_line; mode_line="$(grep -E '^sync mode:' /tmp/dbxignore-status.out)"
        pass "3 — status shows sync mode line"
        note "$mode_line"
    else
        fail "3 — status missing sync mode line"
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
    # detector flags — see rules_conflicts.py for why file-glob negations
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

    # 4e. symlink — INVERTED from Linux: macOS allows xattr on symlinks via
    # the NOFOLLOW path (the `xattr` PyPI package's `symlink=True` kwarg),
    # so a matched symlink gets marked silently and successfully. No
    # PermissionError, no WARNING — the symlink itself is marked, not its
    # target. This is the intentional macOS-vs-Linux behavioral divergence.
    note "4e — symlink marked silently (macOS allows xattr on symlinks)"
    local TS="$T/sym"
    mkdir -p "$TS"
    echo '*.log' > "$TS/.dropboxignore"
    : > "$TS/real.log"
    ln -sfn real.log "$TS/link.log"
    dbxignore apply "$T" --yes >/tmp/dbxignore-apply.out 2>&1 \
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
# to manual-test-ubuntu-vps.sh's; extracted so Phase 4.5 additions only
# have to land in one place.
source "$(dirname "$0")/_phase_extended_cli.sh"

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

    # install verbosity defaults — default WARNING quiets install-backend
    # INFO chatter; the click.echo summary line still surfaces.
    grep -q "Installed dbxignore daemon service" /tmp/dbxignore-install.out \
        && pass "install — click.echo summary present" \
        || fail "install — click.echo summary missing"
    if ! grep -q "^INFO " /tmp/dbxignore-install.out; then
        pass "install — no INFO chatter at default level"
    else
        fail "install — INFO chatter leaked at default level"
    fi

    [ -f "$HOME/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist" ] \
        && pass "LaunchAgent plist written" \
        || fail "LaunchAgent plist missing"

    # install verb-form — plist invokes `dbxignore daemon`
    plutil -p "$HOME/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist" \
        | grep -E '"[^"]*daemon"' >/dev/null \
        && pass "ProgramArguments includes 'daemon' subcommand" \
        || fail "ProgramArguments missing 'daemon'"

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

    # 5a — opportunistic state=starting capture. Probed AFTER the
    # watching-roots break: daemon.run logs 'watching roots' BEFORE writing
    # the early state.json (daemon.py:663 then :678), so an in-loop probe
    # races state.write and almost always misses. Post-readiness, state.json
    # appears within microseconds; on a real Dropbox tree state=starting is
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
    # without the marker, a slow sweep on a real Dropbox tree could race
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

    # Verify the daemon also logged the sync mode at startup.
    if grep -qE 'sync mode detection: (legacy|file_provider|both):' "$HOME/Library/Logs/dbxignore/daemon.log"; then
        local log_line; log_line="$(grep -E 'sync mode detection:' "$HOME/Library/Logs/dbxignore/daemon.log" | head -1)"
        pass "5 — daemon logged sync mode at startup"
        note "$log_line"
    else
        fail "5 — daemon did not log sync mode"
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

    # 5d — DIR_CREATE bypass — newly created dir matching a rule
    # should be marked synchronously without waiting the OTHER debounce.
    # The bypass calls reconcile_subtree directly from the watchdog handler,
    # so even a tight poll (sub-second) should see the marker.
    note "5d — DIR_CREATE bypass for matched directory"
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
    # sweep-complete sentinel. On a real Dropbox tree the sweep can still be
    # running when 5f probes, in which case --summary correctly emits
    # 'state=starting pid=N' (truncated form). Poll for state=running for up
    # to 180s to absorb the transition — matches the watching-roots-wait
    # headroom above. Each iteration also pays one --summary subprocess
    # invocation, so wall-clock can drift somewhat past 180s on slow hosts;
    # acceptable for a manual smoke test.
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
    # run with normal sweep timing. Phase 7 also removes it as a defensive
    # backstop if this point is never reached.
    rm -f "$DBXIGNORE_STATE_DIR/_test_slow_sweep"
    note "5 — slow-sweep marker removed before phase 6"
}

# ---------------------------------------------------------------------------
# Phase 6 — uninstall
# ---------------------------------------------------------------------------

phase_uninstall() {
    phase "Phase 6 — uninstall"

    local T="$DROPBOX_DIR/$TEST_SUBDIR"
    local uid; uid="$(id -u)"

    # plain uninstall: launchd job removed, markers retained
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
    if launchctl print "gui/${uid}/com.kiloscheffer.dbxignore" >/dev/null 2>&1; then
        fail "launchd job still bootstrapped after uninstall"
    else
        pass "launchd job no longer bootstrapped"
    fi
    [ ! -f "$HOME/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist" ] \
        && pass "LaunchAgent plist removed" \
        || fail "LaunchAgent plist still present"

    [ -f "$T/watch-me.tmp" ] && assert_xattr_set "$T/watch-me.tmp" "uninstall — markers retained on watch-me.tmp"

    # 6a — status --summary returns state=not_running post-uninstall.
    # state.json is retained by plain uninstall; the daemon process exits and
    # daemon_is_running(s) flips False. launchctl bootout is synchronous on
    # macOS (the agent is fully torn down before uninstall returns), but
    # Windows schtasks /Delete /F is fire-and-forget on the running task
    # instance — poll for the transition for up to 30s so the case is
    # symmetric across platforms.
    note "6a — status --summary post-uninstall"
    local sum_uninst=""
    local sum_uninst_pattern='^state=not_running pid=[0-9]+ marked=[0-9]+ cleared=[0-9]+ errors=[0-9]+ conflicts=[0-9]+$'
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
        fail "6a — --summary did not advance to state=not_running within 30s (last: $sum_uninst)"
    fi

    # 6c — idempotent uninstall when service is already unloaded.
    # Install, manually bootout via launchctl, then `dbxignore uninstall` —
    # the bootout call inside `uninstall_agent` returns rc=3 / "No such
    # process" stderr; the stderr-tolerant arm in
    # `macos_launchd._is_service_not_loaded` treats this as idempotent
    # success, proceeds to plist removal, and the CLI returns 0.
    note "6c — idempotent uninstall after manual bootout"
    dbxignore install >/dev/null 2>&1 || abort "6c re-install failed"
    sleep 2
    launchctl bootout "gui/${uid}/com.kiloscheffer.dbxignore" >/dev/null 2>&1 || true
    if dbxignore uninstall >/tmp/dbxignore-idemp.out 2>&1; then
        pass "6c — idempotent uninstall after manual bootout (rc=0)"
    else
        fail "6c — idempotent uninstall after manual bootout"
        sed 's/^/    /' /tmp/dbxignore-idemp.out
    fi
    [ ! -f "$HOME/Library/LaunchAgents/com.kiloscheffer.dbxignore.plist" ] \
        && pass "6c — LaunchAgent plist removed by idempotent uninstall" \
        || fail "6c — LaunchAgent plist still present after idempotent uninstall"

    # re-install briefly, then --purge
    note "re-installing for --purge test..."
    dbxignore install >/dev/null 2>&1 || abort "re-install failed"
    sleep 2

    if dbxignore uninstall --purge >/tmp/dbxignore-purge.out 2>&1; then
        pass "dbxignore uninstall --purge (rc=0)"
    else
        fail "dbxignore uninstall --purge"; sed 's/^/    /' /tmp/dbxignore-purge.out
    fi
    # Happy-path purge regression guard: purge emits the "Cleared N" line
    # but must NOT emit the partial-failure error report. (Forcing a real
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

    # macOS splits state vs. log dirs (~/Library/Application Support vs.
    # ~/Library/Logs); --purge should clean both.
    local state_dir="$DBXIGNORE_STATE_DIR"
    local log_dir="$HOME/Library/Logs/dbxignore"
    if [ ! -f "$state_dir/state.json" ] && [ ! -f "$log_dir/daemon.log" ]; then
        pass "purge — state.json + daemon.log removed"
    else
        fail "purge — state files remain"
        ls -la "$state_dir/" 2>/dev/null | sed 's/^/    /'
        ls -la "$log_dir/" 2>/dev/null | sed 's/^/    /'
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

    # 6d — `--purge` proceeds when state.json is unreadable AND no daemon
    # process holds daemon.lock. Force the scenario: re-install (daemon
    # starts), kill -KILL the daemon directly, corrupt state.json so
    # `state.read()` returns None, then run `dbxignore uninstall --purge`.
    # macOS-specific timing concern: launchd's KeepAlive on-Crashed restart
    # triggers after the built-in 10s throttle. The uninstall ceremony below
    # runs in under 5s, well within that window — `launchctl bootout` (inside
    # uninstall_service) removes the registration before any pending restart
    # can fire.
    note "6d — --purge recovers from corrupt state.json + dead daemon"
    dbxignore install >/dev/null 2>&1 || abort "6d re-install failed"
    sleep 2
    local daemon_pid_6d
    daemon_pid_6d="$(python3 -c "import json; print(json.load(open('$state_dir/state.json'))['daemon_pid'])" 2>/dev/null || echo "")"
    if [ -n "$daemon_pid_6d" ] && [ "$daemon_pid_6d" != "None" ]; then
        kill -KILL "$daemon_pid_6d" 2>/dev/null || true
    fi
    sleep 1
    printf '%s\n' 'corrupt {{{ not valid json' > "$state_dir/state.json"
    if dbxignore uninstall --purge >/tmp/dbxignore-recovery.out 2>&1; then
        pass "6d — uninstall --purge succeeded with corrupt state.json + dead daemon"
    else
        fail "6d — uninstall --purge failed; expected exit 0"
        sed 's/^/    /' /tmp/dbxignore-recovery.out
    fi
    [ ! -f "$state_dir/state.json" ] \
        && pass "6d — corrupt state.json cleaned up by recovery purge" \
        || fail "6d — corrupt state.json still present after recovery purge"

    # 6e — uninstall exits 2 on injected launchctl bootout failure
    # DBXIGNORE_TEST_FAIL_BOOTOUT makes uninstall_agent treat the bootout result
    # as a confirmed non-zero-rc failure (stderr that _is_service_not_loaded
    # does NOT match), so uninstall_agent raises RuntimeError → cli.uninstall
    # exits 2. macOS-only: launchctl bootout is the macOS daemon-shutdown step.
    # The plist is preserved and the daemon stays registered; recovery is a
    # clean uninstall re-run.
    note "6e — uninstall exits 2 on injected launchctl bootout failure"
    dbxignore install >/dev/null 2>&1 || abort "6e re-install failed"
    sleep 2
    local bootout_fail_rc
    if DBXIGNORE_TEST_FAIL_BOOTOUT=1 dbxignore uninstall \
        >/tmp/dbx-6e-uninstall.out 2>&1; then
        bootout_fail_rc=0
    else
        bootout_fail_rc=$?
    fi
    if [ "$bootout_fail_rc" -eq 2 ]; then
        pass "6e — uninstall exits 2 on injected bootout failure"
    else
        fail "6e — uninstall exited $bootout_fail_rc instead of 2"
        sed 's/^/    /' /tmp/dbx-6e-uninstall.out
    fi
    assert_grep /tmp/dbx-6e-uninstall.out 'launchctl bootout returned' \
        "6e — uninstall stderr reports the bootout failure"
    # Recovery: clean uninstall (the plist + registration survived the injected run).
    dbxignore uninstall >/dev/null 2>&1 || true

    # 6f — uninstall --purge exits 2 on injected state-file purge failure
    # DBXIGNORE_TEST_FAIL_STATE_PURGE makes _purge_dir's unlink loop raise
    # OSError, exercising the state_errors exit-2 path. Re-install first so the
    # daemon writes state.json / daemon.lock — _purge_dir only injects inside
    # its f.unlink() loop. Markers ARE cleared (failure is in the later
    # state-dir step); recovery is a clean --purge re-run.
    note "6f — uninstall --purge exits 2 on injected state-purge failure"
    dbxignore install >/dev/null 2>&1 || abort "6f re-install failed"
    sleep 2
    local purge_fail_rc
    if DBXIGNORE_TEST_FAIL_STATE_PURGE=1 dbxignore uninstall --purge \
        >/tmp/dbx-6f-purge.out 2>&1; then
        purge_fail_rc=0
    else
        purge_fail_rc=$?
    fi
    if [ "$purge_fail_rc" -eq 2 ]; then
        pass "6f — uninstall --purge exits 2 on injected state-purge failure"
    else
        fail "6f — uninstall --purge exited $purge_fail_rc instead of 2"
        sed 's/^/    /' /tmp/dbx-6f-purge.out
    fi
    assert_grep /tmp/dbx-6f-purge.out 'Could not fully purge state files' \
        "6f — purge stderr reports the state-file failure"
    dbxignore uninstall --purge >/dev/null 2>&1 || true

    # 6g — uninstall --purge exits 2 on injected daemon-alive guard
    # DBXIGNORE_TEST_FAIL_DAEMON_ALIVE fires the --purge daemon-alive gate as if
    # a daemon survived service removal. The gate fires BEFORE the purge body,
    # so nothing is cleared; recovery is a clean --purge re-run.
    note "6g — uninstall --purge exits 2 on injected daemon-alive guard"
    dbxignore install >/dev/null 2>&1 || abort "6g re-install failed"
    sleep 2
    local alive_fail_rc
    if DBXIGNORE_TEST_FAIL_DAEMON_ALIVE=1 dbxignore uninstall --purge \
        >/tmp/dbx-6g-purge.out 2>&1; then
        alive_fail_rc=0
    else
        alive_fail_rc=$?
    fi
    if [ "$alive_fail_rc" -eq 2 ]; then
        pass "6g — uninstall --purge exits 2 on injected daemon-alive guard"
    else
        fail "6g — uninstall --purge exited $alive_fail_rc instead of 2"
        sed 's/^/    /' /tmp/dbx-6g-purge.out
    fi
    assert_grep /tmp/dbx-6g-purge.out 'daemon is running' \
        "6g — purge stderr reports the daemon-alive refusal"
    dbxignore uninstall --purge >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# Phase 7 — final cleanup
# ---------------------------------------------------------------------------

phase_cleanup() {
    phase "Phase 7 — cleanup"

    rm -rf "${DROPBOX_DIR:?}/$TEST_SUBDIR" 2>/dev/null || true
    note "test fixtures removed from Dropbox folder"

    # Defensive backstop for the slow-sweep marker. Honoring a stale marker
    # on a future install would silently pad every initial sweep, so make
    # sure phase 7 cleans it up even when phase 5 returned early.
    rm -f "$DBXIGNORE_STATE_DIR/_test_slow_sweep" 2>/dev/null || true

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

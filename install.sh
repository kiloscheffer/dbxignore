#!/bin/sh
# dbxignore installer for macOS and Linux.
#
# Usage:
#   curl -fsSL https://dbxignore.com/install.sh | sh
#   curl -fsSL https://dbxignore.com/install.sh | sh -s -- --uninstall
#   curl -fsSL https://dbxignore.com/install.sh | sh -s -- --no-daemon --no-modify-path
#
# Environment:
#   DBXIGNORE_VERSION          pin a release, e.g. 1.2.3 (default: latest)
#   DBXIGNORE_INSTALL_TARBALL  install from a local tarball instead of downloading
set -eu

REPO="kiloscheffer/dbxignore"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/dbxignore"
BIN_DIR="$HOME/.local/bin"
SYMLINK="$BIN_DIR/dbxignore"
BLOCK_START="# >>> dbxignore >>>"
BLOCK_END="# <<< dbxignore <<<"

opt_uninstall=0
opt_daemon=1
opt_modify_path=1
path_note=""

info() { printf 'dbxignore: %s\n' "$*"; }
warn() { printf 'dbxignore: %s\n' "$*" >&2; }
die()  { printf 'dbxignore: error: %s\n' "$*" >&2; exit 1; }

for arg in "$@"; do
  case "$arg" in
    --uninstall)      opt_uninstall=1 ;;
    --no-daemon)      opt_daemon=0 ;;
    --no-modify-path) opt_modify_path=0 ;;
    -h|--help)
      printf '%s\n' "dbxignore installer. Options: --uninstall --no-daemon --no-modify-path"
      exit 0 ;;
    *) die "unknown option: $arg (see --help)" ;;
  esac
done

detect_asset() {
  os=$(uname -s)
  arch=$(uname -m)
  case "$os/$arch" in
    Darwin/arm64) asset="dbxignore-macos-arm64.tar.gz" ;;
    Linux/x86_64) asset="dbxignore-linux-x86_64.tar.gz" ;;
    *) die "no pre-built binary for $os/$arch. Install with: pip install dbxignore (or: uv tool install dbxignore)" ;;
  esac
}

# Sets `profile` and `path_line` for the user's shell; returns 1 if unknown.
profile_for_shell() {
  shell_name=$(basename "${SHELL:-sh}")
  case "$shell_name" in
    zsh)
      profile="$HOME/.zshrc"
      path_line="export PATH=\"$BIN_DIR:\$PATH\"" ;;
    bash)
      if [ "$(uname -s)" = Darwin ]; then
        profile="$HOME/.bash_profile"
      else
        profile="$HOME/.bashrc"
      fi
      path_line="export PATH=\"$BIN_DIR:\$PATH\"" ;;
    fish)
      profile="$HOME/.config/fish/config.fish"
      path_line="fish_add_path \"$BIN_DIR\"" ;;
    *) return 1 ;;
  esac
  return 0
}

update_path() {
  case ":$PATH:" in
    *":$BIN_DIR:"*) return 0 ;;  # already on PATH — nothing to do
  esac
  if [ "$opt_modify_path" -eq 0 ]; then
    path_note="PATH not modified (--no-modify-path); add $BIN_DIR to your PATH."
    return 0
  fi
  if ! profile_for_shell; then
    path_note="Unrecognized shell '$shell_name'; add $BIN_DIR to your PATH."
    return 0
  fi
  if [ -f "$profile" ] && grep -qF "$BLOCK_START" "$profile"; then
    path_note="$BIN_DIR already configured in $profile."
    return 0
  fi
  mkdir -p "$(dirname "$profile")"
  if printf '\n%s\n%s\n%s\n' "$BLOCK_START" "$path_line" "$BLOCK_END" >> "$profile" 2>/dev/null; then
    path_note="Added $BIN_DIR to PATH in $profile — open a new shell or run: . $profile"
  else
    path_note="Could not write $profile; add $BIN_DIR to your PATH manually."
  fi
}

remove_path_block() {
  profile_for_shell || return 0
  [ -f "$profile" ] || return 0
  grep -qF "$BLOCK_START" "$profile" || return 0
  tmp=$(mktemp)
  if sed "/^${BLOCK_START}\$/,/^${BLOCK_END}\$/d" "$profile" > "$tmp"; then
    mv "$tmp" "$profile"
    info "removed the PATH block from $profile"
  else
    rm -f "$tmp"
    warn "could not edit $profile; remove the dbxignore PATH block by hand"
  fi
}

do_uninstall() {
  # Locate the installed executable to deregister the daemon. Assumes a
  # non-tampered install — if the install directory and the symlink were
  # both removed by hand, daemon deregistration is skipped.
  dbx=""
  if [ -x "$DATA_DIR/dbxignore" ]; then
    dbx="$DATA_DIR/dbxignore"
  elif command -v dbxignore >/dev/null 2>&1; then
    dbx="dbxignore"
  fi
  if [ -n "$dbx" ]; then
    info "removing the daemon ($dbx uninstall)"
    "$dbx" uninstall || warn "dbxignore uninstall reported an error; continuing"
  fi
  rm -f "$SYMLINK"
  rm -rf "$DATA_DIR"
  remove_path_block
  info "uninstalled. Ignore markers are untouched (run 'dbxignore uninstall --purge' before uninstalling to also clear them)."
}

do_install() {
  detect_asset
  tmp_dir=$(mktemp -d)
  trap 'rm -rf "$tmp_dir"' EXIT
  tarball="$tmp_dir/$asset"

  if [ -n "${DBXIGNORE_INSTALL_TARBALL:-}" ]; then
    info "installing from local tarball: $DBXIGNORE_INSTALL_TARBALL"
    cp "$DBXIGNORE_INSTALL_TARBALL" "$tarball"
  else
    if [ -n "${DBXIGNORE_VERSION:-}" ]; then
      url="https://github.com/$REPO/releases/download/v$DBXIGNORE_VERSION/$asset"
    else
      url="https://github.com/$REPO/releases/latest/download/$asset"
    fi
    command -v curl >/dev/null 2>&1 \
      || die "curl is required to download dbxignore; install curl, or set DBXIGNORE_INSTALL_TARBALL to a local tarball"
    info "downloading $url"
    curl -fsSL "$url" -o "$tarball" || die "download failed: $url"
  fi

  info "installing to $DATA_DIR"
  rm -rf "$DATA_DIR"
  mkdir -p "$DATA_DIR"
  # The tarball's single top-level directory is dbxignore/; --strip-components=1
  # drops it so the executable and _internal/ land directly in DATA_DIR.
  tar -xzf "$tarball" -C "$DATA_DIR" --strip-components=1 || die "failed to extract $tarball"
  [ -x "$DATA_DIR/dbxignore" ] || die "extracted bundle has no dbxignore executable"

  mkdir -p "$BIN_DIR"
  ln -sf "$DATA_DIR/dbxignore" "$SYMLINK"
  info "linked $SYMLINK -> $DATA_DIR/dbxignore"

  update_path

  if [ "$opt_daemon" -eq 1 ]; then
    info "registering the daemon (dbxignore install)"
    "$DATA_DIR/dbxignore" install || warn "dbxignore install reported an error; re-run 'dbxignore install' later"
  fi

  printf '\n'
  info "done — dbxignore is installed."
  [ -n "$path_note" ] && info "$path_note"
  [ "$opt_daemon" -eq 0 ] && info "daemon not registered (--no-daemon); run 'dbxignore install' when ready."
  info "verify with: dbxignore status"
}

if [ "$opt_uninstall" -eq 1 ]; then
  do_uninstall
else
  do_install
fi

#!/usr/bin/env bash

set -euo pipefail

PREFIX="${VKLAUNCHER_PREFIX:-${XDG_DATA_HOME:-$HOME/.local/share}/vklauncher}"
BIN_DIR="${VKLAUNCHER_BIN_DIR:-$HOME/.local/bin}"

if [[ -f "$PREFIX/bin_dir" ]]; then
  BIN_DIR="$(cat "$PREFIX/bin_dir")"
fi

say() { printf '==> %s\n' "$*"; }

say "Removing CLI symlink"
rm -f "$BIN_DIR/vklauncher"

say "Removing install prefix: $PREFIX"
rm -rf "$PREFIX"

if [[ "$(uname -s)" == "Darwin" ]]; then
  say "Removing app bundle"
  rm -rf "$HOME/Applications/vklauncher.app"
else
  say "Removing desktop entry and icons"
  rm -f "$HOME/.local/share/applications/vklauncher.desktop"
  rm -f "$HOME/.local/share/icons/hicolor/256x256/apps/vklauncher.png"
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
  fi
fi

say "vklauncher uninstalled"

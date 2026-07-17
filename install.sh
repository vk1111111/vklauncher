#!/usr/bin/env bash

set -euo pipefail

REPO="${VKLAUNCHER_REPO:-vk1111111/vklauncher}"
PREFIX="${VKLAUNCHER_PREFIX:-${XDG_DATA_HOME:-$HOME/.local/share}/vklauncher}"
BIN_DIR="${VKLAUNCHER_BIN_DIR:-$HOME/.local/bin}"
VERSION="${VKLAUNCHER_VERSION:-latest}"
TMPDIR="${TMPDIR:-/tmp}"
WORKDIR="$(mktemp -d "${TMPDIR%/}/vklauncher-install.XXXXXX")"

cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

say() { printf '==> %s\n' "$*"; }
err() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || err "required command not found: $1"; }

need curl
need unzip
need uname

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH_RAW="$(uname -m)"
case "$ARCH_RAW" in
  x86_64|amd64) ARCH="x86_64" ;;
  arm64|aarch64) ARCH="arm64" ;;
  *) err "unsupported architecture: $ARCH_RAW" ;;
esac

case "$OS" in
  linux)  PLATFORM="linux" ;;
  darwin) PLATFORM="macos" ;;
  *) err "unsupported OS: $OS (use install.ps1 on Windows)" ;;
esac

ASSET="vklauncher-${PLATFORM}-${ARCH}.zip"

if [[ "$VERSION" == "latest" ]]; then
  DOWNLOAD_URL="https://github.com/${REPO}/releases/latest/download/${ASSET}"
else
  DOWNLOAD_URL="https://github.com/${REPO}/releases/download/${VERSION}/${ASSET}"
fi

say "Installing vklauncher (${PLATFORM}-${ARCH})"
say "Download: ${DOWNLOAD_URL}"

ZIP="$WORKDIR/$ASSET"
if ! curl -fsSL --location --output "$ZIP" "$DOWNLOAD_URL"; then
  err "failed to download ${ASSET}. Publish a release first, or set VKLAUNCHER_VERSION."
fi

unzip -qo "$ZIP" -d "$WORKDIR/extract"
BIN_SRC="$(find "$WORKDIR/extract" -type f \( -name vklauncher -o -name vklauncher.exe \) | head -n1)"
[[ -n "$BIN_SRC" ]] || err "archive did not contain a vklauncher binary"

mkdir -p "$PREFIX/bin" "$BIN_DIR"
install -m 755 "$BIN_SRC" "$PREFIX/bin/vklauncher"
ln -sfn "$PREFIX/bin/vklauncher" "$BIN_DIR/vklauncher"

# Prefer packaged icons from the release zip; fall back to repo raw assets.
pick_icon() {
  local preferred="$1"
  local fallback="$2"
  local found=""
  found="$(find "$WORKDIR/extract" -type f -name "$preferred" 2>/dev/null | head -n1 || true)"
  if [[ -z "$found" ]]; then
    found="$(find "$WORKDIR/extract" -type f -name "$fallback" 2>/dev/null | head -n1 || true)"
  fi
  if [[ -z "$found" ]]; then
    found="$(find "$WORKDIR/extract" -type f \( -name 'vklauncher.png' -o -name 'vklauncher.ico' \) 2>/dev/null | head -n1 || true)"
  fi
  printf '%s' "$found"
}

if [[ "$PLATFORM" == "macos" ]]; then
  ICON_SRC="$(pick_icon icon_mac.png icon_universal.png)"
else
  ICON_SRC="$(pick_icon icon_universal.png icon_mac.png)"
fi

if [[ -z "$ICON_SRC" ]]; then
  mkdir -p "$WORKDIR/icons"
  if [[ "$PLATFORM" == "macos" ]]; then
    curl -fsSL "https://raw.githubusercontent.com/${REPO}/main/assets/icon_mac.png" \
      -o "$WORKDIR/icons/icon.png" || true
  else
    curl -fsSL "https://raw.githubusercontent.com/${REPO}/main/assets/icon_universal.png" \
      -o "$WORKDIR/icons/icon.png" || true
  fi
  [[ -f "$WORKDIR/icons/icon.png" ]] && ICON_SRC="$WORKDIR/icons/icon.png"
fi

install_linux_desktop() {
  local icon_dir="$HOME/.local/share/icons/hicolor/256x256/apps"
  local app_dir="$HOME/.local/share/applications"
  mkdir -p "$icon_dir" "$app_dir" "$PREFIX/share"

  if [[ -n "${ICON_SRC:-}" ]]; then
    # Scale to 256 if possible; otherwise copy as-is.
    if command -v convert >/dev/null 2>&1; then
      convert "$ICON_SRC" -resize 256x256 "$icon_dir/vklauncher.png"
    elif command -v magick >/dev/null 2>&1; then
      magick "$ICON_SRC" -resize 256x256 "$icon_dir/vklauncher.png"
    else
      cp "$ICON_SRC" "$icon_dir/vklauncher.png"
    fi
    cp "$ICON_SRC" "$PREFIX/share/icon.png"
  fi

  cat > "$app_dir/vklauncher.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=vklauncher
GenericName=Minecraft Launcher
Comment=Terminal UI Minecraft Java launcher
Exec=${BIN_DIR}/vklauncher
Icon=vklauncher
Terminal=true
Categories=Game;PackageManager;
Keywords=minecraft;launcher;fabric;quilt;
StartupNotify=false
EOF
  chmod 644 "$app_dir/vklauncher.desktop"
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$app_dir" >/dev/null 2>&1 || true
  fi
  if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
  fi
  say "Desktop entry: $app_dir/vklauncher.desktop"
}

install_macos_app() {
  local app="$HOME/Applications/vklauncher.app"
  local contents="$app/Contents"
  local macos_dir="$contents/MacOS"
  local res="$contents/Resources"
  mkdir -p "$macos_dir" "$res" "$HOME/Applications"

  install -m 755 "$PREFIX/bin/vklauncher" "$macos_dir/vklauncher-bin"

  # Wrapper opens Terminal.app so the TUI has a real tty.
  cat > "$macos_dir/vklauncher" <<'EOF'
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
osascript <<APPLESCRIPT
tell application "Terminal"
  do script "\"$DIR/vklauncher-bin\"; exit"
  activate
end tell
APPLESCRIPT
EOF
  chmod 755 "$macos_dir/vklauncher"

  if [[ -n "${ICON_SRC:-}" ]]; then
    local iconset="$WORKDIR/vklauncher.iconset"
    rm -rf "$iconset"
    mkdir -p "$iconset"
    for size in 16 32 128 256 512; do
      sips -z "$size" "$size" "$ICON_SRC" --out "$iconset/icon_${size}x${size}.png" >/dev/null
      double=$((size * 2))
      sips -z "$double" "$double" "$ICON_SRC" --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
    done
    if iconutil -c icns "$iconset" -o "$res/AppIcon.icns" 2>/dev/null; then
      say "App icon: $res/AppIcon.icns"
    else
      cp "$ICON_SRC" "$res/icon.png"
    fi
  fi

  cat > "$contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>vklauncher</string>
  <key>CFBundleIdentifier</key>
  <string>com.vklauncher.app</string>
  <key>CFBundleName</key>
  <string>vklauncher</string>
  <key>CFBundleDisplayName</key>
  <string>vklauncher</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>11.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
EOF
  say "App bundle: $app"
}

printf '%s\n' "$PREFIX" > "$PREFIX/install_prefix"
printf '%s\n' "$BIN_DIR" > "$PREFIX/bin_dir"
date -u +%Y-%m-%dT%H:%M:%SZ > "$PREFIX/installed_at" || true

if [[ "$PLATFORM" == "linux" ]]; then
  install_linux_desktop
elif [[ "$PLATFORM" == "macos" ]]; then
  install_macos_app
fi

# Ensure ~/.local/bin is mentioned if missing from PATH.
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    say "Note: add $BIN_DIR to your PATH to run 'vklauncher' from any shell."
    ;;
esac

say "Installed binary: $PREFIX/bin/vklauncher"
say "CLI symlink:      $BIN_DIR/vklauncher"
say "Done. Run: vklauncher"

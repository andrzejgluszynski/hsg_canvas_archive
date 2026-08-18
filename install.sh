#!/bin/sh
# Canvas Archive installer.
#
#   curl -fsSL https://github.com/andrzejgluszynski/hsg_canvas_archive/releases/latest/download/install.sh | sh
#
# Downloading with curl matters: files fetched this way do NOT get macOS's
# com.apple.quarantine attribute, so Gatekeeper never blocks the binary. A browser
# download does get it, and macOS then kills the program with no explanation at all.

set -eu

REPO="${CANVAS_ARCHIVE_REPO:-andrzejgluszynski/hsg_canvas_archive}"
INSTALL_DIR="${CANVAS_ARCHIVE_INSTALL_DIR:-$HOME/.local/bin}"
BIN="canvas-archive"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

os="$(uname -s)"
arch="$(uname -m)"

case "$os" in
  Darwin)
    case "$arch" in
      arm64)          asset="canvas-archive-macos-arm64" ;;
      x86_64)         asset="canvas-archive-macos-x86_64" ;;
      *)              die "unsupported macOS architecture: $arch" ;;
    esac ;;
  Linux)
    case "$arch" in
      x86_64|amd64)   asset="canvas-archive-linux-x86_64" ;;
      aarch64|arm64)  asset="canvas-archive-linux-arm64" ;;
      *)              die "unsupported Linux architecture: $arch" ;;
    esac ;;
  *)
    die "unsupported OS: $os (on Windows, download the .exe from the releases page)" ;;
esac

url="https://github.com/${REPO}/releases/latest/download/${asset}"

say ""
say "  Canvas Archive"
say "  Downloading ${asset}..."

command -v curl >/dev/null 2>&1 || die "curl is required"
mkdir -p "$INSTALL_DIR"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT INT TERM

curl -fL --progress-bar "$url" -o "$tmp" || die "download failed from $url"
[ -s "$tmp" ] || die "downloaded file is empty"

chmod +x "$tmp"
mv "$tmp" "$INSTALL_DIR/$BIN"
trap - EXIT INT TERM

# Belt and braces: strip quarantine if some other tool applied it.
if [ "$os" = "Darwin" ]; then
  xattr -d com.apple.quarantine "$INSTALL_DIR/$BIN" 2>/dev/null || true
fi

say ""
say "  Installed to $INSTALL_DIR/$BIN"

if command -v "$BIN" >/dev/null 2>&1; then
  say "  Run it with:  $BIN"
else
  say ""
  say "  $INSTALL_DIR is not on your PATH. Either run it directly:"
  say "      $INSTALL_DIR/$BIN"
  say "  or add it to your PATH:"
  say "      echo 'export PATH=\"$INSTALL_DIR:\$PATH\"' >> ~/.zshrc && exec zsh"
fi
say ""

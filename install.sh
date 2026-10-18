#!/usr/bin/env bash
# 9router-manager installer for Linux / macOS / WSL.
# Never touches the 9router config; only makes the tool runnable.
set -euo pipefail

BIN_DIR="$HOME/.local/bin"
NO_LINK=0

while [ $# -gt 0 ]; do
  case "$1" in
    --bin-dir) BIN_DIR="$2"; shift 2 ;;
    --no-link) NO_LINK=1; shift ;;
    -h|--help)
      echo "usage: ./install.sh [--no-link] [--bin-dir DIR]"
      exit 0 ;;
    *) echo "opsi tidak dikenal: $1" >&2; exit 2 ;;
  esac
done

SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  TARGET="$(readlink "$SOURCE")"
  case "$TARGET" in
    /*) SOURCE="$TARGET" ;;
    *)  SOURCE="$(cd "$(dirname "$SOURCE")" && pwd)/$TARGET" ;;
  esac
done
SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
cd "$SCRIPT_DIR"

info() { printf '  %s\n' "$1"; }
ok()   { printf '[ok] %s\n' "$1"; }
warn() { printf '[!] %s\n' "$1"; }
fail() { printf '[x] %s\n' "$1" >&2; }

echo "9router-manager installer"
echo "-------------------------"

chmod +x 9router-manager.sh 9router-manager.py 2>/dev/null || true
ok "script executable"

PYTHON=""
for candidate in python3 python py; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
      PYTHON="$candidate"
      break
    fi
  fi
done
if [ -z "$PYTHON" ]; then
  fail "Python 3.8+ tidak ditemukan"
  info "Linux : sudo apt install python3"
  info "macOS : brew install python3"
  exit 1
fi
ok "python: $($PYTHON --version 2>&1)"

if command -v 9router >/dev/null 2>&1; then
  ok "9router binary: $(command -v 9router)"
else
  warn "binary 9router tidak ada di PATH (tool tetap jalan kalau server sudah hidup)"
  info "jalankan server: npx 9router"
fi

if [ "$NO_LINK" -eq 0 ]; then
  mkdir -p "$BIN_DIR"
  ln -sf "$SCRIPT_DIR/9router-manager.sh" "$BIN_DIR/9router-manager"
  ok "symlink: $BIN_DIR/9router-manager"
  case ":$PATH:" in
    *":$BIN_DIR:"*) ok "$BIN_DIR sudah ada di PATH" ;;
    *) warn "tambahkan ke PATH:  export PATH=\"$BIN_DIR:\$PATH\"" ;;
  esac
fi

echo ""
"$PYTHON" 9router-manager.py info --no-color || true

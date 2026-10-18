#!/usr/bin/env bash
# 9router-manager launcher (Linux / macOS / WSL / Git Bash).
# Locates a Python 3.8+ interpreter and hands over to the engine.
set -euo pipefail

SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  TARGET="$(readlink "$SOURCE")"
  case "$TARGET" in
    /*) SOURCE="$TARGET" ;;
    *)  SOURCE="$(cd "$(dirname "$SOURCE")" && pwd)/$TARGET" ;;
  esac
done
SCRIPT_DIR="$(cd "$(dirname "$SOURCE")" && pwd)"
ENGINE="$SCRIPT_DIR/9router-manager.py"

if [ ! -f "$ENGINE" ]; then
  echo "[x] engine tidak ditemukan: $ENGINE" >&2
  exit 1
fi

PYTHON="${NINEROUTER_MANAGER_PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3 python py; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
      fi
    fi
  done
fi

if [ -z "$PYTHON" ]; then
  echo "[x] Python 3.8+ tidak ditemukan di PATH." >&2
  echo "    Linux : sudo apt install python3" >&2
  echo "    macOS : brew install python3" >&2
  echo "    Windows: winget install Python.Python.3.12" >&2
  exit 1
fi

exec "$PYTHON" "$ENGINE" "$@"

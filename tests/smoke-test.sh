#!/usr/bin/env bash
# Convenience wrapper: bash tests/smoke-test.sh
# On Windows just run: python tests\run_all.py
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON="${NINEROUTER_MANAGER_PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3 python py; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
  done
fi
if [ -z "$PYTHON" ]; then
  echo "[x] Python tidak ditemukan di PATH" >&2
  exit 1
fi

exec "$PYTHON" "$HERE/run_all.py" "$@"

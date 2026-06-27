#!/usr/bin/env bash
# Build the Python backend into a standalone binary for production bundling.
#
# In dev, the Rust `run_api` command just calls system `python3 api.py`. For a
# distributable .app you don't want to depend on the user's Python, so we freeze
# api.py (and the modules + data it imports) into a single binary with
# PyInstaller and hand it to Tauri as a sidecar.
#
# Usage:  ./build-sidecar.sh
# Result: src-tauri/binaries/audit-api-<target-triple>
set -euo pipefail

cd "$(dirname "$0")/.."   # repo root (where api.py lives)

if ! command -v pyinstaller >/dev/null 2>&1; then
  echo "pyinstaller not found. Install it with:  pip install pyinstaller"
  exit 1
fi

# Tauri expects sidecar binaries suffixed with the Rust target triple.
TRIPLE="$(rustc -Vv | grep host | cut -d' ' -f2)"
OUT_DIR="desktop/src-tauri/binaries"
mkdir -p "$OUT_DIR"

echo "Freezing api.py for $TRIPLE ..."
pyinstaller --onefile --name "audit-api" \
  --add-data "data:data" \
  --hidden-import scanner --hidden-import caches \
  --hidden-import privacy --hidden-import sar \
  api.py

cp "dist/audit-api" "$OUT_DIR/audit-api-$TRIPLE"
echo "Wrote $OUT_DIR/audit-api-$TRIPLE"
echo
echo "Next: add an \"externalBin\" entry to tauri.conf.json bundle config:"
echo '  "bundle": { "externalBin": ["binaries/audit-api"] }'
echo "and point APP_AUDIT_API/APP_AUDIT_PYTHON at the sidecar in production."

#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
VERSION=$($PYTHON_BIN "$ROOT/scripts/version.py" show)
NAME="opencode-multiuser-${VERSION}"
DIST_DIR=${1:-"$ROOT/dist"}
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

command -v rsync >/dev/null 2>&1 || { echo "ERROR: rsync is required" >&2; exit 1; }
command -v zip >/dev/null 2>&1 || { echo "ERROR: zip is required" >&2; exit 1; }

mkdir -p "$DIST_DIR" "$STAGE/$NAME"
rsync -a \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '.pytest_cache' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'build' \
  --exclude '*.egg-info' \
  --exclude 'dist' \
  "$ROOT/" "$STAGE/$NAME/"

rm -f "$DIST_DIR/$NAME.zip" "$DIST_DIR/$NAME.zip.sha256"
(
  cd "$STAGE"
  zip -qr "$DIST_DIR/$NAME.zip" "$NAME"
)
(
  cd "$DIST_DIR"
  sha256sum "$NAME.zip" > "$NAME.zip.sha256"
)

echo "$DIST_DIR/$NAME.zip"
echo "$DIST_DIR/$NAME.zip.sha256"

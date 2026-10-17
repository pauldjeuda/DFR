#!/bin/sh
set -eu
ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
BUILD_DIR="$ROOT/build/package"
ARCHIVE="$ROOT/build/source.tar"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
tar --exclude=.git --exclude=.venv --exclude=build --exclude=__pycache__ \
  --exclude=.pytest_cache --exclude=.ruff_cache -cf "$ARCHIVE" -C "$ROOT" .
tar -xf "$ARCHIVE" -C "$BUILD_DIR"
rm -f "$ARCHIVE"
cp -a "$ROOT/packaging/debian" "$BUILD_DIR/debian"
chmod 0755 "$BUILD_DIR/debian/rules" "$BUILD_DIR/debian/dfr.postinst" "$BUILD_DIR/debian/dfr.prerm" "$BUILD_DIR/debian/dfr.postrm"
chmod 0755 "$BUILD_DIR/scripts/"*.sh "$BUILD_DIR/scripts/dfrd" "$BUILD_DIR/scripts/dfr-ctl" "$BUILD_DIR/scripts/dfr-web" "$BUILD_DIR/scripts/ssh-gate"
cd "$BUILD_DIR"
dpkg-buildpackage -us -uc -b

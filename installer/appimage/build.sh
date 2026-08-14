#!/usr/bin/env bash
# Build AIDirector-x86_64.AppImage. No root required.
#
#   installer/appimage/build.sh [output-dir]
#
# The AppImage carries the project source, uv, and licenses. On first run
# it copies the app into ~/.local/share/aidirector, builds the locked
# Python environment there with uv (which also fetches a managed CPython
# if the host lacks 3.11+), and launches `aidirector app`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:-$ROOT/dist}"
CACHE="$ROOT/installer/cache"
WORK="$(mktemp -d /tmp/aidirector-appimage.XXXXXX)"
APPDIR="$WORK/AIDirector.AppDir"
trap 'rm -rf "$WORK"' EXIT

VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml" | head -1)"
STAMP="$VERSION-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d)"
echo "==> building AI Director $STAMP"

# --- 1. payload -------------------------------------------------------
mkdir -p "$APPDIR/usr/app/desktop" "$APPDIR/usr/bin"
rsync -a \
  "$ROOT/pyproject.toml" "$ROOT/uv.lock" "$ROOT/src" "$ROOT/config" \
  "$ROOT/assets" "$ROOT/README.md" "$ROOT/README.ja.md" \
  "$ROOT/LICENSE" "$ROOT/AGENT.md" \
  "$APPDIR/usr/app/"
cp "$ROOT/desktop/bootstrap.py" "$APPDIR/usr/app/desktop/"

# --- 2. third-party licenses ------------------------------------------
if [ -f "$ROOT/THIRD_PARTY_LICENSES.md" ]; then
  cp "$ROOT/THIRD_PARTY_LICENSES.md" "$APPDIR/usr/app/"
else
  echo "WARNING: THIRD_PARTY_LICENSES.md missing — run scripts/generate_third_party_licenses.py" >&2
fi

# --- 3. uv (static binary) ---------------------------------------------
mkdir -p "$CACHE"
UV_TGZ="$CACHE/uv-x86_64-unknown-linux-gnu.tar.gz"
if [ ! -f "$UV_TGZ" ]; then
  echo "==> downloading uv"
  curl -fsSL -o "$UV_TGZ" \
    "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz"
fi
tar -xzf "$UV_TGZ" -C "$WORK"
cp "$WORK"/uv-*/uv "$APPDIR/usr/bin/uv"
chmod +x "$APPDIR/usr/bin/uv"

# --- 4. AppRun / desktop integration -----------------------------------
cat > "$APPDIR/AppRun" <<APPRUN
#!/bin/sh
# AI Director launcher. The AppImage mount is read-only, so the app is
# copied to a writable location on first run (or after an update).
set -e
HERE="\$(dirname "\$(readlink -f "\$0")")"
DATA="\${XDG_DATA_HOME:-\$HOME/.local/share}/aidirector"
APP="\$DATA/app"
STAMP="$STAMP"

if [ ! -f "\$APP/.stamp" ] || [ "\$(cat "\$APP/.stamp")" != "\$STAMP" ]; then
    echo "[aidirector] installing app files to \$APP"
    mkdir -p "\$APP"
    cp -a "\$HERE/usr/app/." "\$APP/"
    printf '%s' "\$STAMP" > "\$APP/.stamp"
fi

export PATH="\$HERE/usr/bin:\$PATH"
cd "\$APP"
PY="\$(command -v python3 || true)"
if [ -n "\$PY" ]; then
    exec "\$PY" desktop/bootstrap.py "\$@"
fi
# No system Python at all: let uv provide everything.
uv sync --frozen --no-dev --extra speech --extra vision --extra embedding --extra web
exec uv run --no-sync aidirector app "\$@"
APPRUN
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/aidirector.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=AI Director
Comment=Local AI video director
Exec=aidirector
Icon=aidirector
Terminal=false
Categories=AudioVideo;Video;AudioVideoEditing;
DESKTOP
cp "$ROOT/assets/icon/icon.png" "$APPDIR/aidirector.png"
cp "$APPDIR/aidirector.png" "$APPDIR/.DirIcon"

# --- 5. pack ------------------------------------------------------------
TOOL="$CACHE/appimagetool-x86_64.AppImage"
if [ ! -f "$TOOL" ]; then
  echo "==> downloading appimagetool"
  curl -fsSL -o "$TOOL" \
    "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
  chmod +x "$TOOL"
fi
# Pre-fetch the AppImage runtime; appimagetool's own download can stall.
RUNTIME="$CACHE/runtime-x86_64"
if [ ! -f "$RUNTIME" ]; then
  echo "==> downloading AppImage runtime"
  curl -fsSL -o "$RUNTIME" \
    "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"
fi
mkdir -p "$OUT"
TARGET="$OUT/AIDirector-$VERSION-x86_64.AppImage"
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 \
  "$TOOL" --no-appstream --runtime-file "$RUNTIME" "$APPDIR" "$TARGET"
echo "==> built: $TARGET ($(du -h "$TARGET" | cut -f1))"

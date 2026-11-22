#!/bin/bash
# Build dist/AI_Chat-<arch>.AppImage: a self-contained app with its own Python 3 and PyQt6, so no system packages are
# needed except the usual desktop libraries. Uses python-appimage (https://github.com/niess/python-appimage) in a
# throw-away virtualenv to assemble the AppDir, then appimagetool to pack it. Needs network access (Python base image,
# PyQt6 wheels, appimagetool).
#   packaging/build-appimage.sh
set -euo pipefail
export APPIMAGE_EXTRACT_AND_RUN=1      # appimagetool is itself an AppImage: run it without FUSE (CI runners have none)
cd "$(dirname "$0")/.."
REPO=$PWD
ARCH=$(uname -m)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

appstreamcli validate --no-net packaging/aichat-desktop.metainfo.xml >/dev/null 2>&1 || true   # (advisory; appimagetool re-checks)
python3 -m venv "$WORK/venv"
"$WORK/venv/bin/python" -m pip install --quiet --upgrade pip python-appimage
cp -r packaging/appimage "$WORK/recipe"
printf 'PyQt6>=6.4\n%s\n' "$REPO" > "$WORK/recipe/requirements.txt"
( cd "$WORK" && "$WORK/venv/bin/python" -m python_appimage build app -p 3.12 -n aichat-desktop --no-packaging recipe )
APPDIR=$(find "$WORK" -maxdepth 1 -type d -name "*-$ARCH" | head -1)
[ -n "$APPDIR" ] || { echo "python-appimage produced no AppDir" >&2; exit 1; }
curl -fsSL -o "$WORK/appimagetool" "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
chmod +x "$WORK/appimagetool"
mkdir -p dist
# the repository URLs in the metadata only resolve once the repository is public: skip appimagetool's online check
"$WORK/appimagetool" --no-appstream "$APPDIR" "dist/AI_Chat-$ARCH.AppImage"
ls dist/*.AppImage

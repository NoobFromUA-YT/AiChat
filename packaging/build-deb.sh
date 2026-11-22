#!/bin/bash
# Build dist/aichat-desktop_<version>_all.deb from this checkout (needs dpkg-deb; nothing is installed).
#   packaging/build-deb.sh
# The package is architecture independent: the app is plain Python in /usr/lib/aichat-desktop, started by
# /usr/bin/aichat-desktop with the distro's python3 and python3-pyqt6. Claude Code itself is not packaged by
# Ubuntu / Debian: install it from https://claude.com/claude-code (the app says so on first start if `claude` is missing).
set -euo pipefail
cd "$(dirname "$0")/.."
PKG=aichat-desktop
MOD=aichat_desktop
LIB=/usr/lib/$PKG
VERSION=$(python3 -c 'import re,pathlib;print(re.search(r"__version__ = \"([^\"]+)\"",pathlib.Path("aichat_desktop/__init__.py").read_text()).group(1))')
MAINTAINER="NoobFromUA <358082442+NoobFromUA-YT@users.noreply.github.com>"
HOMEPAGE=https://github.com/NoobFromUA-YT/AiChat
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT

install -d "$ROOT/DEBIAN" "$ROOT/usr/bin" "$ROOT$LIB" "$ROOT/usr/share/applications" "$ROOT/usr/share/man/man1" \
    "$ROOT/usr/share/doc/$PKG" "$ROOT/usr/share/icons/hicolor/scalable/apps" "$ROOT/usr/share/metainfo"
cp -r "$MOD" "$ROOT$LIB/$MOD"
find "$ROOT$LIB" -name __pycache__ -type d -prune -exec rm -rf {} +
# the extra icon sizes are installed under hicolor, not inside the Python package
rm -f "$ROOT$LIB/$MOD"/assets/icon-*.png

cat > "$ROOT/usr/bin/$PKG" <<LAUNCH
#!/bin/sh
PYTHONPATH="$LIB\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m $MOD "\$@"
LAUNCH

install -m 644 packaging/$PKG.desktop "$ROOT/usr/share/applications/$PKG.desktop"
for n in 16 32 48 64 128 512; do
    install -D -m 644 "$MOD/assets/icon-$n.png" "$ROOT/usr/share/icons/hicolor/${n}x${n}/apps/$PKG.png"
done
install -D -m 644 "$MOD/assets/icon.png" "$ROOT/usr/share/icons/hicolor/256x256/apps/$PKG.png"
install -m 644 "$MOD/assets/icon.svg" "$ROOT/usr/share/icons/hicolor/scalable/apps/$PKG.svg"
install -m 644 packaging/$PKG.metainfo.xml "$ROOT/usr/share/metainfo/io.github.noobfromua_yt.aichat_desktop.metainfo.xml"
install -m 644 LICENSE "$ROOT/usr/share/doc/$PKG/copyright"
gzip -9n -c packaging/$PKG.1 > "$ROOT/usr/share/man/man1/$PKG.1.gz"
{
    echo "$PKG ($VERSION) unstable; urgency=low"
    echo
    echo "  * Release $VERSION. The full history is CHANGELOG.md in the source repository ($HOMEPAGE)."
    echo
    echo " -- $MAINTAINER  $(date -R)"
} | gzip -9n -c > "$ROOT/usr/share/doc/$PKG/changelog.gz"

# Debian policy: directories 755, files 644, only the launcher and maintainer scripts executable
find "$ROOT" -type d -exec chmod 755 {} +
find "$ROOT/usr" -type f -exec chmod 644 {} +
chmod 755 "$ROOT/usr/bin/$PKG"

cat > "$ROOT/DEBIAN/control" <<CONTROL
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-pyqt6
Recommends: fonts-liberation
Maintainer: $MAINTAINER
Homepage: $HOMEPAGE
Description: Desktop chat window for Claude Code
 A PyQt6 chat window around the Claude Code command line: live tool and text
 streaming, a project folder per chat, saved chats with Markdown export,
 read-only / edit / full-auto permission modes, queued messages, themes and an
 optional credit counter. Needs the separate "claude" command. Not affiliated
 with or endorsed by Anthropic.
CONTROL
SIZE=$(du -sk "$ROOT/usr" | cut -f1)
sed -i "s/^Section: utils/Section: utils\nInstalled-Size: $SIZE/" "$ROOT/DEBIAN/control"
( cd "$ROOT" && find usr -type f -print0 | sort -z | xargs -0 md5sum ) > "$ROOT/DEBIAN/md5sums"
chmod 644 "$ROOT/DEBIAN/md5sums"

cat > "$ROOT/DEBIAN/postinst" <<POST
#!/bin/sh
set -e
python3 -m compileall -q "\${DPKG_ROOT:-}$LIB" >/dev/null 2>&1 || true
exit 0
POST
cat > "$ROOT/DEBIAN/postrm" <<POST
#!/bin/sh
set -e
case "\$1" in remove|purge) rm -rf "\${DPKG_ROOT:-}$LIB" ;; esac
exit 0
POST
chmod 755 "$ROOT/DEBIAN/postinst" "$ROOT/DEBIAN/postrm"

mkdir -p dist
dpkg-deb --root-owner-group --build "$ROOT" "dist/${PKG}_${VERSION}_all.deb" >/dev/null
echo "dist/${PKG}_${VERSION}_all.deb"

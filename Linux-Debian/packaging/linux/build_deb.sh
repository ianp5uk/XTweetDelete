#!/bin/bash
# Builds the TweetDelete .deb package from the shared source tree
# (server.py, public/) plus the packaging metadata in this folder.
#
# Run from the project root:
#   bash packaging/linux/build_deb.sh
#
# Produces: packaging/linux/output/tweetdelete_<version>_all.deb
set -euo pipefail

VERSION="1.0.0"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
STAGE="$SCRIPT_DIR/stage"
OUT_DIR="$SCRIPT_DIR/output"
PKG_NAME="tweetdelete_${VERSION}_all"

echo "Project root: $PROJECT_ROOT"
rm -rf "$STAGE" "$OUT_DIR"
mkdir -p "$STAGE" "$OUT_DIR"

# ---- Directory layout ----
mkdir -p "$STAGE/DEBIAN"
mkdir -p "$STAGE/usr/lib/tweetdelete"
mkdir -p "$STAGE/usr/lib/systemd/user"
mkdir -p "$STAGE/usr/share/applications"
mkdir -p "$STAGE/usr/share/doc/tweetdelete"
mkdir -p "$STAGE/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$STAGE/usr/bin"

# ---- App files (the same source used by the Windows build) ----
cp "$PROJECT_ROOT/server.py" "$STAGE/usr/lib/tweetdelete/server.py"
cp -r "$PROJECT_ROOT/public" "$STAGE/usr/lib/tweetdelete/public"

# ---- Packaging metadata ----
sed "s/__VERSION__/$VERSION/" "$SCRIPT_DIR/control" > "$STAGE/DEBIAN/control"
install -m 0755 "$SCRIPT_DIR/postinst" "$STAGE/DEBIAN/postinst"
install -m 0755 "$SCRIPT_DIR/prerm" "$STAGE/DEBIAN/prerm"

install -m 0644 "$SCRIPT_DIR/tweetdelete.service" "$STAGE/usr/lib/systemd/user/tweetdelete.service"
install -m 0644 "$SCRIPT_DIR/tweetdelete.desktop" "$STAGE/usr/share/applications/tweetdelete.desktop"
install -m 0644 "$SCRIPT_DIR/copyright" "$STAGE/usr/share/doc/tweetdelete/copyright"
install -m 0755 "$SCRIPT_DIR/tweetdelete-launcher.sh" "$STAGE/usr/bin/tweetdelete"

# Debian policy: changelog required (even a minimal one), gzip-compressed
# in the installed doc directory. Named changelog.gz rather than
# changelog.Debian.gz because this version string has no upstream/debian-
# revision separator (e.g. "1.0.0-1"), which makes this a *native* Debian
# package in dpkg's terms - correct here, since there's no separate
# upstream tarball this packaging tracks against.
{
  echo "tweetdelete ($VERSION) unstable; urgency=low"
  echo
  echo "  * Initial Linux package."
  echo
  echo " -- TweetDelete <noreply@example.invalid>  $(date -R)"
} > "$STAGE/usr/share/doc/tweetdelete/changelog"
gzip -9 -n "$STAGE/usr/share/doc/tweetdelete/changelog"

# ---- Icon ----
# Linux desktop icons use PNG via the hicolor icon theme convention, not
# .ico. Re-export the same source icon (packaging/icon.ico, already built
# at 256x256 for the Windows build) as a PNG at that size.
python3 "$SCRIPT_DIR/make_icon_png.py" \
  "$SCRIPT_DIR/../icon.ico" \
  "$STAGE/usr/share/icons/hicolor/256x256/apps/tweetdelete.png"

# ---- Permissions sanity pass ----
find "$STAGE/usr/lib/tweetdelete" -type f -exec chmod 0644 {} \;
find "$STAGE/usr/lib/tweetdelete" -type d -exec chmod 0755 {} \;
chmod 0755 "$STAGE/usr/lib/tweetdelete/server.py"

# ---- Build ----
# fakeroot ensures the files inside the .deb are owned by root:root without
# requiring this script itself to run as root.
fakeroot dpkg-deb --build --root-owner-group "$STAGE" "$OUT_DIR/${PKG_NAME}.deb"

echo
echo "Built: $OUT_DIR/${PKG_NAME}.deb"
echo "Inspect with:   dpkg -I \"$OUT_DIR/${PKG_NAME}.deb\""
echo "List files with: dpkg -c \"$OUT_DIR/${PKG_NAME}.deb\""
echo "Install with:   sudo apt install \"$OUT_DIR/${PKG_NAME}.deb\""

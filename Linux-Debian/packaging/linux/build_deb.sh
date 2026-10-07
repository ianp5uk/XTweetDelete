#!/bin/bash
# Builds the TweetDelete .deb package from the shared source tree
# (server.py, public/) plus the packaging metadata in this folder.
#
# Run from the project root:
#   bash packaging/linux/build_deb.sh
#
# Produces: packaging/linux/output/tweetdelete_<version>_all.deb
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
STAGE="$SCRIPT_DIR/stage"
OUT_DIR="$SCRIPT_DIR/output"

# The version lives in server.py (VERSION = "...") - the single source of
# truth that the footer, the .exe file properties and this package's
# metadata all read - so they can never drift apart.
VERSION="$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$PROJECT_ROOT/server.py")"
if [ -z "$VERSION" ]; then
    echo "Could not read VERSION from $PROJECT_ROOT/server.py - it must contain a line like:  VERSION = \"1.0.4.2\"" >&2
    exit 1
fi
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
cp "$PROJECT_ROOT/runner.py" "$STAGE/usr/lib/tweetdelete/runner.py"
cp "$PROJECT_ROOT/launch_window.py" "$STAGE/usr/lib/tweetdelete/launch_window.py"
cp -r "$PROJECT_ROOT/public" "$STAGE/usr/lib/tweetdelete/public"

# The shared public/ tree carries every platform's help guide (server.py
# picks the right one per platform at runtime), but the Debian package
# should not ship Windows documentation: keep only this platform's guides.
find "$STAGE/usr/lib/tweetdelete/public" -maxdepth 1 -type f \
     -name "TweetDelete for *.pdf" \
     ! -name "TweetDelete for Debian.pdf" \
     ! -name "TweetDelete for Linux.pdf" \
     ! -name "TweetDelete for Ubuntu.pdf" \
     -delete

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
  echo "  * Require browser revalidation of HTML, JavaScript and CSS to"
  echo "    prevent stale UI files after upgrades. Unchanged assets retain"
  echo "    Last-Modified/304 support; helper JSON remains no-store."
  echo
  echo " -- TweetDelete <noreply@example.invalid>  $(date -R)"
  echo
  echo "tweetdelete (1.0.4.2) unstable; urgency=low"
  echo
  echo "  * Help guide PDF shipped in the package again; server.py picks"
  echo "    this platform's own guide from the ones bundled in public/."
  echo "  * The app now shows its version in the footer. The version comes"
  echo "    from server.py's VERSION constant - the single source the Debian"
  echo "    package, the Windows .exe file properties and the installer all"
  echo "    read - so a stale install is visible at a glance."
  echo
  echo " -- TweetDelete <noreply@example.invalid>  $(date -R)"
  echo
  echo "tweetdelete (1.0.4.1) unstable; urgency=low"
  echo
  echo "  * Re-added the help guide PDF."
  echo
  echo " -- TweetDelete <noreply@example.invalid>  Mon, 05 Oct 2026 12:00:00 +0000"
  echo
  echo "tweetdelete (1.0.4) unstable; urgency=low"
  echo
  echo "  * Deletions now run in the background helper (runner.py), not the"
  echo "    browser tab, so rate-limit waits finish on time even when the tab"
  echo "    is hidden or closed. Runs resume after a restart."
  echo "  * Live m:ss countdown to the next batch, plus a 'Waiting for X to"
  echo "    accept resumption' message if X refuses after the wait."
  echo "  * Opens as a compact app window sized to the app."
  echo
  echo " -- TweetDelete <noreply@example.invalid>  Mon, 22 Sep 2026 12:00:00 +0000"
  echo
  echo "tweetdelete (1.0.0) unstable; urgency=low"
  echo
  echo "  * Initial Linux package."
  echo
  echo " -- TweetDelete <noreply@example.invalid>  Mon, 11 Aug 2026 12:00:00 +0000"
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
chmod 0755 "$STAGE/usr/lib/tweetdelete/server.py" "$STAGE/usr/lib/tweetdelete/launch_window.py"

# ---- Build ----
# fakeroot ensures the files inside the .deb are owned by root:root without
# requiring this script itself to run as root.
fakeroot dpkg-deb --build --root-owner-group "$STAGE" "$OUT_DIR/${PKG_NAME}.deb"

echo
echo "Built: $OUT_DIR/${PKG_NAME}.deb"
echo "Inspect with:   dpkg -I \"$OUT_DIR/${PKG_NAME}.deb\""
echo "List files with: dpkg -c \"$OUT_DIR/${PKG_NAME}.deb\""
echo "Install with:   sudo apt install \"$OUT_DIR/${PKG_NAME}.deb\""

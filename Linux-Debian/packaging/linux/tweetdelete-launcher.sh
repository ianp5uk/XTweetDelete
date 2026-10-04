#!/bin/sh
# Installed as /usr/bin/tweetdelete. Starts the background service if it
# isn't already running (a no-op if it already is), then opens the app.
# This is what both the Applications-menu icon and running `tweetdelete`
# from a terminal do.
#
# v1.0.4: opens a compact app window sized to the app (Chrome, Chromium,
# Brave, Edge or Vivaldi, via launch_window.py) instead of a tab in a
# full-size browser window. Falls back to the default browser if none of
# those is installed (Firefox has no app-window mode). Set
# TWEETDELETE_BROWSER=default, or "browser": "default" in
# ~/.config/tweetdelete/launcher.json, to keep the old behaviour.
set -e

systemctl --user start tweetdelete.service 2>/dev/null || true

# Give a cold start a brief moment - server.py binds almost instantly, so a
# short fixed sleep is simpler and just as reliable as polling, without
# adding a curl dependency purely for this convenience wrapper.
sleep 0.5

python3 /usr/lib/tweetdelete/launch_window.py http://127.0.0.1:8765/ >/dev/null 2>&1 \
  || xdg-open http://127.0.0.1:8765/ >/dev/null 2>&1 &

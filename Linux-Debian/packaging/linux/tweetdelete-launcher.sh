#!/bin/sh
# Installed as /usr/bin/tweetdelete. Starts the background service if it
# isn't already running (a no-op if it already is), then opens the app in
# your default browser. This is what both the Applications-menu icon and
# running `tweetdelete` from a terminal do.
set -e

systemctl --user start tweetdelete.service 2>/dev/null || true

# Give a cold start a brief moment - server.py binds almost instantly, so a
# short fixed sleep is simpler and just as reliable as polling, without
# adding a curl dependency purely for this convenience wrapper.
sleep 0.5

xdg-open http://127.0.0.1:8765/ >/dev/null 2>&1 &
